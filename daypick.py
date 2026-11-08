"""⭐ Ставка дня · теннис · правило C (решение пользователя 08.11.2026; математика — pick.py).

  • Предложения на матчи текущих суток (местная дата), ещё не начавшиеся:
      – котировки API (крупные турниры): победа, фора по геймам, тотал геймов; кф 1xBet точно, P — Pinnacle без маржи (Шин);
      – скриншот 1xBet по любому матчу (пакет №3, этап 2) — кф точно;
      – запасные линии модели (кф неизвестен): победа, фора, ИТ, тотал с P ≤ 0,71.
  • Выбор — pick.choose: кф ≥ 1,35 → максимум справедливой P → уверенность 90% в выгодности (ступени 1 / 2 / 3).
  • Публикация — после суточного отчёта (не позднее 08:00), закреплённым сообщением. Замена до 17:00, если матч ставки не начался
    и новая ставка выше по ступени или лучше по P на ≥ 2 п.п.; принятую пользователем ставку бот сам не меняет.
  • Итог — по фактическому счёту (любая линия), отдача — по кф 1xBet. Учёт замен, скриншотов, принятий — лист day_pick.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

import config as C
import dpcore
import journal as J
import lines as FL
import markets as MK
import notify
import odds as OD
import pick as PK
from markov import Outcome

DISTS: dict[str, tuple] = {}       # espn_id → (распределение исходов D, игрок 1, игрок 2) — заполняет autorun.forecast_all
TITLE = None                       # совместимость (раньше — подпись линии из autorun)
API_MARKETS = ('h2h', 'spreads', 'totals')


def _local_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz='UTC').tz_localize(None) + pd.Timedelta(hours=C.TZ_OFFSET_HOURS)


def _last(name: str) -> str:
    return str(name).split()[-1] if str(name).strip() else str(name)


def label(market: str, pick: str, line, p1: str, p2: str) -> str:
    L = '' if line in (None, '') else f'{float(line):g}'
    if market == 'ML':
        return f'П1 {_last(p1)}' if pick == '1' else f'П2 {_last(p2)}'
    if market == 'HCP':
        return f'Ф{pick} {_last(p1 if pick == "1" else p2)} ({float(line):+g})'
    if market == 'TOTAL':
        return f'Т{"Б" if pick == "O" else "М"} {L}'
    if market in ('IT1', 'IT2'):
        return f'{market} {_last(p1 if market == "IT1" else p2)} {"Б" if pick == "O" else "М"} {L}'
    return f'{market} {pick} {L}'


def _settle_fn(market: str, pick: str, line):
    if market == 'ML':
        return MK.mk_winner(pick == '1')
    if market == 'HCP':
        return MK.mk_handicap(pick == '1', float(line))
    if market == 'TOTAL':
        return MK.mk_total(float(line), pick == 'O')
    return MK.mk_ind_total(market == 'IT1', float(line), pick == 'O')


def model_price(D, market: str, pick: str, line) -> tuple[float, float]:
    """(P выигрыша, P возврата) по распределению модели."""
    w, p, _ = MK.price(D, _settle_fn(market, pick, line))
    return float(w), float(p)


# ------------------------------------------------------------------ предложения
def _base(m, row) -> dict:
    return dict(espn_id=str(m.id), tour=row['tour'], event=row['event'], start_utc=row['start_utc'],
                start_local=row['start_local'], player1=row['player1'], player2=row['player2'], start=m.start)


def api_offers(m, row, ev: dict, flip: bool, r: float) -> list[dict]:
    """Предложения по котировкам API: стороны каждого рынка; P — Pinnacle (Шин) или модель; кф — 1xBet или r × Pinnacle."""
    q = OD.quotes(ev)
    p1n, p2n = row['player1'], row['player2']
    side = lambda name: '1' if OD.same_name(name, p1n) >= OD.same_name(name, p2n) else '2'
    D = DISTS.get(str(m.id), (None,))[0]
    pairs: dict[tuple, dict] = {}               # (рынок, линия игрока 1 / тотала) → {сторона: {букмекер: кф}}
    for (mk, name, point), books in q.items():
        if mk == 'h2h':
            pairs.setdefault(('ML', None), {})[side(name)] = books
        elif mk == 'spreads' and point is not None:
            s = side(name)
            l1 = float(point) if s == '1' else -float(point)
            pairs.setdefault(('HCP', l1), {})[s] = books
        elif mk == 'totals' and point is not None:
            pairs.setdefault(('TOTAL', float(point)), {})['O' if name == 'Over' else 'U'] = books
    out = []
    for (mk, l1), sides in pairs.items():
        names = ('1', '2') if mk != 'TOTAL' else ('O', 'U')
        if not all(n in sides for n in names):
            continue
        pin = [sides[n].get('pinnacle') for n in names]
        fair = PK.shin(pin) if all(pin) else None
        xb = [sides[n].get('onexbet') for n in names]
        fair_x = PK.shin(xb) if all(xb) else None
        for i, n in enumerate(names):
            line = None if mk == 'ML' else (l1 if (mk == 'TOTAL' or n == '1') else -l1)
            pm, pp = model_price(D, mk, n, line) if D is not None else (np.nan, 0.0)
            if fair:
                p, src = PK.fair(pm, fair[i], 'pinnacle'), 'pinnacle'
            elif fair_x:                                   # нет Pinnacle — 1xBet без маржи (рынок на истории точнее модели)
                p, src = PK.blend(pm, fair_x[i]), 'blend'
            else:
                p, src = pm, 'model'
            if p != p:
                continue
            k1x = sides[n].get('onexbet')
            k, ksrc = (k1x, 'api') if k1x else ((round(r * pin[i], 3), 'est') if pin[i] else (None, None))
            out.append(dict(_base(m, row), market=mk, pick=n, line=line, label=label(mk, n, line, p1n, p2n), p=float(p),
                            p_src=src, p_model=pm, p_push=pp, k=k, k_src=ksrc, source='api'))
    return out


def model_offers(m, row) -> list[dict]:
    """Запасные линии модели (кф неизвестен): P в [0,60; 0,71]."""
    D = DISTS.get(str(m.id), (None,))[0]
    if D is None:
        return []
    cand = [('ML', '1', None), ('ML', '2', None)]
    cand += [('HCP', s, h + 0.5) for h in range(-9, 9) for s in ('1', '2')]
    cand += [('TOTAL', s, t + 0.5) for t in range(14, 34) for s in ('O', 'U')]
    cand += [(mk, s, t + 0.5) for mk in ('IT1', 'IT2') for t in range(3, 17) for s in ('O', 'U')]
    out = []
    for mk, s, line in cand:
        p, pp = model_price(D, mk, s, line)
        if 0.60 <= p <= PK.P_UNKNOWN_MAX:
            out.append(dict(_base(m, row), market=mk, pick=s, line=line, label=label(mk, s, line, row['player1'], row['player2']),
                            p=p, p_src='model', p_model=p, p_push=pp, k=None, k_src=None, source='model'))
    return out


def offers(items: list[tuple], off: dict, day, log: list[str]) -> list[dict]:
    """Все предложения дня по правилу C (уже упорядочены pick.choose)."""
    now = pd.Timestamp.now(tz='UTC').tz_localize(None)
    todays = [(m, row) for m, row, _ in items
              if m.start > now and C.wday(m.start + pd.Timedelta(hours=C.TZ_OFFSET_HOURS)) == day]
    if not todays:
        return []
    events, r = [], OD.r_ratio()
    try:
        keys = [s['key'] for s in OD.sports() if s['active'] and s['group'] == 'Tennis']
        events, lg = OD.day_events('tennis', keys, API_MARKETS, day.isoformat(), refresh_hours=24)
        log += lg
    except Exception as e:                                   # нет ключа / сеть — работаем без котировок
        log.append(f'кф: недоступны ({e.__class__.__name__})')
    out = []
    for m, row in todays:
        ev, flip = OD.find_event(events, row['player1'], row['player2'], row['start_utc']) if events else (None, False)
        if ev:
            out += api_offers(m, row, ev, flip, r)
        out += model_offers(m, row)
    out = [o for o in out if o['market'] not in off]
    return PK.choose(out)


# ------------------------------------------------------------------ текст и публикация
def text(c: dict | None, day, hist: list[str], score: str, reason: str = '') -> str:
    e = notify.esc
    head = f'⭐ <b>Ставка дня · теннис · {day:%d.%m}</b>'
    if c is None:
        return f'{head}\nСегодня ставки дня нет: {e(reason)}.' + (f'\n{score}' if score else '')
    src = {'pinnacle': 'Pinnacle без маржи', 'blend': '1xBet без маржи', 'model': 'модель'}[c['p_src']]
    out = [head, f"🏆 {e(c['tour'])} · {e(c['event'])} · 🕒 {e(str(c['start_local'])[6:])}",
           f"<b>{e(c['player1'])} — {e(c['player2'])}</b>",
           f"<b>{e(c['label'])}</b> — вероятность {c['p'] * 100:.0f}% ({src})",
           e(PK.k_text(c)), e(PK.conf_text(c))]
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
        k = d.k.map(J.num)
        kk = d[k.notna()]
        if len(kk):
            pnl = sum((J.num(r.k) - 1) if str(r.result) == 'W' else -1.0 for r in kk.itertuples())
            out += f' · с кф: {len(kk)}, отдача {pnl / len(kk) * 100:+.1f}%'
    return out


ROW_KEYS = ('espn_id', 'tour', 'event', 'start_utc', 'start_local', 'player1', 'player2', 'market', 'label', 'p')


def _row(c: dict, dstr: str, changes: int, hist: list[str]) -> dict:
    row = {k: c[k] for k in ROW_KEYS}
    row.update(date=dstr, slot=c.get('slot', 0), p_low='',
               published=J.now_utc(), changes=changes, history=' | '.join(hist), pick=c['pick'],
               line='' if c['line'] is None else c['line'], k=c.get('k') or '', k_src=c.get('k_src') or '',
               p_src=c['p_src'], conf='' if c.get('conf') is None else round(c['conf'], 4), value_ok=int(c['value_ok']),
               tier=c['tier'], source=c.get('source', ''), p_model='' if c.get('p_model') != c.get('p_model') else
               round(float(c['p_model']), 4))
    row['p'] = round(float(row['p']), 4)
    return row


def _key(c: dict) -> tuple:
    return str(c['espn_id']), c['market'], str(c['pick']), '' if c.get('line') in (None, '') else f"{float(c['line']):g}"


def _slot(c: dict, items: list[tuple]) -> int:
    """Слот линии списка, если ставка совпадает с показанной линией (для ⭐ в списке), иначе 0."""
    for m, row, lns in items:
        if str(m.id) != str(c['espn_id']):
            continue
        for x in lns:
            same_line = (x['market'] == 'ML') or (str(x.get('line')) not in ('', 'nan') and float(x['line']) == float(c['line'] or 0))
            if x['market'] == c['market'] and str(x['pick']) == str(c['pick']) and same_line:
                return int(x['slot'])
    return 0


def process(rt: dict, store: J.Store, items: list[tuple], off: dict, daily_done: bool, dry: bool = False,
            shots: list[dict] | None = None, actions: list[str] | None = None) -> str:
    """Публикация / замена / принятие — общий цикл dpcore (оба бота одинаково)."""
    return dpcore.process(rt, store, items, off, daily_done, offers_fn=offers, text_fn=text, row_fn=_row, slot_fn=_slot,
                          match_fn=lambda c: f"{c['player1']} — {c['player2']}", score_fn=score_line,
                          shots=shots, actions=actions, dry=dry)


# ------------------------------------------------------------------ итог
def settle(store: J.Store) -> int:
    """Итог по фактическому счёту (лист auto_forecasts); старые строки без pick — по рассчитанной линии auto_lines."""
    dp = store.read('day_pick')
    todo = dp[dp.result.map(lambda x: str(x).strip() in ('', 'nan'))]
    if todo.empty:
        return 0
    af = store.read('auto_forecasts')
    res_af = {str(J.num(e)): (st, sc, se) for e, st, sc, se in zip(af.espn_id, af.status, af.score, af.settled)
              if str(st) in ('FIN', 'RET', 'VOID')}
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
            st, sc, se = x
            if st == 'FIN':
                sets = tuple(tuple(int(v) for v in s.split('-')[:2]) for s in str(sc).split())
                line = None if str(r.line).strip() in ('', 'nan') else J.num(r.line)
                res = _settle_fn(str(r.market), str(r.pick).replace('.0', ''), line)(Outcome(sets))
            else:
                res = 'P'
            dp.loc[i, ['status', 'result', 'settled']] = [st, res, se]
        else:
            x = res_ln.get((str(J.num(r.espn_id)), str(J.num(r.slot))))
            if not x:
                continue
            dp.loc[i, ['status', 'result', 'settled']] = list(x)
        upd.append(i)
    store.update('day_pick', dp.loc[upd], ['status', 'result', 'settled'])
    return len(upd)
