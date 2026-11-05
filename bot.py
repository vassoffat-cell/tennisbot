"""Telegram-бот: скриншоты 1xBet → независимый прогноз по всем линиям → журнал бумажных ставок.

Команды:
  /start   — инструкция
  /today   — авто-прогнозы на ближайшие матчи (из облака, лист auto_forecasts)
  /stats   — качество модели и итоги бумажных ставок
  /update  — скачать свежие данные tennis-data и переобучить модель на этом компьютере
  /reset   — очистить присланные скриншоты
Отправьте скриншоты матча (лучше ФАЙЛОМ, без сжатия) и нажмите «Разобрать».
Чтобы обновить данные, отправьте боту свежий файл 2026.xlsx с tennis-data.co.uk (WTA или ATP).
Запуск: python bot.py
"""
from __future__ import annotations

import asyncio
import logging
import re
import tempfile
from functools import partial
from pathlib import Path

import pandas as pd
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config as C
import journal as J
import names as NM
import pipeline as P

router = Router()
SESSIONS: dict[int, dict] = {}            # chat_id → {'files': [...], 'match': Match | None, 'msg': id}
TMP = Path(tempfile.gettempdir()) / 'tennisbot'
TMP.mkdir(exist_ok=True)
JOURNAL: J.Journal | None = None


def allowed(user_id: int) -> bool:
    return not C.ALLOWED_USERS or user_id in C.ALLOWED_USERS


def session(chat_id: int) -> dict:
    return SESSIONS.setdefault(chat_id, {'files': [], 'match': None, 'msg': None})


def kb(*rows: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows])


async def run_blocking(func, *args):
    return await asyncio.get_running_loop().run_in_executor(None, partial(func, *args))


def esc(s: str) -> str:
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


@router.message(Command('start'))
async def start(msg: Message) -> None:
    if not allowed(msg.from_user.id):
        return
    await msg.answer(
        'Пришлите скриншоты одного матча 1xBet (теннис, WTA или ATP): шапку матча и раскрытые рынки — '
        '«Основная игра», «1-й сет». Лучше отправлять <b>файлом</b> (скрепка → Файл): так распознаётся и шапка с именами.\n'
        'Затем нажмите «Разобрать».\n\n'
        'Прогноз — независимый, котировки в модель не подаются. Режим — <b>бумажные ставки</b>: сигналы пишутся в журнал, '
        'результаты считаются сами, когда вы пришлёте свежий файл 2026.xlsx с tennis-data.co.uk.\n'
        '/today — авто-прогнозы на ближайшие матчи, /stats — качество модели и итоги, '
        '/update — обновить данные, /reset — начать заново.')


@router.message(Command('reset'))
async def reset(msg: Message) -> None:
    SESSIONS.pop(msg.chat.id, None)
    await msg.answer('Скриншоты очищены.')


async def _save_image(msg: Message, bot: Bot) -> Path | None:
    if msg.photo:
        file_id, ext = msg.photo[-1].file_id, '.jpg'
    elif msg.document and (msg.document.mime_type or '').startswith('image/'):
        file_id, ext = msg.document.file_id, Path(msg.document.file_name or 'x.png').suffix or '.png'
    else:
        return None
    path = TMP / f'{msg.chat.id}_{msg.message_id}{ext}'
    await bot.download(file_id, destination=path)
    return path


@router.message(F.photo | (F.document & F.document.mime_type.startswith('image/')))
async def on_image(msg: Message, bot: Bot) -> None:
    if not allowed(msg.from_user.id):
        return
    s = session(msg.chat.id)
    path = await _save_image(msg, bot)
    if path is None:
        return
    s['files'].append(str(path)); s['match'] = None
    text = f'Скриншотов: {len(s["files"])}. Пришлите остальные и нажмите «Разобрать».'
    if msg.photo:
        text += '\nФото сжато Telegram — имена игроков может не распознать; надёжнее отправлять файлом.'
    markup = kb([('Разобрать', 'parse'), ('Сбросить', 'reset')])
    if s['msg']:
        try:
            await bot.edit_message_text(text, chat_id=msg.chat.id, message_id=s['msg'], reply_markup=markup)
            return
        except Exception:
            pass
    s['msg'] = (await msg.answer(text, reply_markup=markup)).message_id


@router.callback_query(F.data == 'reset')
async def cb_reset(cq: CallbackQuery) -> None:
    SESSIONS.pop(cq.message.chat.id, None)
    await cq.message.edit_text('Скриншоты очищены.')


def _summary(m: P.Match) -> str:
    p = m.parsed
    n_lines = len(p.lines)
    lines = [f'<b>{esc(p.tour or "тур?")}</b> · {esc(p.tournament or "турнир?")} · {esc(p.round or "")} · {esc(p.surface or "покрытие?")} · {esc(p.date or "")}',
             f'Линий распознано: {n_lines}']
    if m.candidates:
        for ru, cand in zip(p.players, m.candidates):
            best = cand[0]
            lines.append(f'{esc(ru)} → <b>{esc(best[1])}</b> (сходство {best[2]:.2f})')
    elif p.players:
        lines.append(f'Игроки: {esc(p.players[0])} — {esc(p.players[1])}')
    else:
        lines.append('Имена игроков не распознаны. Напишите их сообщением: <code>Валентова — Эала</code>')
    return '\n'.join(lines)


@router.callback_query(F.data == 'parse')
async def cb_parse(cq: CallbackQuery) -> None:
    s = session(cq.message.chat.id)
    if not s['files']:
        await cq.answer('Нет скриншотов'); return
    await cq.message.edit_text('Распознаю… (около 2 секунд на скриншот)')
    m = await run_blocking(P.recognize, s['files'])
    s['match'] = m
    await _ask_confirm(cq.message, m, edit=True)


async def _ask_confirm(message: Message, m: P.Match, edit: bool = False) -> None:
    rows = []
    if not m.tour:
        rows.append([('WTA', 'tour:wta'), ('ATP', 'tour:atp')])
    if m.candidates:
        for i, cand in enumerate(m.candidates):
            if len(cand) > 1 and not P.sure(cand):
                rows.append([(f'{i+1}: {c[1]} ({c[2]:.2f})', f'pick:{i}:{j}') for j, c in enumerate(cand)])
    ready = m.tour and m.ids and m.parsed.lines
    if ready:
        rows.append([('Верно — прогноз', 'go')])
    rows.append([('Сбросить', 'reset')])
    text = _summary(m) + ('' if ready else '\n\nНе хватает данных для прогноза.')
    if edit:
        await message.edit_text(text, reply_markup=kb(*rows))
    else:
        await message.answer(text, reply_markup=kb(*rows))


@router.callback_query(F.data.startswith('tour:'))
async def cb_tour(cq: CallbackQuery) -> None:
    m = session(cq.message.chat.id)['match']
    m.tour = cq.data.split(':')[1]; m.parsed.tour = m.tour.upper()
    if m.parsed.players:
        await run_blocking(P.identify, m)
    await _ask_confirm(cq.message, m, edit=True)


@router.callback_query(F.data.startswith('pick:'))
async def cb_pick(cq: CallbackQuery) -> None:
    m = session(cq.message.chat.id)['match']
    _, i, j = cq.data.split(':'); i, j = int(i), int(j)
    c = m.candidates[i]
    c.insert(0, c.pop(j)); c[0] = (c[0][0], c[0][1], 1.0)
    m.ids = tuple(x[0][0] for x in m.candidates); m.names_lat = tuple(x[0][1] for x in m.candidates)
    await _ask_confirm(cq.message, m, edit=True)


@router.message(F.text.regexp(r'^\s*[А-ЯЁA-Z][\w\-\. ]+\s*[—–-]\s*[А-ЯЁA-Z][\w\-\. ]+\s*$'))
async def on_names(msg: Message) -> None:
    """Ручной ввод имён: «Валентова — Эала» или «Тереза Валентова — Александра Эала»."""
    s = session(msg.chat.id)
    m = s.get('match')
    if not allowed(msg.from_user.id) or m is None:
        return
    a, b = [x.strip() for x in re.split(r'\s*[—–-]\s*', msg.text.strip(), maxsplit=1)]
    m.parsed.players = (a, b)
    if not m.tour:
        await _ask_confirm(msg, m); return
    await run_blocking(P.identify, m)
    await _ask_confirm(msg, m)


@router.callback_query(F.data == 'go')
async def cb_go(cq: CallbackQuery) -> None:
    global JOURNAL
    s = session(cq.message.chat.id)
    m = s['match']
    await cq.message.edit_text(_summary(m) + '\n\nСчитаю прогноз…')
    fc, text = await run_blocking(P.forecast, m)
    for i in range(0, len(text), 3500):                       # лимит сообщения Telegram — 4096 символов
        await cq.message.answer(f'<pre>{esc(text[i:i + 3500])}</pre>')
    try:
        JOURNAL = JOURNAL or J.Journal()
        n_rows, n_bets = await run_blocking(JOURNAL.log, m, fc)
        await cq.message.answer(f'Записано в журнал ({JOURNAL.backend}): прогнозов {n_rows}, бумажных ставок {n_bets}.')
    except Exception as e:
        await cq.message.answer(f'Прогноз готов, но журнал не записан: {esc(str(e))}')
    SESSIONS.pop(cq.message.chat.id, None)


@router.message(F.document & F.document.file_name.endswith('.xlsx'))
async def on_xlsx(msg: Message, bot: Bot) -> None:
    """Свежий файл tennis-data: сохранить, отправить в GitHub (облако), переобучить модель на компьютере."""
    if not allowed(msg.from_user.id):
        return
    tmp = TMP / f'upd_{msg.message_id}.xlsx'
    await bot.download(msg.document.file_id, destination=tmp)
    import updater
    import ghsync
    try:
        rep = await run_blocking(updater.save_file, tmp.read_bytes())
    except Exception as e:
        await msg.answer(esc(f'Файл не принят: {e}')); return
    push = await run_blocking(ghsync.push_tennis_data)
    await msg.answer(esc(f'Сохранено: {rep}\n' + '\n'.join(push) + '\nПереобучаю модель на компьютере (1–2 минуты)…'))
    reps = await run_blocking(updater.rebuild)
    P._MODELS.clear(); P._FRESH.clear()
    await msg.answer(esc('\n'.join(reps) + ('\nОблако переобучится на этом файле при ближайшем запуске weekly (пн, ср) '
                                              'или сразу, если запустить weekly вручную.' if ghsync.enabled() else '')))


@router.message(Command('stats'))
async def stats(msg: Message) -> None:
    global JOURNAL
    if not allowed(msg.from_user.id):
        return
    JOURNAL = JOURNAL or J.Journal()
    import autorun
    if JOURNAL.store.sh:                       # Google Таблица: расчёт делает облако, здесь только сводка
        _, text = await run_blocking(autorun.summary, JOURNAL.store)
        bets = await run_blocking(JOURNAL.store.read, 'paper_bets')
        text = (text or 'Доигранных авто-прогнозов пока нет.') + '\n' + J.bets_summary(bets)
    else:                                      # CSV на компьютере: расчёт по базе tennis-data
        text = await run_blocking(JOURNAL.settle)
        _, auto = await run_blocking(autorun.summary, JOURNAL.store)
        text = (auto + '\n' if auto else '') + text
    await msg.answer(esc(text))


@router.message(Command('today'))
async def today(msg: Message) -> None:
    global JOURNAL
    if not allowed(msg.from_user.id):
        return
    JOURNAL = JOURNAL or J.Journal()
    import autorun
    df = await run_blocking(JOURNAL.store.read, 'auto_forecasts')
    now = pd.Timestamp.now(tz='UTC').tz_localize(None)
    if df.empty:
        await msg.answer('Авто-прогнозов пока нет: облачный модуль ещё не запускался или не настроен (README, раздел «Облако»).'); return
    st = pd.to_datetime(df.start_utc, errors='coerce')
    sub = df[(st > now - pd.Timedelta(hours=3)) & (df.settled.astype(str).str.strip() == '')]
    if sub.empty:
        await msg.answer('На ближайшие сутки авто-прогнозов нет.'); return
    rows = [{c: (J.num(v) if c in ('p1', 'p2', 'kmin1', 'kmin2', 'total_line', 'p_over', 'hcp1_line', 'set1_p1', 'n1', 'n2')
                 else v) for c, v in r.items()} for r in sub.to_dict('records')]
    for r in rows:
        r['n1'], r['n2'] = int(r['n1'] or 0), int(r['n2'] or 0)
    for i in range(0, len(rows), 15):
        await msg.answer(esc(autorun.digest(rows[i:i + 15])))


@router.message(Command('update'))
async def update(msg: Message) -> None:
    if not allowed(msg.from_user.id):
        return
    await msg.answer('Скачиваю свежие файлы tennis-data и переобучаю модель (1–2 минуты)…')
    await msg.answer(esc(await run_blocking(_update_local)))


def _update_local() -> str:
    import ghsync
    import updater
    log = updater.update_all()
    log += ghsync.push_tennis_data() + updater.rebuild()
    P._MODELS.clear(); P._FRESH.clear()
    return '\n'.join(log)


def _data_age_days() -> float:
    files = list(C.TENNIS_DATA.glob('*.xlsx'))
    if not files:
        return 999.0
    newest = max(f.stat().st_mtime for f in files)
    return (pd.Timestamp.now().timestamp() - newest) / 86400


async def _startup_refresh(bot: Bot) -> None:
    """При запуске: если данные старше 7 дней — обновить в фоне и сообщить."""
    if _data_age_days() < 7:
        return
    try:
        text = await run_blocking(_update_local)
    except Exception as e:
        text = f'Автообновление данных не удалось: {e}'
    for uid in C.ALLOWED_USERS:
        try:
            await bot.send_message(uid, esc('Данные обновлены при запуске:\n' + text))
        except Exception:
            pass


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    if not C.BOT_TOKEN:
        raise SystemExit('Нет BOT_TOKEN: впишите токен бота в файл .env (см. README).')
    bot = Bot(C.BOT_TOKEN, default=DefaultBotProperties(parse_mode='HTML'))
    dp = Dispatcher()
    dp.include_router(router)
    asyncio.get_running_loop().create_task(_startup_refresh(bot))
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
