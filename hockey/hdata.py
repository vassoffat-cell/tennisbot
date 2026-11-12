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


RENAME = {'Kunlun': 'Shanghai'}         # «Куньлунь Ред Стар» → «Шанхай Дрэгонс» (2025): один клуб для модели


def _rows(resp: list, league: int = KHL_ID) -> list[dict]:
    """Ответ API-Sports /games → строки базы (только лига league). Основное время — сумма 3 периодов."""
    out = []
    for g in resp or []:
        if (g.get('league') or {}).get('id') != league:
            continue
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
        state = 'post' if end else 'pre' if stt in ('NS', 'TBD') else 'void' if stt in ('POST', 'CANC', 'ABD', 'AWD', 'WO', 'INTR') else 'in'
        start = pd.Timestamp(g.get('date')).tz_convert('UTC').tz_localize(None)
        out.append(dict(id=f"khl:{g.get('id')}", league='KHL', start=start.strftime('%Y-%m-%d %H:%M'),
                        home=RENAME.get(n := (g.get('teams') or {}).get('home', {}).get('name', ''), n),
                        away=RENAME.get(n := (g.get('teams') or {}).get('away', {}).get('name', ''), n),
                        hg=_int(sc.get('home')), ag=_int(sc.get('away')), hg_reg=reg('h') if end else None,
                        ag_reg=reg('a') if end else None, end=end, hs=None, as_=None, state=state,
                        season=(g.get('league') or {}).get('season', '')))
    return out


def khl_season(season: int, league: int = KHL_ID) -> list[dict]:
    js = _apis(f'/games?league={league}&season={season}')
    if js.get('errors'):
        raise RuntimeError(str(js['errors'])[:160])
    return _rows(js.get('response', []), league)


# --- КХЛ текущего сезона: бесплатный план не даёт «сезон 2026», но отдаёт все матчи ДАТЫ (проверка 11.11.2026:
# /games?date=… → 68 матчей мира, из них КХЛ). Собственная база КХЛ копится по датам: догрузка сезона с 1 сентября +
# ежедневно вчера / сегодня / завтра (Москва). Лимит плана — 100 запросов в сутки (UTC) и 10 в минуту.
# Проверка 11.11.2026: догрузка сезона невозможна — бесплатный план отдаёт только окно ±1 день от сегодня
# («Free plans do not have access to this date, try from 2026-11-10…»). База КХЛ копится с 10.11.2026 вперёд.
KHL_SEASON_START = dt.date(2026, 9, 1)
KSTATE = DATA / 'khl_state.json'
DAY_CAP = 90                       # запас 10 запросов в сутки на ручные проверки
PAUSE = 6.5                        # 10 запросов в минуту


def _kstate() -> dict:
    try:
        return json.loads(KSTATE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def khl_date(d: dt.date) -> list[dict]:
    js = _apis(f'/games?date={d}&timezone=Europe/Moscow')
    if js.get('errors'):
        raise RuntimeError(str(js['errors'])[:160])
    return _rows(js.get('response', []))


def update_khl(log: list[str], backfill: int = 0, fresh: bool = True) -> int:
    """Вчера (пока не всё доиграно), сегодня и завтра по Москве + до backfill недостающих дат сезона. Возвращает число матчей."""
    if not os.getenv('APISPORTS_KEY'):
        return 0
    st = _kstate()
    utc_day = str(dt.datetime.now(dt.timezone.utc).date())
    if st.get('utc_day') != utc_day:
        st['utc_day'], st['used'] = utc_day, 0
    done = set(st.get('done', []))
    today = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=3)).date()
    want = [today - dt.timedelta(days=1), today, today + dt.timedelta(days=1)] if fresh else []
    want = [d for d in want if str(d) not in done]
    d = KHL_SEASON_START
    miss = []
    while d < today - dt.timedelta(days=1):
        if str(d) not in done:
            miss.append(d)
        d += dt.timedelta(days=1)
    want += miss[:backfill]
    rows, asked, errs = [], 0, []
    for d in want:
        if st['used'] >= DAY_CAP:
            errs.append('суточный лимит'); break
        if asked:
            time.sleep(PAUSE)
        try:
            r = khl_date(d); asked += 1; st['used'] += 1
        except Exception as e:
            asked += 1; st['used'] += 1
            errs.append(f'{d}: {str(e)[:80]}')
            if 'limit' in str(e).lower() or 'requests' in str(e).lower():
                break
            continue
        rows += r
        if d < today and all(x['state'] in ('post', 'void') for x in r):
            done.add(str(d))
    if rows:
        save(pd.concat([load(), pd.DataFrame(rows)], ignore_index=True))
    st['done'] = sorted(done)
    DATA.mkdir(parents=True, exist_ok=True)
    KSTATE.write_text(json.dumps(st, ensure_ascii=False), encoding='utf-8')
    log.append(f'🏒 КХЛ: запросов {asked} (сутки {st["used"]}/{DAY_CAP}), матчей {len(rows)}'
               + (f'; {"; ".join(errs[:3])}' if errs else ''))
    return len(rows)


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


SUMMARY = 'https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/summary?event={id}'


def summary_probe(log: list[str]) -> None:
    """Проба протокола матча ESPN: какие поля есть для бросков и вратарей (структура — в журнал)."""
    df = load()
    g = df[(df.league == 'NHL') & df.end.astype(str).isin(['REG', 'OT', 'SO'])].sort_values('start').tail(1).iloc[0]
    js = _get(SUMMARY.format(id=str(g.id).split(':')[1]))
    log.append(f"матч {g.home} — {g.away} {g.start}; ключи: {sorted(js.keys())}")
    box = js.get('boxscore') or {}
    log.append(f"boxscore ключи: {sorted(box.keys())}")
    for t in box.get('teams') or []:
        log.append(f"команда {(t.get('team') or {}).get('displayName')} {t.get('homeAway')}: "
                   + '; '.join(f"{x.get('name')}={x.get('displayValue')}" for x in (t.get('statistics') or [])))
    for t in box.get('players') or []:
        for grp in t.get('statistics') or []:
            log.append(f"игроки {(t.get('team') or {}).get('abbreviation')} · группа {grp.get('name')}: поля {grp.get('names') or grp.get('labels')} · "
                       f"{len(grp.get('athletes') or [])} чел.")
            if str(grp.get('name', '')).lower().startswith('goal'):
                for a in grp.get('athletes') or []:
                    log.append(f"   вратарь {(a.get('athlete') or {}).get('displayName')} starter={a.get('starter')} stats={a.get('stats')}")
    log.append(f"размер ответа {len(json.dumps(js))} байт")


if __name__ == '__main__':
    import sys as _s
    lg: list[str] = []
    if len(_s.argv) > 1 and _s.argv[1] == 'probe':
        khl_probe_dates(lg)
    elif len(_s.argv) > 1 and _s.argv[1] == 'box_probe':
        summary_probe(lg)
    elif len(_s.argv) > 1 and _s.argv[1] == 'khl':                 # догрузка сезона КХЛ по датам (≈ 80 запросов, ~9 мин)
        update_khl(lg, backfill=80)
    else:
        collect_history(lg)
    print('\n'.join(lg))
