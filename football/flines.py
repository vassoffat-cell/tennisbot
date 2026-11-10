"""Футбол: линии матча из одного распределения счёта (90 минут) — только модель (система независима от букмекеров).

Рынки: 1X2, двойной шанс, азиатская фора (полулинии — без возврата), тотал, индивидуальные тоталы, «обе забьют».
Значение форы/тоталов выбирается там, где вероятность стороны ближе всего к TARGET_P (≈0,75) — это правило выбора линии.
Показываются линии с 0,50 ≤ P ≤ 0,80 (справедливый кф 1/P ≥ MIN_ODDS = 1,25): линии с низким кф не предлагаются.
Уверенность P_low = σ(logit P − σ_m), σ_m — типичная ошибка модели (1X2/ДШ/фора 0,38, тоталы 0,24).
"""
from __future__ import annotations

import numpy as np

import config as C

MARKET_RU = {'1X2': 'Исход', 'DC': 'Двойной шанс', 'AH': 'Фора', 'TOT': 'Тотал', 'IT1': 'Инд. тотал 1',
             'IT2': 'Инд. тотал 2', 'BTTS': 'Обе забьют'}


def logit(p):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def scale(p: float, k: float) -> float:
    return float(1 / (1 + np.exp(-k * logit(p))))


def visible(p: float, market: str) -> bool:
    """Линия предлагается: сторона модели (P ≥ 0,5) и справедливый кф не ниже MIN_ODDS. Исход (1X2) — отдельной строкой матча."""
    return market != '1X2' and market not in C.MARKETS_OFF and C.P_SHOW[0] <= p <= C.P_SHOW[1]


def build(P: np.ndarray, kc: dict[str, float]) -> list[dict]:
    """11 линий матча (слоты 1–11). P — матрица счёта 90 минут (строки — голы хозяев)."""
    i, j = np.indices(P.shape)
    ph, pd_, pa = P[i > j].sum(), np.trace(P), P[i < j].sum()
    out = []

    def add(slot, market, pick, line, label, p_raw):
        p = scale(p_raw, kc.get(market, 1.0))
        out.append(dict(slot=slot, market=market, pick=pick, line=line, label=label, p_raw=round(float(p_raw), 4),
                        p=round(p, 4), k_lo=round(1 / max(p, 1e-6), 2), k_hi='', z_lo='', z_hi='',
                        p_low=round(float(1 / (1 + np.exp(-(logit(p) - C.SIGMA[market])))), 4),
                        shown=int(visible(p, market))))

    add(1, '1X2', 'H', '', 'П1', ph); add(2, '1X2', 'D', '', 'X', pd_); add(3, '1X2', 'A', '', 'П2', pa)
    add(4, 'DC', '1X', '', '1X', ph + pd_); add(5, 'DC', 'X2', '', 'X2', pd_ + pa); add(6, 'DC', '12', '', '12', ph + pa)
    # фора: полулинии ±1.5…±3.5 (±0.5 совпадает с П1/П2 и 1X/X2), сторона с P ближе к TARGET_P
    best = None
    for L in (-3.5, -2.5, -1.5, 1.5, 2.5, 3.5):
        p_home = float(P[(i - j + L) > 0].sum())
        for side, p, line in (('H', p_home, L), ('A', 1 - p_home, -L)):
            key = (abs(p - C.TARGET_P), abs(line))
            if best is None or key < best[0]:
                best = (key, side, line, p)
    _, side, line, p = best
    add(7, 'AH', side, float(line), f'Ф{1 if side == "H" else 2}({line:+g})', p)

    def ou(slot, market, goals, lines, prefix):
        best = None
        for L in lines:
            po = float(P[goals > L].sum())
            for s, p in (('O', po), ('U', 1 - po)):
                if p >= 0.5 and (best is None or (abs(p - C.TARGET_P), L) < best[0]):
                    best = ((abs(p - C.TARGET_P), L), s, L, p)
        _, s, L, p = best
        add(slot, market, s, float(L), f'{prefix}{"Б" if s == "O" else "М"}{L:g}', p)
    ou(8, 'TOT', i + j, np.arange(0.5, 6.6, 1.0), 'Т')
    ou(9, 'IT1', i, np.arange(0.5, 4.6, 1.0), 'ИТ1 ')
    ou(10, 'IT2', j, np.arange(0.5, 4.6, 1.0), 'ИТ2 ')
    yes = float(P[(i >= 1) & (j >= 1)].sum())
    add(11, 'BTTS', 'Y' if yes >= 0.5 else 'N', '', 'ОЗ да' if yes >= 0.5 else 'ОЗ нет', max(yes, 1 - yes))
    return out


def tier(p_low: float) -> int:
    """3 — высокая уверенность, 2 — средняя, 1 — низкая (пороги C.CONF_TIERS)."""
    hi, mid = C.CONF_TIERS
    return 3 if p_low >= hi else 2 if p_low >= mid else 1


def settle(market: str, pick: str, line, kind: str, hg: int | None, ag: int | None) -> str:
    """'W' / 'L' / 'P'. kind: 'FT' — счёт 90 минут; 'ET' — ничья в основное время (счёт неизвестен); иное — возврат."""
    if kind == 'ET':
        if market == '1X2':
            return 'W' if pick == 'D' else 'L'
        if market == 'DC':
            return 'L' if pick == '12' else 'W'
        return 'P'
    if kind != 'FT' or hg is None or ag is None:
        return 'P'
    L = float(line) if str(line).strip() not in ('', 'nan', 'None') else None
    if market == '1X2':
        res = 'H' if hg > ag else 'D' if hg == ag else 'A'
        return 'W' if pick == res else 'L'
    if market == 'DC':
        res = 'H' if hg > ag else 'D' if hg == ag else 'A'
        return 'W' if res in {'1X': 'HD', 'X2': 'DA', '12': 'HA'}[pick] else 'L'
    if market == 'AH':
        v = (hg - ag + L) if pick == 'H' else (ag - hg + L)
        return 'W' if v > 0 else 'P' if v == 0 else 'L'
    goals = {'TOT': hg + ag, 'IT1': hg, 'IT2': ag}.get(market)
    if goals is not None:
        return 'P' if goals == L else ('W' if (goals > L) == (pick == 'O') else 'L')
    if market == 'BTTS':
        return 'W' if ((hg > 0 and ag > 0) == (pick == 'Y')) else 'L'
    raise ValueError(market)


def calibrate(p_raw: np.ndarray, won: np.ndarray) -> tuple[float, int]:
    """k самокалибровки рынка: максимум правдоподобия σ(k·logit P), сжатие к 1 (n/(n+300)); n — рассчитанные линии (W/L)."""
    ok = ~np.isnan(p_raw)
    x, y = logit(p_raw[ok]), won[ok].astype(float)
    n = len(x)
    if n < C.CALIB_MIN:
        return 1.0, n
    ks = np.linspace(0.5, 1.6, 221)
    k_hat = float(ks[int(np.argmin([np.sum(np.logaddexp(0, k * x) - y * k * x) for k in ks]))])
    w = n / (n + C.CALIB_PRIOR)
    return round(1 + w * (k_hat - 1), 4), n
