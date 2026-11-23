"""ТЗ-007 шаг 3 (готово 17.11, первый вывод — аудит 30.11): разбор теневого портфеля.

Бумажная ставка = каждый кандидат ставки дня с ценой (dpcore.shadow): ставка по ПЕРВОЙ цене (k_first, P решения p_first),
закрытие — последняя справедливая P рынка до начала (p_mkt_last). Итог — daypick.shadow_settle (тот же счёт и правило, что у ⭐).
  ROI   = средний доход на 1 единицу: W → k − 1, L → −1, P → 0 (± стандартная ошибка, z = ROI / ошибка);
  CLV   = k_first × p_mkt_last − 1 — опережаем ли мы рынок (не зависит от удачи, сходится в ~10 раз быстрее ROI);
  ворота R7 (сегмент допускается в ⭐): n ≥ 300, CLV > 0 при t ≥ 2, ROI ≥ 0.
Запуск локально (файлы репозитория, без таблиц): python3 engine/research/shadow_eval.py [мин_n_среза]
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SRC = {'теннис': ROOT / 'data' / 'shadow', 'футбол': ROOT / 'football' / 'data' / 'shadow',
       'хоккей': ROOT / 'hockey' / 'data' / 'shadow'}     # 23.11 аудит: + хоккей (С21б; закрытие — p_last)
GATE_N, GATE_T = 300, 2.0


def load() -> pd.DataFrame:
    parts = []
    for sport, d in SRC.items():
        for f in sorted(d.glob('*.csv')):
            x = pd.read_csv(f, dtype=str, keep_default_na=False)
            x['sport'] = sport
            if 'p_mkt_last' not in x and 'p_last' in x:          # хоккей (С21б): p — Pinnacle (инверсия), закрытие — p_last
                x['p_mkt_last'] = x['p_last']
                x['mkt_src'] = 'pinnacle'
            parts.append(x)
    if not parts:
        return pd.DataFrame()
    df = pd.concat(parts, ignore_index=True)
    for c in ('result', 'settled', 'k_best', 'book_best', 'group', 'market', 'k_1x', 'book', 'mkt_src', 'p_mkt'):
        if c not in df:
            df[c] = ''
    df = df.fillna('')
    for c in ('k', 'k_first', 'p_first', 'p_mkt', 'p_mkt_last', 'ev', 'k_best', 'k_1x'):
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df['result'] = df.result.astype(str).str.strip()
    df['ev_first'] = df.p_first * df.k_first - 1
    st, la = pd.to_datetime(df.start_utc, errors='coerce'), pd.to_datetime(df.last_at, errors='coerce')
    now = pd.Timestamp.now(tz='UTC').tz_localize(None)
    closed = (st <= now) & ((st - la) <= pd.Timedelta(hours=3))       # закрытие = последняя цена не раньше T−3 ч, матч начался
    df['clv'] = (df.k_first * df.p_mkt_last - 1).where(closed)
    df['pnl'] = np.where(df.result == 'W', df.k_first - 1, np.where(df.result == 'L', -1.0, 0.0))
    df['kf'] = pd.cut(df.k_first, [1.0, 1.35, 1.5, 1.7, 2.5, 99], right=False,
                      labels=['<1,35', '1,35–1,50', '1,50–1,70', '1,70–2,50', '≥2,50'])
    df['evb'] = pd.cut(df.ev_first, [-1, -0.03, 0, 0.03, 0.2, 9], right=False,
                       labels=['< −3%', '−3…0%', '0…3%', '3…20%', '> 20% (ошибка?)'])
    return df


def stats(x: pd.DataFrame) -> dict:
    s = x[x.result.isin(['W', 'L', 'P'])]
    n = len(s)
    roi = s.pnl.mean() if n else np.nan
    se = s.pnl.std(ddof=1) / np.sqrt(n) if n > 1 else np.nan
    c = x.clv.dropna()
    cm = c.mean() if len(c) else np.nan
    cse = c.std(ddof=1) / np.sqrt(len(c)) if len(c) > 1 else np.nan
    return dict(n_all=len(x), n=n, win=(s.result == 'W').mean() if n else np.nan, p=s.p_first.mean() if n else np.nan,
                roi=roi, roi_se=se, z=roi / se if se and se == se and se > 0 else np.nan,
                n_clv=len(c), clv=cm, clv_se=cse, t=cm / cse if cse and cse == cse and cse > 0 else np.nan)


def gate(st: dict) -> str:
    if st['n'] < GATE_N:
        return f'ждём (n {st["n"]}/{GATE_N})'
    ok = st['clv'] > 0 and st['t'] >= GATE_T and st['roi'] >= 0
    return '✅ ворота пройдены' if ok else '❌ не проходит'


def pct(v, se=None) -> str:
    if v != v:
        return '—'
    return f'{v * 100:+.1f}%' + (f' ±{se * 100:.1f}' if se is not None and se == se else '')


def main(min_n: int = 1) -> None:
    df = load()
    if df.empty:
        print('теневой портфель пуст')
        return
    print(f'Теневой портфель: строк {len(df)}, с итогом {df.result.isin(["W", "L", "P"]).sum()}, с ценой закрытия {df.clv.notna().sum()}')
    print(f'Период: {df.date.min()} … {df.date.max()}\n')
    head = f'{"срез":<44} {"n":>5} {"угад":>6} {"P":>6} {"ROI ± ош.":>15} {"z":>5} {"CLV ± ош.":>15} {"t":>5}  ворота R7'
    for title, keys in (('вид', ['sport']), ('вид × рынок', ['sport', 'market']), ('вид × кф', ['sport', 'kf']),
                        ('вид × EV при ставке', ['sport', 'evb']), ('вид × группа (value / fallback)', ['sport', 'group']),
                        ('вид × источник справедливой P (аудит 23.11: blend — дальние линии, маржа 10–25%)', ['sport', 'mkt_src']),
                        ('вид · ⭐-зона (P 0,55–0,85, кф 1,35–2,50 — где выбирается ⭐)', 'zone'),
                        ('теннис 💰 сегмент (П1/П2, кф 1,70–2,50, EV 3–20%)', None)):
        print(f'— {title}\n{head}')
        if keys == 'zone':
            z = df[df.p_first.between(0.55, 0.85) & df.k_first.between(1.35, 2.5)]
            groups = [(f'{sp} · ⭐-зона', g) for sp, g in z.groupby('sport')]
        elif keys is None:
            seg = df[(df.sport == 'теннис') & (df.market == 'ML') & (df.kf == '1,70–2,50') & (df.evb == '3…20%')]
            groups = [('💰 теннис', seg)]
        else:
            groups = [(' · '.join(map(str, k if isinstance(k, tuple) else (k,))), g)
                      for k, g in df.groupby(keys, observed=True)]
        for name, g in groups:
            st = stats(g)
            if st['n_all'] < min_n:
                continue
            print(f'{name:<44} {st["n"]:>5} {pct(st["win"]):>6} {pct(st["p"]):>6} {pct(st["roi"], st["roi_se"]):>15} '
                  f'{st["z"]:>5.1f} {pct(st["clv"], st["clv_se"]):>15} {st["t"]:>5.1f}  {gate(st)}'.replace('nan', '  —'))
        print()
    # 19.11 С25а: k — уже лучшая цена; сравнение — с кф 1xBet (k_1x; старые строки до 19.11 — k = 1xBet)
    old = df.first_at.astype(str) < '2026-11-18 22:30'          # до С25а k = кф 1xBet; позже без k_1x — неизвестно
    df['k1x'] = df.k_1x.where(df.k_1x.notna(), df.k.where(old | (df.book == 'onexbet')))
    b = df.dropna(subset=['k_best', 'k1x'])
    if len(b):
        gain = (b.k_best / b.k1x - 1)
        print(f'Лучшая цена из 10 книг против 1xBet: в среднем +{gain.mean() * 100:.1f}% к кф (n {len(b)}), '
              f'лучше у {(gain > 0.001).mean() * 100:.0f}% кандидатов; книги: '
              + ', '.join(f'{k} {v}' for k, v in b.book_best.value_counts().head(5).items()))


if __name__ == '__main__':
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 1)
