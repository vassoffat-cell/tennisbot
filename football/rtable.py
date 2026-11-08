"""Табличный вид отчётов (решение пользователя 08.11.2026): как лист Excel, одна страница, минимум букв.

Столбцы: «Линия (цель)» | «Кол-во» (угадано/всего) | «%» (+ цвет 🟢🟡🔴 в конце строки).
Цель в скобках — то, с чем сравнивается цвет: ступень точности (исход, линии, ставка дня) или ожидание модели Σp/n (рынки,
корзины калибровки). Таблица — моноширинный блок <pre> Telegram без пустых строк (Apps Script режет текст по пустой строке).
Общий для обоих ботов (копия — football/rtable.py).
"""
from __future__ import annotations

import notify

Row = tuple[str, str, str, str]          # (линия, кол-во, %, цвет); раздел — (название, '', '', '')


def pct(x: float, d: int = 1) -> str:
    return f'{x * 100:.{d}f}'.replace('.', ',')


def num(x: float, d: int = 1) -> str:
    return f'{x:.{d}f}'.replace('.', ',')


def row(name: str, won: int, n: int, goal: str = '', mark: str = '') -> Row:
    """Строка «название (цель) | won/n | won/n %»."""
    return (f'{name} ({goal})' if goal else name, f'{int(won)}/{int(n)}', pct(won / n) if n else '—', mark)


def render(rows: list[Row], head: tuple[str, str, str] = ('Линия (цель)', 'Кол-во', '%')) -> str:
    """Моноширинная таблица: ширина столбцов по содержимому, числа — по правому краю."""
    rows = [r for r in rows if r]
    data = [r for r in rows if r[1] or r[2]] or [head + ('',)]   # заголовки разделов в ширину не входят
    w1 = max(len(head[0]), *(len(r[0]) for r in data))
    w2 = max(len(head[1]), *(len(r[1]) for r in data))
    w3 = max(len(head[2]), *(len(r[2]) for r in data))
    out = [f'{head[0]:<{w1}} {head[1]:>{w2}} {head[2]:>{w3}}', '─' * (w1 + w2 + w3 + 2)]
    for name, cnt, p, mark in rows:
        if not cnt and not p:                                   # заголовок раздела
            out.append(name)
            continue
        out.append(f'{name:<{w1}} {cnt:>{w2}} {p:>{w3}}' + (f' {mark}' if mark else ''))
    return '<pre>' + notify.esc('\n'.join(out)) + '</pre>'


SHORT = {'ML': 'Победа', 'HCP': 'Фора', 'AH': 'Фора', 'IT1': 'ИТ1', 'IT2': 'ИТ2', 'TOTAL': 'Тотал', 'TOT': 'Тотал',
         'DC': 'Дв. шанс', 'BTTS': 'Обе забьют'}
