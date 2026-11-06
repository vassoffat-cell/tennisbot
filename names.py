"""Поиск игроков по русскому написанию из 1xBet и перевод города турнира в обозначение tennis-data."""
from __future__ import annotations

import difflib
import re
import unicodedata

import pandas as pd

TR = {'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e', 'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y',
      'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f',
      'х': 'kh', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'shch', 'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya'}

CITIES = {  # город в шапке 1xBet → Location в tennis-data
    'гонконг': 'Hong Kong', 'токио': 'Tokyo', 'осака': 'Osaka', 'гуанчжоу': 'Guangzhou', 'нинбо': 'Ningbo', 'ухань': 'Wuhan',
    'пекин': 'Beijing', 'шанхай': 'Shanghai', 'ченду': 'Chengdu', 'чэнду': 'Chengdu', 'ханчжоу': 'Hangzhou', 'цзюцзян': 'Jiujiang',
    'сеул': 'Seoul', 'сингапур': 'Singapore', 'алматы': 'Almaty', 'вена': 'Vienna', 'базель': 'Basel', 'париж': 'Paris',
    'лион': 'Lyon', 'мец': 'Metz', 'стокгольм': 'Stockholm', 'брюссель': 'Brussels', 'турин': 'Turin', 'эр-рияд': 'Riyadh',
    'рияд': 'Riyadh', 'мельбурн': 'Melbourne', 'брисбен': 'Brisbane', 'аделаида': 'Adelaide', 'окленд': 'Auckland',
    'хобарт': 'Hobart', 'доха': 'Doha', 'дубай': 'Dubai', 'абу-даби': 'Abu Dhabi', 'индиан-уэллс': 'Indian Wells',
    'майами': 'Miami', 'чарльстон': 'Charleston', 'штутгарт': 'Stuttgart', 'мадрид': 'Madrid', 'рим': 'Rome',
    'страсбург': 'Strasbourg', 'рабат': 'Rabat', 'лондон': 'London', 'уимблдон': 'London', 'берлин': 'Berlin',
    'бад-хомбург': 'Bad Homburg', 'истборн': 'Eastbourne', 'ноттингем': 'Nottingham', 'хертогенбос': "'s-Hertogenbosch",
    'галле': 'Halle', 'мальорка': 'Mallorca', 'гамбург': 'Hamburg', 'бостад': 'Bastad', 'гштаад': 'Gstaad',
    'кицбюэль': 'Kitzbuhel', 'умаг': 'Umag', 'вашингтон': 'Washington', 'монреаль': 'Montreal', 'торонто': 'Toronto',
    'цинциннати': 'Cincinnati', 'кливленд': 'Cleveland', 'монтеррей': 'Monterrey', 'нью-йорк': 'New York',
    'винстон-сейлем': 'Winston-Salem', 'гвадалахара': 'Guadalajara', 'сан-паулу': 'Sao Paulo', 'остравa': 'Ostrava',
    'острава': 'Ostrava', 'прага': 'Prague', 'линц': 'Linz', 'клуж-напока': 'Cluj-Napoca', 'бухарест': 'Bucharest',
    'яссы': 'Iasi', 'роттердам': 'Rotterdam', 'марсель': 'Marseille', 'монпелье': 'Montpellier', 'буэнос-айрес': 'Buenos Aires',
    'рио-де-жанейро': 'Rio de Janeiro', 'сантьяго': 'Santiago', 'акапулько': 'Acapulco', 'мерида': 'Merida',
    'остин': 'Austin', 'хьюстон': 'Houston', 'даллас': 'Dallas', 'делрей-бич': 'Delray Beach', 'мемфис': 'Memphis',
    'марракеш': 'Marrakech', 'эшторил': 'Estoril', 'барселона': 'Barcelona', 'мюнхен': 'Munich', 'монте-карло': 'Monte Carlo',
    'женева': 'Geneva', 'лос-кабос': 'Los Cabos', 'богота': 'Bogota', 'руан': 'Rouen', 'афины': 'Athens', 'ченнаи': 'Chennai',
    'ролан гаррос': 'Paris', 'ролан-гаррос': 'Paris', 'открытый чемпионат австралии': 'Melbourne', 'открытый чемпионат сша': 'New York',
}
W_LAST = 0.65   # вес фамилии в сходстве; имя 0.35 — отсекает однофамильцев (Xu Yifan ≠ Mingge Xu)
SLAM_WORDS = ('мельбурн', 'австрал', 'ролан', 'уимблдон', 'нью-йорк', 'сша', 'us open')


RU_ALIASES = {  # написания, которые не выводятся транслитерацией (английское произношение)
    'киз': 'keys', 'гауфф': 'gauff', 'гофф': 'gauff', 'свёнтек': 'swiatek', 'свентек': 'swiatek', 'швёнтек': 'swiatek',
    'пегула': 'pegula', 'наварро': 'navarro', 'кеннин': 'kenin', 'андерсон': 'anderson', 'шелтон': 'shelton',
    'тиафо': 'tiafoe', 'пол': 'paul', 'коллинз': 'collins', 'стивенс': 'stephens', 'джабир': 'jabeur', 'жабер': 'jabeur',
    'осака': 'osaka', 'радукану': 'raducanu', 'бадоса': 'badosa', 'де минаур': 'deminaur', 'оже-альяссим': 'augeraliassime',
    'монфис': 'monfils', 'ммо': 'mmoh', 'кордa': 'korda', 'корда': 'korda', 'хуркач': 'hurkacz', 'мюллер': 'muller',
}


def translit(s: str) -> list[str]:
    """Латинские варианты русского написания: «Александра» → aleksandra, alexandra; китайские по Палладию: «Чжэн» → zheng."""
    s = s.lower().strip()
    base = ''.join(TR.get(ch, ch) for ch in s).replace('yuy', 'yu')
    out = {base, base.replace('ks', 'x'), base.replace('kh', 'h'), base.replace('iy', 'y').replace('yy', 'y'),
           base.replace('ye', 'e'), base.replace('dzh', 'j'), base.replace('zh', 'j')}
    # Палладий → пиньинь: чж→zh, цз→j, ц(и)→q, с(и)→x, финальные -эн/-ан/-ин → -eng/-ang/-ing
    py = base.replace('chzh', 'zh').replace('tsz', 'j').replace('tsi', 'qi').replace('si', 'xi').replace('syu', 'xu')
    py = py.replace('yu', 'u') if py.startswith(('j', 'q', 'x')) else py
    out |= {py, re.sub(r'(e|a|i|o)n($|[^aeiouyg])', r'\1ng\2', py), py.replace('ts', 'c')}
    if s in RU_ALIASES:
        out.add(RU_ALIASES[s])
    return [re.sub(r'[^a-z]', '', v) for v in out]


def _plain(s: str) -> str:
    return re.sub(r'[^a-z]', '', unicodedata.normalize('NFKD', str(s).lower()).encode('ascii', 'ignore').decode())


def _skeleton(s: str) -> str:
    """Согласный «скелет» фамилии: w→v, ks→x, без гласных и повторов («Соболенко» и «Sabalenka» → sblnk)."""
    s = s.replace('w', 'v').replace('ks', 'x').replace('ck', 'k').replace('ph', 'f')
    s = re.sub(r'c(?!h)', 'k', s)   # румынское/итальянское c = к: Cirstea ↔ Кырстя
    s = re.sub(r'[aeiouy]', '', s)
    return re.sub(r'(.)\1+', r'\1', s)


def _sim(variants: list[str], target: str) -> float:
    full = max(difflib.SequenceMatcher(None, v, target).ratio() for v in variants)
    sk = max(difflib.SequenceMatcher(None, _skeleton(v), _skeleton(target)).ratio() for v in variants)
    return max(full, 0.9 * sk)


def find_player(name_ru: str, players: pd.DataFrame, active_ids: set) -> list[tuple[float, str, float]]:
    """Три лучших кандидата: (player_id, имя латиницей, сходство 0–1). name_ru — «Имя Фамилия»."""
    parts = name_ru.split()
    if len(parts) >= 2:   # «Имя Фамилия», двойная фамилия «Хаддад Майя», обратный порядок «Чжу Линь» (китайские имена)
        orders = [(translit(parts[-1]), translit(' '.join(parts[:-1]))), (translit(parts[0]), translit(' '.join(parts[1:])))]
        if len(parts) >= 3:
            orders.append((translit(' '.join(parts[-2:])), translit(' '.join(parts[:-2]))))
    else:
        orders = [(translit(parts[0]), None)]
    cand = players[players.player_id.isin(active_ids)]
    res = []
    for pid, fn, ln in zip(cand.player_id, cand.name_first, cand.name_last):
        l2, f2 = _plain(ln), _plain(fn)
        score = max(W_LAST * _sim(lasts, l2) + (1 - W_LAST) * (_sim(firsts, f2) if firsts else 0.5) for lasts, firsts in orders)
        res.append((pid, f'{fn} {ln}', score))
    return sorted(res, key=lambda r: -r[2])[:3]


def city(tournament_ru: str | None) -> str:
    if not tournament_ru:
        return ''
    t = tournament_ru.lower().strip()
    if t in CITIES:
        return CITIES[t]
    best = difflib.get_close_matches(t, list(CITIES), n=1, cutoff=0.75)
    return CITIES[best[0]] if best else ''


def is_slam(tournament_ru: str | None) -> bool:
    t = (tournament_ru or '').lower()
    return any(w in t for w in SLAM_WORDS)


def find_latin(full: str, players: pd.DataFrame, active_ids: set) -> list[tuple[float, str, float]]:
    """Поиск по латинскому имени (ESPN: «Pablo Carreno Busta», «Qinwen Zheng»): перебор разбиений на имя и
    фамилию, прямой и обратный порядок. Три лучших кандидата: (player_id, имя, сходство 0–1)."""
    toks = [_plain(t) for t in re.split(r'[\s\-]+', full) if _plain(t)]
    if not toks:
        return []
    splits = []
    for k in range(1, len(toks)):
        splits.append((''.join(toks[:k]), ''.join(toks[k:])))      # имя, фамилия
        splits.append((''.join(toks[k:]), ''.join(toks[:k])))      # обратный порядок
    if len(toks) == 1:
        splits.append(('', toks[0]))
    cand = players[players.player_id.isin(active_ids)]
    res = []
    for pid, fn, ln in zip(cand.player_id, cand.name_first, cand.name_last):
        f2, l2 = _plain(fn), _plain(ln)
        best = 0.0
        for f, l in splits:
            sl = difflib.SequenceMatcher(None, l, l2).ratio()
            if sl < 0.6:
                continue
            sf = difflib.SequenceMatcher(None, f, f2).ratio() if f else 0.5
            if f and f2 and f[0] == f2[0]:
                sf = max(sf, 0.6)                                    # «Alex» ↔ «Alexander»
            best = max(best, W_LAST * sl + (1 - W_LAST) * sf)
        if best:
            res.append((pid, f'{fn} {ln}', best))
    return sorted(res, key=lambda r: -r[2])[:3]
