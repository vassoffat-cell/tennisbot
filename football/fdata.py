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
SHOTS = {'HST': 'hst', 'AST': 'ast'}          # удары в створ (F2b: разница сил по голам 50% + удары 50%)
BASE = ['div', 'season', 'date', 'time', 'home', 'away', 'hg', 'ag', 'country', 'hst', 'ast']


def _norm(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns={**ODDS, **SHOTS})
    df['date'] = pd.to_datetime(df['Date'], dayfirst=True, errors='coerce') if 'Date' in df else pd.to_datetime(df['date'])
    for c in ['hg', 'ag', 'hst', 'ast'] + list(ODDS.values()):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors='coerce')
    df['home'] = df.home.astype(str).str.strip(); df['away'] = df.away.astype(str).str.strip()
    df['country'] = df['div'].map(COUNTRY)
    cols = BASE + [c for c in ODDS.values() if c in df]
    return df[df.date.notna()][[c for c in cols if c in df]]


def load() -> pd.DataFrame:
    df = pd.read_csv(C.MATCHES, parse_dates=['date'], low_memory=False)
    df['season'] = df.season.astype(str)
    for c in ('hst', 'ast'):
        if c not in df:
            df[c] = np.nan
    return df


class WrongFile(ValueError):
    """Файл не принят: причина для пользователя."""


def _season_now(today: pd.Timestamp | None = None) -> int:
    t = today or pd.Timestamp.now()
    return t.year if t.month >= 7 else t.year - 1


def read_any(raw: bytes) -> dict[str, pd.DataFrame]:
    """Excel (все листы) или CSV — по содержимому, имя файла не важно."""
    try:
        return pd.read_excel(io.BytesIO(raw), sheet_name=None)
    except Exception:
        pass
    for enc in ('utf-8-sig', 'latin-1'):
        try:
            return {'csv': pd.read_csv(io.BytesIO(raw), encoding=enc, low_memory=False)}
        except Exception:
            continue
    raise WrongFile('файл не открывается как таблица Excel или CSV')


def classify(sheets: dict[str, pd.DataFrame]) -> str:
    """'EURO' — основные лиги, 'RUS' — дополнительные лиги с Россией; иначе WrongFile с причиной."""
    first = next(iter(sheets.values()))
    if len(first.columns) and str(first.columns[0]).upper() in ('WTA', 'ATP'):
        raise WrongFile('это файл tennis-data — его нужно отправить теннисному боту')
    if any('HomeTeam' in d and 'FTHG' in d for d in sheets.values()) and any(s in sheets for s in ('E0', 'SP1', 'I1', 'D1', 'F1')):
        return 'EURO'
    if 'HomeTeam' in first and 'Div' in first:
        divs = set(first.Div.astype(str))
        if divs & {'E0', 'SP1', 'I1', 'D1', 'F1'}:
            return 'EURO'
    if 'Country' in first and 'Home' in first:
        raise WrongFile('Россия исключена с 10.11.2026 (данные football-data по ней не обновляются) — этот файл больше не нужен, '
                        'нужен только all-euro-data')
    raise WrongFile('это не файл football-data.co.uk (нет столбцов HomeTeam/FTHG или Country/Home)')


def save_file(raw: bytes, today: pd.Timestamp | None = None) -> tuple[str, str]:
    """Принять файл football-data ТОЛЬКО по содержимому (имя не важно): тип, сезон, свежесть.
    Возвращает (тип 'EURO' | 'RUS', отчёт); неподходящий файл — WrongFile с причиной (база не меняется)."""
    today = (today or pd.Timestamp.now()).normalize()
    sheets = read_any(raw)
    kind = classify(sheets)
    y = _season_now(today)
    base = load()
    rep = []
    if kind == 'EURO':
        if 'csv' in sheets and len(sheets) == 1:                                       # CSV одной лиги — по столбцу Div
            d0 = sheets['csv']; sheets = {str(k): g for k, g in d0.groupby('Div')}
        last = pd.NaT
        parts = []
        for sh, d in sheets.items():
            if 'HomeTeam' not in d:
                continue
            d = d.rename(columns={'HomeTeam': 'home', 'AwayTeam': 'away', 'FTHG': 'hg', 'FTAG': 'ag', 'Div': 'div', 'Time': 'time'})
            dates = pd.to_datetime(d['Date'], dayfirst=True, errors='coerce')
            if dates.notna().sum() == 0:
                continue
            y0 = int(dates.min().year if dates.min().month >= 7 else dates.min().year - 1)
            d['season'] = f'{y0}/{y0 + 1}'
            if 'div' not in d:
                d['div'] = sh
            parts.append((sh, y0, _norm(d)))
            last = dates.max() if pd.isna(last) else max(last, dates.max())
        if not parts:
            raise WrongFile('в файле нет листов с матчами')
        seasons = {y0 for _, y0, _ in parts}
        if y not in seasons:
            raise WrongFile(f'это сезон {min(seasons)}/{min(seasons) + 1}, а нужен текущий {y}/{y + 1}')
        if (today - last.normalize()).days > C.FRESH_DAYS:
            raise WrongFile(f'файл устарел: последний матч {last.date():%d.%m.%Y} — скачайте заново')
        for sh, y0, d in parts:
            base = base[~((base['div'] == d['div'].iloc[0]) & (base.season == f'{y0}/{y0 + 1}'))]
            base = pd.concat([base, d], ignore_index=True)
            rep.append(f'{d["div"].iloc[0]} {y0}/{y0 + 1}: {len(d)}')
        text = f'основные лиги {y}/{y + 1}: ' + ', '.join(rep[:12]) + (' …' if len(rep) > 12 else '') + f'; по {last.date():%d.%m}'
    else:
        d = next(iter(sheets.values()))
        d = d[d.Country.astype(str).str.strip().str.lower() == 'russia']
        if d.empty:
            raise WrongFile('в файле нет России — нужен RUS.xlsx')
        d = d.rename(columns={'Home': 'home', 'Away': 'away', 'HG': 'hg', 'AG': 'ag', 'Time': 'time', 'Season': 'season'})
        d['div'] = 'RUS'; d['season'] = d.season.astype(str)
        d = _norm(d)
        if not d.season.str.startswith(str(y)).any():
            raise WrongFile(f'в файле нет текущего сезона {y}/{y + 1}')
        if (today - d.date.max().normalize()).days > C.FRESH_DAYS:
            raise WrongFile(f'файл устарел: последний матч {d.date.max():%d.%m.%Y} — скачайте заново')
        base = base[~((base['div'] == 'RUS') & base.season.isin(set(d.season)))]
        base = pd.concat([base, d], ignore_index=True)
        text = f'Россия: {len(d)} матчей, по {d.date.max():%d.%m}'
    base = base.sort_values(['date', 'div', 'home'], kind='stable').drop_duplicates(['date', 'home', 'away'], keep='last')
    base.to_csv(C.MATCHES, index=False, compression='gzip')
    return kind, text


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
    if C.SHOTS.exists():                         # T30: удары в створ ESPN (сверка 10.11: 98,8% совпадают с football-data, r = 0,999)
        sh = pd.read_csv(C.SHOTS, dtype={'event': str}).drop_duplicates('event', keep='last').set_index('event')
        ids = e.id.astype(str).values
        add['hst'] = [sh.hst.get(i, np.nan) for i in ids]
        add['ast'] = [sh.ast.get(i, np.nan) for i in ids]
    return pd.concat([df, add], ignore_index=True).sort_values('date', kind='stable')
