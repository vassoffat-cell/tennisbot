"""26.11 · Самообучение на теневом портфеле против самообучения на своих ⭐ (футбол 2019–26, без утечки, день за днём).
Кандидаты ⭐ — стороны П1/П2 (кф — лучший по рынку max_*) и ТБ/ТМ 2,5 (кф — средний avg_*), справедливая P — Pinnacle без маржи (пропорц.);
порог как в бою: P ≥ 0,60, кф ≥ 1,35, EV ≥ −3%. Выбор суток — до 3 🎯 по EV, разные матчи. Сегменты selflearn: рынок, сторона, корзина P, лига.
Понижение — как О2: n ≥ N_SEG, z ≤ −1,96 и хуже остальных (zd ≤ −1,64); снятие при z ≥ −1,0. Пониженный сегмент — не в ⭐.
A — без обучения; B — обучение на своих ⭐ (как сейчас); C — обучение на всех кандидатах (теневой портфель).
Запуск: python3 engine/research/sl_shadow.py"""
import warnings; warnings.filterwarnings('ignore')
from pathlib import Path
from collections import defaultdict
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]
N_SEG, Z_DEM, Z_RES, Z_DIFF = 8, -1.96, -1.0, -1.64
WINDOW = 180                                                    # дней истории сегмента (вариант «окно»); None — вся история

def load() -> pd.DataFrame:
    d = pd.read_csv(ROOT / 'football/data/matches.csv.gz')
    d = d.dropna(subset=['ps_h', 'ps_d', 'ps_a', 'hg', 'ag', 'date'])
    q = 1 / d[['ps_h', 'ps_d', 'ps_a']].astype(float).values; P = q / q.sum(1, keepdims=True)
    t = d[['ps_o25', 'ps_u25']].astype(float).values; qt = 1 / t; PT = qt / qt.sum(1, keepdims=True)
    res = np.select([d.hg > d.ag, d.hg == d.ag], [0, 1], 2); tot = (d.hg + d.ag).values
    rows = []
    sides = (('1X2', 'П1', P[:, 0], d.max_h.values, res == 0), ('1X2', 'П2', P[:, 2], d.max_a.values, res == 2),
             ('TOT', 'ТБ', PT[:, 0], d.avg_o25.values, tot > 2.5), ('TOT', 'ТМ', PT[:, 1], d.avg_u25.values, tot < 2.5))
    for mk, sd, p, k, w in sides:
        x = pd.DataFrame(dict(mid=np.arange(len(d)), date=pd.to_datetime(d.date.values), div=d['div'].values, mk=mk, side=sd,
                              p=p, k=k.astype(float), w=w.astype(int)))
        rows.append(x)
    c = pd.concat(rows).dropna(subset=['p', 'k'])
    c['ev'] = c.p * c.k - 1
    c = c[(c.p >= 0.60) & (c.k >= 1.35) & (c.ev >= -0.03) & (c.ev <= 0.20)].copy()
    c['pb'] = pd.cut(c.p, [0.6, 0.7, 0.8, 1.01], right=False, labels=['p60', 'p70', 'p80']).astype(str)
    c['segs'] = list(zip('mk:' + c.mk, 'side:' + c.side, 'pb:' + c.pb, 'lg:' + c['div'].astype(str)))
    return c.sort_values(['date', 'ev'], ascending=[True, False]).reset_index(drop=True)

class Seg:
    """Статистика сегментов по истории (окно WINDOW дней): W, ΣP, ΣP(1−P), n."""
    def __init__(self): self.log = []; self.demoted = set()
    def add(self, day, rows):
        for r in rows: self.log.append((day, r.segs, r.w, r.p))
    def update(self, day):
        lo = day - pd.Timedelta(days=WINDOW) if WINDOW else None
        if lo is not None: self.log = [x for x in self.log if x[0] >= lo]
        S = defaultdict(lambda: [0, 0.0, 0.0, 0]); T = [0, 0.0, 0.0, 0]
        for _, segs, w, p in self.log:
            for s in segs:
                a = S[s]; a[0] += w; a[1] += p; a[2] += p * (1 - p); a[3] += 1
            T[0] += w; T[1] += p; T[2] += p * (1 - p); T[3] += 1
        new = set()
        for s, (w, ps, v, n) in S.items():
            z = (w - ps) / np.sqrt(v) if v > 0 else 0
            ow, op, ov, on = T[0] - w, T[1] - ps, T[2] - v, T[3] - n
            zd = ((w - ps) / n - (ow - op) / on) / np.sqrt(v / n ** 2 + ov / on ** 2) if on > 0 and n > 0 else 0
            if s in self.demoted:
                if z < Z_RES: new.add(s)
            elif n >= N_SEG and z <= Z_DEM and zd <= Z_DIFF:
                new.add(s)
        self.demoted = new

def run(c: pd.DataFrame, mode: str, start='2020-07-01'):
    seg = Seg(); out = []
    for day, g in c.groupby('date', sort=True):
        if mode != 'A': seg.update(day)
        pick, used = [], set()
        for r in g.itertuples():
            if len(pick) >= 3: break
            if r.mid in used: continue
            if mode != 'A' and any(s in seg.demoted for s in r.segs): continue
            pick.append(r); used.add(r.mid)
        if mode == 'B': seg.add(day, pick)                             # учимся на своих ⭐
        elif mode == 'C': seg.add(day, list(g.itertuples()))           # учимся на всех кандидатах (тень)
        if day >= pd.Timestamp(start):
            out += [(day, r.w, r.p, r.k, r.mid) for r in pick]
    o = pd.DataFrame(out, columns=['date', 'w', 'p', 'k', 'mid'])
    o['pnl'] = np.where(o.w == 1, o.k - 1, -1.0)
    return o, seg

def show(name, o):
    n = len(o); w = o.w.sum(); ps = o.p.sum(); z = (w - ps) / np.sqrt((o.p * (1 - o.p)).sum())
    roi = o.pnl.mean(); se = o.pnl.std() / np.sqrt(n)
    print(f'{name}: ⭐ {n}, угадано {w / n:.1%} при обещанных {ps / n:.1%} (z {z:+.2f}); ROI {roi:+.2%} ± {se:.2%}; суток {o.date.nunique()}')
    return o

if __name__ == '__main__':
    c = load()
    print(f'кандидатов {len(c)}, матчей {c.mid.nunique()}, суток {c.date.nunique()} ({c.date.min():%Y-%m} → {c.date.max():%Y-%m}); окно сегмента {WINDOW} дн.')
    res = {}
    for m, nm in (('A', 'A без обучения'), ('B', 'B обучение на своих ⭐'), ('C', 'C обучение на тени')):
        o, seg = run(c, m); res[m] = show(nm, o)
        if m != 'A': print('   понижено к концу:', sorted(seg.demoted) or '—')
    for m in ('B', 'C'):                                               # парное сравнение по суткам: Σ pnl суток m − A
        a = res['A'].groupby('date').pnl.sum(); b = res[m].groupby('date').pnl.sum()
        dlt = (b.reindex(a.index.union(b.index), fill_value=0) - a.reindex(a.index.union(b.index), fill_value=0))
        print(f'{m} − A: Σ {dlt.sum():+.1f} ед. на {len(dlt)} сут., t {dlt.mean() / (dlt.std() / np.sqrt(len(dlt))):+.2f}')
