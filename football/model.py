"""Модель для всех турниров.

Чемпионаты и национальные кубки: подгонка Dixon–Coles по стране (все её дивизионы, переходы команд связывают уровни).
Две подгонки: со слабым сжатием (разница сил S = λд − λг) и с сильным (общий уровень голов T = λд + λг);
итог λд = (T + S)/2, λг = (T − S)/2 — проверено на отложенной выборке (лучше обеих подгонок по отдельности).

Еврокубки: глобальная атака/оборона команды = рейтинг в стране + смещение страны.
  α_c = (μ_c − μ_U + s_c)/2,  δ_c = (s_c − μ_c + μ_U)/2  (μ_c — уровень голов высшего дивизиона страны),
так что внутри страны прогноз не меняется, а сила страны s_c оценивается по результатам еврокубков из ESPN
(взвешенный пуассоновский максимум правдоподобия, сжатие к COUNTRY_PRIOR).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import minimize

import config as C
import dc

TOP = {'ENG': 'E0', 'SCO': 'SC0', 'GER': 'D1', 'ESP': 'SP1', 'ITA': 'I1', 'FRA': 'F1', 'NED': 'N1', 'BEL': 'B1',
       'POR': 'P1', 'TUR': 'T1', 'GRE': 'G1', 'RUS': 'RUS'}


@dataclass
class World:
    sup: dict[str, dc.Fit]
    tot: dict[str, dc.Fit]
    team_country: dict[str, str]
    n_matches: dict[str, int]
    uefa: dict = field(default_factory=dict)      # mu, home, s{country}, n

    def teams(self, country: str | None = None) -> set[str]:
        return {t for t, c in self.team_country.items() if country is None or c == country}

    # ---------------------------------------------------------------- λ
    def _domestic(self, fit: dc.Fit, div: str, h: str, a: str, neutral: bool) -> tuple[float, float] | None:
        if h not in fit.teams or a not in fit.teams:
            return None
        country = self.team_country.get(h)
        d = div if div in fit.divs else (TOP.get(country) if TOP.get(country) in fit.divs else next(iter(fit.divs)))
        k = fit.divs[d]
        i, j = fit.teams[h], fit.teams[a]
        hb = 0.0 if neutral else fit.home[k]
        return (float(np.exp(fit.mu[k] + hb + fit.att[i] - fit.dfn[j])), float(np.exp(fit.mu[k] + fit.att[j] - fit.dfn[i])))

    def _global(self, fits: dict[str, dc.Fit], h: str, a: str, neutral: bool) -> tuple[float, float] | None:
        ch, ca = self.team_country.get(h), self.team_country.get(a)
        if ch is None or ca is None or not self.uefa:
            return None
        u = self.uefa

        def ad(team, c):
            f = fits[c]
            mu_c = f.mu[f.divs[TOP[c]]] if TOP[c] in f.divs else float(np.mean(f.mu))
            s = u['s'].get(c, C.COUNTRY_PRIOR.get(c, 0.0))
            alpha, delta = (mu_c - u['mu'] + s) / 2, (s - mu_c + u['mu']) / 2
            k = f.teams[team]
            return f.att[k] + alpha, f.dfn[k] + delta
        (ah_, dh), (aa, da) = ad(h, ch), ad(a, ca)
        hb = 0.0 if neutral else u['home']
        return float(np.exp(u['mu'] + hb + ah_ - da)), float(np.exp(u['mu'] + aa - dh))

    def lambdas(self, comp: str, h: str, a: str, neutral: bool = False) -> tuple[float, float] | None:
        name, country, div, kind = C.COMPETITIONS[comp]
        neutral = neutral or kind in ('super', 'uefa_super')
        if kind.startswith('uefa'):
            if self.team_country.get(h) == self.team_country.get(a) and h in self.team_country:
                country, div = self.team_country[h], TOP[self.team_country[h]]          # дерби одной страны
            else:
                s, t = self._global(self.sup, h, a, neutral), self._global(self.tot, h, a, neutral)
                return _combine(s, t)
        if country not in self.sup:
            return None
        s = self._domestic(self.sup[country], div, h, a, neutral)
        t = self._domestic(self.tot[country], div, h, a, neutral)
        return _combine(s, t)


def _combine(s, t):
    if s is None or t is None:
        return None
    S, T = s[0] - s[1], t[0] + t[1]
    return max((T + S) / 2, 0.05), max((T - S) / 2, 0.05)


def build(train: pd.DataFrame, espn: pd.DataFrame | None, asof: pd.Timestamp) -> World:
    sup, tot, tc, nm = {}, {}, {}, {}
    for country, m in train[train.country.notna()].groupby('country'):
        m = m[(m.date >= asof - pd.Timedelta(days=C.WINDOW_DAYS)) & m.hg.notna() & m.ag.notna()]
        if len(m) < 100:
            continue
        sup[country] = dc.fit(m, asof, C.XI, C.RIDGE_SUP, C.RHO)
        tot[country] = dc.fit(m, asof, C.XI, C.RIDGE_TOT, C.RHO, warm=sup[country])
        for t in sup[country].teams:
            tc.setdefault(t, country)
        cnt = pd.concat([m.home, m.away]).value_counts()
        nm.update({t: int(n) for t, n in cnt.items()})
    w = World(sup, tot, tc, nm)
    w.uefa = fit_uefa(w, espn, asof)
    return w


def fit_uefa(w: World, espn: pd.DataFrame | None, asof: pd.Timestamp, ridge: float = 8.0) -> dict:
    """Сила стран по еврокубкам: λд = exp(μ_U + h_U + A_i − D_j), A/D = рейтинг в стране + смещение страны."""
    base = dict(mu=float(np.log(1.35)), home=0.25, s=dict(C.COUNTRY_PRIOR), n=0)
    if espn is None or espn.empty:
        return base
    e = espn[espn.comp.astype(str).str.startswith('uefa') & (espn.status.astype(str) == 'FT')].copy()
    e = e[e.home_fd.isin(w.team_country) & e.away_fd.isin(w.team_country)]
    e = e[e.home_fd.map(w.team_country) != e.away_fd.map(w.team_country)]
    if len(e) < 20:
        return {**base, 'n': len(e)}
    cs = sorted(set(e.home_fd.map(w.team_country)) | set(e.away_fd.map(w.team_country)))
    ci = {c: k for k, c in enumerate(cs)}
    prior = np.array([C.COUNTRY_PRIOR.get(c, 0.0) for c in cs])
    f = w.sup

    def rating(team):
        c = w.team_country[team]; k = f[c].teams[team]
        mu_c = f[c].mu[f[c].divs[TOP[c]]] if TOP[c] in f[c].divs else float(np.mean(f[c].mu))
        return f[c].att[k], f[c].dfn[k], mu_c, ci[c]
    H = np.array([rating(t) for t in e.home_fd]); A = np.array([rating(t) for t in e.away_fd])
    hg, ag = e.hg.astype(float).values, e.ag.astype(float).values
    wt = np.exp(-C.XI * (asof - pd.to_datetime(e.date)).dt.days.values)
    hc, ac = H[:, 3].astype(int), A[:, 3].astype(int)

    def nll(x):
        mu, home, s = x[0], x[1], x[2:]
        al_h, de_h = (H[:, 2] - mu + s[hc]) / 2, (s[hc] - H[:, 2] + mu) / 2
        al_a, de_a = (A[:, 2] - mu + s[ac]) / 2, (s[ac] - A[:, 2] + mu) / 2
        lh = mu + home + H[:, 0] + al_h - (A[:, 1] + de_a)
        la = mu + A[:, 0] + al_a - (H[:, 1] + de_h)
        return np.sum(wt * (np.exp(lh) - hg * lh + np.exp(la) - ag * la)) + ridge * np.sum((s - prior) ** 2)
    r = minimize(nll, np.concatenate([[np.log(1.35), 0.25], prior]), method='L-BFGS-B')
    s = dict(C.COUNTRY_PRIOR); s.update({c: float(r.x[2 + ci[c]]) for c in cs})
    return dict(mu=float(r.x[0]), home=float(r.x[1]), s=s, n=len(e))
