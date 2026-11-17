"""17.11 (пользователь: «откажемся от скриншотов»): по каким правилам букмекеры считают тоталы и форы НХЛ — основное время или с ОТ —
БЕЗ скриншота, по ценам истории (data/odds/hockey_hist_*.jsonl.gz). Справедливые λ — из Pinnacle (победа с ОТ + тотал, как A31).
Для каждой пары «больше/меньше» и «фора ±» книги: P без маржи (Шин) против двух гипотез модели: (а) с ОТ, (б) основное время.
Ближе — та гипотеза, по которой книга считает рынок. Контроль — сам Pinnacle (должен быть «с ОТ»).
Запуск: python3 engine/research/hk_book_rules.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'engine' / 'research'))
from a31_hk_value import snapshots, fair_lambdas  # noqa: E402
from hockey import hmodel as HM  # noqa: E402
import pick as PK  # noqa: E402


def main() -> None:
    import pandas as pd
    from hockey import hdata as HD
    games = HD.load()
    M = HM.fit(games, 'NHL', pd.Timestamp('2026-11-01'))     # как A31: модель только для формы матрицы счёта, λ — из Pinnacle
    res: dict[tuple, list] = {}
    for s in snapshots():
        lam = fair_lambdas(M, s)
        if lam is None:
            continue
        P = M.matrix(*lam); q = M.ot_home(*lam)
        i, j = np.indices(P.shape)
        for book, mk in s['books'].items():
            pairs: dict[tuple, dict] = {}
            for m in ('totals', 'spreads'):
                for n, pt, k in mk.get(m, []):
                    if pt is None:
                        continue
                    key = (m, abs(float(pt))) if m == 'totals' else (m, float(pt) if n == s['home'] else -float(pt))
                    pairs.setdefault(key, {})[n] = k
            for (m, pt), v in pairs.items():
                if len(v) != 2:
                    continue
                if m == 'totals':
                    if 'Over' not in v or float(pt).is_integer():
                        continue
                    p_book = PK.shin([v['Over'], v['Under']])[0]
                    p_ot = HM.over_final(P, pt)
                    p_reg = float(P[(i + j) > pt].sum())
                else:                                   # фора хозяев pt (полушаговая): P(разница + pt > 0)
                    if float(pt).is_integer() or s['home'] not in v or s['away'] not in v:
                        continue
                    p_book = PK.shin([v[s['home']], v[s['away']]])[0]
                    p_reg = float(P[(i - j) + pt > 0].sum())
                    d = (i - j)
                    p_ot = float(P[(d != 0) & (d + pt > 0)].sum() + np.trace(P) * (q if 1 + pt > 0 else 0)
                                 + np.trace(P) * ((1 - q) if -1 + pt > 0 else 0))
                res.setdefault((book, m), []).append((p_book, p_ot, p_reg))
    print(f'{"книга":<14}{"рынок":<9}{"n":>5}  {"|ош.| с ОТ":>11} {"|ош.| осн.":>11}  вывод')
    for (book, m), xs in sorted(res.items()):
        a = np.array(xs)
        e_ot, e_reg = np.abs(a[:, 0] - a[:, 1]).mean(), np.abs(a[:, 0] - a[:, 2]).mean()
        verdict = 'с ОТ' if e_ot < e_reg * 0.7 else 'основное время' if e_reg < e_ot * 0.7 else 'неясно'
        print(f'{book:<14}{m:<9}{len(a):>5}  {e_ot * 100:>10.1f}% {e_reg * 100:>10.1f}%  {verdict}')


if __name__ == '__main__':
    main()
