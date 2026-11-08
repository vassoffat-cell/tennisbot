"""⭐ Ставка дня (решение пользователя 08.11.2026): одна самая уверенная линия на матчи текущих суток.

  • Кандидаты — показанные линии (shown = 1: сторона модели, справедливый кф ≥ 1,25) на матчи, которые начинаются
    сегодня (местная дата) и ещё не начались; рынки под ⛔ не участвуют.
  • Выбор — наибольшая вероятность после самокалибровки; при равенстве — выше P_low, затем раньше матч.
  • Публикация — после суточного отчёта (первый запуск после 00:00, не позднее 08:00), отдельным сообщением, закреплённым.
  • Замена — только до 17:00, только если матч ставки не начался и новая линия лучше на ≥ 2 п.п.; сообщение правится на месте.
  • Новые сутки — старое сообщение откреплено и удалено, новое закреплено. Итог — в листе day_pick и в отчётах.
Бэктест на футболе (research/daypick_bt.py, 693 дня): при P ≤ 0,80 точность 78,4% при средней P 79,4% — ставка дня
заходит примерно с той вероятностью, которую ей даёт модель (теннис — та же логика, проверка по ходу).
"""
from __future__ import annotations

import pandas as pd

import config as C
import lines as FL
import journal as J
import notify


def _local_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz='UTC').tz_localize(None) + pd.Timedelta(hours=C.TZ_OFFSET_HOURS)


TITLE = None          # autorun передаёт функцию подписи линии (с фамилиями игроков)


def candidates(items: list[tuple], off: dict, day) -> list[dict]:
    now = pd.Timestamp.now(tz='UTC').tz_localize(None)
    out = []
    for m, row, lns in items:
        if m.start <= now or (m.start + pd.Timedelta(hours=C.TZ_OFFSET_HOURS)).date() != day:
            continue
        for x in lns:
            if int(x.get('shown', 0)) != 1 or x['market'] in off:
                continue
            out.append(dict(espn_id=m.id, slot=int(x['slot']), tour=row['tour'], event=row['event'], start_utc=row['start_utc'],
                            start_local=row['start_local'], player1=row['player1'], player2=row['player2'], market=x['market'],
                            label=TITLE(row, x) if TITLE else x['label'], p=float(x['p']), p_low=float(x['p_low']),
                            start=m.start))
    out.sort(key=lambda c: (-c['p'], -c['p_low'], c['start']))
    return out


def text(c: dict | None, day, hist: list[str], score: str, reason: str = '') -> str:
    e = notify.esc
    head = f'⭐ <b>Ставка дня · теннис · {day:%d.%m}</b>'
    if c is None:
        return f'{head}\nСегодня ставки дня нет: {e(reason)}.' + (f'\n{score}' if score else '')
    tier = FL.tier(c['p_low'])
    lab = e(f"{c['label']} — {c['p'] * 100:.0f}%")
    lab = {3: f'🟢 <b>{lab}</b>', 2: f'🟡 {lab}', 1: f'⚪ <i>{lab}</i>'}[tier]
    out = [head, f"🏆 {e(c['tour'])} · {e(c['event'])} · 🕒 {e(c['start_local'][6:])}",
           f"<b>{e(c['player1'])} — {e(c['player2'])}</b>", lab,
           f"Справедливый кф {1 / c['p']:.2f}".replace('.', ',') + f" · уверенность {['', 'низкая', 'средняя', 'высокая'][tier]}"]
    out += [e(h) for h in hist]
    if score:
        out.append(score)
    return '\n'.join(out)


def score_line(dp: pd.DataFrame) -> str:
    d = dp[dp.result.astype(str).isin(['W', 'L'])] if len(dp) else dp
    if not len(d):
        return ''
    w = int((d.result.astype(str) == 'W').sum())
    return f'📈 Ставки дня: {w}/{len(d)} ({w / len(d) * 100:.0f}%)'


def process(rt: dict, store: J.Store, items: list[tuple], off: dict, daily_done: bool, dry: bool = False) -> str:
    """Публикация / замена ставки дня. rt['day_pick'] = {date, espn_id, slot, p, label, start_utc, msgs, changes, history, row}."""
    loc = _local_now()
    day = loc.date(); dstr = day.isoformat()
    cur = rt.get('day_pick') or {}
    cands = candidates(items, off, day)
    dp = store.read('day_pick')
    if cur.get('date') != dstr:
        if not (daily_done or loc.hour >= 8):
            return 'ставка дня: ждёт суточного отчёта'
        if dry:
            print(text(cands[0] if cands else None, day, [], '', 'нет подходящих линий'))
            return 'dry'
        if cur.get('msgs'):
            notify.pin(cur['msgs'], on=False)
            notify.delete([tuple(x) for x in cur['msgs']])
        best = cands[0] if cands else None
        msgs = notify.send_blocks([text(best, day, [], score_line(dp), 'на сегодня нет матчей с линиями в допустимом диапазоне')])
        notify.pin(msgs)
        rt['day_pick'] = dict(date=dstr, msgs=[list(x) for x in msgs], changes=0, history=[])
        if best:
            rt['day_pick'].update(espn_id=best['espn_id'], slot=best['slot'], p=best['p'], label=best['label'],
                                  start_utc=best['start_utc'], match=f"{best['player1']} — {best['player2']}")
            row = {k: best[k] for k in ('espn_id', 'slot', 'tour', 'event', 'start_utc', 'start_local', 'player1',
                                         'player2', 'market', 'label', 'p', 'p_low')}
            store.append('day_pick', [dict(date=dstr, **row, published=J.now_utc(), changes=0, history='')])
        return f"ставка дня: {best['label'] + ' ' + best['player1'] + ' — ' + best['player2'] if best else 'нет'}"
    # те же сутки: замена до 17:00, если матч текущей ставки не начался и новая лучше на ≥ 2 п.п.
    if loc.hour >= C.DAY_PICK_LOCK_HOUR or not cands:
        return 'ставка дня: без изменений'
    now = pd.Timestamp.now(tz='UTC').tz_localize(None)
    if cur.get('espn_id'):
        if pd.Timestamp(cur['start_utc']) <= now:
            return 'ставка дня: матч начался — замок'
        same = [c for c in cands if c['espn_id'] == cur['espn_id'] and c['slot'] == int(cur['slot'])]
        p_cur = same[0]['p'] if same else float(cur['p'])
        best = cands[0]
        if (best['espn_id'], best['slot']) == (cur['espn_id'], int(cur['slot'])) or best['p'] < p_cur + C.DAY_PICK_SWITCH:
            return 'ставка дня: без изменений'
    else:
        best = cands[0]                                   # утром кандидатов не было — появились до 17:00
    if dry:
        return 'dry'
    was = (f"было {cur.get('label', '—')} {cur.get('p', 0) * 100:.0f}% ({cur.get('match', '')})" if cur.get('espn_id')
           else 'было: ставки не было')
    hist = cur.get('history', []) + [f'изменено в {loc:%H:%M}: {was}']
    notify.edit(cur.get('msgs', []), text(best, day, hist, score_line(dp)))
    cur.update(espn_id=best['espn_id'], slot=best['slot'], p=best['p'], label=best['label'], start_utc=best['start_utc'],
               match=f"{best['player1']} — {best['player2']}", changes=int(cur.get('changes', 0)) + 1, history=hist)
    rt['day_pick'] = cur
    row = dict(date=dstr, **{k: best[k] for k in ('espn_id', 'slot', 'tour', 'event', 'start_utc', 'start_local', 'player1',
                                                   'player2', 'market', 'label', 'p', 'p_low')},
               published=J.now_utc(), changes=cur['changes'], history=' | '.join(hist))
    idx = dp.index[dp.date.astype(str) == dstr]
    if len(idx):
        store.update('day_pick', pd.DataFrame([row], index=[idx[-1]]), J.DAY_COLS[:J.DAY_COLS.index('history') + 1])
    else:
        store.append('day_pick', [row])
    return f"ставка дня: замена → {best['label']} {best['player1']} — {best['player2']}"


def settle(store: J.Store) -> int:
    """Итог ставки дня — из рассчитанной линии auto_lines (тот же матч и слот)."""
    dp = store.read('day_pick')
    todo = dp[dp.result.map(lambda x: str(x).strip() in ('', 'nan'))]
    if todo.empty:
        return 0
    ld = store.read('auto_lines')
    res = {(str(J.num(e)), str(J.num(s))): (st, r, se) for e, s, st, r, se in zip(ld.espn_id, ld.slot, ld.status, ld.result, ld.settled)
           if str(r) in ('W', 'L', 'P')}
    upd = []
    for i, r in todo.iterrows():
        x = res.get((str(J.num(r.espn_id)), str(J.num(r.slot))))
        if x:
            dp.loc[i, ['status', 'result', 'settled']] = list(x)
            upd.append(i)
    store.update('day_pick', dp.loc[upd], ['status', 'result', 'settled'])
    return len(upd)
