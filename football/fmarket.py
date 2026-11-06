"""Рынок → ожидаемые голы. Маржа снимается методом Шина, затем двумерная инверсия: λ хозяев и гостей, при которых
матрица счёта Dixon–Coles воспроизводит рыночные P(П1), P(П2) и P(тотал больше линии). Из λ строятся все линии,
поэтому исход, фора, тоталы и «обе забьют» согласованы с рынком и между собой."""
from __future__ import annotations

import numpy as np
from scipy.optimize import brentq, minimize

import config as C
import dc


def shin(odds) -> np.ndarray:
    """Истинные вероятности по котировкам с маржой (Шин, 1993): z — доля информированных игроков."""
    pi = 1 / np.asarray(odds, float)
    s = pi.sum()
    if not np.all(np.isfinite(pi)) or s <= 1:
        return pi / s

    def p(z):
        return (np.sqrt(z * z + 4 * (1 - z) * pi * pi / s) - z) / (2 * (1 - z))
    try:
        z = brentq(lambda z: p(z).sum() - 1, 0.0, 0.99)
    except ValueError:
        return pi / s
    return p(z)


def _probs(lh: float, la: float, line: float | None) -> tuple[float, float, float | None]:
    P = dc.score_matrix(lh, la, C.RHO)
    i, j = np.indices(P.shape)
    po = None
    if line is not None:
        win, push = float(P[(i + j) > line].sum()), float(P[(i + j) == line].sum())
        po = win / (1 - push) if push < 1 else None                     # целая линия: возврат исключается
    return float(P[i > j].sum()), float(P[i < j].sum()), po


def invert(odds: dict) -> dict | None:
    """odds: h, d, a (+ line, over, under). Возвращает λ, рыночные вероятности и маржу; None — котировок нет."""
    try:
        q = shin([odds['h'], odds['d'], odds['a']])
    except (KeyError, TypeError):
        return None
    line, po = odds.get('line'), None
    quarter = line is not None and abs(line * 2 - round(line * 2)) > 1e-9          # 2.25, 2.75 — не используем
    if line is not None and not quarter and odds.get('over') and odds.get('under'):
        po = float(shin([odds['over'], odds['under']])[0])
    tgt = np.array([q[0], q[2]] + ([po] if po is not None else []))
    w = np.array([1.0, 1.0] + ([1.0] if po is not None else []))

    def loss(x):
        lh, la = np.exp(x)
        ph, pa, pov = _probs(lh, la, line if po is not None else None)
        v = np.array([ph, pa] + ([pov] if po is not None else []))
        return float(np.sum(w * (v - tgt) ** 2))
    r = minimize(loss, np.log([1.5, 1.1]), method='Nelder-Mead', options={'xatol': 1e-4, 'fatol': 1e-10, 'maxiter': 400})
    lh, la = (float(v) for v in np.exp(r.x))
    margin = float(sum(1 / odds[k] for k in ('h', 'd', 'a')) - 1)
    return dict(lh=lh, la=la, ph=float(q[0]), pd=float(q[1]), pa=float(q[2]), po=po, line=line,
                fit_err=float(np.sqrt(r.fun / len(tgt))), margin=margin)
