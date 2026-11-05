"""Quant Engine v5.0 — шаг 4: рыночный модуль.

1. Очистка маржи: Шин (по умолчанию), степенной, пропорциональный; n исходов.
   На целой линии букмекер платит возврат при равенстве, поэтому очищенная вероятность —
   это P(сторона | нет возврата); с моделью сравнивается так же.
2. Инверсия: по победителю матча и тоталам геймов находим (pa, pb) — вероятности очка на подаче.
   Параметризация pa = s − d, pb = s + d: d отвечает за победителя, s — за тотал (при s ≥ 0,5
   тотал монотонно растёт по s). Взвешенный МНК по победителю и всем линиям тотала.
3. Feasibility Guard: если ни одна пара (pa, pb) не воспроизводит линию в допуске — INFEASIBLE.
4. Аудит линии: по найденной цепи считаются все остальные линии, расхождения с ценами
   букмекера, K_Fair, EV; одно и то же событие по двум ценам помечается.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
from scipy.optimize import brentq, least_squares

from markov import match_distribution
import markets as M

# параметры momentum: (в сете, между сетами); подбор на 2019–2023 (закрытие Pinnacle), проверка на 2024–2025 и 2026
MOMENTUM = {'wta': (0.071, 0.182), 'atp': (0.061, 0.187)}
S_RANGE = (0.50, 0.80)          # допустимый уровень подачи
TOL_FIT = 0.005                 # допуск инверсии: 0,5 п.п.


# ---------------------------------------------------------------- очистка маржи
def devig(odds: list[float], method: str = 'shin') -> np.ndarray:
    """Очищенные вероятности исходов по коэффициентам одного рынка."""
    pi = 1.0 / np.asarray(odds, float)
    B = pi.sum()
    if B <= 1.0 + 1e-9 or method == 'proportional':
        return pi / B
    if method == 'shin':
        p = lambda z: (np.sqrt(z * z + 4 * (1 - z) * pi * pi / B) - z) / (2 * (1 - z))
        z = brentq(lambda z: p(z).sum() - 1, 0.0, 0.5, xtol=1e-14)
        return p(z)
    if method == 'power':
        k = brentq(lambda k: (pi ** k).sum() - 1, 1.0, 10.0, xtol=1e-14)
        return pi ** k
    raise ValueError(method)


# ---------------------------------------------------------------- описание линий
@dataclass
class Line:
    """Двусторонняя линия: settle1 / settle2 — расчёт сторон, k1 / k2 — коэффициенты букмекера."""
    name: str
    settle1: M.Settle
    settle2: M.Settle
    k1: float
    k2: float
    kind: str = 'other'          # 'ml' | 'total' | 'other'
    event1: str = ''             # ключ события стороны 1 (для поиска дублей)


def ml(k1: float, k2: float) -> Line:
    return Line('Победитель', M.mk_winner(True), M.mk_winner(False), k1, k2, 'ml', 'match_A')


def total(line: float, k_over: float, k_under: float) -> Line:
    return Line(f'Тотал {line}', M.mk_total(line, True), M.mk_total(line, False), k_over, k_under, 'total')


def hcp(h_a: float, k_a: float, k_b: float) -> Line:
    return Line(f'Фора A({h_a:+g})', M.mk_handicap(True, h_a), M.mk_handicap(False, -h_a), k_a, k_b)


def ind_total(player_a: bool, line: float, k_over: float, k_under: float) -> Line:
    who = 'A' if player_a else 'B'
    return Line(f'ИТ{who} {line}', M.mk_ind_total(player_a, line, True), M.mk_ind_total(player_a, line, False), k_over, k_under)


def set_ml(i: int, k1: float, k2: float) -> Line:
    return Line(f'{i+1}-й сет: победитель', M.mk_set_winner(i, True), M.mk_set_winner(i, False), k1, k2, 'other', f'set{i}_A')


def set_total(i: int, line: float, k_over: float, k_under: float) -> Line:
    return Line(f'{i+1}-й сет: тотал {line}', M.mk_set_total(i, line, True), M.mk_set_total(i, line, False), k_over, k_under)


def set_hcp(i: int, h_a: float, k_a: float, k_b: float) -> Line:
    # в сете не бывает разницы 0 геймов, поэтому фора A(+0,5) — то же событие, что «A выигрывает сет»
    ev = f'set{i}_A' if h_a == 0.5 else ''
    return Line(f'{i+1}-й сет: фора A({h_a:+g})', M.mk_set_handicap_games(i, True, h_a),
                M.mk_set_handicap_games(i, False, -h_a), k_a, k_b, 'other', ev)


def set_ind_total(i: int, player_a: bool, line: float, k_over: float, k_under: float) -> Line:
    who = 'A' if player_a else 'B'
    return Line(f'{i+1}-й сет: ИТ{who} {line}', M.mk_set_ind_total(i, player_a, line, True),
                M.mk_set_ind_total(i, player_a, line, False), k_over, k_under)


# ---------------------------------------------------------------- инверсия
@dataclass
class Fit:
    status: str                      # 'FEASIBLE' | 'INFEASIBLE'
    pa: float
    pb: float
    residuals: dict[str, float]      # модель − рынок по линиям инверсии, в долях
    note: str = ''


@dataclass
class Audit:
    fit: Fit
    rows: list[dict] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)


def _cond(dist, settle: M.Settle) -> tuple[float, float]:
    """P(сторона | нет возврата) и P(возврат)."""
    w, p, _ = M.price(dist, settle)
    return (w / (1 - p) if p < 1 else 0.0), p


def invert(lines: list[Line], tour: str, best_of: int = 3, final_tb_to: int = 7) -> Fit:
    mom, set_mom = MOMENTUM[tour]
    dist = lambda pa, pb: match_distribution(pa, pb, best_of, final_tb_to, mom, set_mom)
    ml_line = next(l for l in lines if l.kind == 'ml')
    tot_lines = [l for l in lines if l.kind == 'total']
    p_ml = devig([ml_line.k1, ml_line.k2])[0]
    p_tot = {l.name: devig([l.k1, l.k2])[0] for l in tot_lines}

    def d_for(s: float) -> float:
        """Разница подач, при которой цепь даёт рыночного победителя (монотонно по d)."""
        f = lambda d: M.price(dist(s - d, s + d), ml_line.settle1)[0] - p_ml
        lo, hi = -min(s - 0.30, 0.95 - s), min(s - 0.30, 0.95 - s)
        return brentq(f, lo, hi, xtol=1e-10)

    def resid(x: np.ndarray) -> np.ndarray:
        s = float(x[0]); d = d_for(s)
        D = dist(s - d, s + d)
        return np.array([_cond(D, l.settle1)[0] - p_tot[l.name] for l in tot_lines])

    # стартовая точка: перебор s по сетке, затем МНК в допустимом диапазоне
    grid = np.linspace(*S_RANGE, 13)
    s0 = min(grid, key=lambda s: float(np.sum(resid(np.array([s])) ** 2)))
    sol = least_squares(resid, x0=[s0], bounds=([S_RANGE[0]], [S_RANGE[1]]), xtol=1e-12, ftol=1e-14)
    s = float(sol.x[0]); d = d_for(s)
    D = dist(s - d, s + d)
    res = {ml_line.name: M.price(D, ml_line.settle1)[0] - p_ml}
    res.update({l.name: _cond(D, l.settle1)[0] - p_tot[l.name] for l in tot_lines})
    worst = max(abs(v) for v in res.values())
    status = 'FEASIBLE' if worst <= TOL_FIT else 'INFEASIBLE'
    note = '' if status == 'FEASIBLE' else (
        f'ни одна пара (pa, pb) при s в [{S_RANGE[0]}, {S_RANGE[1]}] не воспроизводит линию: '
        f'наибольшее расхождение {worst*100:.2f} п.п.')
    return Fit(status, s - d, s + d, res, note)


def audit(lines: list[Line], tour: str, best_of: int = 3, final_tb_to: int = 7,
          extra: list[Line] | None = None) -> Audit:
    """Инверсия по победителю и тоталам + оценка всех остальных линий (extra) этой цепью."""
    fit = invert(lines, tour, best_of, final_tb_to)
    mom, set_mom = MOMENTUM[tour]
    D = match_distribution(fit.pa, fit.pb, best_of, final_tb_to, mom, set_mom)
    out = Audit(fit)
    for l in lines + (extra or []):
        w1, p1, _ = M.price(D, l.settle1); w2, p2, _ = M.price(D, l.settle2)
        book = devig([l.k1, l.k2])
        out.rows.append({
            'линия': l.name, 'роль': 'инверсия' if l.kind in ('ml', 'total') else 'проверка',
            'P_мод': w1 / (1 - p1), 'P_БК': book[0], 'Δ': w1 / (1 - p1) - book[0], 'возврат': p1,
            'маржа': 1 / l.k1 + 1 / l.k2 - 1,
            'K_fair1': M.fair_odds(w1, p1), 'K1': l.k1, 'EV1': M.ev(w1, p1, l.k1),
            'K_fair2': M.fair_odds(w2, p2), 'K2': l.k2, 'EV2': M.ev(w2, p2, l.k2)})
    # одно и то же событие по разным ценам: стороны с одинаковым расчётом во ВСЕХ исходах матча
    outcomes = sorted(D, key=lambda o: o.sets)
    groups: dict[tuple, list[tuple[str, float]]] = {}
    for l in lines + (extra or []):
        for side, settle, k in ((1, l.settle1, l.k1), (2, l.settle2, l.k2)):
            sig = tuple(settle(o) for o in outcomes)
            groups.setdefault(sig, []).append((f'{l.name} [сторона {side}]', k))
    for members in groups.values():
        if len(members) > 1:
            best = max(members, key=lambda m: m[1])
            out.duplicates.append(' = '.join(f'{n} по {k}' for n, k in members) + f' → брать {best[0]} ({best[1]})')
    return out
