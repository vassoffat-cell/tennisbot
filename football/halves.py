"""⏱ Ставки каперов на таймы (решение пользователя 21.11 «давай», CONTROL К17).

Каппер UK и Green Line часто ставят на 1-й тайм («Тотал. (1.5) Б. 1-й тайм», «Индивидуальный тотал 1-го. (0.5) Б. 1-й тайм»),
«Выиграет оба тайма», «1-й тайм/Матч, Бавария / Бавария». Раньше такие ставки — «рынок вне модели» и без итога.

Запись ноги: рынок с суффиксом тайма — 'TOT@1', 'IT1@1', '1X2@2', 'AH@1' …; особые рынки 'WBH' (выиграет оба тайма, H/A) и
'HTFT' (тайм/матч, 'H/D' …). Итог — по счёту таймов из карточки матча ESPN (summary, общий слаг 'all' — проба 21.11
engine/research/half_probe.py: 5 из 5 лиг, в т. ч. РПЛ, Сегунда, Саудовская лига). Вероятность (вердикт) — из средних голов
матрицы счёта модели или рынка: голы таймов — независимые Пуассоны с долей 1-го тайма SHARE_1H (≈ 45% голов, оценка;
калибруется аудитом по накопленным счетам таймов, halves.json)."""
from __future__ import annotations

import json
import pathlib
import re

import numpy as np
from scipy.stats import poisson

SHARE_1H = 0.45                                 # доля голов 1-го тайма (оценка; калибровка — аудит по halves.json)
FILE = pathlib.Path(__file__).resolve().parent / 'data' / 'tips' / 'halves.json'
FETCH_MAX = 40                                  # запросов карточек ESPN за прогон
_fetched = 0

HALF = re.compile(r'(?:\b([12])\s*-?\s*[a-zа-яё]{0,3}\.?\s*тайм\w*|(перв|втор)\w*\s+тайм\w*|тайм\w*\s*№?\s*([12])\b)', re.I)
BOTH = re.compile(r'(выигра\w*|побед\w*)\s+(в\s+)?(оба|обоих)\s+тайм', re.I)
HTFT = re.compile(r'тайм\s*/\s*матч[\s,:.]+([^/]+?)\s*/\s*([^/\n]+)', re.I)
OTHER = re.compile(r'углов|карточ|\bжк\b|фол|удар|офсайд|минут|точн\w* сч|период|сет\b|гейм', re.I)
RES = {'H': 'П1', 'D': 'Х', 'A': 'П2'}


def is_half(mk: str) -> bool:
    return '@' in mk or mk in ('WBH', 'HTFT')


def any_half(legs) -> bool:
    return any(is_half(lg[0]) for lg in legs or [])


# ------------------------------------------------------------------ разбор
def parse(s: str, home_first, teams, natural: bool, parse_legs, find_teams) -> list | None:
    """None — ставка не на тайм (обычный разбор); [] — тайм, но не разобрано / рынок вне счёта; иначе — ноги."""
    if 'тайм' not in s:
        return None
    if OTHER.search(s):                                       # фолы / угловые в тайме — не голы
        return []

    def side_of(txt: str):
        if teams:
            pos = find_teams(txt, list(teams))
            if len(pos) == 1:
                return 'H' if teams[0] in pos else 'A'
        if re.search(r'\bп\s*1\b|хозя', txt):
            return 'H'
        if re.search(r'\bп\s*2\b|гост', txt):
            return 'A'
        return None if home_first is None else ('H' if home_first else 'A')

    if BOTH.search(s):
        sd = side_of(s)
        return [('WBH', sd, '')] if sd else []
    m = HTFT.search(s)
    if m:
        def res(x: str):
            x = x.strip(' .,')
            if re.search(r'ничь|^x$|^х$', x):
                return 'D'
            if teams:
                pos = find_teams(x, list(teams))
                if len(pos) == 1:
                    return 'H' if teams[0] in pos else 'A'
            return {'п1': 'H', '1': 'H', 'п2': 'A', '2': 'A'}.get(x.replace(' ', ''))
        a, b = res(m.group(1)), res(m.group(2))
        return [('HTFT', f'{a}/{b}', '')] if a and b else []
    m = HALF.search(s)
    if not m:
        return []
    n = m.group(1) or m.group(3) or ('1' if (m.group(2) or '').startswith('перв') else '2')
    rest = (s[:m.start()] + ' ' + s[m.end():]).replace(' в ', ' ')
    rest = re.sub(r'\s+', ' ', re.sub(r'(?<=[бм])\.(?=\s)', '', rest)).strip(' .,')
    legs = parse_legs(rest, home_first, teams, natural)
    if not legs or any_half(legs):
        return []
    return [(f'{mk}@{n}', sd, L) for mk, sd, L in legs]


def label(leg, base_label) -> str:
    mk, s, L = leg
    if mk == 'WBH':
        return f'{RES[s]} оба тайма'
    if mk == 'HTFT':
        a, b = s.split('/')
        return f'тайм/матч {RES[a]}/{RES[b]}'
    base, n = mk.split('@')
    return f'{base_label(base, s, L)} · {n}-й тайм'


def flip(leg, flip_one):
    mk, s, L = leg
    if mk == 'WBH':
        return (mk, {'H': 'A', 'A': 'H'}[s], L)
    if mk == 'HTFT':
        f = {'H': 'A', 'A': 'H', 'D': 'D'}
        a, b = s.split('/')
        return (mk, f'{f[a]}/{f[b]}', L)
    base, n = mk.split('@')
    b2, s2, L2 = flip_one((base, s, L))
    return (f'{b2}@{n}', s2, L2)


# ------------------------------------------------------------------ вероятность по матрице счёта матча
def _half_mat(lh: float, la: float, share: float, n: int = 11) -> np.ndarray:
    k = np.arange(n)
    return np.outer(poisson.pmf(k, lh * share), poisson.pmf(k, la * share))


def p_legs(P: np.ndarray, legs, p_plain) -> float:
    """P выигрыша ставки на тайм(ы) без возвратов: голы таймов — Пуассон от средних матрицы матча (доля SHARE_1H)."""
    i, j = np.indices(P.shape)
    tot = float(P.sum()) or 1.0
    lh, la = float((P * i).sum()) / tot, float((P * j).sum()) / tot
    H = {'1': _half_mat(lh, la, SHARE_1H), '2': _half_mat(lh, la, 1 - SHARE_1H)}
    if len(legs) == 1 and legs[0][0] == 'WBH':
        s = legs[0][1]
        w = [float(M[(np.indices(M.shape)[0] > np.indices(M.shape)[1]) if s == 'H'
                     else (np.indices(M.shape)[0] < np.indices(M.shape)[1])].sum()) for M in H.values()]
        return w[0] * w[1]
    if len(legs) == 1 and legs[0][0] == 'HTFT':
        a, b = legs[0][1].split('/')
        r = lambda x, y: 'H' if x > y else 'D' if x == y else 'A'   # noqa: E731
        p = 0.0
        A, B = H['1'], H['2']
        for (x1, y1), v1 in np.ndenumerate(A):
            if v1 < 1e-9 or r(x1, y1) != a:
                continue
            for (x2, y2), v2 in np.ndenumerate(B):
                if v2 > 1e-9 and r(x1 + x2, y1 + y2) == b:
                    p += v1 * v2
        return p
    ns = {lg[0].split('@')[1] for lg in legs if '@' in lg[0]}
    if len(ns) != 1 or any(lg[0] in ('WBH', 'HTFT') for lg in legs):
        return float('nan')
    n = ns.pop()
    return p_plain(H[n], [(lg[0].split('@')[0], lg[1], lg[2]) for lg in legs])


# ------------------------------------------------------------------ итог по счёту таймов
def _load() -> dict:
    try:
        return json.loads(FILE.read_text())
    except Exception:
        return {}


def score(eid) -> tuple[int, int, int, int] | None:
    """(голы хозяев 1-й тайм, гостей 1-й, хозяев 2-й, гостей 2-й) — кэш halves.json, иначе карточка матча ESPN."""
    global _fetched
    eid = str(eid)
    c = _load()
    if eid in c:
        return tuple(c[eid])
    if _fetched >= FETCH_MAX:
        return None
    _fetched += 1
    try:
        import fespn
        js = fespn._get(fespn.BASE.format(comp='all') + f'summary?event={eid}')
        comp = ((js.get('header') or {}).get('competitions') or [{}])[0]
        if not ((comp.get('status') or {}).get('type') or {}).get('completed'):
            return None
        sc = {}
        for x in comp.get('competitors') or []:
            ls = x.get('linescores') or []
            if len(ls) < 2:
                return None
            sc[x.get('homeAway')] = [int(float(ls[k].get('displayValue', ls[k].get('value')))) for k in (0, 1)]
        v = (sc['home'][0], sc['away'][0], sc['home'][1], sc['away'][1])
    except Exception as e:
        print(f'⏱ таймы {eid}: карточка ESPN — сбой {e.__class__.__name__}')
        return None
    c[eid] = list(v)
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(c))
    return v


def settle(legs, kind: str, eid, settle_one) -> str:
    """'W'/'L'/'P'; '' — счёта таймов пока нет (повтор следующим прогоном)."""
    if kind not in ('FT', 'ET'):
        return 'P'
    hs = score(eid) if eid not in (None, '') else None
    if hs is None:
        return ''
    h1, a1, h2, a2 = hs
    rs = []
    for mk, s, L in legs:
        if mk == 'WBH':
            w = (h1 > a1 and h2 > a2) if s == 'H' else (a1 > h1 and a2 > h2)
            rs.append('W' if w else 'L')
        elif mk == 'HTFT':
            r = lambda x, y: 'H' if x > y else 'D' if x == y else 'A'   # noqa: E731
            a, b = s.split('/')
            rs.append('W' if (r(h1, a1) == a and r(h1 + h2, a1 + a2) == b) else 'L')
        elif '@' in mk:
            base, n = mk.split('@')
            x, y = (h1, a1) if n == '1' else (h2, a2)
            rs.append(settle_one(base, s, L, 'FT', x, y))
        else:
            rs.append(settle_one(mk, s, L, 'FT', h1 + h2, a1 + a2))
    return 'L' if 'L' in rs else 'P' if 'P' in rs else 'W'
