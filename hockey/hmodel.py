"""🏒 Модель хоккея: голы основного времени — Пуассон с силой атаки/защиты команд, преимуществом своей площадки
и затуханием старых матчей (как Dixon–Coles в футболе); ОТ/буллиты — отдельная вероятность хозяев.

λ_хоз = exp(μ + h + att_хоз − def_гост), λ_гост = exp(μ + att_гост − def_хоз); вес матча = exp(−ξ·дней назад).
Матрица счёта основного времени P[i, j] (i — голы хозяев) → все рынки (hlines.py). Ничья в основное время →
победа хозяев в ОТ/буллитах с вероятностью q_ot (по истории лиги, со сдвигом к силе команд).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import poisson

XI = 0.0035            # затухание в сутки (≈ полвеса за 200 дней) — уточняется бэктестом
REG = 0.02             # L2-сжатие сил к 0 (стабильность для новых команд)
MAXG = 12


@dataclass
class Model:
    league: str
    teams: list[str]
    mu: float
    home: float
    att: dict = field(default_factory=dict)
    dfn: dict = field(default_factory=dict)
    q_ot: float = 0.5           # P(хозяева выигрывают ОТ/буллиты) по лиге
    k_ot: float = 0.0           # наклон q_ot по разнице сил (логит)
    dinf: float = 1.0           # раздувание ничьих основного времени (в хоккее ничьих больше, чем у Пуассона)
    n: int = 0

    def lambdas(self, h: str, a: str) -> tuple[float, float] | None:
        if h not in self.att or a not in self.att:
            return None
        lh = np.exp(self.mu + self.home + self.att[h] - self.dfn[a])
        la = np.exp(self.mu + self.att[a] - self.dfn[h])
        return float(lh), float(la)

    def matrix(self, lh: float, la: float) -> np.ndarray:
        g = np.arange(MAXG + 1)
        P = np.outer(poisson.pmf(g, lh), poisson.pmf(g, la))
        P[np.diag_indices_from(P)] *= self.dinf
        return P / P.sum()

    def ot_home(self, lh: float, la: float) -> float:
        z = np.log(self.q_ot / (1 - self.q_ot)) + self.k_ot * (np.log(lh) - np.log(la))
        return float(1 / (1 + np.exp(-z)))


def shift(m: Model, lh: float, la: float, beta: float) -> tuple[float, float]:
    """λ хозяев × e^c, λ гостей × e^−c — так, чтобы логит «победы с ОТ» хозяев вырос на beta (все рынки сдвигаются согласованно)."""
    def ml(c: float) -> float:
        a, b = lh * np.exp(c), la * np.exp(-c)
        P = m.matrix(a, b)
        i, j = np.indices(P.shape)
        return float(P[i > j].sum() + np.trace(P) * m.ot_home(a, b))
    lg = lambda p: np.log(p / (1 - p))
    target = lg(ml(0.0)) + beta
    lo, hi = (0.0, 0.6) if beta >= 0 else (-0.6, 0.0)
    for _ in range(40):
        mid = (lo + hi) / 2
        if lg(ml(mid)) < target:
            lo = mid
        else:
            hi = mid
    c = (lo + hi) / 2
    return float(lh * np.exp(c)), float(la * np.exp(-c))


def fit(games: pd.DataFrame, league: str, asof: pd.Timestamp, xi: float = XI, reg: float = REG) -> Model | None:
    """Подгонка по завершённым матчам лиги до asof (без утечки)."""
    g = games[(games.league == league) & games.hg_reg.notna() & games.ag_reg.notna()
              & (pd.to_datetime(games.start) < asof) & (games.end.astype(str).isin(['REG', 'OT', 'SO']))]
    g = g[pd.to_datetime(g.start) >= asof - pd.Timedelta(days=900)]
    g = g[~g.home.astype(str).str.contains('Division') & ~g.away.astype(str).str.contains('Division')]   # матчи звёзд КХЛ
    if len(g) < 200:
        return None
    teams = sorted(set(g.home) | set(g.away))
    ix = {t: i for i, t in enumerate(teams)}
    T = len(teams)
    hi, ai = g.home.map(ix).values, g.away.map(ix).values
    yh, ya = g.hg_reg.astype(float).values, g.ag_reg.astype(float).values
    age = (asof - pd.to_datetime(g.start)).dt.total_seconds().values / 86400
    w = np.exp(-xi * age)

    def nll(x):
        mu, h, att, dfn = x[0], x[1], x[2:2 + T], x[2 + T:]
        llh = mu + h + att[hi] - dfn[ai]
        lla = mu + att[ai] - dfn[hi]
        f = np.sum(w * (np.exp(llh) - yh * llh + np.exp(lla) - ya * lla)) + reg * (att @ att + dfn @ dfn) * w.sum() / 100
        gr = np.zeros_like(x)
        rh, ra = w * (np.exp(llh) - yh), w * (np.exp(lla) - ya)
        gr[0] = rh.sum() + ra.sum(); gr[1] = rh.sum()
        np.add.at(gr[2:2 + T], hi, rh); np.add.at(gr[2:2 + T], ai, ra)
        np.add.at(gr[2 + T:], ai, -rh); np.add.at(gr[2 + T:], hi, -ra)
        gr[2:] += 2 * reg * x[2:] * w.sum() / 100
        return f, gr
    x0 = np.zeros(2 + 2 * T); x0[0] = np.log(max(np.average((yh + ya) / 2, weights=w), 0.5))
    r = minimize(nll, x0, jac=True, method='L-BFGS-B')
    x = r.x
    m = Model(league, teams, float(x[0]), float(x[1]), {t: float(x[2 + ix[t]]) for t in teams},
              {t: float(x[2 + T + ix[t]]) for t in teams}, n=len(g))
    lam_all = np.array([m.lambdas(a, b) for a, b in zip(g.home, g.away)])          # ничьи: факт / Пуассон (взвешено)
    gg = np.arange(MAXG + 1)
    pdraw = np.array([float(np.sum(poisson.pmf(gg, x) * poisson.pmf(gg, y))) for x, y in lam_all])
    fact = (g.hg_reg.values == g.ag_reg.values).astype(float)
    r_ = np.average(fact, weights=w) / np.average(pdraw, weights=w)            # доля ничьих растёт в r_ раз → множитель диагонали
    m.dinf = float(np.clip(r_ * (1 - np.average(pdraw, weights=w)) / (1 - r_ * np.average(pdraw, weights=w)), 1.0, 2.5))
    ot = g[g.end.isin(['OT', 'SO'])]                     # ОТ/буллиты: доля побед хозяев и наклон по силе
    if len(ot) >= 50:
        lam = np.array([m.lambdas(a, b) for a, b in zip(ot.home, ot.away)])
        d = np.log(lam[:, 0]) - np.log(lam[:, 1])
        y = (ot.hg.astype(float).values > ot.ag.astype(float).values).astype(float)
        ww = np.exp(-xi * (asof - pd.to_datetime(ot.start)).dt.total_seconds().values / 86400)

        def nll2(p):
            z = p[0] + p[1] * d
            return np.sum(ww * (np.logaddexp(0, z) - y * z)) + 0.5 * p[1] ** 2
        p = minimize(nll2, [0.0, 0.0]).x
        m.q_ot, m.k_ot = float(1 / (1 + np.exp(-p[0]))), float(p[1])
    return m
