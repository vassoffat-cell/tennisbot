import sys
import numpy as np, pandas as pd
from scipy.optimize import minimize
sys.path.insert(0, '/home/claude/tennisbot')
import pick

OUT = '/tmp/autopilot'
lg = lambda p: np.log(p / (1 - p))


def prep(tour):
    d = pd.read_pickle(f'{OUT}/a1_{tour}.pkl')
    d = d[d.use].copy()
    d['PSW'] = pd.to_numeric(d.PSW, errors='coerce'); d['PSL'] = pd.to_numeric(d.PSL, errors='coerce')
    d = d[(d.PSW > 1) & (d.PSL > 1)].copy()
    d['zp'] = [lg(pick.shin([a, b])[0]) for a, b in zip(d.PSW, d.PSL)]   # Pinnacle без маржи (Шин, как в боте)
    return d


def fit_w(d):
    rng = np.random.default_rng(7); s = np.where(rng.random(len(d)) < 0.5, -1.0, 1.0)
    X = np.c_[d.zm.values, d.zp.values] * s[:, None]; y = (s > 0).astype(float)
    nll = lambda w: np.sum(np.logaddexp(0, X @ w) - y * (X @ w))
    return minimize(nll, np.array([0.0, 1.0]), method='BFGS').x


def stats(z, d):
    win = z > 0
    ll = np.logaddexp(0, -z)                                            # −log P(победитель)
    k = np.where(win, d.PSW, d.PSL); roi = np.where(win, d.PSW - 1, -1.0)
    return win.astype(float), ll, roi


rows = []
for tour in ('wta', 'atp'):
    d = prep(tour)
    tr, te = d[d.season == 2023], d[d.season >= 2024]
    w = fit_w(tr)
    zm, zp = te.zm.values, te.zp.values
    zb = w[0] * zm + w[1] * zp
    zf = -0.11 * zm + 1.11 * zp                                         # текущие веса pick.WEIGHTS
    am, lm, rm = stats(zm, te); ap, lp, rp = stats(zp, te); ab, lb, rb = stats(zb, te); af, lf, rf = stats(zf, te)
    n = len(te)
    zacc = (ab - am).mean() / ((ab - am).std(ddof=1) / np.sqrt(n))
    zll = (lb - lm).mean() / ((lb - lm).std(ddof=1) / np.sqrt(n))
    zroi = (rb - rm).mean() / ((rb - rm).std(ddof=1) / np.sqrt(n))
    agree = np.mean((zb > 0) == (zm > 0))
    print(f'{tour.upper()} обучение 2023 n={len(tr)}  веса модель {w[0]:+.3f} рынок {w[1]:+.3f}   проверка 2024–26 n={n}')
    for nm, a, l, r in (('модель', am, lm, rm), ('Pinnacle', ap, lp, rp), ('смесь(2023)', ab, lb, rb), ('смесь(бот)', af, lf, rf)):
        print(f'  {nm:12s} точн {a.mean()*100:6.2f}%  лл {l.mean():.4f}  ROI@Pin {r.mean()*100:+6.2f}%')
    print(f'  смесь−модель: точн {(ab-am).mean()*100:+.2f} п.п. z={zacc:.1f}; лл {(lb-lm).mean():+.4f} z={zll:.1f}; '
          f'ROI {(rb-rm).mean()*100:+.2f} п.п. z={zroi:.1f}; совпадение исхода {agree*100:.1f}%')
    # охват: доля матчей тура с кф Pinnacle
    full = pd.read_pickle(f'{OUT}/a1_{tour}.pkl'); full = full[full.use & (full.season >= 2024)]
    print(f'  охват Pinnacle в истории: {n}/{len(full)} = {n/len(full)*100:.1f}%')
    rows.append((tour, n, am.mean(), ab.mean(), lm.mean(), lb.mean(), rm.mean(), rb.mean(), zacc, zll))
pd.DataFrame(rows, columns='tour n acc_m acc_b ll_m ll_b roi_m roi_b z_acc z_ll'.split()).to_csv(f'{OUT}/a1_result.csv', index=False)
