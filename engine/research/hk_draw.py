"""18.11.2026 · НХЛ: «Х» основного времени у 1xBet — есть ли плюс (наблюдение IMPROVE 17.11: EV +4…+5% по модели).

Без модели, только факты и рынок:
  1. История НХЛ (games.csv.gz + кф в протоколах box.csv.gz): доля ничьих основного времени по «близости» матча
     (справедливая P победы хозяев с ОТ из 2-исходной линии; |P − 0,5|) и по линии тотала.
  2. Снимки цен 1xBet (data/odds/hockey_hist_*.jsonl.gz): для каждого матча справедливая P хозяев и тотал — из Pinnacle,
     частота ничьих похожих матчей истории → EV ставки «Х» по кф 1xBet = p × k − 1. Сравнение с P модели не нужно.
  3. Итоги уже сыгранных матчей снимков (сколько «Х» сыграло).
Запуск локально: python3 engine/research/hk_draw.py
"""
from __future__ import annotations

import gzip
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
G = ROOT / 'hockey' / 'data' / 'games.csv.gz'
BOX = ROOT / 'hockey' / 'data' / 'box.csv.gz'
HIST = sorted((ROOT / 'data' / 'odds').glob('hockey_hist_*.jsonl.gz'))


def _imp(ml: float) -> float:
    return 100 / (ml + 100) if ml > 0 else -ml / (-ml + 100)


def history() -> pd.DataFrame:
    g = pd.read_csv(G)
    g = g[(g.league == 'NHL') & g.end.isin(['REG', 'OT', 'SO'])].copy()
    b = pd.read_csv(BOX, usecols=['id', 'odds'])
    b = b[b.odds.notna() & (b.odds != '')]
    rows = []
    for r in b.itertuples(index=False):
        try:
            o = json.loads(r.odds)
            h, a = _imp(float(o['homeTeamOdds']['moneyLine'])), _imp(float(o['awayTeamOdds']['moneyLine']))
        except Exception:
            continue
        s = h + a
        if s < 1.0:                                   # 3-исходная линия (осн. время) — другая постановка, пропуск
            continue
        rows.append(dict(id=r.id, ph=h / s, tot=o.get('overUnder')))
    o = pd.DataFrame(rows)
    d = g.merge(o, on='id')
    d['draw'] = (d.hg_reg == d.ag_reg).astype(int)
    d['close'] = (d.ph - 0.5).abs()
    return d


def rate(d: pd.DataFrame, close: float, tot: float | None) -> tuple[float, int]:
    """Частота ничьих у похожих матчей: |P − 0,5| ± 0,05 и тот же тотал (если есть ≥ 150 таких), сжатие к общей доле."""
    m = d[(d.close - close).abs() <= 0.05]
    if tot is not None and tot == tot:
        mt = m[m.tot == tot]
        if len(mt) >= 150:
            m = mt
    base = d.draw.mean()
    n = len(m)
    return (m.draw.sum() + 100 * base) / (n + 100), n


def snaps() -> pd.DataFrame:
    rows = []
    for f in HIST:
        for ln in gzip.open(f, 'rt', encoding='utf-8'):
            x = json.loads(ln)
            for e in x.get('events', []):
                bk = e.get('books', {})
                one, pin = bk.get('onexbet', {}), bk.get('pinnacle', {})
                dr = [p for n, _, p in one.get('h2h', []) if n == 'Draw']
                ph = {n: p for n, _, p in pin.get('h2h', [])}
                if not dr or e['home'] not in ph or e['away'] not in ph:
                    continue
                ih, ia = 1 / ph[e['home']], 1 / ph[e['away']]
                tot = [L for n, L, _ in pin.get('totals', []) if n == 'Over']
                rows.append(dict(ts=x['ts'], start=e['start'], home=e['home'], away=e['away'], kx=dr[0],
                                 ph=ih / (ih + ia), tot=tot[0] if tot else np.nan))
    s = pd.DataFrame(rows)
    if not len(s):
        return s
    s['start'] = pd.to_datetime(s.start).dt.tz_localize(None)
    s = s.sort_values('ts').groupby(['home', 'start']).tail(1)     # последний снимок до начала (ближе к закрытию)
    return s


def main() -> None:
    d = history()
    print(f'История НХЛ с 2-исходной линией: n {len(d)}, ничьих осн. времени {d.draw.mean():.1%}')
    for lo, hi in ((0, .05), (.05, .10), (.10, .15), (.15, .20), (.20, .35)):
        m = d[(d.close >= lo) & (d.close < hi)]
        print(f'  |P−0,5| {lo:.2f}–{hi:.2f}: n {len(m):4d}, ничьих {m.draw.mean():.1%}')
    for t in (5.5, 6.0, 6.5):
        m = d[d.tot == t]
        print(f'  тотал {t}: n {len(m):4d}, ничьих {m.draw.mean():.1%}')
    s = snaps()
    if not len(s):
        print('снимков нет'); return
    g = pd.read_csv(G)
    g = g[g.league == 'NHL']
    g['start_ts'] = pd.to_datetime(g.start)
    out = []
    for r in s.itertuples(index=False):
        p, n = rate(d, abs(r.ph - 0.5), r.tot)
        hit = g[(abs((g.start_ts - r.start).dt.total_seconds()) < 4 * 3600)
                & g.home.str.lower().str.contains(r.home.split()[-1].lower().replace('é', 'e'))]
        res = ''
        if len(hit) and str(hit.end.iloc[0]) in ('REG', 'OT', 'SO'):
            res = 'W' if hit.hg_reg.iloc[0] == hit.ag_reg.iloc[0] else 'L'
        out.append(dict(match=f'{r.home} — {r.away}', kx=r.kx, imp=1 / r.kx, p=p, ev=p * r.kx - 1, res=res))
    o = pd.DataFrame(out)
    print(f'\nСнимки 1xBet «Х» (последний до начала): матчей {len(o)}, кф ср. {o.kx.mean():.2f} (мин {o.kx.min():.2f}, '
          f'макс {o.kx.max():.2f}); безубыточная P ср. {o.imp.mean():.1%}')
    print(f'  частота ничьих похожих матчей истории ср. {o.p.mean():.1%} → EV «Х» ср. {o.ev.mean():+.1%}, '
          f'EV > 0 у {(o.ev > 0).sum()} из {len(o)}')
    st = o[o.res != '']
    if len(st):
        w = (st.res == 'W').sum()
        roi = ((st.res == 'W') * st.kx).sum() / len(st) - 1
        se = math.sqrt(st.p.mean() * (1 - st.p.mean()) / len(st))
        print(f'  сыграно {len(st)}: ничьих {w} ({w / len(st):.0%} при ожидании {st.p.mean():.0%} ± {se:.0%}), ROI ставки «Х» {roi:+.0%}')


if __name__ == '__main__':
    main()
