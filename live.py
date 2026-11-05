"""Связка живых данных ESPN с моделью: опознание игроков, покрытие турнира, свежая форма между обновлениями tennis-data.

Модель пересобирается раз в неделю по tennis-data. Между пересборками результаты ESPN «докатываются» в состояние
игроков в памяти (Elo, усталость, отдых, личные встречи, смена покрытия) — прогноз всегда видит вчерашние матчи.
На диск такое состояние не пишется: при следующей пересборке те же матчи придут из tennis-data без дублей.
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
    return td[['Date', 'Location', 'Tournament', 'Surface']].copy()


def known_location(tour: str, location: str) -> bool:
    """Турнир основного тура: город встречался в tennis-data (там только основной тур, без квалификаций и 125/челленджеров)."""
    return bool(location) and location in set(_history(tour).Location)


def surface_for(tour: str, location: str, date: pd.Timestamp) -> str:
    """Покрытие по самому свежему турниру в этом городе в тот же сезонный отрезок (Paris: ноябрь — хард, май — грунт)."""
    h = _history(tour)
    h = h[h.Location == location]
    if h.empty:
        return 'Hard'
    md = (h.Date.dt.month - date.month).abs()
    md = np.minimum(md, 12 - md)
    h = h.assign(md=md)
    h = h[h.md == h.md.min()]
    return str(h.sort_values('Date').Surface.iloc[-1])


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


def refresh(model, matches: list[EspnMatch], players: pd.DataFrame) -> int:
    """Докатить в состояние модели доигранные матчи ESPN после последней даты tennis-data. Возвращает число матчей."""
    st = model.state
    if not hasattr(st, 'td_last'):
        st.td_last = st.last_date
    if not hasattr(st, 'applied'):
        st.applied = set()
    act = active_ids(st)
    n = 0
    for m in sorted(matches, key=lambda x: x.start):
        if (not m.completed or m.qualifying or m.id in st.applied or m.start.normalize() <= st.td_last
                or not known_location(model.tour, m.location)):
            continue
        ids = [identify(nm, players, act)[0] for nm in m.names]
        if None in ids:
            continue
        w, l = (ids[0], ids[1]) if m.winner == 0 else (ids[1], ids[0])
        date = m.start.normalize()
        r = SimpleNamespace(winner_id=w, loser_id=l, Surface=surface_for(model.tour, m.location, date), Date=date,
                            w_svpt=np.nan, l_svpt=np.nan, w_1stWon=np.nan, w_2ndWon=np.nan, l_1stWon=np.nan, l_2ndWon=np.nan,
                            WRank=np.nan, LRank=np.nan, ridx=m.ridx, season=date.year, Location=m.location)
        st.update(r)
        st.applied.add(m.id)
        n += 1
    return n


def utc_today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()
