"""Дешёвая проверка здоровья за последние N часов (по умолчанию 26) — вместо перечитывания чата и всех логов.

Запуск (чат разработки, аудит, автопилот): python3 engine/health.py [часы]
Читает только GitHub API: итоги прогонов по workflow, плановые запуски, которые не случились, и ключевые строки
ТОЛЬКО тех прогонов, где должны быть контрольные события (engine/CONTROL.md, раздел «Контрольные точки»).
Вывод — таблица «точка | ожидание | факт | ✅/❌». Логи целиком не скачиваются.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

REPO = 'vassoffat-cell/tennisbot'
TZ = timedelta(hours=5)                     # Ташкент


def gh(path: str) -> dict:
    return json.loads(subprocess.run(['gh', 'api', path], capture_output=True, text=True, check=True).stdout)


def runs(wf: str, since: datetime) -> list[dict]:
    js = gh(f'repos/{REPO}/actions/workflows/{wf}/runs?per_page=100&created=>={since:%Y-%m-%dT%H:%M:%SZ}')
    return js.get('workflow_runs', [])


def log(run_id: int) -> str:
    return subprocess.run(['gh', 'run', 'view', str(run_id), '--repo', REPO, '--log'], capture_output=True, text=True).stdout


def grep(text: str, pat: str) -> list[str]:
    return [re.sub(r'^.*?Z ', '', x)[:160] for x in text.splitlines() if re.search(pat, x)]


def main(hours: int = 26) -> None:
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    rows = []
    for wf, per_day in (('daily.yml', 15), ('football.yml', 15), ('cappers.yml', 24)):
        rs = runs(wf, since)
        bad = [r for r in rs if r['conclusion'] not in ('success', None)]
        exp = int(per_day * hours / 24)
        rows.append((f'{wf}: прогонов', f'≈ {exp}', f'{len(rs)} (сбоев {len(bad)})', '✅' if not bad and len(rs) >= exp * 0.7 else '❌'))
        for r in bad[:5]:
            rows.append(('  сбой', '', f"{(datetime.fromisoformat(r['created_at'].replace('Z', '+00:00')) + TZ):%d.%m %H:%M} {r['event']}", '❌'))
    # контрольные события: ищем в прогонах нужного часа (по местному времени начала)
    checks = [('daily.yml', (8, 9), r'ставка дня: объявлена', 'теннис: 🎾1 объявлена утром'),
              ('daily.yml', (10, 12), r'суточный отчёт отправлен', 'теннис: суточный отчёт'),
              ('football.yml', (8, 9), r'ставка дня: объявлена', 'футбол: ⚽1 объявлена утром'),
              ('football.yml', (10, 12), r'суточный отчёт отправлен|отчёт отправлен', 'футбол: суточный отчёт'),
              ('daily.yml', (12, 14), r'🏒 суточный отчёт', 'хоккей: отчёт за сутки 12:00→12:00'),
              ('daily.yml', (12, 14), r'⭐🏒 .*⭐1|⭐🏒 \d', 'хоккей: 🏒1 объявлена'),
              ('daily.yml', (0, 24), r'Traceback|сбой', 'теннис/хоккей: сбои внутри прогонов (последний прогон)'),
              ('football.yml', (0, 24), r'Traceback|сбой', 'футбол: сбои внутри прогонов (последний прогон)')]
    cache: dict[int, str] = {}
    for wf, (h0, h1), pat, name in checks:
        rs = [r for r in runs(wf, since) if r['conclusion'] == 'success']
        if (h0, h1) != (0, 24):
            rs = [r for r in rs if h0 <= (datetime.fromisoformat(r['created_at'].replace('Z', '+00:00')) + TZ).hour < h1]
        else:
            rs = rs[:1]
        hit = []
        for r in rs:
            cache.setdefault(r['id'], log(r['id']))
            hit += grep(cache[r['id']], pat)
        bad = 'сбо' in name
        ok = (not hit) if bad else bool(hit)
        rows.append((name, 'нет' if bad else 'есть', (hit[0] if hit else '—') + (f' (+{len(hit) - 1})' if len(hit) > 1 else ''), '✅' if ok else '❌'))
    w = max(len(r[0]) for r in rows)
    print(f'Здоровье за {hours} ч (до {(now + TZ):%d.%m %H:%M} Ташкент)')
    for a, b, c, d in rows:
        print(f'{d} {a:<{w}} | {b:<8} | {c}')


if __name__ == '__main__':
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 26)
