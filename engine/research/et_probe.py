"""21.11: проба ESPN — счёт 90 минут у матчей с доп. временем (AET/PEN): scoreboard не даёт счёт по таймам (linescores пусто),
проверяем summary (keyEvents: голы с минутой и периодом). Запуск: daily.yml inputs[research]=engine/research/et_probe.py. Только чтение."""
import sys
sys.path.insert(0, 'football')
import fespn
for comp, eid in [('eng.league_cup', None), ('eng.fa', '401931105')]:
    if eid is None:
        js = fespn._get(fespn.BASE.format(comp=comp) + 'scoreboard?dates=20261028')
        eid = next(ev['id'] for ev in js['events'] if 'PEN' in ev['competitions'][0]['status']['type']['name'])
    s = fespn._get(fespn.BASE.format(comp=comp) + f'summary?event={eid}')
    print(comp, eid, 'ключи', list(s.keys())[:20])
    for k in s.get('keyEvents', []) or []:
        t = (k.get('type') or {}).get('text', '')
        if 'oal' in t or k.get('scoringPlay'):
            print('  ', t, 'период', (k.get('period') or {}).get('number'), 'минута', (k.get('clock') or {}).get('displayValue'),
                  'команда', (k.get('team') or {}).get('displayName'), 'scoringPlay', k.get('scoringPlay'), 'shootout', k.get('shootout'))
    hdr = (s.get('header') or {}).get('competitions', [{}])[0]
    for x in hdr.get('competitors', []):
        print('  header', x.get('homeAway'), x.get('team', {}).get('displayName'), 'score', x.get('score'),
              'linescores', [l.get('displayValue') for l in x.get('linescores') or []])
