"""T33 · хоккей (НХЛ): скрытые факторы календаря против модели — остаток «победа хозяев с ОТ» по срезам.
Данные — бэктест без утечки (hk_bt.py NHL, 2024–26). Запуск: python3 t33_hockey.py"""
import numpy as np, pandas as pd
R = pd.read_pickle('/home/claude/research/hk/NHL_bt_0.0035_2024-10-01.pkl')
g = pd.read_csv('/home/claude/tennisbot/hockey/data/games.csv.gz')
g = g[(g.league == 'NHL') & g.end.isin(['REG', 'OT', 'SO'])].copy(); g['t'] = pd.to_datetime(g.start)
lg = pd.concat([g[['t', 'home', 'away']].assign(team=g.home, at_home=1), g[['t', 'home', 'away']].assign(team=g.away, at_home=0)]).sort_values('t')
lg['d'] = (lg.t - pd.Timedelta(hours=5)).dt.normalize()
rows = {}
for team, x in lg.groupby('team'):
    d = x.d.values; ah = x.at_home.values
    for k in range(len(x)):
        rest = (d[k] - d[k - 1]) / np.timedelta64(1, 'D') if k else np.nan
        g4 = int(((d[k] - d[:k]) / np.timedelta64(1, 'D') <= 3).sum())          # матчей за предыдущие 3 дня
        g7 = int(((d[k] - d[:k]) / np.timedelta64(1, 'D') <= 6).sum())
        trip = 0                                                                # длина текущей серии (дом/выезд) до этого матча
        j = k - 1
        while j >= 0 and ah[j] == ah[k] and (d[k] - d[j]) / np.timedelta64(1, 'D') <= 14:
            trip += 1; j -= 1
        rows[(team, x.t.values[k])] = (rest, g4, g7, trip)
def col(side, i):
    return np.array([rows.get((tm, np.datetime64(t)), (np.nan,) * 4)[i] for tm, t in zip(R[side], R.t)], float)
rh, ra, h4, a4, h7, a7, ht, at = col('home', 0), col('away', 0), col('home', 1), col('away', 1), col('home', 2), col('away', 2), col('home', 3), col('away', 3)
y = (R.hg > R.ag).astype(float).values; p = R.mh.values; first = (R.t < '2025-09-01').values
def z(m, name):
    m = m & ~np.isnan(rh) & ~np.isnan(ra)
    f = lambda k: (y[k] - p[k]).sum() / np.sqrt((p[k] * (1 - p[k])).sum()) if k.sum() >= 40 else np.nan
    print(f'{name:<62} n={m.sum():>4} факт {y[m].mean()*100:5.1f}% модель {p[m].mean()*100:5.1f}% Δ {100*(y[m].mean()-p[m].mean()):+5.1f} z {f(m):+.1f} ({f(m&first):+.1f} / {f(m&~first):+.1f})')
print('Победа хозяев (с ОТ): факт против модели')
z((ra == 1) & (rh >= 2), 'гости 2-й день подряд, хозяева отдохнули [внедрено A11]')
z((rh == 1) & (ra >= 2), 'хозяева 2-й день подряд, гости отдохнули')
z((a4 >= 2) & (h4 <= 1), 'гости: 3-й матч за 4 дня, хозяева — нет')
z((h4 >= 2) & (a4 <= 1), 'хозяева: 3-й матч за 4 дня, гости — нет')
z((a7 >= 3) & (h7 <= 2), 'гости: 4-й матч за 7 дней, хозяева — нет')
z((at >= 3), 'гости: 4-й и далее матч выездной серии')
z((at >= 3) & (ra >= 2), '  то же, без «второй день подряд»')
z((ht >= 3), 'хозяева: 4-й и далее матч домашней серии')
z((ht == 0) & (rh >= 2), 'хозяева: первый домашний матч после выезда')
z((rh >= 4) & (ra <= 2), 'хозяева отдыхали 4+ дня, гости — нет («ржавчина»)')
z((ra >= 4) & (rh <= 2), 'гости отдыхали 4+ дня, хозяева — нет')
z((ra == 1) & (rh >= 2) & (at >= 2), 'гости 2-й день подряд И 3-й+ матч выезда')
