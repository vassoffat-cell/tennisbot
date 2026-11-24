"""24.11.2026 · IMP-04: бесплатная законная цена КХЛ (без внутренних API сайтов, без платного).

The Odds API КХЛ не котирует (проба 16.11). Проверяем два документированных API, ключи которых у нас уже есть:
  1) API-Sports Hockey (v1.hockey.api-sports.io, документированный эндпоинт /odds; бесплатный план 100 запросов/сутки,
     у нас ≤ 90 — сегодня занято ≈ 11): /games КХЛ на сегодня–завтра → /odds?game=… → какие конторы и рынки, есть ли 1xBet / Pinnacle.
  2) OddsPapi v4 (oddspapi.io, 250 запросов/мес, в ноябре потрачено 7): /sports → хоккей → /tournaments → КХЛ →
     /fixtures → /odds одного матча.
Расход: API-Sports ≤ 4 запроса, OddsPapi ≤ 5. Только чтение и печать — ничего не пишет, ключи не печатает.
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
    q = urllib.parse.urlencode(dict(params, apiKey=os.getenv('ODDSPAPI_KEY', '')))
    try:
        return _get(f'{OP}{path}?{q}')
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
    sp = op('/sports')
    sports = sp if isinstance(sp, list) else (sp or {}).get('data') or []
    hk = [s for s in sports if 'hockey' in json.dumps(s).lower()]
    print('  видов спорта', len(sports), '| хоккей:', hk[:3])
    if not hk:
        return
    sid = hk[0].get('sportId') or hk[0].get('id')
    tr = op('/tournaments', sportId=sid)
    tours = tr if isinstance(tr, list) else (tr or {}).get('data') or []
    khl = [x for x in tours if any(k in json.dumps(x, ensure_ascii=False).lower() for k in ('khl', 'kontinental', 'кхл'))]
    print('  турниров хоккея', len(tours), '| КХЛ:', khl[:3])
    if not khl:
        return
    tid = khl[0].get('tournamentId') or khl[0].get('id')
    d = dt.date.today()
    fx = op('/fixtures', tournamentId=tid, **{'from': d.isoformat(), 'to': (d + dt.timedelta(days=2)).isoformat()})
    fl = fx if isinstance(fx, list) else (fx or {}).get('data') or []
    print('  матчей КХЛ на 2 дня:', len(fl), [(x.get('participant1Name'), x.get('participant2Name'), x.get('startTime')) for x in fl[:3]])
    if not fl:
        return
    od = op('/odds', fixtureId=fl[0].get('fixtureId'))
    books = (od or {}).get('bookmakerOdds') or {}
    print('  контор с ценой:', len(books), sorted(books)[:25])
    for b in ('pinnacle', '1xbet', 'marathonbet'):
        mk = (books.get(b) or {}).get('markets') or {}
        print(f'   {b}: рынков {len(mk)} — {sorted(mk)[:12]}')


if __name__ == '__main__':
    for f in (probe_apisports, probe_oddspapi):
        try:
            f()
        except Exception as e:
            print(f'  {f.__name__}: сбой {type(e).__name__} {getattr(e, "code", "")}')
