"""A1b: линии тенниса (фора, тотал, ИТ) из распределения с P исхода = смесь Pinnacle+модель против P модели.
Модель: веса ≤2022 (без утечки), смесь: pick.WEIGHTS (подобраны по 2023), проверка 2024–26, матчи с кф Pinnacle."""
import sys, time
import numpy as np, pandas as pd
sys.path.insert(0, '/home/claude/tennisbot')
import config as C, dataset, state as S, serve_model as SM, forecast as FC, lines as LN, pick as PK
from markov import Outcome

OUT = '/tmp/autopilot'
sg = lambda z: 1 / (1 + np.exp(-z))


def walk(tour):
    h = dataset.build(tour)
    pl = dataset.players(tour)
    cols = ['w_1stWon', 'w_2ndWon', 'l_1stWon', 'l_2ndWon', 'w_svpt', 'l_svpt']
    t = h[cols].sum()
    f_av = float((t.w_1stWon + t.w_2ndWon + t.l_1stWon + t.l_2ndWon) / (t.w_svpt + t.l_svpt))
    st = S.TourState(tour, dict(zip(pl.player_id, pl.dob)), f_av)
    st.table = S.win_table(tour)
    st.srv, pa, pb = SM.walk_forward(h, C.SERVE_MODEL[tour])
    X, bc, nmin = [], [], []
    for i, r in enumerate(h.itertuples(index=False)):
        for pid, pt in ((r.winner_id, r.WPts), (r.loser_id, r.LPts)):
            if pt == pt and pt:
                st.pts[pid] = float(pt)
        sn = st.snapshot(r.winner_id, r.loser_id, r.Date, r.Surface, r.Location, r.season, t21=(pa[i], pb[i]))
        X.append(sn.x); bc.append((sn.pa_bc, sn.pb_bc)); nmin.append(min(st.n.get(r.winner_id, 0), st.n.get(r.loser_id, 0)))
        st.update(r)
    X = np.array(X)
    use = (h.Comment == 'Completed').values & (h.season >= 2020).values
    rng = np.random.default_rng(5)
    flip = rng.random(len(X)) < 0.5
    Xs = X * np.where(flip, -1.0, 1.0)[:, None]; y = (~flip).astype(float)
    b = S._fit(Xs[use & (h.season <= 2022).values], y[use & (h.season <= 2022).values])
    d = h[['Date', 'season', 'Series', 'Best of', 'PSW', 'PSL'] + [f'{a}{k}' for k in range(1, 6) for a in 'WL']].copy()
    d['zm'] = X @ b; d['pa'] = [x[0] for x in bc]; d['pb'] = [x[1] for x in bc]; d['nmin'] = nmin; d['use'] = use
    return d[use & (d.season >= 2024)]


def run(tour, step, off=0):
    import os
    f = f'{OUT}/a1b_walk_{tour}.pkl'
    d = pd.read_pickle(f) if os.path.exists(f) else walk(tour)
    d.to_pickle(f)
    for c in ('PSW', 'PSL'):
        d[c] = pd.to_numeric(d[c], errors='coerce')
    d = d[(d.PSW > 1) & (d.PSL > 1) & d.W1.notna() & d.L1.notna() & (d['Best of'] != 5)].iloc[off::step]
    md = type('M', (), {'tour': tour})()
    rng = np.random.default_rng(11)
    rows = []
    t0 = time.time()
    for n, r in enumerate(d.itertuples(index=False)):
        sets = [(int(getattr(r, f'W{k}')), int(getattr(r, f'L{k}'))) for k in range(1, 6)
                if getattr(r, f'W{k}') == getattr(r, f'W{k}') and getattr(r, f'L{k}') == getattr(r, f'L{k}')]
        fl = rng.random() < 0.5                                    # игрок 1 — проигравший
        if fl:
            sets = [(b, a) for a, b in sets]
        pm = float(sg(-r.zm if fl else r.zm))
        ppin = PK.shin([r.PSL, r.PSW] if fl else [r.PSW, r.PSL])[0]
        pb_ = float(min(max(PK.fair(pm, ppin, 'pinnacle'), 0.01), 0.99))
        snap = S.Snapshot(None, r.pb if fl else r.pa, r.pa if fl else r.pb)
        bo = int(r[3]) if r[3] == r[3] else 3
        tb = 10 if str(r.Series) == 'Grand Slam' else 7
        o = Outcome(tuple(sets))
        sig = LN.sigma_for(int(r.nmin))
        try:
            _, Dm = FC.distribution(md, snap, bo, tb, p_override=pm)
            _, Db = FC.distribution(md, snap, bo, tb, p_override=pb_)
        except Exception as e:
            continue
        for src, D, p in (('model', Dm, pm), ('blend', Db, pb_)):
            for x in LN.build(D, p, {}, sig):
                if x['market'] == 'ML':
                    continue
                res = LN.settle(x['market'], x['pick'], x['line'], o)
                # та же линия по другому распределению (парное сравнение лог-лосса)
                rows.append(dict(mid=n * 10 + off, date=r.Date, src=src, slot=x['slot'], market=x['market'], pick=x['pick'], line=x['line'],
                                 p=x['p_raw'], res=res))
        if n % 500 == 0:
            print(tour, n, len(d), f'{time.time() - t0:.0f} s', flush=True)
    pd.DataFrame(rows).to_pickle(f'{OUT}/a1b_{tour}_{off}.pkl')


if __name__ == '__main__':
    run(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]))
