"""⏱ Минуты GitHub Actions и режим репозитория (решение пользователя 27.11 19:42, CONTROL Ц19).

Правило: на аудите, саморазвитии и развитии не экономим — работаем максимально. Приватный репозиторий тратит 2 000 мин в месяц;
как только минуты на исходе — репозиторий переводится в public (стандартные раннеры бесплатны), с 1-го числа — снова private.
Переключает пользователь (GitHub не даёт это сделать облаку и чату) — бот присылает ему одно напоминание:
  • private и израсходовано ≥ ALERT мин → «⚠️ минуты GitHub N / 2 000 — переведите в public»;
  • public и 1–3 число месяца → «🔒 новый месяц — верните private».
Не чаще раза в 6 ч проверка, одно напоминание каждого вида в месяц. Счёт — [РАСЧЁТ] по прогонам (Billing API облаку недоступен):
длительность каждого прогона, округлённая вверх до минуты, с 1-го числа месяца (UTC) — так же, как engine/health.py.
"""
from __future__ import annotations

import json
import math
import os
import urllib.request
from datetime import datetime, timedelta, timezone

REPO = os.getenv('GITHUB_REPOSITORY', 'vassoffat-cell/tennisbot')
LIMIT = 2000
ALERT = 1850                     # запас ≈ 2 суток при темпе 86 мин/сутки (27.11)
EVERY_H = 6


def _get(path: str, token: str) -> dict:
    rq = urllib.request.Request(f'https://api.github.com/{path}', headers={
        'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json', 'User-Agent': 'tennisbot'})
    with urllib.request.urlopen(rq, timeout=30) as r:
        return json.loads(r.read().decode())


def _dur(r: dict) -> float:
    try:
        a = datetime.fromisoformat((r.get('run_started_at') or r['created_at']).replace('Z', '+00:00'))
        return (datetime.fromisoformat(r['updated_at'].replace('Z', '+00:00')) - a).total_seconds()
    except Exception:
        return 0.0


def used(token: str, now: datetime | None = None) -> int:
    """Минуты всех workflow с 1-го числа месяца (UTC)."""
    now = now or datetime.now(timezone.utc)
    m0 = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total = 0
    for w in _get(f'repos/{REPO}/actions/workflows?per_page=100', token).get('workflows', []):
        for page in range(1, 16):
            js = _get(f"repos/{REPO}/actions/workflows/{w['id']}/runs?per_page=100&page={page}"
                      f"&created=>={m0:%Y-%m-%dT%H:%M:%SZ}", token)
            got = js.get('workflow_runs', [])
            total += sum(math.ceil(max(_dur(r), 1) / 60) for r in got)
            if len(got) < 100:
                break
    return total


def message(private: bool, n: int, day: int, sent: dict, month: str) -> tuple[str, str] | None:
    """Чистая логика (тест): (вид напоминания, текст) или None."""
    if private and n >= ALERT and sent.get('public') != month:
        return 'public', (f'⚠️ <b>Минуты GitHub: {n} / {LIMIT}</b>\nПереведите репозиторий в public до 1-го числа (Ц19): '
                          'GitHub → tennisbot → Settings → Danger Zone → Change visibility → Make public.')
    if not private and day <= 3 and sent.get('private') != month:
        return 'private', ('🔒 <b>Новый месяц — минуты GitHub обновились</b>\nВерните репозиторий в private (Ц19): '
                           'Settings → Danger Zone → Change visibility → Make private.')
    return None


def watch(rt: dict) -> str:
    """Вызов из autorun.run: проверка раз в 6 ч, одно напоминание каждого вида в месяц. Сбой — строка в лог."""
    token = os.getenv('GITHUB_TOKEN', '')
    if not token or not os.getenv('GITHUB_ACTIONS'):
        return ''
    now = datetime.now(timezone.utc)
    st = rt.setdefault('ghmin', {})
    try:
        if st.get('ts') and now - datetime.fromisoformat(st['ts']) < timedelta(hours=EVERY_H):
            return ''
        private = bool(_get(f'repos/{REPO}', token).get('private'))
        n = used(token, now) if private else 0
        st.update(ts=now.isoformat(timespec='seconds'), private=private, used=n)
        month = now.strftime('%Y-%m')
        msg = message(private, n, (now + timedelta(hours=5)).day, st.setdefault('sent', {}), month)
        if msg:
            import notify
            notify.send_blocks([msg[1]])
            st['sent'][msg[0]] = month
            return f'⏱ минуты GitHub: {n} / {LIMIT} · напоминание «{msg[0]}» отправлено (Ц19)'
        return f'⏱ минуты GitHub: {"private, " + str(n) + " / " + str(LIMIT) if private else "public — без лимита"}'
    except Exception as ex:
        return f'⏱ минуты GitHub: сбой {ex.__class__.__name__}: {str(ex)[:100]}'
