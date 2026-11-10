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
    """ВСЕ ещё не начавшиеся матчи суток (решение пользователя 11.11.2026): у каждого — цена Pinnacle на исход, если есть
    (тогда «выгодно от X»), и вероятность модели; скриншоты просятся по всем матчам по порядку начала."""
    now = _now()
    todays = [(m, row) for m, row, _ in items if m.start > now and C.wday(m.start + pd.Timedelta(hours=C.TZ_OFFSET_HOURS)) == day]
    if not todays:
        return []
    try:
        keys = [s['key'] for s in OD.sports() if s['active'] and s['group'] == 'Tennis']
        events, _ = OD.day_events('tennis', keys, ('h2h', 'spreads', 'totals'), day.isoformat(), refresh_hours=24)
    except Exception:
        events = []
    out = []
    for m, row in sorted(todays, key=lambda x: x[0].start):
        ev, _ = OD.find_event(events, row['player1'], row['player2'], row['start_utc']) if events else (None, False)
        pin = {}
        for (mk, name, _pt), books in (OD.quotes(ev).items() if ev else []):
            if mk == 'h2h' and books.get('pinnacle'):
                s = '1' if OD.same_name(name, row['player1']) >= OD.same_name(name, row['player2']) else '2'
                pin[s] = books['pinnacle']
        p1 = p2 = None
        if '1' in pin and '2' in pin:
            p1, p2 = PK.shin([pin['1'], pin['2']])
        out.append(dict(id=str(m.id), t=str(row['start_local'])[6:], tour=row['tour'], event=row['event'],
                        match=f"{row['player1']} — {row['player2']}", p1=p1, p2=p2, pm=float(row.get('p1') or 0.5),
                        kmin1=(1 + PK.VALUE_EDGE) / p1 if p1 else None, kmin2=(1 + PK.VALUE_EDGE) / p2 if p2 else None,
                        start=str(row['start_utc'])))
    return out


def _request(q: list[dict], i: int) -> str:
    x = q[i]
    return (f"📸 <b>Скриншот {i + 1} из {len(q)}</b>: {notify.esc(x['match'])} · {notify.esc(x['t'])}\n"
            'Страница матча 1xBet, вкладка «Основная игра»: блок <b>1X2 (П1/П2)</b> и <b>Тотал</b> (можно 2 скриншота).')


def _next(r: dict, now: pd.Timestamp) -> int | None:
    for i, x in enumerate(r['queue']):
        if x['id'] not in r['got'] and pd.Timestamp(x['start']) > now + pd.Timedelta(minutes=15):
            return i
    return None


def _edge_row(x: dict, k: list | None) -> str:
    if not k:
        return f"{x['match']}: скриншота нет"
    if not x.get('p1'):                                         # цены Pinnacle нет — 1xBet без маржи и модель
        q = PK.shin([k[0], k[1]])
        return (f"{x['match']}: П1 {_f(k[0])} · П2 {_f(k[1])} — 1xBet без маржи П1 {q[0] * 100:.0f}%, "
                f"модель {x['pm'] * 100:.0f}% (Pinnacle нет — перевес не измерить)")
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
    if r.get('date') != day.isoformat() or r.get('v') != 2:  # новые сутки (или новая версия цикла) — список и первый запрос
        misses = int((rt.get('r4') or {}).get('misses', 0))
        q = candidates(items, day)
        r = dict(date=day.isoformat(), queue=q, got={}, offers=[], state='wait', misses=misses, asked_at=str(now), v=2)
        rt['r4'] = r
        if not q:
            r['state'] = 'done'
            return 'done_now', ['🔎 <b>Проверка ставки дня</b>: матчей до конца суток нет.'], []
        lines = [f"{i + 1}) {notify.esc(x['t'])} {notify.esc(x['tour'])} · {notify.esc(x['match'])}"
                 + (f" — П1 выгодно от {_f(x['kmin1'])} · П2 от {_f(x['kmin2'])}" if x['p1'] else '') for i, x in enumerate(q)]
        msgs.append(f'🔎 <b>Проверка ставки дня · {len(q)} {notify.plural(len(q), "матч", "матча", "матчей")}</b>\n'
                    + '\n'.join(lines) + '\nПрогноз на сутки и ставка дня — после скриншотов (не позднее 14:00). '
                    '«Выгодно от» — где есть цена Pinnacle.')
        msgs.append(_request(q, 0))
        return 'wait', msgs, []
    if r.get('state') == 'done':
        return 'done', [], []
    # скриншоты: П1/П2 матчей очереди
    ids = {x['id']: x for x in r['queue']}
    for o in shot_offers:
        if str(o.get('espn_id')) in ids and o.get('k'):
            r['offers'] = [x for x in r['offers'] if not (x['espn_id'] == o['espn_id'] and x['market'] == o['market']
                                                          and str(x['pick']) == str(o['pick']) and x.get('line') == o.get('line'))]
            r['offers'].append({k: (str(v) if k == 'start' else v) for k, v in o.items()})
        if o.get('market') == 'ML' and str(o.get('espn_id')) in ids and o.get('k'):
            g = r['got'].setdefault(str(o['espn_id']), [None, None])
            g[0 if str(o['pick']) == '1' else 1] = float(o['k'])
    got_now = [x for x in r['queue'] if x['id'] in r['got'] and not x.get('replied')]
    for x in got_now:
        k = r['got'][x['id']]
        msgs.append('✅ Принят: ' + notify.esc(_edge_row(x, k if None not in k else None)))
        x['replied'] = True
    if got_now:                                                 # общий ответ shots заменён коротким
        replies[:] = [y for y in replies if not any(x['match'].split(' — ')[0] in y for x in got_now)]
    nxt = _next(r, now)
    late = DEADLINE_HOUR <= loc.hour and C.wday(loc) == pd.Timestamp(r['date']).date()      # 14:00 своих суток
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
    msgs.append('📊 <b>Проверка ставки дня — итог</b> (перевес = P Pinnacle × кф 1xBet − 1; ставка с перевесом — от +3% при кф 1,70–2,50; '
                'без цены Pinnacle — ставка дня по запасному правилу с точными кф скриншотов)\n' + table)
    offers = []
    for o in r['offers']:
        x = dict(o)
        x['start'] = pd.Timestamp(x['start_utc'])
        offers.append(x)
    return 'done_now', msgs, offers
