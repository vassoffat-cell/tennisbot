"""27.11 (В9): разово нарисовать картинки ВСЕХ отчётов меню (лист archive обеих таблиц) → ветки cards-tn / cards-fb.
Запуск в облаке: daily.yml, вход research = engine/research/cards_backfill.py (нужны SHEET_ID и FOOT_SHEET_ID). Сообщений не шлёт. Аргумент all — перерисовать все (после правки вида)."""
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
    items = []
    if 'all' in sys.argv[1:]:                                  # перерисовать ВСЕ (после правки вида, Ц17)
        import json
        for k, t in zip(df.key.astype(str), df.text.astype(str)):
            if cardstore.BRANCH.get(k.split('|')[0], 'cards-tn') == br:
                try:
                    items.append((k, json.loads(t)))
                except Exception:
                    pass
    print(cardstore.publish(items, df, [br]))
