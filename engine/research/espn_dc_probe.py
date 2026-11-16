"""16.11: Кубок Дэвиса в ESPN и The Odds API? (решение пользователя — добавить финал 8 команд 24–29.11 в ставку дня тенниса).
ESPN: табло ATP за даты финала 2025 (18–23.11.2025) и 2026 (24–29.11.2026) — названия турниров, матчи «Davis».
The Odds API: список видов спорта (/sports — бесплатно, кредиты не тратит) — ключи с «davis». Запуск: daily.yml вход research."""
import sys, datetime as dt
sys.path.insert(0, '.')
import espn, odds as OD

for d0, n in ((dt.date(2026, 11, 16), 1), (dt.date(2025, 11, 18), 2), (dt.date(2026, 11, 24), 2)):
    for k in range(n):
        d = d0 + dt.timedelta(days=k)
        try:
            ms = list(espn.parse(espn._get(espn.URL.format(tour='atp', d=d.strftime('%Y%m%d'))), 'atp'))
        except Exception as e:
            print(d, 'ошибка', e.__class__.__name__, e); continue
        ev = sorted({m.event for m in ms})
        dc = [m for m in ms if 'davis' in str(m.event).lower()]
        print(d, f'матчей {len(ms)}; турниры: {ev}; Davis {len(dc)}',
              ('пример: ' + ' | '.join(f"{' — '.join(m.names)} {m.state} {m.location}" for m in dc[:3])) if dc else '')
try:
    js, _ = OD._get('/sports', {'all': 'true'})
    print('The Odds API, теннис (все):', [(s['key'], s.get('active')) for s in js if 'tennis' in s['key'] and ('davis' in s['key'] or 'atp' in s['key'])])
    import json, urllib.request
    raw = espn._get(espn.URL.format(tour='atp', d='20251120'))
    print('ESPN сырое 20.11.2025: events', len(raw.get('events', [])), [e.get('name') for e in raw.get('events', [])][:5])
except Exception as e:
    print('The Odds API: ошибка', e)
