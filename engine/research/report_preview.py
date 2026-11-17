"""Просмотр отчёта ③ (ставки дня) тенниса и хоккея по живым листам — без отправки (daily.yml вход research)."""
import datetime as dt
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import journal as J  # noqa: E402
import report as R  # noqa: E402
from hockey import hrun, hpick  # noqa: E402,F401

store = J.Store()
day = dt.date(2026, 11, 17)
for b in R.dp_report(store.read('day_pick'), f'🎾 Теннис · ставки дня · {day:%d.%m}', day):
    print(re.sub(r'<[^>]+>', '', b).replace('&lt;', '<').replace('&gt;', '>'), '\n')
for b in hrun.dp_report(store.read('hk_day_pick'), f'🏒 Хоккей · ставки дня · {day:%d.%m}', day):
    print(re.sub(r'<[^>]+>', '', b).replace('&lt;', '<').replace('&gt;', '>'), '\n')
