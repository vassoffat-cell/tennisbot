"""Telegram через Bot API (футбольный бот): облако отправляет и удаляет сообщения. Входящие принимает бот на Apps Script."""
from __future__ import annotations

import json
import urllib.request

import config as C

API = 'https://api.telegram.org/bot{token}/{method}'


def esc(s: str) -> str:
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


BOTS = {'main': lambda: C.BOT_TOKEN, 'tennis': lambda: getattr(C, 'TENNIS_BOT_TOKEN', '')}   # 'tennis' — из футбольного облака
_SENT: set[str] = set()                         # боты, которым в этом прогоне ушли новые сообщения (для заглушки 🔒)
COVER = '🔒 ·'                                  # не одиночный эмодзи — без крупной анимации


def _call(method: str, payload: dict | None = None, timeout: int = 30, bot: str = 'main') -> dict:
    if method in ('sendMessage', 'sendPhoto', 'sendDocument') and not (payload or {}).get('_cover'):
        _SENT.add(bot)
    (payload or {}).pop('_cover', None)
    data = json.dumps(payload or {}).encode()
    req = urllib.request.Request(API.format(token=BOTS[bot](), method=method), data=data,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def folded(head: str, items: list[str], limit: int = 3500) -> list[str]:
    """Турнир: заголовок + матчи в сворачиваемой цитате Telegram (<blockquote expandable>, разворачивается касанием).
    Длинный турнир делится на несколько цитат, заголовок повторяется с пометкой «продолжение»."""
    out, cur, h = [], [], head
    for it in items:
        if cur and sum(len(x) + 2 for x in cur) + len(it) > limit:
            out.append(f'{h}\n<blockquote expandable>' + '\n\n'.join(cur) + '</blockquote>')
            cur, h = [], head + ' (продолжение)'
        cur.append(it)
    if cur:
        out.append(f'{h}\n<blockquote expandable>' + '\n\n'.join(cur) + '</blockquote>')
    return out


def _pack(blocks: list[str], limit: int = 3800) -> list[str]:
    """Склеить блоки в сообщения не длиннее limit, не разрывая блок (HTML-теги внутри блока не рвутся)."""
    out, cur = [], ''
    for b in blocks:
        if cur and len(cur) + len(b) + 2 > limit:
            out.append(cur); cur = ''
        cur = f'{cur}\n\n{b}' if cur else b
    if cur:
        out.append(cur)
    return out


def send_blocks(blocks: list[str], chat_ids: set[int] | None = None, silent: bool = False,
                bot: str = 'main') -> list[tuple[int, int]]:
    """Отправить HTML-блоки (каждый блок — законченный кусок с тегами), упаковав их в минимум сообщений.
    silent — без звука уведомления; bot — 'main' (свой бот) или 'tennis' (теннисный — из футбольного облака).
    Возвращает [(chat_id, message_id)] отправленных сообщений."""
    msgs = _pack([b for b in blocks if b])
    if not BOTS[bot]() or not (chat_ids or C.ALLOWED_USERS):
        print('\n\n'.join(msgs))
        return []
    sent = []
    for chat in (chat_ids or C.ALLOWED_USERS):
        for m in msgs:
            try:
                r = _call('sendMessage', {'chat_id': chat, 'text': m, 'parse_mode': 'HTML',
                                          'disable_web_page_preview': True, 'disable_notification': silent}, bot=bot)
                sent.append((chat, r['result']['message_id']))
            except Exception as e:
                print(f'Telegram: не отправлено ({e})')
    return sent


def send_markup(text: str, markup: dict | None = None, silent: bool = False) -> list[tuple[int, int]]:
    """Одно сообщение (HTML) с кнопками под ним всем ALLOWED_USERS. Возвращает [(chat_id, message_id)]."""
    if not C.BOT_TOKEN or not C.ALLOWED_USERS:
        print(text)
        return []
    sent = []
    for chat in C.ALLOWED_USERS:
        try:
            p = {'chat_id': chat, 'text': text[:4000], 'parse_mode': 'HTML', 'disable_web_page_preview': True,
                 'disable_notification': silent}
            if markup:
                p['reply_markup'] = markup
            r = _call('sendMessage', p)
            sent.append((chat, r['result']['message_id']))
        except Exception as e:
            print(f'Telegram: не отправлено ({e})')
    return sent


def delete(messages: list) -> int:
    """Удалить сообщения бота [(chat_id, message_id)] пачками по 100. Старше 48 ч Telegram удалить не даёт — пропуск."""
    if not C.BOT_TOKEN:
        return 0
    by_chat: dict[int, list[int]] = {}
    for chat, mid in messages:
        by_chat.setdefault(int(chat), []).append(int(mid))
    n = 0
    for chat, ids in by_chat.items():
        for i in range(0, len(ids), 100):
            try:
                _call('deleteMessages', {'chat_id': chat, 'message_ids': ids[i:i + 100]})
                n += len(ids[i:i + 100])
            except Exception as e:
                print(f'Telegram: не удалено ({e})')
    return n


def send(text: str, pre: bool = False) -> int:
    """Простой текст (экранируется); длинный режется по строкам."""
    lines = esc(text).split('\n')
    blocks, cur = [], ''
    for line in lines:
        if len(cur) + len(line) > 3500:
            blocks.append(cur); cur = ''
        cur += line + '\n'
    blocks.append(cur)
    if pre:
        blocks = [f'<pre>{b}</pre>' for b in blocks]
    return send_blocks(blocks)


def edit(messages: list, text: str, markup: dict | None = None) -> int:
    """Изменить текст уже отправленных сообщений [(chat_id, message_id)] (HTML). markup — кнопки под сообщением
    (None — убрать). Возвращает число изменённых."""
    if not C.BOT_TOKEN:
        print(text)
        return 0
    n = 0
    for chat, mid in messages:
        try:
            _call('editMessageText', {'chat_id': int(chat), 'message_id': int(mid), 'text': text, 'parse_mode': 'HTML',
                                      'disable_web_page_preview': True,
                                      'reply_markup': markup or {'inline_keyboard': []}})
            n += 1
        except Exception as e:
            if 'not modified' not in str(e):
                print(f'Telegram: не изменено ({e})')
    return n


def pin(messages: list, on: bool = True) -> int:
    """Закрепить (on) или открепить сообщения бота в чате. Закрепление — без звука."""
    if not C.BOT_TOKEN:
        return 0
    n = 0
    for chat, mid in messages:
        try:
            if on:
                _call('pinChatMessage', {'chat_id': int(chat), 'message_id': int(mid), 'disable_notification': True})
            else:
                _call('unpinChatMessage', {'chat_id': int(chat), 'message_id': int(mid)})
            n += 1
        except Exception as e:
            print(f'Telegram: закрепление не выполнено ({e})')
    return n


def plural(n: int, one: str, few: str, many: str) -> str:
    """Согласование с числом: 1 матч, 2 матча, 5 матчей (ТЗ-001 C)."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def cover(path) -> int:
    """🔒 Заглушка последним сообщением чата: в списке чатов не видно, что прислал бот, пока чат не открыт.
    Ставится в конце прогона, только если в прогоне ушли новые сообщения; прошлая заглушка удаляется (файл path)."""
    import pathlib
    f = pathlib.Path(path)
    old = json.loads(f.read_text()) if f.exists() else {}
    n = 0
    for bot in sorted(_SENT):
        if not BOTS[bot]():
            continue
        prev = old.get(bot, [])
        if prev:
            try:
                by: dict[int, list[int]] = {}
                for chat, mid in prev:
                    by.setdefault(int(chat), []).append(int(mid))
                for chat, ids in by.items():
                    _call('deleteMessages', {'chat_id': chat, 'message_ids': ids}, bot=bot)
            except Exception as e:
                print(f'Telegram: заглушка не удалена ({e})')
        cur = []
        for chat in C.ALLOWED_USERS:
            try:
                r = _call('sendMessage', {'chat_id': chat, 'text': COVER, 'disable_notification': True, '_cover': 1}, bot=bot)
                cur.append([chat, r['result']['message_id']]); n += 1
            except Exception as e:
                print(f'Telegram: заглушка не отправлена ({e})')
        old[bot] = cur
    _SENT.clear()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(old))
    return n


def fair(p: float) -> str:
    """Честный кф = 1 / P модели (решение пользователя 11.11.2026: на каждой линии, во всех видах спорта).
    Ставка в плюс, только если кф букмекера выше честного."""
    try:
        p = float(p)
    except (TypeError, ValueError):
        return ''
    return f'честный кф {1 / p:.2f}'.replace('.', ',') if p > 0 else ''


# ------------------------------------------------------------------ сбой прогона (11.11.2026: без трейсбека в чате)
def _why(tb: str) -> str:
    """Причина сбоя по-русски (одна строка); трейсбек — только в журнале GitHub."""
    t = str(tb)
    if '429' in t or 'Quota exceeded' in t:
        return 'лимит запросов Google Таблиц (оба бота сразу) — повтор при лимите уже встроен'
    if 'timed out' in t or 'Timeout' in t or 'URLError' in t or 'Connection' in t:
        return 'внешний сервис не ответил вовремя'
    if 'HTTP Error 5' in t or '[500]' in t or '[502]' in t or '[503]' in t:
        return 'сбой на стороне внешнего сервиса'
    last = [x for x in t.strip().splitlines() if x.strip()][-1:] or ['']
    return 'ошибка в коде: ' + last[0].strip()[:140]


def crash(name: str, tb: str) -> None:
    """Короткое уведомление о сбое; номер сообщения — в data/crash.json, после успешного прогона удаляется (crash_clear)."""
    import datetime as _dt
    import json as _json
    now = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=C.TZ_OFFSET_HOURS)).strftime('%H:%M')
    msgs = send_blocks([f'⚠️ <b>{esc(name)}: прогон {now} не завершён</b>\n{esc(_why(tb))}.\n'
                        f'Повтор — следующим прогоном (≤ 2 ч), подробности — журнал GitHub.\n'
                        f'<i>Сообщение удалится само после успешного прогона.</i>'])
    f = C.DATA / 'crash.json'
    try:
        old = _json.loads(f.read_text(encoding='utf-8')) if f.exists() else []
        f.write_text(_json.dumps(old + [list(m) for m in msgs]), encoding='utf-8')
    except Exception as e:
        print(f'crash.json: {e}')


def crash_clear() -> int:
    """Удалить уведомления о сбоях после успешного прогона."""
    import json as _json
    f = C.DATA / 'crash.json'
    if not f.exists():
        return 0
    try:
        msgs = _json.loads(f.read_text(encoding='utf-8'))
    except Exception:
        msgs = []
    n = delete([tuple(m) for m in msgs]) if msgs else 0
    f.unlink(missing_ok=True)
    return n
