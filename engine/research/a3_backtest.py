"""A3: футбол — порог показа линий P ≥ 0,50 / 0,55 / 0,60. Модель — еженедельная переподгонка без утечки (как A2),
линии — flines.build, k самокалибровки = 1 (по рынкам — по обучающему сезону, см. ниже). Проверка 07.2024 → конец данных."""
import sys
import numpy as np, pandas as pd
sys.path.insert(0, '/home/claude/tennisbot/football')
import config as C, model, dc, flines as FL

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
        for r in wk[['country', 'home', 'div', 'away', 'date', 'hg', 'ag']].itertuples():
            if r.country not in w.sup:
                continue
            lam = model._combine(w._domestic(w.sup[r.country], r[3], r.home, r.away, False),
                                 w._domestic(w.tot[r.country], r[3], r.home, r.away, False))
            if lam is None:
                continue
            for x in FL.build(dc.score_matrix(lam[0], lam[1], C.RHO), {}):
                if x['market'] == '1X2':
                    continue
                res = FL.settle(x['market'], x['pick'], x['line'], 'FT', int(r.hg), int(r.ag))
                rows.append(dict(date=r.date, mid=r.Index, market=x['market'], p_raw=x['p_raw'], res=res))
    pd.DataFrame(rows).to_pickle(f'{OUT}/a3_lines.pkl')
    print('lines', len(rows)); sys.exit()

L = pd.read_pickle(f'{OUT}/a3_lines.pkl')
L = L[L.res != 'P']
tr, te = L[L.date < '2024-07-01'], L[L.date >= '2024-07-01'].copy()
k = {mk: FL.calibrate(g.p_raw.values, (g.res == 'W').values)[0] for mk, g in tr.groupby('market')}   # k по обучению
te['p'] = [FL.scale(p, k.get(mk, 1.0)) for p, mk in zip(te.p_raw, te.market)]
te['w'] = (te.res == 'W').astype(float)
days = te.date.dt.normalize().nunique()
print('k по сезону 2023/24:', {a: round(b, 3) for a, b in k.items()}, f'; дней с матчами {days}')
base = None
for lo in (0.50, 0.55, 0.60):
    for excl in ((), ('BTTS',)):
        s = te[(te.p >= lo) & (te.p <= C.P_SHOW[1]) & ~te.market.isin(excl)]
        hits = s.w.sum()
        tag = f'P≥{lo:.2f}' + (' без ОЗ' if excl else '')
        if base is None:
            base = (s, hits)
        print(f'{tag:14s} линий {len(s):6d} угадано {int(hits):6d} ({s.w.mean()*100:5.2f}%)  '
              f'угаданных/день {hits/days:5.2f} ({(hits/base[1]-1)*100:+5.1f}%)  ожид.P {s.p.mean()*100:5.2f}%')
print('\nпо рынкам, P≥0,50:')
s = te[(te.p >= 0.5) & (te.p <= C.P_SHOW[1])]
print(s.groupby('market').agg(n=('w', 'size'), acc=('w', 'mean'), p=('p', 'mean'),
                              n_lt55=('p', lambda x: int((x < 0.55).sum())), n_lt60=('p', lambda x: int((x < 0.60).sum()))).round(3))
