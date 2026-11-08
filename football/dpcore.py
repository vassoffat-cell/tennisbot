"""⭐ Ставка дня — общий цикл публикации для обоих ботов (копия — football/dpcore.py). Пакет №3, правило C.

Состояние rt['day_pick']: date, msgs (закреплённое), ask (сообщение «Принимаем?»), espn_id, slot, key, p, tier, label, start_utc,
match, changes, history, accepted ('' | 'yes'), rejected [ключи], shots [предложения со скриншотов за день], screenshots, text.

События и реакция:
  • новые сутки — публикация лучшей ставки (после суточного отчёта, не позднее 08:00), закрепление, кнопки ✅ / 🔄;
  • ✅ «Принимаем» (кнопка; бот пишет в лист dp_actions) — ставка зафиксирована, бот её больше сам не меняет;
  • 🔄 «Другую» — ставка в «отказанные», выбирается следующая лучшая (в этом или другом матче);
  • скриншот — кф 1xBet по его линиям становятся точными; выбор пересчитывается сразу;
  • сам бот (новые котировки / модель) меняет ставку только до 17:00, если она не принята и новая выше по ступени
    или лучше по P на ≥ 2 п.п.
Каждое изменение: правка закреплённого сообщения + новое «Принимаем?» (старое удаляется) + обновлённый список (autorun).
"""
from __future__ import annotations

from typing import Callable

import pandas as pd

import config as C
import journal as J
import notify
import pick as PK

KB = {'inline_keyboard': [[{'text': '✅ Принимаем', 'callback_data': 'dp:yes'}, {'text': '🔄 Другую', 'callback_data': 'dp:no'}]]}
PRIORITY = {'screenshot': 0, 'api': 1, 'model': 2}


def _local_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz='UTC').tz_localize(None) + pd.Timedelta(hours=C.TZ_OFFSET_HOURS)


def key(c: dict) -> list:
    return [str(c['espn_id']), c['market'], str(c['pick']), '' if c.get('line') in (None, '') else f"{float(c['line']):g}"]


def _plain(o: dict) -> dict:
    """Предложение для хранения в runtime.json (без Timestamp и вспомогательных полей)."""
    return {k: (None if (isinstance(v, float) and v != v) else v) for k, v in o.items()
            if k not in ('start', 'conf', 'ev', 'value_ok', 'tier')}


def _revive(o: dict) -> dict:
    x = dict(o)
    x['start'] = pd.Timestamp(x['start_utc'])
    return x


def _dedupe(offers: list[dict]) -> list[dict]:
    """Одна запись на линию: скриншот (точный кф) > котировки API > модель."""
    best: dict[tuple, dict] = {}
    for o in offers:
        k = tuple(key(o))
        if k not in best or PRIORITY.get(o.get('source'), 9) < PRIORITY.get(best[k].get('source'), 9):
            best[k] = o
    return list(best.values())


def process(rt: dict, store: J.Store, items: list[tuple], off: dict, daily_done: bool, *,
            offers_fn: Callable, text_fn: Callable, row_fn: Callable, slot_fn: Callable, match_fn: Callable,
            score_fn: Callable, shots: list[dict] | None = None, actions: list[str] | None = None, dry: bool = False) -> str:
    loc = _local_now()
    day = loc.date(); dstr = day.isoformat()
    cur = rt.get('day_pick') or {}
    if cur.get('date') != dstr:
        cur = {}
    log: list[str] = []
    dp = store.read('day_pick')
    force, reason = False, ''
    if not cur.get('date'):                                     # новые сутки: прошлое сообщение — открепить и удалить
        if not (daily_done or loc.hour >= 8) and not shots:
            return 'ставка дня: ждёт суточного отчёта'
        old = rt.get('day_pick') or {}
        if not dry and old.get('msgs'):
            notify.pin(old['msgs'], on=False)
            notify.delete([tuple(x) for x in old['msgs']] + [tuple(x) for x in old.get('ask', [])])
        cur = dict(date=dstr, msgs=[], ask=[], changes=0, history=[], accepted='', rejected=[], shots=[], screenshots=0)

    # ---- действия пользователя (кнопки) — только для текущих суток
    for a in (actions or []):
        if not cur.get('espn_id'):
            continue
        if a == 'yes' and cur.get('accepted') != 'yes':
            cur['accepted'] = 'yes'
            cur['accepted_at'] = f'{loc:%H:%M}'
            force, reason = False, ''
        elif a == 'no':
            cur.setdefault('rejected', []).append(list(cur.get('key', [])))
            cur['accepted'] = ''
            force, reason = True, 'отказ'
    # ---- скриншоты дня
    if shots:
        known = {tuple(key(o)): o for o in cur.get('shots', [])}
        for o in shots:
            known[tuple(key(o))] = _plain(o)
        cur['shots'] = list(known.values())
        cur['screenshots'] = int(cur.get('screenshots', 0)) + 1
        force, reason = True, 'скриншот'

    now = pd.Timestamp.now(tz='UTC').tz_localize(None)
    started = bool(cur.get('espn_id')) and pd.Timestamp(cur['start_utc']) <= now
    if cur.get('accepted') == 'yes' and cur.get('msgs') and not force:
        if cur.get('accepted_at') and not cur.get('accepted_shown'):
            cur['text'] = str(cur.get('text', '')).split('\n✅ Принята')[0] + f"\n✅ Принята в {cur['accepted_at']}"
            if not dry:
                notify.edit(cur['msgs'], cur['text'], None)
                notify.delete([tuple(x) for x in cur.get('ask', [])])
                _sheet_accept(store, dp, dstr, cur)
            cur['ask'], cur['accepted_shown'] = [], True
        rt['day_pick'] = cur
        return 'ставка дня: принята'
    if started and not force:
        rt['day_pick'] = cur
        return 'ставка дня: матч начался — замок'
    if cur.get('msgs') and not force and loc.hour >= C.DAY_PICK_LOCK_HOUR:
        rt['day_pick'] = cur
        return 'ставка дня: без изменений (после 17:00)'

    rejected = {tuple(k) for k in cur.get('rejected', [])}
    cands = offers_fn(items, off, day, log) + [_revive(o) for o in cur.get('shots', []) if pd.Timestamp(o['start_utc']) > now]
    cands = [o for o in PK.choose(_dedupe(cands)) if tuple(key(o)) not in rejected and o['market'] not in off]
    for c in cands:
        c['slot'] = slot_fn(c, items)
    note = ('; ' + '; '.join(log)) if log else ''
    best = cands[0] if cands else None

    if cur.get('msgs') and not force:                         # плановая проверка: менять ли самому
        if not best:
            rt['day_pick'] = cur
            return 'ставка дня: без изменений' + note
        same = [c for c in cands if key(c) == cur.get('key')]
        p_cur, t_cur = (same[0]['p'], same[0]['tier']) if same else (float(cur.get('p', 0)), 9)
        if key(best) == cur.get('key') or not (best['tier'] < t_cur or best['p'] >= p_cur + C.DAY_PICK_SWITCH):
            rt['day_pick'] = cur
            return 'ставка дня: без изменений' + note
        reason = 'лучше'
    if dry:
        print(text_fn(best, day, [], '', 'нет подходящих линий'))
        return 'dry'

    hist = list(cur.get('history', []))
    first = not cur.get('msgs')
    if not first and best and key(best) == cur.get('key'):
        cur['best'] = _plain(best)                            # та же ставка — обновить кф (скриншот уточнил)
        cur['text'] = text_fn(best, day, hist, score_fn(dp))
        notify.edit(cur['msgs'], cur['text'], KB)
        rt['day_pick'] = cur
        _sheet_row(store, dp, dstr, row_fn(best, dstr, int(cur.get('changes', 0)), hist), cur)
        return f"ставка дня: кф уточнён ({reason}){note}"
    if not first:
        was = f"было {cur.get('label', '—')} {float(cur.get('p', 0)) * 100:.0f}% ({cur.get('match', '')})" if cur.get('espn_id') \
            else 'было: ставки не было'
        hist.append(f"{ {'отказ': 'отказ', 'скриншот': 'по скриншоту', 'лучше': 'изменено'}.get(reason, 'изменено') } в {loc:%H:%M}: {was}".strip())
    reason_txt = {'отказ': 'по вашему отказу', 'скриншот': 'по скриншоту', 'лучше': 'найдена лучше'}.get(reason, '')
    cur['text'] = text_fn(best, day, hist, score_fn(dp), 'нет линий с кф 1xBet ≥ 1,35 и вероятностью ≥ 60% — пришлите скриншот 1xBet '
                          'любого матча из списка')
    if first:
        cur['msgs'] = [list(x) for x in notify.send_markup(cur['text'], KB if best else None)]
        notify.pin(cur['msgs'])
    else:
        notify.edit(cur['msgs'], cur['text'], KB if best else None)
        notify.delete([tuple(x) for x in cur.get('ask', [])])
        ask = (f'🔁 <b>Ставка дня изменена</b> ({reason_txt}): <b>{notify.esc(best["label"])}</b> · '
               f'{notify.esc(match_fn(best))} — {best["p"] * 100:.0f}%, {notify.esc(PK.k_text(best))}\nПринимаем ставку дня?'
               if best else f'🔁 Других линий с кф ≥ 1,35 нет ({reason_txt}). Пришлите скриншот 1xBet любого матча из списка.')
        cur['ask'] = [list(x) for x in notify.send_markup(ask, KB if best else None)]
    cur['changes'] = int(cur.get('changes', 0)) + (0 if first else 1)
    cur['history'], cur['accepted'] = hist, ''
    if best:
        cur.update(espn_id=best['espn_id'], slot=best['slot'], key=key(best), p=best['p'], tier=best['tier'], label=best['label'],
                   start_utc=best['start_utc'], match=match_fn(best), best=_plain(best))
        _sheet_row(store, dp, dstr, row_fn(best, dstr, cur['changes'], hist), cur)
    rt['day_pick'] = cur
    return (f"ставка дня: {'публикация' if first else 'замена (' + reason + ')'} → "
            f"{best['label'] + ' (ступень ' + str(best['tier']) + ')' if best else 'нет'}{note}")


def _sheet_row(store: J.Store, dp: pd.DataFrame, dstr: str, row: dict, cur: dict) -> None:
    """Строка суток в листе day_pick: новая или обновлённая целиком (запись идёт сплошным диапазоном столбцов)."""
    row.update(screenshots=int(cur.get('screenshots', 0)), accepted=cur.get('accepted', ''))
    idx = dp.index[dp.date.astype(str) == dstr] if len(dp) else []
    if len(idx):
        old = {c: dp.at[idx[-1], c] if c in dp.columns else '' for c in J.DAY_COLS}
        old.update({k: v for k, v in row.items() if k in J.DAY_COLS})
        store.update('day_pick', pd.DataFrame([old], index=[idx[-1]]), J.DAY_COLS)
    else:
        store.append('day_pick', [row])


def _sheet_accept(store: J.Store, dp: pd.DataFrame, dstr: str, cur: dict) -> None:
    idx = dp.index[dp.date.astype(str) == dstr] if len(dp) else []
    if len(idx):
        store.update('day_pick', pd.DataFrame([dict(accepted='yes', accepted_at=cur.get('accepted_at', ''))], index=[idx[-1]]),
                     ['accepted', 'accepted_at'])


def read_actions(store: J.Store) -> list[str]:
    """Нажатия ✅ / 🔄 (лист dp_actions пишет бот) — новые, по порядку; помечаются обработанными."""
    try:
        df = store.read('dp_actions')
    except Exception:
        return []
    if not len(df):
        return []
    todo = df[df.done.astype(str).str.strip().isin(['', 'nan'])]
    if todo.empty:
        return []
    df.loc[todo.index, 'done'] = J.now_utc()
    store.update('dp_actions', df.loc[todo.index], ['done'])
    return [str(a).strip() for a in todo.action]
