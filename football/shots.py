"""Пакет №3 · скриншоты 1xBet (футбол): присланный боту скриншот любого матча → точные кф 1xBet по его линиям.

Матч: местное время начала из шапки («08.11.2026 (22:45)») + слова лиги из шапки («Чемпионат России» → РПЛ) +
фамилии/команды из сообщения, на которое ответили скриншотом. Порядок 1xBet — хозяева, гости (как у нас).
Линии: тотал, фора (азиатские ±0,25/0,75 не берутся), ИТ1, ИТ2. P — Pinnacle без маржи (та же линия в котировках дня)
или среднее на логит-шкале модели и 1xBet без маржи; сверки 1xBet/Pinnacle уточняют r (data/odds/r.json).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

import config as C
import daypick as DP
import odds as OD
import pick as PK

LEAGUE_WORDS = {'ned.1': ('нидерланд',), 'eng.1': ('англи',), 'eng.fa': ('англи', 'кубок'), 'esp.1': ('испани',),
                'esp.copa_del_rey': ('испани', 'кубок'), 'fra.1': ('франци',), 'fra.coupe_de_france': ('франци', 'кубок'),
                'ger.1': ('германи',), 'ger.dfb_pokal': ('германи', 'кубок'), 'ita.1': ('итали',), 'ita.coppa_italia': ('итали', 'кубок'),
                'por.1': ('португали',), 'por.taca.portugal': ('португали', 'кубок'), 'tur.1': ('турци',),
                'uefa.champions': ('чемпионов',), 'uefa.europa': ('европы',), 'uefa.europa.conf': ('конференц',)}
MK = {'TOTAL': 'TOT', 'HCP': 'AH', 'IT1': 'IT1', 'IT2': 'IT2'}


def ensure_ocr() -> str:
    if shutil.which(C.TESSERACT_CMD) or shutil.which('tesseract'):
        return ''
    if not os.getenv('CLOUD'):
        return 'Tesseract не установлен'
    r = subprocess.run('sudo apt-get -qq update && sudo apt-get -qq install -y tesseract-ocr tesseract-ocr-rus',
                       shell=True, capture_output=True)
    return '' if r.returncode == 0 and shutil.which('tesseract') else 'не удалось установить Tesseract'


def is_image(raw: bytes) -> bool:
    return raw[:8] == b'\x89PNG\r\n\x1a\n' or raw[:3] == b'\xff\xd8\xff' or raw[:4] == b'RIFF'


def _parse(raw: bytes):
    import ocr
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / 'shot.png'
        f.write_bytes(raw)
        return ocr.parse_screenshot(f)


def _identify(p, items: list[tuple], reply: str) -> tuple[list[tuple], str]:
    if not (p.date and p.time):
        return [], 'на скриншоте не видна дата и время начала матча (шапка страницы матча)'
    when = f'{p.date[:5]} {p.time}'
    c = [(m, row, lns) for m, row, lns in items if str(row['start_local']) == when]
    head = p.header.lower()
    if len(c) > 1:
        c2 = [x for x in c if all(w in head for w in LEAGUE_WORDS.get(x[0].comp, ('\x00',)))]
        c = c2 or c
    if len(c) > 1 and reply:
        rr = OD._norm(reply)
        c2 = [x for x in c if OD.same_name(rr, x[1]['home']) > 0 or OD.same_name(rr, x[1]['away']) > 0]
        c = c2 or c
    if not c:
        return [], f'матч {when} не найден в прогнозе на 24 ч'
    if len(c) > 1:
        names = '; '.join(f"{x[1]['home']} — {x[1]['away']}" for x in c[:6])
        return [], (f'в {when} несколько матчей ({names}) — пришлите скриншот ответом на нужный матч в списке '
                    '(свайп влево по сообщению списка)')
    return c, ''


def _pinnacle(q: dict, mk: str, s: str, line, home_api: str) -> tuple[float, float] | None:
    pairs = {}
    for (m, n, pt), books in q.items():
        if 'pinnacle' not in books or pt is None:
            continue
        if mk == 'TOT' and m == 'totals' and float(pt) == float(line):
            pairs['O' if n == 'Over' else 'U'] = books['pinnacle']
        elif mk == 'AH' and m == 'spreads':
            sd = 'H' if n == home_api else 'A'
            if (sd == s and float(pt) == float(line)) or (sd != s and float(pt) == -float(line)):
                pairs[sd] = books['pinnacle']
    keys = ('O', 'U') if mk == 'TOT' else ('H', 'A')
    if not all(k in pairs for k in keys):
        return None
    fair = PK.shin([pairs[k] for k in keys])
    return fair[keys.index(s)], pairs[s]


def _offers_for(m, row, p, events: list[dict]) -> tuple[list[dict], list[float]]:
    P = DP.DISTS.get(str(m.id))
    lines = [(mk, line, ks) for (tab, mk, line), ks in p.lines.items() if tab == 'main' and mk in MK and isinstance(ks, tuple)]
    if P is None or not lines:
        return [], []
    ev, flip = OD.find_event(events, row['home'], row['away'], row['start_utc']) if events else (None, False)
    q = OD.quotes(ev) if ev else {}
    home_api = (ev['away_team'] if flip else ev['home_team']) if ev else ''
    out, rs = [], []
    for mk0, line, (k1, k2) in lines:
        mk = MK[mk0]
        if abs(float(line) * 2 - round(float(line) * 2)) > 1e-9:
            continue
        fb = PK.shin([k1, k2])
        sides = (('O', line, k1, fb[0]), ('U', line, k2, fb[1])) if mk != 'AH' else (('H', line, k1, fb[0]), ('A', -line, k2, fb[1]))
        for s, ln, k, pb in sides:
            pm, pp = DP.model_price(P, mk, s, ln)
            p_f, src = PK.blend(pm, pb), 'blend'
            pin = _pinnacle(q, mk, s, ln, home_api) if q else None
            if pin:
                p_f, src = PK.fair(pm, pin[0], 'pinnacle'), 'pinnacle'
                rs.append(k / pin[1])
            out.append(dict(DP._base(m, row), market=mk, pick=s, line=ln, label=DP.label(mk, s, ln), p=float(p_f), p_src=src,
                            p_model=pm, p_push=pp, k=float(k), k_src='screenshot', source='screenshot', sigma=C.SIGMA.get(mk)))
    return out, rs


def learn_r(samples: list[float]) -> None:
    if not samples:
        return
    f = OD.DIR / 'r.json'
    try:
        d = json.loads(f.read_text(encoding='utf-8'))
    except Exception:
        d = {'samples': []}
    xs = (d.get('samples', []) + [round(x, 4) for x in samples if 0.85 < x < 1.15])[-300:]
    OD.DIR.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(dict(r=round(float(np.median(xs)), 4), n=len(xs), samples=xs)), encoding='utf-8')


def process(shots: list[tuple], items: list[tuple], events: list[dict]) -> tuple[list[dict], list[str], list[str]]:
    if not shots:
        return [], [], []
    err = ensure_ocr()
    if err:
        return [], [f'📸 Скриншот не обработан: {err}.'], [f'скриншоты: {err}']
    offers, replies, log, rs = [], [], [], []
    for fid, name, raw, desc in shots:
        try:
            meta = json.loads(desc) if desc else {}
        except Exception:
            meta = {}
        try:
            p = _parse(raw)
        except Exception as e:
            replies.append(f'📸 Скриншот не распознан ({e.__class__.__name__}). Нужен скриншот страницы матча 1xBet с шапкой.')
            continue
        c, why = _identify(p, items, str(meta.get('reply', '')) + ' ' + str(meta.get('caption', '')))
        if not c:
            replies.append(f'📸 Скриншот: {why}.')
            log.append(f'скриншот: {why}')
            continue
        m, row, _ = c[0]
        o, r = _offers_for(m, row, p, events)
        rs += r
        if not o:
            replies.append(f"📸 {row['home']} — {row['away']}: линий не распознано — пришлите «Основная игра» с развёрнутыми "
                           'рынками (тотал, фора, индивидуальные тоталы).')
            continue
        offers += o
        n = len({(x['market'], x['line']) for x in o})
        replies.append(f"📸 {row['home']} — {row['away']}: распознано линий — {n}. Подбираю ставку дня с учётом кф 1xBet.")
        log.append(f"скриншот: {row['home']} — {row['away']}, линий {n}")
    learn_r(rs)
    return offers, replies, log
