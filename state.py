"""Состояние игроков и независимая модель (без котировок).

Один и тот же класс TourState считает признаки и при обучении (проход по истории), и в живом прогнозе —
поэтому модель в боте получает ровно те признаки, на которых обучалась.

Признаки матча с позиции игрока A (A − B):
  0 Elo (логит P(A) по Elo: общий + по покрытию)      5 дни отдыха (до 7)
  1 подача/приём → цепь с momentum (логит P(A))        6 защита: раунд на этом турнире год назад
  2 возраст, лет                                        7 личные встречи (сглаженные)
  3 матчей за 14 дней                                   8 log(рейтинг B) − log(рейтинг A)
  4 смена покрытия
"""
from __future__ import annotations

import pickle
from collections import defaultdict, deque
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.interpolate import RegularGridInterpolator
from scipy.optimize import minimize

import config as C
import dataset
import markets as MK
from markov import match_distribution

FEATURES = ['Elo', 'Подача/приём', 'Возраст', 'Матчей за 14 дней', 'Смена покрытия', 'Отдых',
            'Защита очков', 'Личные встречи', 'Рейтинг']
GRID = np.round(np.arange(0.40, 0.8001, 0.01), 4)
PRIOR_PTS = 400.0


def win_table(tour: str) -> RegularGridInterpolator:
    mom, smom = C.MOMENTUM[tour]
    T = np.array([[MK.price(match_distribution(a, b, mom=mom, set_mom=smom), MK.mk_winner(True))[0] for b in GRID] for a in GRID])
    return RegularGridInterpolator((GRID, GRID), T)


def logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return float(np.log(p / (1 - p)))


@dataclass
class Snapshot:
    """Признаки матча и вероятности очка на подаче по Barnett–Clarke (до поправки моделью)."""
    x: np.ndarray
    pa_bc: float
    pb_bc: float


class TourState:
    def __init__(self, tour: str, dob: dict[float, pd.Timestamp], f_av: float):
        self.tour, self.dob, self.f_av, self.g_av = tour, dob, f_av, 1 - f_av
        self.w_surf = C.ELO_W_SURF[tour]
        self.elo = defaultdict(lambda: 1500.0); self.n = defaultdict(int)
        self.elo_s = defaultdict(lambda: 1500.0); self.n_s = defaultdict(int)
        self.serve: dict[float, deque] = defaultdict(deque)
        self.dates: dict[float, list] = defaultdict(list)
        self.last_surface: dict[float, str] = {}
        self.rank: dict[float, float] = {}
        self.reached: dict[tuple, int] = {}                # (pid, сезон, город) → раунд (8 — титул)
        self.h2h: dict[tuple, list] = defaultdict(lambda: [0, 0])
        self.last_date = None
        self.table: RegularGridInterpolator | None = None

    # ---------- признаки ----------
    def _sr(self, pid: float, date: pd.Timestamp) -> tuple[float, float]:
        q = self.serve[pid]
        while q and (date - q[0][0]).days > 365:
            q.popleft()
        sp = sum(x[1] for x in q); sw = sum(x[2] for x in q); rp = sum(x[3] for x in q); rw = sum(x[4] for x in q)
        return (sw + PRIOR_PTS * self.f_av) / (sp + PRIOR_PTS), (rw + PRIOR_PTS * self.g_av) / (rp + PRIOR_PTS)

    def snapshot(self, a: float, b: float, date: pd.Timestamp, surface: str, location: str, season: int) -> Snapshot:
        ra = (1 - self.w_surf) * self.elo[a] + self.w_surf * self.elo_s[(a, surface)]
        rb = (1 - self.w_surf) * self.elo[b] + self.w_surf * self.elo_s[(b, surface)]
        p_elo = 1 / (1 + 10 ** ((rb - ra) / 400))
        fa, ga = self._sr(a, date); fb, gb = self._sr(b, date)
        pa = float(np.clip(self.f_av + (fa - self.f_av) - (gb - self.g_av), 0.401, 0.799))
        pb = float(np.clip(self.f_av + (fb - self.f_av) - (ga - self.g_av), 0.401, 0.799))
        p_sr = float(self.table([[pa, pb]])[0])

        def age(p):
            d = self.dob.get(p)
            return (date - d).days / 365.25 if d is not None and not pd.isna(d) else np.nan

        def n14(p):
            return sum(1 for d in self.dates[p] if 0 < (date - d).days <= 14)

        def rest(p):
            return min((date - self.dates[p][-1]).days, 7) if self.dates[p] else 7

        def surf_change(p):
            return float(self.last_surface.get(p) != surface)

        key = (min(a, b), max(a, b)); r = self.h2h[key]
        wa, wb = (r[0], r[1]) if a == key[0] else (r[1], r[0])
        age_d = age(a) - age(b)
        ra_, rb_ = self.rank.get(a), self.rank.get(b)
        x = np.array([
            logit(p_elo), logit(p_sr), 0.0 if np.isnan(age_d) else age_d, n14(a) - n14(b),
            surf_change(a) - surf_change(b), rest(a) - rest(b),
            self.reached.get((a, season - 1, location), 0) - self.reached.get((b, season - 1, location), 0),
            (wa - wb) / (wa + wb + 2),
            np.log(rb_) - np.log(ra_) if ra_ and rb_ and ra_ > 0 and rb_ > 0 else 0.0])
        return Snapshot(x, pa, pb)

    # ---------- обновление после матча ----------
    def update(self, r) -> None:
        w, l, s, d = r.winner_id, r.loser_id, r.Surface, r.Date
        for dct, ndct, kw, kl in ((self.elo, self.n, w, l), (self.elo_s, self.n_s, (w, s), (l, s))):
            e = 1 / (1 + 10 ** ((dct[kl] - dct[kw]) / 400))
            dct[kw] += C.ELO_A / (ndct[kw] + 5) ** C.ELO_C * (1 - e)
            dct[kl] -= C.ELO_A / (ndct[kl] + 5) ** C.ELO_C * (1 - e)
            ndct[kw] += 1; ndct[kl] += 1
        if r.w_svpt == r.w_svpt and r.w_svpt > 0 and r.l_svpt == r.l_svpt and r.l_svpt > 0:
            ws, ls = r.w_1stWon + r.w_2ndWon, r.l_1stWon + r.l_2ndWon
            self.serve[w].append((d, r.w_svpt, ws, r.l_svpt, r.l_svpt - ls))
            self.serve[l].append((d, r.l_svpt, ls, r.w_svpt, r.w_svpt - ws))
        for p in (w, l):
            self.dates[p].append(d); self.last_surface[p] = s
        if r.WRank == r.WRank: self.rank[w] = r.WRank
        if r.LRank == r.LRank: self.rank[l] = r.LRank
        champ = 8 if r.ridx == 7 else r.ridx
        self.reached[(w, r.season, r.Location)] = max(self.reached.get((w, r.season, r.Location), 0), champ)
        self.reached[(l, r.season, r.Location)] = max(self.reached.get((l, r.season, r.Location), 0), r.ridx)
        key = (min(w, l), max(w, l))
        self.h2h[key][0 if w == key[0] else 1] += 1
        self.last_date = d


@dataclass
class Model:
    tour: str
    state: TourState
    weights: np.ndarray
    test_report: str

    def p_win(self, snap: Snapshot) -> float:
        return float(1 / (1 + np.exp(-snap.x @ self.weights)))


def _fit(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    def nll(b):
        z = X @ b
        return np.sum(np.logaddexp(0, z) - y * z) + 0.5e-3 * np.sum(b * b)
    return minimize(nll, np.zeros(X.shape[1]), method='BFGS').x


def build(tour: str, verbose: bool = True) -> Model:
    """Проход по истории: признаки до матча → обучение (проверка 2023+), итоговые веса по всей истории."""
    h = dataset.build(tour)
    pl = dataset.players(tour)
    st_cols = ['w_1stWon', 'w_2ndWon', 'l_1stWon', 'l_2ndWon', 'w_svpt', 'l_svpt']
    t = h[st_cols].sum()
    f_av = float((t.w_1stWon + t.w_2ndWon + t.l_1stWon + t.l_2ndWon) / (t.w_svpt + t.l_svpt))
    st = TourState(tour, dict(zip(pl.player_id, pl.dob)), f_av)
    st.table = win_table(tour)
    X, seasons, use = [], [], []
    for r in h.itertuples(index=False):
        snap = st.snapshot(r.winner_id, r.loser_id, r.Date, r.Surface, r.Location, r.season)
        X.append(snap.x); seasons.append(r.season); use.append(r.Comment == 'Completed' and r.season >= 2020)
        st.update(r)
    st.td_last = st.last_date                       # последняя дата tennis-data (дальше — докатка из ESPN)
    X, seasons, use = np.array(X), np.array(seasons), np.array(use)
    rng = np.random.default_rng(5)
    flip = rng.random(len(X)) < 0.5
    Xs = X * np.where(flip, -1.0, 1.0)[:, None]; y = (~flip).astype(float)
    tr, te = use & (seasons <= 2022), use & (seasons >= 2023)
    b_tr = _fit(Xs[tr], y[tr])
    z = Xs[te] @ b_tr
    ll = float(np.mean(np.logaddexp(0, z) - y[te] * z))
    acc = float(np.mean((z > 0) == (y[te] == 1)))
    b = _fit(Xs[use], y[use])
    rep = (f'{tour.upper()}: обучение 2020–22 n={tr.sum()}, проверка 2023–{seasons.max()} n={te.sum()}: '
           f'лог-лосс {ll:.4f}, точность {acc*100:.1f}%; итоговые веса по {use.sum()} матчам; '
           f'данные по {st.last_date.date()}')
    if verbose:
        print(rep)
        print('  веса: ' + ', '.join(f'{k} {v:+.3f}' for k, v in zip(FEATURES, b)))
    return Model(tour, st, b, rep)


def save(model: Model) -> None:
    C.STATE.mkdir(exist_ok=True)
    st = model.state
    st.elo, st.n, st.elo_s, st.n_s = dict(st.elo), dict(st.n), dict(st.elo_s), dict(st.n_s)
    st.serve, st.dates, st.h2h = dict(st.serve), dict(st.dates), {k: v for k, v in st.h2h.items()}
    with open(C.STATE / f'{model.tour}.pkl', 'wb') as f:
        pickle.dump(model, f)


def load(tour: str) -> Model:
    with open(C.STATE / f'{tour}.pkl', 'rb') as f:
        m: Model = pickle.load(f)
    st = m.state
    st.elo = defaultdict(lambda: 1500.0, st.elo); st.n = defaultdict(int, st.n)
    st.elo_s = defaultdict(lambda: 1500.0, st.elo_s); st.n_s = defaultdict(int, st.n_s)
    st.serve = defaultdict(deque, st.serve); st.dates = defaultdict(list, st.dates)
    st.h2h = defaultdict(lambda: [0, 0], st.h2h)
    return m


