"""Telegram через Bot API без постоянно запущенного бота: облако само отправляет сообщения и при каждом запуске
забирает входящие (файлы tennis-data и команды). Компьютер не нужен.

Важно: одновременно с облаком не должен работать бот на компьютере (bot.py) — Telegram отдаёт входящие только одному
получателю, второй получит ошибку 409.
"""
from __future__ import annotations

import json
import urllib.error
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


def send_blocks(blocks: list[str], chat_ids: set[int] | None = None) -> int:
    """Отправить HTML-блоки (каждый блок — законченный кусок с тегами), упаковав их в минимум сообщений."""
    msgs = _pack([b for b in blocks if b])
    if not C.BOT_TOKEN or not (chat_ids or C.ALLOWED_USERS):
        print('\n\n'.join(msgs))
        return 0
    sent = 0
    for chat in (chat_ids or C.ALLOWED_USERS):
        for m in msgs:
            try:
                _call('sendMessage', {'chat_id': chat, 'text': m, 'parse_mode': 'HTML', 'disable_web_page_preview': True})
                sent += 1
            except Exception as e:
                print(f'Telegram: не отправлено ({e})')
    return sent


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


# ======================================================================== входящие
def inbox() -> tuple[list[tuple[int, str, bytes]], list[tuple[int, str]], str]:
    """Забрать входящие: (файлы [(chat, имя, байты)], команды [(chat, текст)], примечание).
    Принимаются только сообщения от ALLOWED_USERS. Полученные сообщения подтверждаются и больше не приходят."""
    if not C.BOT_TOKEN:
        return [], [], ''
    try:
        res = _call('getUpdates', {'timeout': 0, 'allowed_updates': ['message']}).get('result', [])
    except urllib.error.HTTPError as e:
        if e.code == 409:
            return [], [], 'входящие не прочитаны: на компьютере запущен бот (выключите bot.py)'
        raise
    files, cmds, last = [], [], None
    for u in res:
        last = u['update_id']
        m = u.get('message') or {}
        chat = (m.get('chat') or {}).get('id')
        if (m.get('from') or {}).get('id') not in C.ALLOWED_USERS:
            continue
        doc = m.get('document')
        if doc and str(doc.get('file_name', '')).lower().endswith('.xlsx'):
            try:
                path = _call('getFile', {'file_id': doc['file_id']})['result']['file_path']
                url = f'https://api.telegram.org/file/bot{C.BOT_TOKEN}/{path}'
                with urllib.request.urlopen(url, timeout=60) as r:
                    files.append((chat, doc.get('file_name', ''), r.read()))
            except Exception as e:
                cmds.append((chat, f'#ошибка файла: {e}'))
        elif m.get('text'):
            cmds.append((chat, m['text'].strip()))
    if last is not None:
        _call('getUpdates', {'offset': last + 1, 'timeout': 0})        # подтвердить прочитанное
    return files, cmds, ''


def reply(chat: int, text: str) -> None:
    send_blocks([esc(text)], {chat})
