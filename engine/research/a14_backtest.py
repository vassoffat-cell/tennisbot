"""A14 (T33): «любимое покрытие» как признак модели исхода тенниса — проверка ворот без утечки.

Признак игрока до матча: fav = (побед на покрытии + K·общая доля) / (матчей на покрытии + K) − общая доля  (сжатие K = 20).
Разница d = fav(игрок A) − fav(игрок B). Логит итоговой P: z_base + β·d.
База: (1) модель V4 (a1_backtest, обучение ≤ 2022), (2) Pinnacle без маржи (Шин) — живой источник P там, где есть цена.
β обучается на 2023–24, проверка — 2025–26. Ворота: n ≥ 1000, Δ лог-лосс значимо < 0 (z ≤ −2), угадано не ниже.
Запуск: python3 a1_backtest.py wta atp  →  python3 a14_backtest.py
"""
import sys

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar

sys.path.insert(0, '/home/claude/tennisbot')
import dataset  # noqa: E402

OUT = '/tmp/autopilot'
K = 20.0


def shin2(a: float, b: float) -> float:
    """P первого по Шину для двух исходов."""
    if not (a > 1 and b > 1):
        return np.nan
    pi = np.array([1 / a, 1 / b])
    s = pi.sum()
    lo, hi = 0.0, 0.4
    for _ in range(60):
        z = (lo + hi) / 2
        p = (np.sqrt(z * z + 4 * (1 - z) * pi * pi / s) - z) / (2 * (1 - z))
        lo, hi = (z, hi) if p.sum() > 1 else (lo, z)
    return float(p[0] / p.sum())


def features(h: pd.DataFrame) -> np.ndarray:
    tot: dict = {}
    srf: dict = {}
    d = np.zeros(len(h))
    nmin = np.zeros(len(h))
    for i, r in enumerate(h.itertuples(index=False)):
        f = []
        ns = []
        for pid in (r.winner_id, r.loser_id):
            aw, an = tot.get(pid, (0, 0))
            sw, sn = srf.get((pid, r.Surface), (0, 0))
            base = aw / an if an else 0.5
            f.append((sw + K * base) / (sn + K) - base)
            ns.append(min(an, sn))
        d[i] = f[0] - f[1]
        nmin[i] = min(ns)
        for pid, won in ((r.winner_id, 1), (r.loser_id, 0)):
            a = tot.get(pid, (0, 0)); tot[pid] = (a[0] + won, a[1] + 1)
            s = srf.get((pid, r.Surface), (0, 0)); srf[(pid, r.Surface)] = (s[0] + won, s[1] + 1)
    return d, nmin


def ll(z: np.ndarray, y: np.ndarray) -> np.ndarray:
    p = 1 / (1 + np.exp(-z))
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def run(tour: str) -> list:
    h = dataset.build(tour)
    a = pd.read_pickle(f'{OUT}/a1_{tour}.pkl')
    assert len(a) == len(h)
    d, nmin = features(h)
    rng = np.random.default_rng(5)
    flip = rng.random(len(h)) < 0.5                 # ориентация «A — случайный игрок», y = A победил
    sg = np.where(flip, -1.0, 1.0)
    y = (~flip).astype(float)
    zm = a.zm.values * sg
    pin = np.array([shin2(w, l) for w, l in zip(h.PSW.astype(float), h.PSL.astype(float))])
    zp = np.log(pin / (1 - pin)) * sg
    x = d * sg
    use = a.use.values & (nmin >= 10)
    out = []
    for name, zb in (('модель', zm), ('Pinnacle', zp)):
        ok = use & np.isfinite(zb)
        tr = ok & (h.season.values >= 2023) & (h.season.values <= 2024)
        te = ok & (h.season.values >= 2025)
        f = lambda b: ll(zb[tr] + b * x[tr], y[tr]).mean()
        beta = minimize_scalar(f, bounds=(-10, 10), method='bounded').x
        l0, l1 = ll(zb[te], y[te]), ll(zb[te] + beta * x[te], y[te])
        dd = l1 - l0
        z = dd.mean() / (dd.std(ddof=1) / np.sqrt(len(dd)))
        acc0 = ((zb[te] > 0) == (y[te] == 1)).mean() * 100
        acc1 = ((zb[te] + beta * x[te] > 0) == (y[te] == 1)).mean() * 100
        # срез, где признак заметен (|d| ≥ 0,05)
        big = te & (np.abs(x) >= 0.05)
        dd_b = ll(zb[big] + beta * x[big], y[big]) - ll(zb[big], y[big])
        out.append(dict(tour=tour.upper(), base=name, beta=round(beta, 2), n=int(te.sum()), ll0=l0.mean(), ll1=l1.mean(),
                        d_ll=dd.mean(), z=z, acc0=acc0, acc1=acc1, n_big=int(big.sum()),
                        z_big=dd_b.mean() / (dd_b.std(ddof=1) / np.sqrt(len(dd_b))) if big.sum() > 30 else np.nan))
    return out


if __name__ == '__main__':
    rows = [r for t in ('atp', 'wta') for r in run(t)]
    df = pd.DataFrame(rows)
    pd.set_option('display.width', 220)
    print(df.to_string(index=False, float_format=lambda v: f'{v:.4f}'))
