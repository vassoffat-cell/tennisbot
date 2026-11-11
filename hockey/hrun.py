"""🏒 Хоккей в теннисном боте («Заметки Т») — отдельными сообщениями, по тем же правилам, что теннис и футбол.

Каждый прогон теннисного облака (раз в 2 ч):
  1. Результаты и расписание НХЛ (ESPN) за −3…+2 суток → hockey/data/games.csv.gz.
  2. Подгонка модели (hmodel: Пуассон с силой атаки/защиты, раздутие ничьих, ОТ/буллиты) по истории до сейчас.
  3. Прогноз матчей хоккейных суток (12:00 → 12:00 Ташкента, см. ниже): лист hk_games (исход) и hk_lines (линии, hlines).
  4. Расчёт итогов прошедших матчей (основное время; «Победа с ОТ» — по итоговому счёту).
  5. Список «🏒 Хоккей · прогноз до …» — отдельным сообщением (прошлый удаляется, как у тенниса).
  6. Раз в сутки (12:07, после последних матчей НХЛ): «📈 Хоккей · отчёт» — таблица по стандарту engine/REPORT_STYLE.md,
     сводка-таблица, полный список матчей с линиями; самокалибровка k по рынкам (с 30 линий), ⛔ рынок при z ≤ −2 на 30 линиях.
     Понедельник — «📅 Хоккей · неделя» (линии по типу). Отчёт «🏒 Хоккей» — в «Аналитике» (лист reports, ключ hk).
Тотал — одна линия лиги (T32): НХЛ 4,5, КХЛ 3,5 (решение 11.11 23:01; факт «больше» ≈ 72% / 75%).

Хоккейные сутки (решение 11.11.2026: «часовой пояс КХЛ и НХЛ не такой, как у тенниса»). Время Ташкента (UTC+5):
  КХЛ — начало 12:00–21:30 (Дальний Восток и Шанхай — днём, Москва — 21:30), конец до ~00:30;
  НХЛ — начало 22:30–08:30 (дневные матчи выходных — 22:30–01:00, вечер США — 04:00–08:30), конец до ~11:30.
  Граница 12:00 не режет ни одну лигу: сутки D = вечер КХЛ D + ночь НХЛ D→D+1.
  Прогноз — каждые 2 ч до конца суток (за 6 ч до границы — уже на следующие); отчёт за сутки — 12:07 (если матч ещё
  не рассчитан — до 14:07); неделя (пн 12:00 → пн 12:00) — понедельник 12:07; аудит — понедельник 15:00.
КХЛ — своя база с 11.11.2026 (API-Sports по датам, hdata.update_khl): прогнозы считаются «в тени» (лист, отчёт),
в список — после ворот бэктеста (config.HK_KHL_PUBLIC). Каперы по хоккею — hcappers.py (🔥/⚔️, учёт точности).
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

import config as C
import journal as J
import notify
import rtable as RT
from hockey import hcappers as HC
from hockey import hdata as HD
from hockey import hlines as HL
from hockey import hmodel as HM
from hockey import hpick as HP

LEAGUES = ('NHL', 'KHL')
LEAGUE_RU = {'NHL': 'НХЛ', 'KHL': 'КХЛ'}
HK_DAY_START = 12                                       # хоккейные сутки 12:00 → 12:00 Ташкента
REPORT_GRACE_H = 2                                      # отчёт ждёт нерассчитанные матчи до 14:00


def public() -> tuple[str, ...]:
    """Лиги в списке и отчёте; КХЛ — после ворот бэктеста (флаг), до того — «тень»."""
    return ('NHL',) + (('KHL',) if getattr(C, 'HK_KHL_PUBLIC', False) else ())
GAME_COLS = ['created', 'id', 'league', 'start_utc', 'start_local', 'home', 'away', 'lam_h', 'lam_a', 'p_h', 'p_d', 'p_a',
             'p_ml', 'q_ot', 'status', 'hg', 'ag', 'hg_reg', 'ag_reg', 'settled', 'logloss']
LINE_COLS = ['created', 'id', 'league', 'start_utc', 'start_local', 'home', 'away', 'slot', 'market', 'pick', 'line', 'label',
             'p_raw', 'p', 'p_low', 'shown', 'status', 'result', 'settled']
J.SHEETS.setdefault('hk_games', GAME_COLS)
J.SHEETS.setdefault('hk_lines', LINE_COLS)
STEPS_ML = (56.0, 58.0, 60.0, 62.5, 65.0, 70.0)          # ступени «Победа с ОТ» (бэктест 2024–26: 56,0%)
STEPS_LINES = (65.0, 67.5, 70.0, 72.5, 75.0)
CALIB_MIN, CALIB_PRIOR = 30, 300
COLOR = {3: '🟢', 2: '🟡', 1: '🔴'}
LMARK = {'W': '✅', 'L': '❌', 'P': '⚪'}


def _now() -> pd.Timestamp:
    return pd.Timestamp.now(tz='UTC').tz_localize(None)


def _local(ts: pd.Timestamp | None = None) -> pd.Timestamp:
    return (ts if ts is not None else _now()) + pd.Timedelta(hours=C.TZ_OFFSET_HOURS)


def hday(loc) -> dt.date:
    """Хоккейные сутки для местного времени (00:00–11:59 — ещё прошлые сутки)."""
    return (pd.Timestamp(loc) - pd.Timedelta(hours=HK_DAY_START)).date()


def _bound_utc(d: dt.date) -> pd.Timestamp:
    """Начало хоккейных суток d (12:00 Ташкента) в UTC."""
    return pd.Timestamp(dt.datetime.combine(d, dt.time(HK_DAY_START))) - pd.Timedelta(hours=C.TZ_OFFSET_HOURS)


def window_end(now: pd.Timestamp) -> pd.Timestamp:
    """Горизонт списка: конец текущих хоккейных суток; за 6 ч до границы — конец следующих."""
    return _bound_utc(hday(_local(now) + pd.Timedelta(hours=6)) + dt.timedelta(days=1))


def _day(start_utc) -> dt.date:
    return hday(pd.Timestamp(start_utc) + pd.Timedelta(hours=C.TZ_OFFSET_HOURS))


def _blank(x) -> bool:
    return str(x).strip() in ('', 'nan', 'None')


def _num(x):
    try:
        return float(str(x).replace(',', '.'))
    except Exception:
        return float('nan')


# ------------------------------------------------------------------ калибровка и предохранитель
def calibration(ld: pd.DataFrame) -> dict[str, tuple[float, int]]:
    out = {}
    L = _done(ld)
    L = L[L.league.isin(public())]
    for mk in HL.SIGMA:
        s = L[L.market == mk]
        if len(s) < CALIB_MIN:
            out[mk] = (1.0, len(s)); continue
        x, y = HL.logit(s.p_raw.map(_num).values.astype(float)), s.won.values.astype(float)
        ks = np.linspace(0.5, 1.6, 221)
        k_hat = float(ks[int(np.argmin([np.sum(np.logaddexp(0, k * x) - y * k * x) for k in ks]))])
        w = len(s) / (len(s) + CALIB_PRIOR)
        out[mk] = (round(1 + w * (k_hat - 1), 4), len(s))
    return out


def breaker(ld: pd.DataFrame) -> dict[str, str]:
    """⛔ рынок: последние 30 показанных линий значимо ниже обещанного (z ≤ −2); снова ✅ при z > −1."""
    L = _done(ld)
    L = L[L.league.isin(public())]
    off = {}
    for mk, g in L.groupby('market'):
        g = g.tail(30)
        if len(g) >= 30:
            p = g.p.values
            z = (g.won.sum() - p.sum()) / np.sqrt((p * (1 - p)).sum())
            if z <= -2:
                off[mk] = f'z {z:.1f}'
    return off


def _done(ld: pd.DataFrame) -> pd.DataFrame:
    if not len(ld):
        return pd.DataFrame(columns=LINE_COLS + ['won', 'day'])
    x = ld[ld.result.astype(str).isin(['W', 'L']) & (ld.shown.astype(str).str.strip().isin(['1', '1.0']))].copy()
    tot = x.market.astype(str) == 'TOT'                 # тотал фикс.: только текущая линия лиги (смена 11.11: 5,5 → 4,5 / 3,5)
    x = x[~tot | (x.line.map(_num) == x.league.map(lambda lg: HL.FIXED_TOTAL.get(lg, -1)))].copy()
    x['p'] = x.p.map(_num).astype(float)
    x['won'] = (x.result.astype(str) == 'W').astype(int)
    x['day'] = x.start_utc.map(_day)
    return x.sort_values('start_utc')


# ------------------------------------------------------------------ прогноз и итоги
def forecast(games: pd.DataFrame, kc: dict, off: dict, models: dict | None = None) -> list[tuple[dict, list[dict]]]:
    now = _now()
    end = window_end(now)
    up = games[(games.state.astype(str) == 'pre') & (pd.to_datetime(games.start) > now)
               & (pd.to_datetime(games.start) <= end) & games.league.isin(LEAGUES)]
    out = []
    models = models if models is not None else {}
    for r in up.itertuples(index=False):
        if r.league not in models:
            models[r.league] = HM.fit(games, r.league, pd.Timestamp(now))
        m = models[r.league]
        lam = m.lambdas(r.home, r.away) if m else None
        if not lam:
            continue
        P = m.matrix(*lam); q = m.ot_home(*lam)
        i, j = np.indices(P.shape)
        ph, pd_, pa = float(P[i > j].sum()), float(np.trace(P)), float(P[i < j].sum())
        row = dict(created=J.now_utc(), id=r.id, league=r.league, start_utc=r.start, start_local=J.to_local(pd.Timestamp(r.start)),
                   home=r.home, away=r.away, lam_h=round(lam[0], 3), lam_a=round(lam[1], 3), p_h=round(ph, 4), p_d=round(pd_, 4),
                   p_a=round(pa, 4), p_ml=round(ph + pd_ * q, 4), q_ot=round(q, 4), status='', hg='', ag='', hg_reg='', ag_reg='',
                   settled='', logloss='')
        lns = []
        for x in HL.build(P, q, r.league, kc):
            if x['market'] in off or r.league not in public():
                x['shown'] = 0
            lns.append(dict(created=row['created'], id=r.id, league=r.league, start_utc=r.start, start_local=row['start_local'],
                            home=r.home, away=r.away, status='', result='', settled='', **x))
        out.append((row, lns))
    return out


def save(store: J.Store, items: list[tuple[dict, list[dict]]], gf: pd.DataFrame, ld: pd.DataFrame) -> tuple[int, int]:
    """Новые матчи — дописать; не начавшиеся — обновить прогноз (линии — по слоту)."""
    have = {str(x): i for i, x in zip(gf.index, gf.id)} if len(gf) else {}
    new_g, new_l, upd = [], [], 0
    lix = {(str(a), int(_num(b))): i for i, a, b in zip(ld.index, ld.id, ld.slot)} if len(ld) else {}
    for row, lns in items:
        if row['id'] not in have:
            new_g.append(row); new_l += lns
            continue
        i = have[row['id']]
        store.update('hk_games', pd.DataFrame([row], index=[i]), GAME_COLS[:15])
        u = [(lix[(row['id'], x['slot'])], x) for x in lns if (row['id'], x['slot']) in lix]
        if u:
            store.update('hk_lines', pd.DataFrame([x for _, x in u], index=[k for k, _ in u]), LINE_COLS[:16])
        upd += 1
    if new_g:
        store.append('hk_games', new_g)
        store.append('hk_lines', new_l)
    return len(new_g), upd


def settle(store: J.Store, games: pd.DataFrame, gf: pd.DataFrame, ld: pd.DataFrame) -> tuple[int, int]:
    res = {str(r.id): r for r in games[games.end.astype(str).isin(['REG', 'OT', 'SO'])].itertuples(index=False)}
    today = dt.date.today().isoformat()
    ug, ul = [], []
    for i, r in gf[gf.settled.map(_blank)].iterrows() if len(gf) else []:
        x = res.get(str(r.id))
        if x is None:
            continue
        pm = _num(r.p_ml)
        won = 1.0 if x.hg > x.ag else 0.0
        ll = round(-float(np.log(max(pm if won else 1 - pm, 1e-6))), 4)
        gf.loc[i, ['status', 'hg', 'ag', 'hg_reg', 'ag_reg', 'settled', 'logloss']] = [x.end, int(x.hg), int(x.ag),
                                                                                         int(x.hg_reg), int(x.ag_reg), today, ll]
        ug.append(i)
    if ug:
        store.update('hk_games', gf.loc[ug], ['status', 'hg', 'ag', 'hg_reg', 'ag_reg', 'settled', 'logloss'])
    for i, r in ld[ld.settled.map(_blank)].iterrows() if len(ld) else []:
        x = res.get(str(r.id))
        if x is None:
            continue
        v = HL.settle(str(r.market), str(r.pick), r.line, x.end, x.hg, x.ag, x.hg_reg, x.ag_reg)
        ld.loc[i, ['status', 'result', 'settled']] = [x.end, v, today]
        ul.append(i)
    if ul:
        store.update('hk_lines', ld.loc[ul], ['status', 'result', 'settled'])
    return len(ug), len(ul)


# ------------------------------------------------------------------ сообщения
def _tier(p_low: float) -> int:
    return 3 if p_low >= 0.70 else 2 if p_low >= 0.65 else 1


# Тотал фикс. КХЛ 3,5 (решение 11.11 23:15): модель различает голевые матчи — бэктест 2024/25 (n 869) по четвертям P:
# 70% / 76% / 77% / 85% факт «больше» → 🟢 только при P ≥ 0,75, иначе ⚪. НХЛ 4,5: четверти 69–73% — без отбора (по p_low).
KHL_TOT_GREEN = 0.75


def _ltier(x: dict, league: str) -> int:
    if x['market'] == 'TOT' and league == 'KHL':
        return 3 if float(x['p']) >= KHL_TOT_GREEN else 1
    return _tier(float(x['p_low']))


def list_blocks(items: list[tuple[dict, list[dict]]], tp: pd.DataFrame | None = None) -> list[str]:
    items = [t for t in items if t[0]['league'] in public()]
    if not items:
        return []
    e = notify.esc
    today = _local().strftime('%d.%m')
    blocks = {}
    for row, lns in sorted(items, key=lambda t: t[0]['start_utc']):
        when = row['start_local'][6:] if row['start_local'][:5] == today else row['start_local']
        k = 0 if row['p_ml'] >= 0.5 else 1
        out = [f"🕒 <b>{e(when)}</b> · <b>{e(row['home'])}</b> — <b>{e(row['away'])}</b>",
               f"🎯 Победа с ОТ: <b>{'П1' if k == 0 else 'П2'} — {max(row['p_ml'], 1 - row['p_ml']) * 100:.0f}%</b>"
               f" · {notify.fair(max(row['p_ml'], 1 - row['p_ml']))}"]
        for x in sorted([x for x in lns if int(x['shown']) and x['market'] != 'ML'],
                        key=lambda x: (-_ltier(x, row['league']), -float(x['p']), x['slot'])):
            t = e(f"{x['label']} — {float(x['p']) * 100:.0f}% · {notify.fair(x['p'])}")   # честный кф — на каждой линии
            out.append({3: f'🟢 <b>{t}</b>', 2: f'🟡 {t}', 1: f'⚪ <i>{t}</i>'}[_ltier(x, row['league'])])
        out += [e(x) for x in HC.for_game(tp if tp is not None else pd.DataFrame(), row['id'])]
        blocks.setdefault(row['league'], []).append('\n'.join(out))
    wend = window_end(_now()) + pd.Timedelta(hours=C.TZ_OFFSET_HOURS)
    head = (f"🏒 <b>Хоккей · прогноз до {wend:%d.%m %H:%M}</b> · "
            f"{_local():%H:%M} · {len(items)} {notify.plural(len(items), 'матч', 'матча', 'матчей')}\n"
            f"<i>линии — основное время; 🎯 — с ОТ и буллитами; тотал — одна линия лиги; 🔥/⚔️ — капер согласен/спорит</i>\n"
            f"<i>честный кф = 1 / вероятность: ставка в плюс, если кф букмекера выше</i>")
    out = [head]
    for lg in LEAGUES:
        if blocks.get(lg):
            out += notify.folded(f'🏆 <b>{LEAGUE_RU[lg]}</b>', blocks[lg])
    return out


def publish(rt: dict, items: list[tuple[dict, list[dict]]], tp: pd.DataFrame | None = None) -> str:
    items = [t for t in items if t[0]['league'] in public()]
    ids = sorted(r['id'] for r, _ in items)
    removed = notify.delete([tuple(x) for x in rt.get('hk_list', [])])
    sent = []
    if items:
        silent = set(ids) <= set(rt.get('hk_list_ids', []))
        sent = notify.send_blocks(list_blocks(items, tp), silent=silent)
    rt['hk_list'], rt['hk_list_ids'] = [list(x) for x in sent], ids
    return f'🏒 список: удалено {removed}, отправлено {len(sent)}, матчей {len(ids)}'


def _wilson_lo(w: int, n: int, z: float = 1.96) -> float:
    if not n:
        return 0.0
    p = w / n
    return (p + z * z / (2 * n) - z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / (1 + z * z / n)


def _period(df: pd.DataFrame, col: str, day: dt.date | None, d0: dt.date | None) -> pd.DataFrame:
    if not len(df) or day is None:
        return df
    d = df[col].map(_day)
    return df[(d >= (d0 or day)) & (d <= day)]


def _shadow(gf: pd.DataFrame, ld: pd.DataFrame, lg: str, day, d0) -> str:
    """Лига «в тени» (ещё не публикуется): исход с ОТ и показываемые линии за период."""
    G = _period(gf[(gf.league == lg) & gf.status.astype(str).isin(['REG', 'OT', 'SO'])], 'start_utc', day, d0) if len(gf) else gf
    L = _period(ld[(ld.league == lg) & ld.result.astype(str).isin(['W', 'L'])], 'start_utc', day, d0) if len(ld) else ld
    if len(L):
        L = L[[m == 'TOT' or HL.visible(_num(p), m) for p, m in zip(L.p, L.market)]]
    if not len(G):
        return 'нет итогов'
    hit = sum((_num(p) >= 0.5) == (_num(h) > _num(a)) for p, h, a in zip(G.p_ml, G.hg, G.ag))
    out = f'{hit}/{len(G)} ({hit / len(G) * 100:.0f}%)'
    if len(L):
        out += f' · лин {int((L.result == "W").sum())}/{len(L)}'
    return out


def report(gf: pd.DataFrame, ld: pd.DataFrame, title: str, day: dt.date | None, kc: dict, kprev: dict, off: dict,
           d0: dt.date | None = None, tp: pd.DataFrame | None = None, dp: pd.DataFrame | None = None) -> list[str]:
    """Отчёт: таблица «Линия (цель) | Кол-во | %», сводка-таблица, полный список матчей с линиями (суточный) / по типу (неделя)."""
    e = notify.esc
    gf_all, ld_all = gf, ld
    gf = gf[gf.league.isin(public())] if len(gf) else gf
    ld = ld[ld.league.isin(public())] if len(ld) else ld
    G = gf[gf.status.astype(str).isin(['REG', 'OT', 'SO'])].copy() if len(gf) else gf
    L = _done(ld)
    if len(G):
        G['day'] = G.start_utc.map(_day)
        G['hit'] = [int((_num(p) >= 0.5) == (_num(h) > _num(a))) for p, h, a in zip(G.p_ml, G.hg, G.ag)]
    if d0 is not None:
        G = G[(G.day >= d0) & (G.day <= day)] if len(G) else G
        L = L[(L.day >= d0) & (L.day <= day)]
    T, S, worst = [], [], 3
    if len(G):
        tgt = STEPS_ML[0]
        lo = _wilson_lo(int(G.hit.sum()), len(G)) * 100
        c = 3 if lo >= tgt else 1 if (G.hit.mean() * 100 < tgt and _wilson_lo(len(G) - int(G.hit.sum()), len(G)) * 100 > 100 - tgt) else 2
        worst = min(worst, c)
        T.append(RT.row('Победа с ОТ', G.hit.sum(), len(G), RT.num(tgt, 0) + '%', COLOR[c]))
        if day is not None and d0 is None:
            d = G[G.day == day]; w = G[G.day > day - dt.timedelta(days=7)]
            T += [RT.row(' сутки', d.hit.sum(), len(d)), RT.row(' 7 дней', w.hit.sum(), len(w))]
        ll = G.logloss.map(_num).astype(float)
        T.append((' лог-лосс', str(len(G)), RT.num(ll.mean(), 3), ''))
    else:
        T.append(('Исход — доигранных пока нет', '', '', ''))
    fx = L[L.market == 'TOT']
    rest = L[L.market != 'TOT']
    if len(rest):
        T.append(RT.row('Линии', rest.won.sum(), len(rest), RT.num(STEPS_LINES[0], 0) + '%'))
        for mk in ('ML', 'DC', 'AH', 'IT1', 'IT2'):
            s = rest[rest.market == mk]
            if len(s):
                p = s.p.values
                z = (s.won.sum() - p.sum()) / np.sqrt((p * (1 - p)).sum())
                cc = 3 if z >= 0 else 2 if z > -2 else 1
                T.append(RT.row(' ' + HL.MARKET_RU[mk][:11], s.won.sum(), len(s), RT.pct(p.mean(), 0) + '%',
                                COLOR[cc] + (' ⛔' if mk in off else '')))
    if len(fx):
        p = fx.p.values
        z = (fx.won.sum() - p.sum()) / np.sqrt((p * (1 - p)).sum())
        T.append(RT.row('Тотал фикс.', fx.won.sum(), len(fx), RT.pct(p.mean(), 0) + '%', COLOR[3 if z >= 0 else 2 if z > -2 else 1]))
        for pk, ru in (('O', ' Б'), ('U', ' М')):
            q = fx[fx.pick.astype(str) == pk]
            if len(q):
                T.append(RT.row(ru, q.won.sum(), len(q), RT.pct(q.p.mean(), 0) + '%'))
    if dp is not None and len(dp):                         # ⭐ ставка дня: итоговая, новые линии, правки первого приговора
        rr = HP.rows(dp, (d0 or day) if day is not None else None, day)
        for k, lab in (('main', '⭐ Ставка дня'), ('extra', ' ➕ новые линии')):
            w, n, pm = rr.get(k, (0, 0, 0.0))
            if n:
                T.append(RT.row(lab, w, n, RT.pct(pm, 0) + '%'))
        ed, nd = rr.get('days', (0, 0))
        if nd:
            sh = ed / nd
            T.append(RT.row(' ✏️ дни с правкой', ed, nd, mark='🟢' if sh <= 0.2 else '🟡' if sh <= 0.4 else '🔴'))
        g, b = rr.get('who', (0, 0))
        if g + b:
            T.append(RT.row(' права итоговая', g, g + b))
    T.append(('До выводов', '', '', ''))
    T.append(RT.row(' калибровка', min(len(L), CALIB_PRIOR), CALIB_PRIOR))
    for mk, (k, n) in kc.items():
        if n >= CALIB_MIN:
            S.append((f'k {HL.MARKET_RU[mk][:9]}', f'{RT.num((kprev or {}).get(mk, (1.0, 0))[0], 2)}→{RT.num(k, 2)}'))
    if not any(n >= CALIB_MIN for _, n in kc.values()):
        S.append(('k', f'1,00 (< {CALIB_MIN} линий)'))
    S.append(('Данные', '✅ НХЛ ESPN · КХЛ API'))
    for lg in LEAGUES:
        if lg not in public():
            S.append((f'{LEAGUE_RU[lg]} в тени', _shadow(gf_all, ld_all, lg, day, d0)))
    if tp is not None and len(tp):
        cs = HC.stats(_period(tp, 'start', day, d0))
        if cs.get('n'):
            S.append(('Каперы', f"{cs['w']}/{cs['w'] + cs['l']}" + (f" · ROI {cs['roi'] * 100:+.0f}%" if cs['nk'] else '')))
            f, c = cs['fire'], cs['clash']
            if f[1] or c[1]:
                S.append(('🔥 / ⚔️', f'{f[0]}/{f[1]} · {c[0]}/{c[1]}'))
    status = {3: '🟢 цели доказаны', 2: '🟡 цели ещё не доказаны', 1: '🔴 нужен разбор'}[worst]
    blocks = [f'<b>{e(title)}</b> · {status}\n' + RT.render(T) + '\n' + RT.kv(S)]
    if day is not None and d0 is None and len(gf):        # суточный: каждый матч и каждая предложенная линия
        A = gf.copy(); A['day'] = A.start_utc.map(_day)
        A = A[A.day == day].sort_values('start_utc')
        items = []
        for r in A.itertuples(index=False):
            fav = 'П1' if _num(r.p_ml) >= 0.5 else 'П2'
            st = str(r.status)
            if st in ('REG', 'OT', 'SO'):
                hit = (_num(r.p_ml) >= 0.5) == (_num(r.hg) > _num(r.ag))
                mark, tail = ('✅' if hit else '❌'), f"{int(_num(r.hg))}:{int(_num(r.ag))}{'' if st == 'REG' else ' ' + st}"
            else:
                mark, tail = '⏳', 'ждёт итога'
            it = f"{mark} {e(r.home)} — {e(r.away)} · {fav} {max(_num(r.p_ml), 1 - _num(r.p_ml)) * 100:.0f}% · {e(tail)}"
            for x in ld[(ld.id.astype(str) == str(r.id)) & ld.shown.astype(str).str.strip().isin(['1', '1.0'])].itertuples(index=False):
                it += f"\n   {LMARK.get(str(x.result), '⏳')} {e(x.label)} · {_num(x.p) * 100:.0f}%"
            if tp is not None and len(tp):
                for c in tp[tp.game_id.astype(str) == str(r.id)].itertuples(index=False):
                    it += (f"\n   {LMARK.get(str(c.result), '⏳')} {HC.REL.get(c.rel if c.rel != '' else 0, '·')} "
                           f"{e(HC.SRC_RU.get(c.src, c.src))}: {e(c.label)}")
            items.append(it)
        if items:
            cnt = {m: sum(x.startswith(m) for x in items) for m in ('✅', '❌', '⏳')}
            blocks += notify.folded(f"▸ Матчи за сутки ({len(items)}: " + ' · '.join(f'{m} {n}' for m, n in cnt.items() if n) + ')', items)
    if d0 is not None and len(L):                          # неделя: линии по типу, по дням
        for mk, g in L.groupby('market', sort=False):
            items = []
            for d, gd in g.groupby('day', sort=False):
                items.append(f'<b>{d:%d.%m}</b>\n' + '\n'.join(
                    f"{LMARK.get(str(x.result), '⏳')} {e(str(x.home).split()[-1])}–{e(str(x.away).split()[-1])} · {e(x.label)} · {x.p * 100:.0f}%"
                    for x in gd.itertuples(index=False)))
            blocks += notify.folded(f"▸ {HL.MARKET_RU[mk]} ({int(g.won.sum())}/{len(g)})", items)
    return blocks


# ------------------------------------------------------------------ прогон
def run(store: J.Store, rt: dict, log: list[str], force: bool = False, dry: bool = False) -> list[dict]:
    """Вызывается из autorun.py после тенниса (каждые 2 ч). Расписание — свои хоккейные сутки (12:00 → 12:00).
    Возвращает строки отчёта для «Аналитики» (лист reports)."""
    today = dt.date.today()
    n = HD.update_nhl([today + dt.timedelta(days=k) for k in range(-3, 3)], log, pause=0.1)
    try:
        HD.update_khl(log)                                # вчера/сегодня/завтра (Москва) — окно бесплатного плана
    except Exception as ex:
        log.append(f'🏒 КХЛ: сбой {ex.__class__.__name__}: {ex}')
    games = HD.load()
    gf, ld = store.read('hk_games'), store.read('hk_lines')
    ng, nl = settle(store, games, gf, ld)
    gf, ld = store.read('hk_games'), store.read('hk_lines')
    loc, now = _local(), _now()
    cur = hday(loc)
    yday = cur - dt.timedelta(days=1)
    rt.setdefault('hk_last_daily', str(yday))             # переход на хоккейные сутки 11.11: первый отчёт — 12.11 12:07
    pend = 0
    if len(gf):
        Y = gf[gf.league.isin(public()) & (gf.start_utc.map(_day) == yday)]
        pend = int(Y.settled.map(_blank).sum())
    since = (now - _bound_utc(cur)).total_seconds() / 3600
    due = force or (rt['hk_last_daily'] != str(yday) and (pend == 0 or since >= REPORT_GRACE_H))
    if due or not rt.get('hk_kcal'):
        prev = rt.get('hk_kcal') or {}
        rt['hk_kcal_prev'] = prev
        rt['hk_kcal'] = {k: list(v) for k, v in calibration(ld).items()}
    kcal = {k: tuple(v) for k, v in rt['hk_kcal'].items()}
    kc = {k: v[0] for k, v in kcal.items()}
    off = breaker(ld)
    models: dict = {}
    items = forecast(games, kc, off, models)
    try:
        tp = HC.run(games, log)
    except Exception as ex:
        tp = pd.DataFrame(columns=HC.COLS)
        log.append(f'🏒 каперы: сбой {ex.__class__.__name__}: {ex}')
    nn, nu = save(store, items, gf, ld) if not dry else (0, 0)
    by = {lg: sum(r['league'] == lg for r, _ in items) for lg in LEAGUES}
    log.append(f'🏒 матчей ESPN {n}, рассчитано {ng}/{nl}, прогнозов НХЛ {by["NHL"]} · КХЛ {by["KHL"]}'
               f'{"" if "KHL" in public() else " (тень)"}, новых {nn}, обновлено {nu}; ⛔ {", ".join(off) or "нет"}; '
               f'сутки {cur:%d.%m}, отчёт {"да" if due else "нет"} (ждут итога {pend})')
    if dry:
        print('\n\n'.join(list_blocks(items, tp)))
        return []
    log.append(publish(rt, items, tp))
    dp = None
    try:                                                  # ⭐ ставка дня хоккея (hpick.py)
        HP.settle(store, games)
        HP.process(store, rt, items, tp, public(), cur, _bound_utc(cur), _bound_utc(cur + dt.timedelta(days=1)), now, _ltier, log)
        dp = store.read('hk_day_pick')
    except Exception as ex:
        log.append(f'⭐🏒 сбой {ex.__class__.__name__}: {ex}')
    gf, ld = store.read('hk_games'), store.read('hk_lines')
    kprev = {k: tuple(v) for k, v in (rt.get('hk_kcal_prev') or {}).items()}
    if due:
        notify.send_blocks(report(gf, ld, f'📈 Хоккей · отчёт · {yday:%d.%m}', yday, kcal, kprev, off, tp=tp, dp=dp))
        rt['hk_last_daily'] = str(yday)
        log.append(f'🏒 суточный отчёт {yday:%d.%m} отправлен')
        if yday.weekday() == 6:                           # сутки воскресенья закрылись в пн 12:00 → неделя пн–вс
            d0 = yday - dt.timedelta(days=6)
            notify.send_blocks(report(gf, ld, f'📅 Хоккей · неделя {d0:%d.%m}–{yday:%d.%m}', yday, kcal, kprev, off, d0=d0, tp=tp, dp=dp))
            log.append('🏒 недельный отчёт отправлен')
    txt = report(gf, ld, '🏒 Хоккей · всё время', None, kcal, kprev, off, tp=tp, dp=dp)[0]
    return [dict(key='hk', kind='hk', title='🏒 Хоккей', text=txt, updated=J.now_utc())]
