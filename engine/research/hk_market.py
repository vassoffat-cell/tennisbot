"""НХЛ: модель против рынка (кф ESPN pickcenter из протоколов) и их смесь; броски и вратари как признаки.
Обучение весов — сезон 2024/25, проверка — 2025/26 и далее (без утечки; прогнозы модели — hk_bt.py). Запуск: python3 hk_market.py"""
import json, numpy as np, pandas as pd
from scipy.optimize import minimize
R = pd.read_pickle('/home/claude/research/hk/NHL_bt_0.0035_2024-10-01.pkl')
g = pd.read_csv('/home/claude/tennisbot/hockey/data/games.csv.gz', dtype={'id': str}); g['t'] = pd.to_datetime(g.start)
b = pd.read_csv('/home/claude/tennisbot/hockey/data/box.csv.gz', dtype={'id': str})
R = R.merge(g[g.league == 'NHL'][['id', 't', 'home', 'away']], on=['t', 'home', 'away'], how='left').merge(b, on='id', how='left')
def am(x):
    try:
        x = float(x); return 100 / (x + 100) if x > 0 else -x / (-x + 100)
    except Exception: return np.nan
def odds(s):
    try:
        o = json.loads(s); return am(o['homeTeamOdds'].get('moneyLine')), am(o['awayTeamOdds'].get('moneyLine')), o.get('overUnder'), am(o.get('overOdds')), am(o.get('underOdds')), o.get('provider')
    except Exception: return (np.nan,) * 5 + (None,)
O = [odds(s) for s in R.odds]
R['qh'], R['qa'], R['ou'], R['qo'], R['qu'], R['prov'] = zip(*O)
R['pm'] = R.qh / (R.qh + R.qa)                                   # рынок без маржи (пропорционально)
R['marg'] = R.qh + R.qa - 1
if __name__ == '__main__':
    print('матчей бэктеста', len(R), '· с кф победы', int(R.pm.notna().sum()), '· маржа средняя', round(R.marg.mean() * 100, 2), '% · источники', R.prov.value_counts().to_dict())
    d = R[R.pm.notna()].copy(); y = (d.hg > d.ag).astype(float).values
    lg = lambda p: np.log(np.clip(p, 1e-6, 1 - 1e-6) / (1 - np.clip(p, 1e-6, 1 - 1e-6)))
    ll = lambda p, yy: -np.mean(yy * np.log(p) + (1 - yy) * np.log(1 - p))
    tr = (d.t < '2025-09-01').values; te = ~tr
    zm, zk = lg(d.mh.values), lg(d.pm.values)
    w = minimize(lambda w: ll(1 / (1 + np.exp(-(w[0] * zm[tr] + w[1] * zk[tr]))), y[tr]), [0.5, 0.5]).x
    pb = 1 / (1 + np.exp(-(w[0] * zm + w[1] * zk)))
    print(f'веса (обучение 2024/25, n={tr.sum()}): модель {w[0]:+.2f}, рынок {w[1]:+.2f}')
    for nm, k in (('обучение 2024/25', tr), ('ТЕСТ 2025/26+', te)):
        row = []
        for lab, p in (('модель', d.mh.values), ('рынок', d.pm.values), ('смесь', pb)):
            row.append(f'{lab}: угадано {((p[k] >= .5) == (y[k] == 1)).mean()*100:.1f}% лл {ll(np.clip(p[k],1e-6,1-1e-6), y[k]):.4f}')
        print(f'{nm} (n={k.sum()}): ' + ' | '.join(row) + f' | хозяева всегда {y[k].mean()*100:.1f}%')
    l_m = -(y * np.log(d.mh) + (1 - y) * np.log(1 - d.mh)); l_b = -(y * np.log(pb) + (1 - y) * np.log(1 - pb)); l_k = -(y * np.log(d.pm) + (1 - y) * np.log(1 - d.pm))
    for lab, a in (('смесь − модель', (l_b - l_m)[te]), ('смесь − рынок', (l_b - l_k)[te])):
        print(f'  тест, лог-лосс {lab}: {a.mean():+.4f} (z {a.mean() / (a.std(ddof=1) / np.sqrt(len(a))):+.1f})')
    # ROI: ставка на сторону смеси по кф рынка (как есть, с маржой)
    k = te; side = pb >= .5; won = side == (y == 1); dec = np.where(side, 1 / d.qh, 1 / d.qa)
    print(f'  тест: ставка на сторону смеси по кф букмекера: ROI {np.where(won, dec - 1, -1)[k].mean()*100:+.1f}% (n={k.sum()}); '
          f'только перевес смеси над ценой ≥ 3%: ', end='')
    edge = np.where(side, pb * (1 / d.qh), (1 - pb) * (1 / d.qa)) - 1; e = k & (edge >= 0.03)
    print(f'ROI {np.where(won, dec - 1, -1)[e].mean()*100:+.1f}% (n={e.sum()})')
