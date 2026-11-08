"""⭐ Ставка дня · футбол · правило C (решение пользователя 08.11.2026; математика — pick.py).

  • Предложения на матчи текущих суток (местная дата), ещё не начавшиеся:
      – котировки API по лигам дня: исход 1X2 и тотал (1xBet точно, P — Pinnacle без маржи, Шин);
      – по двум лучшим матчам дня — альтернативные тоталы и форы Pinnacle (кф 1xBet ≈ r × Pinnacle) и двойной шанс 1xBet
        (P — из исхода Pinnacle без маржи);
      – скриншот 1xBet по любому матчу (пакет №3, этап 2) — кф точно;
      – запасные линии модели (кф неизвестен): 1X2, двойной шанс, фора, тоталы, ИТ, «обе забьют» с P 0,60–0,71.
  • Выбор — pick.choose: кф ≥ 1,35 → максимум справедливой P → уверенность 90% в выгодности.
  • Публикация, замена (до 17:00, принятую не меняем), итог по фактическому счёту — как в теннисе.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config as C
import dpcore
import flines as FL
import journal as J
import notify
import odds as OD
import pick as PK

DISTS: dict[str, np.ndarray] = {}      # espn_id → матрица счёта (строки — голы хозяев); заполняет run.forecast_all
SPORT_KEYS = {'eng.1': 'soccer_epl', 'eng.fa': 'soccer_fa_cup', 'esp.1': 'soccer_spain_la_liga',
              'esp.copa_del_rey': 'soccer_spain_copa_del_rey', 'fra.1': 'soccer_france_ligue_one',
              'fra.coupe_de_france': 'soccer_france_coupe_de_france', 'ger.1': 'soccer_germany_bundesliga',
              'ger.dfb_pokal': 'soccer_germany_dfb_pokal', 'ita.1': 'soccer_italy_serie_a', 'ita.coppa_italia': 'soccer_italy_coppa_italia',
              'por.1': 'soccer_portugal_primeira_liga', 'por.taca.portugal': 'soccer_portugal_taca_de_portugal',
              'rus.1': 'soccer_russia_premier_league', 'tur.1': 'soccer_turkey_super_league',
              'uefa.champions': 'soccer_uefa_champs_league', 'uefa.europa': 'soccer_uefa_europa_league',
              'uefa.europa.conf': 'soccer_uefa_europa_conference_league'}
API_MARKETS = ('h2h', 'totals')
ALT_MARKETS = ('alternate_totals', 'alternate_spreads', 'double_chance')
ALT_EVENTS = 2


def _local_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz='UTC').tz_localize(None) + pd.Timedelta(hours=C.TZ_OFFSET_HOURS)


def label(market: str, pick: str, line) -> str:
    L = '' if line in (None, '') else f'{float(line):g}'
    return {'1X2': {'H': 'П1', 'D': 'X', 'A': 'П2'}.get(pick, pick), 'DC': pick,
            'AH': f'Ф{1 if pick == "H" else 2}({float(line or 0):+g})', 'TOT': f'Т{"Б" if pick == "O" else "М"}{L}',
            'IT1': f'ИТ1 {"Б" if pick == "O" else "М"}{L}', 'IT2': f'ИТ2 {"Б" if pick == "O" else "М"}{L}',
            'BTTS': 'ОЗ да' if pick == 'Y' else 'ОЗ нет'}[market]


def model_price(P: np.ndarray, market: str, pick: str, line) -> tuple[float, float]:
    """(P выигрыша, P возврата) по матрице счёта модели."""
    i, j = np.indices(P.shape)
    L = None if line in (None, '') else float(line)
    if market == '1X2':
        m = {'H': i > j, 'D': i == j, 'A': i < j}[pick]
        return float(P[m].sum()), 0.0
    if market == 'DC':
        m = {'1X': i >= j, 'X2': i <= j, '12': i != j}[pick]
        return float(P[m].sum()), 0.0
    if market == 'AH':
        v = (i - j + L) if pick == 'H' else (j - i + L)
        return float(P[v > 0].sum()), float(P[v == 0].sum())
    if market == 'BTTS':
        y = (i >= 1) & (j >= 1)
        return float(P[y if pick == 'Y' else ~y].sum()), 0.0
    g = {'TOT': i + j, 'IT1': i, 'IT2': j}[market]
    return float(P[(g > L) if pick == 'O' else (g < L)].sum()), float(P[g == L].sum())


def _base(m, row) -> dict:
    return dict(espn_id=str(m.id), comp_name=row['comp_name'], start_utc=row['start_utc'], start_local=row['start_local'],
                home=row['home'], away=row['away'], start=m.start)


def _offer(m, row, market, pick, line, p, p_src, k, k_src, source) -> dict | None:
    P = DISTS.get(str(m.id))
    pm, pp = model_price(P, market, pick, line) if P is not None else (np.nan, 0.0)
    if p is None:
        p, p_src = pm, 'model'
    elif p_src == 'pinnacle':
        p = PK.fair(pm, p, 'pinnacle')                     # веса истории: рынок 1,11, модель −0,11
    elif p_src == 'blend':
        p = PK.blend(pm, p)                                # 1xBet без маржи: рынок 1,08, модель −0,09
    if p != p:
        return None
    return dict(_base(m, row), market=market, pick=pick, line=line, label=label(market, pick, line), p=float(p), p_src=p_src,
                p_model=pm, p_push=pp, k=k, k_src=k_src, source=source, sigma=C.SIGMA.get(market))


def api_offers(m, row, ev: dict, flip: bool, r: float) -> list[dict]:
    q = OD.quotes(ev)
    home_api, away_api = (ev['away_team'], ev['home_team']) if flip else (ev['home_team'], ev['away_team'])
    side = lambda name: 'H' if name == home_api else 'A' if name == away_api else 'D'
    out = []
    # 1X2 и двойной шанс: P — Pinnacle без маржи по 3 исходам
    h2h = {side(n): b for (mk, n, pt), b in q.items() if mk == 'h2h'}
    fair, fsrc = None, 'model'
    if all(s in h2h and h2h[s].get('pinnacle') for s in 'HDA'):
        fair, fsrc = dict(zip('HDA', PK.shin([h2h[s]['pinnacle'] for s in 'HDA']))), 'pinnacle'
    elif all(s in h2h and h2h[s].get('onexbet') for s in 'HDA'):
        fair, fsrc = dict(zip('HDA', PK.shin([h2h[s]['onexbet'] for s in 'HDA']))), 'blend'
    for s, books in h2h.items():
        k1 = books.get('onexbet')
        k, ks = (k1, 'api') if k1 else ((round(r * books['pinnacle'], 3), 'est') if books.get('pinnacle') else (None, None))
        o = _offer(m, row, '1X2', s, None, fair[s] if fair else None, fsrc, k, ks, 'api')
        if o:
            out.append(o)
    for (mk, n, pt), books in q.items():
        if mk == 'double_chance' and books.get('onexbet'):
            parts = [x.strip() for x in n.split(' or ')]
            dc = ''.join(sorted({side(x) for x in parts}, key='HDA'.index))
            code = {'HD': '1X', 'DA': 'X2', 'HA': '12'}.get(dc)
            if code:
                p = sum(fair[x] for x in dc) if fair else None
                o = _offer(m, row, 'DC', code, None, p, fsrc, books['onexbet'], 'api', 'api')
                if o:
                    out.append(o)
    # тоталы и форы (основные и альтернативные): P — Pinnacle без маржи по паре сторон той же линии
    pairs: dict[tuple, dict] = {}
    for (mk, n, pt), books in q.items():
        if pt is None or mk not in ('totals', 'spreads'):
            continue
        if abs(float(pt) * 2 - round(float(pt) * 2)) > 1e-9:           # четвертные форы/тоталы (±0,25/0,75) — не берём
            continue
        if mk == 'totals':
            pairs.setdefault(('TOT', float(pt)), {})['O' if n == 'Over' else 'U'] = books
        else:
            s = side(n)
            lh = float(pt) if s == 'H' else -float(pt)
            pairs.setdefault(('AH', lh), {})[s] = books
    for (mk, lh), sides in pairs.items():
        names = ('O', 'U') if mk == 'TOT' else ('H', 'A')
        if not all(x in sides for x in names):
            continue
        pin = [sides[x].get('pinnacle') for x in names]
        xb = [sides[x].get('onexbet') for x in names]
        fair2, src2 = (PK.shin(pin), 'pinnacle') if all(pin) else ((PK.shin(xb), 'blend') if all(xb) else (None, 'model'))
        for idx, x in enumerate(names):
            line = lh if (mk == 'TOT' or x == 'H') else -lh
            k1 = sides[x].get('onexbet')
            k, ks = (k1, 'api') if k1 else ((round(r * pin[idx], 3), 'est') if pin[idx] else (None, None))
            o = _offer(m, row, mk, x, line, fair2[idx] if fair2 else None, src2, k, ks, 'api')
            if o:
                out.append(o)
    return out


def model_offers(m, row) -> list[dict]:
    P = DISTS.get(str(m.id))
    if P is None:
        return []
    cand = [('1X2', s, None) for s in 'HDA'] + [('DC', s, None) for s in ('1X', 'X2', '12')]
    cand += [('AH', s, L) for L in (-2.5, -1.5, 1.5, 2.5) for s in 'HA'] + [('BTTS', s, None) for s in 'YN']
    cand += [('TOT', s, L + 0.5) for L in range(0, 6) for s in 'OU'] + [(mk, s, L + 0.5) for mk in ('IT1', 'IT2') for L in range(0, 4) for s in 'OU']
    out = []
    for mk, s, line in cand:
        p, _ = model_price(P, mk, s, line)
        if PK.MIN_P_VALUE <= p <= PK.P_UNKNOWN_MAX:
            o = _offer(m, row, mk, s, line, None, 'model', None, None, 'model')
            if o:
                out.append(o)
    return out


def offers(items: list[tuple], off: dict, day, log: list[str]) -> list[dict]:
    now = pd.Timestamp.now(tz='UTC').tz_localize(None)
    todays = [(m, row) for m, row, _ in items if m.start > now and C.wday(m.start + pd.Timedelta(hours=C.TZ_OFFSET_HOURS)) == day]
    if not todays:
        return []
    events, r = [], OD.r_ratio()
    try:
        active = {s['key'] for s in OD.sports() if s['active']}
        keys = list(dict.fromkeys(SPORT_KEYS[m.comp] for m, _ in todays if SPORT_KEYS.get(m.comp) in active))
        events, lg = OD.day_events('football', keys, API_MARKETS, day.isoformat(), refresh_hours=24)
        log += lg
    except Exception as e:
        log.append(f'кф: недоступны ({e.__class__.__name__})')
    found = []
    for m, row in todays:
        ev, flip = OD.find_event(events, row['home'], row['away'], row['start_utc']) if events else (None, False)
        found.append((m, row, ev, flip))
    # альтернативные линии и двойной шанс — по ALT_EVENTS матчам с лучшей линией модели в зоне 0,65–0,80
    def promise(m):
        P = DISTS.get(str(m.id))
        if P is None:
            return 0.0
        ps = [model_price(P, mk, s, L)[0] for mk, s, L in [('DC', x, None) for x in ('1X', 'X2')] +
              [('TOT', x, y) for x in 'OU' for y in (1.5, 2.5, 3.5)]]
        ps = [p for p in ps if 0.65 <= p <= 0.80]
        return max(ps) if ps else 0.0
    for m, row, ev, flip in sorted([x for x in found if x[2]], key=lambda x: -promise(x[0]))[:ALT_EVENTS]:
        alt, msg = OD.add_alternates('football', day.isoformat(), ev['sport_key'], ev['id'], ALT_MARKETS)
        if msg:
            log.append(msg)
        if alt:
            ev['bookmakers'] = ev.get('bookmakers', []) + alt.get('bookmakers', [])
    out = []
    for m, row, ev, flip in found:
        if ev:
            out += api_offers(m, row, ev, flip, r)
        out += model_offers(m, row)
    out = [o for o in out if o['market'] not in off]
    return PK.choose(out)


# ------------------------------------------------------------------ текст и публикация
def text(c: dict | None, day, hist: list[str], score: str, reason: str = '') -> str:
    e = notify.esc
    head = f'⭐ <b>Ставка дня · футбол · {day:%d.%m}</b>'
    if c is None:
        return f'{head}\nСегодня ставки дня нет: {e(reason)}.' + (f'\n{score}' if score else '')
    src = {'pinnacle': 'Pinnacle без маржи', 'blend': '1xBet без маржи', 'model': 'модель'}[c['p_src']]
    out = [head, f"🏆 {e(c['comp_name'])} · 🕒 {e(str(c['start_local'])[6:])}", f"<b>{e(c['home'])} — {e(c['away'])}</b>",
           f"<b>{e(c['label'])}</b> — вероятность {c['p'] * 100:.0f}% ({src})", e(PK.k_text(c)), e(PK.conf_text(c))]
    if c.get('tier') == 3:
        out.append('<i>Котировок по матчу нет — линия модели с запасом по кф.</i>')
    out += [e(h) for h in hist]
    if score:
        out.append(score)
    return '\n'.join(out)


def score_line(dp: pd.DataFrame) -> str:
    d = dp[dp.result.astype(str).isin(['W', 'L'])] if len(dp) else dp
    if not len(d):
        return ''
    w = int((d.result.astype(str) == 'W').sum())
    out = f'📈 Ставки дня: {w}/{len(d)} ({w / len(d) * 100:.0f}%)'
    if 'k' in d.columns:
        kk = d[d.k.map(J.num).notna()]
        if len(kk):
            pnl = sum((J.num(r.k) - 1) if str(r.result) == 'W' else -1.0 for r in kk.itertuples())
            out += f' · с кф: {len(kk)}, отдача {pnl / len(kk) * 100:+.1f}%'
    return out


ROW_KEYS = ('espn_id', 'comp_name', 'start_utc', 'start_local', 'home', 'away', 'market', 'label', 'p')


def _row(c: dict, dstr: str, changes: int, hist: list[str]) -> dict:
    row = {k: c[k] for k in ROW_KEYS}
    row.update(date=dstr, slot=c.get('slot', 0), p_low='', published=J.now_utc(), changes=changes, history=' | '.join(hist),
               pick=c['pick'], line='' if c['line'] is None else c['line'], k=c.get('k') or '', k_src=c.get('k_src') or '',
               p_src=c['p_src'], conf='' if c.get('conf') is None else round(c['conf'], 4), value_ok=int(c['value_ok']),
               tier=c['tier'], source=c.get('source', ''),
               p_model='' if c.get('p_model') != c.get('p_model') else round(float(c['p_model']), 4))
    row['p'] = round(float(row['p']), 4)
    return row


def _key(c: dict) -> tuple:
    return str(c['espn_id']), c['market'], str(c['pick']), '' if c.get('line') in (None, '') else f"{float(c['line']):g}"


def _slot(c: dict, items: list[tuple]) -> int:
    for m, row, lns in items:
        if str(m.id) != str(c['espn_id']):
            continue
        for x in lns:
            xl = '' if str(x.get('line')) in ('', 'nan', 'None') else f"{float(x['line']):g}"
            if x['market'] == c['market'] and str(x['pick']) == str(c['pick']) and xl == _key(c)[3]:
                return int(x['slot'])
    return 0


def process(rt: dict, store: J.Store, items: list[tuple], off: dict, daily_done: bool, dry: bool = False,
            shots: list[dict] | None = None, actions: list[str] | None = None) -> str:
    """Публикация / замена / принятие — общий цикл dpcore (оба бота одинаково)."""
    return dpcore.process(rt, store, items, off, daily_done, offers_fn=offers, text_fn=text, row_fn=_row, slot_fn=_slot,
                          match_fn=lambda c: f"{c['home']} — {c['away']}", score_fn=score_line,
                          shots=shots, actions=actions, dry=dry)


def settle(store: J.Store) -> int:
    """Итог по фактическому счёту (auto_forecasts: status FT/ET, hg, ag); старые строки без pick — по рассчитанной линии."""
    dp = store.read('day_pick')
    todo = dp[dp.result.map(lambda x: str(x).strip() in ('', 'nan'))]
    if todo.empty:
        return 0
    af = store.read('auto_forecasts')
    res_af = {str(J.num(e)): (str(st), hg, ag, se) for e, st, hg, ag, se in zip(af.espn_id, af.status, af.hg, af.ag, af.settled)
              if str(se).strip() not in ('', 'nan')}
    ld = store.read('auto_lines')
    res_ln = {(str(J.num(e)), str(J.num(s))): (st, r, se) for e, s, st, r, se in zip(ld.espn_id, ld.slot, ld.status, ld.result, ld.settled)
              if str(r) in ('W', 'L', 'P')}
    upd = []
    for i, r in todo.iterrows():
        has_pick = 'pick' in dp.columns and str(r.get('pick', '')).strip() not in ('', 'nan')
        if has_pick:
            x = res_af.get(str(J.num(r.espn_id)))
            if not x:
                continue
            st, hg, ag, se = x
            ft = st == 'FT'
            res = FL.settle(str(r.market), str(r.pick), r.line, st, int(J.num(hg)) if ft else None, int(J.num(ag)) if ft else None)
            dp.loc[i, ['status', 'result', 'settled']] = [st, res, se]
        else:
            x = res_ln.get((str(J.num(r.espn_id)), str(J.num(r.slot))))
            if not x:
                continue
            dp.loc[i, ['status', 'result', 'settled']] = list(x)
        upd.append(i)
    store.update('day_pick', dp.loc[upd], ['status', 'result', 'settled'])
    return len(upd)
