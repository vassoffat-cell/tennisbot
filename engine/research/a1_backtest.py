"""A1: исход тенниса — модель против Pinnacle без маржи и смеси (веса по 2023, проверка 2024–26). Без утечки."""
import sys, time
import numpy as np, pandas as pd
sys.path.insert(0, '/home/claude/tennisbot')
import config as C, dataset, state as S, serve_model as SM

OUT = '/tmp/autopilot'


def oos(tour):
    h = dataset.build(tour)
    pl = dataset.players(tour)
    cols = ['w_1stWon', 'w_2ndWon', 'l_1stWon', 'l_2ndWon', 'w_svpt', 'l_svpt']
    t = h[cols].sum()
    f_av = float((t.w_1stWon + t.w_2ndWon + t.l_1stWon + t.l_2ndWon) / (t.w_svpt + t.l_svpt))
    st = S.TourState(tour, dict(zip(pl.player_id, pl.dob)), f_av)
    st.table = S.win_table(tour)
    st.srv, pa, pb = SM.walk_forward(h, C.SERVE_MODEL[tour])
    X = []
    for i, r in enumerate(h.itertuples(index=False)):
        for pid, pt in ((r.winner_id, r.WPts), (r.loser_id, r.LPts)):
            if pt == pt and pt:
                st.pts[pid] = float(pt)
        X.append(st.snapshot(r.winner_id, r.loser_id, r.Date, r.Surface, r.Location, r.season, t21=(pa[i], pb[i])).x)
        st.update(r)
    X = np.array(X)
    use = (h.Comment == 'Completed').values & (h.season >= 2020).values
    rng = np.random.default_rng(5)
    flip = rng.random(len(X)) < 0.5
    Xs = X * np.where(flip, -1.0, 1.0)[:, None]; y = (~flip).astype(float)
    tr = use & (h.season <= 2022).values
    b = S._fit(Xs[tr], y[tr])
    d = h[['Date', 'season', 'Comment', 'PSW', 'PSL']].copy()
    d['zm'] = X @ b                                   # логит P(победитель) от модели, обучение ≤2022
    d['use'] = use
    d.to_pickle(f'{OUT}/a1_{tour}.pkl')
    return d


if __name__ == '__main__':
    for tour in sys.argv[1:]:
        t0 = time.time(); d = oos(tour)
        print(tour, len(d), f'{time.time() - t0:.0f} s', flush=True)
