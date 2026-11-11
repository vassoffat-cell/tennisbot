"""T32 (11.11.2026): фиксированная линия тотала — P(ТБ L) модели по всем кандидатам L, факт; 3 и 5 сетов."""
import sys, numpy as np, pandas as pd
from multiprocessing import Pool
from scipy.optimize import brentq
import t21_lines as TL
from t21_common import OUT
import config as C, markets as MK
from markov import match_distribution
GRID3 = [x + 0.5 for x in range(17, 28)]
GRID5 = [x + 0.5 for x in range(30, 45)]

def one(a):
    tour, p_star, pa, pb, bo, ft, sets = a
    mom, smom = C.MOMENTUM[tour]
    dist = lambda d: match_distribution(float(np.clip(pa + d, .30, .95)), float(np.clip(pb - d, .30, .95)), bo, ft, mom, smom)
    f = lambda d: MK.price(dist(d), MK.mk_winner(True))[0] - p_star
    d = brentq(f, -0.3, 0.3, xtol=1e-7) if f(-0.3) * f(0.3) < 0 else 0.0
    D = dist(d)
    tot = {}
    for x, p in D.items():
        g = x.games_a + x.games_b
        tot[g] = tot.get(g, 0) + p
    grid = GRID3 if bo == 3 else GRID5
    r = {f'p{L}': sum(p for g, p in tot.items() if g > L) for L in grid}
    r['total'] = sum(a + b for a, b in sets); r['bo'] = bo
    return r

BO = int(sys.argv[2])
for tour in (sys.argv[1],):
    m = pd.read_pickle(f'{OUT}/{tour}_matches.pkl'); z = pd.read_pickle(f'{OUT}/{tour}_z.pkl')
    ok = m.use & (m.season >= 2023) & m.sets.map(lambda s: len(s) >= 2)
    ok &= np.array([sum(a > b for a, b in s) == (2 if bo == 3 else 3) for s, bo in zip(m.sets, m.best_of)])
    ok &= (m.best_of == BO)
    ok = ok.values; mt = m[ok]; p = 1 / (1 + np.exp(-z['V4 T21+рейтинг вместо BC'][ok]))
    ft = np.where(mt.level.astype(str).str.contains('Grand Slam'), 10, 7)
    args = [(tour, float(a), float(b), float(c), int(bo), int(f), s) for a, b, c, bo, f, s in zip(p, mt.pa_bc, mt.pb_bc, mt.best_of, ft, mt.sets)]
    with Pool(2) as pool:
        r = pd.DataFrame(pool.map(one, args, chunksize=64), index=mt.index)
    r['surface'] = mt.surface.values; r['season'] = mt.season.values
    r.to_pickle(f'{OUT}/{tour}_fixed_total_bo{BO}.pkl'); print(tour, len(r), flush=True)
