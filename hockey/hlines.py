"""🏒 Линии хоккея из матрицы счёта основного времени (как в футболе) + победа с учётом ОТ/буллитов.

Рынки: исход основного времени (П1/X/П2), победа в матче с ОТ (П1 ОТ / П2 ОТ), двойной шанс, фора, тотал (одна линия лиги,
«больше/меньше», всегда показан — T32), индивидуальные тоталы. Значение форы/ИТ — ближе к центру при P стороны ≥ 0,65
(правило Б, как в теннисе): без «космических» линий. Показ — P 0,55–0,80 (кф ≥ 1,25), тотал — всегда.
"""
from __future__ import annotations

import numpy as np

P_SHOW = (0.55, 0.80)
LINE_P_MIN = 0.65
# Тотал фикс. (T32) — решение пользователя 11.11.2026 23:01: НХЛ 4,5, КХЛ 3,5 (было 5,5 / 4,5).
# Факт «больше» в основное время (история): НХЛ Б4,5 68,9–75,6% (сезоны 2024–27), КХЛ Б3,5 73,2–77,0% (2022–25).
# Линия ниже середины → почти всегда «Б» и кф букмекера ≈ 1,25–1,40: высокий % угаданного — от линии, а не от модели;
# в отчёте цель тотала = средняя P модели, сравнение честное (z). Старые линии 5,5 из калибровки и отчёта исключены (hrun._done).
FIXED_TOTAL = {'NHL': 4.5, 'KHL': 3.5}
SIGMA = {'1X2': 0.38, 'ML': 0.38, 'DC': 0.38, 'AH': 0.38, 'TOT': 0.24, 'IT1': 0.24, 'IT2': 0.24}
MARKET_RU = {'1X2': 'Исход осн.', 'ML': 'Победа с ОТ', 'DC': 'Двойной шанс', 'AH': 'Фора', 'TOT': 'Тотал',
             'IT1': 'ИТ1', 'IT2': 'ИТ2'}


def logit(p):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def scale(p: float, k: float) -> float:
    return float(1 / (1 + np.exp(-k * logit(p))))


def visible(p: float, market: str) -> bool:
    return market not in ('1X2',) and P_SHOW[0] <= p <= P_SHOW[1]


def _near_center(cands):
    """(p, line, side…) → ближе к центру среди P ≥ LINE_P_MIN, иначе — наибольшая P."""
    ok = [c for c in cands if c[0] >= LINE_P_MIN]
    return min(ok, key=lambda c: (c[0], abs(c[1]))) if ok else max(cands, key=lambda c: c[0])


def build(P: np.ndarray, q_ot: float, league: str, kc: dict[str, float]) -> list[dict]:
    i, j = np.indices(P.shape)
    ph, pd_, pa = float(P[i > j].sum()), float(np.trace(P)), float(P[i < j].sum())
    out = []

    def add(slot, market, pick, line, label, p_raw, force=False):
        p = scale(p_raw, kc.get(market, 1.0))
        out.append(dict(slot=slot, market=market, pick=pick, line=line, label=label, p_raw=round(float(p_raw), 4),
                        p=round(p, 4), k_lo=round(1 / max(p, 1e-6), 2),
                        p_low=round(float(1 / (1 + np.exp(-(logit(p) - SIGMA[market])))), 4),
                        shown=int(force or visible(p, market))))
    add(1, '1X2', 'H', '', 'П1 осн.', ph); add(2, '1X2', 'D', '', 'X осн.', pd_); add(3, '1X2', 'A', '', 'П2 осн.', pa)
    mh = ph + pd_ * q_ot
    add(4, 'ML', 'H', '', 'П1 ОТ', mh); add(5, 'ML', 'A', '', 'П2 ОТ', 1 - mh)
    add(6, 'DC', '1X', '', '1X', ph + pd_); add(7, 'DC', 'X2', '', 'X2', pd_ + pa); add(8, 'DC', '12', '', '12', ph + pa)
    cands = []
    for L in (-3.5, -2.5, -1.5, 1.5, 2.5, 3.5):
        p_home = float(P[(i - j + L) > 0].sum())
        cands += [(p_home, L, 'H'), (1 - p_home, -L, 'A')]
    p, line, side = _near_center([c for c in cands if c[0] >= 0.5])
    add(9, 'AH', side, float(line), f'Ф{1 if side == "H" else 2}({line:+g})', p)
    L = FIXED_TOTAL.get(league, 5.5)
    po = float(P[(i + j) > L].sum())
    s, p = ('O', po) if po >= 0.5 else ('U', 1 - po)
    add(10, 'TOT', s, L, f'Т{"Б" if s == "O" else "М"}{L:g}', p, force=True)
    for slot, mk, g, pre in ((11, 'IT1', i, 'ИТ1 '), (12, 'IT2', j, 'ИТ2 ')):
        c = []
        for LL in np.arange(0.5, 5.6, 1.0):
            po = float(P[g > LL].sum())
            c += [(po, LL, 'O'), (1 - po, LL, 'U')]
        p, LL, s = _near_center([x for x in c if x[0] >= 0.5])
        add(slot, mk, s, float(LL), f'{pre}{"Б" if s == "O" else "М"}{LL:g}', p)
    return out


def settle(market: str, pick: str, line, end: str, hg, ag, hg_reg, ag_reg) -> str:
    """'W' / 'L' / 'P'. Все рынки, кроме «Победа с ОТ», — по основному времени (hg_reg, ag_reg)."""
    if end not in ('REG', 'OT', 'SO') or hg_reg is None or ag_reg is None or hg_reg != hg_reg or ag_reg != ag_reg:
        return 'P'
    h, a = int(hg_reg), int(ag_reg)
    L = None if line in ('', None) or str(line) == 'nan' else float(line)
    if market == 'ML':
        return 'W' if ((int(hg) > int(ag)) == (pick == 'H')) else 'L'
    if market == '1X2':
        return 'W' if pick == ('H' if h > a else 'D' if h == a else 'A') else 'L'
    if market == 'DC':
        res = 'H' if h > a else 'D' if h == a else 'A'
        return 'W' if res in {'1X': 'HD', 'X2': 'DA', '12': 'HA'}[pick] else 'L'
    if market == 'AH':
        v = (h - a + L) if pick == 'H' else (a - h + L)
        return 'W' if v > 0 else 'P' if v == 0 else 'L'
    goals = {'TOT': h + a, 'IT1': h, 'IT2': a}[market]
    return 'P' if goals == L else ('W' if (goals > L) == (pick == 'O') else 'L')
