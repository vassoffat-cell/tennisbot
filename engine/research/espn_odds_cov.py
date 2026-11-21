"""21.11: охват цен DraftKings на общем табло ESPN (футбол, все лиги) — для вердикта каперам по рынку вне наших лиг."""
import sys, datetime as dt
sys.path.insert(0, 'football')
import fespn, tipswide as W
for d in (dt.date.today(), dt.date.today() + dt.timedelta(days=1)):
    ms = fespn.parse(fespn._get(W.ALL.format(d=d)), 'all')
    pre = [m for m in ms if m.state == 'pre']
    ok = [m for m in pre if m.odds.get('h')]
    print(d, 'матчей до начала', len(pre), 'с ценой 1X2', len(ok), 'с тоталом', sum(1 for m in ok if m.odds.get('line')))
    for m in pre[:400]:
        if any(k in m.home + m.away for k in ('Rostov', 'Zenit', 'Spartak', 'CSKA', 'Al ', 'Brugge', 'Leganes', 'Westerlo', 'Gent',
                                               'Sevilla', 'Bayern', 'Krasnodar', 'Fenerbahce', 'Celtic', 'Benfica')):
            print('  ', m.home, '—', m.away, m.odds or 'нет цены')
