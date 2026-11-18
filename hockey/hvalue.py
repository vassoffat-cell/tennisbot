"""17.11 (ТЗ-007, лестница С23 для хоккея): кандидаты ставки дня НХЛ с ЦЕНОЙ 1xBet.

Справедливая P — из Pinnacle (Шин): победа с ОТ + основной тотал → λ хозяев/гостей (hmodel.invert, как A31).
Рынки 1xBet по НХЛ в The Odds API: исход основного времени (3 исхода), тотал (основное время — engine/research/hk_book_rules.py),
фора 0 (правило при ничьей не установлено — не берём). Итог такой ставки — hlines.settle (все рынки, кроме ML, по основному времени).
EV = P × кф − 1 (полушаговые линии — без возврата).
История (13 матчей НХЛ, снимки 16–17.11): P ≥ 0,60 — кандидат у 1 матча из 13; P ≥ 0,55 — у 8 из 13, средний EV −1,0%
(у линий модели без цены ожидание ≈ −5…−9%, A30) → порог хоккея P ≥ 0,55.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import odds as OD
import pick as PK
from hockey import hmodel as HM

P_MIN = 0.55
BOOK = 'onexbet'
BOOKS = ('onexbet', 'marathonbet')          # 19.11 С25а: конторы с тоталом/1X2 основного времени (hk_book_rules.py)
SLOT = {('1X2', 'H'): 20, ('1X2', 'D'): 21, ('1X2', 'A'): 22, ('TOT', 'O'): 23, ('TOT', 'U'): 24}


def _books(ev: dict) -> dict:
    return {b['key']: {m['key']: [(o['name'], o.get('point'), float(o['price'])) for o in m['outcomes']] for m in b.get('markets', [])}
            for b in ev.get('bookmakers', [])}


def lambdas(M, ev: dict):
    """λ хозяев/гостей из Pinnacle: победа с ОТ + тотал (полушаговая пара с кф ближе друг к другу, иначе целая — с поправкой на возврат, как A31)."""
    pin = _books(ev).get('pinnacle', {})
    h2h = {n: k for n, _, k in pin.get('h2h', [])}
    home, away = ev['home_team'], ev['away_team']
    if home not in h2h or away not in h2h:
        return None
    p_home = PK.shin([h2h[home], h2h[away]])[0]
    tot: dict = {}
    for n, pt, k in pin.get('totals', []):
        if pt is not None:
            tot.setdefault(float(pt), {})[n] = k
    pairs = [(pt, v) for pt, v in tot.items() if 'Over' in v and 'Under' in v]
    if not pairs:
        return None                                    # без тотала Pinnacle — нет справедливой цены (без допущений)
    half = [x for x in pairs if not float(x[0]).is_integer()] or pairs
    pt, v = min(half, key=lambda x: abs(x[1]['Over'] - x[1]['Under']))
    p_over = PK.shin([v['Over'], v['Under']])[0]
    lam = HM.invert(M, p_home, pt, p_over)
    if float(pt).is_integer():                         # целая линия (6,0): Шин даёт P «больше» без возврата → P(>L) = p × (1 − P(=L))
        P = M.matrix(*lam)
        i, j = np.indices(P.shape)
        push = float(P[((i + j) == pt) & (i != j)].sum() + P[(i == j) & ((i + j + 1) == pt)].sum())
        lam = HM.invert(M, p_home, pt, p_over * (1 - push))
    return lam


def _match(row: dict, evs: list[dict]) -> dict | None:
    st = pd.Timestamp(row['start_utc'])
    best, sc = None, 0.0
    for ev in evs:
        if abs((pd.Timestamp(ev['commence_time']).tz_convert(None) - st).total_seconds()) > 3 * 3600:
            continue
        s = min(OD.same_name(row['home'], ev['home_team']), OD.same_name(row['away'], ev['away_team']))
        if s > sc:
            best, sc = ev, s
    return best if sc >= 0.5 else None


def candidates(M, row: dict, evs: list[dict]) -> list[dict]:
    """Линии 1xBet матча с P ≥ P_MIN, кф ≥ 1,35, EV ≥ пола: dict(market, pick, line, label, p, k, ev, slot)."""
    ev = _match(row, evs)
    if ev is None or M is None:
        return []
    bks = _books(ev)
    lam = lambdas(M, ev)
    if lam is None:
        return []
    P = M.matrix(*lam)
    i, j = np.indices(P.shape)
    ph, pd_, pa = float(P[i > j].sum()), float(np.trace(P)), float(P[i < j].sum())
    out = []
    # 19.11 С25а: лучшая цена среди контор с правилом основного времени (1xBet, Marathon — С24); у остальных тотал с ОТ
    for book in BOOKS if getattr(PK, 'BEST_PRICE', False) else (BOOK,):
        ob = bks.get(book)
        if not ob:
            continue
        for n, pt, k in ob.get('h2h', []):
            pk = 'H' if n == ev['home_team'] else 'A' if n == ev['away_team'] else 'D' if n == 'Draw' else None
            if pk is None or not any(x[0] == 'Draw' for x in ob['h2h']):        # 3 исхода основного времени
                continue
            p = {'H': ph, 'D': pd_, 'A': pa}[pk]
            out.append(dict(market='1X2', pick=pk, line='', label={'H': 'П1', 'D': 'Х', 'A': 'П2'}[pk], p=p, k=k, book=book))
        for n, pt, k in ob.get('totals', []):
            if pt is None or float(pt).is_integer():
                continue
            po = float(P[(i + j) > float(pt)].sum())                       # основное время (правило 1xBet / Marathon)
            pk = 'O' if n == 'Over' else 'U'
            out.append(dict(market='TOT', pick=pk, line=float(pt), label=f'Т{"Б" if pk == "O" else "М"}{float(pt):g}',
                            p=po if pk == 'O' else 1 - po, k=k, book=book))
    top: dict = {}
    for c in out:                                                       # одна линия — лучшая цена
        kk = (c['market'], c['pick'], c['line'])
        if kk not in top or c['k'] > top[kk]['k']:
            top[kk] = c
    out = list(top.values())
    res = []
    for c in out:
        c['ev'] = c['p'] * c['k'] - 1
        c['slot'] = SLOT[(c['market'], c['pick'])]
        if c['p'] >= P_MIN and c['k'] >= PK.MIN_K and c['ev'] >= PK.EV_FLOOR:
            res.append(c)
    return sorted(res, key=lambda c: -c['ev'])
