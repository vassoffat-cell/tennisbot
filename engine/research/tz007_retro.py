"""ТЗ-007 шаг 0 — проверка правил ставки дня версии 2 ДО внедрения (без утечки, только цены до матча).

Часть 1 · История тенниса П1/П2 2023–25 (tennis-data, закрытие): справедливая P = Pinnacle (Шин); «1xBet» ≈ Bet365 (как в ТЗ-004),
«лучшая цена» = MaxW/MaxL. Правило R3: EV = P × кф − 1 ≥ порога, по ВСЕМ диапазонам кф (не только 1,70–2,50) — n, ROI ± ошибка, z.
Для сравнения — нынешний выбор «max P при кф ≥ 1,35» (одна ставка на матч).
Часть 2 · Живые сутки 11–17.11 (кэш data/odds/tennis_*.json, итоги ESPN): что выбрали бы R1–R5 и чем кончилось.
Запуск: python3 engine/research/tz007_retro.py
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import pick as PK          # noqa: E402
import odds as OD          # noqa: E402


def roi(pnl: np.ndarray) -> str:
    if not len(pnl):
        return 'n 0'
    m, se = pnl.mean(), pnl.std(ddof=1) / np.sqrt(len(pnl)) if len(pnl) > 1 else np.nan
    return f'n {len(pnl):>5}  ROI {m * 100:+6.1f}% ± {se * 100:4.1f}  z {m / se if se else 0:+5.2f}'


def history() -> None:
    fs = [f for y in (2023, 2024, 2025) for f in glob.glob(str(ROOT / f'data/tennis-data/{y}_*.xlsx'))]
    d = pd.concat([pd.read_excel(f).assign(tour=Path(f).stem.split('_')[1]) for f in fs], ignore_index=True)
    for c in ('PSW', 'PSL', 'B365W', 'B365L', 'MaxW', 'MaxL'):
        d[c] = pd.to_numeric(d[c], errors='coerce')
    d = d.dropna(subset=['PSW', 'PSL', 'B365W', 'B365L'])
    d = d[d.Comment.astype(str).str.startswith('Compl')]
    rows = []
    for r in d.itertuples():
        pw, pl = PK.shin([r.PSW, r.PSL])                     # справедливая P победителя / проигравшего (до матча)
        for p, k, mx, won in ((pw, r.B365W, r.MaxW, 1), (pl, r.B365L, r.MaxL, 0)):
            rows.append((r.tour, p, float(k), float(mx) if mx == mx else np.nan, won))
    H = pd.DataFrame(rows, columns=['tour', 'p', 'k', 'kmax', 'won'])
    H['ev'] = H.p * H.k - 1
    H['ev_max'] = H.p * H.kmax - 1
    print(f'История 2023–25: матчей {len(d)}, сторон {len(H)}')
    print('\nR3: «1xBet»(Bet365) против справедливой Pinnacle, все кф')
    for lo, hi in ((0.03, 0.20), (0.05, 0.20), (0.0, 0.20)):
        s = H[(H.ev >= lo) & (H.ev <= hi)]
        print(f'  EV {lo * 100:.0f}–{hi * 100:.0f}%: {roi(np.where(s.won == 1, s.k - 1, -1.0))}')
        for a, b in ((1.0, 1.35), (1.35, 1.70), (1.70, 2.50), (2.50, 99)):
            ss = s[(s.k >= a) & (s.k < b)]
            print(f'      кф {a:.2f}–{b:.2f}: {roi(np.where(ss.won == 1, ss.k - 1, -1.0))}')
    print('\nлучшая цена рынка (MaxW/MaxL — верхняя граница, не 1xBet):')
    s = H[(H.ev_max >= 0.03) & (H.ev_max <= 0.20)].dropna(subset=['kmax'])
    print(f'  EV 3–20%: {roi(np.where(s.won == 1, s.kmax - 1, -1.0))}')
    m = H[(H.k >= 1.35)].copy()
    m['mid'] = m.index // 2
    top = m.sort_values('p').groupby('mid').tail(1)
    top = top[top.p >= 0.60]
    print(f'\nнынешний выбор «max P, кф ≥ 1,35, P ≥ 0,60» (1 на матч): {roi(np.where(top.won == 1, top.k - 1, -1.0))}')


def live() -> None:
    res = {}
    for t in ('atp', 'wta'):
        e = pd.read_csv(ROOT / f'data/espn/{t}.csv', header=None, dtype=str)
        for r in e.itertuples():
            res[(r[10], r[11])] = r[12]                       # (игрок1, игрок2) → счёт по сетам (первым — победитель)
    print('\nЖивые сутки (кэш цен утра; Pinnacle Шин против 1xBet, П1/П2):')
    for f in sorted(glob.glob(str(ROOT / 'data/odds/tennis_2026-11-1*.json'))):
        dd = json.loads(Path(f).read_text(encoding='utf-8'))
        for evs in dd.get('events', {}).values():
            for ev in evs:
                q = OD.quotes(ev)
                h2 = {n: b for (mk, n, pt), b in q.items() if mk == 'h2h'}
                names = list(h2)
                if len(names) != 2 or not all(h2[n].get('pinnacle') and h2[n].get('onexbet') for n in names):
                    continue
                fair = PK.shin([h2[n]['pinnacle'] for n in names])
                win = next((w for (a, b), w in res.items() if {a, b} == set(names) or OD.same_name(a, names[0]) > .9 and OD.same_name(b, names[1]) > .9
                            or OD.same_name(a, names[1]) > .9 and OD.same_name(b, names[0]) > .9), None)
                for n, p in zip(names, fair):
                    k = h2[n]['onexbet']; ev_ = p * k - 1
                    mark = '💰' if 0.03 <= ev_ <= 0.20 else '  '
                    print(f'  {Path(f).stem[7:]} {mark} {n[:22]:<22} P {p:.3f} кф {k:.2f} EV {ev_ * 100:+5.1f}%')


if __name__ == '__main__':
    history()
    live()
