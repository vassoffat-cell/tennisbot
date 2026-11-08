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
BOOKS = ('onexbet', 'pinnacle')
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
