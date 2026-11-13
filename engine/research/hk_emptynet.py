"""A22 · НХЛ: пустые ворота — часть матчей с разницей 1 в основное время превращается в разницу 2 (лидер забивает в пустые).
Поправка матрицы: доля e масс P[i,j] с |i−j| = 1 переносится в клетку лидера +1 гол. e — по сезону 2024/25 (лог-правдоподобие
счёта основного времени), проверка — 2025/26+ на линиях (фора ±1,5, тотал, ИТ, исход). λ — из бэктеста без утечки (hk_bt.py)."""
import sys, numpy as np, pandas as pd
sys.path.insert(0, '/home/claude/tennisbot')
from hockey import hmodel as HM, hdata as HD
from scipy.optimize import minimize_scalar
R = pd.read_pickle('/home/claude/research/hk/NHL_bt_0.0035_2024-10-01.pkl').dropna(subset=['hr', 'ar']).reset_index(drop=True)
M = HM.fit(HD.load(), 'NHL', pd.Timestamp('2025-09-01'))          # раздутие ничьих — по данным до проверки
G = HM.MAXG + 1; I, J = np.indices((G, G))
def mat(lh, la, e):
    """e = (e1, e2): доля разницы 1 → 2 и разницы 2 → 3 (сначала 2→3, затем 1→2, чтобы не переносить дважды)."""
    P = M.matrix(lh, la)
    e1, e2 = (e, 0.0) if np.isscalar(e) else e
    for k, ek in ((2, e2), (1, e1)):
        if ek:
            mh = (I - J == k) & (I < G - 1); ma = (J - I == k) & (J < G - 1)
            mv1 = P * mh * ek; mv2 = P * ma * ek
            P = P - mv1 - mv2
            P[1:, :] += mv1[:-1, :]; P[:, 1:] += mv2[:, :-1]
    return P
hr, ar = R.hr.astype(int).clip(0, G - 1).values, R.ar.astype(int).clip(0, G - 1).values
tr = (R.t < '2025-09-01').values; te = ~tr
def nll(e, k):
    return -np.mean([np.log(max(mat(a, b, e)[x, y], 1e-12)) for a, b, x, y in zip(R.lh[k], R.la[k], hr[k], ar[k])])
from scipy.optimize import minimize
r = minimize(lambda x: nll((x[0], x[1]), tr), [0.2, 0.2], bounds=[(0, 0.7), (0, 0.7)], method='L-BFGS-B')
e = tuple(float(x) for x in r.x)
print(f'e1 (1→2) = {e[0]:.3f}, e2 (2→3) = {e[1]:.3f} по 2024/25 (n={tr.sum()}) · −лог-правдоподобие счёта на тесте: {nll(0, te):.4f} → {nll(e, te):.4f}')
d = (R.hr - R.ar).values
print('распределение |разницы| осн. времени (тест): факт / модель / с поправкой')
P0 = np.array([mat(a, b, 0) for a, b in zip(R.lh[te], R.la[te])]); P1 = np.array([mat(a, b, e) for a, b in zip(R.lh[te], R.la[te])])
for k in range(5):
    print(f'  |d|={k}: {np.mean(np.abs(d[te]) == k)*100:5.1f}%  {P0[:, np.abs(I - J) == k].sum(1).mean()*100:5.1f}%  {P1[:, np.abs(I - J) == k].sum(1).mean()*100:5.1f}%')
ll = lambda p, y: -(y * np.log(np.clip(p, 1e-6, 1)) + (1 - y) * np.log(np.clip(1 - p, 1e-6, 1)))
tot = (R.hr + R.ar).values[te]
for lab, f, y in (('Ф1(+1,5)', lambda P: P[:, (I - J + 1.5) > 0].sum(1), (d[te] + 1.5 > 0)),
                  ('Ф2(+1,5)', lambda P: P[:, (J - I + 1.5) > 0].sum(1), (-d[te] + 1.5 > 0)),
                  ('Ф1(−1,5)', lambda P: P[:, (I - J - 1.5) > 0].sum(1), (d[te] - 1.5 > 0)),
                  ('ТБ 4,5', lambda P: P[:, (I + J) > 4.5].sum(1), tot > 4.5), ('ТБ 5,5', lambda P: P[:, (I + J) > 5.5].sum(1), tot > 5.5),
                  ('П1 осн.', lambda P: P[:, I > J].sum(1), d[te] > 0), ('Ничья осн.', lambda P: P[:, I == J].sum(1), d[te] == 0),
                  ('Ф1(−2,5)', lambda P: P[:, (I - J - 2.5) > 0].sum(1), (d[te] - 2.5 > 0)), ('Ф2(+2,5)', lambda P: P[:, (J - I + 2.5) > 0].sum(1), (-d[te] + 2.5 > 0))):
    y = y.astype(float); p0, p1 = f(P0), f(P1); dl = ll(p1, y) - ll(p0, y)
    print(f'{lab:<10} факт {y.mean()*100:5.1f}% | без {p0.mean()*100:5.1f}% лл {ll(p0, y).mean():.4f} | с поправкой {p1.mean()*100:5.1f}% лл {ll(p1, y).mean():.4f} | Δ {dl.mean():+.4f} (z {dl.mean()/(dl.std(ddof=1)/np.sqrt(len(dl))):+.1f})')
