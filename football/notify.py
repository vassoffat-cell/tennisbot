"""Telegram через Bot API (футбольный бот): облако отправляет и удаляет сообщения. Входящие принимает бот на Apps Script."""
from __future__ import annotations

import json
import urllib.request

import config as C

API = 'https://api.telegram.org/bot{token}/{method}'


def esc(s: str) -> str:
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _call(method: str, payload: dict | None = None, timeout: int = 30) -> dict:
    data = json.dumps(payload or {}).encode()
    req = urllib.request.Request(API.format(token=C.BOT_TOKEN, method=method), data=data,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


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


def send_blocks(blocks: list[str], chat_ids: set[int] | None = None, silent: bool = False) -> list[tuple[int, int]]:
    """Отправить HTML-блоки (каждый блок — законченный кусок с тегами), упаковав их в минимум сообщений.
    silent — без звука уведомления. Возвращает [(chat_id, message_id)] отправленных сообщений."""
    msgs = _pack([b for b in blocks if b])
    if not C.BOT_TOKEN or not (chat_ids or C.ALLOWED_USERS):
        print('\n\n'.join(msgs))
        return []
    sent = []
    for chat in (chat_ids or C.ALLOWED_USERS):
        for m in msgs:
            try:
                r = _call('sendMessage', {'chat_id': chat, 'text': m, 'parse_mode': 'HTML',
                                          'disable_web_page_preview': True, 'disable_notification': silent})
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
