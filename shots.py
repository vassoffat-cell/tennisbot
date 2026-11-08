"""Пакет №3 · скриншоты 1xBet (теннис): присланный боту скриншот любого матча → точные кф 1xBet по его линиям.

Порядок:
  1) OCR (Tesseract, ocr.py; ставится в облаке при первом скриншоте) → турнир/тур, дата, время начала, линии с кф;
  2) матч: тур + местное время начала из шапки; при нескольких — по фамилиям из сообщения, на которое ответили скриншотом;
  3) порядок игроков 1xBet ↔ наш: по согласию кф с моделью (ИТ1/ИТ2, форы, победа меняются местами);
  4) предложения: кф 1xBet точно; P — Pinnacle без маржи (если та же линия есть в котировках дня), иначе
     среднее на логит-шкале модели и 1xBet без маржи (pick.blend); каждый случай «1xBet и Pinnacle на одной линии»
     уточняет отношение r (data/odds/r.json), по которому оцениваются кф 1xBet без скриншота.
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

FLIP = {'IT1': 'IT2', 'IT2': 'IT1'}


def ensure_ocr() -> str:
    """Tesseract с русским языком; в облаке ставится при первом скриншоте (~20 с). Возвращает '' или текст ошибки."""
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
    """Матчи-кандидаты по туру, дате и времени из шапки; уточнение фамилиями из сообщения, на которое ответили."""
    if not (p.date and p.time):
        return [], 'на скриншоте не видна дата и время начала матча (шапка страницы матча)'
    when = f'{p.date[:5]} {p.time}'
    c = [(m, row, lns) for m, row, lns in items if str(row['start_local']) == when and (not p.tour or row['tour'] == p.tour)]
    if len(c) > 1 and reply:
        rr = OD._norm(reply)
        c2 = [x for x in c if DP._last(x[1]['player1']).lower() in rr or DP._last(x[1]['player2']).lower() in rr]
        c = c2 or c
    if not c:
        return [], f'матч {p.tour or ""} {when} не найден в прогнозе на 24 ч'
    if len(c) > 1:
        names = '; '.join(f"{x[1]['player1']} — {x[1]['player2']}" for x in c)
        return [], (f'в {when} несколько матчей ({names}) — пришлите скриншот ответом на нужный матч в списке '
                    '(свайп влево по сообщению списка)')
    return c, ''


def _offers_for(m, row, p, events: list[dict]) -> tuple[list[dict], list[float]]:
    """Предложения по линиям скриншота (вкладка «Основная игра»); второе — образцы r = кф 1xBet / кф Pinnacle."""
    D = DP.DISTS.get(str(m.id), (None,))[0]
    lines = [(mk, line, ks) for (tab, mk, line), ks in p.lines.items() if tab == 'main' and mk in ('ML', 'HCP', 'TOTAL', 'IT1', 'IT2')
             and isinstance(ks, tuple)]
    if D is None or not lines:
        return [], []

    def ours(mk, line, flip):
        """Наша запись (рынок, сторона игрока 1 / «больше», линия) для стороны 1 со скриншота."""
        if mk == 'ML':
            return 'ML', '2' if flip else '1', None
        if mk == 'HCP':
            return 'HCP', '2' if flip else '1', line
        if mk == 'TOTAL':
            return 'TOTAL', 'O', line
        return (FLIP[mk] if flip else mk), 'O', line

    def other(mk, pick):
        return {'1': '2', '2': '1', 'O': 'U', 'U': 'O'}[pick]

    # порядок игроков: где модель ближе к 1xBet без маржи
    err = {}
    for flip in (False, True):
        e = 0.0
        for mk, line, (k1, k2) in lines:
            if mk == 'TOTAL':
                continue
            m2, s1, l2 = ours(mk, line, flip)
            pm = DP.model_price(D, m2, s1, l2)[0]
            pb = PK.shin([k1, k2])[0]
            e += (PK.logit(pm) - PK.logit(pb)) ** 2
        err[flip] = e
    flip = err[True] < err[False]
    ev, _ = OD.find_event(events, row['player1'], row['player2'], row['start_utc']) if events else (None, False)
    q = OD.quotes(ev) if ev else {}
    out, rs = [], []
    for mk, line, (k1, k2) in lines:
        m2, s1, l2 = ours(mk, line, flip)
        fair_book = PK.shin([k1, k2])
        for s, k, pb in ((s1, k1, fair_book[0]), (other(m2, s1), k2, fair_book[1])):
            ln = l2 if m2 != 'HCP' or s == s1 else -l2
            pm, pp = DP.model_price(D, m2, s, ln)
            p_f, src = PK.blend(pm, pb), 'blend'
            pin = _pinnacle(q, m2, s, ln, row)
            if pin:
                p_f, src = PK.fair(pm, pin[0], 'pinnacle'), 'pinnacle'
                rs.append(k / pin[1])
            out.append(dict(DP._base(m, row), market=m2, pick=s, line=ln, label=DP.label(m2, s, ln, row['player1'], row['player2']),
                            p=float(p_f), p_src=src, p_model=pm, p_push=pp, k=float(k), k_src='screenshot', source='screenshot'))
    return out, rs


def _pinnacle(q: dict, mk: str, s: str, line, row) -> tuple[float, float] | None:
    """(P без маржи, кф Pinnacle) той же линии из котировок дня, если есть."""
    if not q:
        return None
    p1n, p2n = row['player1'], row['player2']
    side_name = lambda n: '1' if OD.same_name(n, p1n) >= OD.same_name(n, p2n) else '2'
    pairs = {}
    for (m, n, pt), books in q.items():
        if 'pinnacle' not in books:
            continue
        if mk == 'ML' and m == 'h2h':
            pairs[side_name(n)] = books['pinnacle']
        elif mk == 'TOTAL' and m == 'totals' and pt is not None and float(pt) == float(line):
            pairs['O' if n == 'Over' else 'U'] = books['pinnacle']
        elif mk == 'HCP' and m == 'spreads' and pt is not None:
            sd = side_name(n)
            if (sd == s and float(pt) == float(line)) or (sd != s and float(pt) == -float(line)):
                pairs[sd] = books['pinnacle']
    keys = ('O', 'U') if mk == 'TOTAL' else ('1', '2')
    if not all(k in pairs for k in keys):
        return None
    fair = PK.shin([pairs[k] for k in keys])
    return fair[keys.index(s)], pairs[s]


def learn_r(samples: list[float]) -> None:
    """Отношение кф 1xBet / Pinnacle: последние 300 сверок, медиана (используется с 5 сверок)."""
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
    """shots = [(id, имя, байты, описание)] → (предложения, ответы пользователю, журнал)."""
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
            replies.append(f"📸 {row['player1']} — {row['player2']}: линий со скриншота не распознано — пришлите вкладку «Основная игра» "
                           'с развёрнутыми рынками (тотал, фора, индивидуальные тоталы).')
            continue
        offers += o
        n = len({(x['market'], x['line']) for x in o})
        replies.append(f"📸 {row['player1']} — {row['player2']}: распознано линий — {n}. Подбираю ставку дня с учётом кф 1xBet.")
        log.append(f"скриншот: {row['player1']} — {row['player2']}, линий {n}")
    learn_r(rs)
    return offers, replies, log
