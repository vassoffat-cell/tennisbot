"""🔏 Проверка честности снимков суток (CONTROL С16) — для аудита и чата разработки.

Запуск: python3 engine/seal_check.py [дней=7]
Для каждого вида (теннис, хоккей, футбол) и каждых суток окна:
  • есть ли снимок <data>/seal/<вид>_ГГГГ-ММ-ДД.csv, сколько в нём матчей и линий;
  • первый коммит файла (время ставит GitHub, через API — работает и в клоне --depth 1) — должен быть РАНЬШЕ начала
    первого матча суток: тогда линии записаны до игр, а не задним числом;
  • ставка дня суток (data/runtime.json → day_pick, только текущие сутки) есть в снимке — та же линия.
Сверка ставок дня недели со снимками (лист day_pick / hk_day_pick) — вручную по выводу: match_id + рынок + исход.
Вывод — таблица «вид | сутки | матчи | линии | версий | первый коммит | первый матч | ✅/❌».
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

REPO = 'vassoffat-cell/tennisbot'
ROOT = Path(__file__).resolve().parent.parent
TZ = timedelta(hours=5)
FOLDERS = {'tennis': 'data', 'hockey': 'hockey/data', 'football': 'football/data'}


def commits(path: str) -> list[datetime]:
    """Времена коммитов файла (UTC) по GitHub API, от старых к новым."""
    r = subprocess.run(['gh', 'api', f'repos/{REPO}/commits?path={path}&per_page=100'], capture_output=True, text=True)
    if r.returncode:
        return []
    return sorted(datetime.fromisoformat(c['commit']['committer']['date'].replace('Z', '+00:00')) for c in json.loads(r.stdout))


def main(days: int = 7) -> None:
    today = (datetime.now(timezone.utc) + TZ).date()
    rows, bad = [], 0
    for sport, folder in FOLDERS.items():
        for k in range(days):
            day = today - timedelta(days=k)
            rel = f'{folder}/seal/{sport}_{day}.csv'
            f = ROOT / rel
            if not f.exists():
                rows.append((sport, f'{day:%d.%m}', '—', '—', '—', '—', '—', '⬜ нет снимка'))
                continue
            df = pd.read_csv(f, dtype=str, keep_default_na=False)
            st = pd.to_datetime(df.start_utc, errors='coerce').min()
            cs = commits(rel)
            first = cs[0] if cs else None
            ok = bool(first) and pd.notna(st) and first.replace(tzinfo=None) <= st.to_pydatetime()
            bad += not ok
            rows.append((sport, f'{day:%d.%m}', df.match_id.nunique(), len(df), len(cs),
                         f'{(first + TZ):%d.%m %H:%M}' if first else '—',
                         f'{(st + TZ):%d.%m %H:%M}' if pd.notna(st) else '—', '✅' if ok else '❌'))
    w = [8, 6, 6, 6, 6, 12, 12, 12]
    head = ('вид', 'сутки', 'матчи', 'линии', 'версий', 'первый коммит', 'первый матч', 'итог')
    print(' | '.join(str(h).ljust(n) for h, n in zip(head, w)))
    for r in rows:
        print(' | '.join(str(x).ljust(n) for x, n in zip(r, w)))
    # ставка дня текущих суток — есть ли в снимке
    for sport, folder in (('tennis', 'data'), ('football', 'football/data')):
        try:
            dp = json.loads((ROOT / folder / 'runtime.json').read_text(encoding='utf-8')).get('day_pick', {})
            key = dp.get('key')
            if not key:
                print(f'⭐ {sport} {dp.get("date", "")}: ставки дня нет')
                continue
            f = ROOT / folder / 'seal' / f'{sport}_{dp["date"]}.csv'
            df = pd.read_csv(f, dtype=str, keep_default_na=False) if f.exists() else pd.DataFrame(columns=['match_id', 'market', 'pick'])
            hit = df[(df.match_id == str(key[0])) & (df.market == str(key[1])) & (df.pick == str(key[2]))]
            bad += hit.empty
            print(f'⭐ {sport} {dp["date"]}: {dp.get("label", key)} — в снимке {"✅" if len(hit) else "❌ нет"}')
        except Exception as e:
            print(f'⭐ {sport}: проверка не выполнена ({e.__class__.__name__})')
    print(f'\nИтог: {"✅ снимки честные" if not bad else f"❌ нарушений {bad} — разобрать (CONTROL §3)"}'
          '\n⬜ нет снимка — норма для суток до 15.11 (печать с 15.11) и для суток без матчей.')


if __name__ == '__main__':
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 7)
