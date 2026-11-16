"""Задача 3 HANDOFF (16.11.2026): хоккей ИТ2 и фора ниже обещанного. Живой путь НХЛ — инверсия рынка DK (A21) + пустые ворота (A22).
Повторяем ровно его на истории: λ из рынка каждого матча (кф ESPN pickcenter из протоколов), линии hlines.build (kc = 1), итог по счёту.
Параметры модели (раздутие ничьих, ОТ) — fit на дату ДО проверки. Калибровка: рынок × сторона × линия, по сезонам. Запуск: python3 hk_side.py"""
import json, sys, numpy as np, pandas as pd
sys.path.insert(0, '/home/claude/tennisbot')
from hockey import hmodel as HM, hdata as HD, hlines as HL

def am(x):
    try:
        x = float(x); return 100 / (x + 100) if x > 0 else -x / (-x + 100)
    except Exception: return np.nan

g = HD.load(); g['t'] = pd.to_datetime(g.start)
b = pd.read_csv('/home/claude/tennisbot/hockey/data/box.csv.gz', dtype={'id': str})
x = g[(g.league == 'NHL') & g.end.isin(['REG', 'OT', 'SO'])].merge(b[['id', 'odds']], on='id', how='inner')
M = HM.fit(g, 'NHL', pd.Timestamp('2025-10-01'))
rows = []
for r in x.itertuples():
    try:
        o = json.loads(r.odds)
    except Exception:
        continue
    qh, qa = am(o.get('homeTeamOdds', {}).get('moneyLine')), am(o.get('awayTeamOdds', {}).get('moneyLine'))
    qo, qu, line = am(o.get('overOdds')), am(o.get('underOdds')), o.get('overUnder')
    if not (qh == qh and qa == qa):
        continue
    pml = qh / (qh + qa)
    po = qo / (qo + qu) if qo == qo and qu == qu else None
    lam = HM.invert(M, pml, float(line) if line else None, po, total0=6.0)
    P = M.matrix(*lam); q = M.ot_home(*lam)
    for ln in HL.build(P, q, 'NHL', {}):
        w = HL.settle(ln['market'], ln['pick'], ln['line'], r.end, r.hg, r.ag, r.hg_reg, r.ag_reg)
        if w in ('W', 'L'):
            rows.append(dict(t=r.t, mk=ln['market'], pick=ln['pick'], line=ln['line'], p=ln['p'], shown=ln['shown'], w=int(w == 'W'),
                             lh=lam[0], la=lam[1], hr=r.hg_reg, ar=r.ag_reg))
R = pd.DataFrame(rows); R['season'] = np.where(R.t.dt.month >= 8, R.t.dt.year, R.t.dt.year - 1)
R.to_pickle('/home/claude/research/hk/side.pkl')
def z(d):
    n, W, P = len(d), d.w.sum(), d.p.sum(); v = (d.p * (1 - d.p)).sum()
    return pd.Series(dict(n=n, hit=round(W / n * 100, 1), prom=round(P / n * 100, 1), z=round((W - P) / np.sqrt(v), 2)))
s = R[(R.p >= .6) & (R.p <= .85)]
print('матчей', R.drop_duplicates(['t', 'lh']).shape[0], '| линий P 0,60–0,85', len(s))
print(s[s.mk.isin(['IT1', 'IT2', 'AH', 'TOT'])].groupby(['mk', 'pick', 'season']).apply(z).to_string())
m = R.drop_duplicates(['t', 'lh'])
print(m.groupby('season').agg(n=('hr', 'size'), hr=('hr', 'mean'), lh=('lh', 'mean'), ar=('ar', 'mean'), la=('la', 'mean')).round(3).to_string())
