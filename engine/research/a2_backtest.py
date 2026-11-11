"""A2: футбол — модель (еженедельная переподгонка, без утечки) против Pinnacle без маржи и их смеси.
Веса смеси — по сезону 2023/24, проверка 07.2024 → конец данных; только лиги бота."""
import sys
import numpy as np, pandas as pd
from scipy.optimize import minimize
sys.path.insert(0, '/home/claude/tennisbot/football')
import config as C, model, dc, fmarket

OUT = '/tmp/autopilot'
DIVS = {v[2] for v in C.COMPETITIONS.values() if v[3] == 'league'}

m = pd.read_csv('/home/claude/tennisbot/football/data/matches.csv.gz')
m['date'] = pd.to_datetime(m.date)
m = m.sort_values('date').reset_index(drop=True)

if 'eval' not in sys.argv:
    rows = []
    for asof in pd.date_range('2023-07-03', m.date.max() + pd.Timedelta(days=1), freq='7D'):
        wk = m[(m.date >= asof) & (m.date < asof + pd.Timedelta(days=7)) & m['div'].isin(DIVS) & m.hg.notna()]
        if wk.empty:
            continue
        w = model.build(m[m.date < asof], None, asof)
        for r in wk[['country','home','div','away','date','hg','ag','ps_h','ps_d','ps_a','ps_o25','ps_u25']].itertuples():
            if r.country not in w.sup:
                continue
            s = w._domestic(w.sup[r.country], r[2], r.home, r.away, False)
            t = w._domestic(w.tot[r.country], r[2], r.home, r.away, False)
            lam = model._combine(s, t)
            if lam is None:
                continue
            mk = dc.markets(dc.score_matrix(lam[0], lam[1], C.RHO))
            rows.append(dict(i=r.Index, date=r.date, div=r[2], hg=r.hg, ag=r.ag, mh=mk['h'], md=mk['d'], ma=mk['a'],
                             mo=mk['o25'], ps_h=r.ps_h, ps_d=r.ps_d, ps_a=r.ps_a, ps_o=r.ps_o25, ps_u=r.ps_u25))
    pd.DataFrame(rows).to_pickle(f'{OUT}/a2_oos.pkl')
    print('rows', len(rows))
    sys.exit()

d = pd.read_pickle(f'{OUT}/a2_oos.pkl')
d['res'] = np.select([d.hg > d.ag, d.hg == d.ag], [0, 1], 2)
d['over'] = (d.hg + d.ag > 2.5).astype(int)


def report(name, d, pm, pp, y, odds, thr):
    """pm, pp: (n, k) вероятности модели и Pinnacle; y — индекс исхода; odds — кф Pinnacle (n, k)."""
    tr = (d.date < '2024-07-01').values; te = ~tr
    lm, lp = np.log(np.clip(pm, 1e-9, 1)), np.log(np.clip(pp, 1e-9, 1))

    def pool(w, idx):
        z = w[0] * lm[idx] + w[1] * lp[idx]
        z -= z.max(1, keepdims=True); p = np.exp(z); return p / p.sum(1, keepdims=True)
    nll = lambda w: -np.sum(np.log(pool(w, tr)[np.arange(tr.sum()), y[tr]]))
    w = minimize(nll, np.array([0.0, 1.0]), method='BFGS').x
    pb = pool(w, te)
    n = te.sum(); yt = y[te]; ar = np.arange(n)
    out = {}
    for nm, p in (('модель', pm[te]), ('Pinnacle', pp[te]), ('смесь', pb)):
        pick = p.argmax(1); hit = (pick == yt).astype(float)
        ll = -np.log(np.clip(p[ar, yt], 1e-9, 1))
        roi = np.where(hit == 1, odds[te][ar, pick] - 1, -1.0)
        out[nm] = (hit, ll, roi, pick)
    print(f'{name}: веса модель {w[0]:+.3f} рынок {w[1]:+.3f}; обучение n={tr.sum()}, проверка n={n}')
    for nm, (h, l, r, _) in out.items():
        print(f'  {nm:9s} точн {h.mean()*100:6.2f}%  лл {l.mean():.4f}  ROI@Pin {r.mean()*100:+6.2f}%')
    (hb, lb, rb, pkb), (hm, lm_, rm, pkm) = out['смесь'], out['модель']
    z = lambda a: a.mean() / (a.std(ddof=1) / np.sqrt(len(a)))
    print(f'  смесь−модель: точн {(hb-hm).mean()*100:+.2f} п.п. (порог {thr}) z={z(hb-hm):.1f}; лл {(lb-lm_).mean():+.4f} '
          f'z={z(lb-lm_):.1f}; ROI {(rb-rm).mean()*100:+.2f} п.п. z={z(rb-rm):.1f}; смена стороны {np.mean(pkb != pkm)*100:.1f}%')
    if pm.shape[1] == 3:
        for nm, (_, _, _, pk) in out.items():
            print(f'    {nm:9s} доля ничьих в выборе {np.mean(pk == 1)*100:.1f}%')
    return w


ok = d[['ps_h', 'ps_d', 'ps_a']].gt(1).all(1).values
x = d[ok].reset_index(drop=True)
pp = np.array([fmarket.shin([a, b, c]) for a, b, c in zip(x.ps_h, x.ps_d, x.ps_a)])
pm = x[['mh', 'md', 'ma']].values; pm = pm / pm.sum(1, keepdims=True)
print(f'охват 1X2 Pinnacle: {ok.sum()}/{len(d)}')
w1 = report('1X2', x, pm, pp, x.res.values, x[['ps_h', 'ps_d', 'ps_a']].values, 0.3)

ok = d[['ps_o', 'ps_u']].gt(1).all(1).values
x = d[ok].reset_index(drop=True)
pp = np.array([fmarket.shin([a, b]) for a, b in zip(x.ps_o, x.ps_u)])
pm = np.c_[x.mo, 1 - x.mo]
print(f'охват ТБ2,5 Pinnacle: {ok.sum()}/{len(d)}')
w2 = report('Тотал 2,5', x, pm, pp, 1 - x.over.values, x[['ps_o', 'ps_u']].values, 0.45)
