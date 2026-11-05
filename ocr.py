"""Распознавание скриншотов 1xBet (мобильное приложение, теннис) бесплатным Tesseract.

Скриншот делится по якорю — панели вкладок («Основная игра», «1-й сет», «2-й сет», «Эйсы»):
  * выше — шапка: «Теннис. WTA. Гонконг», «1/8 финала. Гонконг Хард», имена игроков, дата;
  * сама панель — активная вкладка определяется по яркости надписи (активная — белая);
  * ниже — рынки: заголовок рынка, строка подписей линий, строка коэффициентов.
Светло-серые подписи линий видны только после предобработки: увеличение ×2 и порог яркости.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

import config as C

NUM = r'-?\d+(?:[.,]\d+)?'
TABS = {'основная': 'main', '1-й': 'set1', '2-й': 'set2', 'эйсы': 'aces'}
MARKET_TITLES = [  # (регулярное выражение по нормализованной строке, код рынка)
    (r'^1x2', 'ML'), (r'^тотал(?!\s*(чет|сет|матч|самого))', 'TOTAL'), (r'^фора(?!\s*по)', 'HCP'),
    (r'^индивидуальныйтотал1', 'IT1'), (r'^индивидуальныйтотал2', 'IT2'), (r'^точныйсчет', 'EXACT'),
]


@dataclass
class Parsed:
    tour: str | None = None
    tournament: str | None = None
    round: str | None = None
    surface: str | None = None
    players: tuple[str, str] | None = None
    date: str | None = None
    lines: dict = field(default_factory=dict)       # (вкладка, рынок, линия) → (k1, k2) или для EXACT: счёт → k


def _tesseract(img: Image.Image, psm: int = 6, tsv: bool = False) -> str:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / 'i.png'
        img.save(p)
        args = [C.TESSERACT_CMD, str(p), '-', '-l', 'rus+eng', '--psm', str(psm)] + (['tsv'] if tsv else [])
        # Tesseract пишет UTF-8; без явной кодировки Windows читает вывод как cp1251 и падает на русских буквах
        r = subprocess.run(args, capture_output=True, check=True)
        return r.stdout.decode('utf-8', errors='replace')


def _norm(s: str) -> str:
    """Нижний регистр, латинские двойники → кириллица, без пробелов и знаков."""
    s = s.lower().strip()
    s = re.sub(r'^1\s*[xх]\s*2', '1x2', s)                      # «1X2», «1Х2», «1 X 2»
    if s.startswith('1x2'):
        return '1x2'
    s = s.replace('totan', 'тотал').replace('totaл', 'тотал')   # частая ошибка OCR: «Тотал» → «Totan»
    tr = str.maketrans({'a': 'а', 'b': 'в', 'c': 'с', 'e': 'е', 'k': 'к', 'm': 'м', 'o': 'о', 'p': 'р', 't': 'т',
                        'x': 'х', 'y': 'у', 'n': 'п'})
    s = s.translate(tr).replace('тотап', 'тотал')
    return re.sub(r'[^0-9a-zа-яё]', '', s)


def _num(s: str) -> float:
    return float(s.replace(',', '.'))


def find_tab_bar(gray: Image.Image) -> tuple[int, str | None]:
    """y-координата панели вкладок и активная вкладка (по яркости надписи)."""
    tsv = _tesseract(gray, psm=11, tsv=True)
    best_y, cand = None, {}
    for row in tsv.splitlines()[1:]:
        f = row.split('\t')
        if len(f) < 12 or not f[11].strip():
            continue
        word = f[11].strip().lower()
        x, y, w, h = map(int, f[6:10])
        for key, code in TABS.items():
            if word.startswith(key):
                box = np.asarray(gray.crop((x, y, x + w, y + h)), dtype=float)
                bright = float(np.percentile(box, 95))
                cand[code] = bright
                if code == 'main' or best_y is None:
                    best_y = y + h
    active = max(cand, key=cand.get) if cand else None
    return (best_y if best_y is not None else 0), active


def parse_header(text: str, p: Parsed) -> None:
    for line in text.splitlines():
        s = line.strip()
        m = re.search(r'Теннис\.\s*([^.]+)\.\s*([^№=]+)', s)
        if m:
            tok = re.sub(r'[^A-Za-zА-Яа-я]', '', m.group(1)).upper()
            p.tour = 'ATP' if ('ТР' in tok or 'TP' in tok) and tok.startswith(('А', 'A')) else ('WTA' if 'ТА' in tok or 'TA' in tok else None)
            p.tournament = m.group(2).strip(' .')
        m = re.search(r'(финал\w*|1/\d+ финала|полуфинал|четвертьфинал|\d-й круг|квалификация)\.\s*(.+?)\s+(Хард|Грунт|Трава|Хард \(зал\)|Ковер)', s, re.I)
        if m:
            p.round = m.group(1); p.surface = {'хард': 'Hard', 'грунт': 'Clay', 'трава': 'Grass', 'ковер': 'Hard'}[m.group(3).split()[0].lower()]
        m = re.search(r'(\d{2}\.\d{2}\.\d{4})', s)
        if m:
            p.date = m.group(1)
        words = re.findall(r'[А-ЯЁ][а-яё\-]+', s)
        if len(words) >= 4 and re.search(r'\d+\s*[:\-]\s*\d+|VS', s) and not s.startswith('Теннис'):
            p.players = (' '.join(words[:2]), ' '.join(words[-2:]))


def parse_markets(text: str, tab: str, p: Parsed) -> None:
    market, pending = None, None
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        n = _norm(s)
        for pat, code in MARKET_TITLES:
            if re.match(pat, n):
                market, pending = code, None
                if code == 'ML':
                    pending = ('ML', None)
                break
        else:
            if market is None:
                continue
            m_tot = re.search(rf'\(({NUM})\)\s*[БB6]\s*\(({NUM})\)\s*[МM]', s)
            m_hcp = re.search(rf'1\s*\(({NUM})\)\s*2\s*\(({NUM})\)', s)
            m_ex = re.findall(r'\b([0-2])\s*[-–]\s*([0-2])\b', s)
            m_odds = re.fullmatch(rf'({NUM})\s+({NUM})', s)
            if market in ('TOTAL', 'IT1', 'IT2') and m_tot:
                pending = (market, _num(m_tot.group(1)))
            elif market == 'HCP' and m_hcp:
                pending = ('HCP', _num(m_hcp.group(1)))
            elif market == 'EXACT' and len(m_ex) == 2:
                pending = ('EXACT', tuple(f'{a}-{b}' for a, b in m_ex))
            elif m_odds and pending is not None:
                k1, k2 = _num(m_odds.group(1)), _num(m_odds.group(2))
                kind, line = pending
                if kind == 'EXACT':
                    p.lines[(tab, 'EXACT', line[0])] = k1
                    p.lines[(tab, 'EXACT', line[1])] = k2
                else:
                    p.lines[(tab, kind, line)] = (k1, k2)
                pending = None
            elif re.search(r'\(\d+\)\s*$', s):               # свёрнутый рынок «Название (6)»
                market, pending = None, None


def parse_screenshot(path: str | Path, p: Parsed | None = None) -> Parsed:
    """Разбор одного скриншота; результаты накапливаются в p (несколько скриншотов одного матча)."""
    p = p or Parsed()
    gray = Image.open(path).convert('L')
    if gray.height < 2400:                                         # сжатое фото из Telegram: увеличиваем до ~2800
        k = 2800 / gray.height
        gray = gray.resize((int(gray.width * k), 2800), Image.LANCZOS)
    W, H = gray.size
    tab_y, tab = find_tab_bar(gray)
    if tab_y > 0:
        parse_header(_tesseract(gray.crop((0, 0, W, tab_y - 60))), p)
    body = gray.crop((0, tab_y + 10, W, H - int(0.075 * H)))       # без нижней панели навигации
    body = body.resize((body.width * 2, body.height * 2), Image.LANCZOS).point(lambda v: 0 if v < 205 else 255)
    parse_markets(_tesseract(body), tab or 'main', p)
    return p
