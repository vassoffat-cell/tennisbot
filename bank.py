"""17.11 ТЗ-007 шаг 3 (банк) и С23 «лестница»: итог ставок дня в ЕДИНИЦАХ банка — общий модуль (теннис, футбол, хоккей).

Ставка: 💰 (ступень 0, перевес над Pinnacle) — 1 ед.; 🎯 и весь хоккей — ¼ ед. (pick.stake). Учёт — с начала лестницы (17.11),
по кф 1xBet ставки (лист day_pick / hk_day_pick, столбец k); итог W → ставка × (кф − 1), L → −ставка, P (возврат) → 0.
ROI = Σ итог / Σ ставок. Строки — для таблицы отчёта ③ (rtable.render: название | кол-во | значение | цвет).
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

START = dt.date(2026, 11, 17)


def _num(x) -> float:
    try:
        return float(str(x).replace(',', '.'))
    except (TypeError, ValueError):
        return float('nan')


def stake(row, sport: str) -> float:
    if sport == 'hockey':
        return 0.25
    t = str(getattr(row, 'tier', '')).strip()
    return 1.0 if t in ('0', '0.0') else 0.25


def frame(dp: pd.DataFrame, sport: str) -> pd.DataFrame:
    """Рассчитанные ставки с кф с начала лестницы: d (дата), st (ставка), pnl (итог в ед.)."""
    if dp is None or not len(dp) or 'k' not in dp.columns:
        return pd.DataFrame(columns=['d', 'st', 'pnl'])
    x = dp.copy()
    x['d'] = pd.to_datetime(x.date, errors='coerce').dt.date
    x['kk'] = x.k.map(_num)
    x = x[(x.d >= START) & x.kk.notna() & (x.kk > 1) & x.result.astype(str).isin(['W', 'L', 'P'])]
    if sport == 'hockey' and 'status' in x.columns:          # хоккей: строки «правка» — заменённые версии, в банк не идут
        x = x[x.status.astype(str).str.strip().isin(['', 'nan'])]
    if not len(x):
        return pd.DataFrame(columns=['d', 'st', 'pnl'])
    x['st'] = [stake(r, sport) for r in x.itertuples(index=False)]
    x['pnl'] = [s * (k - 1) if r == 'W' else (-s if r == 'L' else 0.0) for s, k, r in zip(x.st, x.kk, x.result.astype(str))]
    return x[['d', 'st', 'pnl']]


def _u(v: float) -> str:
    return f'{v:+.2f}'.replace('.', ',').replace('-', '−')


def rows(dp: pd.DataFrame, sport: str, day: dt.date | None, d0: dt.date | None = None) -> list[tuple[str, str, str, str]]:
    """«💼 Банк с 17.11, ед.»: сутки / эта неделя / всего (недельный отчёт — неделя / всего) и ROI всего."""
    f = frame(dp, sport)
    if not len(f):
        return []
    if day is None:
        per = [(' всего', None, None)]
    elif d0 is None:
        mon = day - dt.timedelta(days=day.weekday())
        per = [(' сутки', day, day), (' эта неделя', mon, day), (' всего', None, None)]
    else:
        per = [(' неделя', d0, day), (' всего', None, None)]
    out = [(f'💼 Банк с {START:%d.%m}, ед.', '', '', '')]
    for lab, a, b in per:
        s = f if a is None else f[(f.d >= a) & (f.d <= b)]
        if len(s):
            v = float(s.pnl.sum())
            out.append((lab, str(len(s)), _u(v), '🟢' if v > 0 else '🔴' if v < 0 else ''))
    roi = float(f.pnl.sum() / f.st.sum()) if f.st.sum() else 0.0
    out.append(('  ROI', str(len(f)), f'{roi * 100:+.1f}%'.replace('.', ',').replace('-', '−'), '🟢' if roi > 0 else '🔴' if roi < 0 else ''))
    return out
