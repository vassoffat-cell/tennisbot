"""A31 (16.11): разовая проба — есть ли у The Odds API (1xBet + Pinnacle) цены хоккея на рынках наших ставок дня:
победа, тотал, фора, индивидуальные тоталы, альтернативные линии. Список матчей /events — бесплатный; проба одного матча
НХЛ — ≤ 6 кредитов. Запуск: hockey.yml inputs[mode]=odds_probe. Итог — в лог и data/odds/probe_nhl.json."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import odds as O

MK = ('h2h', 'totals', 'spreads', 'team_totals', 'alternate_totals', 'alternate_team_totals')
sp, _ = O._get('/sports', {})                                        # бесплатно
hk = [s['key'] for s in sp if s.get('group') == 'Ice Hockey']
print('хоккей в The Odds API:', hk)
ev, meta = O._get('/sports/icehockey_nhl/events', dict(dateFormat='iso'))   # бесплатно
ev = sorted(ev, key=lambda e: e['commence_time'])
print('матчей НХЛ впереди:', len(ev), '| кредитов осталось', meta.get('remaining'))
if not ev or not O.budget_ok(len(MK)):
    sys.exit('нет матчей или бережём кредиты')
e = ev[0]
out, meta = O.event_odds('icehockey_nhl', e['id'], MK)
O._save_usage(meta)
(O.DIR / 'probe_nhl.json').write_text(json.dumps(dict(event=out, meta=meta), ensure_ascii=False, indent=1), encoding='utf-8')
print(f"матч {out.get('home_team')} — {out.get('away_team')} {out.get('commence_time')}; стоимость {meta.get('last')}, осталось {meta.get('remaining')}")
for b in out.get('bookmakers', []):
    for m in b.get('markets', []):
        pts = sorted({o.get('point') for o in m['outcomes'] if o.get('point') is not None})
        print(f"  {b['key']:9} {m['key']:22} исходов {len(m['outcomes']):3} линии {pts[:12]}")
