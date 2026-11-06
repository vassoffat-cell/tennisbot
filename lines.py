"""Рекомендованные линии с диапазоном коэффициентов (без котировок букмекера).

Для каждого матча 7 линий из одного распределения марковской цепи:
  П1, П2 · фора по геймам 1 и 2 (одна линия с двух сторон) · инд. тотал 1 · инд. тотал 2 · тотал.
Линия форы/тоталов выбирается там, где вероятность стороны модели ближе всего к TARGET_P (≈0,75): тогда диапазон кф
попадает в зону ODDS_ZONE (1,35–1,65), в которой предлагаются ставки. Показывается часть диапазона внутри зоны (z_lo–z_hi);
линии без пересечения с зоной не показываются, но пишутся в журнал и участвуют в самообучении.

Диапазон кф: [ (1+EV_LO)/P ; (1+EV_HI)/P ].
  • нижняя граница — ставка даёт EV ≥ 5 % по модели;
  • верхняя граница — EV ≤ 20 %: при большем расхождении с рынком ошибается, как правило, модель
    (бэктест 2023–2026: EV 5–20 % → ROI −9,1 ± 2,9 %, EV > 50 % → ROI −19,8 ± 9,0 %).
Самокалибровка по рынкам: P' = σ(k_m · logit P), k_m оценивается по рассчитанным линиям этого рынка.
"""
from __future__ import annotations

import numpy as np

import config as C
import markets as MK
from markov import Outcome

TARGET_P = C.TARGET_P          # ≈ 0,75: диапазон кф попадает в зону ODDS_ZONE (1,35–1,65)
MARKET_RU = {'ML': 'Победа', 'HCP': 'Фора по геймам', 'IT1': 'Инд. тотал 1', 'IT2': 'Инд. тотал 2', 'TOTAL': 'Тотал'}


def logit(p):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def scale(p: float, k: float) -> float:
    return float(1 / (1 + np.exp(-k * logit(p))))


def k_range(p: float) -> tuple[float, float]:
    return round((1 + C.EV_THRESHOLD) / p, 2), round((1 + C.EV_MAX) / p, 2)


def zone(lo: float, hi: float) -> tuple[float | str, float | str]:
    """Пересечение диапазона системы с зоной ODDS_ZONE; пусто — линия не показывается."""
    a, b = max(lo, C.ODDS_ZONE[0]), min(hi, C.ODDS_ZONE[1])
    return (round(a, 2), round(b, 2)) if a < b else ('', '')


def _pick_ou(pr, make, grid) -> tuple[float, str, float]:
    """Линия Б/М с вероятностью стороны ≥ 0,5, ближайшей к TARGET_P. Возвращает (линия, 'Б'|'М', P)."""
    best = None
    for L in grid:
        po = pr(make(L, True))
        for side, p in (('Б', po), ('М', 1 - po)):
            if p < 0.5:
                continue
            key = (abs(p - TARGET_P), L)
            if best is None or key < best[0]:
                best = (key, L, side, p)
    return best[1], best[2], best[3]


def build(D: dict, p1: float, kc: dict[str, float]) -> list[dict]:
    """7 линий матча. D — распределение исходов (с позиции игрока 1), p1 — P(П1) после самокалибровки,
    kc — коэффициенты самокалибровки по рынкам."""
    pr = lambda s: MK.price(D, s)[0]
    out = []

    def add(slot, market, pick, line, label, p_raw, k):
        p = scale(p_raw, k)
        lo, hi = k_range(p)
        zl, zh = zone(lo, hi)
        out.append(dict(slot=slot, market=market, pick=pick, line=line, label=label,
                        p_raw=round(p_raw, 4), p=round(p, 4), k_lo=lo, k_hi=hi, z_lo=zl, z_hi=zh))

    add(1, 'ML', '1', '', 'П1', p1, 1.0)
    add(2, 'ML', '2', '', 'П2', 1 - p1, 1.0)
    # фора по геймам: линия для игрока 1, сторона модели ближе к TARGET_P
    hs = [x + 0.5 for x in range(-15, 15)]
    h = min(hs, key=lambda h: (abs(max(pr(MK.mk_handicap(True, h)), 1 - pr(MK.mk_handicap(True, h))) - TARGET_P), abs(h)))
    ph = pr(MK.mk_handicap(True, h))
    k = kc.get('HCP', 1.0)
    p1h = scale(ph, k)
    for slot, pick, line, pp in ((3, '1', h, p1h), (4, '2', -h, 1 - p1h)):
        lo, hi = k_range(pp)
        zl, zh = zone(lo, hi)
        out.append(dict(slot=slot, market='HCP', pick=pick, line=line, label=f'Ф{pick}({line:+g})',
                        p_raw=round(ph if pick == '1' else 1 - ph, 4), p=round(pp, 4), k_lo=lo, k_hi=hi, z_lo=zl, z_hi=zh))
    for slot, market, a in ((5, 'IT1', True), (6, 'IT2', False)):
        L, side, p = _pick_ou(pr, lambda L, o: MK.mk_ind_total(a, L, o), [x + 0.5 for x in range(1, 26)])
        add(slot, market, 'O' if side == 'Б' else 'U', L, f'{side} {L:g}', p, kc.get(market, 1.0))
    L, side, p = _pick_ou(pr, MK.mk_total, [x + 0.5 for x in range(10, 50)])
    add(7, 'TOTAL', 'O' if side == 'Б' else 'U', L, f'{side} {L:g}', p, kc.get('TOTAL', 1.0))
    return out


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
    """k самокалибровки: максимум правдоподобия σ(k·logit P) по рассчитанным ставкам, сжатие к 1 (априори prior матчей)."""
    prior = C.CALIB_PRIOR if prior is None else prior
    ok = ~np.isnan(p_raw)
    x, y = logit(p_raw[ok]), won[ok].astype(float)
    n = len(x)
    if n < 30:
        return 1.0, n
    ks = np.linspace(0.5, 1.6, 221)
    k_hat = float(ks[int(np.argmin([np.sum(np.logaddexp(0, k * x) - y * k * x) for k in ks]))])
    w = n / (n + prior)
    return round(1 + w * (k_hat - 1), 4), n
