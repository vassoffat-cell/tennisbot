"""Единая база матчей: tennis-data.co.uk (результаты, счёт по сетам, рейтинг, котировки) + связка с Sackmann
(ID игроков, статистика подачи, возраст). Котировки в модель НЕ подаются — они нужны только для проверок.
"""
from __future__ import annotations

import re
import unicodedata

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


def players(tour: str) -> pd.DataFrame:
    pl = pd.read_csv(C.SACKMANN / f'{tour}_players.csv', low_memory=False)
    pl['last'] = pl.name_last.map(norm); pl['fi'] = pl.name_first.map(lambda s: norm(s)[:1])
    pl['dob'] = pd.to_datetime(pl.dob.astype('Int64').astype(str), format='%Y%m%d', errors='coerce')
    return pl


def sackmann(tour: str) -> pd.DataFrame:
    files = [C.SACKMANN / f'{tour}_matches_{y}.csv' for y in range(C.FIRST_SEASON, C.LAST_SEASON + 1)]
    sk = pd.concat([pd.read_csv(f, low_memory=False) for f in files if f.exists()])
    sk['tdate'] = pd.to_datetime(sk.tourney_date.astype(str), format='%Y%m%d')
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
