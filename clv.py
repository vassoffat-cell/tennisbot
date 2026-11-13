"""📈 CLV — цена закрытия (решение пользователя 14.11.2026: «внедряй»). Общий модуль тенниса, футбола и хоккея.

Зачем: «угадал / не угадал» — шумный сигнал (перевес +3% при кф 1,4 виден только на ~1 800 ставках). CLV — насколько наша цена
лучше цены закрытия рынка — виден на 30–70 ставках. Это главный ранний показатель «в плюс ли мы» и сигнал самообучения.

Цена закрытия (бесплатно, без превышения лимитов — данные уже в репозитории):
  • теннис — tennis-data (data/tennis-data/<год>_<ATP|WTA>.xlsx): Betfair Exchange закрытия (BFEW/BFEL; Pinnacle в файлах
    нет с 02.2026), запасной — средний кф (AvgW/AvgL); рынок «победа» (ML);
  • футбол — football-data (football/data/matches.csv.gz): Betfair Exchange (bf_h/d/a, bf_o25/u25), запасной — средний (avg_*);
    рынки 1X2, двойной шанс, тотал 2,5, азиатская фора (если линия совпала, средний кф);
  • хоккей — DraftKings перед матчем (hockey/data/odds_live.json: последняя цена до начала) → двумерная инверсия A21 →
    P любой линии (ИТ, тотал, фора, победа) из той же модели.
Маржа снимается методом Шина. CLV = кф ставки × P_закр − 1 (есть кф); без кф — ΔP = P_закр − P ставки (рынок пошёл к нам / от нас).
"""
from __future__ import annotations

import math
import re
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
TD = ROOT / 'data' / 'tennis-data'
FD = ROOT / 'football' / 'data' / 'matches.csv.gz'
HK = ROOT / 'hockey' / 'data'


# ───────────────────────────── общее ─────────────────────────────
def shin(prices: list[float]) -> list[float] | None:
    """Вероятности без маржи (Шин) по десятичным кф; None — цены нет."""
    try:
        pr = [float(x) for x in prices]
    except Exception:
        return None
    if any(not (x > 1.0) or x != x for x in pr):
        return None
    pi = np.array([1 / x for x in pr])
    s = pi.sum()
    if s <= 1.0:
        return list(pi / s)
    lo, hi = 0.0, 0.4
    for _ in range(60):
        z = (lo + hi) / 2
        p = (np.sqrt(z * z + 4 * (1 - z) * pi * pi / s) - z) / (2 * (1 - z))
        lo, hi = (z, hi) if p.sum() > 1 else (lo, z)
    p = (np.sqrt(z * z + 4 * (1 - z) * pi * pi / s) - z) / (2 * (1 - z))
    return list(p / p.sum())


def _num(x) -> float:
    try:
        return float(str(x).replace(',', '.'))
    except Exception:
        return float('nan')


def _fold(s: str) -> str:
    s = unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z ]+', ' ', s.replace('-', ' ')).strip()


def result_of(p_close: float, k) -> float:
    kk = _num(k)
    return round(kk * p_close - 1, 4) if kk == kk and kk > 1 and p_close == p_close else float('nan')


# ───────────────────────────── теннис ─────────────────────────────
_TD_CACHE: dict = {}


def _td(year: int) -> pd.DataFrame:
    if year not in _TD_CACHE:
        parts = []
        for tour in ('ATP', 'WTA'):
            f = TD / f'{year}_{tour}.xlsx'
            if f.exists():
                try:
                    x = pd.read_excel(f)
                    parts.append(x.assign(tour=tour))
                except Exception:
                    pass
        d = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        if len(d):
            d['Date'] = pd.to_datetime(d.Date, errors='coerce')
            d['w_sur'], d['w_ini'] = zip(*d.Winner.map(_td_name))
            d['l_sur'], d['l_ini'] = zip(*d.Loser.map(_td_name))
        _TD_CACHE[year] = d
    return _TD_CACHE[year]


def _td_name(s: str) -> tuple[str, str]:
    """«Auger-Aliassime F.» → ('auger aliassime', 'f')."""
    parts = str(s).strip().split()
    if len(parts) >= 2 and parts[-1].rstrip('.').replace('.', '').isalpha() and len(parts[-1].rstrip('.')) <= 3:
        return _fold(' '.join(parts[:-1])), _fold(parts[-1])[:1]
    return _fold(s), ''


def _same(full: str, sur: str, ini: str) -> bool:
    f = _fold(full)
    return bool(sur) and (f.endswith(' ' + sur) or f == sur or (' ' + sur + ' ') in f + ' ') and (not ini or f[:1] == ini)


def tennis_close(p1: str, p2: str, start, side: int) -> float:
    """P_закр игрока side (1 — первый, 2 — второй) по Betfair Exchange (запасной — средний кф); nan — нет матча / цены."""
    t = pd.Timestamp(start)
    d = _td(t.year)
    if not len(d):
        return float('nan')
    q = d[(d.Date >= t.normalize() - pd.Timedelta(days=2)) & (d.Date <= t.normalize() + pd.Timedelta(days=1))]
    for r in q.itertuples(index=False):
        a = _same(p1, r.w_sur, r.w_ini) and _same(p2, r.l_sur, r.l_ini)
        b = _same(p2, r.w_sur, r.w_ini) and _same(p1, r.l_sur, r.l_ini)
        if not (a or b):
            continue
        pr = shin([r.BFEW, r.BFEL]) or shin([r.AvgW, r.AvgL])
        if not pr:
            return float('nan')
        won = (side == 1) == a                             # наш игрок — победитель матча в файле
        return round(pr[0] if won else pr[1], 4)
    return float('nan')


# ───────────────────────────── футбол ─────────────────────────────
_FD_CACHE: list = []
_FN: list = []


def _fnames():
    """football/names.py по пути (в корне есть свой names.py тенниса — не перепутать)."""
    if not _FN:
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location('football_names', ROOT / 'football' / 'names.py')
            mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
            _FN.append(mod)
        except Exception:
            _FN.append(None)
    return _FN[0]


def _fd() -> pd.DataFrame:
    if not _FD_CACHE:
        try:
            f = pd.read_csv(FD)
            f['date'] = pd.to_datetime(f.date, errors='coerce')
            _FD_CACHE.append(f[f.date >= pd.Timestamp.now() - pd.Timedelta(days=400)])
        except Exception:
            _FD_CACHE.append(pd.DataFrame())
    return _FD_CACHE[0]


def football_close(home: str, away: str, start, market: str, pick: str, line) -> float:
    """P_закр линии ставки по Betfair Exchange / среднему кф football-data; nan — нет матча, цены или рынка."""
    d = _fd()
    if not len(d):
        return float('nan')
    t = pd.Timestamp(start)
    q = d[(d.date >= t.normalize() - pd.Timedelta(days=1)) & (d.date <= t.normalize() + pd.Timedelta(days=1))]
    if not len(q):
        return float('nan')
    names = _fnames()                                       # football/names.py — ESPN → football-data
    if names is None:
        return float('nan')
    hn, an = names.match(home, set(q.home)), names.match(away, set(q.away))
    m = q[(q.home == hn) & (q.away == an)] if hn and an else q.iloc[:0]
    if not len(m):
        return float('nan')
    r = m.iloc[0]
    x12 = shin([r.bf_h, r.bf_d, r.bf_a]) or shin([r.avg_h, r.avg_d, r.avg_a])
    L = _num(line)
    if market == '1X2' and x12:
        return round(x12['HDA'.index(pick)], 4) if pick in 'HDA' and len(pick) == 1 else float('nan')
    if market == 'DC' and x12:
        idx = {'1X': (0, 1), 'X2': (1, 2), '12': (0, 2)}.get(pick)
        return round(x12[idx[0]] + x12[idx[1]], 4) if idx else float('nan')
    if market == 'TOT' and L == 2.5:
        ou = shin([r.bf_o25, r.bf_u25]) or shin([r.avg_o25, r.avg_u25])
        return round(ou[0] if pick == 'O' else ou[1], 4) if ou else float('nan')
    if market == 'AH' and L == L:
        ah = _num(r.ah_line)
        mine = L if pick == 'H' else -L                     # линия хозяев в записи football-data
        if ah == ah and abs(ah - mine) < 1e-6:
            pr = shin([r.avg_ahh, r.avg_aha])
            return round(pr[0] if pick == 'H' else pr[1], 4) if pr else float('nan')
    return float('nan')


# ───────────────────────────── хоккей ─────────────────────────────
_HK: dict = {}


def hockey_close(gid: str, league: str, start, market: str, pick: str, line, kc: dict | None = None) -> float:
    """P_закр линии из инверсии последней цены DraftKings до начала матча (odds_live.json); nan — цены нет."""
    import json
    import sys
    if 'odds' not in _HK:
        try:
            _HK['odds'] = json.loads((HK / 'odds_live.json').read_text(encoding='utf-8'))
        except Exception:
            _HK['odds'] = {}
    mk = _HK['odds'].get(str(gid))
    if not mk or not mk.get('p_ml'):
        return float('nan')
    if str(HK.parent) not in sys.path:
        sys.path.append(str(HK.parent))
    if str(ROOT) not in sys.path:
        sys.path.append(str(ROOT))
    from hockey import hdata as HD, hlines as HL, hmodel as HM
    if 'games' not in _HK:
        _HK['games'] = HD.load()
    key = (league, str(pd.Timestamp(start).date()))
    if key not in _HK:
        _HK[key] = HM.fit(_HK['games'], league, pd.Timestamp(start))
    m = _HK[key]
    if not m:
        return float('nan')
    g = _HK['games']
    row = g[g.id.astype(str) == str(gid)]
    if not len(row):
        return float('nan')
    lam0 = m.lambdas(row.home.iloc[0], row.away.iloc[0])
    if not lam0:
        return float('nan')
    lam = HM.invert(m, float(mk['p_ml']), mk.get('line'), mk.get('p_over'), total0=lam0[0] + lam0[1])
    P = m.matrix(*lam); q = m.ot_home(*lam)
    p_raw = line_p(P, q, market, pick, _num(line))
    return round(float(HL.scale(p_raw, (kc or {}).get(market, 1.0))), 4) if p_raw == p_raw else float('nan')


def line_p(P: np.ndarray, q_ot: float, market: str, pick: str, L: float) -> float:
    """P линии хоккея по матрице основного времени (те же определения, что hlines.build / settle; возврат — не в счёт)."""
    i, j = np.indices(P.shape)
    ph, pd_, pa = float(P[i > j].sum()), float(np.trace(P)), float(P[i < j].sum())
    if market == 'ML':
        mh = ph + pd_ * q_ot
        return mh if pick == 'H' else 1 - mh
    if market == '1X2':
        return {'H': ph, 'D': pd_, 'A': pa}.get(pick, float('nan'))
    if market == 'DC':
        return {'1X': ph + pd_, 'X2': pd_ + pa, '12': ph + pa}.get(pick, float('nan'))
    if L != L:
        return float('nan')
    if market == 'AH':
        v = (i - j + L) if pick == 'H' else (j - i + L)
    else:
        g = {'TOT': i + j, 'IT1': i, 'IT2': j}.get(market)
        if g is None:
            return float('nan')
        v = (g - L) if pick == 'O' else (L - g)
    win, lose = float(P[v > 0].sum()), float(P[v < 0].sum())
    return win / (win + lose) if win + lose > 0 else float('nan')


# ───────────────────────────── запись в листы ─────────────────────────────
def _todo(dp: pd.DataFrame, days: int = 45) -> pd.DataFrame:
    if not len(dp) or 'p_close' not in dp.columns:
        return dp.iloc[:0] if len(dp) else dp
    d = dp[dp.result.astype(str).isin(['W', 'L', 'P']) & dp.p_close.astype(str).str.strip().isin(['', 'nan', 'None'])]
    return d[pd.to_datetime(d.date, errors='coerce') >= pd.Timestamp.now() - pd.Timedelta(days=days)]


def _row_key(r) -> str:
    line = r.line if str(r.line).strip() not in ('', 'nan', 'None') else ''
    if line != '':
        try:
            line = f'{float(str(line).replace(",", ".")):g}'
        except Exception:
            pass
    z = lambda v: str(v)[:-2] if str(v).endswith('.0') else str(v)
    return '|'.join([z(r.espn_id), str(r.market), z(r.pick), str(line)])


def update(store, sport: str, kc: dict | None = None, last: dict | None = None) -> str:
    """Дописать p_close и clv рассчитанным ставкам дня (лист day_pick / hk_day_pick). Цена закрытия: файлы (Betfair Exchange) →
    иначе последний снимок рынка до начала (rt['dp_last'], Pinnacle / 1xBet без маржи, ≤ 2 ч до матча). Возвращает строку журнала."""
    sheet = 'hk_day_pick' if sport == 'hockey' else 'day_pick'
    dp = store.read(sheet).astype(object)
    todo = _todo(dp)
    if not len(todo):
        return f'📈 CLV {sport}: новых нет'
    got = 0
    for i, r in todo.iterrows():
        try:
            if sport == 'tennis':
                pc = tennis_close(r.player1, r.player2, r.start_utc, 1 if str(r.pick) in ('1', '1.0') else 2) \
                    if str(r.market) == 'ML' else float('nan')
            elif sport == 'football':
                pc = football_close(r.home, r.away, r.start_utc, str(r.market), str(r.pick), r.line)
            else:
                pc = hockey_close(r.id, r.league, r.start_utc, str(r.market), str(r.pick), r.line, kc)
        except Exception:
            pc = float('nan')
        if pc != pc and last and sport != 'hockey':              # снимок последней цены до начала матча
            rk = str(r.get('rank', '')).strip()
            rk = rk[:-2] if rk.endswith('.0') else (rk or '1')
            s = last.get(f"{str(r.date)[:10]}|{rk if rk not in ('', 'nan') else '1'}")
            if s and s.get('key') == _row_key(r) and s.get('p'):
                pc = float(s['p'])
        if pc == pc:
            dp.loc[i, 'p_close'] = pc
            k = r.get('k', '') if 'k' in dp.columns else ''
            cv = result_of(pc, k)
            dp.loc[i, 'clv'] = cv if cv == cv else ''
            got += 1
    ix = [i for i in todo.index if str(dp.loc[i, 'p_close']).strip() not in ('', 'nan', 'None')]
    if ix:
        store.update(sheet, dp.loc[ix], ['p_close', 'clv'])
    return f'📈 CLV {sport}: цена закрытия найдена {got}/{len(todo)}'


def summary(dp: pd.DataFrame) -> dict:
    """{'n': ставок с CLV, 'clv': средний CLV, 't': t-статистика, 'n_dp': с ΔP, 'dp': средний ΔP (п.п. как доля)}."""
    out = dict(n=0, clv=float('nan'), t=float('nan'), n_dp=0, dp=float('nan'))
    if not len(dp) or 'p_close' not in dp.columns:
        return out
    cv = dp.clv.map(_num).dropna() if 'clv' in dp.columns else pd.Series(dtype=float)
    if len(cv):
        out.update(n=len(cv), clv=float(cv.mean()), t=float(cv.mean() / (cv.std(ddof=1) / math.sqrt(len(cv)))) if len(cv) > 2 and cv.std() > 0 else float('nan'))
    pc, p = dp.p_close.map(_num), dp.p.map(_num)
    ok = pc.notna() & p.notna()
    if ok.any():
        out.update(n_dp=int(ok.sum()), dp=float((pc[ok] - p[ok]).mean()))
    return out


def capper_values(sport: str) -> dict[str, list[float]]:
    """CLV прогнозов каперов (их кф × P_закр − 1) по каналам: футбол — tips_all.csv и tips.csv (одиночные ставки),
    хоккей — hockey/data/tips.csv. Только рассчитанные прогнозы с кф. Теннис — пока нет источников."""
    out: dict[str, list[float]] = {}
    try:
        if sport == 'football':
            parts = []
            for f in ('tips_all.csv', 'tips.csv'):
                p = ROOT / 'football' / 'data' / 'tips' / f
                if p.exists():
                    parts.append(pd.read_csv(p, dtype=str, keep_default_na=False))
            d = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
            if not len(d):
                return out
            d = d[d.result.isin(['W', 'L', 'P']) & ~d.legs.str.contains(r'\|') & (d.legs != '')]
            d = d.drop_duplicates(['src', 'espn_id', 'legs'])
            for r in d.itertuples(index=False):
                mk, pk, L = (str(r.legs).split(':') + ['', '', ''])[:3]
                pc = football_close(r.home, r.away, r.start_utc, mk, pk, L)
                cv = result_of(pc, r.k)
                if cv == cv:
                    out.setdefault(r.src, []).append(cv)
        elif sport == 'hockey':
            p = HK / 'tips.csv'
            d = pd.read_csv(p, dtype=str, keep_default_na=False) if p.exists() else pd.DataFrame()
            if not len(d):
                return out
            d = d[d.result.isin(['W', 'L', 'P']) & (d.game_id != '')]
            for r in d.itertuples(index=False):
                pc = hockey_close(r.game_id, r.league, r.start, r.market, r.pick, r.line)
                cv = result_of(pc, r.k)
                if cv == cv:
                    out.setdefault(r.src, []).append(cv)
    except Exception:
        pass
    return out
