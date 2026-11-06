"""База матчей: история football-data (data/matches.csv.gz) + доигранные матчи ESPN после последней даты файлов.

Новые файлы football-data (раз в неделю) приходят от пользователя через бота:
  • all-euro-data-YYYY-YYYY.xlsx — все основные лиги сезона (листы E0, SP1, …);
  • RUS.xlsx (или общий файл дополнительных лиг) — Россия.
Строки этого сезона/дивизиона в базе заменяются содержимым файла.
"""
from __future__ import annotations

import io
import re

import numpy as np
import pandas as pd

import config as C

COUNTRY = {'E0': 'ENG', 'E1': 'ENG', 'E2': 'ENG', 'E3': 'ENG', 'EC': 'ENG', 'SC0': 'SCO', 'SC1': 'SCO', 'SC2': 'SCO', 'SC3': 'SCO',
           'D1': 'GER', 'D2': 'GER', 'SP1': 'ESP', 'SP2': 'ESP', 'I1': 'ITA', 'I2': 'ITA', 'F1': 'FRA', 'F2': 'FRA',
           'N1': 'NED', 'B1': 'BEL', 'P1': 'POR', 'T1': 'TUR', 'G1': 'GRE', 'RUS': 'RUS'}
ODDS = {'PSCH': 'ps_h', 'PSCD': 'ps_d', 'PSCA': 'ps_a', 'AvgCH': 'avg_h', 'AvgCD': 'avg_d', 'AvgCA': 'avg_a',
        'MaxCH': 'max_h', 'MaxCD': 'max_d', 'MaxCA': 'max_a', 'BFECH': 'bf_h', 'BFECD': 'bf_d', 'BFECA': 'bf_a',
        'PC>2.5': 'ps_o25', 'PC<2.5': 'ps_u25', 'AvgC>2.5': 'avg_o25', 'AvgC<2.5': 'avg_u25',
        'BFEC>2.5': 'bf_o25', 'BFEC<2.5': 'bf_u25', 'AHCh': 'ah_line', 'PCAHH': 'ps_ahh', 'PCAHA': 'ps_aha',
        'AvgCAHH': 'avg_ahh', 'AvgCAHA': 'avg_aha'}
BASE = ['div', 'season', 'date', 'time', 'home', 'away', 'hg', 'ag', 'country']


def _norm(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns=ODDS)
    df['date'] = pd.to_datetime(df['Date'], dayfirst=True, errors='coerce') if 'Date' in df else pd.to_datetime(df['date'])
    for c in ['hg', 'ag'] + list(ODDS.values()):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors='coerce')
    df['home'] = df.home.astype(str).str.strip(); df['away'] = df.away.astype(str).str.strip()
    df['country'] = df['div'].map(COUNTRY)
    cols = BASE + [c for c in ODDS.values() if c in df]
    return df[df.date.notna()][[c for c in cols if c in df]]


def load() -> pd.DataFrame:
    df = pd.read_csv(C.MATCHES, parse_dates=['date'], low_memory=False)
    df['season'] = df.season.astype(str)
    return df


def save_file(raw: bytes) -> str:
    """Принять файл football-data: определить тип по содержимому, заменить в базе соответствующие строки."""
    sheets = pd.read_excel(io.BytesIO(raw), sheet_name=None)
    base = load()
    rep = []
    if any(s in sheets for s in ('E0', 'SP1', 'I1', 'D1', 'F1')):                     # основные лиги сезона
        for sh, d in sheets.items():
            if 'HomeTeam' not in d:
                continue
            d = d.rename(columns={'HomeTeam': 'home', 'AwayTeam': 'away', 'FTHG': 'hg', 'FTAG': 'ag', 'Div': 'div', 'Time': 'time'})
            dates = pd.to_datetime(d['Date'], dayfirst=True, errors='coerce')
            y0 = int(dates.min().year if dates.min().month >= 7 else dates.min().year - 1)
            d['season'] = f'{y0}/{y0 + 1}'
            d = _norm(d)
            base = base[~((base['div'] == sh) & (base.season == f'{y0}/{y0 + 1}'))]
            base = pd.concat([base, d], ignore_index=True)
            rep.append(f'{sh} {y0}/{y0 + 1}: {len(d)}')
        kind = 'основные лиги'
    else:                                                                              # дополнительные лиги
        d = next(iter(sheets.values()))
        if 'Country' not in d or 'Home' not in d:
            raise ValueError('это не файл football-data.co.uk')
        d = d[d.Country.astype(str).str.strip().str.lower() == 'russia']
        if d.empty:
            raise ValueError('в файле нет России')
        d = d.rename(columns={'Home': 'home', 'Away': 'away', 'HG': 'hg', 'AG': 'ag', 'Time': 'time', 'Season': 'season'})
        d['div'] = 'RUS'; d['season'] = d.season.astype(str)
        d = _norm(d)
        base = base[~((base['div'] == 'RUS') & base.season.isin(set(d.season)))]
        base = pd.concat([base, d], ignore_index=True)
        rep.append(f'RUS: {len(d)} матчей, по {d.date.max().date()}')
        kind = 'Россия'
    base = base.sort_values(['date', 'div', 'home'], kind='stable').drop_duplicates(['date', 'home', 'away'], keep='last')
    base.to_csv(C.MATCHES, index=False, compression='gzip')
    return f'{kind}: ' + ', '.join(rep[:12]) + (' …' if len(rep) > 12 else '')


def training_set(espn: pd.DataFrame | None) -> pd.DataFrame:
    """История + матчи чемпионатов из ESPN, которых ещё нет в файлах (кубки в подгонку не входят — как в проверке)."""
    df = load()
    if espn is None or espn.empty:
        return df
    last = df.groupby('country').date.max()
    e = espn[(espn.status.astype(str) == 'FT') & (espn.kind.astype(str) == 'league')].copy()
    e = e[e.country.notna() & e.home_fd.notna() & e.away_fd.notna()]
    e['date'] = pd.to_datetime(e.date)
    e = e[e.apply(lambda r: r.date > last.get(r.country, pd.Timestamp('1900-01-01')), axis=1)] if len(e) else e
    if e.empty:
        return df
    add = pd.DataFrame(dict(div=e['div'], season='espn', date=e.date, time='', home=e.home_fd, away=e.away_fd,
                            hg=e.hg.astype(float), ag=e.ag.astype(float), country=e.country))
    return pd.concat([df, add], ignore_index=True).sort_values('date', kind='stable')
