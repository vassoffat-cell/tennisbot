"""Прогноз без Telegram (для проверки): python analyze.py скрин1.png скрин2.png …
Флаг --log записывает прогноз в журнал (Google Таблица или logs/*.csv)."""
import sys

import journal as J
import pipeline as P

if __name__ == '__main__':
    files = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not files:
        raise SystemExit(__doc__)
    m = P.recognize(files)
    print(f'Тур: {m.tour}; турнир: {m.parsed.tournament}; покрытие: {m.parsed.surface}; линий: {len(m.parsed.lines)}')
    if not m.ids:
        raise SystemExit('Игроки не распознаны: отправьте скриншот с шапкой матча (файлом, без сжатия).')
    for ru, c in zip(m.parsed.players, m.candidates):
        print(f'  {ru} → {c[0][1]} (сходство {c[0][2]:.2f}); другие: ' + ', '.join(f'{x[1]} {x[2]:.2f}' for x in c[1:]))
    if not P.confident(m):
        print('ВНИМАНИЕ: игрок найден неуверенно — проверьте имена выше.')
    fc, text = P.forecast(m)
    print(text.replace('◀', ' <<'))   # в консоли Windows символа ◀ нет в шрифте
    if '--log' in sys.argv:
        print('Журнал:', J.Journal().log(m, fc))
