"""28.11 отдел «Новости»: какие открытые ленты (ESPN API, RSS) доступны облаку — код ответа, число записей, пример заголовка.
Запуск: daily.yml, вход research = engine/research/news_probe.py. Сообщений не шлёт."""
import re
import urllib.request

UA = {'User-Agent': 'Mozilla/5.0 (tennisbot news probe)'}
CANDIDATES = [
    'https://site.api.espn.com/apis/site/v2/sports/tennis/atp/news?limit=5',
    'https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/news?limit=5',
    'https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/injuries',
    'https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/news?limit=5',
    'https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/injuries',
    'https://feeds.bbci.co.uk/sport/football/rss.xml',
    'https://feeds.bbci.co.uk/sport/tennis/rss.xml',
    'https://feeds.bbci.co.uk/sport/football/european/rss.xml',
    'https://www.espn.com/espn/rss/soccer/news',
    'https://www.espn.com/espn/rss/nhl/news',
    'https://www.espn.com/espn/rss/tennis/news',
    'https://www.championat.com/rss/news/football/',
    'https://www.championat.com/rss/news/hockey/',
    'https://www.championat.com/rss/news/tennis/',
    'https://www.sports.ru/rss/main.xml',
    'https://www.sport-express.ru/services/materials/news/football/se/',
    'https://www.khl.ru/rss/news/',
    'https://www.theguardian.com/football/rss',
    'https://www.theguardian.com/sport/tennis/rss',
    'https://www.skysports.com/rss/12040',
    'https://www.marca.com/rss/futbol/primera-division.xml',
    'https://www.gazzetta.it/rss/calcio.xml',
    'https://www.kicker.de/news/fussball/bundesliga/startseite/rss.xml',
    'https://www.lequipe.fr/rss/actu_rss_Football.xml',
    'https://www.ojogo.pt/rss',
    'https://www.fotomac.com.tr/rss/anasayfa.xml',
    'https://www.vi.nl/rss',
    'https://www.tennis.com/rss',
    'https://www.atptour.com/en/media/rss-feed/xml-feed',
    'https://www.nhl.com/rss/news',
    'https://t.me/s/championat',
    'https://t.me/s/sportsru',
]

for u in CANDIDATES:
    try:
        with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=20) as r:
            body = r.read(400000).decode('utf-8', 'ignore')
            code = r.status
    except Exception as ex:
        print(f'❌ {u} — {ex.__class__.__name__}: {str(ex)[:80]}')
        continue
    n = len(re.findall(r'<item[ >]', body)) or body.count('"headline"') or body.count('tgme_widget_message_text')
    t = re.search(r'<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>.*?<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', body, re.S)
    h = re.search(r'"headline"\s*:\s*"([^"]{0,90})', body)
    ex = (t.group(2) if t else (h.group(1) if h else ''))[:90]
    print(f'✅ {code} {u} — записей {n} · {ex}')
