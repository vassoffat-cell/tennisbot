"""Модель Dixon–Coles для футбола.

λ_дом = exp(μ_div + h_div + att_дом − def_гость),  λ_гость = exp(μ_div + att_гость − def_дом)
  • att / def — атака и оборона команды (общие для всех дивизионов страны: переходы при повышении/понижении);
  • μ_div — уровень голов дивизиона, h_div — домашнее преимущество;
  • вес матча w = exp(−ξ·дней назад): свежие матчи важнее (затухание по времени);
  • L2-штраф на att/def (сжатие к среднему) — устойчивость для новичков и малых выборок.
Счёт: P(i, j) = Pois(i; λд)·Pois(j; λг)·τ(i, j; ρ) — поправка Dixon–Coles на счета 0:0, 1:0, 0:1, 1:1.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import poisson

MAX_GOALS = 10


@dataclass
class Fit:
    teams: dict[str, int]
    divs: dict[str, int]
    att: np.ndarray
    dfn: np.ndarray
    mu: np.ndarray
    home: np.ndarray
    rho: float

    def lambdas(self, div: str, h: str, a: str) -> tuple[float, float] | None:
        if h not in self.teams or a not in self.teams or div not in self.divs:
            return None
        i, j, d = self.teams[h], self.teams[a], self.divs[div]
        return (float(np.exp(self.mu[d] + self.home[d] + self.att[i] - self.dfn[j])),
                float(np.exp(self.mu[d] + self.att[j] - self.dfn[i])))


def fit(m: pd.DataFrame, asof: pd.Timestamp, xi: float, ridge: float = 2.0, rho: float = -0.06,
        warm: Fit | None = None) -> Fit:
    """Взвешенный пуассоновский максимум правдоподобия по матчам до даты asof (одна страна)."""
    m = m[m.date < asof]
    teams = {t: k for k, t in enumerate(sorted(set(m.home) | set(m.away)))}
    divs = {d: k for k, d in enumerate(sorted(m['div'].unique()))}
    T, Dv = len(teams), len(divs)
    hi = m.home.map(teams).values; ai = m.away.map(teams).values; di = m['div'].map(divs).values
    hg = m.hg.values.astype(float); ag = m.ag.values.astype(float)
    w = np.exp(-xi * (asof - m.date).dt.days.values)

    def unpack(x):
        return x[:T], x[T:2 * T], x[2 * T:2 * T + Dv], x[2 * T + Dv:]

    def f(x):
        att, dfn, mu, home = unpack(x)
        lh = mu[di] + home[di] + att[hi] - dfn[ai]
        la = mu[di] + att[ai] - dfn[hi]
        eh, ea = np.exp(lh), np.exp(la)
        nll = np.sum(w * (eh - hg * lh + ea - ag * la)) + ridge * (att @ att + dfn @ dfn)
        rh, ra = w * (eh - hg), w * (ea - ag)                     # производные по линейным предикторам
        g_att = np.bincount(hi, rh, T) + np.bincount(ai, ra, T) + 2 * ridge * att
        g_dfn = -np.bincount(ai, rh, T) - np.bincount(hi, ra, T) + 2 * ridge * dfn
        g_mu = np.bincount(di, rh + ra, Dv)
        g_home = np.bincount(di, rh, Dv)
        return nll, np.concatenate([g_att, g_dfn, g_mu, g_home])

    x0 = np.zeros(2 * T + 2 * Dv)
    x0[2 * T:2 * T + Dv] = np.log(1.3); x0[2 * T + Dv:] = 0.25
    if warm is not None:                                          # старт с прошлой подгонки — быстрее
        for t, k in teams.items():
            if t in warm.teams:
                x0[k] = warm.att[warm.teams[t]]; x0[T + k] = warm.dfn[warm.teams[t]]
    r = minimize(f, x0, jac=True, method='L-BFGS-B', options={'maxiter': 500})
    att, dfn, mu, home = unpack(r.x)
    return Fit(teams, divs, att, dfn, mu, home, rho)


def score_matrix(lh: float, la: float, rho: float) -> np.ndarray:
    g = np.arange(MAX_GOALS + 1)
    P = np.outer(poisson.pmf(g, lh), poisson.pmf(g, la))
    P[0, 0] *= 1 - lh * la * rho; P[0, 1] *= 1 + lh * rho
    P[1, 0] *= 1 + la * rho;      P[1, 1] *= 1 - rho
    return P / P.sum()


def markets(P: np.ndarray, ah: float | None = None) -> dict:
    """1X2, тотал 2.5, азиатская фора хозяев ah (вероятности выигрыша/возврата с учётом четвертных линий)."""
    i, j = np.indices(P.shape)
    out = dict(h=float(P[i > j].sum()), d=float(np.trace(P)), a=float(P[i < j].sum()),
               o25=float(P[i + j > 2.5].sum()), u25=float(P[i + j < 2.5].sum()))
    if ah is not None and not np.isnan(ah):
        out['ah'] = ah_prob(P, ah)
    return out


def ah_prob(P: np.ndarray, line: float) -> tuple[float, float, float]:
    """(P_выигрыш, P_возврат, P_полувыигрыш/полупроигрыш как доля) для ставки на хозяев с форой line.
    Четвертные линии (−0.25, −0.75 …) = пополам две соседние; возвращается эквивалент (W, push, L) в долях ставки."""
    i, j = np.indices(P.shape)
    d = i - j
    def single(L):
        v = d + L
        return float(P[v > 0].sum()), float(P[v == 0].sum()), float(P[v < 0].sum())
    if abs(line * 4 - round(line * 4)) < 1e-9 and abs(line * 2 - round(line * 2)) > 1e-9:   # четвертная линия
        a, b = single(line - 0.25), single(line + 0.25)
        return tuple((x + y) / 2 for x, y in zip(a, b))
    return single(line)
