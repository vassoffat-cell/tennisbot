"""T33 · футбол: «мёртвые» туры — команде нечего решать (не достать еврозону и не вылететь), сопернику есть что.
Таблица — по сыгранным матчам до даты; «еврозона» — 6-е место, «зона вылета» — 3-е с конца; осталось ≤ 5 туров.
Остаток «победа хозяев» против модели (a2_oos, без утечки) и Pinnacle без маржи. Запуск: python3 t33_deadrubber.py"""
import numpy as np, pandas as pd
d = pd.read_pickle('/home/claude/research/out/a2_oos.pkl')
m = pd.read_csv('/home/claude/tennisbot/football/data/matches.csv.gz'); m['date'] = pd.to_datetime(m.date)
state = {}
for (dv, ss), x in m[m.hg.notna()].groupby(['div', 'season']):
    teams = sorted(set(x.home) | set(x.away)); n = len(teams); total = 2 * (n - 1)
    pts = dict.fromkeys(teams, 0); pl = dict.fromkeys(teams, 0)
    for r in x.sort_values('date').itertuples():
        tab = sorted(pts.values(), reverse=True)
        if n >= 16 and len(tab) == n:
            eu, rel = tab[5], tab[n - 3]                       # очки 6-го места и первой команды зоны вылета
            safe = tab[n - 4]
            def dead(t):
                left = total - pl[t]
                return left <= 5 and pts[t] + 3 * left < eu and pts[t] > rel + 3 * min(left, 5)
            def stake(t):
                left = total - pl[t]
                return left <= 5 and ((pts[t] <= safe + 3 and pts[t] + 3 * left >= safe) or (abs(pts[t] - eu) <= 4) or (abs(pts[t] - tab[0]) <= 4) or (abs(pts[t]-tab[3])<=3))
            state[r.Index] = (dead(r.home), dead(r.away), stake(r.home), stake(r.away), total - pl[r.home])
        ph, pa = (3, 0) if r.hg > r.ag else (1, 1) if r.hg == r.ag else (0, 3)
        pts[r.home] += ph; pts[r.away] += pa; pl[r.home] += 1; pl[r.away] += 1
S = [state.get(i, (False, False, False, False, 99)) for i in d.i]
dh, da, sh, sa, left = map(np.array, zip(*S))
y = (d.hg > d.ag).astype(float).values; ya = (d.hg < d.ag).astype(float).values; pm = d.mh.values; pma = d.ma.values
q = 1 / d[['ps_h', 'ps_d', 'ps_a']].values; pp = q[:, 0] / q.sum(1); ppa = q[:, 2] / q.sum(1)
over = ((d.hg + d.ag) > 2.5).astype(float).values; po = d.mo.values
def z(yv, p, k):
    k = k & ~np.isnan(p)
    return (yv[k] - p[k]).sum() / np.sqrt((p[k] * (1 - p[k])).sum()) if k.sum() >= 40 else np.nan
def T(mask, name, side='h'):
    yy, mod, pin = (y, pm, pp) if side == 'h' else (ya, pma, ppa)
    print(f'{name:<66} n={mask.sum():>4} | факт {yy[mask].mean()*100:4.1f}% мод {mod[mask].mean()*100:4.1f}% z {z(yy,mod,mask):+.1f} · Pinn {np.nanmean(pin[mask])*100:4.1f}% z {z(yy,pin,mask):+.1f} | ТБ2,5 факт {over[mask].mean()*100:4.1f}% мод {po[mask].mean()*100:4.1f}% z {z(over,po,mask):+.1f}')
print('осталось ≤ 5 туров, матчей:', int((left <= 5).sum()))
T(left <= 5, 'все матчи последних 5 туров — победа хозяев')
T(dh & sa & ~da, 'хозяевам нечего решать, гостям есть что — победа ГОСТЕЙ', 'a')
T(da & sh & ~dh, 'гостям нечего решать, хозяевам есть что — победа ХОЗЯЕВ', 'h')
T(dh & da, 'обеим нечего решать — победа хозяев')
T(sh & sa, 'обеим есть что решать — победа хозяев')
T(dh & ~da, 'хозяевам нечего решать (гости — любые) — победа ГОСТЕЙ', 'a')
T(da & ~dh, 'гостям нечего решать (хозяева — любые) — победа ХОЗЯЕВ', 'h')
