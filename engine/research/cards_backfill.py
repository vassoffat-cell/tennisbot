"""27.11 (В9): разово нарисовать картинки ВСЕХ отчётов меню (лист archive обеих таблиц) → ветки cards-tn / cards-fb.
Запуск в облаке: daily.yml, вход research = engine/research/cards_backfill.py (нужны SHEET_ID и FOOT_SHEET_ID). Сообщений не шлёт."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import cardstore  # noqa: E402
import config as C  # noqa: E402
import journal as J  # noqa: E402

cardstore.BACKFILL = 100000
for sid, br in ((os.getenv('SHEET_ID'), 'cards-tn'), (os.getenv('FOOT_SHEET_ID'), 'cards-fb')):
    if not sid:
        print(f'{br}: нет ID таблицы — пропуск')
        continue
    C.SHEET_ID = sid
    st = J.Store()
    df = st.read('archive')
    print(f'{br}: строк архива {len(df)}')
    print(cardstore.publish([], df, [br]))
