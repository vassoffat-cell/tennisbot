"""⭐ Ставка дня — общий цикл для тенниса и футбола (копия — football/dpcore.py; хоккей — hockey/hpick.py, те же правила).

ПРАВИЛА (решение пользователя 11.11.2026 23:27 — прежние правила стёрты: кнопок ✅/🔄 нет, часа 17:00 нет):
  1. Утро (первый прогон суток 08:00 → 08:00; теннис — после утреннего цикла скриншотов): ставка дня ⭐1 объявляется сразу,
     закреплённым сообщением. Это «первый приговор».
  2. В течение дня, до начала матча ставки, по каперам (🔥/⚔️) или сравнению (котировки, скриншот 1xBet, пересчёт модели):
     • лучшая линия ТОГО ЖЕ матча → ставка правится, в сообщении — «мнение изменилось: было … → стало … (причина)»;
     • лучший кандидат ДРУГОГО матча (другой игры, лиги) → объявляется новая ставка ⭐2… (лимит динамический — selflearn.max_picks), прежние не трогаются.
     «Лучше» (с запасом против дёрганья): у текущей ⚔️, у новой нет; или у новой 🔥, у текущей нет, и P ниже не более чем на 3 п.п.;
     или ступень выше; или та же ступень и P выше на ≥ 2 п.п. (DAY_PICK_SWITCH). Ремонт данных (ошибка кф) — правка «исправление».
  3. После начала матча ставка — замок. Ставки ⭐2/⭐3 правятся по тем же правилам до начала своего матча.
Каждая ставка (⭐1, ⭐2, ⭐3) — отдельное сообщение, каждое закреплено; правка — то же сообщение обновляется + короткое
уведомление «✏️ мнение изменилось». Новые сутки — закрепы прошлых ставок снимаются, уведомления удаляются, новые закрепляются.
Учёт: правка — строка dp_changes «правка: причина» (заменённая версия тоже получает итог → «права первая / права итоговая»);
новая ставка — строка «новая: причина». Отчёт: дни с правкой первого приговора (чем реже, тем лучше), правки по причинам,
кто был прав, итоги новых линий. Нажатия старых кнопок (лист dp_actions) только помечаются прочитанными.
"""
from __future__ import annotations

from typing import Callable

import pandas as pd

import config as C
import journal as J
import notify
import pick as PK

try:                                                            # 🧠 самообучение (общий модуль в корне репозитория)
    import selflearn as SL
except ImportError:                                             # футбол: прогон из football/ — корень в sys.path
    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    import selflearn as SL

PRIORITY = {'screenshot': 0, 'api': 1, 'model': 2}
SEGMENT_H = 2.0                 # 14.11 (решение пользователя): одна ставка дня на игровой отрезок — начала не ближе 2 ч
MAX_PICKS = 3                   # 14.11: только запасное значение — лимит динамический (selflearn.max_picks)
REASON = {'исправление': 'исправлена ошибка кф скриншота', 'отказ': 'по вашему отказу', 'скриншот': 'по скриншоту', 'лучше': 'найдена лучше',
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


def category(c: dict | None) -> str:
    """14.11 (решение пользователя): категория ставки дня — «💰 выгодная» (перевес над Pinnacle, ТЗ-004) или «🎯 уверенная»
    (максимум вероятности без перевеса над рынком) — не путать: у выгодной плюс в цене, у уверенной — в вероятности."""
    return '💰 выгодная' if (c or {}).get('group') == 'value' else '🎯 уверенная'


def _syn(c: dict) -> str:
    return ' 🔥' if c.get('fire') else ' ⚔️' if c.get('clash') else ''


def snap_prices(rt: dict, cur: dict, cands: list[dict], now) -> None:
    """14.11 (CLV): последняя увиденная цена рынка без маржи (Pinnacle, иначе 1xBet) для каждой ставки дня до начала матча —
    «почти закрытие» (прогон каждые 2 ч; без лишних запросов к API). rt['dp_last']['дата|номер'] = {p, src, at}."""
    last = rt.setdefault('dp_last', {})
    picks = ([dict(cur, rank=1)] if cur.get('espn_id') else []) + [dict(x) for x in (cur.get('extras') or [])]
    for pk in picks:
        if not pk.get('start_utc') or pd.Timestamp(pk['start_utc']) <= now:
            continue
        c = next((o for o in cands if key(o) == list(pk.get('key') or [])), None)
        if c is not None and c.get('p_mkt'):
            last[f"{cur.get('date')}|{int(pk['rank'])}"] = dict(p=round(float(c['p_mkt']), 4), src=c.get('mkt_src', ''),
                                                                 at=f'{now:%Y-%m-%d %H:%M}', key='|'.join(map(str, key(c))))
    if len(last) > 120:
        for k in sorted(last)[:len(last) - 120]:
            last.pop(k, None)


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


def _sk() -> str:
    return {'Теннис': 'tennis', 'Футбол': 'football'}.get(SPORT, 'tennis')


def better(a: dict, b: dict | None) -> str:
    """Причина, по которой кандидат a лучше текущей ставки b ('' — не лучше). Запас против частых правок.
    🧠 Самообучение: отключённые причины правок, пониженные сегменты и добавочный запас (selflearn) — тоже здесь."""
    why = _better(a, b)
    return why if SL.allow(_sk(), why, a, b) else ''


def _better(a: dict, b: dict | None) -> str:
    if not b:
        return 'сравнение'
    pa, pb = float(a.get('p', 0)), float(b.get('p', 0) or 0)
    ta = int(a.get('tier', 9))
    tb = int(b['tier']) if b.get('tier') not in (None, '') else ta     # ступень неизвестна — по ступени не сравнивать
    if b.get('clash') and not a.get('clash'):
        return 'каперы против (⚔️)'
    if a.get('clash'):
        return ''
    if a.get('fire') and not b.get('fire') and pa >= pb - 0.03:
        return 'каперы за (🔥)'
    if ta < tb:
        return 'сравнение (ступень выше)'
    if ta == tb and pa >= pb + C.DAY_PICK_SWITCH + SL.switch_extra(_sk()):
        return f'сравнение (P +{(pa - pb) * 100:.0f} п.п.)'
    return ''


def _pick_view(x: dict) -> dict:
    """Текущая ставка в виде кандидата для сравнения."""
    b = dict(x.get('best') or {})
    for k in ('p', 'tier', 'fire', 'clash'):
        if x.get(k) is not None:
            b.setdefault(k, x.get(k))
    return b


def _short(c: dict, match_fn: Callable) -> str:
    return (f"<b>{notify.esc(c['label'])}</b>{_syn(c)} · {notify.esc(match_fn(c))} — {float(c['p']) * 100:.0f}% · {notify.fair(c['p'])}"
            + (f", {notify.esc(PK.k_text(c))}" if c.get('k') else ''))


ICON, SPORT = '⭐', 'Ставка'      # задаёт daypick вида спорта: 🎾 Теннис / ⚽ Футбол (хоккей — hockey/hpick.py, 🏒)


def _titled(text: str, rank: int) -> str:
    """Номер ставки после значка вида спорта: «🎾 <b>Теннис · ставка дня</b>» → «🎾2 <b>Теннис · ставка дня</b>»."""
    t = str(text)
    i = t.find(' <b>')
    return t[:i] + str(rank) + t[i:] if 0 <= i <= 4 else t


def _set_main(cur: dict, c: dict, match_fn: Callable) -> None:
    cur.update(espn_id=c['espn_id'], slot=c['slot'], key=key(c), p=c['p'], tier=c['tier'], label=c['label'],
               start_utc=c['start_utc'], match=match_fn(c), best=_plain(c))


def process(rt: dict, store: J.Store, items: list[tuple], off: dict, daily_done: bool, *,
            offers_fn: Callable, text_fn: Callable, row_fn: Callable, slot_fn: Callable, match_fn: Callable,
            score_fn: Callable, shots: list[dict] | None = None, actions: list[str] | None = None, dry: bool = False) -> str:
    """Каждая ставка дня — отдельное закреплённое сообщение (⭐1, ⭐2, ⭐3). Новые сутки — старые закрепы сняты."""
    loc = _local_now()
    day = C.wday(loc); dstr = day.isoformat()                   # сутки системы 08:00 → 08:00
    cur = rt.get('day_pick') or {}
    if cur.get('date') != dstr:
        cur = {}
    log: list[str] = []
    dp = store.read('day_pick')
    if not cur.get('date'):                                     # новые сутки: снять закрепы прошлых ставок, убрать уведомления
        old = rt.get('day_pick') or {}
        if not dry:
            pins = [tuple(x) for x in old.get('msgs', [])] + [tuple(m) for x in (old.get('extras') or []) for m in x.get('msgs', [])]
            if pins:
                notify.pin(pins, on=False)
            notify.delete([tuple(x) for x in old.get('ask', [])] + [tuple(x) for x in old.get('notes', [])])
        cur = dict(date=dstr, msgs=[], changes=0, history=[], shots=[], screenshots=0, extras=[], log=[], notes=[], nokb=True, sep=True,
                   hdr2=True, hdr3=True)
    fix = bool(cur.pop('fix', None)) and bool(cur.get('espn_id'))
    if shots:                                                   # скриншоты дня — точные кф (сравнение)
        known = {tuple(key(o)): o for o in cur.get('shots', [])}
        for o in shots:
            known[tuple(key(o))] = _plain(o)
        cur['shots'] = list(known.values())
        cur['screenshots'] = int(cur.get('screenshots', 0)) + 1

    now = pd.Timestamp.now(tz='UTC').tz_localize(None)
    cands = offers_fn(items, off, day, log) + [_revive(o) for o in cur.get('shots', []) if pd.Timestamp(o['start_utc']) > now]
    cands = [o for o in PK.choose(_dedupe(cands)) if o['market'] not in off]
    cands = SL.reorder(_sk(), cands)                            # 🧠 пониженные сегменты — в конец очереди
    snap_prices(rt, cur, cands, now)                            # 📈 последняя цена рынка до начала — для CLV
    for c in cands:
        c['slot'] = slot_fn(c, items)
    info = None
    if not dry:                                                 # 🔏 15.11: печать прогнозов суток (снимок в репозитории каждый прогон)
        try:
            import seal as SEAL
            lo = pd.Timestamp(day) + pd.Timedelta(hours=C.DAY_START_HOUR - C.TZ_OFFSET_HOURS)
            info = SEAL.seal(_sk(), items, lo, lo + pd.Timedelta(days=1), now, C.DATA, dstr, SEAL.cand_rows(cands))
        except Exception as e:
            log.append(f'печать: сбой {e.__class__.__name__}: {e}')
    note = ('; ' + '; '.join(log)) if log else ''
    if dry:
        print(text_fn(cands[0] if cands else None, day, [], '', 'нет подходящих линий'))
        return 'dry'

    def main_text() -> str:
        best = next((c for c in cands if key(c) == list(cur.get('key', []))), None) or \
            (_revive(cur['best']) if cur.get('best') else None)
        t = _titled(text_fn(best, day, cur.get('history', []), score_fn(dp),
                            'подходящей линии пока нет (кф ≥ 1,35 и вероятность ≥ 60%) — появится, объявлю'), 1)
        if cur.get('scan'):                                     # 🔎 15.11: что рассмотрено на старте суток — строкой под заголовком
            head, _, rest = t.partition('\n')
            t = f"{head}\n{notify.esc(cur['scan'])}" + (f"\n{rest}" if rest else '')
        return t

    def extra_text(x: dict) -> str:
        c = next((o for o in cands if key(o) == list(x['key'])), None) or _revive(x)
        return _titled(text_fn(c, day, x.get('hist', []), ''), int(x['rank']))

    if not cur.get('msgs'):                                     # 1. утро — первый приговор: ⭐1, закреплён
        best = cands[0] if cands else None
        if info:
            import seal as SEAL
            cur['scan'] = SEAL.text(info, len({str(c.get('espn_id', c.get('id', ''))) for c in cands}))
        if best:
            _set_main(cur, best, match_fn)
            cur.update(first_label=best['label'], first_match=match_fn(best))
            _sheet_row(store, dp, dstr, row_fn(best, dstr, 0, []), cur, best)
        cur['text'] = main_text()
        cur['msgs'] = [list(x) for x in notify.send_markup(cur['text'], None)]
        notify.pin(cur['msgs'])
        rt['day_pick'] = cur
        return f"ставка дня: объявлена → {best['label'] + ' (ступень ' + str(best['tier']) + ')' if best else 'нет'}{note}"

    if not cur.get('nokb'):                                     # переход 11.11: убрать кнопки и вопрос «Принимаем?»
        notify.delete([tuple(x) for x in cur.get('ask', [])])
        cur['ask'], cur['nokb'] = [], True
    if not cur.get('sep'):                                      # переход 11.11: доп. ставки — отдельными закреплёнными сообщениями
        for x in cur.get('extras') or []:
            if not x.get('msgs'):
                x['text'] = extra_text(x)
                x['msgs'] = [list(m) for m in notify.send_markup(x['text'], None, silent=True)]
                notify.pin(x['msgs'])
        cur['text'] = main_text()
        notify.edit(cur['msgs'], cur['text'], None)
        cur['sep'] = True

    if not cur.get('hdr2') or not cur.get('hdr3'):               # 11–12.11 начало без «хвоста»; 14.11 категория — обновить закрепы
        cur['text'] = main_text()
        notify.edit(cur['msgs'], cur['text'], None)
        for x in cur.get('extras') or []:
            if x.get('msgs'):
                x['text'] = extra_text(x)
                notify.edit(x['msgs'], x['text'], None)
        cur['hdr2'] = cur['hdr3'] = True

    if __import__('os').getenv('RESEND_PICKS') and not dry:     # ТЗ-006 «🔄 Ставки на сутки»: заново все ставки, старые — удалить
        n_ = 0
        for h in [cur] + list(cur.get('extras') or []):
            if h.get('text'):
                if h.get('msgs'):
                    notify.delete([tuple(m) for m in h['msgs']])
                h['msgs'] = [list(m) for m in notify.send_markup(h['text'], None, silent=True)]
                notify.pin(h['msgs'])
                n_ += 1
        log.append(f'ставки на сутки: прислано заново {n_}')
    back = 0                                                    # 12.11: удалённые пользователем ставки — заново, с закрепом
    for h in [cur] + list(cur.get('extras') or []):
        if h.get('text') and (not h.get('msgs') or not notify.alive(h['msgs'])):   # 14.11: и не доставленные вовсе
            h['msgs'] = [list(m) for m in notify.send_markup(h['text'], None, silent=True)]
            notify.pin(h['msgs'])
            back += 1
    if back:
        log.append(f'возвращено удалённых ставок: {back}')
        note = '; ' + '; '.join(log)

    events = []
    if not cur.get('espn_id') and cands:                       # утром ставки не было — первая появившаяся = ⭐1 (не правка)
        best = cands[0]
        _set_main(cur, best, match_fn)
        cur.update(first_label=best['label'], first_match=match_fn(best))
        _sheet_row(store, dp, dstr, row_fn(best, dstr, 0, []), cur, best)
        _log_new(store, cur, loc, 'появилась линия', best, 1, match_fn)
        cur['text'] = main_text()
        notify.edit(cur['msgs'], cur['text'], None)
        events.append('⭐1 появилась')
    # 2a. правки: лучшая линия того же матча (каждая ставка — до начала своего матча)
    picks = ([dict(cur, rank=1)] if cur.get('espn_id') else []) + [dict(x) for x in (cur.get('extras') or [])]
    for pk in picks:
        r = int(pk['rank'])
        same = [c for c in cands if str(c['espn_id']) == str(pk['espn_id'])]
        if pd.Timestamp(pk['start_utc']) <= now or not same:
            continue
        mine = next((c for c in same if key(c) == list(pk['key'])), None)
        if mine and r > 1:                                      # ступень и синтез ⭐2/⭐3 — по свежему расчёту
            for x in cur.get('extras') or []:
                if int(x['rank']) == r:
                    x.update(tier=mine['tier'], fire=mine.get('fire'), clash=mine.get('clash'))
        top = same[0]
        why = 'исправление' if (fix and r == 1) else better(top, mine or _pick_view(pk)) if key(top) != list(pk['key']) else ''
        if not why:
            if mine and r == 1 and _changed(mine, cur.get('best') or {}):        # та же ставка, новые цифры — не правка
                cur.update(best=_plain(mine), p=mine['p'], tier=mine['tier'])
                _sheet_row(store, dp, dstr, row_fn(mine, dstr, int(cur.get('changes', 0)), cur.get('history', [])), cur, mine)
                cur['text'] = main_text()
                notify.edit(cur['msgs'], cur['text'], None)
            continue
        if shots and why.startswith('сравнение'):
            why = 'скриншот 1xBet' + (' ' + why.split(' ', 1)[1] if ' ' in why else '')
        old = dict(pk.get('best') or pk, **{k: pk.get(k) for k in ('espn_id', 'label', 'p', 'match', 'slot')})
        _log_change(store, cur, loc, f'правка: {why}', old, top, rank=r)
        hist_line = f"✏️ мнение изменилось в {loc:%H:%M}: было {pk['label']} {float(pk['p']) * 100:.0f}% → {top['label']} ({why})"
        if r == 1:
            cur['history'] = list(cur.get('history', [])) + [hist_line]
            cur['changes'] = int(cur.get('changes', 0)) + 1
            _set_main(cur, top, match_fn)
            _sheet_row(store, dp, dstr, row_fn(top, dstr, cur['changes'], cur['history']), cur, top)
            cur['text'] = main_text()
            notify.edit(cur['msgs'], cur['text'], None)
        else:
            for x in cur['extras']:
                if int(x['rank']) == r:
                    x.update(_plain(top), tier=top['tier'], key=key(top), match=match_fn(top), edits=int(x.get('edits', 0)) + 1,
                             hist=list(x.get('hist', [])) + [hist_line])
                    _sheet_row(store, dp, dstr, row_fn(top, dstr, x['edits'], x['hist']), cur, top, rank=r)
                    x['text'] = extra_text(x)
                    notify.edit(x.get('msgs', []), x['text'], None)
        cur['notes'] = list(cur.get('notes', [])) + [list(m) for m in notify.send_blocks(
            [f"{ICON}{r} <b>{SPORT} · ставка дня: мнение изменилось</b> ✏️ ({notify.esc(why)})\n"
             f"было {notify.esc(pk['label'])} → стало {_short(top, match_fn)}"])]
        events.append(f'{ICON}{r} правка ({why})')
    # 2b. новая ставка: другой матч лучше ⭐1 → ⭐2 / ⭐3 — отдельное закреплённое сообщение (одна за прогон)
    picks = ([dict(cur, rank=1)] if cur.get('espn_id') else []) + [dict(x) for x in (cur.get('extras') or [])]
    nday = sum(1 for it in items if C.wday(it[0].start + pd.Timedelta(hours=C.TZ_OFFSET_HOURS)) == day) if items else 0
    cur['vol'] = max(int(cur.get('vol', 0)), nday)              # матчей суток (максимум за день: утром список может быть неполным)
    if picks and len(picks) < SL.max_picks(_sk(), MAX_PICKS, cur['vol'], loc):
        used = {str(x['espn_id']) for x in picks}
        main = next((c for c in cands if key(c) == list(cur.get('key', []))), None) or _pick_view(cur)
        starts = [x.get('start_utc') for x in picks]
        for c in cands:
            if str(c['espn_id']) in used or pd.Timestamp(c['start_utc']) <= now:
                continue
            if any(abs((pd.Timestamp(c['start_utc']) - pd.Timestamp(s)).total_seconds()) < SEGMENT_H * 3600
                   for s in starts if s not in (None, '')):     # 14.11: игровой отрезок уже занят ставкой дня
                continue
            why = better(c, main)
            if not why:
                continue
            r = len(picks) + 1
            x = dict(_plain(c), tier=c['tier'], rank=r, key=key(c), match=match_fn(c), why=why, at=f'{loc:%H:%M}', edits=0,
                     hist=[f'➕ новая линия дня в {loc:%H:%M}: {why} (лучше ⭐1 {cur.get("label", "")})'])
            x['text'] = extra_text(x)
            x['msgs'] = [list(m) for m in notify.send_markup(x['text'], None)]
            notify.pin(x['msgs'])
            cur.setdefault('extras', []).append(x)
            _sheet_row(store, dp, dstr, row_fn(c, dstr, 0, x['hist']), cur, c, rank=r)
            _log_new(store, cur, loc, why, c, r, match_fn)
            events.append(f'{ICON}{r} новая ({why})')
            break
    rt['day_pick'] = cur
    return 'ставка дня: ' + ('; '.join(events) if events else 'без изменений') + note


def _log_new(store: J.Store, cur: dict, loc, why: str, c: dict, rank: int, match_fn: Callable) -> None:
    """Строка dp_changes «новая: причина» — новая линия дня (получит итог; отчёт — «➕ новые линии»)."""
    row = dict(date=cur['date'], time=f'{loc:%H:%M}', reason=f'новая: {why}', rank=rank, espn_id=c['espn_id'],
               market=c.get('market', ''), pick=c.get('pick', ''), line='' if c.get('line') in (None, '') else c['line'],
               label=c.get('label', ''), match=match_fn(c), p=round(float(c.get('p', 0) or 0), 4), k=c.get('k') or '',
               to_label='—', to_key='|'.join(key(c)), slot=c.get('slot', 0))
    cur.setdefault('log', []).append(row)
    try:
        store.append('dp_changes', [row])
    except Exception as e:
        print(f'dp_changes: не записано ({e})')


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


def _reason(r: str) -> str:
    """Причина правки без префикса и уточнений: «правка: каперы против (⚔️)» → «каперы против»."""
    r = str(r).replace('правка: ', '').split(' (')[0].strip()
    return {'лучше': 'сравнение', 'каперы': 'каперы', 'скриншот': 'скриншот 1xBet'}.get(r, r)


def changes_rows(store: J.Store, dp: pd.DataFrame) -> list:
    """Отчёт «✏️ Правки первого приговора» (правило 11.11: чем реже, тем лучше): дни с правкой ⭐1 из всех дней со ставкой,
    правки по причинам, кто был прав — первая версия или итоговая (где исходы разные).
    Новые линии дня (⭐2/⭐3) — строкой «➕ новые линии» в ops_rows отчёта (лист day_pick, rank 2–3)."""
    import rtable
    try:
        ch = store.read('dp_changes')
    except Exception:
        return []
    if not len(dp):
        return []
    rk = dp['rank'].astype(str).str.strip().replace({'': '1', 'nan': '1'}) if 'rank' in dp.columns else pd.Series('1', index=dp.index)
    days = dp[rk == '1'].date.astype(str).nunique()
    if not days:
        return []
    fin = {(str(d), r): str(x) for d, r, x in zip(dp.date, rk, dp.result) if str(x) in ('W', 'L')}
    ed = ch[~ch.reason.astype(str).str.startswith('новая') & (ch.reason.astype(str) != 'доп.')] if len(ch) else ch
    erk = ed['rank'].astype(str).str.strip().replace({'': '1', 'nan': '1'}) if len(ed) else pd.Series(dtype=str)
    edays = ed[erk == '1'].date.astype(str).nunique() if len(ed) else 0
    share = edays / days
    rows = [('✏️ Правки первого приговора', '', '', ''),
            rtable.row(' дни с правкой ⭐1', edays, days, mark='🟢' if share <= 0.2 else '🟡' if share <= 0.4 else '🔴')]
    if not len(ed):
        return rows
    rows.append((' правок всего', str(len(ed)), '', ''))
    for rs, n in ed.reason.map(_reason).value_counts().items():
        rows.append((f'  {rs}'[:22], str(int(n)), '', ''))
    good = bad = 0
    for _, r in ed.iterrows():
        a, b = str(r.result), fin.get((str(r.date), str(r.get('rank', '1')).strip() or '1'))
        if a in ('W', 'L') and b and a != b:
            good += b == 'W'; bad += a == 'W'
    if good + bad:
        rows.append(rtable.row(' права итоговая', good, good + bad))
        rows.append(rtable.row(' права первая', bad, good + bad))
    return rows
