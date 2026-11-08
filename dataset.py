"""Единая база матчей: tennis-data.co.uk (результаты, счёт по сетам, рейтинг, котировки) + связка с Sackmann
(ID игроков, статистика подачи, возраст). Котировки в модель НЕ подаются — они нужны только для проверок.
T29 (пакет №3): после последней даты Sackmann (2026: обрыв 25.05) — продолжение из TennisMyLife (data/tml/, тот же формат);
игроки TennisMyLife связываются с ID Sackmann по полному имени, новые получают постоянный ID 9xxxxxx (по имени).
"""
from __future__ import annotations

import re
import unicodedata
import zlib

import numpy as np
import pandas as pd

import config as C

norm = lambda s: re.sub(r'[^a-z]', '', unicodedata.normalize('NFKD', str(s).lower()).encode('ascii', 'ignore').decode())
ROUND_IDX = {'1st Round': 1, '2nd Round': 2, '3rd Round': 3, '4th Round': 4, 'Round Robin': 4,
             'Quarterfinals': 5, 'Semifinals': 6, 'The Final': 7}
ALIASES = {'Friedsam A-L.': 204431, 'Mediorreal A.V.': 260848, 'Barrios M.': 144642,
           'Bu Y.': 207352, 'Marcondes I.': 127108}
PATTERNS = (r"^(.*?)\s+([A-Z][A-Za-z]*)[.\-]?(?:[A-Z.\-]*)$",     # «Badosa Gibert P.», «Choi J-H.»
            r"^(.*?)\s+((?:[A-Z][a-z]?\.\s?)+)$",                   # «Varillas J. P.»
            r"^(.*?)\s+(\S+)$")                                     # «Aragone JC»


TML_FILES = {'atp': ('{y}.csv', 'ongoing_tourneys.csv'), 'wta': ('{y}_wta.csv', 'wta_ongoing_tourneys.csv')}
NEW_ID = 9_000_000


def _full(first, last) -> str:
    return norm(f'{first or ""}{last or ""}')


def _new_id(name: str) -> int:
    """Постоянный ID нового игрока (нет в Sackmann): по нормализованному имени."""
    return NEW_ID + zlib.crc32(norm(name).encode()) % 1_000_000


def _sackmann_players(tour: str) -> pd.DataFrame:
    return pd.read_csv(C.SACKMANN / f'{tour}_players.csv', low_memory=False)


def tml_raw(tour: str) -> pd.DataFrame:
    """TennisMyLife: сезоны с FIRST_SEASON + текущие турниры; дубли (турнир, номер матча) — последняя версия."""
    season, ongoing = TML_FILES[tour]
    files = [C.DATA / 'tml' / season.format(y=y) for y in range(C.FIRST_SEASON, C.LAST_SEASON + 1)] + [C.DATA / 'tml' / ongoing]
    parts = [pd.read_csv(f, low_memory=False) for f in files if f.exists()]
    if not parts or not C.USE_TML:
        return pd.DataFrame()
    t = pd.concat(parts, ignore_index=True).drop_duplicates(['tourney_id', 'match_num'], keep='last')
    t['tdate'] = pd.to_datetime(t.tourney_date.astype(str).str[:8], format='%Y%m%d', errors='coerce')
    return t[t.tdate.notna()].reset_index(drop=True)


def _tml_ids(tour: str, t: pd.DataFrame) -> dict[str, int]:
    """Полное имя TennisMyLife → ID Sackmann (при однофамильцах — у кого позже последний матч в Sackmann), иначе новый ID."""
    pl = _sackmann_players(tour)
    pl['full'] = [_full(a, b) for a, b in zip(pl.name_first, pl.name_last)]
    last = {}
    for f in C.SACKMANN.glob(f'{tour}_matches_*.csv'):
        d = pd.read_csv(f, usecols=['winner_id', 'loser_id', 'tourney_date'], low_memory=False)
        for c in ('winner_id', 'loser_id'):
            for pid, dt_ in zip(d[c], d.tourney_date):
                last[pid] = max(last.get(pid, 0), int(dt_))
    best = {}
    for pid, full in zip(pl.player_id, pl.full):
        if full not in best or last.get(pid, 0) > last.get(best[full], 0):
            best[full] = pid
    names = pd.unique(pd.concat([t.winner_name, t.loser_name]).astype(str))
    return {n: best.get(norm(n), _new_id(n)) for n in names}


def players(tour: str) -> pd.DataFrame:
    pl = _sackmann_players(tour)
    t = tml_raw(tour)
    if len(t):                                   # новые игроки TennisMyLife (нет в Sackmann): имя, рука, страна, дата рождения по возрасту
        ids = _tml_ids(tour, t)
        rows = {}
        for side in ('winner', 'loser'):
            for r in t[[f'{side}_name', f'{side}_hand', f'{side}_ioc', f'{side}_age', 'tdate']].itertuples(index=False):
                pid = ids.get(str(r[0]))
                if pid is None or pid < NEW_ID or pid in rows:
                    continue
                first, _, lastn = str(r[0]).partition(' ')
                dob = (r[4] - pd.Timedelta(days=float(r[3]) * 365.25)) if r[3] == r[3] else pd.NaT
                rows[pid] = dict(player_id=pid, name_first=first, name_last=lastn or first, hand=r[1], ioc=r[2],
                                 dob=float(dob.strftime('%Y%m%d')) if not pd.isna(dob) else np.nan)
        if rows:
            pl = pd.concat([pl, pd.DataFrame(rows.values())], ignore_index=True)
    pl['last'] = pl.name_last.map(norm); pl['fi'] = pl.name_first.map(lambda s: norm(s)[:1])
    pl['dob'] = pd.to_datetime(pl.dob.astype('Int64').astype(str), format='%Y%m%d', errors='coerce')
    return pl


def sackmann(tour: str) -> pd.DataFrame:
    files = [C.SACKMANN / f'{tour}_matches_{y}.csv' for y in range(C.FIRST_SEASON, C.LAST_SEASON + 1)]
    sk = pd.concat([pd.read_csv(f, low_memory=False) for f in files if f.exists()])
    sk['tdate'] = pd.to_datetime(sk.tourney_date.astype(str), format='%Y%m%d')
    t = tml_raw(tour)
    if len(t):                                   # T29: продолжение после последней даты Sackmann
        t = t[t.tdate > sk.tdate.max()].copy()
        ids = _tml_ids(tour, t)
        t['winner_id'] = t.winner_name.astype(str).map(ids); t['loser_id'] = t.loser_name.astype(str).map(ids)
        sk = pd.concat([sk, t[[c for c in sk.columns if c in t.columns]]], ignore_index=True)
    return sk.reset_index(drop=True)


def tennis_data(tour: str) -> pd.DataFrame:
    parts = []
    for y in range(C.FIRST_SEASON, C.LAST_SEASON + 1):
        f = C.TENNIS_DATA / f'{y}_{tour.upper()}.xlsx'
        if f.exists():
            parts.append(pd.read_excel(f).assign(season=y))
    td = pd.concat(parts, ignore_index=True)
    td['Date'] = pd.to_datetime(td.Date)
    for c in [c for c in td.columns if re.fullmatch(r'[WL]\d', c)] + ['WRank', 'LRank']:
        td[c] = pd.to_numeric(td[c].replace('\t', np.nan), errors='coerce')
    td['Winner'] = td.Winner.astype(str).str.strip(); td['Loser'] = td.Loser.astype(str).str.strip()
    td['Location'] = td.Location.astype(str).str.strip()
    return td


def _name_map(td: pd.DataFrame, sk: pd.DataFrame, pl: pd.DataFrame) -> dict:
    rk = pd.concat([sk[['winner_id', 'winner_rank']].set_axis(['pid', 'rank'], axis=1),
                    sk[['loser_id', 'loser_rank']].set_axis(['pid', 'rank'], axis=1)])
    rk['season'] = np.concatenate([sk.tdate.dt.year.values] * 2)
    med = rk.groupby(['season', 'pid'])['rank'].median()
    active = rk.groupby('season').pid.agg(set)
    obs = pd.concat([td[['season', 'Winner', 'WRank']].set_axis(['season', 'n', 'r'], axis=1),
                     td[['season', 'Loser', 'LRank']].set_axis(['season', 'n', 'r'], axis=1)]).groupby(['season', 'n'])['r'].median()
    last_season = max(active.index)
    out = {}
    for (season, n), r in obs.items():
        if n in ALIASES:
            out[(season, n)] = ALIASES[n]; continue
        pool = set().union(*[active.get(s, set()) for s in (season - 1, season, season + 1, last_season)])
        cand = pl[pl.player_id.isin(pool)]
        c = []
        for pat in PATTERNS:
            m = re.match(pat, n)
            if not m:
                continue
            last, fi = norm(m.group(1)), m.group(2)[0].lower()
            first_part = norm(re.split(r'[\s\-]', m.group(1))[0])
            for mask in ((cand['last'] == last) & (cand.fi == fi), cand['last'].str.startswith(last) & (cand.fi == fi),
                         (cand['last'] == first_part) & (cand.fi == fi), cand['last'].str.startswith(first_part) & (cand.fi == fi),
                         cand['last'] == last):
                c = cand[mask].player_id.tolist()
                if c:
                    break
            if c:
                break
        if len(c) == 1:
            out[(season, n)] = c[0]
        elif len(c) > 1 and not np.isnan(r):
            out[(season, n)] = min(c, key=lambda p: abs(med.get((season, p), med.get((season - 1, p), 9999)) - r))
    return out


def build(tour: str) -> pd.DataFrame:
    """Хронологическая база матчей тура со связкой на Sackmann."""
    pl, sk, td = players(tour), sackmann(tour), tennis_data(tour)
    mp = _name_map(td, sk, pl)
    td['winner_id'] = [mp.get((s, n)) for s, n in zip(td.season, td.Winner)]
    td['loser_id'] = [mp.get((s, n)) for s, n in zip(td.season, td.Loser)]
    key = sk[['winner_id', 'loser_id', 'tdate']].reset_index().rename(columns={'index': 'sk_idx'})
    m = td.reset_index().rename(columns={'index': 'td_idx'}).merge(key, on=['winner_id', 'loser_id'], how='left')
    lag = (m.Date - m.tdate).dt.days
    m = m[(lag >= -1) & (lag <= 20)].sort_values('tdate').drop_duplicates('td_idx', keep='last')
    td['sk_idx'] = m.set_index('td_idx').sk_idx.reindex(td.index)
    cols = ['w_svpt', 'w_1stWon', 'w_2ndWon', 'l_svpt', 'l_1stWon', 'l_2ndWon', 'minutes']
    sub = sk.loc[td.sk_idx.dropna().astype(int), cols].set_index(td.sk_idx.dropna().index)
    h = td.join(sub)
    h = h[h.winner_id.notna() & h.loser_id.notna()].copy()
    h['ridx'] = h.Round.map(ROUND_IDX).fillna(1)
    h = h.sort_values(['Date', 'ridx'], kind='stable').reset_index(drop=True)
    h['Surface'] = h.Surface.replace({'Carpet': 'Hard'})
    return h
