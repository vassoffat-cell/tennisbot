"""21.11 (пользователь: «все сообщения — скрытыми, как в Telegram»): какие сочетания скрытого текста (tg-spoiler) принимает Bot API
с нашей разметкой (таблицы <pre>, свёрнутые списки <blockquote expandable>, ссылки). Каждый вариант отправляется без звука в
«Заметки Т» и СРАЗУ удаляется; в журнал — принят / ошибка Telegram. Запуск: daily.yml inputs[research]=engine/research/spoiler_probe.py."""
import json, sys, time
sys.path.insert(0, '.')
import config as C, notify
V = {
    'A текст': '<tg-spoiler>🧪 проверка формата · текст</tg-spoiler>',
    'B spoiler⊃pre': '<tg-spoiler><pre>Тотал  3/4 75%</pre></tg-spoiler>',
    'C pre⊃spoiler': '<pre><tg-spoiler>Тотал  3/4 75%</tg-spoiler></pre>',
    'D spoiler⊃b,a': '<tg-spoiler><b>⚽1 Футбол</b> · <a href="https://t.me/s/telegram">пост ↗</a></tg-spoiler>',
    'E bq⊃spoiler': '<blockquote expandable><tg-spoiler>строка 1\nстрока 2\nстрока 3\nстрока 4</tg-spoiler></blockquote>',
    'F spoiler⊃bq': '<tg-spoiler><blockquote expandable>строка 1\nстрока 2</blockquote></tg-spoiler>',
    'G bq⊃pre': '<blockquote expandable><pre>Тотал  3/4 75%</pre></blockquote>',
    'H span': '<span class="tg-spoiler">🧪 span</span>',
}
chat = sorted(C.ALLOWED_USERS)[0]
for name, html in V.items():
    try:
        r = notify._call('sendMessage', {'chat_id': chat, 'text': html, 'parse_mode': 'HTML', 'disable_notification': True, '_cover': True})
        mid = r['result']['message_id']
        ents = [e['type'] for e in r['result'].get('entities', [])]
        print(f'{name:16s} ✅ принят · сущности {ents}')
        time.sleep(0.5)
        notify._call('deleteMessage', {'chat_id': chat, 'message_id': mid})
    except Exception as e:
        print(f'{name:16s} ❌ {getattr(e, "tg_body", e)}')
