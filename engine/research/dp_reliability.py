"""20.11.2026 (пользователь: «прогнозы ставки дня должны быть 100%, хоть один мимо — критично»).
История тенниса П1/П2 2019–25 (tennis-data; справедливая P — Pinnacle по Шину, кф — лучшая цена контор MaxW/MaxL):
одна ⭐ в сутки = сторона с максимальной P среди кандидатов с кф ≥ K_MIN. Для порогов P: доля дней со ставкой, угадано,
ROI, самая длинная серия без промаха, вероятность месяца (30 ставок) без единого промаха.
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
import daily_hybrid as DH

H = DH.load((2019, 2020, 2021, 2022, 2023, 2024, 2025))
days = H.day.nunique()
def streak(w):
    best = cur = 0
    for x in w:
        cur = cur + 1 if x else 0; best = max(best, cur)
    return best
print(f'дней с матчами {days}, сторон {len(H)}')
for kmin in (1.01, 1.15, 1.25, 1.35):
    for pmin in (0.60, 0.70, 0.80, 0.85, 0.90, 0.95):
        c = H[(H.kmax >= kmin) & (H.p >= pmin)]
        if not len(c): continue
        top = c.sort_values('p', ascending=False).groupby('day').head(1).sort_values('day')
        w = top.won.values
        pnl = np.where(w == 1, top.kmax - 1, -1.0)
        p30 = float(np.prod(np.clip(top.p.values, 0, 1)) ** (30 / len(top))) if len(top) else 0
        print(f'кф ≥ {kmin:.2f}, P ≥ {pmin:.2f}: дней {len(top):4d} ({len(top)/days*100:3.0f}%), угадано {w.mean()*100:5.1f}% '
              f'(P ср. {top.p.mean()*100:4.1f}%), ROI {pnl.mean()*100:+5.1f}%, кф ср. {top.kmax.mean():.2f}, '
              f'макс. серия {streak(w):3d}, P(30 подряд) {p30*100:4.1f}%')
