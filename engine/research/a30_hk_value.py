"""A30 (аудит 16.11): НХЛ — ценность «модель против рынка» на победе с ОТ. Ставка дня НХЛ сейчас берёт P линий из инверсии
рынка (A21) → P ≈ цена рынка, ожидание ≈ −маржа. Проверка без утечки: прогнозы hk_bt.py (подгонка раз в 7 дней на прошлом),
кф DraftKings ESPN (box.csv.gz, с 11.2025). Ставим сторону, где P модели − P рынка (без маржи) ≥ порог; ROI по кф рынка.
Сначала: python3 hk_bt.py NHL 0.0035 2024-10-01."""
import sys
import numpy as np
sys.path.insert(0, '/home/claude/tennisbot/engine/research')
from hk_market import R
d = R[R.pm.notna() & R.qh.notna() & R.qa.notna()].copy()
y = (d.hg > d.ag).astype(float).values
kh, ka = 1 / d.qh.values, 1 / d.qa.values                         # десятичные кф с маржой
for thr in (0.0, 0.02, 0.03, 0.05, 0.08):
    eh = d.mh.values - d.pm.values
    bh, ba = eh >= thr, -eh >= thr
    pnl = np.r_[np.where(y[bh] == 1, kh[bh] - 1, -1), np.where(y[ba] == 0, ka[ba] - 1, -1)]
    n = len(pnl); roi = pnl.mean() if n else np.nan; se = pnl.std(ddof=1) / np.sqrt(n) if n > 1 else np.nan
    print(f'порог {thr:.2f}: ставок {n}, ROI {roi*100:+.1f}% ± {se*100:.1f} (z {roi/se:+.2f}), маржа {d.marg.mean()*100:.1f}%')
fav = np.where(d.pm.values >= .5, np.where(y == 1, kh - 1, -1), np.where(y == 0, ka - 1, -1))
print(f'всегда фаворит рынка: n {len(fav)} ROI {fav.mean()*100:+.1f}%')
