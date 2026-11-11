"""⭐ Ставка дня — общий цикл публикации для обоих ботов (копия — football/dpcore.py). Пакет №3, ТЗ-004, синтез с каперами (11.11).

Состояние rt['day_pick']: date, msgs (закреплённое), ask (сообщение «Принимаем?»), espn_id, slot, key, p, tier, label, start_utc,
match, changes, history, accepted ('' | 'yes'), rejected [ключи], shots [предложения со скриншотов за день], screenshots, text,
extras [доп. ставки ⭐2/⭐3], log [журнал смен].

События и реакция:
  • новые сутки (08:00 → 08:00) — публикация лучшей ставки первым сообщением, закрепление, кнопки ✅ / 🔄;
  • ✅ «Принимаем» — ставка зафиксирована, бот её больше сам не меняет;
  • 🔄 «Другую» — ставка в «отказанные», выбирается следующая лучшая;
  • скриншот — кф 1xBet по его линиям становятся точными; выбор пересчитывается сразу;
  • модель / котировки — замена до 17:00, если новая выше по ступени или лучше по P на ≥ 2 п.п.;
  • 👥 каперы (решение пользователя 11.11.2026) — замена В ЛЮБОЕ ВРЕМЯ суток до начала матча ставки:
      конфликт ⚔️ у текущей ставки и есть кандидат без конфликта; или кандидат 🔥 (модель + капер) при текущей без 🔥
      и P не ниже текущей более чем на 3 п.п.
Каждая смена — строка в листе dp_changes (что было, на что, причина, время); заменённая ставка тоже получает итог →
отчёт «🔁 смены: права итоговая / права первая» — частые смены под внешними факторами видны цифрами.
Доп. ставки (до 3 в сутки): уверенные кандидаты других матчей — ступень 0–1 (перевес / уверенность ≥ 90%) или 🔥, без ⚔️;
строки day_pick с rank 2/3; до начала своего матча обновляются, после — замок.
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
MAX_PICKS = 3
EXTRA_MARK = '\n\n⭐ <b>Ещё уверенные</b>'
REASON = {'отказ': 'по вашему отказу', 'скриншот': 'по скриншоту', 'лучше': 'найдена лучше',
          'каперы': 'по каперам (🔥/⚔️)'}


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


def confident(c: dict) -> bool:
    """Уверенная доп. ставка: перевес над Pinnacle / уверенность ≥ 90% (ступень 0–1) или 🔥 модель + капер; без ⚔️."""
    return (int(c.get('tier', 9)) <= 1 or bool(c.get('fire'))) and not c.get('clash')


def _syn(c: dict) -> str:
    return ' 🔥' if c.get('fire') else ' ⚔️' if c.get('clash') else ''


def _extras_text(cur: dict, match_fn: Callable) -> str:
    ex = cur.get('extras') or []
    if not ex:
        return ''
    lines = [f"⭐{n + 2} <b>{notify.esc(x['label'])}</b>{_syn(x)} · {notify.esc(match_fn(x))} — {float(x['p']) * 100:.0f}%"
             + (f" · кф {float(x['k']):.2f}".replace('.', ',') if x.get('k') else '') for n, x in enumerate(ex)]
    return EXTRA_MARK + '\n' + '\n'.join(lines)


def _compose(base: str, cur: dict, match_fn: Callable) -> str:
    return str(base).split(EXTRA_MARK)[0] + _extras_text(cur, match_fn)


def _log_change(store: J.Store, cur: dict, loc, reason: str, old: dict, new: dict | None, rank: int = 1) -> None:
    """Строка dp_changes: заменённая ставка (получит итог) + на что заменили и почему."""
    if not old or not old.get('espn_id'):
        return
    row = dict(date=cur['date'], time=f'{loc:%H:%M}', reason=reason, rank=rank, espn_id=old['espn_id'], market=old.get('market', ''),
               pick=old.get('pick', ''), line='' if old.get('line') in (None, '') else old['line'], label=old.get('label', ''),
               match=old.get('match', f"{old.get('home', '')} — {old.get('away', '')}"), p=round(float(old.get('p', 0) or 0), 4),
               k=old.get('k') or '', to_label=(new or {}).get('label', '—'), to_key='|'.join(key(new)) if new else '',
               slot=old.get('slot', 0))
    cur.setdefault('log', []).append(row)
    try:
        store.append('dp_changes', [row])
    except Exception as e:
        print(f'dp_changes: не записано ({e})')


def process(rt: dict, store: J.Store, items: list[tuple], off: dict, daily_done: bool, *,
            offers_fn: Callable, text_fn: Callable, row_fn: Callable, slot_fn: Callable, match_fn: Callable,
            score_fn: Callable, shots: list[dict] | None = None, actions: list[str] | None = None, dry: bool = False) -> str:
    loc = _local_now()
    day = C.wday(loc); dstr = day.isoformat()                   # сутки системы 08:00 → 08:00
    cur = rt.get('day_pick') or {}
    if cur.get('date') != dstr:
        cur = {}
    log: list[str] = []
    dp = store.read('day_pick')
    force, reason = False, ''
    if not cur.get('date'):                                     # новые сутки: прошлое сообщение — открепить и удалить
        old = rt.get('day_pick') or {}
        if not dry and old.get('msgs'):
            notify.pin(old['msgs'], on=False)
            notify.delete([tuple(x) for x in old['msgs']] + [tuple(x) for x in old.get('ask', [])])
        cur = dict(date=dstr, msgs=[], ask=[], changes=0, history=[], accepted='', rejected=[], shots=[], screenshots=0,
                   extras=[], log=[])

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
    accepted = cur.get('accepted') == 'yes' and cur.get('msgs') and not force
    if accepted and cur.get('accepted_at') and not cur.get('accepted_shown'):
        base = str(cur.get('text', '')).split(EXTRA_MARK)[0].split('\n✅ Принята')[0]
        cur['text'] = _compose(base + f"\n✅ Принята в {cur['accepted_at']}", cur, match_fn)
        if not dry:
            notify.edit(cur['msgs'], cur['text'], None)
            notify.delete([tuple(x) for x in cur.get('ask', [])])
            _sheet_accept(store, dp, dstr, cur)
        cur['ask'], cur['accepted_shown'] = [], True

    rejected = {tuple(k) for k in cur.get('rejected', [])}
    cands = offers_fn(items, off, day, log) + [_revive(o) for o in cur.get('shots', []) if pd.Timestamp(o['start_utc']) > now]
    cands = [o for o in PK.choose(_dedupe(cands)) if tuple(key(o)) not in rejected and o['market'] not in off]
    for c in cands:
        c['slot'] = slot_fn(c, items)
    note = ('; ' + '; '.join(log)) if log else ''
    best = cands[0] if cands else None

    if accepted or started:                                    # главная ставка зафиксирована — доп. ставки живут сами
        msg = _extras(rt, store, dp, cur, cands, items, loc, now, match_fn, row_fn, dry)
        if msg and cur.get('msgs'):
            cur['text'] = _compose(cur.get('text', ''), cur, match_fn)
            notify.edit(cur['msgs'], cur['text'], None if accepted or started else KB)
        rt['day_pick'] = cur
        return ('ставка дня: принята' if accepted else 'ставка дня: матч начался — замок') + msg + note

    if cur.get('msgs') and not force:                         # плановая проверка: менять ли самому
        if not best:
            rt['day_pick'] = cur
            return 'ставка дня: без изменений' + note
        same = [c for c in cands if key(c) == cur.get('key')]
        p_cur, t_cur = (same[0]['p'], same[0]['tier']) if same else (float(cur.get('p', 0)), 9)
        fire_cur, clash_cur = bool(same and same[0].get('fire')), bool(same and same[0].get('clash'))
        other = key(best) != cur.get('key')
        capper = other and ((clash_cur and not best.get('clash'))
                            or (best.get('fire') and not fire_cur and best['p'] >= p_cur - 0.03))
        model = other and C.DAY_START_HOUR <= loc.hour < C.DAY_PICK_LOCK_HOUR and \
            (best['tier'] < t_cur or best['p'] >= p_cur + C.DAY_PICK_SWITCH)
        if not (capper or model):
            msg = _extras(rt, store, dp, cur, cands, items, loc, now, match_fn, row_fn, dry, main_key=cur.get('key'))
            if same and not dry and (_changed(same[0], cur.get('best') or {}) or msg):
                same[0]['slot'] = slot_fn(same[0], items)
                cur['best'] = _plain(same[0]); cur['p'], cur['tier'] = same[0]['p'], same[0]['tier']
                cur['text'] = _compose(text_fn(same[0], day, cur.get('history', []), score_fn(dp)), cur, match_fn)
                notify.edit(cur['msgs'], cur['text'], KB)
                _sheet_row(store, dp, dstr, row_fn(same[0], dstr, int(cur.get('changes', 0)), cur.get('history', [])), cur, same[0])
                rt['day_pick'] = cur
                return 'ставка дня: цифры пересчитаны' + msg + note
            if msg and cur.get('msgs'):
                cur['text'] = _compose(cur.get('text', ''), cur, match_fn)
                notify.edit(cur['msgs'], cur['text'], KB)
            rt['day_pick'] = cur
            return 'ставка дня: без изменений' + msg + note
        reason = 'каперы' if capper else 'лучше'
    if dry:
        print(text_fn(best, day, [], '', 'нет подходящих линий'))
        return 'dry'

    hist = list(cur.get('history', []))
    first = not cur.get('msgs')
    if not first and best and key(best) == cur.get('key'):
        cur['best'] = _plain(best)                            # та же ставка — обновить кф (скриншот уточнил)
        _extras(rt, store, dp, cur, cands, items, loc, now, match_fn, row_fn, dry, main_key=key(best))
        cur['text'] = _compose(text_fn(best, day, hist, score_fn(dp)), cur, match_fn)
        notify.edit(cur['msgs'], cur['text'], KB)
        rt['day_pick'] = cur
        _sheet_row(store, dp, dstr, row_fn(best, dstr, int(cur.get('changes', 0)), hist), cur, best)
        return f"ставка дня: кф уточнён ({reason}){note}"
    if not first:
        was = f"было {cur.get('label', '—')} {float(cur.get('p', 0)) * 100:.0f}% ({cur.get('match', '')})" if cur.get('espn_id') \
            else 'было: ставки не было'
        word = {'отказ': 'отказ', 'скриншот': 'по скриншоту', 'лучше': 'изменено', 'каперы': 'по каперам'}.get(reason, 'изменено')
        hist.append(f"{word} в {loc:%H:%M}: {was}".strip())
        _log_change(store, cur, loc, reason, dict(cur.get('best') or {}, **{k: cur.get(k) for k in ('espn_id', 'label', 'p', 'match', 'slot')}),
                    best)
    reason_txt = REASON.get(reason, '')
    _extras(rt, store, dp, cur, cands, items, loc, now, match_fn, row_fn, dry, main_key=key(best) if best else None)
    cur['text'] = _compose(text_fn(best, day, hist, score_fn(dp), 'нет линий с кф 1xBet ≥ 1,35 и вероятностью ≥ 60% — пришлите скриншот 1xBet '
                                   'любого матча из списка'), cur, match_fn)
    if first:
        cur['msgs'] = [list(x) for x in notify.send_markup(cur['text'], KB if best else None)]
        notify.pin(cur['msgs'])
    else:
        notify.edit(cur['msgs'], cur['text'], KB if best else None)
        notify.delete([tuple(x) for x in cur.get('ask', [])])
        ask = (f'🔁 <b>Ставка дня изменена</b> ({reason_txt}): <b>{notify.esc(best["label"])}</b>{_syn(best)} · '
               f'{notify.esc(match_fn(best))} — {best["p"] * 100:.0f}%, {notify.esc(PK.k_text(best))}\nПринимаем ставку дня?'
               if best else f'🔁 Других линий с кф ≥ 1,35 нет ({reason_txt}). Пришлите скриншот 1xBet любого матча из списка.')
        cur['ask'] = [list(x) for x in notify.send_markup(ask, KB if best else None)]
    cur['changes'] = int(cur.get('changes', 0)) + (0 if first else 1)
    cur['history'], cur['accepted'] = hist, ''
    if best:
        cur.update(espn_id=best['espn_id'], slot=best['slot'], key=key(best), p=best['p'], tier=best['tier'], label=best['label'],
                   start_utc=best['start_utc'], match=match_fn(best), best=_plain(best))
        _sheet_row(store, dp, dstr, row_fn(best, dstr, cur['changes'], hist), cur, best)
    rt['day_pick'] = cur
    return (f"ставка дня: {'публикация' if first else 'замена (' + reason + ')'} → "
            f"{best['label'] + ' (ступень ' + str(best['tier']) + ')' if best else 'нет'}{note}")


def _extras(rt: dict, store: J.Store, dp: pd.DataFrame, cur: dict, cands: list[dict], items: list[tuple], loc, now,
            match_fn: Callable, row_fn: Callable, dry: bool, main_key: list | None = None) -> str:
    """⭐2 / ⭐3: уверенные кандидаты других матчей. Начавшиеся — замок; остальные пересобираются каждый прогон; смена — в dp_changes.
    Возвращает пометку для лога ('' — без изменений)."""
    if dry:
        return ''
    main_key = main_key or cur.get('key')
    old = {int(x['rank']): x for x in (cur.get('extras') or [])}
    locked = {r: x for r, x in old.items() if pd.Timestamp(x['start_utc']) <= now}
    used = {str(main_key[0])} if main_key else set()
    used |= {str(x['espn_id']) for x in locked.values()}
    pool = [c for c in cands if confident(c) and str(c['espn_id']) not in used]
    new: dict[int, dict] = dict(locked)
    for r in range(2, MAX_PICKS + 1):                         # прежние доп. ставки, всё ещё уверенные, — на своих местах
        keep = old.get(r)
        c = next((c for c in pool if keep and r not in new and key(c) == keep.get('key')), None)
        if c:
            pool = [o for o in pool if str(o['espn_id']) != str(c['espn_id'])]
            new[r] = dict(_plain(c), rank=r, key=key(c), match=match_fn(c))
    for r in range(2, MAX_PICKS + 1):                         # свободные места — следующие уверенные
        if r in new or not pool:
            continue
        c = pool.pop(0)
        pool = [o for o in pool if str(o['espn_id']) != str(c['espn_id'])]
        new[r] = dict(_plain(c), rank=r, key=key(c), match=match_fn(c))
    changed = []
    for r in range(2, MAX_PICKS + 1):
        a, b = old.get(r), new.get(r)
        if (a or {}).get('key') == (b or {}).get('key'):
            continue
        changed.append(r)
        if a and r not in locked:
            _log_change(store, cur, loc, 'доп.', a, b, rank=r)
        if b:
            c = next(o for o in cands if key(o) == b['key'])
            _sheet_row(store, dp, cur['date'], row_fn(c, cur['date'], 0, []), cur, c, rank=r)
        elif a:
            _sheet_drop(store, dp, cur['date'], r)
    cur['extras'] = [new[r] for r in sorted(new)]
    return f"; доп. ставки: {', '.join('⭐' + str(r) for r in changed)}" if changed else ''


def _changed(new: dict, old: dict) -> bool:
    """Цифры ставки изменились заметно (P на 1 п.п., кф, источник, 🔥/⚔️) — текст закреплённого сообщения надо обновить."""
    return (abs(float(new.get('p', 0)) - float(old.get('p', 0) or 0)) >= 0.01 or new.get('k') != old.get('k')
            or new.get('p_src') != old.get('p_src') or bool(new.get('fire')) != bool(old.get('fire'))
            or bool(new.get('clash')) != bool(old.get('clash')))


def _rank_idx(dp: pd.DataFrame, dstr: str, rank: int) -> list:
    if not len(dp):
        return []
    rk = dp['rank'].astype(str).str.strip() if 'rank' in dp.columns else pd.Series('', index=dp.index)
    mine = rk.isin(['', 'nan', '1']) if rank == 1 else rk == str(rank)
    return list(dp.index[(dp.date.astype(str) == dstr) & mine])


def _sheet_row(store: J.Store, dp: pd.DataFrame, dstr: str, row: dict, cur: dict, c: dict | None = None, rank: int = 1) -> None:
    """Строка ставки в листе day_pick: rank 1 — главная (⭐), 2–3 — доп. Новая или обновлённая целиком."""
    row.update(screenshots=int(cur.get('screenshots', 0)), accepted=cur.get('accepted', '') if rank == 1 else '',
               rank=rank, fire=int(bool((c or {}).get('fire'))), clash=int(bool((c or {}).get('clash'))))
    idx = _rank_idx(dp, dstr, rank)
    if len(idx):
        old = {k: dp.at[idx[-1], k] if k in dp.columns else '' for k in J.DAY_COLS}
        old.update({k: v for k, v in row.items() if k in J.DAY_COLS})
        old.update(status='', result='', settled='')
        store.update('day_pick', pd.DataFrame([old], index=[idx[-1]]), J.DAY_COLS)
        for k, v in old.items():
            if k in dp.columns:
                dp.at[idx[-1], k] = v
    else:
        store.append('day_pick', [row])


def _sheet_drop(store: J.Store, dp: pd.DataFrame, dstr: str, rank: int) -> None:
    """Доп. ставка снята (кандидатов не осталось): строка помечается VOID — в статистику не идёт."""
    idx = _rank_idx(dp, dstr, rank)
    if len(idx):
        store.update('day_pick', pd.DataFrame([dict(status='OFF', result='P', settled=J.now_utc())], index=[idx[-1]]),
                     ['status', 'result', 'settled'])


def _sheet_accept(store: J.Store, dp: pd.DataFrame, dstr: str, cur: dict) -> None:
    idx = _rank_idx(dp, dstr, 1)
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


def changes_rows(store: J.Store, dp: pd.DataFrame) -> list:
    """Отчёт «🔁 Смены ставки дня» по причинам: заменённая против итоговой (те же сутки и ранг), только где у обеих W/L.
    «помогла» — итоговая W при заменённой L; знаменатель — смены, где исходы разные."""
    import rtable
    try:
        ch = store.read('dp_changes')
    except Exception:
        return []
    if not len(ch) or not len(dp):
        return []
    rk = dp['rank'].astype(str).str.strip().replace({'': '1', 'nan': '1'}) if 'rank' in dp.columns else pd.Series('1', index=dp.index)
    fin = {(str(d), r): str(x) for d, r, x in zip(dp.date, rk, dp.result) if str(x) in ('W', 'L')}
    by: dict[str, list] = {}
    for _, r in ch.iterrows():
        a, b = str(r.result), fin.get((str(r.date), str(r.get('rank', '1')).strip() or '1'))
        if a in ('W', 'L') and b:
            by.setdefault(str(r.reason), []).append((a, b))
    if not by:
        return []
    rows = [('🔁 Смены ставки дня', '', '', '')]
    for rs, ps in by.items():
        good = sum(1 for a, b in ps if b == 'W' and a == 'L')
        bad = sum(1 for a, b in ps if b == 'L' and a == 'W')
        rows.append(rtable.row(f' {rs}: помогла', good, good + bad))
    return rows
