"""T33 · футбол: скрытые факторы против модели и против Pinnacle без маржи (остаток «победа хозяев» и «тотал больше 2,5»).
Данные — a2_backtest.py (еженедельная переподгонка без утечки, 07.2023 → конец), лиги бота. Запуск: python3 t33_football.py"""
import numpy as np, pandas as pd
d = pd.read_pickle('/home/claude/research/out/a2_oos.pkl')
m = pd.read_csv('/home/claude/tennisbot/football/data/matches.csv.gz'); m['date'] = pd.to_datetime(m.date)
u = pd.read_csv('/home/claude/tennisbot/football/data/uefa_hist.csv'); u['date'] = pd.to_datetime(u.date)
# все матчи команды: лига + еврокубки (названия football-data)
lg = pd.concat([m[['date', 'home']].rename(columns={'home': 'team'}).assign(eu=0), m[['date', 'away']].rename(columns={'away': 'team'}).assign(eu=0),
                u[['date', 'home_fd']].rename(columns={'home_fd': 'team'}).assign(eu=1, trip=0),
                u[['date', 'away_fd']].rename(columns={'away_fd': 'team'}).assign(eu=1, trip=1)]).dropna(subset=['team']).sort_values('date')
prev = {}
for team, x in lg.groupby('team'):
    prev[team] = (x.date.values, x.eu.values, x.get('trip', pd.Series(0, index=x.index)).fillna(0).values)
def last(team, date):
    if team not in prev: return (np.nan, 0, 0)
    ds, eu, tr = prev[team]; k = np.searchsorted(ds, np.datetime64(date)) - 1
    return ((np.datetime64(date) - ds[k]) / np.timedelta64(1, 'D'), int(eu[k]), int(tr[k])) if k >= 0 else (np.nan, 0, 0)
mm = m.loc[d.i]
H = [last(t, dt) for t, dt in zip(mm.home, mm.date)]; A = [last(t, dt) for t, dt in zip(mm.away, mm.date)]
rh, euh, trh = map(np.array, zip(*H)); ra, eua, tra = map(np.array, zip(*A))
# новички лиги: команды не было в этом дивизионе в прошлом сезоне
seas = m.groupby(['div', 'season']).apply(lambda x: set(x.home) | set(x.away))
def promoted(div, season, team):
    try:
        a, b = season.split('/'); ps = f'{int(a)-1}/{int(b)-1}'
        return team not in seas.get((div, ps), {team})
    except Exception: return False
ph = np.array([promoted(a, b, c) for a, b, c in zip(mm['div'], mm.season, mm.home)]); pa = np.array([promoted(a, b, c) for a, b, c in zip(mm['div'], mm.season, mm.away)])
# номер тура команды в сезоне
rnd = m.sort_values('date').groupby(['div', 'season', 'home']).cumcount()
rn = (m.assign(k=1).sort_values('date'))
cnt = {}
order = []
for r in rn.itertuples():
    a = cnt.get((r.div, r.season, r.home), 0); b = cnt.get((r.div, r.season, r.away), 0)
    order.append((r.Index, min(a, b))); cnt[(r.div, r.season, r.home)] = a + 1; cnt[(r.div, r.season, r.away)] = b + 1
rno = pd.Series(dict(order)).reindex(d.i).values
y = (d.hg > d.ag).astype(float).values; over = ((d.hg + d.ag) > 2.5).astype(float).values
pm = d.mh.values; po = d.mo.values
q = 1 / d[['ps_h', 'ps_d', 'ps_a']].values; pp = q[:, 0] / q.sum(1)                      # Pinnacle без маржи (пропорционально)
qo = 1 / d[['ps_o', 'ps_u']].values; ppo = qo[:, 0] / qo.sum(1)
first = (d.date < '2025-01-01').values
def zz(yv, p, k):
    k = k & ~np.isnan(p)
    return (yv[k] - p[k]).sum() / np.sqrt((p[k] * (1 - p[k])).sum()) if k.sum() >= 50 else np.nan
def T(mask, name):
    mask = np.asarray(mask, bool)
    k = mask & ~np.isnan(pp)
    print(f'{name:<58} n={mask.sum():>5} | хозяева факт {y[mask].mean()*100:4.1f}% мод {pm[mask].mean()*100:4.1f}% z {zz(y,pm,mask):+.1f} ({zz(y,pm,mask&first):+.1f}/{zz(y,pm,mask&~first):+.1f})'
          f' · Pinn {pp[k].mean()*100:4.1f}% z {zz(y,pp,mask):+.1f} | ТБ2,5 факт {over[mask].mean()*100:4.1f}% мод {po[mask].mean()*100:4.1f}% z {zz(over,po,mask):+.1f} · Pinn z {zz(over,ppo,mask):+.1f}')
print(f'всего матчей {len(d)}; с ценой Pinnacle {int((~np.isnan(pp)).sum())}')
T(np.ones(len(d), bool), 'все матчи (контроль)')
T((euh == 1) & (rh <= 4) & ~((eua == 1) & (ra <= 4)), 'хозяева играли в еврокубке ≤ 4 дней назад, гости — нет')
T((eua == 1) & (ra <= 4) & ~((euh == 1) & (rh <= 4)), 'гости играли в еврокубке ≤ 4 дней назад, хозяева — нет')
T((eua == 1) & (ra <= 4) & (tra == 1) & ~((euh == 1) & (rh <= 4)), '  гости — после ВЫЕЗДНОГО еврокубка')
T((euh == 1) & (rh <= 4) & (trh == 1) & ~((eua == 1) & (ra <= 4)), '  хозяева — после ВЫЕЗДНОГО еврокубка')
T((rh <= 3) & (ra >= 6), 'хозяева отдыхали ≤ 3 дней, гости ≥ 6')
T((ra <= 3) & (rh >= 6), 'гости отдыхали ≤ 3 дней, хозяева ≥ 6')
T((rh >= 12) & (ra >= 12), 'первый тур после перерыва (обе ≥ 12 дней)')
T(ph & ~pa, 'хозяева — новичок лиги')
T(pa & ~ph, 'гости — новичок лиги')
T(ph & ~pa & (rno < 6), '  хозяева — новичок, первые 6 туров')
T(pa & ~ph & (rno < 6), '  гости — новичок, первые 6 туров')
T(rno < 3, 'первые 3 тура сезона')
T(rno >= 33, 'концовка сезона (34-й тур и далее)')
dow = pd.to_datetime(d.date).dt.dayofweek.values
T(np.isin(dow, [1, 2, 3]), 'матчи вт–чт (середина недели)')
