"""0б аудита 16.11: КХЛ на устаревших силах. Имитация сегодняшней ситуации на истории без утечки:
обучение = сезоны до 05.2023 (сезон 2023/24 ПРОПУЩЕН, как сейчас пропущен 2025/26) + матчи 2024/25 до недели теста;
тест 09–12.2024. Сравнение с «полной» моделью (все данные до недели). Срез: свежих матчей у слабейшей по данным команды
за 60 дней < 5 против ≥ 5. Метрики: лог-лосс «победа с ОТ», показанные линии — угадано против обещанной P."""
import sys, numpy as np, pandas as pd
sys.path.insert(0, '/home/claude/tennisbot/hockey')
import hmodel as HM, hlines as HL
g = pd.read_csv('/home/claude/tennisbot/hockey/data/games.csv.gz')
g = g[(g.league == 'KHL') & g.end.isin(['REG', 'OT', 'SO'])].copy()
g['t'] = pd.to_datetime(g.start)
gap0, gap1 = pd.Timestamp('2023-05-15'), pd.Timestamp('2024-08-01')
stale_src = g[(g.t < gap0) | (g.t >= gap1)]
test = g[(g.t >= '2024-09-01') & (g.t < '2025-01-01')].sort_values('t')
rows = []
d0 = test.t.min().normalize()
while d0 <= test.t.max():
    d1 = d0 + pd.Timedelta(days=7)
    wk = test[(test.t >= d0) & (test.t < d1)]
    ms = {'stale': HM.fit(stale_src, 'KHL', d0), 'full': HM.fit(g, 'KHL', d0)}
    for r in wk.itertuples():
        rec = stale_src[(stale_src.t < r.t) & (stale_src.t >= r.t - pd.Timedelta(days=60))]
        nf = min(((rec.home == r.home) | (rec.away == r.home)).sum(), ((rec.home == r.away) | (rec.away == r.away)).sum())
        for name, m in ms.items():
            lam = m.lambdas(r.home, r.away) if m else None
            if not lam:
                continue
            P = m.matrix(*lam); q = m.ot_home(*lam); i, j = np.indices(P.shape)
            mh = P[i > j].sum() + np.trace(P) * q
            for x in HL.build(P, q, 'KHL', {}):
                w = HL.settle(x['market'], x['pick'], x['line'], r.end, r.hg, r.ag, r.hg_reg, r.ag_reg)
                rows.append(dict(m=name, nf=nf, mh=mh, y=int(r.hg > r.ag), gid=r.id, p=x['p'], sh=x['shown'], w=w))
            rows.append(dict(m=name, nf=nf, mh=mh, y=int(r.hg > r.ag), gid=r.id, p=np.nan, sh=0, w='ML'))
    d0 = d1
R = pd.DataFrame(rows)
for name in ('stale', 'full'):
    for b, sub in R[R.m == name].groupby(R.nf >= 5):
        gm = sub[sub.w == 'ML']; p = gm.mh.clip(1e-6, 1 - 1e-6); y = gm.y
        ll = -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)); h = y.mean(); llc = -(h * np.log(h) + (1 - h) * np.log(1 - h))
        ln = sub[(sub.w.isin(['W', 'L'])) & (sub.p >= 0.75)]
        hit = (ln.w == 'W').mean(); pm = ln.p.mean(); n = len(ln)
        z = (hit - pm) / np.sqrt(pm * (1 - pm) / n) if n else np.nan
        print(f"{name:5} свежих {'≥5' if b else '<5'}: матчей {len(gm):3} лл {ll:.4f} (пост. {llc:.4f}) | линии P≥0,75: n={n} угадано {hit*100:.1f}% при {pm*100:.1f}% z={z:+.2f}")
