"""21.11: проба ESPN — счёт по таймам для ставок каперов на 1-й тайм / «оба тайма».
Табло (scoreboard) по таймам пусто; проверяем карточку матча (summary) по общему слагу 'all' и по слагу лиги из табло.
Запуск: daily.yml inputs[research]=engine/research/half_probe.py. Только чтение."""
import sys
sys.path.insert(0, 'football')
import fespn

IDS = ['401882805', '401884736', '401900905', '401879805', '401883112']


def show(tag, js):
    c = ((js.get('header') or {}).get('competitions') or [{}])[0]
    out = []
    for x in c.get('competitors') or []:
        out.append((x.get('homeAway'), (x.get('team') or {}).get('displayName'),
                    [l.get('displayValue', l.get('value')) for l in x.get('linescores') or []]))
    lg = ((js.get('header') or {}).get('league') or {}).get('slug')
    print(tag, 'лига', lg, out)
    return lg


sb = fespn._get(fespn.BASE.format(comp='all') + 'scoreboard?dates=20261120')
slug = {}
for ev in sb.get('events', []):
    if ev['id'] in IDS:
        c = ev['competitions'][0]
        print('табло', ev['id'], 'ключи события', sorted(ev.keys())[:15], 'ключи соревнования', sorted(c.keys())[:25])
        print('  linescores в табло', [len(x.get('linescores') or []) for x in c.get('competitors', [])],
              'league', (ev.get('league') or c.get('league') or {}).get('slug') if isinstance(ev.get('league') or c.get('league'), dict) else None,
              'uid', ev.get('uid'))
for eid in IDS:
    for comp in ('all',):
        try:
            show(f'summary[{comp}] {eid}', fespn._get(fespn.BASE.format(comp=comp) + f'summary?event={eid}'))
        except Exception as e:
            print(f'summary[{comp}] {eid} — сбой {e.__class__.__name__}: {e}')
