"""T21 · V4 — иерархическая модель очка на подаче (пакет №2, проверка 08.11.2026: engine/research/T21.md).

logit p(i подаёт против j, покрытие s) = μ_s + S_i + S_{i,s} − R_j − R_{j,s} + γ_S·lr_i − γ_R·lr_j

* наблюдение — матч × подающий: k выигранных очков на подаче из n (Sackmann: svpt, 1stWon + 2ndWon);
* иерархия = гауссовы прайоры: S, R ~ N(0, σ²), S_{i,s}, R_{i,s} ~ N(0, σ_s²); μ_s и γ без штрафа;
* lr — центрированный log(рейтинг) на момент матча: новичок стартует от уровня своего рейтинга, а не от среднего тура;
* свежесть: вес наблюдения 2^(−возраст / полураспад); оценка — максимум апостериорной вероятности (L-BFGS, разреженная матрица).

Модель заменяет сырую оценку Barnett–Clarke ТОЛЬКО в признаке «подача/приём» логистики.
Уровень цепи для линий (Snapshot.pa_bc / pb_bc) остаётся прежним — так проверено (вариант L2).
Проверка 2023–26: WTA 66,34% / 0,6085 → 66,56% / 0,6057; ATP 65,96% / 0,6127 → 66,13% / 0,6100.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import scipy.sparse as sp
from scipy.optimize import minimize

SURF = {'Hard': 0, 'Clay': 1, 'Grass': 2}
LR_FILL = float(np.log(300.0))       # рейтинг неизвестен → как у №300
LR_CENTER = float(np.log(60.0))
FIRST_FIT = pd.Timestamp('2019-06-01')   # раньше — мало истории; признак = 0 (равные)


def log_rank(r) -> np.ndarray:
    """Центрированный log(рейтинг); пусто / 0 → LR_FILL."""
    x = pd.to_numeric(pd.Series(np.atleast_1d(r), dtype='object'), errors='coerce')
    return np.log(x.where(x > 0)).fillna(LR_FILL).to_numpy(dtype=float) - LR_CENTER


def observations(h: pd.DataFrame) -> pd.DataFrame:
    """2 наблюдения на матч со статистикой подачи (h — база dataset.build: winner_id, loser_id, Surface, Date, w_svpt …)."""
    ok = h.w_svpt.notna() & (h.w_svpt > 0) & h.l_svpt.notna() & (h.l_svpt > 0)
    m = h[ok]
    s = m.Surface.map(SURF).fillna(0).astype(int).to_numpy()
    lw, ll = log_rank(m.WRank.to_numpy()), log_rank(m.LRank.to_numpy())
    a = pd.DataFrame(dict(date=m.Date.to_numpy(), srv=m.winner_id.to_numpy(), ret=m.loser_id.to_numpy(), s=s,
                          k=(m.w_1stWon + m.w_2ndWon).to_numpy(), n=m.w_svpt.to_numpy(), lr_s=lw, lr_r=ll))
    b = pd.DataFrame(dict(date=m.Date.to_numpy(), srv=m.loser_id.to_numpy(), ret=m.winner_id.to_numpy(), s=s,
                          k=(m.l_1stWon + m.l_2ndWon).to_numpy(), n=m.l_svpt.to_numpy(), lr_s=ll, lr_r=lw))
    o = pd.concat([a, b], ignore_index=True).sort_values('date', kind='stable').reset_index(drop=True)
    o['k'] = o.k.clip(lower=0, upper=o.n)
    return o


@dataclass
class ServeModel:
    players: dict                     # player_id → номер столбца
    half_days: float
    sigma: float
    sigma_s: float
    theta: np.ndarray | None = None
    asof: pd.Timestamp | None = None  # данные строго до этой даты

    @classmethod
    def new(cls, o: pd.DataFrame, cfg: dict) -> 'ServeModel':
        ids = pd.unique(np.concatenate([o.srv.to_numpy(), o.ret.to_numpy()]))
        return cls({p: i for i, p in enumerate(ids)}, cfg['half_days'], cfg['sigma'], cfg['sigma_s'])

    @property
    def P(self) -> int:
        return len(self.players)

    def _X(self, srv, ret, s, lr_s, lr_r) -> sp.csr_matrix:
        """Порядок параметров: μ(3) | S(P) | R(P) | S_s(3P) | R_s(3P) | γ_S, γ_R. Неизвестный игрок → только μ и рейтинг."""
        P, n = self.P, len(srv)
        i = np.array([self.players.get(x, -1) for x in srv], dtype=int)
        j = np.array([self.players.get(x, -1) for x in ret], dtype=int)
        s = np.asarray(s, dtype=int)
        r = np.arange(n)
        rows, cols, vals = [r], [s], [np.ones(n)]
        for idx, off, sign, by_surf in ((i, 3, 1.0, False), (j, 3 + P, -1.0, False),
                                        (i, 3 + 2 * P, 1.0, True), (j, 3 + 5 * P, -1.0, True)):
            ok = idx >= 0
            rows.append(r[ok]); vals.append(np.full(ok.sum(), sign))
            cols.append(off + (idx[ok] * 3 + s[ok] if by_surf else idx[ok]))
        rows += [r, r]; cols += [np.full(n, 3 + 8 * P), np.full(n, 3 + 8 * P + 1)]
        vals += [np.asarray(lr_s, dtype=float), -np.asarray(lr_r, dtype=float)]
        return sp.csr_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(n, 3 + 8 * P + 2))

    def fit(self, o: pd.DataFrame, asof: pd.Timestamp) -> None:
        """MAP по наблюдениям строго до asof. Тёплый старт от прошлой подгонки."""
        ob = o[o.date < asof]
        if ob.empty:
            return
        w = np.power(2.0, -(asof - ob.date).dt.days.to_numpy(dtype=float) / self.half_days)
        X = self._X(ob.srv.to_numpy(), ob.ret.to_numpy(), ob.s.to_numpy(), ob.lr_s.to_numpy(), ob.lr_r.to_numpy())
        Xt = X.T.tocsr()
        wk, wn = w * ob.k.to_numpy(dtype=float), w * ob.n.to_numpy(dtype=float)
        P, dim = self.P, X.shape[1]
        lam = np.zeros(dim)
        lam[3:3 + 2 * P] = 1 / self.sigma ** 2
        lam[3 + 2 * P:3 + 8 * P] = 1 / self.sigma_s ** 2

        def f(th):
            z = X @ th
            return (float(np.sum(wn * np.logaddexp(0, z) - wk * z) + 0.5 * np.sum(lam * th * th)),
                    Xt @ (wn / (1 + np.exp(-z)) - wk) + lam * th)

        if self.theta is None or len(self.theta) != dim:
            x0 = np.zeros(dim)
            x0[:3] = np.log(o.k.sum() / (o.n.sum() - o.k.sum()))
        else:
            x0 = self.theta
        self.theta = minimize(f, x0, jac=True, method='L-BFGS-B', options=dict(maxiter=2000, gtol=1e-6)).x
        self.asof = asof

    def predict(self, srv, ret, surface_idx, lr_s, lr_r) -> np.ndarray:
        """P(подающий выигрывает очко). До первой подгонки — NaN."""
        if self.theta is None:
            return np.full(len(srv), np.nan)
        return 1 / (1 + np.exp(-(self._X(srv, ret, surface_idx, lr_s, lr_r) @ self.theta)))

    def pair(self, a: float, b: float, surface: str, rank_a, rank_b) -> tuple[float, float]:
        """(p_a, p_b) — вероятности очка на своей подаче для живого прогноза."""
        s = SURF.get(surface, 0)
        la, lb = log_rank([rank_a])[0], log_rank([rank_b])[0]
        p = self.predict([a, b], [b, a], [s, s], [la, lb], [lb, la])
        return float(p[0]), float(p[1])


def walk_forward(h: pd.DataFrame, cfg: dict) -> tuple[ServeModel, np.ndarray, np.ndarray]:
    """Для обучения логистики: переподгонка каждый понедельник по данным строго до него; матчи недели получают её прогноз.
    Возвращает итоговую модель (по всем данным, на день после последнего матча) и (pa, pb) для каждого матча h
    (A = winner_id, B = loser_id; NaN — до FIRST_FIT)."""
    o = observations(h)
    sm = ServeModel.new(o, cfg)
    pa, pb = np.full(len(h), np.nan), np.full(len(h), np.nan)
    dates = h.Date.to_numpy()
    s = h.Surface.map(SURF).fillna(0).astype(int).to_numpy()
    lw, ll = log_rank(h.WRank.to_numpy()), log_rank(h.LRank.to_numpy())
    w_id, l_id = h.winner_id.to_numpy(), h.loser_id.to_numpy()
    d0 = h.Date.min().normalize()
    for wk in pd.date_range(d0 - pd.Timedelta(days=d0.weekday()), h.Date.max(), freq='W-MON'):
        idx = np.where((dates >= wk.to_datetime64()) & (dates < (wk + pd.Timedelta(days=7)).to_datetime64()))[0]
        if len(idx) == 0 or wk < FIRST_FIT:
            continue
        sm.fit(o, wk)
        pa[idx] = sm.predict(w_id[idx], l_id[idx], s[idx], lw[idx], ll[idx])
        pb[idx] = sm.predict(l_id[idx], w_id[idx], s[idx], ll[idx], lw[idx])
    sm.fit(o, h.Date.max() + pd.Timedelta(days=1))
    return sm, pa, pb
