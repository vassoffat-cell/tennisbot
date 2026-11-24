"""25.11 (IMP-04, CONTROL Ц6б): цены КХЛ из OddsPapi — шаг 1 полной дороги: только снимки, без ⭐.

The Odds API КХЛ не котирует; API-Sports (бесплатный план) не видит сезон 2026. OddsPapi v4 (документированный API, ключ
ODDSPAPI_KEY): Ice Hockey sportId 15, KHL tournamentId 268; /odds-by-tournaments отдаёт все матчи турнира одним запросом
на контору (проба 24.11, engine/research/khl_price_probe.py).

Расход — только из остатка после резерва тенниса (oddspapi.spare): один снимок в хоккейные сутки (12:00 → 12:00 Ташкента),
2 запроса (Pinnacle, 1xBet) + 1 запрос /fixtures, пока не известны названия всех команд дня, + 1 раз справочник рынков.
Не хватает остатка — снимок пропускается (теннис в приоритете), в логе причина.

Файл снимка: data/odds/khl_<сутки>.json —
  {ts, fixtures: [{id, start, home, away, books: {pinnacle: {рынок: {исход: кф}}, 1xbet: {...}}}]}.
Справочник рынков хоккея: data/odds/oddspapi_markets_15.json ({рынок: [название, линия, {исход: имя}]}).
Команды: data/odds/oddspapi_khl_teams.json ({id участника: название}).
Шаг 2 (после 7 дней снимков): сверка правил тотала (с ОТ / основное время, как С24 для НХЛ) → кандидаты ⭐ КХЛ (hvalue).
"""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

import oddspapi as OP

SPORT, KHL = 15, 268
BOOKS = ('pinnacle', '1xbet')
PAUSE = 3.0                                 # секунд между запросами (25.11: без паузы 2-й запрос — HTTPError)
DIR = OP.DIR
TEAMS = DIR / 'oddspapi_khl_teams.json'
MARKETS = DIR / f'oddspapi_markets_{SPORT}.json'


def _load(f: Path, default):
    try:
        return json.loads(f.read_text(encoding='utf-8'))
    except Exception:
        return default


def _rows(js) -> list[dict]:
    return js if isinstance(js, list) else ((js or {}).get('data') or []) if isinstance(js, dict) else []


def _prices(fx: dict, book: str) -> dict:
    """{рынок: {исход: кф}} одной конторы из ответа odds-by-tournaments (структура как у /odds)."""
    mk = (((fx.get('bookmakerOdds') or {}).get(book) or {}).get('markets') or {})
    out = {}
    for mid, m in mk.items():
        oc = {}
        for oid, o in ((m or {}).get('outcomes') or {}).items():
            pr = OP._price(o)
            if pr:
                oc[str(oid)] = pr
        if oc:
            out[str(mid)] = oc
    return out


def snapshot(day: str, now: dt.datetime, log: list[str]) -> int:
    """Снимок цен КХЛ на хоккейные сутки day (ГГГГ-ММ-ДД). Возвращает число матчей с ценой (0 — нет снимка)."""
    import os
    if not os.getenv('ODDSPAPI_KEY'):
        return 0
    f = DIR / f'khl_{day}.json'
    old = _load(f, {})
    have = {b for r in old.get('fixtures', []) for b in r.get('books', {})}
    if set(BOOKS) <= have or int(old.get('tries', 0)) >= 2:            # полный снимок есть или 2 попытки — квоту не жжём
        return len(old.get('fixtures', []))
    teams = _load(TEAMS, {})
    need = len(set(BOOKS) - have) + (0 if MARKETS.exists() else 1)
    left = OP.spare()
    if left < need:
        log.append(f'🏒 КХЛ цены: пропуск — остаток OddsPapi {left} < {need} (резерв тенниса {OP.tennis_reserve()}, '
                   f'месяц {OP.month_used()}/{OP.LIMIT})')
        return 0
    iso = now.strftime('%Y-%m-%dT%H:%M')
    fx: dict[str, dict] = {r['id']: dict(r, p1=r.get('p1', ''), p2=r.get('p2', '')) for r in old.get('fixtures', [])}
    try:
        for b in BOOKS:
            if b in have:                                                   # повторная попытка — только недостающая контора
                continue
            time.sleep(PAUSE)                                               # 25.11: 2-й запрос подряд → HTTPError (минимальная пауза API)
            try:
                got = _rows(OP._get('/odds-by-tournaments', dict(tournamentIds=KHL, bookmaker=b), who='khl'))
            except Exception as e:
                log.append(f'🏒 КХЛ цены: {b} — {e.__class__.__name__} {getattr(e, "code", "")}')
                continue
            for x in got:
                if str(x.get('startTime', '')) <= iso:                      # начавшиеся и сыгранные — не нужны
                    continue
                r = fx.setdefault(str(x.get('fixtureId')), dict(
                    id=str(x.get('fixtureId')), start=str(x.get('startTime', '')),
                    p1=str(x.get('participant1Id', '')), p2=str(x.get('participant2Id', '')), books={}))
                if not r['p1']:                                             # строка прошлой попытки без id участников
                    r['p1'], r['p2'] = str(x.get('participant1Id', '')), str(x.get('participant2Id', ''))
                pr = _prices(x, b)
                if pr:
                    r['books'][b] = pr
        unknown = {r[k] for r in fx.values() for k in ('p1', 'p2')} - set(teams) - {''}
        if unknown and OP.spare() >= 1:
            time.sleep(PAUSE)                                     # названия команд — один раз на новую команду
            d0 = dt.date.fromisoformat(day)
            for x in _rows(OP._get('/fixtures', {'tournamentId': KHL, 'from': d0.isoformat(),
                                                 'to': (d0 + dt.timedelta(days=7)).isoformat()}, who='khl')):   # окно как у odds-by-tournaments
                for i in ('1', '2'):
                    if x.get(f'participant{i}Id') is not None and x.get(f'participant{i}Name'):
                        teams[str(x[f'participant{i}Id'])] = x[f'participant{i}Name']
            TEAMS.write_text(json.dumps(teams, ensure_ascii=False, sort_keys=True), encoding='utf-8')
        if not MARKETS.exists() and OP.spare() >= 1:                        # справочник рынков — один раз
            time.sleep(PAUSE)
            mk = {str(m.get('marketId')): [m.get('marketName'), m.get('handicap'),
                                           {str(o.get('outcomeId')): o.get('outcomeName') for o in (m.get('outcomes') or [])}]
                  for m in _rows(OP._get('/markets', dict(sportId=SPORT), who='khl'))}
            if mk:
                MARKETS.write_text(json.dumps(mk, ensure_ascii=False), encoding='utf-8')
    except Exception as e:
        log.append(f'🏒 КХЛ цены: сбой OddsPapi ({e.__class__.__name__})')
    rows = []
    for r in sorted(fx.values(), key=lambda r: r['start']):
        if not r['books']:
            continue
        rows.append(dict(id=r['id'], start=r['start'], p1=r['p1'], p2=r['p2'], home=teams.get(r['p1'], r.get('home', r['p1'])),
                         away=teams.get(r['p2'], r.get('away', r['p2'])), books=r['books']))
    # пустой ответ — помечаем попытку (не больше 2 за сутки), снимок с ценами — пишем
    f.write_text(json.dumps(dict(ts=now.isoformat(timespec='seconds'), fixtures=rows, tries=int(old.get('tries', 0)) + 1),
                            ensure_ascii=False), encoding='utf-8')
    pin = sum('pinnacle' in r['books'] for r in rows)
    x1 = sum('1xbet' in r['books'] for r in rows)
    log.append(f'🏒 КХЛ цены (OddsPapi): матчей {len(rows)} · Pinnacle {pin} · 1xBet {x1}; месяц {OP.month_used()}/{OP.LIMIT}, '
               f'резерв тенниса {OP.tennis_reserve()}')
    return len(rows)
