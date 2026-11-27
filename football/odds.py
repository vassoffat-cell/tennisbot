"""Пакет №3 · котировки букмекеров из The Odds API (бесплатно 500 кредитов/мес; 1xBet = onexbet, Pinnacle = pinnacle).

Котировки используются ТОЛЬКО для ⭐ ставки дня (решение пользователя 08.11.2026, вариант C):
кф 1xBet ≥ 1,35, максимальная справедливая вероятность, уверенность 90% в выгодности.
Прогноз-список по-прежнему — только модель.

Расход кредитов: запрос /sports бесплатный; /odds стоит (число рынков) × (число групп по 10 букмекеров) = рынки × 1.
Ключ — секрет GitHub ODDS_API_KEY (в коде и файлах не хранится). Остаток кредитов — из заголовков ответа.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

API = 'https://api.the-odds-api.com/v4'
BOOKS = ('onexbet', 'pinnacle',                     # 16.11 (HANDOFF з.2): + 8 книг для «лучшей цены» — та же цена в кредитах:
         'marathonbet', 'williamhill', 'unibet_eu', 'betsson',   # до 10 букмекеров = 1 регион (кредиты = рынки × 1)
         'sport888', 'betclic', 'betfair_ex_eu', 'matchbook')
EXCH = {'betfair_ex_eu': 0.05, 'matchbook': 0.02}     # биржи: комиссия с выигрыша → кф для сравнения = 1 + (кф − 1)(1 − c)


def best(books: dict | None) -> tuple[float | None, str]:
    """Лучшая цена исхода среди букмекеров (биржи — за вычетом комиссии): (кф, букмекер)."""
    eff = {b: 1 + (float(k) - 1) * (1 - EXCH.get(b, 0.0)) for b, k in (books or {}).items() if k}
    if not eff:
        return None, ''
    b = max(eff, key=eff.get)
    return round(eff[b], 3), b


def ev(p, k) -> float | None:
    """Ожидание ставки на 1 единицу: P × кф − 1 (возврат — без учёта, линии с половинкой)."""
    try:
        p, k = float(p), float(k)
    except (TypeError, ValueError):
        return None
    return round(p * k - 1, 4) if p == p and k > 1 else None
DIR = Path(__file__).resolve().parent / 'data' / 'odds'


class OddsError(RuntimeError):
    pass


def _key() -> str:
    k = os.getenv('ODDS_API_KEY', '')
    if not k:
        raise OddsError('нет ODDS_API_KEY')
    return k


def _get(path: str, params: dict) -> tuple[object, dict]:
    """GET с ключом; возвращает (JSON, {remaining, used, last}). Ключ не попадает в логи и файлы."""
    q = urllib.parse.urlencode({**params, 'apiKey': _key()})
    req = urllib.request.Request(f'{API}{path}?{q}', headers={'User-Agent': 'standing-in-motion/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            meta = {k: r.headers.get(f'x-requests-{k}') for k in ('remaining', 'used', 'last')}
            return json.loads(r.read().decode('utf-8')), meta
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', 'replace')[:300]
        raise OddsError(f'HTTP {e.code}: {body}') from None


def sports() -> list[dict]:
    """Список видов спорта/турниров (бесплатно)."""
    js, _ = _get('/sports', {})
    return js


def odds(sport_key: str, markets: tuple[str, ...]) -> tuple[list[dict], dict]:
    """Котировки 1xBet и Pinnacle по турниру/лиге: [{id, commence_time, home_team, away_team, bookmakers: [...]}]."""
    return _get(f'/sports/{sport_key}/odds', dict(bookmakers=','.join(BOOKS), markets=','.join(markets),
                                                 oddsFormat='decimal', dateFormat='iso'))


def probe(groups: tuple[str, ...] = ('Tennis',), extra: tuple[str, ...] = ('soccer_russia_premier_league',)) -> list[str]:
    """Разовая проверка покрытия: какие турниры активны, какие рынки дают 1xBet и Pinnacle. Результат — data/odds/probe.json."""
    DIR.mkdir(parents=True, exist_ok=True)
    out, log = {'when': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), 'sports': [], 'events': {}}, []
    try:
        sp = sports()
    except OddsError as e:
        return [f'кф: проба не удалась ({e})']
    out['sports'] = [dict(key=s['key'], group=s['group'], title=s['title'], active=s['active']) for s in sp]
    keys = [s['key'] for s in sp if s['active'] and s['group'] in groups] + [k for k in extra if any(s['key'] == k for s in sp)]
    meta = {}
    for k in keys[:8]:
        try:
            ev, meta = odds(k, ('h2h', 'spreads', 'totals'))
        except OddsError as e:
            log.append(f'кф: {k} — {e}')
            continue
        out['events'][k] = ev
        books = {}
        for e in ev:
            for b in e.get('bookmakers', []):
                for mk in b.get('markets', []):
                    books.setdefault((b['key'], mk['key']), 0)
                    books[(b['key'], mk['key'])] += 1
        log.append(f'кф: {k}: матчей {len(ev)}; ' + ', '.join(f'{b}/{m} {n}' for (b, m), n in sorted(books.items())))
    out['meta'] = meta
    (DIR / 'probe.json').write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    log.append(f"кф: кредитов осталось {meta.get('remaining')}, использовано {meta.get('used')}")
    return log


def event_odds(sport_key: str, event_id: str, markets: tuple[str, ...]) -> tuple[dict, dict]:
    """Котировки одного матча по любым рынкам (альтернативные линии, индивидуальные тоталы). Стоимость: рынки × 1 за матч."""
    return _get(f'/sports/{sport_key}/events/{event_id}/odds', dict(bookmakers=','.join(BOOKS), markets=','.join(markets),
                                                                       oddsFormat='decimal', dateFormat='iso'))


def probe_event(sport_key: str, event_id: str, markets: tuple[str, ...]) -> list[str]:
    """Разовая проба рынков одного матча → data/odds/probe_event.json."""
    try:
        ev, meta = event_odds(sport_key, event_id, markets)
    except OddsError as e:
        return [f'кф: проба матча не удалась ({e})']
    (DIR / 'probe_event.json').write_text(json.dumps(dict(event=ev, meta=meta), ensure_ascii=False, indent=1), encoding='utf-8')
    got = sorted({(b['key'], m['key'], len(m['outcomes'])) for b in ev.get('bookmakers', []) for m in b.get('markets', [])})
    return [f'кф: матч {ev.get("home_team")} — {ev.get("away_team")}: ' + ', '.join(f'{b}/{m} {n}' for b, m, n in got),
            f"кф: кредитов осталось {meta.get('remaining')}, стоимость запроса {meta.get('last')}"]


# ============================================================ котировки дня для ⭐ ставки дня
import re as _re
import unicodedata as _ud

USAGE = DIR / 'usage.json'
RESERVE_PER_DAY = 5          # запас кредитов на каждый оставшийся день месяца (второй бот и скриншоты)


def _norm(s: str) -> str:
    return _re.sub(r'[^a-z0-9 ]', ' ', _ud.normalize('NFKD', str(s).lower()).encode('ascii', 'ignore').decode()).strip()


def _tokens(s: str) -> set[str]:
    stop = {'fc', 'fk', 'cf', 'sc', 'ac', 'as', 'sk', 'afc', 'cd', 'ud', 'sv', 'vfb', 'vfl', 'tsg', 'rc', 'us', 'ss', 'de', 'la', 'le'}
    return {t for t in _norm(s).split() if len(t) > 1 and t not in stop}


def same_name(a: str, b: str) -> float:
    """Сходство названий команд / имён игроков: доля общих слов (0…1); для игроков решает фамилия."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def find_event(events: list[dict], home: str, away: str, start_utc: str, hours: float = 3.0) -> tuple[dict | None, bool]:
    """Матч API для нашего (home/away или player1/player2) по времени ±hours и названиям; второй элемент — порядок обратный."""
    t0 = dt.datetime.fromisoformat(str(start_utc).replace(' ', 'T')).replace(tzinfo=dt.timezone.utc)
    best, flip, score = None, False, 0.0
    for e in events:
        t = dt.datetime.fromisoformat(e['commence_time'].replace('Z', '+00:00'))
        if abs((t - t0).total_seconds()) > hours * 3600:
            continue
        s1 = same_name(home, e['home_team']) + same_name(away, e['away_team'])
        s2 = same_name(home, e['away_team']) + same_name(away, e['home_team'])
        s, f = (s1, False) if s1 >= s2 else (s2, True)
        if s > score:
            best, flip, score = e, f, s
    return (best, flip) if score >= 1.0 else (None, False)


def _usage() -> dict:
    try:
        return json.loads(USAGE.read_text(encoding='utf-8'))
    except Exception:
        return {}


# 16.11 (решение пользователя): доли месячного лимита The Odds API (500) по ботам — ставка дня не остаётся без цен из-за исследования.
# Декабрь без долей: футбол ≈ 440 + хоккей A31 ≈ 186 = 626 > 500. Тенниса в январе станет больше — пересмотр на аудите (AUDIT).
QUOTA = {'football': 300, 'hockey': 150, 'tennis': 50}
QUOTA_BY_MONTH = {'2026-12': {'football': 350, 'hockey': 120, 'tennis': 30}}   # 16.11: декабрь — тенниса нет, футбол важнее A31


def _quota() -> dict:
    if PAID_PLAN:
        return PAID_QUOTA
    return QUOTA_BY_MONTH.get(_month(), QUOTA)
SAFETY = 5                   # общий остаток API не опускать ниже
# 28.11 Ц22 (решение пользователя «давай», включит сам после оплаты): тариф The Odds API 20K — $30/мес, 20 000 кредитов.
# PAID_PLAN = True → свежая цена в каждом окне плана (T−3 ч, T−40 мин): лига с матчем в ближайшие SOON_H ч обновляется не реже FRESH_MIN.
# Флаг — в обеих копиях (корень и football/), тест регрессии сверяет. Оценка расхода: футбол ≈ 1 500, хоккей ≈ 450, теннис ≈ 1 800 в мес.
PAID_PLAN = False
PAID_QUOTA = {'football': 9000, 'hockey': 4000, 'tennis': 6000}   # из 20 000; ≈ 1 000 — запас и исследования
FRESH_MIN = 30
SOON_H = 3.5


def _month() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime('%Y-%m')


def _save_usage(meta: dict, bot: str = '', cost: int = 0) -> None:
    if meta.get('remaining') is not None:
        DIR.mkdir(parents=True, exist_ok=True)
        u = _usage()
        spent = {m: v for m, v in (u.get('spent') or {}).items() if m == _month()}          # учёт доли — только текущий месяц
        if bot and cost:
            spent.setdefault(_month(), {})[bot] = int(spent.get(_month(), {}).get(bot, 0)) + int(cost)
        USAGE.write_text(json.dumps(dict(remaining=int(float(meta['remaining'])), used=meta.get('used'),
                                         when=dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), spent=spent)),
                         encoding='utf-8')


def quota_left(bot: str) -> int | None:
    """Остаток доли бота в этом месяце (None — у бота нет доли)."""
    q = _quota()
    if bot not in q:
        return None
    return q[bot] - int(((_usage().get('spent') or {}).get(_month()) or {}).get(bot, 0))


def budget_ok(cost: int, bot: str = '') -> bool:
    """Хватает ли кредитов: у бота с долей — в пределах доли месяца и общий остаток ≥ SAFETY; без доли — с запасом
    RESERVE_PER_DAY на каждый оставшийся день месяца (новый месяц — сброс до 500)."""
    left = quota_left(bot)
    if left is not None and cost > left:
        return False
    u = _usage()
    if not u:
        return True
    now = dt.datetime.now(dt.timezone.utc)
    if u.get('when', '')[:7] != now.strftime('%Y-%m'):
        return True
    if left is not None:
        return int(u['remaining']) - cost >= SAFETY
    nxt = (now.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    days_left = (nxt - now).days + 1
    return int(u['remaining']) - cost >= RESERVE_PER_DAY * days_left


def _soon(events: list[dict], now: dt.datetime) -> bool:
    """Есть ли в кэше лиги матч, который начнётся в ближайшие SOON_H ч (окна плана T−3 ч / T−40 мин)."""
    for e in events or []:
        try:
            t = dt.datetime.fromisoformat(str(e.get('commence_time', '')).replace('Z', '+00:00'))
        except ValueError:
            continue
        if now < t <= now + dt.timedelta(hours=SOON_H):
            return True
    return False


def day_events(bot: str, sport_keys: list[str], markets: tuple[str, ...], day: str, refresh_hours: float = 6.0,
               force: dict[str, str] | None = None) -> tuple[list[dict], list[str]]:
    """Котировки 1xBet/Pinnacle по турнирам дня. Кэш data/odds/{bot}_{day}.json; повтор не чаще refresh_hours;
    каждая лига — только если хватает кредитов. Возвращает (матчи с котировками, журнал)."""
    if not os.getenv('ODDS_API_KEY'):
        return [], ['кф: нет ключа']
    DIR.mkdir(parents=True, exist_ok=True)
    f = DIR / f'{bot}_{day}.json'
    cache = json.loads(f.read_text(encoding='utf-8')) if f.exists() else {'sports': {}, 'events': {}}
    now = dt.datetime.now(dt.timezone.utc)
    log, spent = [], 0
    for key in sport_keys:
        got = cache['sports'].get(key)
        ttl = refresh_hours * 3600
        if PAID_PLAN and _soon(cache['events'].get(key, []), now):   # Ц22: матч лиги скоро — цена не старше FRESH_MIN
            ttl = min(ttl, FRESH_MIN * 60)
        if got and (now - dt.datetime.fromisoformat(got)).total_seconds() < ttl and not (force and str(force.get(key, '')) > got):
            continue                                            # 28.11 Ц23б: важная новость новее цены лиги — цена обновляется
        if not budget_ok(len(markets), bot):
            log.append(f'кф: {key} пропущен — бережём кредиты (доля {bot}: осталось {quota_left(bot)})')
            continue
        try:
            ev, meta = odds(key, markets)
        except OddsError as e:
            log.append(f'кф: {key} — {e}')
            continue
        c = int(float(meta.get('last') or len(markets)))
        _save_usage(meta, bot, c)
        spent += c
        cache['events'][key] = ev
        cache['sports'][key] = now.isoformat(timespec='seconds')
    f.write_text(json.dumps(cache, ensure_ascii=False), encoding='utf-8')
    if spent:
        log.append(f'кф: потрачено {spent}, осталось {_usage().get("remaining")}; доля {bot}: осталось {quota_left(bot)}')
    return [dict(e, sport_key=k) for k, evs in cache['events'].items() for e in evs], log


def add_alternates(bot: str, day: str, sport_key: str, event_id: str, markets: tuple[str, ...]) -> tuple[dict | None, str]:
    """Альтернативные линии Pinnacle по одному матчу (кэш на день); None — нет кредитов / ошибка."""
    f = DIR / f'{bot}_{day}_alt.json'
    cache = json.loads(f.read_text(encoding='utf-8')) if f.exists() else {}
    if event_id in cache:
        return cache[event_id], ''
    if not budget_ok(len(markets), bot):
        return None, 'кф: альтернативные линии — бережём кредиты'
    try:
        ev, meta = event_odds(sport_key, event_id, markets)
    except OddsError as e:
        return None, f'кф: альтернативы {event_id} — {e}'
    _save_usage(meta, bot, int(float(meta.get('last') or len(markets))))
    cache[event_id] = ev
    f.write_text(json.dumps(cache, ensure_ascii=False), encoding='utf-8')
    return ev, ''


def quotes(event: dict) -> dict[tuple, dict[str, float]]:
    """{(рынок, исход, линия): {букмекер: кф}} по матчу API (исход — имя команды/игрока, Over/Under, Draw, Yes/No…)."""
    out: dict[tuple, dict[str, float]] = {}
    for b in event.get('bookmakers', []):
        for m in b.get('markets', []):
            for o in m.get('outcomes', []):
                key = (m['key'].replace('alternate_', ''), o['name'], o.get('point'))
                out.setdefault(key, {})[b['key']] = float(o['price'])
    return out


def r_ratio() -> float:
    """Отношение кф 1xBet / Pinnacle по сверкам со скриншотами (data/odds/r.json); нет данных — 0,98."""
    try:
        d = json.loads((DIR / 'r.json').read_text(encoding='utf-8'))
        return float(d['r']) if d.get('n', 0) >= 5 else 0.98
    except Exception:
        return 0.98


def cached_events(bot: str, day: str) -> list[dict]:
    """Котировки дня из кэша (без запроса к API) — для сверки скриншотов с Pinnacle."""
    out = []
    for f in (DIR / f'{bot}_{day}.json', DIR / f'{bot}_{day}_alt.json'):
        try:
            d = json.loads(f.read_text(encoding='utf-8'))
        except Exception:
            continue
        if 'events' in d:
            out += [dict(e, sport_key=k) for k, evs in d['events'].items() for e in evs]
        else:                                                   # альтернативы: {event_id: event}
            alt = {k: v for k, v in d.items()}
            for e in out:
                if e['id'] in alt:
                    e['bookmakers'] = e.get('bookmakers', []) + alt[e['id']].get('bookmakers', [])
    return out
