"""📰 Отдел «Новости» (решение пользователя 28.11 01:14, CONTROL Ц23, ORG отдел 11).

Задача: читать новости о травмах, снятиях, дисквалификациях, ротации, мотивации, тренерах, конфликтах и интригах, слухах,
погоде, финансах — по всем нашим видам спорта и лигам — и связывать каждую новость с нашими матчами ближайших 48 ч.

Этап 1 (внедрён 28.11): сбор открытых лент (RSS и публичные превью Telegram — проверены облаком, news_probe.py), метки,
привязка к матчам, журнал data/news/feed.csv.gz и сводка data/news/match_news.json (матч → новости). В выбор ⭐ и тексты
ботов пока НЕ входит: сначала лаборатория (пн) проверяет на истории, дают ли новости прирост (n, z); показ «📰» в ⭐ и в
вердиктах каперов и влияние на выбор — только по решению пользователя (полная дорога ORG §2).
Источники — открытые ленты (RSS сделаны для чтения программами; t.me/s — публичное превью, как у каперов). Внутренние API
сайтов не используются. Платные ленты — предложением (Ц22).
"""
from __future__ import annotations

import ast
import datetime as dt
import gzip
import hashlib
import html
import json
import re
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
DIR = ROOT / 'data' / 'news'
FEED = DIR / 'feed.csv.gz'
MATCHES = DIR / 'match_news.json'
UA = {'User-Agent': 'Mozilla/5.0 (StandingInMotion newsdesk)'}
EVERY_MIN = 55                                   # не чаще раза в час
WINDOW_H = 48                                    # матчи на 48 ч вперёд

# (id, адрес, вид ленты, виды спорта) — все проверены облаком 28.11 (news_probe.py: 200, записи есть)
FEEDS = [
    ('bbc_fb', 'https://feeds.bbci.co.uk/sport/football/rss.xml', 'rss', 'fb'),
    ('bbc_eu', 'https://feeds.bbci.co.uk/sport/football/european/rss.xml', 'rss', 'fb'),
    ('bbc_tn', 'https://feeds.bbci.co.uk/sport/tennis/rss.xml', 'rss', 'tn'),
    ('guardian_fb', 'https://www.theguardian.com/football/rss', 'rss', 'fb'),
    ('guardian_tn', 'https://www.theguardian.com/sport/tennis/rss', 'rss', 'tn'),
    ('sky_fb', 'https://www.skysports.com/rss/12040', 'rss', 'fb'),
    ('marca', 'https://www.marca.com/rss/futbol/primera-division.xml', 'rss', 'fb'),
    ('gazzetta', 'https://www.gazzetta.it/rss/calcio.xml', 'rss', 'fb'),
    ('fotomac', 'https://www.fotomac.com.tr/rss/anasayfa.xml', 'rss', 'fb'),
    ('vi', 'https://www.vi.nl/rss', 'rss', 'fb'),
    ('champ_fb', 'https://www.championat.com/rss/news/football/', 'rss', 'fb'),
    ('champ_hk', 'https://www.championat.com/rss/news/hockey/', 'rss', 'hk'),
    ('champ_tn', 'https://www.championat.com/rss/news/tennis/', 'rss', 'tn'),
    ('sportexpress', 'https://www.sport-express.ru/services/materials/news/football/se/', 'rss', 'fb'),
    ('tg_championat', 'https://t.me/s/championat', 'tg', 'fb hk tn'),
    ('tg_sportsru', 'https://t.me/s/sportsru', 'tg', 'fb hk tn'),
]

# метки: травма, не сыграет, болезнь, дисквалификация, ротация, мотивация, тренер, конфликт, слух, погода, финансы (RU/EN/ES/IT/TR/NL)
TAGS = {
    'травма': r'injur|травм|повре[жд]|растяж|надрыв|перелом|hamstring|knee|ankle|groin|lesi[oó]n|infortun|sakat|blessur|verletz',
    'не сыграет': r'ruled out|will miss|out for|withdr[ae]w|walkover|пропуст|не сыграет|не примет участ|снял(ся|ась)|отказал(ся|ась)|baja\b|forfait|kadroda yok|niet inzetbaar',
    'болезнь': r'\bill\b|illness|virus|болез|болеет|грипп|температур|enferm|malat|hastal',
    'дисквалификация': r'suspen|\bban(ned)?\b|red card|дисквал|удален|sanci[oó]n|squalific|cezal',
    'ротация': r'rotat|rested|резерв|ротац|дубл|turnover|wisselspeler',
    'мотивация': r'derby|дерби|relegat|вылет|title race|чемпионств|must.win|реванш|принципиал|revenge|motivat|мотивац|lider|scudetto',
    'тренер': r'sack|head coach|manager|тренер|отставк|уволен|назначен|entrenador|allenatore|teknik direkt|\btrainer\b',
    'конфликт': r'\brow\b|feud|clash with|скандал|конфликт|ссор|бунт|разборк|polemic|crisis|кризис|kriz',
    'слух': r'rumou?r|transfer|трансфер|слух|переход|инсайд|insider|fichaje|calciomercato|iddia',
    'погода': r'weather|\brain\b|snow|storm|wind|погод|дожд|снег|мороз|ветер|lluvia|pioggia',
    'финансы': r'wage|salary|unpaid|задолж|зарплат|долг[иа]? |bankrupt|банкрот',
}
TAG_RX = {k: re.compile(v, re.I) for k, v in TAGS.items()}
GENERIC = {'city', 'united', 'real', 'club', 'sporting', 'athletic', 'town', 'county', 'rovers', 'wanderers', 'albion',
           'hotspur', 'football', 'calcio', 'deportivo', 'olympique', 'stade', 'inter', 'dynamo', 'dinamo', 'saint'}


def _get(url: str) -> str:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
        return r.read(1_500_000).decode('utf-8', 'ignore')


def _clean(s: str) -> str:
    s = re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'\1', s or '', flags=re.S)
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', s))).strip()


def _when(s: str) -> str:
    try:
        return parsedate_to_datetime(s).astimezone(dt.timezone.utc).strftime('%Y-%m-%d %H:%M')
    except Exception:
        try:
            return pd.Timestamp(s).tz_convert('UTC').strftime('%Y-%m-%d %H:%M')
        except Exception:
            return ''


def parse_rss(body: str) -> list[dict]:
    out = []
    for it in re.findall(r'<item[\s>](.*?)</item>', body, re.S):
        g = lambda tag: (re.search(rf'<{tag}[^>]*>(.*?)</{tag}>', it, re.S) or [None, ''])[1]
        out.append(dict(title=_clean(g('title')), summary=_clean(g('description'))[:400], link=_clean(g('link')),
                        published=_when(_clean(g('pubDate')) or _clean(g('dc:date')))))
    return out


def parse_tg(body: str) -> list[dict]:
    out = []
    for chunk in body.split('tgme_widget_message_wrap')[1:]:
        post = re.search(r'data-post="([^"]+)"', chunk)
        txt = re.search(r'tgme_widget_message_text[^>]*>(.*?)</div>', chunk, re.S)
        tm = re.search(r'<time[^>]*datetime="([^"]+)"', chunk)
        if not (post and txt):
            continue
        t = _clean(txt.group(1))
        out.append(dict(title=t[:160], summary=t[:400], link=f'https://t.me/{post.group(1)}',
                        published=_when(tm.group(1)) if tm else ''))
    return out


def tags(text: str) -> list[str]:
    return [k for k, rx in TAG_RX.items() if rx.search(text)]


def _literal(path: Path, name: str):
    """Справочник из модуля без импорта (ALIAS футбола, KHL/NHL хоккея) — ast.literal_eval присваивания."""
    try:
        for node in ast.parse(path.read_text(encoding='utf-8')).body:
            if isinstance(node, ast.Assign) and any(getattr(t, 'id', '') == name for t in node.targets):
                return ast.literal_eval(node.value)
    except Exception:
        pass
    return None


def _team_names(team: str) -> list[str]:
    t = team.lower().strip()
    out = [t]
    for w in re.findall(r'[a-zà-ÿ]{5,}', t):
        if w not in GENERIC:
            out.append(w)
    return out


def targets(now: dt.datetime | None = None) -> list[dict]:
    """Наши матчи на 48 ч вперёд: ключ, вид, начало, латинские имена, русские регэкспы."""
    now = now or dt.datetime.utcnow()
    lo, hi = now - dt.timedelta(hours=6), now + dt.timedelta(hours=WINDOW_H)
    out: list[dict] = []
    try:                                                                      # 🎾 снимок суток: id → [игрок 1, игрок 2, начало]
        for mid, (p1, p2, st) in json.loads((ROOT / 'data/seal/tennis_names.json').read_text(encoding='utf-8')).items():
            if lo <= pd.Timestamp(st).to_pydatetime() <= hi:
                out.append(dict(key=f'tn|{mid}', sport='tn', start=st, label=f'{p1} — {p2}',
                                lat=[p1.split()[-1].lower(), p2.split()[-1].lower()], ru=[], surn=True))
    except Exception:
        pass
    try:                                                                      # 🏒 НХЛ и КХЛ: матчи «pre»
        rus = (_literal(ROOT / 'hockey/hcappers.py', 'KHL') or []) + (_literal(ROOT / 'hockey/hcappers.py', 'NHL') or [])
        g = pd.read_csv(ROOT / 'hockey/data/games.csv.gz')
        g = g[g.state.astype(str) == 'pre']
        st = pd.to_datetime(g.start, errors='coerce')
        for _, r in g[(st >= lo) & (st <= hi)].iterrows():
            names = _team_names(r.home) + _team_names(r.away)
            ru = [a for a, b in rus if re.search(b, r.home.lower()) or re.search(b, r.away.lower())]
            out.append(dict(key=f'hk|{r.id}', sport='hk', start=str(r.start), label=f'{r.home} — {r.away}', lat=names, ru=ru, surn=False))
    except Exception:
        pass
    try:                                                                      # ⚽ теневой портфель: матчи с ценой (кандидаты ⭐)
        alias = _literal(ROOT / 'football/tips.py', 'ALIAS') or {}
        fs = sorted((ROOT / 'football/data/shadow').glob('football_*.csv'))[-2:]
        f = pd.concat([pd.read_csv(x, usecols=['espn_id', 'start_utc', 'match']) for x in fs], ignore_index=True).drop_duplicates('espn_id')
        st = pd.to_datetime(f.start_utc, errors='coerce')
        for _, r in f[(st >= lo) & (st <= hi)].iterrows():
            h, a = (str(r.match).split(' — ') + [''])[:2]
            names = _team_names(h) + _team_names(a)
            ru = [re.escape(k) for k, v in alias.items() if v and (v in h.lower() or v in a.lower())]
            out.append(dict(key=f'fb|{r.espn_id}', sport='fb', start=str(r.start_utc), label=str(r.match), lat=names, ru=ru, surn=False))
    except Exception:
        pass
    return out


_NM = None


def _names():
    """Корневой names.py (у футбола свой names.py — поэтому явный путь)."""
    global _NM
    if _NM is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location('names_root', ROOT / 'names.py')
        _NM = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_NM)
    return _NM


def link(item: dict, tg: list[dict]) -> list[str]:
    """Ключи наших матчей, о которых новость (по имени команды / фамилии; русский текст — по справочникам и транслиту)."""
    text = f"{item['title']} {item['summary']}".lower()
    cyr = re.findall(r'[а-яё]{4,}', text)
    hits = []
    for t in tg:
        if t['sport'] not in item['sports']:
            continue
        ok = any(re.search(rf'\b{re.escape(n)}\b', text) for n in t['lat'] if len(n) >= 4) or any(re.search(rx, text) for rx in t['ru'])
        if not ok and t['surn'] and cyr:                                       # 🎾 «Медведев» → medvedev
            try:
                NM = _names()
                ok = any(NM._sim(NM.translit(w), s) >= 0.88 for w in cyr for s in t['lat'] if len(s) >= 4)
            except Exception:
                pass
        if ok:
            hits.append(t['key'])
    return hits


def run(rt: dict, force: bool = False) -> str:
    """Раз в час: все ленты → новые записи с метками и привязкой к матчам → журнал + сводка по матчам. Сбой ленты — пропуск."""
    now = dt.datetime.utcnow()
    st = rt.setdefault('newsdesk', {})
    if not force and st.get('at') and (now - dt.datetime.fromisoformat(st['at'])).total_seconds() < EVERY_MIN * 60:
        return ''
    st['at'] = now.isoformat(timespec='seconds')
    DIR.mkdir(parents=True, exist_ok=True)
    old = pd.read_csv(FEED, dtype=str) if FEED.exists() else pd.DataFrame()
    seen = set(old['id']) if len(old) else set()
    tg = targets(now)
    rows, ok, bad = [], 0, []
    for fid, url, kind, sports in FEEDS:
        try:
            items = (parse_rss if kind == 'rss' else parse_tg)(_get(url))
            ok += 1
        except Exception:
            bad.append(fid)
            continue
        for it in items:
            if not it['title']:
                continue
            nid = hashlib.sha1((it['link'] or it['title']).encode()).hexdigest()[:16]
            if nid in seen:
                continue
            seen.add(nid)
            it['sports'] = sports.split()
            text = f"{it['title']} {it['summary']}"
            rows.append(dict(id=nid, src=fid, sports=sports, published=it['published'], fetched=now.strftime('%Y-%m-%d %H:%M'),
                             title=it['title'][:300], summary=it['summary'], link=it['link'], tags=';'.join(tags(text)),
                             matches=';'.join(link(it, tg))))
    new = pd.DataFrame(rows)
    allf = pd.concat([old, new], ignore_index=True) if len(new) else old
    if len(allf):
        cut = (now - dt.timedelta(days=120)).strftime('%Y-%m-%d')            # журнал — 120 дней (для проверки на истории)
        allf = allf[allf.fetched.astype(str) >= cut]
        with gzip.open(FEED, 'wt', encoding='utf-8') as fh:
            allf.to_csv(fh, index=False)
    by: dict[str, list] = {}                                                  # сводка: матч → новости за 72 ч
    if len(allf):
        recent = allf[(allf.fetched.astype(str) >= (now - dt.timedelta(hours=72)).strftime('%Y-%m-%d %H:%M'))
                      & allf.matches.fillna('').astype(str).ne('')]
        for _, r in recent.iterrows():
            for k in str(r.matches).split(';'):
                by.setdefault(k, []).append(dict(t=r.title, tags=str(r.tags) if str(r.tags) != 'nan' else '', src=r.src,
                                                 at=r.published or r.fetched, link=r.link))
    lab = {t['key']: t['label'] for t in tg}
    MATCHES.write_text(json.dumps({k: dict(match=lab.get(k, ''), news=v[-10:]) for k, v in by.items() if k in lab},
                                  ensure_ascii=False, indent=1), encoding='utf-8')
    linked = int(new.matches.astype(str).ne('').sum()) if len(new) else 0
    tg_cnt: dict[str, int] = {}
    for x in (new.tags if len(new) else []):
        for t in str(x).split(';'):
            if t:
                tg_cnt[t] = tg_cnt.get(t, 0) + 1
    top = ', '.join(f'{k} {v}' for k, v in sorted(tg_cnt.items(), key=lambda z: -z[1])[:5])
    return (f'📰 новости: лент {ok}/{len(FEEDS)}{" (нет: " + ", ".join(bad) + ")" if bad else ""} · новых {len(new)} · '
            f'к нашим матчам {linked} (матчей {len([k for k in by if k in lab])} из {len(tg)}) · {top or "меток нет"}')
