"""17.11 (пользователь: «объедини строгий и мягкий — польза, чтобы ставка дня была каждый день»).
История тенниса П1/П2 (tennis-data; справедливая P = Pinnacle по Шину, «1xBet» ≈ Bet365, лучшая цена = MaxW/MaxL):
ОДНА ⭐ в сутки (по дате матча) разными правилами выбора — n дней, угадано, ROI ± ошибка, z; отдельно 2019–22 и 2023–25.
Запуск: python3 engine/research/daily_hybrid.py
"""
from __future__ import annotations

import glob
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import pick as PK  # noqa: E402


def load(years) -> pd.DataFrame:
    fs = [f for y in years for f in glob.glob(str(ROOT / f'data/tennis-data/{y}_*.xlsx'))]
    d = pd.concat([pd.read_excel(f) for f in fs], ignore_index=True)
    for c in ('PSW', 'PSL', 'B365W', 'B365L', 'MaxW', 'MaxL'):
        d[c] = pd.to_numeric(d[c], errors='coerce')
    d = d.dropna(subset=['PSW', 'PSL', 'B365W', 'B365L'])
    d = d[d.Comment.astype(str).str.startswith('Compl')]
    rows = []
    for i, r in enumerate(d.itertuples()):
        pw, pl = PK.shin([r.PSW, r.PSL])
        for p, k, mx, won in ((pw, r.B365W, r.MaxW, 1), (pl, r.B365L, r.MaxL, 0)):
            ps = r.PSW if won else r.PSL
            rows.append((pd.Timestamp(r.Date).date(), i, p, float(k), float(mx) if mx == mx else float(k), max(float(k), float(ps)), won))
    H = pd.DataFrame(rows, columns=['day', 'mid', 'p', 'k', 'kmax', 'k2', 'won'])
    H['ev2'] = H.p * H.k2 - 1
    H['kmax'] = H[['k', 'kmax']].max(axis=1)
    H['ev'] = H.p * H.k - 1
    H['evm'] = H.p * H.kmax - 1
    bad = (H.ev > 0.20) | (H.evm > 0.20)                 # как VALUE_EDGE_MAX в живой системе: перевес > 20% — ошибка данных
    H.loc[H.evm > 0.20, 'kmax'] = H.k                    # ошибочная «лучшая» цена → берём 1xBet
    H['evm'] = H.p * H.kmax - 1
    return H[~(H.ev > 0.20)].assign(bad=bad)


def money(H: pd.DataFrame) -> bool:
    return (H.k >= 1.70) & (H.k < 2.50) & (H.ev >= 0.03) & (H.ev <= 0.20)


def rep(name: str, s: pd.DataFrame, days: int, kcol: str = 'k', stake=None) -> None:
    st = np.ones(len(s)) if stake is None else stake
    pnl = np.where(s.won == 1, s[kcol] - 1, -1.0) * st
    turn = st.sum()
    m = pnl.sum() / turn
    se = pnl.std(ddof=1) * np.sqrt(len(pnl)) / turn
    print(f'  {name:<46} дней {len(s):>4}/{days} угадано {s.won.mean() * 100:4.1f}%  ROI {m * 100:+5.1f}% ± {se * 100:3.1f}  '
          f'z {m / se:+5.2f}  итог {pnl.sum():+6.1f} ед.')


def main() -> None:
    for years in ((2019, 2020, 2021, 2022), (2023, 2024, 2025)):
        H = load(years)
        days = H.day.nunique()
        base = H[(H.k >= 1.35) & (H.p >= 0.60)]
        print(f'\n{years[0]}–{years[-1]}: дней с матчами {days}, сторон {len(H)}')
        g = base.sort_values('p').groupby('day').tail(1)
        rep('① мягкий сейчас: max P (кф ≥ 1,35, P ≥ 0,60)', g, days)
        g = base.sort_values('ev').groupby('day').tail(1)
        rep('② max EV среди P ≥ 0,60 (1xBet)', g, days)
        g = base.sort_values('evm').groupby('day').tail(1)
        rep('③ max EV по лучшей цене, ставка по лучшей', g, days, 'kmax')
        mo = H[money(H)].sort_values('ev').groupby('day').tail(1)
        rep('④ только 💰 (строгий)', mo, days)
        rest = base[~base.day.isin(mo.day)].sort_values('ev').groupby('day').tail(1)
        lad = pd.concat([mo.assign(t='m'), rest.assign(t='f')])
        rep('⑤ лестница: 💰, иначе max EV (1 ед.)', lad, days)
        st = np.where(lad.t == 'm', 1.0, 0.25)
        rep('⑥ лестница + ставка: 💰 1 ед., 🎯 0,25 ед.', lad, days, stake=st)
        restm = base[~base.day.isin(mo.day)].sort_values('evm').groupby('day').tail(1)
        lad2 = pd.concat([mo.assign(t='m', kb=mo.k), restm.assign(t='f', kb=restm.kmax)])
        rep('⑦ лестница, 🎯 по лучшей цене (1 ед.)', lad2, days, 'kb')
        rep('⑧ ⑦ + ставка: 💰 1 ед., 🎯 0,25 ед.', lad2, days, 'kb', stake=np.where(lad2.t == 'm', 1.0, 0.25))
        rest2 = base[~base.day.isin(mo.day)].sort_values('ev2').groupby('day').tail(1)
        lad3 = pd.concat([mo.assign(t='m', kb=mo.k), rest2.assign(t='f', kb=rest2.k2)])
        rep('⑨ лестница, 🎯 лучшая из 2 книг (Bet365/Pinnacle)', lad3, days, 'kb')
        rep('⑩ ⑨ + ставка: 💰 1 ед., 🎯 0,25 ед.', lad3, days, 'kb', stake=np.where(lad3.t == 'm', 1.0, 0.25))
        f = rest
        print(f'     в днях без 💰: 🎯 max EV — EV ≥ 0 у {(f.ev >= 0).mean() * 100:.0f}% дней (1xBet), '
              f'по лучшей цене — у {(restm.evm >= 0).mean() * 100:.0f}%; 💰 в {len(mo)} из {days} дней')


if __name__ == '__main__':
    main()
