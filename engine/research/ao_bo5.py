"""19.11.2026 · Подготовка к Australian Open: линии матчей до трёх побед (ATP, Большие шлемы) — калибровка на истории.

Без утечки: веса модели исхода обучены на сезонах ≤ 2022, проверка 2023–26 (как a1b_backtest). Для каждого матча:
P исхода модели (и смесь с Pinnacle, где есть кф) → распределение счёта (марковская цепь, bo5, тай-брейк 5-го сета до 10) →
линии системы (lines.build: фора, ИТ1, ИТ2, тотал фикс. 35,5) → итог по счёту. Сравнение: обещанная P против факта,
по рынкам и диапазонам P, отдельно для bo5 и контрольно bo3 (тот же период, Шлемы WTA не входят — ATP).
Запуск: python3 engine/research/ao_bo5.py [шаг_bo3]   (кэш прохода — /tmp/autopilot/a1b_walk_atp23.pkl)
"""
from __future__ import annotations

import math
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, '/home/claude/tennisbot')
sys.path.insert(0, '/home/claude/tennisbot/engine/research')
import config as C, forecast as FC, lines as LN, pick as PK, state as S  # noqa: E402
from markov import Outcome  # noqa: E402
import a1b_backtest as A  # noqa: E402

OUT = '/tmp/autopilot'
STEP5 = 1                                   # bo5: все матчи (свёрнутая цепь — точная, ≈ 0,1 с на матч)

# ускорение только для проверки (боевой код не меняется): все рынки линий зависят лишь от (победитель, геймы A, геймы B) —
# распределение сворачивается до этих ключей; точность подбора разницы подач 1e-6 вместо 1e-9 (ошибка P < 1e-5)
import markov as _MV  # noqa: E402
from scipy.optimize import brentq as _bq  # noqa: E402


class _O:
    """Свёрнутый исход: только то, от чего зависят рынки линий (победитель, геймы A, геймы B)."""
    __slots__ = ('a_won', 'games_a', 'games_b')

    def __init__(self, w, ga, gb):
        self.a_won, self.games_a, self.games_b = w, ga, gb


def _collapsed(pa, pb, best_of=3, final_tb_to=7, mom=0.0, set_mom=0.0):
    """То же распределение, что markov.match_distribution, но динамикой по (сеты A, сеты B, кто подаёт, геймы A, геймы B)."""
    need = best_of // 2 + 1
    cache = {}

    def sd(a_first, deciding, lead):
        key = (a_first, final_tb_to if deciding else 7, lead)
        if key not in cache:
            cache[key] = _MV.set_distribution(pa, pb, a_first, key[1], mom, set_mom * lead)
        return cache[key]

    st = {(0, 0, True, 0, 0): 0.5, (0, 0, False, 0, 0): 0.5}
    out = {}
    while st:
        nx = {}
        for (wa, wb, af, ga, gb), pr in st.items():
            deciding = wa == need - 1 and wb == need - 1
            for sc, q in sd(af, deciding, wa - wb).items():
                na, nb = wa + (sc[0] > sc[1]), wb + (sc[1] > sc[0])
                k = (na, nb, _MV.next_set_a_serves(sc, af), ga + sc[0], gb + sc[1])
                if na == need or nb == need:
                    ok = (na == need, k[3], k[4])
                    out[ok] = out.get(ok, 0.0) + pr * q
                else:
                    nx[k] = nx.get(k, 0.0) + pr * q
        st = nx
    return {_O(*k): v for k, v in out.items()}


FC.match_distribution = _collapsed
FC.brentq = lambda f, a, b, xtol=1e-6, **k: _bq(f, a, b, xtol=max(xtol, 1e-6), **k)
sg = lambda z: 1 / (1 + np.exp(-z))


def walk() -> pd.DataFrame:
    f = f'{OUT}/a1b_walk_atp23.pkl'
    if os.path.exists(f):
        return pd.read_pickle(f)
    os.makedirs(OUT, exist_ok=True)
    src = A.walk.__code__
    # тот же проход, что a1b_backtest.walk, но с сезона 2023 (веса ≤ 2022 — 2023 тоже вне обучения)
    import dataset, serve_model as SM
    h = dataset.build('atp')
    pl = dataset.players('atp')
    cols = ['w_1stWon', 'w_2ndWon', 'l_1stWon', 'l_2ndWon', 'w_svpt', 'l_svpt']
    t = h[cols].sum()
    f_av = float((t.w_1stWon + t.w_2ndWon + t.l_1stWon + t.l_2ndWon) / (t.w_svpt + t.l_svpt))
    st = S.TourState('atp', dict(zip(pl.player_id, pl.dob)), f_av)
    st.table = S.win_table('atp')
    st.srv, pa, pb = SM.walk_forward(h, C.SERVE_MODEL['atp'])
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
    d = h[['Date', 'season', 'Series', 'Tournament', 'Best of', 'PSW', 'PSL'] + [f'{a}{k}' for k in range(1, 6) for a in 'WL']].copy()
    d['zm'] = X @ b; d['pa'] = [x[0] for x in bc]; d['pb'] = [x[1] for x in bc]; d['nmin'] = nmin; d['use'] = use
    d = d[use & (d.season >= 2023)]
    d.to_pickle(f)
    del src
    return d


def _chunk(args) -> list[dict]:
    part, off, step, step3 = args
    d = walk()
    for c in ('PSW', 'PSL'):
        d[c] = pd.to_numeric(d[c], errors='coerce')
    d = d[d.W1.notna() & d.L1.notna()]
    bo = pd.to_numeric(d['Best of'], errors='coerce').fillna(3).astype(int)
    dd = (d[bo == 5].iloc[::STEP5] if part == 'bo5' else d[bo == 3].iloc[::step3]).iloc[off::step]
    md = type('M', (), {'tour': 'atp'})()
    rng = np.random.default_rng(11 + off)
    rows = []
    t0 = time.time()
    for n, r in enumerate(dd.itertuples(index=False)):
        sets = [(int(getattr(r, f'W{k}')), int(getattr(r, f'L{k}'))) for k in range(1, 6)
                if getattr(r, f'W{k}') == getattr(r, f'W{k}') and getattr(r, f'L{k}') == getattr(r, f'L{k}')]
        need = 3 if part == 'bo5' else 2
        if sum(a > b for a, b in sets) != need:          # отказ / неполный счёт
            continue
        fl = rng.random() < 0.5
        if fl:
            sets = [(b, a) for a, b in sets]
        pm = float(sg(-r.zm if fl else r.zm))
        has_pin = r.PSW == r.PSW and r.PSL == r.PSL and r.PSW > 1 and r.PSL > 1
        # как в бою (A1, ATP): P исхода — смесь с Pinnacle, где есть кф; иначе модель
        p = float(min(max(PK.fair(pm, PK.shin([r.PSL, r.PSW] if fl else [r.PSW, r.PSL])[0], 'pinnacle'), .01), .99)) if has_pin else pm
        src = 'pin' if has_pin else 'model'
        snap = S.Snapshot(None, r.pb if fl else r.pa, r.pa if fl else r.pb)
        B = 5 if part == 'bo5' else 3
        tb = 10 if str(r.Series) == 'Grand Slam' else 7
        o = Outcome(tuple(sets))
        try:
            _, D = FC.distribution(md, snap, B, tb, p_override=p)
        except Exception:
            continue
        for x in LN.build(D, p, {}, LN.sigma_for(int(r.nmin)), fixed_total=C.FIXED_TOTAL.get(('atp', B))):
            res = LN.settle(x['market'], x['pick'], x['line'], o) if x['market'] != 'ML' else \
                ('W' if (x['pick'] == '1') == (sum(a > b for a, b in sets) == need) else 'L')
            rows.append(dict(part=part, src=src, season=r.season, tourn=str(r.Tournament), market=x['market'], pick=x['pick'],
                             line=x['line'], p=x['p_raw'], shown=x['shown'], res=res, games=sum(a + b for a, b in sets)))
        if n % 100 == 0:
            print(part, off, n, len(dd), f'{time.time() - t0:.0f} s', flush=True)
    return rows


def run(step3: int = 20) -> pd.DataFrame:
    from multiprocessing import Pool
    walk()
    jobs = [('bo5', 0, 2, step3), ('bo5', 1, 2, step3), ('bo3', 0, 2, step3), ('bo3', 1, 2, step3)]
    with Pool(2) as pool:
        parts = pool.map(_chunk, jobs, chunksize=1)
    out = pd.DataFrame([x for p in parts for x in p])
    out.to_pickle(f'{OUT}/ao_bo5.pkl')
    return out


def calib(g: pd.DataFrame) -> dict:
    g = g[g.res.isin(['W', 'L'])]
    if not len(g):
        return {}
    w = (g.res == 'W').astype(float)
    p = g.p.clip(1e-4, 1 - 1e-4)
    z = (w.sum() - p.sum()) / math.sqrt((p * (1 - p)).sum())
    return dict(n=len(g), fact=round(w.mean() * 100, 1), promised=round(p.mean() * 100, 1), z=round(z, 2),
                ll=round(float(-(w * np.log(p) + (1 - w) * np.log(1 - p)).mean()), 4))


def report(o: pd.DataFrame) -> None:
    sh = o[(o.shown == 1) & (o.market != 'ML')]
    for part in ('bo5', 'bo3'):
        for src in ('pin', 'model', 'all'):
            g = sh[(sh.part == part) & ((sh.src == src) | (src == 'all'))]
            print(f'\n== {part} · {src}: показываемые линии {calib(g)}')
            for mk in ('HCP', 'IT1', 'IT2', 'TOTAL'):
                x = g[g.market == mk]
                print(f'   {mk:5s} {calib(x)}')
                if mk == 'TOTAL' and part == 'bo5':
                    for pk in ('O', 'U'):
                        print(f'      {pk} {calib(x[x.pick == pk])}')
            for lo, hi in ((.5, .6), (.6, .7), (.7, .8)):
                print(f'   P {lo:.1f}–{hi:.1f} {calib(g[(g.p >= lo) & (g.p < hi)])}')
    m5 = o[(o.part == 'bo5') & (o.market == 'ML') & (o.pick == '1')]
    print('\nbo5 исход (П1, модель):', calib(m5))
    g = o[(o.part == 'bo5') & (o.src == 'model')].drop_duplicates(['season', 'tourn', 'games', 'p'])
    gm = m5
    print(f'bo5 геймов в матче: ср. {gm.games.mean():.1f}, доля > 35,5 {(gm.games > 35.5).mean():.1%} (n {len(gm)})')
    del g


if __name__ == '__main__':
    st = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    f = f'{OUT}/ao_bo5.pkl'
    o = pd.read_pickle(f) if os.path.exists(f) and 'fresh' not in sys.argv else run(st)
    report(o)
