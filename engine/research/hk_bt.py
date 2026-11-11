"""Бэктест хоккея: подгонка раз в 7 дней на прошлом (без утечки), прогноз следующих 7 дней.
Запуск: hk_bt.py <лига NHL|KHL> [ξ] [начало теста ГГГГ-ММ-ДД] [конец теста]. Ворота КХЛ — тот же отчёт по текущему сезону."""
import sys, numpy as np, pandas as pd
sys.path.insert(0, '/home/claude/tennisbot/hockey')
import hmodel as HM, hlines as HL
league = sys.argv[1] if len(sys.argv) > 1 else 'NHL'
xi = float(sys.argv[2]) if len(sys.argv) > 2 else HM.XI
g = pd.read_csv('/home/claude/tennisbot/hockey/data/games.csv.gz')
g = g[(g.league == league) & g.end.isin(['REG', 'OT', 'SO'])].copy()
g['t'] = pd.to_datetime(g.start)
t0 = sys.argv[3] if len(sys.argv) > 3 else '2024-10-01'
t1 = sys.argv[4] if len(sys.argv) > 4 else '2100-01-01'
test = g[(g.t >= t0) & (g.t < t1)].sort_values('t')
rows = []
d0 = test.t.min().normalize()
while d0 <= test.t.max():
    d1 = d0 + pd.Timedelta(days=7)
    wk = test[(test.t >= d0) & (test.t < d1)]
    if len(wk):
        m = HM.fit(g, league, d0, xi=xi)
        if m:
            for r in wk.itertuples():
                lam = m.lambdas(r.home, r.away)
                if not lam: continue
                P = m.matrix(*lam); q = m.ot_home(*lam)
                i, j = np.indices(P.shape)
                ph, pd_, pa = P[i > j].sum(), np.trace(P), P[i < j].sum()
                row = dict(t=r.t, home=r.home, away=r.away, hg=r.hg, ag=r.ag, hr=r.hg_reg, ar=r.ag_reg, end=r.end,
                           lh=lam[0], la=lam[1], ph=ph, pd=pd_, pa=pa, mh=ph + pd_ * q)
                for L in (3.5, 4.5, 5.5, 6.5):
                    row[f'po{L}'] = P[(i + j) > L].sum()
                for x in HL.build(P, q, league, {}):
                    row[f"s{x['slot']}_p"] = x['p']; row[f"s{x['slot']}_sh"] = x['shown']
                    row[f"s{x['slot']}_w"] = HL.settle(x['market'], x['pick'], x['line'], r.end, r.hg, r.ag, r.hg_reg, r.ag_reg)
                    row[f"s{x['slot']}_mk"] = x['market']
                rows.append(row)
    d0 = d1
import os; os.makedirs('/home/claude/research/hk', exist_ok=True)
R = pd.DataFrame(rows); R.to_pickle(f'/home/claude/research/hk/{league}_bt_{xi}_{t0}.pkl')
hw = (R.hg > R.ag).astype(int)
p = R.mh.clip(1e-6, 1 - 1e-6)
print(f'{league} ξ={xi} n={len(R)} | победа с ОТ: угадано {((p >= .5) == hw).mean()*100:.1f}% лл {-np.mean(hw*np.log(p)+(1-hw)*np.log(1-p)):.4f} | хозяева всегда {hw.mean()*100:.1f}%')
reg = np.where(R.hr > R.ar, 0, np.where(R.hr == R.ar, 1, 2)); P3 = R[['ph', 'pd', 'pa']].values
print(f'  1X2 осн.: угадано {(P3.argmax(1) == reg).mean()*100:.1f}% лл {-np.mean(np.log(P3[np.arange(len(R)), reg])):.4f}; ничьих факт {(reg == 1).mean()*100:.1f}% мод {R.pd.mean()*100:.1f}%')
tot = R.hr + R.ar
for L in (3.5, 4.5, 5.5, 6.5):
    po = R[f'po{L}']; ov = tot > L; side = po >= .5; pp = np.where(side, po, 1 - po)
    print(f'  тотал {L}: факт Б {ov.mean()*100:.1f}% мод Б {side.mean()*100:.1f}% угадано {(side == ov).mean()*100:.1f}% при {pp.mean()*100:.1f}%')
for s in range(4, 13):
    sh = R[f's{s}_sh'] == 1; w = R[f's{s}_w']; ok = sh & w.isin(['W', 'L'])
    if ok.sum():
        print(f'  слот {s} {R[f"s{s}_mk"].iloc[0]}: показано {sh.mean()*100:.0f}% угадано {(w[ok] == "W").mean()*100:.1f}% при {R.loc[ok, f"s{s}_p"].mean()*100:.1f}% n={ok.sum()}')
