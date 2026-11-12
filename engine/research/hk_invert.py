"""НХЛ: двумерная инверсия рынка (победа с ОТ + тотал) → λ хозяев/гостей → все линии; сравнение с моделью на истории.
Рынок: кф ESPN pickcenter (DraftKings) без маржи; тотал рынка — с учётом ОТ/буллитов (ничья осн. времени → +1 гол).
Запуск: python3 hk_invert.py"""
import sys, numpy as np, pandas as pd
sys.path.insert(0, '/home/claude/tennisbot')
from hockey import hmodel as HM, hdata as HD
from hk_market import R

games = HD.load()
M = HM.fit(games, 'NHL', pd.Timestamp('2026-11-12'))
I, J = np.indices((HM.MAXG + 1, HM.MAXG + 1))


def stats(lh, la):
    P = M.matrix(lh, la); tie = np.trace(P)
    ml = P[I > J].sum() + tie * M.ot_home(lh, la)
    return P, ml, tie


def p_over_final(P, L):
    """P(итоговый тотал с ОТ > L): основное время > L, либо ничья и (сумма + 1) > L."""
    return float(P[(I + J) > L].sum() + P[(I == J) & ((I + J) <= L) & ((I + J + 1) > L)].sum())


def invert(p_ml, L, p_over):
    def split(s):                                  # при сумме s — доля хозяев под p_ml
        lo, hi = 0.2, 0.8
        for _ in range(30):
            mid = (lo + hi) / 2
            if stats(s * mid, s * (1 - mid))[1] < p_ml: lo = mid
            else: hi = mid
        return (lo + hi) / 2
    lo, hi = 3.0, 9.0
    for _ in range(30):
        s = (lo + hi) / 2; r = split(s)
        if p_over_final(stats(s * r, s * (1 - r))[0], L) < p_over: lo = s
        else: hi = s
    s = (lo + hi) / 2; r = split(s)
    return s * r, s * (1 - r)


if __name__ == '__main__':
    d = R[R.pm.notna() & R.ou.notna() & R.qo.notna() & R.qu.notna()].sort_values('t').reset_index(drop=True)
    d['po_m'] = d.qo / (d.qo + d.qu)
    rows = []
    for r in d.itertuples():
        lh, la = invert(r.pm, float(r.ou), r.po_m)
        Pk = stats(lh, la)[0]; Pm = stats(r.lh, r.la)[0]
        tot = r.hr + r.ar
        rec = dict(t=r.t, lh=lh, la=la, lh0=r.lh, la0=r.la, ou=r.ou)
        for nm, P in (('k', Pk), ('m', Pm)):
            rec[f'o45_{nm}'] = P[(I + J) > 4.5].sum(); rec[f'o55_{nm}'] = P[(I + J) > 5.5].sum()
            rec[f'h25_{nm}'] = P[I > 2.5].sum(); rec[f'a25_{nm}'] = P[J > 2.5].sum()
            rec[f'h1_{nm}'] = P[I > J].sum(); rec[f'x_{nm}'] = np.trace(P)
            rec[f'ah_{nm}'] = P[(I - J + 1.5) > 0].sum()
        rec.update(y45=float(tot > 4.5), y55=float(tot > 5.5), yh25=float(r.hr > 2.5), ya25=float(r.ar > 2.5), yh1=float(r.hr > r.ar),
                   yx=float(r.hr == r.ar), yah=float(r.hr - r.ar + 1.5 > 0))
        rows.append(rec)
    T = pd.DataFrame(rows); T.to_pickle('/home/claude/research/hk/invert.pkl')
    print(f'матчей {len(T)}; λ рынка: сумма {np.mean(T.lh + T.la):.2f} (модель {np.mean(T.lh0 + T.la0):.2f}); линия тотала рынка: {T.ou.value_counts().to_dict()}')
    ll = lambda p, y: -(y * np.log(np.clip(p, 1e-6, 1)) + (1 - y) * np.log(np.clip(1 - p, 1e-6, 1)))
    print(f"{'рынок (линия)':<26}{'факт':>6} {'модель: P / угад. / лл':>26} {'инверсия: P / угад. / лл':>28}   Δлл (z)")
    for lab, y, a in (('ТБ 4,5 осн. время', 'y45', 'o45'), ('ТБ 5,5 осн. время', 'y55', 'o55'), ('ИТ1 Б 2,5', 'yh25', 'h25'), ('ИТ2 Б 2,5', 'ya25', 'a25'),
                      ('П1 осн. время', 'yh1', 'h1'), ('Ничья осн. время', 'yx', 'x'), ('Ф1(+1,5)', 'yah', 'ah')):
        yy = T[y].values; pm, pk = T[a + '_m'].values, T[a + '_k'].values
        dl = ll(pk, yy) - ll(pm, yy)
        print(f"{lab:<26}{yy.mean()*100:5.1f}% {pm.mean()*100:8.1f}% / {((pm >= .5) == (yy == 1)).mean()*100:5.1f}% / {ll(pm, yy).mean():.4f} "
              f"{pk.mean()*100:9.1f}% / {((pk >= .5) == (yy == 1)).mean()*100:5.1f}% / {ll(pk, yy).mean():.4f}   {dl.mean():+.4f} ({dl.mean() / (dl.std(ddof=1) / np.sqrt(len(dl))):+.1f})")
