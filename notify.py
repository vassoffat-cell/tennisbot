"""Сообщения в Telegram напрямую через Bot API (без запуска бота): авто-прогнозы и отчёты из облака."""
from __future__ import annotations

import json
import urllib.request

import config as C


def esc(s: str) -> str:
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def send(text: str, pre: bool = False) -> int:
    """Отправить всем из ALLOWED_USERS; длинный текст режется по строкам на куски до 3500 символов."""
    if not C.BOT_TOKEN or not C.ALLOWED_USERS:
        print(text)
        return 0
    chunks, cur = [], ''
    for line in text.split('\n'):
        if len(cur) + len(line) + 1 > 3500:
            chunks.append(cur); cur = ''
        cur += line + '\n'
    if cur.strip():
        chunks.append(cur)
    sent = 0
    for chat in C.ALLOWED_USERS:
        for ch in chunks:
            body = f'<pre>{esc(ch)}</pre>' if pre else ch
            data = json.dumps({'chat_id': chat, 'text': body, 'parse_mode': 'HTML',
                               'disable_web_page_preview': True}).encode()
            req = urllib.request.Request(f'https://api.telegram.org/bot{C.BOT_TOKEN}/sendMessage', data=data,
                                         headers={'Content-Type': 'application/json'})
            try:
                urllib.request.urlopen(req, timeout=30).read()
                sent += 1
            except Exception as e:
                print(f'Telegram: не отправлено ({e})')
    return sent
