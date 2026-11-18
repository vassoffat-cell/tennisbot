"""19.11: TheSportsDB — полнота КХЛ по дням (сравнение с базой API-Sports 10–18.11) и статусы матчей."""
import json, time, urllib.request
B = 'https://www.thesportsdb.com/api/v1/json/123/'
def get(p):
    with urllib.request.urlopen(urllib.request.Request(B + p, headers={'User-Agent': 'tennisbot-research/1.0'}), timeout=30) as r:
        return json.loads(r.read().decode() or '{}') or {}
st = set()
for d in ['2026-11-%02d' % i for i in range(10, 19)] + ['2025-10-15', '2025-12-20', '2026-02-10', '2026-04-20']:
    ev = get(f'eventsday.php?d={d}&l=4920').get('events') or []
    st |= {e.get('strStatus') for e in ev}
    print(d, len(ev), [(e.get('strHomeTeam'), e.get('intHomeScore'), e.get('intAwayScore'), e.get('strAwayTeam'), e.get('strStatus'), e.get('strTimestamp')) for e in ev][:6])
    time.sleep(2.2)
print('статусы', st)
