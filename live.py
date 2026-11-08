"""Связка живых данных ESPN с моделью: опознание игроков, покрытие турнира, свежая форма между обновлениями tennis-data.

Модель переобучается по tennis-data. Результаты ESPN копятся в data/espn/{tour}.csv и «докатываются» в состояние
игроков (Elo, усталость, отдых, личные встречи, смена покрытия) — прогноз всегда видит вчерашние матчи, даже если
tennis-data давно не обновлялся. Докатываются только матчи новее последней даты tennis-data, поэтому дублей нет.
"""
from __future__ import annotations

import datetime as dt
from functools import lru_cache
from types import SimpleNamespace

import numpy as np
import pandas as pd

import dataset
import names as NM
from espn import EspnMatch

AUTO_MIN_SCORE, AUTO_MIN_GAP = 0.92, 0.03     # опознание без участия человека: почти точное совпадение имени


@lru_cache(maxsize=4)
def _history(tour: str) -> pd.DataFrame:
    td = dataset.tennis_data(tour)
    td['Surface'] = td.Surface.replace({'Carpet': 'Hard'})
    lvl = td['Series' if 'Series' in td else 'Tier'].astype(str)
    return td.assign(Level=lvl.map(_level))[['Date', 'Location', 'Tournament', 'Surface', 'Level']].copy()


def _level(x: str) -> str:
    """Категория турнира в единой шкале: Grand Slam, 1000, 500, 250, Finals."""
    x = x.strip()
    if x == 'Grand Slam':
        return 'Grand Slam'
    if x in ('Masters Cup', 'Tour Championships'):
        return 'Finals'
    if '1000' in x:
        return '1000'
    if '500' in x or x == 'Premier':
        return '500'
    if x.startswith(('ATP2', 'WTA2')) or x == 'International':
        return '250'
    return x


def _same_season_slot(tour: str, location: str, date: pd.Timestamp) -> pd.Series | None:
    """Самый свежий турнир в этом городе в тот же отрезок сезона (Paris: ноябрь — Masters, май — Roland Garros)."""
    h = _history(tour)
    h = h[h.Location == location]
    if h.empty:
        return None
    md = (h.Date.dt.month - date.month).abs()
    md = np.minimum(md, 12 - md)
    h = h.assign(md=md)
    h = h[h.md == h.md.min()]
    if h.md.iloc[0] > 1:                       # в этом городе в такое время года турнира основного тура не было
        return None
    return h.sort_values('Date').iloc[-1]


def level_for(tour: str, location: str, date: pd.Timestamp, major: bool = False) -> str | None:
    """Категория турнира или None, если это не основной тур (WTA 125, челленджеры, ITF, выставки)."""
    if major:
        return 'Grand Slam'
    r = _same_season_slot(tour, location, date)
    return None if r is None else str(r.Level)


def eligible(tour: str, location: str, date: pd.Timestamp, major: bool = False) -> bool:
    import config as C
    return level_for(tour, location, date, major) in C.LEVELS


def known_location(tour: str, location: str) -> bool:
    """Город встречался в tennis-data (только основной тур)."""
    return bool(location) and location in set(_history(tour).Location)


def surface_for(tour: str, location: str, date: pd.Timestamp) -> str:
    """Покрытие по самому свежему турниру в этом городе в тот же сезонный отрезок."""
    r = _same_season_slot(tour, location, date)
    if r is None:
        h = _history(tour)
        h = h[h.Location == location]
        return str(h.sort_values('Date').Surface.iloc[-1]) if not h.empty else 'Hard'
    return str(r.Surface)


def active_ids(st) -> set:
    last = st.last_date
    return {pid for pid, ds in st.dates.items() if ds and (last - ds[-1]).days <= 540}


_EXACT: dict[int, dict[str, list]] = {}
_CACHE: dict[tuple, tuple] = {}


def _exact_index(players: pd.DataFrame, active: set) -> dict[str, list]:
    """Точный индекс «имя+фамилия» и «фамилия+имя» без пробелов, дефисов и диакритики → id."""
    key = id(players), hash(frozenset(active))
    if key not in _EXACT:
        idx: dict[str, list] = {}
        cand = players[players.player_id.isin(active)]
        for pid, fn, ln in zip(cand.player_id, cand.name_first, cand.name_last):
            for k in {NM._plain(f'{fn}{ln}'), NM._plain(f'{ln}{fn}')}:
                idx.setdefault(k, []).append((pid, f'{fn} {ln}'))
        if len(_EXACT) > 8:
            _EXACT.clear()
        _EXACT[key] = idx
    return _EXACT[key]


def identify(name: str, players: pd.DataFrame, active: set) -> tuple[float | None, str, float]:
    """(player_id или None, имя в базе, сходство). None — опознание неуверенное, матч пропускается.
    Сначала точное совпадение имени (мгновенно), иначе нечёткий поиск с жёстким порогом."""
    ck = (name, id(players), hash(frozenset(active)))
    if ck in _CACHE:
        return _CACHE[ck]
    hit = _exact_index(players, active).get(NM._plain(name), [])
    if len({h[0] for h in hit}) == 1:
        _CACHE[ck] = (hit[0][0], hit[0][1], 1.0)
        return _CACHE[ck]
    _CACHE[ck] = _fuzzy(name, players, active)
    return _CACHE[ck]


def _fuzzy(name: str, players: pd.DataFrame, active: set) -> tuple[float | None, str, float]:
    c = NM.find_latin(name, players, active)
    if not c:
        return None, '', 0.0
    gap = c[0][2] - c[1][2] if len(c) > 1 else 1.0
    ok = c[0][2] >= AUTO_MIN_SCORE and gap >= AUTO_MIN_GAP
    return (c[0][0] if ok else None), c[0][1], c[0][2]


STORE_COLS = ['espn_id', 'date', 'location', 'event', 'round', 'ridx', 'major', 'winner_id', 'loser_id', 'winner', 'loser',
              'score', 'status']


def store_path(tour: str):
    import config as C
    return C.DATA / 'espn' / f'{tour}.csv'


def load_store(tour: str) -> pd.DataFrame:
    f = store_path(tour)
    if not f.exists():
        return pd.DataFrame(columns=STORE_COLS)
    df = pd.read_csv(f, dtype={'espn_id': str})
    df['date'] = pd.to_datetime(df.date)
    return df


def persist(model, matches: list[EspnMatch], players: pd.DataFrame) -> int:
    """Сохранить доигранные матчи основного тура в data/espn/{tour}.csv (накопительно, без дублей).
    Так форма игроков не теряется, даже если tennis-data долго не обновляется."""
    tour = model.tour
    old = load_store(tour)
    have = set(old.espn_id.astype(str))
    act = active_ids(model.state)
    rows = []
    for m in matches:
        if (not m.completed or m.qualifying or m.id in have
                or not eligible(tour, m.location, m.start, m.major)):
            continue
        ids = [identify(nm, players, act) for nm in m.names]
        if any(i[0] is None for i in ids):
            continue
        wi = m.winner
        w, l = ids[wi], ids[1 - wi]
        score = ' '.join(f'{a}-{b}' if wi == 0 else f'{b}-{a}' for a, b in m.sets)      # с позиции победителя
        rows.append(dict(espn_id=m.id, date=m.start.normalize(), location=m.location, event=m.event, round=m.round,
                         ridx=m.ridx, major=int(m.major), winner_id=w[0], loser_id=l[0], winner=w[1], loser=l[1],
                         score=score, status=m.status))
    if rows:
        df = pd.concat([old, pd.DataFrame(rows)], ignore_index=True)
        df['date'] = pd.to_datetime(df.date)
        df = df.sort_values(['date', 'ridx'], kind='stable')
        f = store_path(tour)
        f.parent.mkdir(parents=True, exist_ok=True)
        df.assign(date=df.date.dt.strftime('%Y-%m-%d'))[STORE_COLS].to_csv(f, index=False)
    return len(rows)


def apply_store(model) -> int:
    """Докатить в состояние модели сохранённые матчи ESPN после последней даты tennis-data. Возвращает число матчей."""
    st = model.state
    if not hasattr(st, 'td_last'):
        st.td_last = st.last_date
    if not hasattr(st, 'applied'):
        st.applied = set()
    df = load_store(model.tour)
    df = df[(df.date > st.td_last) & ~df.espn_id.astype(str).isin(st.applied)].sort_values(['date', 'ridx'], kind='stable')
    for r in df.itertuples(index=False):
        sets = {}
        for k, sc in enumerate(str(r.score).split()[:5], 1):          # счёт с позиции победителя: «6-4 3-6 7-6»
            try:
                a_, b_ = sc.split('-')[:2]
                sets[f'W{k}'], sets[f'L{k}'] = float(a_[:2]), float(b_[:2])
            except ValueError:
                pass
        x = SimpleNamespace(winner_id=float(r.winner_id), loser_id=float(r.loser_id),
                            Surface=surface_for(model.tour, r.location, r.date), Date=r.date,
                            w_svpt=np.nan, l_svpt=np.nan, w_1stWon=np.nan, w_2ndWon=np.nan, l_1stWon=np.nan, l_2ndWon=np.nan,
                            WRank=np.nan, LRank=np.nan, ridx=int(r.ridx), season=r.date.year, Location=r.location, **sets)
        st.update(x)
        st.applied.add(str(r.espn_id))
    return len(df)


def refresh(model, matches: list[EspnMatch], players: pd.DataFrame) -> int:
    """Сохранить свежие результаты ESPN и докатить в форму всё, что новее tennis-data."""
    persist(model, matches, players)
    return apply_store(model)


def utc_today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()
