"""Состояние игроков и независимая модель (без котировок).

Один и тот же класс TourState считает признаки и при обучении (проход по истории), и в живом прогнозе —
поэтому модель в боте получает ровно те признаки, на которых обучалась.

Признаки матча с позиции игрока A (A − B):
  0 Elo (логит P(A) по Elo: общий + по покрытию)      5 дни отдыха (до 7)
  1 подача/приём → цепь с momentum (логит P(A))        6 защита: раунд на этом турнире год назад
    (пакет №2: из модели очка T21/V4 — serve_model.py)
  2 возраст, лет                                        7 личные встречи (сглаженные)
  3 матчей за 14 дней                                   8 log(рейтинг B) − log(рейтинг A)
  4 смена покрытия                                      9 Elo по ГЕЙМАМ (доля выигранных геймов; общий + покрытие)
                                                       10 log(очки рейтинга A) − log(очки B) — только WTA
Признаки 9–10 — пакет №1 (08.11.2026), проверка 2023–26 (research/tennis_exp2/3.py):
  WTA 65,15% → 66,25% (лог-лосс 0,6159 → 0,6083), ATP 65,53% → 66,00% (0,6155 → 0,6127; очки ATP мешают — не берутся).
Признак 1 — пакет №2: вероятности очка на подаче из иерархической модели очка (serve_model.py, V4) вместо сырой
Barnett–Clarke; уровень цепи для линий (pa_bc / pb_bc) — прежний. Проверка 2023–26 (engine/research/T21.md):
  WTA 66,34% / 0,6085 → 66,56% / 0,6057, ATP 65,96% / 0,6127 → 66,13% / 0,6100.
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
import serve_model as SM
from markov import match_distribution

FEATURES = ['Elo', 'Подача/приём (модель очка)', 'Возраст', 'Матчей за 14 дней', 'Смена покрытия', 'Отдых',
            'Защита очков', 'Личные встречи', 'Рейтинг', 'Elo по геймам', 'Очки рейтинга']
VERSION = 4                     # 2 — Elo по геймам (+ очки WTA); 3 — модель очка T21/V4; 4 — подача TennisMyLife (T29); модель другой версии пересобирается автоматически
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
        self.ge = defaultdict(lambda: 1500.0); self.ge_s = defaultdict(lambda: 1500.0)   # Elo по геймам
        self.pts: dict[float, float] = {}                                                  # очки рейтинга
        self.last_date = None
        self.table: RegularGridInterpolator | None = None
        self.srv: SM.ServeModel | None = None                                              # модель очка (пакет №2)

    # ---------- признаки ----------
    def _sr(self, pid: float, date: pd.Timestamp) -> tuple[float, float]:
        q = self.serve[pid]
        while q and (date - q[0][0]).days > 365:
            q.popleft()
        sp = sum(x[1] for x in q); sw = sum(x[2] for x in q); rp = sum(x[3] for x in q); rw = sum(x[4] for x in q)
        return (sw + PRIOR_PTS * self.f_av) / (sp + PRIOR_PTS), (rw + PRIOR_PTS * self.g_av) / (rp + PRIOR_PTS)

    def _p_serve(self, a: float, b: float, surface: str, t21: tuple[float, float] | None) -> float:
        """Признак 1: P(A) цепи по вероятностям очка из модели очка. t21 — готовая пара при обучении (недельная
        переподгонка без утечки); в живом прогнозе — итоговая модель и текущий рейтинг. Нет оценки → 0,6 / 0,6 (равные)."""
        if t21 is None:
            t21 = self.srv.pair(a, b, surface, self.rank.get(a), self.rank.get(b)) if self.srv is not None else (np.nan, np.nan)
        pa, pb = (0.6 if x != x else float(x) for x in t21)
        return float(self.table([[float(np.clip(pa, 0.401, 0.799)), float(np.clip(pb, 0.401, 0.799))]])[0])

    def snapshot(self, a: float, b: float, date: pd.Timestamp, surface: str, location: str, season: int,
                 t21: tuple[float, float] | None = None) -> Snapshot:
        ra = (1 - self.w_surf) * self.elo[a] + self.w_surf * self.elo_s[(a, surface)]
        rb = (1 - self.w_surf) * self.elo[b] + self.w_surf * self.elo_s[(b, surface)]
        p_elo = 1 / (1 + 10 ** ((rb - ra) / 400))
        fa, ga = self._sr(a, date); fb, gb = self._sr(b, date)
        pa = float(np.clip(self.f_av + (fa - self.f_av) - (gb - self.g_av), 0.401, 0.799))
        pb = float(np.clip(self.f_av + (fb - self.f_av) - (ga - self.g_av), 0.401, 0.799))
        p_sr = self._p_serve(a, b, surface, t21)              # признак 1 — модель очка; pa/pb (BC) — уровень цепи для линий

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
        g = C.GAMES_ELO[self.tour]
        ws = g['w_surf']
        ga_ = (1 - ws) * self.ge[a] + ws * self.ge_s[(a, surface)]
        gb_ = (1 - ws) * self.ge[b] + ws * self.ge_s[(b, surface)]
        extra = [(ga_ - gb_) / g['scale']]
        if g['points']:
            pa_, pb_ = self.pts.get(a), self.pts.get(b)
            extra.append(np.log(pa_) - np.log(pb_) if pa_ and pb_ and pa_ > 0 and pb_ > 0 else 0.0)
        return Snapshot(np.concatenate([x, extra]), pa, pb)

    def games_update(self, r) -> None:
        """Elo по геймам: ожидаемая доля геймов из разницы рейтингов (масштаб 300), шаг K·(факт − ожидание)."""
        gw = gl = 0
        for k in range(1, 6):
            a_, b_ = getattr(r, f'W{k}', np.nan), getattr(r, f'L{k}', np.nan)
            try:
                a_, b_ = float(a_), float(b_)
            except (TypeError, ValueError):
                continue
            if a_ == a_ and b_ == b_:
                gw += a_; gl += b_
        if gw + gl < 6:
            return
        g = C.GAMES_ELO[self.tour]
        sh = gw / (gw + gl)
        w, l, s = r.winner_id, r.loser_id, r.Surface
        for dct, kw, kl in ((self.ge, w, l), (self.ge_s, (w, s), (l, s))):
            e = 1 / (1 + 10 ** ((dct[kl] - dct[kw]) / g['scale']))
            dct[kw] += g['K'] * (sh - e); dct[kl] -= g['K'] * (sh - e)

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
        self.games_update(r)
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
    st.srv, t21_pa, t21_pb = SM.walk_forward(h, C.SERVE_MODEL[tour])   # модель очка: недельная переподгонка + итоговая
    X, seasons, use = [], [], []
    for i, r in enumerate(h.itertuples(index=False)):
        for pid, pt in ((r.winner_id, r.WPts), (r.loser_id, r.LPts)):     # очки рейтинга на момент матча (tennis-data)
            if pt == pt and pt:
                st.pts[pid] = float(pt)
        snap = st.snapshot(r.winner_id, r.loser_id, r.Date, r.Surface, r.Location, r.season, t21=(t21_pa[i], t21_pb[i]))
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
    m = Model(tour, st, b, rep)
    m.version = VERSION
    return m


def _plain(st: TourState) -> None:
    st.elo, st.n, st.elo_s, st.n_s = dict(st.elo), dict(st.n), dict(st.elo_s), dict(st.n_s)
    st.serve, st.dates, st.h2h = dict(st.serve), dict(st.dates), {k: v for k, v in st.h2h.items()}
    st.ge, st.ge_s = dict(st.ge), dict(st.ge_s)


def _restore(st: TourState) -> None:
    """Словари со значениями по умолчанию (новый игрок / новая пара) — после загрузки и после сохранения."""
    st.elo = defaultdict(lambda: 1500.0, st.elo); st.n = defaultdict(int, st.n)
    st.elo_s = defaultdict(lambda: 1500.0, st.elo_s); st.n_s = defaultdict(int, st.n_s)
    st.serve = defaultdict(deque, st.serve); st.dates = defaultdict(list, st.dates)
    st.h2h = defaultdict(lambda: [0, 0], st.h2h)
    st.ge = defaultdict(lambda: 1500.0, getattr(st, 'ge', {})); st.ge_s = defaultdict(lambda: 1500.0, getattr(st, 'ge_s', {}))


def save(model: Model) -> None:
    """Сохранить модель. Состояние в памяти после сохранения остаётся рабочим (словари по умолчанию восстанавливаются)."""
    C.STATE.mkdir(exist_ok=True)
    _plain(model.state)
    try:
        with open(C.STATE / f'{model.tour}.pkl', 'wb') as f:
            pickle.dump(model, f)
    finally:
        _restore(model.state)


def load(tour: str) -> Model:
    with open(C.STATE / f'{tour}.pkl', 'rb') as f:
        m: Model = pickle.load(f)
    if getattr(m, 'version', 1) != VERSION:
        raise ValueError(f'модель версии {getattr(m, "version", 1)}, нужна {VERSION}')
    _restore(m.state)
    return m
