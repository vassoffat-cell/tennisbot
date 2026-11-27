"""Telegram через Bot API (футбольный бот): облако отправляет и удаляет сообщения. Входящие принимает бот на Apps Script."""
from __future__ import annotations

import html
import json
import re
import time
import urllib.error
import urllib.request

import config as C

API = 'https://api.telegram.org/bot{token}/{method}'


def esc(s: str) -> str:
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


BOTS = {'main': lambda: C.BOT_TOKEN, 'tennis': lambda: getattr(C, 'TENNIS_BOT_TOKEN', '')}   # 'tennis' — из футбольного облака
_SENT: set[str] = set()                         # боты, которым в этом прогоне ушли новые сообщения (для заглушки 🔒)
_CNT: dict[str, int] = {}                        # 15.11: сколько сообщений ушло боту в этом прогоне (контрольная точка)
COVER = '🔒 ·'                                  # не одиночный эмодзи — без крупной анимации


HIDE = True                     # 21.11 (решение пользователя): все сообщения ботов — скрытым текстом Telegram (tg-spoiler)
PRE_PLAIN = True                # 23.11: <pre>/<code> снимаются — Telegram не прячет их под spoiler (spoiler_probe.py)
_KEEP = re.compile(r'(<pre>.*?</pre>|<code>.*?</code>|<blockquote[^>]*>|</blockquote>)', re.S)


def hide(text: str) -> str:
    """HTML → тот же текст под «скрытым» (spoiler): открыл чат — текст под точками, нажал — виден; вышел — снова скрыт.
    Проба 21.11 (engine/research/spoiler_probe.py): Telegram принимает spoiler вокруг текста, жирного, ссылок и внутри/вокруг
    свёрнутых списков; таблицу <pre> спрятать нельзя — spoiler вокруг неё Telegram отбрасывает. Поэтому скрывается всё, кроме <pre>/<code>."""
    if not HIDE or not text or 'tg-spoiler' in text:
        return text
    if PRE_PLAIN:                # 23.11 (решение пользователя): таблицы <pre>/<code> — простым текстом, тоже под spoiler
        text = re.sub(r'</?(?:pre|code)>', '', text)
    out = []
    for part in _KEEP.split(text):
        if not part or _KEEP.fullmatch(part) or not part.strip():
            out.append(part)
            continue
        core = part.strip()
        a, b = part[:len(part) - len(part.lstrip())], part[len(part.rstrip()):]
        out.append(f'{a}<tg-spoiler>{core}</tg-spoiler>{b}')
    return ''.join(out)


def _call(method: str, payload: dict | None = None, timeout: int = 30, bot: str = 'main') -> dict:
    """Вызов Bot API. 14.11 (доставка): сеть / таймаут / 429 / 5xx — до 3 попыток с паузой (429 — сколько просит Telegram).
    Ошибка 400 (неверный запрос) не повторяется — её разбирает вызывающий (send_blocks)."""
    if method in ('sendMessage', 'sendPhoto', 'sendDocument') and not (payload or {}).get('_cover'):
        _SENT.add(bot)
        _CNT[bot] = _CNT.get(bot, 0) + 1
    (payload or {}).pop('_cover', None)
    if method in ('sendMessage', 'editMessageText') and payload and payload.get('parse_mode') == 'HTML':
        payload = dict(payload, text=hide(payload.get('text', '')))      # 21.11: скрытый текст во всех сообщениях
    data = json.dumps(payload or {}).encode()
    for k in range(TRIES):
        req = urllib.request.Request(API.format(token=BOTS[bot](), method=method), data=data,
                                     headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            try:
                e.tg_body = e.read().decode('utf-8', 'replace')[:300]
            except Exception:
                e.tg_body = ''
            wait = 0
            if e.code == 429:
                try:
                    wait = min(30, int(json.loads(e.tg_body).get('parameters', {}).get('retry_after', 3)) + 1)
                except Exception:
                    wait = 5
            elif e.code >= 500:
                wait = 3 * (k + 1)
            if not wait or k == TRIES - 1:
                raise
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            if k == TRIES - 1:
                raise
            time.sleep(3 * (k + 1))
    raise RuntimeError('Telegram: попытки исчерпаны')


_TAG = re.compile(r'<(/?)(b|i|u|s|code|pre|blockquote|a)(\s[^>]*)?>')


def _split_html(text: str, limit: int = 3800) -> list[str]:
    """14.11: сообщение длиннее лимита Telegram — по строкам, открытые теги (<pre>, <blockquote>, <b> …) закрываются в конце
    части и открываются в начале следующей (ни один отчёт не теряется из-за «message is too long»)."""
    if len(text) <= limit:
        return [text]
    out, cur, stack = [], '', []

    def close(st):
        return ''.join(f'</{n}>' for n, _ in reversed(st))

    def reopen(st):
        return ''.join(o for _, o in st)

    lines = []
    for line in text.split('\n'):                               # одна строка длиннее лимита — режется по пробелу
        while len(line) > limit - 300:
            cut = line.rfind(' ', 0, limit - 300)
            cut = cut if cut > 0 else limit - 300
            lines.append(line[:cut]); line = line[cut:].lstrip()
        lines.append(line)
    for line in lines:
        add = ('\n' if cur else '') + line
        if cur and len(cur) + len(add) + len(close(stack)) > limit:
            out.append(cur + close(stack))
            cur = reopen(stack) + line
        else:
            cur += add
        for m in _TAG.finditer(line):
            if m.group(1):
                for i in range(len(stack) - 1, -1, -1):
                    if stack[i][0] == m.group(2):
                        del stack[i]
                        break
            else:
                stack.append((m.group(2), m.group(0)))
    if cur:
        out.append(cur + close(stack))
    return out


def _plain(text: str) -> str:
    """HTML → простой текст (запасной вариант, если Telegram не разобрал разметку)."""
    return html.unescape(re.sub(r'<[^>]+>', '', text))


def _send_one(chat: int, m: str, silent: bool, bot: str) -> int | None:
    """Одно сообщение с запасными путями: разметка не разобрана → простой текст; «too long» → мельче части."""
    p = {'chat_id': chat, 'text': m, 'parse_mode': 'HTML', 'disable_web_page_preview': True, 'disable_notification': silent}
    try:
        return _call('sendMessage', p, bot=bot)['result']['message_id']
    except urllib.error.HTTPError as e:
        body = getattr(e, 'tg_body', '')
        if e.code == 400 and 'parse entities' in body:      # 21.11: и запасной простой текст — скрытым
            p = dict(p, text=esc(_plain(m)[:3900]))
            return _call('sendMessage', p, bot=bot)['result']['message_id']
        if e.code == 400 and 'too long' in body:
            mid = None
            for part in _split_html(m, 3000):
                mid = _send_one(chat, part, silent, bot)
            return mid
        raise


FAILED: list[dict] = []                         # 14.11: не доставлено в этом прогоне → undelivered.json → повтор в следующем
TRIES = 3


def folded(head: str, items: list[str], limit: int = 3500) -> list[str]:
    """Турнир: заголовок + матчи в сворачиваемой цитате Telegram (<blockquote expandable>, разворачивается касанием).
    Длинный турнир делится на несколько цитат, заголовок повторяется с пометкой «продолжение»."""
    out, cur, h = [], [], head
    parts = []
    for it in items:                                          # 14.11: пункт длиннее лимита (неделя «Дв. шанс» — 240 линий) — по строкам
        if len(it) <= limit:
            parts.append(it); continue
        buf = ''
        for line in it.split('\n'):
            if buf and len(buf) + len(line) + 1 > limit:
                parts.append(buf); buf = ''
            buf = f'{buf}\n{line}' if buf else line
        if buf:
            parts.append(buf)
    for it in parts:
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


def _why_tg(e: Exception, text: str = '') -> str:
    """Текст ошибки Telegram (описание из ответа) + начало сообщения — чтобы причина 400 была видна в журнале."""
    body = getattr(e, 'tg_body', '')
    if not body:
        try:
            body = e.read().decode('utf-8', 'replace')[:200]              # urllib.error.HTTPError
        except Exception:
            pass
    return f'{e} {body} · начало: {text[:80]!r}'


def send_blocks(blocks: list[str], chat_ids: set[int] | None = None, silent: bool = False,
                bot: str = 'main', queue: bool = True) -> list[tuple[int, int]]:
    """Отправить HTML-блоки (каждый блок — законченный кусок с тегами), упаковав их в минимум сообщений.
    silent — без звука уведомления; bot — 'main' (свой бот) или 'tennis' (теннисный — из футбольного облака).
    Возвращает [(chat_id, message_id)] отправленных сообщений."""
    msgs = _pack([b for b in blocks if b])
    if not BOTS[bot]() or not (chat_ids or C.ALLOWED_USERS):
        print('\n\n'.join(msgs))
        return []
    msgs = [p for m in msgs for p in _split_html(m)]
    sent = []
    for chat in (chat_ids or C.ALLOWED_USERS):
        for m in msgs:
            try:
                mid = _send_one(chat, m, silent, bot)
                if mid is not None:
                    sent.append((chat, mid))
            except Exception as e:
                print(f'Telegram: не отправлено ({_why_tg(e, m)})' + (' — в очередь повтора' if queue else ''))
                if queue:                                     # закреп ставки дня повторяет сам цикл ставки (queue=False)
                    FAILED.append(dict(bot=bot, chat=int(chat), text=m, silent=bool(silent), tries=0))
    return sent


CARDS = True                    # 27.11 (решение пользователя): отчёты ①②③ — картинкой (cards.py, светлый фон); свёрнутые списки — текстом


def _photo(chat: int, png: bytes, caption: str, silent: bool, bot: str) -> int | None:
    """sendPhoto с has_spoiler (картинка размыта, пока не нажата — как скрытый текст В3б). Multipart без сторонних библиотек."""
    import uuid
    b = uuid.uuid4().hex
    fields = {'chat_id': str(chat), 'caption': hide(caption), 'parse_mode': 'HTML', 'has_spoiler': 'true',
              'disable_notification': 'true' if silent else 'false'}
    body = b''.join(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items())
    body += (f'--{b}\r\nContent-Disposition: form-data; name="photo"; filename="card.png"\r\nContent-Type: image/png\r\n\r\n'.encode()
             + png + f'\r\n--{b}--\r\n'.encode())
    _SENT.add(bot)
    _CNT[bot] = _CNT.get(bot, 0) + 1
    for k in range(TRIES):
        req = urllib.request.Request(API.format(token=BOTS[bot](), method='sendPhoto'), data=body,
                                     headers={'Content-Type': f'multipart/form-data; boundary={b}'})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())['result']['message_id']
        except urllib.error.HTTPError as e:
            if e.code < 500 and e.code != 429 or k == TRIES - 1:
                raise
            time.sleep(3 * (k + 1))
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            if k == TRIES - 1:
                raise
            time.sleep(3 * (k + 1))
    return None


def send_card(blocks: list[str], chat_ids: set[int] | None = None, silent: bool = False,
              bot: str = 'main', queue: bool = True, extra: list | None = None) -> list[tuple[int, int]]:
    """27.11: отчёт картинкой (cards.render) + свёрнутые списки текстом. Сбой отрисовки или отправки фото — тот же отчёт текстом
    (ничего не теряется; текст при сбое уходит в очередь повтора Д4)."""
    blocks = [b for b in blocks if b]
    if not CARDS or not blocks:
        return send_blocks(blocks, chat_ids, silent, bot, queue)
    try:
        import cards
        pngs, texts, title = cards.render(blocks)
    except Exception as e:
        print(f'🖼 картинка: сбой {e.__class__.__name__}: {e} — отчёт текстом')
        return send_blocks(blocks, chat_ids, silent, bot, queue)
    if not pngs:
        return send_blocks(blocks, chat_ids, silent, bot, queue)
    pngs = pngs + [x for x in (extra or []) if x]                # 27.11 этап 3: график банка в недельном ③
    if not BOTS[bot]() or not (chat_ids or C.ALLOWED_USERS):
        print(f'🖼 {title}: картинок {len(pngs)} ({sum(len(p) for p in pngs) // 1024} КБ) + текстом {len(texts)}')
        return []
    sent = []
    for chat in (chat_ids or C.ALLOWED_USERS):
        try:
            for k, png in enumerate(pngs):
                cap = f'<b>{esc(title)}</b>' + (f' · {k + 1}/{len(pngs)}' if len(pngs) > 1 else '')
                mid = _photo(chat, png, cap, silent, bot)
                if mid is not None:
                    sent.append((chat, mid))
        except Exception as e:
            print(f'🖼 фото не отправлено ({_why_tg(e)}) — отчёт текстом')
            sent += send_blocks(blocks, {chat}, silent, bot, queue)
            continue
        if texts:
            sent += send_blocks(texts, {chat}, silent, bot, queue)
    return sent


PIN_CARDS = True                # 27.11 этап 2: закреп ставки дня — картинкой (cards.render_pick); правка — editMessageMedia


def _media_edit(chat: int, mid: int, png: bytes, caption: str, bot: str = 'main') -> None:
    """Заменить картинку закрепа (правка / снятие ⭐): editMessageMedia, has_spoiler сохраняется."""
    import uuid
    b = uuid.uuid4().hex
    media = json.dumps({'type': 'photo', 'media': 'attach://card', 'caption': hide(caption), 'parse_mode': 'HTML', 'has_spoiler': True})
    fields = {'chat_id': str(chat), 'message_id': str(mid), 'media': media}
    body = b''.join(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items())
    body += (f'--{b}\r\nContent-Disposition: form-data; name="card"; filename="card.png"\r\nContent-Type: image/png\r\n\r\n'.encode()
             + png + f'\r\n--{b}--\r\n'.encode())
    req = urllib.request.Request(API.format(token=BOTS[bot](), method='editMessageMedia'), data=body,
                                 headers={'Content-Type': f'multipart/form-data; boundary={b}'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            r.read()
    except urllib.error.HTTPError as e:
        t = e.read().decode('utf-8', 'replace')[:200]
        if 'not modified' not in t:
            print(f'Telegram: картинка не изменена ({e.code} {t})')


def _pick_caption(text: str) -> str:
    first = _plain(text.split('\n', 1)[0]).strip()
    return f'<b>{esc(first)}</b>'


def send_pick(text: str, silent: bool = False, bot: str = 'main') -> list[tuple[int, int]]:
    """27.11 этап 2: закреп ставки дня картинкой; сбой отрисовки / отправки — тем же текстом (send_markup)."""
    if not PIN_CARDS:
        return send_markup(text, None, silent)
    try:
        import cards
        png = cards.render_pick(text)
    except Exception as e:
        print(f'🖼 ставка дня: сбой отрисовки {e.__class__.__name__}: {e} — текстом')
        return send_markup(text, None, silent)
    if not BOTS[bot]() or not C.ALLOWED_USERS:
        print(f'🖼 ставка дня картинкой ({len(png) // 1024} КБ): {_plain(text)[:80]}')
        return []
    sent = []
    for chat in C.ALLOWED_USERS:
        try:
            mid = _photo(chat, png, _pick_caption(text), silent, bot)
            if mid is not None:
                sent.append((chat, mid))
        except Exception as e:
            print(f'🖼 ставка дня: фото не отправлено ({_why_tg(e)}) — текстом')
            try:
                mid = _send_one(chat, text, silent, bot)
                if mid is not None:
                    sent.append((chat, mid))
            except Exception as e2:
                print(f'Telegram: не отправлено ({e2})')
    return sent


def redeliver(path) -> str:
    """14.11: очередь недоставленных (файл path, рядом с cover.json): повтор каждого прогона с пометкой «↩️ доставлено
    повторно»; до 12 попыток (≈ сутки), затем — запись в журнал. Новые недоставленные этого прогона — в очередь."""
    import pathlib
    f = pathlib.Path(path)
    try:
        q = json.loads(f.read_text()) if f.exists() else []
    except Exception:
        q = []
    ok = lost = 0
    keep = []
    for x in q:
        if not BOTS.get(x.get('bot', 'main'), lambda: '')():
            keep.append(x); continue
        try:
            _send_one(int(x['chat']), '↩️ <b>доставлено повторно</b>\n' + x['text'], bool(x.get('silent')), x.get('bot', 'main'))
            ok += 1
        except Exception as e:
            x['tries'] = int(x.get('tries', 0)) + 1
            if x['tries'] < 12:
                keep.append(x)
            else:
                lost += 1
                print(f'Telegram: НЕ ДОСТАВЛЕНО после 12 попыток ({_why_tg(e, x["text"])})')
    keep += FAILED
    n_new = len(FAILED)
    FAILED.clear()
    if keep or f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(keep[-50:], ensure_ascii=False))
    return f'доставка: повторно {ok}, в очереди {len(keep)} (новых {n_new})' + (f', потеряно {lost}' if lost else '')


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
            body = getattr(e, 'tg_body', '')
            if 'no text in the message' in body:              # 27.11: закреп — картинка → заменить картинку
                try:
                    import cards
                    _media_edit(int(chat), int(mid), cards.render_pick(text), _pick_caption(text))
                    n += 1
                except Exception as e2:
                    print(f'🖼 правка картинки: сбой {e2.__class__.__name__}: {e2}')
                continue
            if 'not modified' not in str(e) + body:
                print(f'Telegram: не изменено ({_why_tg(e, text)})')
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


def slot(now, minute: int, hours=None, back: bool = False):
    """Плановое время прогона (UTC, naive): ближайшее после now (back=False) или последнее не позже now (back=True)."""
    import datetime as _d
    hours = set(hours) if hours else set(range(24))
    t = now.replace(minute=minute, second=0, microsecond=0)
    step = _d.timedelta(hours=-1 if back else 1)
    while (t > now if back else t < now) or t.hour not in hours:
        t += step
    return t


def status(head: str, quiet: str, n: int, nxt=None, missed: bool = False) -> str:
    """15.11 (решение пользователя): контрольная точка вместо «🔒 ·» — видно, что проверка прошла и что нового нет.
    head — что проверено, quiet — текст «новостей нет», n — сколько сообщений ушло в этом прогоне, nxt — следующая проверка (UTC)."""
    import datetime as _d
    off = _d.timedelta(hours=C.TZ_OFFSET_HOURS)
    now = _d.datetime.now(_d.timezone.utc).replace(tzinfo=None)
    s = f'🔒 {(now + off):%H:%M} · {head} ✓\n' + (quiet if n == 0 else f'прислано {n} ↑')
    if nxt is not None:
        s += f' · далее {(nxt + off):%H:%M}'
    if missed:
        s += '\n⚠️ прошлая проверка пропущена'
    return s


def cover(path, spec: dict | None = None) -> int:
    """🔒 Контрольная точка последним сообщением чата (превью скрыто: в списке чатов не видно, что прислал бот).
    Ставится в конце КАЖДОГО прогона; прошлая точка этого прогона удаляется (файл path).
    spec: {бот: функция(n отправленных) -> текст}; без spec — «🔒 ·» (как раньше)."""
    import pathlib
    f = pathlib.Path(path)
    try:                                                          # 14.11: сначала — повтор недоставленного (очередь рядом)
        print(redeliver(f.parent / 'undelivered.json'))
    except Exception as e:
        print(f'Telegram: очередь повтора — сбой {e.__class__.__name__}')
    old = json.loads(f.read_text()) if f.exists() else {}
    n = 0
    spec = spec or {}
    _SENT.add('main')            # 12.11: заглушка — после КАЖДОГО прогона (списков нет → иначе ответ на кнопку остаётся на виду)
    _SENT.update(spec)
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
        text = COVER
        if bot in spec:
            try:
                text = spec[bot](_CNT.get(bot, 0))
            except Exception as e:
                print(f'Telegram: текст контрольной точки — сбой {e.__class__.__name__}')
        cur = []
        for chat in C.ALLOWED_USERS:
            try:
                r = _call('sendMessage', {'chat_id': chat, 'text': esc(text), 'parse_mode': 'HTML', 'disable_notification': True, '_cover': 1},
                          bot=bot)                                   # 21.11: контрольная точка — тоже скрытым текстом
                cur.append([chat, r['result']['message_id']]); n += 1
            except Exception as e:
                print(f'Telegram: заглушка не отправлена ({e})')
        old[bot] = cur
    _SENT.clear()
    _CNT.clear()
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


def alive(messages: list) -> bool:
    """Сообщения бота ещё существуют? Пользователь мог удалить их или очистить чат (12.11: удалённые ставки дня — заново).
    Пустая правка кнопок: «not modified» — живо; «not found» / «can't be edited» — удалено. Сеть / прочее — считаем живым."""
    if not C.BOT_TOKEN or not messages:
        return True
    for chat, mid in messages:
        try:
            _call('editMessageReplyMarkup', {'chat_id': int(chat), 'message_id': int(mid), 'reply_markup': {'inline_keyboard': []}})
        except urllib.error.HTTPError as e:
            body = getattr(e, 'tg_body', '')                     # тело ответа уже прочитано в _call (14.11)
            if 'not found' in body or "can't be edited" in body or 'MESSAGE_ID_INVALID' in body:
                return False
        except Exception:
            pass
    return True
