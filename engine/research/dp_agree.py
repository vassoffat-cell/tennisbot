"""21.11 (замечание пользователя: ⭐ футбола 20.11 ТБ2 Атлетик — Эспаньол проиграна; 21.11 ТБ3 Сити — Фулхэм):
у обеих ⭐ рынок (Pinnacle) давал P 0,72 / 0,63, а модель — 0,43 / 0,34. Вопрос: угадываются ли ⭐-кандидаты хуже, когда модель
против рынка? История без утечки: модель переподгоняется каждую неделю по прошлому (как A2), рынок — закрытие Pinnacle без маржи (Шин).
Кандидат ⭐ 🎯: P рынка ≥ 0,60, кф Pinnacle ≥ 1,35 (П1/П2, ТБ/ТМ 2,5). Срезы: модель согласна (P модели ≥ 0,50) / против.
Запуск: daily.yml inputs[research]=engine/research/dp_agree.py (≈ 10–15 мин)."""
import sys, math
import numpy as np, pandas as pd
sys.path.insert(0, 'football')
import config as C, model, dc, fmarket as FM

DIVS = {v[2] for v in C.COMPETITIONS.values() if v[3] == 'league'}
m = pd.read_csv('football/data/matches.csv.gz')
m['date'] = pd.to_datetime(m.date)
m = m.sort_values('date').reset_index(drop=True)
rows = []
for asof in pd.date_range('2024-07-01', m.date.max() + pd.Timedelta(days=1), freq='14D'):
    wk = m[(m.date >= asof) & (m.date < asof + pd.Timedelta(days=14)) & m['div'].isin(DIVS) & m.hg.notna()]
    if wk.empty:
        continue
    w = model.build(m[m.date < asof], None, asof)
    for r in wk.itertuples():
        if r.country not in w.sup or not (r.ps_h > 1 and r.ps_d > 1 and r.ps_a > 1):
            continue
        s = w._domestic(w.sup[r.country], r.div, r.home, r.away, False)
        t = w._domestic(w.tot[r.country], r.div, r.home, r.away, False)
        lam = model._combine(s, t)
        if lam is None:
            continue
        mk = dc.markets(dc.score_matrix(lam[0], lam[1], C.RHO))
        q = FM.shin([r.ps_h, r.ps_d, r.ps_a])
        cand = [('П1', q[0], r.ps_h, mk['h'], r.hg > r.ag), ('П2', q[2], r.ps_a, mk['a'], r.hg < r.ag)]
        if r.ps_o25 > 1 and r.ps_u25 > 1:
            o = FM.shin([r.ps_o25, r.ps_u25])
            cand += [('ТБ2.5', o[0], r.ps_o25, mk['o25'], r.hg + r.ag > 2.5), ('ТМ2.5', o[1], r.ps_u25, 1 - mk['o25'], r.hg + r.ag < 2.5)]
        for lab, pm, k, pmod, win in cand:
            if pm >= 0.60 and k >= 1.35:
                rows.append(dict(date=r.date, lab=lab, p=pm, k=k, pmod=pmod, win=int(win)))
d = pd.DataFrame(rows)
print(f'кандидатов ⭐ 🎯 (P рынка ≥ 0,60, кф ≥ 1,35): {len(d)}, период {d.date.min():%d.%m.%Y}–{d.date.max():%d.%m.%Y}')


def show(name, x):
    n = len(x)
    if n == 0:
        print(f'  {name:34s} n 0'); return
    w, ep = x.win.sum(), x.p.sum()
    z = (w - ep) / math.sqrt((x.p * (1 - x.p)).sum())
    roi = (x.win * x.k - 1).mean()
    print(f'  {name:34s} n {n:5d}  угадано {w / n * 100:5.1f}%  обещано {ep / n * 100:5.1f}%  z {z:+5.2f}  ROI@Pin {roi * 100:+5.1f}%')


show('все', d)
show('модель согласна (P мод ≥ 0,50)', d[d.pmod >= 0.5])
show('модель против (P мод < 0,50)', d[d.pmod < 0.5])
d['gap'] = d.pmod - d.p
for lo, hi in [(-1, -0.20), (-0.20, -0.10), (-0.10, 0), (0, 0.10), (0.10, 1)]:
    show(f'модель − рынок в [{lo:+.2f}; {hi:+.2f})', d[(d.gap >= lo) & (d.gap < hi)])
for lab in ['П1', 'П2', 'ТБ2.5', 'ТМ2.5']:
    x = d[d.lab == lab]
    show(f'{lab}: согласна', x[x.pmod >= 0.5]); show(f'{lab}: против', x[x.pmod < 0.5])
show('строгий: P ≥ 0,70 и модель ≥ 0,60', d[(d.p >= 0.70) & (d.pmod >= 0.60)])
