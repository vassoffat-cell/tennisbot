"""OddsPapi (oddspapi.io, бесплатно 250 запросов/мес) — цена Pinnacle на исход теннисного матча, когда её нет в The Odds API.
ТЗ-004: без цены Pinnacle перевес не измерить. Ключ — секрет ODDSPAPI_KEY (пользователь регистрируется сам).
API v4: /fixtures?sportId=12&from&to (матчи), /odds?fixtureId (все букмекеры; Winner — рынок 121, исходы 121 / 122).
Бюджет: не больше DAILY_MAX запросов в сутки (≈ 7 × 30 = 210 < 250): 1 — список матчей, остальные — котировки самых равных
матчей (по модели P ближе к 0,5 — там зона кф 1,70–2,50). Кэш дня: data/odds/oddspapi_<дата>.json.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

DIR = Path(__file__).resolve().parent / 'data' / 'odds'
BASE = 'https://api.oddspapi.io/v4'
DAILY_MAX = 7
TENNIS = 12
WINNER, OUT1, OUT2 = '121', '121', '122'


def _get(path: str, params: dict) -> object:
    q = urllib.parse.urlencode(dict(params, apiKey=os.getenv('ODDSPAPI_KEY', '')))
    req = urllib.request.Request(f'{BASE}{path}?{q}', headers={'User-Agent': 'standing-in-motion/1.0'})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode('utf-8'))


def _cache(day: str) -> tuple[Path, dict]:
    f = DIR / f'oddspapi_{day}.json'
    try:
        return f, json.loads(f.read_text(encoding='utf-8'))
    except Exception:
        return f, {'calls': 0, 'fixtures': None, 'odds': {}}


def _price(o: dict) -> float | None:
    try:
        pl = (o.get('players') or {}).get('0') or {}
        return float(pl['price']) if pl.get('active', True) and pl.get('price') else None
    except Exception:
        return None


def pinnacle_ml(items: list[tuple], day: dt.date, same_name, log: list[str]) -> dict[str, tuple[float, float]]:
    """{espn_id: (кф Pinnacle П1, кф Pinnacle П2)} для матчей items (порядок игроков — как в нашем списке)."""
    if not os.getenv('ODDSPAPI_KEY') or not items:
        return {}
    DIR.mkdir(parents=True, exist_ok=True)
    f, c = _cache(day.isoformat())
    out: dict[str, tuple[float, float]] = {}
    try:
        if c['fixtures'] is None and c['calls'] < DAILY_MAX:
            c['calls'] += 1
            fx = _get('/fixtures', dict(sportId=TENNIS, **{'from': day.isoformat(), 'to': (day + dt.timedelta(days=2)).isoformat()}))
            c['fixtures'] = [dict(id=str(x.get('fixtureId')), a=x.get('participant1Name', ''), b=x.get('participant2Name', ''),
                                  t=x.get('startTime', '')) for x in (fx if isinstance(fx, list) else fx.get('data', []) or [])]
        fixtures = c['fixtures'] or []
        # пары: наш матч → фикстура OddsPapi (обе фамилии совпадают)
        pairs = []
        for m, row, _ in items:
            for x in fixtures:
                s1 = same_name(x['a'], row['player1']) + same_name(x['b'], row['player2'])
                s2 = same_name(x['a'], row['player2']) + same_name(x['b'], row['player1'])
                if max(s1, s2) >= 1.6:
                    pairs.append((abs(float(row.get('p1') or 0.5) - 0.5), str(m.id), x['id'], s2 > s1))
                    break
        for _, mid, fid, flip in sorted(pairs):                  # самые равные матчи — первыми (бюджет)
            if fid not in c['odds']:
                if c['calls'] >= DAILY_MAX:
                    continue
                c['calls'] += 1
                js = _get('/odds', dict(fixtureId=fid))
                pin = ((js.get('bookmakerOdds') or {}).get('pinnacle') or {}).get('markets', {}).get(WINNER, {}).get('outcomes', {})
                k = [_price(pin.get(OUT1, {})), _price(pin.get(OUT2, {}))]
                if None not in k:                                # пустое не кэшируем: рынок мог быть закрыт временно
                    c['odds'][fid] = k
            k = c['odds'].get(fid)
            if k and None not in k:
                out[mid] = (k[1], k[0]) if flip else (k[0], k[1])
        log.append(f'OddsPapi: Pinnacle по {len(out)} матчам, запросов за сутки {c["calls"]}/{DAILY_MAX}')
    except Exception as e:
        log.append(f'OddsPapi: недоступен ({e.__class__.__name__})')
    f.write_text(json.dumps(c, ensure_ascii=False), encoding='utf-8')
    return out


def probe() -> str:
    """Проверка ключа один раз в сутки: есть ли ключ, отвечает ли API, сколько теннисных матчей на сегодня (1 запрос)."""
    if not os.getenv('ODDSPAPI_KEY'):
        return 'OddsPapi: ключа нет (секрет ODDSPAPI_KEY не найден)'
    day = dt.date.today()
    f = DIR / f'oddspapi_probe_{day.isoformat()}.json'
    if f.exists():
        return 'OddsPapi: ключ есть (проверен сегодня)'
    DIR.mkdir(parents=True, exist_ok=True)
    try:
        fx = _get('/fixtures', dict(sportId=TENNIS, **{'from': day.isoformat(), 'to': (day + dt.timedelta(days=1)).isoformat()}))
        data = fx if isinstance(fx, list) else (fx.get('data') or [])
        info = dict(n=len(data), sample=data[:3])
        now = dt.datetime.utcnow().strftime('%Y-%m-%dT%H:%M')
        cand = [x for x in data if x.get('hasOdds') and (x.get('externalProviders') or {}).get('pinnacleId')]
        cand.sort(key=lambda x: (str(x.get('startTime', '')) < now, str(x.get('startTime', ''))))   # не начавшиеся — первыми
        msg = ''
        if cand:                                                   # 1 запрос: проверка разбора котировок Pinnacle (рынок 121)
            js = _get('/odds', dict(fixtureId=cand[0]['fixtureId']))
            books = js.get('bookmakerOdds') or {}
            pin = (books.get('pinnacle') or {}).get('markets', {})
            win = pin.get(WINNER, {}).get('outcomes', {})
            k = [_price(win.get(OUT1, {})), _price(win.get(OUT2, {}))]
            info.update(odds_fixture=cand[0]['fixtureId'], books=sorted(books)[:40], pin_markets=sorted(pin)[:30], pin_winner=k)
            msg = (f"; Pinnacle на исход: {cand[0].get('participant1Name')} — {cand[0].get('participant2Name')} {k}, "
                   f"1xBet в списке: {'да' if any('1xbet' in b for b in books) else 'нет'}")
        f.write_text(json.dumps(info, ensure_ascii=False), encoding='utf-8')
        return f'OddsPapi: ключ работает, теннисных матчей на сегодня {len(data)}, с Pinnacle {len(cand)}' + msg
    except Exception as e:
        return f'OddsPapi: ключ есть, но API ответил ошибкой ({e.__class__.__name__}: {str(e)[:80]})'
