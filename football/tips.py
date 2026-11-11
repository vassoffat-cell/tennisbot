"""👥 Каперы (ТЗ-005, 11.11.2026): чужие прогнозы как источник данных — сбор, привязка к нашим матчам, учёт точности.

Источники (только публичные страницы):
  • Telegram — веб-лента канала https://t.me/s/<канал> (последние ~20 постов, без входа в аккаунт);
  • nb-bet.com/feed — открытая лента прогнозов (robots.txt разрешает), автор, матч, ставка, кф.
Каждый прогон (раз в 2 ч): новые посты → поиск наших матчей (обе команды в тексте) → разбор ставки
(П1/X/П2, 1X/X2/12, фора, ТБ/ТМ, ИТ, «обе забьют», связки «A + B») → вероятность по матрице счёта модели.
Угловые, карточки, таймы, лайв, хоккей и матчи вне нашего охвата — не учитываются (их нечем проверить).

Учёт (файл data/tips/tips.csv):
  • точность источника — угадано / рассчитано и ожидание модели Σp/n по тем же ставкам;
    z = (угадано − Σp) / √Σp(1−p): z ≥ 1,64 при n ≥ 30 — источник знает больше модели («надёжный» ✔️);
  • спор — ставки, с которыми модель не согласна (p·k < 1, без кф — p < 0,5): кто оказался прав;
  • ROI по кф источника (если кф указан).
Влияние на решения: пока источник не «надёжный» — только показ (👥 в списке и в ставке дня).
«Надёжный» источник получает право вето: кандидат в ставку дня, несовместимый с его ставкой
(совместная вероятность по модели < 5%), не выбирается.
"""
from __future__ import annotations

import difflib
import html
import json
import math
import re
import time
import unicodedata
import urllib.request

import numpy as np
import pandas as pd

import config as C
import flines as FL

DIR = C.DATA / 'tips'
TIPS = DIR / 'tips.csv'
SEEN = DIR / 'seen.json'
TG = {'stavki': 'Прогнозы', 'xs_stavkii': 'Хитрости', 'groznyi_ruslan': 'Грозный',
      'kapper_uk': 'Каппер UK', 'stavki_na_sport_betting1': 'Green Line'}
NB = 'nb-bet'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'
COLS = ['uid', 'src', 'author', 'post_ts', 'seen_ts', 'espn_id', 'comp_name', 'start_utc', 'home', 'away', 'legs', 'label',
        'k', 'p_model', 'agree', 'raw', 'status', 'result', 'settled']
TRUST_N, TRUST_Z = 30, 1.64


# ------------------------------------------------------------------ загрузка страниц
def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Language': 'ru'})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode('utf-8', 'replace')


def _clean(s: str) -> str:
    s = re.sub(r'<br\s*/?>', '\n', s)
    return html.unescape(re.sub(r'<[^>]+>', '', s)).strip()


LAST_POSTS: list[tuple[str, str, dict]] = []      # свежие посты прогона (src, имя, пост) — для tipswide (все матчи)
LAST_FEED: list[dict] = []                          # лента nb-bet прогона


def tg_posts(channel: str, before: int | None = None) -> list[dict]:
    """Посты веб-ленты канала: id, время (UTC), текст. Медиа без текста пропускаются. before — страница старше id."""
    s = _get(f'https://t.me/s/{channel}' + (f'?before={before}' if before else ''))
    out = []
    for b in s.split('<div class="tgme_widget_message_wrap')[1:]:
        pid = re.search(r'data-post="[^"/]+/(\d+)"', b)
        ts = re.search(r'<time datetime="([^"]+)"', b)
        tx = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', b, re.S)
        if pid and ts and tx:
            out.append(dict(id=int(pid.group(1)), ts=pd.Timestamp(ts.group(1)).tz_convert('UTC').tz_localize(None),
                            text=_clean(tx.group(1))))
    return out


def nb_feed() -> list[dict]:
    """Открытая лента nb-bet.com/feed: автор, команды, ставка, кф (время — относительное, берём момент чтения)."""
    s = _get('https://nb-bet.com/feed')
    s = re.sub(r'src="data:[^"]*"', '', s)
    out = []
    for b in re.split(r'<div class="[^"]*no-line[^"]*">', s)[1:]:
        prof = re.search(r'href="https://nb-bet\.com/profile/(\d+)"><div[^>]*><div>([^<]*)</div><div class="(\w+)">([^<]*)', b)
        ev = re.search(r'href="https://nb-bet\.com/Events/(\d+)-[^"]*"', b)
        teams = re.findall(r'<div>([^<>]{2,60})</div>', b[ev.end():] if ev else '')
        sel = re.search(r'<div class="[^"]*">([^<>]{1,40})</div><div class="[^"]*">(\d+\.\d+)</div>', b)
        if ev and sel and len(teams) >= 2:
            out.append(dict(id=f"{prof.group(1) if prof else '0'}:{ev.group(1)}:{sel.group(1)}",
                            author=html.unescape(prof.group(2)) if prof else '', home=html.unescape(teams[0]),
                            away=html.unescape(teams[1]), sel=html.unescape(sel.group(1)), k=float(sel.group(2))))
    return out


# ------------------------------------------------------------------ команды: русское название → наш матч
TR = dict(zip('абвгдеёжзийклмнопрстуфхцчшщъыьэюя',
              ['a', 'b', 'v', 'g', 'd', 'e', 'e', 'zh', 'z', 'i', 'y', 'k', 'l', 'm', 'n', 'o', 'p', 'r', 's', 't', 'u', 'f',
               'h', 'ts', 'ch', 'sh', 'sch', '', 'y', '', 'e', 'yu', 'ya']))
# русское слово (или пара слов) → ключ латиницей, который ищется в названии ESPN
ALIAS = {
    'мю': 'manchester united', 'ман юнайтед': 'manchester united', 'манчестер юнайтед': 'manchester united',
    'ман сити': 'manchester city', 'манчестер сити': 'manchester city', 'сити': 'manchester city',
    'юнайтед': 'manchester united', 'челси': 'chelsea', 'ливерпуль': 'liverpool', 'арсенал': 'arsenal',
    'тоттенхэм': 'tottenham', 'тоттенхем': 'tottenham', 'вулверхэмптон': 'wolverhampton', 'вулверхэмптон уондерерс': 'wolverhampton',
    'вулвз': 'wolverhampton', 'брайтон': 'brighton', 'ноттингем': 'nottingham', 'ноттингем форест': 'nottingham',
    'ньюкасл': 'newcastle', 'вест хэм': 'west ham', 'эвертон': 'everton', 'фулхэм': 'fulham', 'борнмут': 'bournemouth',
    'кристал пэлас': 'crystal palace', 'сандерленд': 'sunderland', 'лидс': 'leeds', 'бернли': 'burnley',
    'брентфорд': 'brentford', 'астон вилла': 'aston villa', 'вилла': 'aston villa',
    'реал мадрид': 'real madrid', 'реал': 'real madrid', 'барселона': 'barcelona', 'барса': 'barcelona',
    'атлетико': 'atletico', 'атлетик': 'athletic', 'реал сосьедад': 'sociedad', 'сосьедад': 'sociedad',
    'бетис': 'betis', 'вильярреал': 'villarreal', 'севилья': 'sevilla', 'валенсия': 'valencia', 'хетафе': 'getafe',
    'жирона': 'girona', 'мальорка': 'mallorca', 'осасуна': 'osasuna', 'алавес': 'alaves', 'эспаньол': 'espanyol',
    'сельта': 'celta', 'леванте': 'levante', 'эльче': 'elche', 'овьедо': 'oviedo', 'райо': 'rayo', 'райо вальекано': 'rayo',
    'ювентус': 'juventus', 'интер': 'internazionale', 'милан': 'ac milan', 'наполи': 'napoli', 'рома': 'roma',
    'лацио': 'lazio', 'аталанта': 'atalanta', 'фиорентина': 'fiorentina', 'торино': 'torino', 'удинезе': 'udinese',
    'комо': 'como', 'парма': 'parma', 'верона': 'verona', 'пиза': 'pisa', 'сассуоло': 'sassuolo', 'кремонезе': 'cremonese',
    'болонья': 'bologna', 'дженоа': 'genoa', 'лечче': 'lecce', 'кальяри': 'cagliari',
    'бавария': 'bayern', 'боруссия д': 'dortmund', 'дортмунд': 'dortmund', 'боруссия м': 'monchengladbach',
    'гладбах': 'monchengladbach', 'лейпциг': 'leipzig', 'байер': 'leverkusen', 'леверкузен': 'leverkusen',
    'айнтрахт': 'frankfurt', 'вольфсбург': 'wolfsburg', 'фрайбург': 'freiburg', 'хоффенхайм': 'hoffenheim',
    'штутгарт': 'stuttgart', 'унион': 'union berlin', 'вердер': 'werder', 'гамбург': 'hamburg', 'санкт-паули': 'pauli',
    'хайденхайм': 'heidenheim', 'аугсбург': 'augsburg', 'майнц': 'mainz', 'кельн': 'koln',
    'псж': 'paris saint', 'пари сен-жермен': 'paris saint', 'марсель': 'marseille', 'лион': 'lyon',
    'монако': 'monaco', 'лилль': 'lille', 'ренн': 'rennes', 'ланс': 'lens', 'нант': 'nantes', 'страсбур': 'strasbourg',
    'страсбург': 'strasbourg', 'тулуза': 'toulouse', 'брест': 'brest', 'лорьян': 'lorient', 'мец': 'metz', 'ницца': 'nice',
    'осер': 'auxerre', 'анже': 'angers', 'гавр': 'havre', 'париж': 'paris fc',
    'рио аве': 'rio ave', 'каза пия': 'casa pia', 'сент-этьен': 'etienne', 'лаваль': 'laval', 'бенфика': 'benfica', 'порту': 'porto', 'спортинг': 'sporting', 'брага': 'braga', 'витория': 'vitoria',
    'аякс': 'ajax', 'псв': 'psv', 'фейеноорд': 'feyenoord', 'аз': 'az alkmaar', 'твенте': 'twente', 'утрехт': 'utrecht',
    'гронинген': 'groningen', 'херенвен': 'heerenveen', 'неймеген': 'nec', 'спарта': 'sparta',
    'галатасарай': 'galatasaray', 'фенербахче': 'fenerbahce', 'бешикташ': 'besiktas', 'трабзонспор': 'trabzonspor',
    'башакшехир': 'basaksehir', 'гент': 'gent', 'брюгге': 'brugge', 'андерлехт': 'anderlecht', 'селтик': 'celtic',
    'рейнджерс': 'rangers', 'зальцбург': 'salzburg', 'копенгаген': 'copenhagen', 'олимпиакос': 'olympiacos',
}


def _fold(s: str) -> str:
    s = unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9 ]+', ' ', s).strip()


def _tr(s: str) -> str:
    s = re.sub(r'(^|[^а-яё])у(?=[аеиоэ])', r'\1w', s.lower())          # «Уиган», «Уолсолл» → w
    return ''.join(TR.get(c, c) for c in s)


def _skel(s: str) -> str:
    """Согласный скелет для сравнения транслита с английским написанием: Blackpool / blekpul → blkpl."""
    s = s.lower()
    for a, b in (('ck', 'k'), ('ph', 'f'), ('x', 'ks'), ('q', 'k'), ('c', 'k'), ('w', 'v'), ('z', 's'), ('j', 'g'), ('h', '')):
        s = s.replace(a, b)
    s = re.sub(r'[aeiouy\-\s]', '', s)
    return re.sub(r'(.)\1+', r'\1', s)


def _like(w: str, x: str) -> bool:
    if difflib.SequenceMatcher(None, w, x).ratio() >= 0.84:
        return True
    a, b = _skel(w), _skel(x)
    return len(a) >= 3 and len(b) >= 3 and (a == b or (min(len(a), len(b)) >= 4 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.85))


STOP = {'fc', 'cf', 'ac', 'sc', 'afc', 'cd', 'ud', 'sv', 'vfl', 'vfb', 'tsg', 'ss', 'ssc', 'as', 'rc', 'rcd', 'ogc', 'us',
        'club', 'de', 'la', 'the', 'and', 'hove', 'albion', 'city', 'united', 'real', 'sporting', 'fk', 'sk', 'bk', 'if'}


def _words(text: str) -> list[str]:
    return re.findall(r'[а-яёa-z0-9\-]+', text.lower())


def _alias(w: str) -> str | None:
    """Псевдоним с учётом падежа: «Баварии» → «бавария», «Сент-Этьена» → «сент-этьен»."""
    for v in (w, w[:-1], w[:-2], w[:-1] + 'я', w[:-2] + 'я', w[:-1] + 'а', w[:-2] + 'а', w[:-1] + 'ь', w[:-2] + 'ь'):
        if len(v) >= 2 and v in ALIAS:
            return ALIAS[v]
    return None


def find_teams(text: str, teams: list[str]) -> dict[str, int]:
    """Позиции (номер слова) упоминаний команд ESPN в русском тексте: псевдонимы, затем транслит (сходство ≥ 0,84)."""
    ws = _words(text)
    out: dict[str, int] = {}
    keys = {t: _fold(t) for t in teams}
    for n in (3, 2, 1):                                           # сначала длинные псевдонимы («реал сосьедад» раньше «реал»)
        for i in range(len(ws) - n + 1):
            al = _alias(' '.join(ws[i:i + n]))
            if not al:
                continue
            for t, k in keys.items():
                if t not in out and al in k and not (al == 'real madrid' and 'madrid' not in k):
                    out[t] = i
    tw = [(i, _tr(w)) for i, w in enumerate(ws) if len(w) >= 4]
    for t, k in keys.items():
        if t in out:
            continue
        toks = [x for x in k.split() if len(x) >= 4 and x not in STOP]
        for i, w in tw:
            if any(_like(w, x) for x in toks):
                out[t] = i
                break
    return out


# ------------------------------------------------------------------ ставка: русская запись → ноги (рынок, сторона, линия)
NUM = r'(\d+(?:[.,]\d+)?)'
SKIP = re.compile(r'углов|карточ|\bжк\b|фол|тайм|период|офсайд|удар|сет\b|гейм|лайв|live|минут|точн\w* сч', re.I)


def _num(x: str) -> float:
    return float(x.replace(',', '.'))


def parse_legs(sel: str, home_first: bool | None = None, teams: tuple[str, str] | None = None,
               natural: bool = True) -> list[tuple[str, str, object]]:
    """Ноги ставки из фрагмента текста. home_first: команда, названная перед ставкой (True — хозяева, False — гости) —
    для «ИТБ(1)», «Ф(−1)», «победа». Пусто — не распознано или рынок не наш."""
    s = sel.lower().replace('х', 'x').replace('–', '-').replace('—', '-').replace('−', '-')
    if SKIP.search(s):
        return []
    legs: list[tuple[str, str, object]] = []
    side = None if home_first is None else ('1' if home_first else '2')
    for part in re.split(r'\s\+\s|\+(?![\s(]*\d)|\sи\s', s):
        p = part.strip()
        if not p:
            continue
        side = None if home_first is None else ('1' if home_first else '2')
        if teams:                                                  # команда внутри ноги («победа Сент-Этьена», «Велес ИТБ(1)»)
            pos = find_teams(part, list(teams))
            if len(pos) == 1:
                side = '1' if teams[0] in pos else '2'
        m = re.search(r'индивидуальн\w*\s+тотал\w*\s*([12])-?\w*\s*\(?\s*' + NUM + r'\s*\)?\s*(больше|меньше|б|м)(?![а-я])', p)
        if m:
            legs.append((f'IT{m.group(1)}', 'O' if m.group(3)[0] == 'б' else 'U', _num(m.group(2))))
            continue
        m = re.search(r'инд\w*\.?\s*тотал\w*\s*(больше|меньше)\s*\(?\s*' + NUM, p)
        if m and side:
            legs.append((f'IT{side}', 'O' if m.group(1)[0] == 'б' else 'U', _num(m.group(2))))
            continue
        m = re.search(r'\bи\s*т\s*([12])?\s*([бм])[а-я]*\s*([12])?\s*\(?\s*' + NUM, p)
        if m:
            d = m.group(1) or m.group(3) or side
            if d:
                legs.append((f'IT{d}', 'O' if m.group(2) == 'б' else 'U', _num(m.group(4))))
            continue
        m = re.search(r'\bт\s*([бм])\s*\(?\s*' + NUM, p) or \
            re.search(r'тотал[а-я ]*\(?\s*' + NUM + r'\s*\)?\s*(больше|меньше|б|м)(?![а-я])', p) or re.search(r'тотал\w*\s*\(?\s*' + NUM + r'\s*\)?\s*(больше|меньше)', p) \
            or re.search(r'тотал\w*\s*(больше|меньше)\s*\(?\s*' + NUM, p)
        if m:
            g = m.groups()
            bm = next(x for x in g if not re.match(r'\d', x))
            L = next(x for x in g if re.match(r'\d', x))
            legs.append(('TOT', 'O' if bm[0] == 'б' else 'U', _num(L)))
            continue
        m = re.search(r'(нулев\w*\s+)?фор\w*\s+(хозяев|гост\w*)\s*\(?\s*([+-]?\s*\d+(?:[.,]\d+)?)?', p)
        if m and (m.group(1) or m.group(3)):
            legs.append(('AH', 'H' if m.group(2).startswith('хоз') else 'A', 0.0 if m.group(1) else _num(m.group(3).replace(' ', ''))))
            continue
        m = re.search(r'\bф(?:ора)?\s*([12])?\s*\(?\s*([+-]?\s*' + NUM[1:-1] + r')\s*\)?', p)
        if m and (m.group(1) or side):
            legs.append(('AH', 'H' if (m.group(1) or side) == '1' else 'A', _num(m.group(2).replace(' ', ''))))
            continue
        m = re.search(r'(?<![\d.,])(1x|x2|2x|12)(?![\d.,])', p)
        if m:
            legs.append(('DC', {'2x': 'X2'}.get(m.group(1), m.group(1).upper()), ''))
            continue
        m = re.search(r'\bп\s*([12])\b', p)
        if m:
            legs.append(('1X2', 'H' if m.group(1) == '1' else 'A', ''))
            continue
        if not natural:                                           # строка без пометки «Ставка:» — только краткая запись
            continue
        if re.search(r'\bничь|^x$', p):
            legs.append(('1X2', 'D', ''))
            continue
        if re.search(r'обе\s*забьют|\bоз\b', p):
            legs.append(('BTTS', 'N' if re.search(r'нет', p) else 'Y', ''))
            continue
        if side and re.search(r'побед|выигра|\bп\b', p):
            legs.append(('1X2', 'H' if side == '1' else 'A', ''))
            continue
    return legs


def odds_in(sel: str) -> float | None:
    m = re.search(r'(?:кф|коэф\w*|@)\s*\.?\s*:?\s*(\d[.,]\d{1,3})', sel.lower()) or re.search(r'\((\d[.,]\d{2,3})\)', sel)
    k = _num(m.group(1)) if m else None
    return k if k and 1.01 < k < 30 else None


def leg_label(leg) -> str:
    import daypick
    return daypick.label(leg[0], leg[1], leg[2])


def masks(P: np.ndarray, leg) -> tuple[np.ndarray, np.ndarray]:
    """(выигрыш, возврат) ноги на сетке счёта."""
    i, j = np.indices(P.shape)
    mk, s, L = leg
    z = np.zeros(P.shape, bool)
    if mk == '1X2':
        return {'H': i > j, 'D': i == j, 'A': i < j}[s], z
    if mk == 'DC':
        return {'1X': i >= j, 'X2': i <= j, '12': i != j}[s], z
    if mk == 'BTTS':
        y = (i >= 1) & (j >= 1)
        return (y if s == 'Y' else ~y), z
    if mk == 'AH':
        v = (i - j + float(L)) if s == 'H' else (j - i + float(L))
        return v > 0, v == 0
    g = {'TOT': i + j, 'IT1': i, 'IT2': j}[mk]
    return ((g > float(L)) if s == 'O' else (g < float(L))), g == float(L)


def p_legs(P: np.ndarray, legs) -> float:
    """P выигрыша связки без возвратов: W / (W + L) по матрице счёта (как в расчёте: возврат в точность не входит)."""
    w = np.ones(P.shape, bool)
    lose = np.zeros(P.shape, bool)
    for lg in legs:
        mw, mp = masks(P, lg)
        lose |= ~(mw | mp)
        w &= mw
    pw, pl = float(P[w & ~lose].sum()), float(P[lose].sum())
    return pw / (pw + pl) if pw + pl > 0 else 0.0


def settle_legs(legs, kind: str, hg, ag) -> str:
    rs = [FL.settle(mk, s, L, kind, hg, ag) for mk, s, L in legs]
    return 'L' if 'L' in rs else 'P' if 'P' in rs else 'W'


def _enc(legs) -> str:
    return '|'.join(f'{mk}:{s}:{L}' for mk, s, L in legs)


def _dec(x: str):
    out = []
    for p in str(x).split('|'):
        mk, s, L = p.split(':')
        out.append((mk, s, float(L) if L not in ('', 'nan', 'None') else ''))
    return out


# ------------------------------------------------------------------ хранилище
def load() -> pd.DataFrame:
    if TIPS.exists():
        df = pd.read_csv(TIPS, dtype=str, keep_default_na=False)
        return df.reindex(columns=COLS, fill_value='')
    return pd.DataFrame(columns=COLS)


def save(df: pd.DataFrame) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(TIPS, index=False)


def _segments(text: str, fixtures: list[tuple]) -> list[tuple[tuple, str, bool | None]]:
    """Куски текста по матчам: (матч, фрагмент, команда перед ставкой — хозяева?)."""
    hits = []
    for fx in fixtures:
        m, row = fx
        pos = find_teams(text, [row['home'], row['away']])
        if len(pos) == 2:
            hits.append((min(pos.values()), fx, pos))
    if not hits:
        return []
    ws = re.findall(r'\S+', text)
    words = _words(text)
    # переводим номер слова (_words) в позицию символа: по совпадающей последовательности слов
    starts, k = [], 0
    low = text.lower()
    for w in words:
        k = low.find(w, k)
        starts.append(k)
        k += len(w)
    hits.sort(key=lambda h: h[0])
    out = []
    for n, (w0, fx, pos) in enumerate(hits):
        a = starts[w0]
        b = starts[hits[n + 1][0]] if n + 1 < len(hits) else len(text)
        frag = text[a:b]
        out.append((fx, frag, pos))
    return out


def _side_before(frag: str, row: dict) -> bool | None:
    """Какая команда названа последней перед словом «ставка/ИТ/Ф/победа» во фрагменте."""
    m = re.search(r'ставк\w*|прогноз\w*|\bит|\bф\s*\(|побед|\bтб|\bтм', frag.lower())
    head = frag[:m.start()] if m else frag
    lines = [x for x in re.split(r'[\n:]', head) if x.strip()]
    tail = lines[-1] if lines else head
    pos = find_teams(tail, [row['home'], row['away']])
    if len(pos) == 1:
        return row['home'] in pos
    return None


def _pick_lines(frag: str) -> list[tuple[str, bool]]:
    """Кандидаты строки со ставкой: сначала после «Ставка:/Прогноз на матч:/Берём:», затем любые строки фрагмента."""
    out = []
    for ln in frag.split('\n'):
        m = re.search(r'(?:ставк\w*|прогноз\w*(?: на матч)?|бер[её]м|играем)\s*[:\-—]\s*(.{2,120})', ln, re.I)
        if m:
            out.append((m.group(1), True))
    return out + [(ln, False) for ln in frag.split('\n') if ln.strip()]


def _row(src, author, ts, fx, legs, k, raw, uid) -> dict | None:
    import daypick
    m, row = fx
    P = daypick.DISTS.get(str(m.id))
    if P is None or not legs:
        return None
    p = p_legs(P, legs)
    agree = (p * k >= 1) if k else (p >= 0.5)
    return dict(uid=uid, src=src, author=author, post_ts=str(ts)[:16], seen_ts=pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M'),
                espn_id=str(m.id), comp_name=row['comp_name'], start_utc=row['start_utc'], home=row['home'], away=row['away'],
                legs=_enc(legs), label=' + '.join(leg_label(lg) for lg in legs), k=f'{k:.2f}' if k else '',
                p_model=f'{p:.4f}', agree='1' if agree else '0', raw=raw[:160].replace('\n', ' '), status='', result='',
                settled='')


def collect(items: list[tuple], log: list[str]) -> int:
    """Новые посты всех источников → прогнозы на наши матчи (окно прогноза: матчи items, не начавшиеся)."""
    now = pd.Timestamp.now(tz='UTC').tz_localize(None)
    fixtures = [(m, row) for m, row, _ in items if m.start > now]
    LAST_POSTS.clear()
    LAST_FEED.clear()
    df = load()
    have = set(df.uid)
    seen = json.loads(SEEN.read_text()) if SEEN.exists() else {}
    new, stat = [], []
    for ch, name in TG.items():
        try:
            posts = tg_posts(ch)
        except Exception as e:
            stat.append(f'{name}: ✖ {e.__class__.__name__}')
            continue
        last = int(seen.get(ch, 0))
        fresh = [p for p in posts if p['id'] > last]
        got = 0
        for p in fresh:
            if p['ts'] < now - pd.Timedelta(hours=36):
                continue
            LAST_POSTS.append((ch, name, p))
            for fx, frag, pos in _segments(p['text'], fixtures):
                side, tm = _side_before(frag, fx[1]), (fx[1]['home'], fx[1]['away'])
                sel, legs = '', []
                for cand, nat in _pick_lines(frag):
                    legs = parse_legs(cand, side, tm, nat)
                    if legs:
                        sel = cand
                        break
                uid = f"tg:{ch}:{p['id']}:{fx[0].id}"
                r = _row(ch, name, p['ts'], fx, legs, odds_in(sel), sel or frag, uid) if uid not in have else None
                if r:
                    new.append(r); have.add(uid); got += 1
        if posts:
            seen[ch] = max(last, max(p['id'] for p in posts))
        stat.append(f'{name}: постов {len(fresh)}, наших {got}')
    try:
        feed = nb_feed()
        LAST_FEED[:] = feed
        got = 0
        for t in feed:
            uid = f"nb:{t['id']}"
            if uid in have:
                continue
            for fx in fixtures:
                pos = find_teams(f"{t['home']} — {t['away']}", [fx[1]['home'], fx[1]['away']])
                if len(pos) == 2:
                    r = _row(NB, t['author'], now, fx, parse_legs(t['sel'], None, (fx[1]['home'], fx[1]['away'])), t['k'], t['sel'], uid)
                    if r:
                        new.append(r); have.add(uid); got += 1
                    break
        stat.append(f'nb-bet: в ленте {len(feed)}, наших {got}')
    except Exception as e:
        stat.append(f'nb-bet: ✖ {e.__class__.__name__}')
    DIR.mkdir(parents=True, exist_ok=True)
    SEEN.write_text(json.dumps(seen))
    if new:
        df = pd.concat([df, pd.DataFrame(new)], ignore_index=True)
    save(df)
    log.append('👥 каперы: ' + '; '.join(stat) + f'; новых прогнозов {len(new)}')
    return len(new)


def settle(res: dict) -> int:
    """Итоги по результатам ESPN (те же, что у наших линий)."""
    df = load()
    if df.empty:
        return 0
    n, today = 0, pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d')
    for i, r in df[df.settled == ''].iterrows():
        x = res.get(str(r.espn_id))
        if not x or x[0] not in ('FT', 'ET', 'VOID', 'OFF'):
            continue
        st, hg, ag = x
        ft = st == 'FT'
        v = settle_legs(_dec(r.legs), st, int(hg) if ft else None, int(ag) if ft else None)
        df.loc[i, ['status', 'result', 'settled']] = [st, v, today]
        n += 1
    save(df)
    return n


# ------------------------------------------------------------------ учёт точности и влияние
def stats(df: pd.DataFrame | None = None) -> pd.DataFrame:
    """По источнику: n, угадано, Σp модели, z, ROI по кф; спор (модель против): n, права модель."""
    df = load() if df is None else df
    d = df[df.result.isin(['W', 'L'])].copy()
    if d.empty:
        return pd.DataFrame(columns=['src', 'name', 'n', 'won', 'exp', 'z', 'roi', 'nk', 'dis_n', 'dis_sys'])
    d['w'] = (d.result == 'W').astype(int)
    d['p'] = d.p_model.astype(float)
    d['kk'] = pd.to_numeric(d.k, errors='coerce')
    rows = []
    for src, g in d.groupby('src'):
        var = float((g.p * (1 - g.p)).sum())
        z = (g.w.sum() - g.p.sum()) / math.sqrt(var) if var > 0 else 0.0
        gk = g[g.kk.notna()]
        roi = float((gk.w * gk.kk - 1).mean()) if len(gk) else float('nan')
        dis = g[g.agree == '0']
        rows.append(dict(src=src, name=TG.get(src, src), n=len(g), won=int(g.w.sum()), exp=float(g.p.mean()), z=round(z, 2),
                         roi=roi, nk=len(gk), dis_n=len(dis), dis_sys=int((dis.w == 0).sum())))
    return pd.DataFrame(rows).sort_values('n', ascending=False)


def trusted(st: pd.DataFrame | None = None) -> set[str]:
    """Надёжные: на наших матчах выше модели (n ≥ 30, z ≥ 1,64) ИЛИ на всех матчах обыгрывают рынок (tipswide, ≥ 50 с кф)."""
    st = stats() if st is None else st
    out = set(st[(st.n >= TRUST_N) & (st.z >= TRUST_Z)].src) if len(st) else set()
    try:
        import tipswide
        out |= tipswide.trusted()
    except Exception:
        pass
    return out


def for_match(espn_id: str) -> pd.DataFrame:
    df = load()
    return df[(df.espn_id == str(espn_id)) & (df.settled == '')]


def match_line(espn_id: str, trust: set[str] | None = None) -> str:
    """«👥 ТБ2.5 ×2 · 58% | П2 · 41%» — ставки каперов на матч и вероятность модели; ✔️ — надёжный источник."""
    d = for_match(espn_id)
    if d.empty:
        return ''
    trust = trusted() if trust is None else trust
    parts = []
    for lab, g in d.groupby('label', sort=False):
        mark = '✔️' if set(g.src) & trust else ''
        parts.append(f"{mark}{lab}{' ×' + str(len(g)) if len(g) > 1 else ''} · {float(g.p_model.iloc[0]) * 100:.0f}%")
    return '👥 ' + ' | '.join(parts[:4])


def conflicts(espn_id: str, P: np.ndarray, market: str, pick: str, line) -> tuple[int, int]:
    """(против надёжных, за любых): несовместимость — совместная P < 5%; поддержка — P(наша | их) ≥ P(наша) + 0,10."""
    d = for_match(espn_id)
    if d.empty or P is None:
        return 0, 0
    trust = trusted()
    ours = masks(P, (market, pick, '' if line in (None, '') else float(line)))[0]
    p0 = float(P[ours].sum())
    bad = good = 0
    for r in d.itertuples(index=False):
        m = np.ones(P.shape, bool)
        for lg in _dec(r.legs):
            m &= masks(P, lg)[0]
        pt = float(P[m].sum())
        if pt <= 0:
            continue
        joint = float(P[m & ours].sum())
        if r.src in trust and joint < 0.05:
            bad += 1
        if joint / pt >= p0 + 0.10 or joint / pt >= 0.95:
            good += 1
    return bad, good


def report_block() -> str:
    """Таблица «👥 Каперы» для суточного отчёта и аудита (стандарт rtable): источник (ожидание модели) | угадано/n | %."""
    import rtable
    st = stats()
    if st.empty:
        return '👥 <b>Каперы</b>: рассчитанных прогнозов на наши матчи пока нет'
    rows = []
    for r in st.itertuples(index=False):
        mark = '✔️' if (r.n >= TRUST_N and r.z >= TRUST_Z) else ('🟢' if r.z >= TRUST_Z else '🔴' if r.z <= -TRUST_Z else '🟡')
        rows.append(rtable.row(r.name[:10], r.won, r.n, rtable.pct(r.exp, 0), mark))
    dn, ds = int(st.dis_n.sum()), int(st.dis_sys.sum())
    if dn:
        rows.append(rtable.row('Спор: модель', ds, dn))
    k = st[st.nk > 0]
    head = rtable.render(rows, ('Источник (мод.)', 'Кол-во', '%'))
    roi = ' · '.join(f"{r.name[:10]} {'+' if r.roi > 0 else ''}{rtable.pct(r.roi, 0)}%" for r in k.itertuples(index=False)
                     if r.roi == r.roi)
    return ('👥 <b>Каперы</b>\n' + head + '\n<i>(мод.) ожидание модели · ✔️ надёжный · 🟢🟡🔴 выше/как/ниже</i>'
            + (f'\n<i>ROI по их кф: {roi}</i>' if roi else ''))
