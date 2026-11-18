"""19.11.2026 · КХЛ: бесплатный источник истории сезона (пользователь: «ищи решение, никаких расходов»).

API-Sports (бесплатный план) отдаёт КХЛ только ±1 день от сегодня → в базе нет 2025/26 и начала 2026/27.
Проба TheSportsDB — официальный открытый JSON API (документация thesportsdb.com/documentation, бесплатный ключ «123»):
лиги хоккея → id КХЛ → матчи сезонов 2025-2026 и 2026-2027 (сколько, со счётом ли, даты), последние 15 результатов лиги.
Только чтение, без записи. Запуск: daily.yml, вход research = "engine/research/khl_free_probe.py".
"""
from __future__ import annotations

import json
import urllib.request

BASE = 'https://www.thesportsdb.com/api/v1/json/123/'


def get(path: str) -> dict:
    req = urllib.request.Request(BASE + path, headers={'User-Agent': 'tennisbot-research/1.0'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode('utf-8') or '{}') or {}


def main() -> None:
    lg = get('search_all_leagues.php?s=Ice%20Hockey').get('countries') or []
    khl = [x for x in lg if 'KHL' in (x.get('strLeague') or '') or 'Kontinental' in (x.get('strLeague') or '')
           or 'KHL' in (x.get('strLeagueAlternate') or '')]
    print('лиг хоккея:', len(lg), '| КХЛ:', [(x['idLeague'], x['strLeague'], x.get('strCurrentSeason')) for x in khl])
    ids = [x['idLeague'] for x in khl] or ['4920']           # 4920 — Russian KHL (thesportsdb.com/league/4920)
    for lid in ids[:2]:
        try:
            print('  лига:', [(x.get('strLeague'), x.get('strCurrentSeason')) for x in (get(f'lookupleague.php?id={lid}').get('leagues') or [])])
        except Exception as e:
            print('  лига: ошибка', e)
        for s in ('2024-2025', '2025-2026', '2026-2027'):
            try:
                ev = get(f'eventsseason.php?id={lid}&s={s}').get('events') or []
            except Exception as e:
                print(s, 'ошибка', e); continue
            sc = [e for e in ev if e.get('intHomeScore') not in (None, '')]
            d = sorted(e.get('dateEvent') or '' for e in ev)
            print(f'  {s}: матчей {len(ev)}, со счётом {len(sc)}, даты {d[:1]}…{d[-1:]}',
                  '| пример:', [(e.get('dateEvent'), e.get('strHomeTeam'), e.get('intHomeScore'), e.get('intAwayScore'),
                                e.get('strAwayTeam'), e.get('strStatus')) for e in sc[-2:]])
        try:
            day = get('eventsday.php?d=2026-11-17&l=4920').get('events') or []
            print('  матчи 17.11.2026:', len(day), [(e.get('strEvent'), e.get('intHomeScore'), e.get('intAwayScore')) for e in day[:3]])
        except Exception as e:
            print('  день: ошибка', e)
        try:
            past = get(f'eventspastleague.php?id={lid}').get('events') or []
            print('  последние результаты:', len(past), [(e.get('dateEvent'), e.get('strEvent'), e.get('intHomeScore'),
                                                         e.get('intAwayScore')) for e in past[:3]])
        except Exception as e:
            print('  последние: ошибка', e)


if __name__ == '__main__':
    main()
