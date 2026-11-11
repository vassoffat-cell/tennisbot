"""🏒 Хоккей: данные матчей (НХЛ — открытое табло ESPN; КХЛ — API-Sports Hockey, ключ APISPORTS_KEY).

Матч: id, лига, начало (UTC), хозяева, гости, счёт основного времени (сумма 3 периодов), итог (с ОТ/буллитами),
тип окончания (REG / OT / SO), броски в створ (если есть), статус. Хранилище — hockey/data/games.csv.gz.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
GAMES = DATA / 'games.csv.gz'
COLS = ['id', 'league', 'start', 'home', 'away', 'hg', 'ag', 'hg_reg', 'ag_reg', 'end', 'hs', 'as_', 'state', 'season']
ESPN = 'https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard?dates={d:%Y%m%d}&limit=200'
APIS = 'https://v1.hockey.api-sports.io'
KHL_ID = 35                       # уточняется по /leagues?search=KHL (проверка из облака)


def _get(url: str, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (hockey-model)', **(headers or {})})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode('utf-8'))


def _int(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


def espn_day(d: dt.date) -> list[dict]:
    """Матчи НХЛ даты d (ESPN): счёт по периодам → основное время, ОТ/буллиты; броски — из statistics, если есть."""
    js = _get(ESPN.format(d=d))
    out = []
    for ev in js.get('events', []) or []:
        c = (ev.get('competitions') or [{}])[0]
        cs = c.get('competitors') or []
        if len(cs) != 2:
            continue
        h = next((x for x in cs if x.get('homeAway') == 'home'), cs[0])
        a = next((x for x in cs if x.get('homeAway') == 'away'), cs[1])
        st = (c.get('status') or ev.get('status') or {}).get('type') or {}
        per = int((c.get('status') or {}).get('period') or 0)

        def reg(x):
            ls = [(_int(p.get('value')) or 0) for p in (x.get('linescores') or [])]
            return sum(ls[:3]) if len(ls) >= 3 else None

        def shots(x):
            for s in x.get('statistics') or []:
                if s.get('name') in ('shotsTotal', 'shots', 'shotsOnGoal'):
                    return _int(s.get('displayValue'))
            return None
        name = st.get('name', '')
        end = '' if st.get('state') != 'post' else ('SO' if 'SHOOTOUT' in name.upper() or per >= 5 else 'OT' if per == 4 else 'REG')
        start = pd.Timestamp(c.get('date') or ev.get('date'))
        start = start.tz_convert('UTC').tz_localize(None) if start.tzinfo else start
        season = (ev.get('season') or {}).get('year', '')
        out.append(dict(id=f"nhl:{ev.get('id')}", league='NHL', start=start.strftime('%Y-%m-%d %H:%M'),
                        home=((h.get('team') or {}).get('displayName') or '').strip(),
                        away=((a.get('team') or {}).get('displayName') or '').strip(),
                        hg=_int(h.get('score')), ag=_int(a.get('score')), hg_reg=reg(h), ag_reg=reg(a), end=end,
                        hs=shots(h), as_=shots(a), state=st.get('state', ''), season=season))
    return out


def load() -> pd.DataFrame:
    if GAMES.exists():
        return pd.read_csv(GAMES, dtype={'id': str}).reindex(columns=COLS)
    return pd.DataFrame(columns=COLS)


def save(df: pd.DataFrame) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    df.sort_values('start').drop_duplicates('id', keep='last').to_csv(GAMES, index=False, compression='gzip')


def update_nhl(days: list[dt.date], log: list[str], pause: float = 0.25) -> int:
    df = load()
    new = []
    for d in days:
        try:
            new += espn_day(d)
        except Exception as e:
            log.append(f'НХЛ {d}: {e.__class__.__name__}')
        time.sleep(pause)
    if new:
        df = pd.concat([df, pd.DataFrame(new)], ignore_index=True)
        save(df)
    return len(new)


# ------------------------------------------------------------------ КХЛ (API-Sports)
def _apis(path: str) -> dict:
    key = os.getenv('APISPORTS_KEY', '')
    if not key:
        raise RuntimeError('нет APISPORTS_KEY')
    return _get(APIS + path, {'x-apisports-key': key})


def khl_probe() -> str:
    """Проверка ключа и доступа: id лиги КХЛ, доступные сезоны, остаток запросов."""
    js = _apis('/leagues?search=KHL')
    out = []
    for x in js.get('response', []):
        lg = x.get('league') or x
        seasons = [s.get('season') for s in (x.get('seasons') or [])]
        out.append(f"{lg.get('id')} {lg.get('name')} ({(x.get('country') or {}).get('name')}) сезоны {seasons[-4:]}")
    return '; '.join(out) or f"ответ: {json.dumps(js)[:200]}"


def khl_season(season: int, league: int = KHL_ID) -> list[dict]:
    js = _apis(f'/games?league={league}&season={season}')
    if js.get('errors'):
        raise RuntimeError(str(js['errors'])[:160])
    out = []
    for g in js.get('response', []):
        sc = g.get('scores') or {}
        per = g.get('periods') or {}

        def reg(side):
            v = [per.get(k) for k in ('first', 'second', 'third')]
            try:
                return sum(int(str(x).split('-')[0 if side == 'h' else 1]) for x in v)
            except Exception:
                return None
        stt = ((g.get('status') or {}).get('short') or '')
        end = {'FT': 'REG', 'AOT': 'OT', 'AP': 'SO'}.get(stt, '')
        start = pd.Timestamp(g.get('date')).tz_convert('UTC').tz_localize(None)
        out.append(dict(id=f"khl:{g.get('id')}", league='KHL', start=start.strftime('%Y-%m-%d %H:%M'),
                        home=(g.get('teams') or {}).get('home', {}).get('name', ''),
                        away=(g.get('teams') or {}).get('away', {}).get('name', ''),
                        hg=_int(sc.get('home')), ag=_int(sc.get('away')), hg_reg=reg('h'), ag_reg=reg('a'), end=end,
                        hs=None, as_=None, state='post' if end else ('pre' if stt == 'NS' else 'in'), season=season))
    return out


def collect_history(log: list[str], seasons=(2022, 2023, 2024, 2025, 2026)) -> None:
    """Разово: НХЛ — все игровые дни сезонов (окт.–июнь); КХЛ — по сезонам (если есть ключ и доступ)."""
    today = dt.date.today()
    days = []
    for y in seasons:
        d, end = dt.date(y, 10, 1), min(dt.date(y + 1, 6, 30), today)
        while d <= end:
            days.append(d); d += dt.timedelta(days=1)
    have = set(pd.to_datetime(load().start).dt.date.astype(str)) if len(load()) else set()
    days = [d for d in days if str(d) not in have or d >= today - dt.timedelta(days=3)]
    n = update_nhl(days, log)
    log.append(f'НХЛ: дней {len(days)}, матчей {n}')
    if os.getenv('APISPORTS_KEY'):
        try:
            log.append('КХЛ лиги: ' + khl_probe())
            df, got = load(), []
            for y in seasons:
                try:
                    got += khl_season(y)
                except Exception as e:
                    log.append(f'КХЛ {y}: {e}')
            if got:
                save(pd.concat([df, pd.DataFrame(got)], ignore_index=True))
            log.append(f'КХЛ: матчей {len(got)}')
        except Exception as e:
            log.append(f'КХЛ: {e}')


def khl_probe_dates(log: list[str]) -> None:
    """Проверка: отдаёт ли бесплатный ключ матчи КХЛ текущего сезона по дате (без параметра сезона)."""
    import datetime as _d
    for q in (f'/games?date={_d.date.today() - _d.timedelta(days=1)}&league={KHL_ID}',
              f'/games?date={_d.date.today() - _d.timedelta(days=1)}',
              f'/games?league={KHL_ID}&season=2026&date={_d.date.today() - _d.timedelta(days=1)}',
              '/status'):
        try:
            js = _apis(q)
            rs = js.get('response')
            n = len(rs) if isinstance(rs, list) else rs
            khl = [g for g in (rs if isinstance(rs, list) else []) if (g.get('league') or {}).get('id') == KHL_ID]
            log.append(f"{q} → ошибки {js.get('errors')} · ответов {n if not isinstance(n, dict) else str(n)[:160]} · КХЛ {len(khl)}"
                       + (f" · пример {khl[0]['teams']['home']['name']}–{khl[0]['teams']['away']['name']} {khl[0].get('scores')}" if khl else ''))
        except Exception as e:
            log.append(f'{q} → {e.__class__.__name__}: {e}')


if __name__ == '__main__':
    import sys as _s
    lg: list[str] = []
    if len(_s.argv) > 1 and _s.argv[1] == 'probe':
        khl_probe_dates(lg)
    else:
        collect_history(lg)
    print('\n'.join(lg))
