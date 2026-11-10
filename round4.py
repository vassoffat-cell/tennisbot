"""ТЗ-004 · теннис: утренний цикл «список матчей → скриншоты 1xBet по очереди → анализ → ⭐ ставка дня + прогноз на 24 ч».

Состояние rt['r4']: date, queue [{id, t, match, p1, p2, kmin1, kmin2, start}], idx (текущий запрос), got {id: [k1, k2]},
offers [предложения со скриншотов П1/П2 — для ставки дня], state 'wait' | 'done', asked_at, misses (дней подряд без всех скриншотов).
1) Новые сутки: матчи суток с ценой Pinnacle (The Odds API, кэш дня) и кф близкими к равным (хотя бы одна сторона: кф 1xBet,
   при котором перевес ≥ 3%, лежит в 1,70–2,50) → сообщение-список БЕЗ прогнозов + запрос скриншота №1.
2) Каждый запуск: скриншот текущего матча (П1/П2 распознаны) → ответ «принят … перевес …» → следующий запрос; начавшиеся — пропуск.
3) Все скриншоты получены, или 14:00, или до начала раньшего матча очереди < 15 мин → 'done': ставка дня (pick: перевес → иначе правило C),
   таблица проверенных матчей, прогноз на 24 ч (autorun публикует после done).
"""
from __future__ import annotations

import pandas as pd

import config as C
import notify
import odds as OD
import pick as PK

DEADLINE_HOUR = 14


def _now() -> pd.Timestamp:
    return pd.Timestamp.now(tz='UTC').tz_localize(None)


def _f(x: float) -> str:
    return f'{x:.2f}'.replace('.', ',')


def candidates(items: list[tuple], day) -> list[dict]:
    """Матчи суток с ценой Pinnacle на исход и подходящими кф (см. модуль)."""
    now = _now()
    todays = [(m, row) for m, row, _ in items if m.start > now and C.wday(m.start + pd.Timedelta(hours=C.TZ_OFFSET_HOURS)) == day]
    if not todays:
        return []
    try:
        keys = [s['key'] for s in OD.sports() if s['active'] and s['group'] == 'Tennis']
        events, _ = OD.day_events('tennis', keys, ('h2h', 'spreads', 'totals'), day.isoformat(), refresh_hours=24)
    except Exception:
        return []
    out = []
    for m, row in sorted(todays, key=lambda x: x[0].start):
        ev, _ = OD.find_event(events, row['player1'], row['player2'], row['start_utc'])
        if not ev:
            continue
        pin = {}
        for (mk, name, _pt), books in OD.quotes(ev).items():
            if mk == 'h2h' and books.get('pinnacle'):
                s = '1' if OD.same_name(name, row['player1']) >= OD.same_name(name, row['player2']) else '2'
                pin[s] = books['pinnacle']
        if '1' not in pin or '2' not in pin:
            continue
        p1, p2 = PK.shin([pin['1'], pin['2']])
        kmin = [(1 + PK.VALUE_EDGE) / p for p in (p1, p2)]
        if not any(PK.VALUE_K[0] <= k <= PK.VALUE_K[1] for k in kmin):
            continue
        out.append(dict(id=str(m.id), t=str(row['start_local'])[6:], tour=row['tour'], event=row['event'],
                        match=f"{row['player1']} — {row['player2']}", p1=p1, p2=p2, kmin1=kmin[0], kmin2=kmin[1],
                        start=str(row['start_utc'])))
    return out


def _request(q: list[dict], i: int) -> str:
    x = q[i]
    return (f"📸 <b>Скриншот {i + 1} из {len(q)}</b>: {notify.esc(x['match'])} · {notify.esc(x['t'])}\n"
            'Нужен блок <b>1X2 (П1/П2)</b> в начале страницы матча 1xBet.')


def _next(r: dict, now: pd.Timestamp) -> int | None:
    for i, x in enumerate(r['queue']):
        if x['id'] not in r['got'] and pd.Timestamp(x['start']) > now:
            return i
    return None


def _edge_row(x: dict, k: list | None) -> str:
    if not k:
        return f"{x['match']}: скриншота нет"
    e1, e2 = (f"{(x[f'p{i}'] * k[i - 1] - 1) * 100:+.1f}".replace('.', ',') for i in (1, 2))
    return (f"{x['match']}: П1 {_f(k[0])} (справ. {_f(1 / x['p1'])}, {e1}%) · "
            f"П2 {_f(k[1])} (справ. {_f(1 / x['p2'])}, {e2}%)")


def step(rt: dict, items: list[tuple], day, shot_offers: list[dict], replies: list[str]) -> tuple[str, list[str], list[dict]]:
    """Шаг цикла. Возвращает (состояние 'wait' | 'done_now' | 'done', сообщения пользователю, предложения со скриншотов для ставки дня).
    replies — ответы shots.process; для матчей очереди заменяются коротким «принят … перевес …»."""
    now = _now()
    loc = now + pd.Timedelta(hours=C.TZ_OFFSET_HOURS)
    r = rt.get('r4') or {}
    msgs: list[str] = []
    if r.get('date') != day.isoformat():                       # новые сутки — список и первый запрос
        misses = int((rt.get('r4') or {}).get('misses', 0))
        q = candidates(items, day)
        r = dict(date=day.isoformat(), queue=q, got={}, offers=[], state='wait', misses=misses, asked_at=str(now))
        rt['r4'] = r
        if not q:
            r['state'] = 'done'
            return 'done_now', ['🔎 <b>Проверка ставки дня</b>: сегодня нет матчей с ценой Pinnacle и равными кф — скриншоты не нужны.'], []
        lines = [f"{i + 1}) {notify.esc(x['t'])} {notify.esc(x['tour'])} · {notify.esc(x['match'])} — "
                 f"П1 выгодно от {_f(x['kmin1'])} · П2 от {_f(x['kmin2'])}" for i, x in enumerate(q)]
        msgs.append(f'🔎 <b>Проверка ставки дня · {len(q)} {notify.plural(len(q), "матч", "матча", "матчей")}</b>\n'
                    + '\n'.join(lines) + '\nПрогноз на сутки и ставка дня — после скриншотов (не позднее 14:00).')
        msgs.append(_request(q, 0))
        return 'wait', msgs, []
    if r.get('state') == 'done':
        return 'done', [], []
    # скриншоты: П1/П2 матчей очереди
    ids = {x['id']: x for x in r['queue']}
    for o in shot_offers:
        if o.get('market') == 'ML' and str(o.get('espn_id')) in ids and o.get('k'):
            g = r['got'].setdefault(str(o['espn_id']), [None, None])
            g[0 if str(o['pick']) == '1' else 1] = float(o['k'])
            r['offers'] = [x for x in r['offers'] if not (x['espn_id'] == o['espn_id'] and x['pick'] == o['pick'])]
            r['offers'].append({k: (str(v) if k == 'start' else v) for k, v in o.items()})
    got_now = [x for x in r['queue'] if x['id'] in r['got'] and not x.get('replied')]
    for x in got_now:
        k = r['got'][x['id']]
        msgs.append('✅ Принят: ' + notify.esc(_edge_row(x, k if None not in k else None)))
        x['replied'] = True
    if got_now:                                                 # общий ответ shots заменён коротким
        replies[:] = [y for y in replies if not any(x['match'].split(' — ')[0] in y for x in got_now)]
    nxt = _next(r, now)
    late = loc.hour >= DEADLINE_HOUR or (nxt is not None and pd.Timestamp(r['queue'][nxt]['start']) - now < pd.Timedelta(minutes=15))
    if nxt is not None and not late:
        if got_now:
            msgs.append(_request(r['queue'], nxt))
        return 'wait', msgs, []
    r['state'] = 'done'
    if any(x['id'] not in r['got'] for x in r['queue']):
        r['misses'] = int(r.get('misses', 0)) + 1
        if r['misses'] >= 3:
            msgs.append('⚠️ Скриншоты не пришли к сроку третий день подряд — ставка дня выбиралась без свежих кф 1xBet.')
    else:
        r['misses'] = 0
    table = '\n'.join(notify.esc(_edge_row(x, r['got'].get(x['id']) if None not in r['got'].get(x['id'], [None]) else None))
                      for x in r['queue'])
    msgs.append('📊 <b>Проверка ставки дня — итог</b> (перевес = P Pinnacle × кф 1xBet − 1; ставка — от +3% при кф 1,70–2,50)\n' + table)
    offers = []
    for o in r['offers']:
        x = dict(o)
        x['start'] = pd.Timestamp(x['start_utc'])
        offers.append(x)
    return 'done_now', msgs, offers
