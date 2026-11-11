import pandas as pd, numpy as np, sys, os
for tour in ('wta','atp'):
    import glob
    fs=glob.glob(f'out/{tour}_fixed_total*.pkl')
    if not fs: print(tour,'нет файла'); continue
    r=pd.concat([pd.read_pickle(f) for f in fs])
    for bo in (3,5):
        g=r[r.bo==bo]
        if len(g)<50: continue
        print(f'== {tour.upper()} bo{bo} n={len(g)} медиана факта {g.total.median()} средний {g.total.mean():.1f}')
        for c in [c for c in g.columns if c.startswith('p') and g[c].notna().all()]:
            L=float(c[1:]); po=g[c].values; over=g.total.values>L
            side=po>=0.5; w=(side==over); p=np.where(side,po,1-po)
            z=(w.sum()-p.sum())/np.sqrt((p*(1-p)).sum()); conf=p>=0.60
            print(f'  L={L}: факт Б {over.mean()*100:4.1f}% | мод. Б {side.mean()*100:4.1f}% | угад. {w.mean()*100:4.1f}% при {p.mean()*100:4.1f}% (z {z:+.1f}) | P≥60%: {conf.mean()*100:3.0f}% м., угад. {w[conf].mean()*100 if conf.any() else 0:4.1f}%')
