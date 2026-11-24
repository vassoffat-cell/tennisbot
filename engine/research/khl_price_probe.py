"""24.11.2026 · IMP-04: бесплатная законная цена КХЛ (без внутренних API сайтов, без платного).

The Odds API КХЛ не котирует (проба 16.11). Проверяем два документированных API, ключи которых у нас уже есть:
  1) API-Sports Hockey (v1.hockey.api-sports.io, документированный эндпоинт /odds; бесплатный план 100 запросов/сутки,
     у нас ≤ 90 — сегодня занято ≈ 11): /games КХЛ на сегодня–завтра → /odds?game=… → какие конторы и рынки, есть ли 1xBet / Pinnacle.
  2) OddsPapi v4 (oddspapi.io, 250 запросов/мес, в ноябре потрачено 7): /sports → хоккей → /tournaments → КХЛ →
     /fixtures → /odds одного матча.
Расход: API-Sports ≤ 4 запроса, OddsPapi ≤ 5 (проба 2 — 2 запроса, API-Sports пропущен: бесплатный план не видит сезон 2026). Только чтение и печать — ничего не пишет, ключи не печатает.
Запуск: daily.yml, вход research = "engine/research/khl_price_probe.py".
"""
from __future__ import annotations

import datetime as dt
import json
import os
import urllib.parse
import urllib.request

APIS = 'https://v1.hockey.api-sports.io'
KHL_ID = 35
OP = 'https://api.oddspapi.io/v4'


def _get(url: str, headers: dict | None = None) -> object:
    req = urllib.request.Request(url, headers={'User-Agent': 'standing-in-motion-research/1.0', **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode('utf-8') or '{}')


def apis(path: str) -> dict:
    js = _get(APIS + path, {'x-apisports-key': os.getenv('APISPORTS_KEY', '')})
    if js.get('errors'):
        print('   ошибки API-Sports:', js['errors'])
    return js


def op(path: str, **params) -> object:
    """25.11: через oddspapi._get — запрос попадает в месячный учёт (who='research'), daily.yml сохраняет учёт после пробы."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import oddspapi as OPM
    try:
        return OPM._get(path, params, who='research')
    except Exception as e:                       # ключ в тексте ошибки не печатается (печатаем только тип/код)
        print(f'   OddsPapi {path}: {type(e).__name__} {getattr(e, "code", "")}')
        return None


def probe_apisports() -> None:
    print('== API-Sports Hockey: КХЛ ==')
    today = dt.date.today()
    games = []
    for d in (today, today + dt.timedelta(days=1)):
        js = apis(f'/games?league={KHL_ID}&season=2026&date={d}')
        rs = js.get('response') or []
        print(f'  {d}: матчей КХЛ {len(rs)}')
        games += [g for g in rs if (g.get('status') or {}).get('short') == 'NS']
    if not games:
        print('  нет несыгранных матчей КХЛ — пробую /odds по лиге')
        js = apis(f'/odds?league={KHL_ID}&season=2026')
        print('  /odds по лиге: записей', len(js.get('response') or []))
        return
    g = games[0]
    t = g.get('teams') or {}
    print('  матч:', g.get('id'), (t.get('home') or {}).get('name'), '—', (t.get('away') or {}).get('name'), g.get('date'))
    js = apis(f'/odds?game={g.get("id")}')
    rs = js.get('response') or []
    print('  /odds: записей', len(rs))
    for r in rs[:1]:
        for b in r.get('bookmakers') or []:
            bets = b.get('bets') or []
            names = [x.get('name') for x in bets]
            print(f"   контора {b.get('name')}: рынков {len(bets)} — {names[:8]}")
            for x in bets:
                if x.get('name') in ('Home/Away', '3Way Result', 'Over/Under'):
                    print('      ', x.get('name'), [(v.get('value'), v.get('odd')) for v in (x.get('values') or [])][:6])


def probe_oddspapi() -> None:
    print('== OddsPapi v4: КХЛ ==')
    tid = 268                                # 24.11 проба 1: /sports → Ice Hockey 15, /tournaments → KHL 268
    d = dt.date.today()
    fx = op('/fixtures', tournamentId=tid, **{'from': d.isoformat(), 'to': (d + dt.timedelta(days=2)).isoformat()})
    fl = fx if isinstance(fx, list) else (fx or {}).get('data') or []
    print('  матчей КХЛ на 2 дня:', len(fl), [(x.get('participant1Name'), x.get('participant2Name'), x.get('startTime')) for x in fl[:3]])
    now = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M')
    fl = [x for x in fl if str(x.get('startTime', '')) > now]          # проба 1 взяла уже сыгранный матч → цен 0
    if not fl:
        return
    fl.sort(key=lambda x: str(x.get('startTime', '')))
    print('  беру будущий матч:', fl[0].get('participant1Name'), '—', fl[0].get('participant2Name'), fl[0].get('startTime'))
    od = op('/odds', fixtureId=fl[0].get('fixtureId'))
    books = (od or {}).get('bookmakerOdds') or {}
    print('  контор с ценой:', len(books), sorted(books)[:25])
    for b in ('pinnacle', '1xbet', 'marathonbet'):
        mk = (books.get(b) or {}).get('markets') or {}
        print(f'   {b}: рынков {len(mk)} — {sorted(mk)[:15]}')
        for mid in sorted(mk)[:6]:
            oc = (mk[mid] or {}).get('outcomes') or {}
            print('      рынок', mid, [(o, ((v.get('players') or {}).get('0') or {}).get('price')) for o, v in list(oc.items())[:4]])


def probe_markets() -> None:
    """Проба 3: названия рынков (1 запрос) и все матчи КХЛ одним запросом /odds-by-tournaments (≤ 2 запроса)."""
    print('== OddsPapi: рынки хоккея и odds-by-tournaments ==')
    mk = op('/markets', sportId=15)
    ml = mk if isinstance(mk, list) else (mk or {}).get('data') or []
    print('  рынков в справочнике:', len(ml), '| пример:', ml[:1])
    want = {'151', '151281', '151283', '151285', '151236', '151242', '151244', '1520', '1522', '1524', '15100'}
    for m in ml:
        if str(m.get('marketId')) in want:
            print('   ', m.get('marketId'), m.get('marketName'), m.get('period') or m.get('marketType') or '',
                  m.get('handicap', ''), [(o.get('outcomeId'), o.get('outcomeName')) for o in (m.get('outcomes') or [])][:3])
    for key in ('tournamentIds', 'tournamentId'):
        js = op('/odds-by-tournaments', bookmaker='pinnacle', **{key: 268})
        lst = js if isinstance(js, list) else (js or {}).get('data') if isinstance(js, dict) else None
        if lst:
            print(f'  odds-by-tournaments ({key}): матчей {len(lst)}; ключи {sorted(lst[0])[:12]}')
            break
        print(f'  odds-by-tournaments ({key}): пусто / {str(js)[:200]}')


if __name__ == '__main__':
    for f in (probe_markets,):                  # 24.11 проба 2 ✅: KHL 268 — Pinnacle 22 рынка, 1xBet 116, Marathon 86                 # 24.11 проба 1: API-Sports — «Free plans do not have access to this season»
        try:
            f()
        except Exception as e:
            print(f'  {f.__name__}: сбой {type(e).__name__} {getattr(e, "code", "")}')
