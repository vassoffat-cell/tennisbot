"""A31 шаг 2 (HANDOFF з.1): есть ли в НХЛ цена 1xBet ВЫШЕ справедливой (Pinnacle без маржи) и даёт ли она плюс на итогах и по CLV.

Данные: снимки The Odds API (10 букмекеров) — data/odds/hockey_hist_ГГГГ-ММ.jsonl.gz (каждый платный запрос, с 16.11) и кэш дня
data/odds/hockey_ДАТА.json; итоги — hockey/data/games.csv.gz (ESPN). Без утечки: справедливая P — только из цен снимка.
Справедливая цена: Pinnacle победа с ОТ (2 исхода) + основной тотал Pinnacle → метод Шина → инверсия λ хозяев/гостей (hmodel.invert,
модель счёта с раздутием ничьих и пустыми воротами) → P любого рынка любого букмекера: 1X2 основного времени (3 исхода), победа с ОТ,
фора с ОТ (с возвратом на целых линиях), тотал с ОТ.
EV = P(выигрыш) × кф + P(возврат) − 1. Ставка — по РАННЕМУ снимку матча (утро, как ставка дня); CLV = кф × P_закр − 1, P_закр — по
последнему снимку до начала. Вывод: по порогам EV — n, ROI ± ошибка, z, CLV; калибровка справедливой P (лог-лосс против «50/50»).
Запуск: python3 engine/research/a31_hk_value.py [порог_мин_EV] — локально (данные в репозитории), ≈ 30 с.
"""
from __future__ import annotations

import glob
import gzip
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from hockey import hdata as HD, hmodel as HM          # noqa: E402
import odds as OD                                      # noqa: E402
import pick as PK                                      # noqa: E402



def snapshots() -> list[dict]:
    """Все снимки: [{ts, start, home, away, books: {книга: {рынок: [[исход, линия, кф], …]}}}]."""
    out = []
    for f in sorted(glob.glob(str(ROOT / 'data/odds/hockey_hist_*.jsonl.gz'))):
        with gzip.open(f, 'rt', encoding='utf-8') as fh:
            for ln in fh:
                s = json.loads(ln)
                out += [dict(e, ts=s['ts']) for e in s['events']]
    for f in sorted(glob.glob(str(ROOT / 'data/odds/hockey_2*.json'))):     # кэш дня (до истории — 16.11)
        d = json.loads(Path(f).read_text(encoding='utf-8'))
        ts = d.get('sports', {}).get('icehockey_nhl')
        for e in d.get('events', {}).get('icehockey_nhl', []):
            books = {b['key']: {m['key']: [[o['name'], o.get('point'), o['price']] for o in m['outcomes']] for m in b.get('markets', [])}
                     for b in e.get('bookmakers', [])}
            out.append(dict(id=e['id'], start=e['commence_time'], home=e['home_team'], away=e['away_team'], books=books, ts=ts))
    seen, uniq = set(), []
    for s in out:                                         # один и тот же снимок мог попасть и в кэш, и в историю
        k = (s['id'], str(s['ts'])[:16])
        if k not in seen:
            seen.add(k)
            uniq.append(s)
    return uniq


def fair_lambdas(M, s: dict):
    """λ хозяев/гостей из Pinnacle (Шин): победа с ОТ + основной тотал (пара с кф ближе всего друг к другу)."""
    pin = s['books'].get('pinnacle', {})
    h2h = {n: k for n, _, k in pin.get('h2h', [])}
    if s['home'] not in h2h or s['away'] not in h2h:
        return None
    p_home = PK.shin([h2h[s['home']], h2h[s['away']]])[0]
    tot = {}
    for n, pt, k in pin.get('totals', []):
        tot.setdefault(pt, {})[n] = k
    pairs = [(pt, v) for pt, v in tot.items() if 'Over' in v and 'Under' in v]
    if pairs:
        half = [x for x in pairs if not float(x[0]).is_integer()] or pairs      # целая линия — возврат при равенстве
        pt, v = min(half, key=lambda x: abs(x[1]['Over'] - x[1]['Under']))
        p_over = PK.shin([v['Over'], v['Under']])[0]
        lam = HM.invert(M, p_home, float(pt), p_over)
        if float(pt).is_integer():                     # Шин — P «больше» БЕЗ возврата: P(>L) = p × (1 − P(=L)); 2 прохода
            P = M.matrix(*lam); i, j = np.indices(P.shape)
            push = float(P[((i + j) == pt) & (i != j)].sum() + P[(i == j) & ((i + j + 1) == pt)].sum())
            lam = HM.invert(M, p_home, float(pt), p_over * (1 - push))
        return lam
    return HM.invert(M, p_home, None, None, total0=6.0)


def probs(M, lam, s: dict) -> dict[tuple, tuple[float, float]]:
    """{(книга, рынок, исход, линия): (P выигрыша, P возврата)} для всех цен снимка."""
    P = M.matrix(*lam); q = M.ot_home(*lam)
    i, j = np.indices(P.shape)
    ph, pd_, pa = float(P[i > j].sum()), float(np.trace(P)), float(P[i < j].sum())
    d = (i - j).ravel(); w = P.ravel()                    # итоговая разница с ОТ: ничья осн. времени → ±1
    fm: dict[int, float] = {}
    for dd, ww in zip(d, w):
        if dd == 0:
            fm[1] = fm.get(1, 0) + ww * q
            fm[-1] = fm.get(-1, 0) + ww * (1 - q)
        else:
            fm[int(dd)] = fm.get(int(dd), 0) + ww
    out = {}
    for book, mk in s['books'].items():
        for m, outs in mk.items():
            three = any(n == 'Draw' for n, _, _ in outs)
            for n, pt, k in outs:
                home = n == s['home']
                if m == 'h2h':
                    pw = (ph if home else pa if n != 'Draw' else pd_) if three else ((ph + pd_ * q) if home else (pa + pd_ * (1 - q)))
                    out[(book, m, n, pt)] = (pw, 0.0)
                elif m == 'spreads' and pt is not None:
                    sgn = 1 if home else -1
                    pw = sum(v for x, v in fm.items() if sgn * x + pt > 0)
                    pp = sum(v for x, v in fm.items() if sgn * x + pt == 0)
                    out[(book, m, n, pt)] = (pw, pp)
                elif m == 'totals' and pt is not None:
                    po = HM.over_final(P, float(pt))
                    pe = float(P[((i + j) == pt) & (i != j)].sum() + P[(i == j) & ((i + j + 1) == pt)].sum()) if float(pt).is_integer() else 0.0
                    out[(book, m, n, pt)] = ((po if n == 'Over' else 1 - po - pe), pe)
    return out


def settle(m: str, n: str, pt, g, home_name: str) -> str:
    """W / L / P форы и тотала по итогу с ОТ (ESPN hg/ag); исходы h2h решаются в main (2 или 3 исхода)."""
    hg, ag = float(g.hg), float(g.ag)
    home = n == home_name
    if m == 'spreads':
        x = (hg - ag) if home else (ag - hg)
        v = x + float(pt)
        return 'W' if v > 0 else 'P' if v == 0 else 'L'
    if m == 'totals':
        t = hg + ag
        v = (t - float(pt)) if n == 'Over' else (float(pt) - t)
        return 'W' if v > 0 else 'P' if v == 0 else 'L'
    return ''


def main(t_min: float = 0.0) -> None:
    games = HD.load()
    nhl = games[games.league == 'NHL'].copy()
    nhl['t'] = pd.to_datetime(nhl.start)
    snaps = snapshots()
    M = HM.fit(games, 'NHL', pd.Timestamp('2026-11-01'))
    rows = []
    by_ev: dict[str, list[dict]] = {}
    for s in snaps:
        by_ev.setdefault(s['id'], []).append(s)
    for eid, ss in by_ev.items():
        ss = sorted(ss, key=lambda x: str(x['ts']))
        st = pd.Timestamp(ss[0]['start']).tz_convert(None)
        ss = [x for x in ss if pd.Timestamp(x['ts']).tz_convert(None) < st] if all(x['ts'] for x in ss) else ss
        if not ss:
            continue
        cand = nhl[(nhl.t - st).abs() <= pd.Timedelta(hours=3)]
        g = next((r for r in cand.itertuples() if OD.same_name(r.home, ss[0]['home']) > 0.6 and OD.same_name(r.away, ss[0]['away']) > 0.6), None)
        first, last = ss[0], ss[-1]
        lam0, lam1 = fair_lambdas(M, first), fair_lambdas(M, last)
        if lam0 is None:
            continue
        p0, p1 = probs(M, lam0, first), (probs(M, lam1, last) if lam1 is not None else {})
        done = g is not None and str(g.end) in ('REG', 'OT', 'SO') and g.hg == g.hg
        for (book, m, n, pt), (pw, pp) in p0.items():
            k = next(kk for nn, ptt, kk in first['books'][book][m] if nn == n and ptt == pt)
            ev = pw * k + pp - 1
            res = ''
            if done:
                if m == 'h2h':
                    three = any(x[0] == 'Draw' for x in first['books'][book][m])
                    if n == 'Draw':
                        res = 'W' if g.hg_reg == g.ag_reg else 'L'
                    elif three:
                        h, a = float(g.hg_reg), float(g.ag_reg)
                        res = 'W' if ((h > a) if n == first['home'] else (a > h)) else 'L'
                    else:
                        res = 'W' if ((g.hg > g.ag) if n == first['home'] else (g.ag > g.hg)) else 'L'
                else:
                    res = settle(m, n, pt, g, first['home'])
            c = p1.get((book, m, n, pt))
            clv = (c[0] * k + c[1] - 1) if c and last is not first else np.nan
            rows.append(dict(ev_id=eid, start=st, match=f"{first['home']} — {first['away']}", book=book, market=m, pick=n, line=pt, k=k,
                             p=pw, p_push=pp, ev=ev, res=res, clv=clv, n_snaps=len(ss)))
    R = pd.DataFrame(rows)
    if R.empty:
        print('снимков нет'); return
    R.to_csv('/tmp/a31_rows.csv', index=False)
    n_ev = R.ev_id.nunique(); n_done = R[R.res != ''].ev_id.nunique()
    print(f'A31 · НХЛ: матчей со снимками {n_ev}, с итогом {n_done}; снимков {len(snaps)}; цен {len(R)}; книги: '
          + ', '.join(f'{b} {c}' for b, c in R.book.value_counts().items()))
    x1 = R[R.book == 'onexbet']
    print(f'\n1xBet: цен {len(x1)}, EV ≥ 0 — {(x1.ev >= 0).mean() * 100:.1f}%, средний EV {x1.ev.mean() * 100:+.2f}% (маржа против справедливой цены)')
    print(x1.groupby('market').ev.describe()[['count', 'mean', '50%', 'max']].round(3).to_string())
    for who, D in (('1xBet', x1), ('лучшая цена (любая книга, кроме Pinnacle)', R[R.book != 'pinnacle'])):
        print(f'\n{who}: ставки по раннему снимку при EV ≥ порога')
        print(f"{'порог':>6} {'ставок':>7} {'с итогом':>8} {'ROI':>8} {'±':>6} {'z':>6} {'CLV':>7} {'n CLV':>6}")
        for t in sorted({t_min, 0.0, 0.02, 0.04, 0.06, 0.10}):
            d = D[D.ev >= t]
            if D is not x1:                                   # одна ставка на исход — лучшая цена
                d = d.sort_values('k').groupby(['ev_id', 'market', 'pick', 'line'], dropna=False).tail(1)
            s = d[d.res.isin(['W', 'L', 'P'])]
            pnl = np.where(s.res == 'W', s.k - 1, np.where(s.res == 'P', 0.0, -1.0))
            roi = pnl.mean() if len(s) else np.nan
            se = pnl.std(ddof=1) / np.sqrt(len(s)) if len(s) > 1 else np.nan
            cl = d.clv.dropna()
            print(f'{t * 100:>5.0f}% {len(d):>7} {len(s):>8} {roi * 100 if roi == roi else 0:>+7.1f}% {se * 100 if se == se else 0:>5.1f} '
                  f'{(roi / se) if se and se == se else 0:>6.2f} {cl.mean() * 100 if len(cl) else 0:>+6.1f}% {len(cl):>6}')
    s = R[(R.book == 'pinnacle') & R.res.isin(['W', 'L'])]                     # калибровка справедливой P
    if len(s):
        y = (s.res == 'W').astype(float); p = s.p.clip(1e-4, 1 - 1e-4)
        print(f'\nкалибровка справедливой P (цены Pinnacle, n {len(s)}): лог-лосс {-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean():.4f}, '
              f'угадано {(y == (p >= .5)).mean() * 100:.1f}%, обещано {np.maximum(p, 1 - p).mean() * 100:.1f}%')
    print('\nворота правила «⭐ хоккея по EV»: ≥ 300 ставок с итогом при пороге, ROI и CLV > 0, z ≥ 2 (иначе — копим)')


if __name__ == '__main__':
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 0.0)
