"""21.11: проба ESPN — есть ли у матчей с доп. временем (AET/PEN) счёт по таймам (linescores) → счёт 90 минут.
Запуск в облаке: daily.yml inputs[research]=engine/research/et_probe.py. Только чтение, без записи."""
import json, sys
sys.path.insert(0, 'football')
import fespn
BASE = fespn.BASE
for comp, d in [('esp.copa_del_rey', '20261107'), ('esp.copa_del_rey', '20261108'), ('ger.dfb_pokal', '20261028'),
                ('ita.coppa_italia', '20261107'), ('eng.league_cup', '20261028'), ('fra.coupe_de_france', '20261108')]:
    try:
        js = fespn._get(BASE.format(comp=comp) + f'scoreboard?dates={d}')
    except Exception as e:
        print(comp, d, 'ошибка', e); continue
    for ev in js.get('events', []):
        c = ev['competitions'][0]
        st = c['status']['type']['name']
        if 'AET' not in st and 'PEN' not in st and 'EXTRA' not in st:
            continue
        for x in c['competitors']:
            print(comp, d, st, x['homeAway'], x['team']['displayName'], 'score', x.get('score'),
                  'linescores', [l.get('value', l.get('displayValue')) for l in x.get('linescores') or []],
                  'shootout', x.get('shootoutScore'))
