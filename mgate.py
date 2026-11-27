"""🚪 Понедельник: новые сутки — после аудита и решений (решение пользователя 28.11 00:22, CONTROL Р9).

По понедельникам ⭐1 новых суток всех видов (🎾 ⚽ — сутки 08:00 → 08:00, 🏒 — 12:00 → 12:00) ждёт:
  13:00 аудит → ≈ 14:00 итоги и предложения в чате «Разработчик» →
  нет предложений / «нет» — сутки стартуют сразу; «да» — быстрые улучшения (≤ 1 ч) внедряются, затем «начать сутки?» → старт;
  нет ответа — старт сам в 17:00 (DEADLINE) по действующим правилам.
Старт открывает чат «Разработчик»: строка {"ГГГГ-ММ-ДД": "open"} в monday_gate.json (корень репозитория — его видят оба
workflow, у football.yml sparse-checkout берёт файлы корня) + запуск daily.yml и football.yml. Пока ворота закрыты — печать С16
и теневой портфель пишутся как обычно (hold), ⭐ не выбирается. Матчи, начавшиеся до старта, — без ⭐ (как окно 08:00–11:30, Р8).
Другие дни недели — без изменений. Откат: ON = False.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

ON = True
DEADLINE = (17, 0)                                    # 28.11: крайний срок старта суток по понедельникам (Ташкент)
FILE = Path(__file__).resolve().parent / 'monday_gate.json'


def _opened() -> dict:
    try:
        return json.loads(FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def hold(day, loc) -> str:
    """day — дата начала суток (сутки вида), loc — текущее время Ташкента (наивное). Пусто — выбирать можно."""
    if not ON:
        return ''
    d = day if isinstance(day, dt.date) else dt.date.fromisoformat(str(day)[:10])
    if d.weekday() != 0:
        return ''
    if _opened().get(str(d)) == 'open':
        return ''
    t = loc if isinstance(loc, dt.datetime) else dt.datetime.fromisoformat(str(loc)[:19])
    if t >= dt.datetime.combine(d, dt.time(*DEADLINE)):
        return ''
    return f'понедельник: ждём аудит и решения (старт суток — после них, не позже {DEADLINE[0]}:{DEADLINE[1]:02d})'
