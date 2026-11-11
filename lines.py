"""Линии матча без котировок букмекера (система независима от рынка — решение 07.11.2026).

Для каждого матча 7 линий из одного распределения марковской цепи:
  П1, П2 · фора по геймам 1 и 2 (одна линия с двух сторон) · инд. тотал 1 · инд. тотал 2 · тотал.
Значение форы/тоталов выбирается там, где вероятность стороны модели ближе всего к TARGET_P (≈0,75) — правило выбора.
Показываются линии с 0,50 ≤ P ≤ 0,80 (справедливый кф 1/P ≥ MIN_ODDS = 1,25): линии с низким кф не предлагаются;
исход (П1/П2) называется в каждом матче отдельной строкой.
Уверенность P_low = σ(logit P − σ_n): σ_n — измеренная ошибка модели (0,76 при < 15 матчах игроков … 0,44 при ≥ 120).
Самокалибровка по рынкам: P' = σ(k_m · logit P), k_m — раз в сутки по рассчитанным показанным линиям рынка.
"""
from __future__ import annotations

import numpy as np

import config as C
import markets as MK
from markov import Outcome

TARGET_P = C.TARGET_P          # ≈ 0,75: правило выбора значения форы/тоталов
MARKET_RU = {'ML': 'Победа', 'HCP': 'Фора по геймам', 'IT1': 'Инд. тотал 1', 'IT2': 'Инд. тотал 2', 'TOTAL': 'Тотал'}


def logit(p):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def scale(p: float, k: float) -> float:
    return float(1 / (1 + np.exp(-k * logit(p))))


def sigma_for(n_min: int) -> float:
    return next(s for lim, s in C.SIGMA_BY_N if n_min < lim)


def scale_shift(p: float, d: float) -> float:
    """σ(logit P + d)."""
    return float(1 / (1 + np.exp(-(logit(p) + d))))


def visible(p: float, market: str) -> bool:
    """Линия предлагается: сторона модели (P ≥ 0,5) и справедливый кф не ниже MIN_ODDS. Исход — отдельной строкой."""
    return market != 'ML' and C.P_SHOW[0] <= p <= C.P_SHOW[1]


def _key(p: float, L: float):
    """Ключ выбора линии. Вариант Б (выбор пользователя 10.11.2026, C.LINE_P_MIN): ближе всего к центру среди линий,
    где P стороны ≥ C.LINE_P_MIN (меньшая подходящая P); иначе — P ближе всего к TARGET_P."""
    if getattr(C, 'LINE_P_MIN', None):
        return (p < C.LINE_P_MIN, p if p >= C.LINE_P_MIN else -p, L)
    return (abs(p - TARGET_P), L)


def _pick_ou(pr, make, grid) -> tuple[float, str, float]:
    """Линия Б/М с вероятностью стороны ≥ 0,5 по правилу выбора (_key). Возвращает (линия, 'Б'|'М', P)."""
    best = None
    for L in grid:
        po = pr(make(L, True))
        for side, p in (('Б', po), ('М', 1 - po)):
            if p < 0.5:
                continue
            key = _key(p, L)
            if best is None or key < best[0]:
                best = (key, L, side, p)
    return best[1], best[2], best[3]


def build(D: dict, p1: float, kc: dict[str, float], sigma: float, fixed_total: float | None = None) -> list[dict]:
    """7 линий матча. D — распределение исходов (с позиции игрока 1), p1 — P(П1) после самокалибровки,
    kc — коэффициенты самокалибровки по рынкам, sigma — неопределённость модели для этого матча."""
    pr = lambda s: MK.price(D, s)[0]
    out = []

    def add(slot, market, pick, line, label, p_raw, k):
        p = scale(p_raw, k)
        out.append(dict(slot=slot, market=market, pick=pick, line=line, label=label, p_raw=round(p_raw, 4), p=round(p, 4),
                        k_lo=round(1 / max(p, 1e-6), 2), k_hi='', z_lo='', z_hi='', shown=int(visible(p, market))))

    add(1, 'ML', '1', '', 'П1', p1, 1.0)
    add(2, 'ML', '2', '', 'П2', 1 - p1, 1.0)
    # фора по геймам: линия для игрока 1, сторона модели ближе к TARGET_P
    hs = [x + 0.5 for x in range(-15, 15)]
    h = min(hs, key=lambda h: _key(max(pr(MK.mk_handicap(True, h)), 1 - pr(MK.mk_handicap(True, h))), abs(h)))
    ph = pr(MK.mk_handicap(True, h))
    k = kc.get('HCP', 1.0)
    p1h = scale(ph, k)
    for slot, pick, line, pp in ((3, '1', h, p1h), (4, '2', -h, 1 - p1h)):
        out.append(dict(slot=slot, market='HCP', pick=pick, line=line, label=f'Ф{pick}({line:+g})',
                        p_raw=round(ph if pick == '1' else 1 - ph, 4), p=round(pp, 4), k_lo=round(1 / max(pp, 1e-6), 2),
                        k_hi='', z_lo='', z_hi='', shown=int(visible(pp, 'HCP'))))
    for slot, market, a in ((5, 'IT1', True), (6, 'IT2', False)):
        L, side, p = _pick_ou(pr, lambda L, o: MK.mk_ind_total(a, L, o), [x + 0.5 for x in range(1, 26)])
        add(slot, market, 'O' if side == 'Б' else 'U', L, f'{side} {L:g}', p, kc.get(market, 1.0))
    if fixed_total:                                 # T32: одна линия тотала на тур и формат — «больше или меньше», всегда показана
        po = pr(MK.mk_total(fixed_total, True))
        side, p = ('Б', po) if po >= 0.5 else ('М', 1 - po)
        add(7, 'TOTAL', 'O' if side == 'Б' else 'U', fixed_total, f'{side} {fixed_total:g}', p, kc.get('TOTAL', 1.0))
        out[-1]['shown'] = 1
    else:
        L, side, p = _pick_ou(pr, MK.mk_total, [x + 0.5 for x in range(10, 50)])
        add(7, 'TOTAL', 'O' if side == 'Б' else 'U', L, f'{side} {L:g}', p, kc.get('TOTAL', 1.0))
    for x in out:                                   # уверенность: вероятность с поправкой на типичную ошибку модели
        x['p_low'] = round(scale_shift(x['p'], -C.SIGMA_Z * sigma), 4)
    return out


def tier(p_low: float) -> int:
    """3 — высокая уверенность, 2 — средняя, 1 — низкая (пороги C.CONF_TIERS)."""
    hi, mid = C.CONF_TIERS
    return 3 if p_low >= hi else 2 if p_low >= mid else 1


def settle(market: str, pick: str, line, o: Outcome) -> str:
    """'W' / 'L' / 'P' для линии по исходу матча (счёт по сетам с позиции игрока 1)."""
    L = float(line) if str(line).strip() not in ('', 'nan', 'None') else None
    if market == 'ML':
        return MK.mk_winner(pick == '1')(o)
    if market == 'HCP':
        return MK.mk_handicap(pick == '1', L)(o)
    if market in ('IT1', 'IT2'):
        return MK.mk_ind_total(market == 'IT1', L, pick == 'O')(o)
    if market == 'TOTAL':
        return MK.mk_total(L, pick == 'O')(o)
    raise ValueError(market)


def calibrate(p_raw: np.ndarray, won: np.ndarray, prior: float | None = None) -> tuple[float, int]:
    """k самокалибровки: максимум правдоподобия σ(k·logit P) по рассчитанным линиям/матчам, сжатие к 1 (n/(n+300)).
    n — число рассчитанных (W/L); включается с CALIB_MIN = 30."""
    prior = C.CALIB_PRIOR if prior is None else prior
    ok = ~np.isnan(p_raw)
    x, y = logit(p_raw[ok]), won[ok].astype(float)
    n = len(x)
    if n < C.CALIB_MIN:
        return 1.0, n
    ks = np.linspace(0.5, 1.6, 221)
    k_hat = float(ks[int(np.argmin([np.sum(np.logaddexp(0, k * x) - y * k * x) for k in ks]))])
    w = n / (n + prior)
    return round(1 + w * (k_hat - 1), 4), n
