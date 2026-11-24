"""📷 Купоны букмекера на картинках каперов (Green Line — 1WIN): OCR-текст → купон.

Купон: ID, статус (open / W / L / P), тип (ординар / экспресс), кф (из «ставка » выигрыш» — точнее значка),
сумма ставки, события: вид спорта, команды, рынок как написан и в краткой записи бота (ТБ 2.5, ИТ1Б(2), Ф1(-1.5), П1 …).
Два вида карточек 1WIN: «A — B» + рынок одной строкой; блок «Событие» (вид • страна • лига, команда 1, команда 2, дата, рынок).
Честный учёт: купон, впервые увиденный уже рассчитанным, — «постфактум» (канал показывает в основном выигрыши) —
в точность не идёт; считаются только купоны, показанные до матча (open), итог — по их же ID или по табло.
"""
from __future__ import annotations

import re

NUM = r'(\d+(?:[.,]\d+)?)'
SPORT_WORDS = {'футбол': 'football', 'хоккей': 'hockey', 'теннис': 'tennis', 'баскетбол': 'basketball', 'волейбол': 'volleyball',
               'киберспорт': 'esports', 'настольный теннис': 'tabletennis'}
STATUS = [('W', r'выиграна'), ('L', r'проиграна'), ('P', r'возврат'), ('open', r'открыта')]


def _amount(s: str) -> float | None:
    m = re.search(r'(\d[\d ]{0,9}[.,]\d{2})', s)
    return float(m.group(1).replace(' ', '').replace(',', '.')) if m else None


def _market(txt: str, a: str, b: str) -> str:
    """Рынок 1WIN → краткая запись для разбора (tips.parse_legs)."""
    t = re.sub(r'\s+', ' ', txt.strip().lower().replace('ё', 'е'))
    m = re.search(r'индивидуальный тотал (.+?),? (больше|меньше) ' + NUM, t)
    if m:
        return f"{m.group(1)} ИТ{'Б' if m.group(2) == 'больше' else 'М'}({m.group(3)})"
    m = re.search(r'^тотал,? (больше|меньше) ' + NUM, t)
    if m:
        return f"Т{'Б' if m.group(1) == 'больше' else 'М'}({m.group(2)})"
    m = re.search(r'фора,? (.+?),? \(?([+-−]?\d+(?:[.,]\d+)?)\)?$', t)
    if m:
        return f"{m.group(1)} фора ({m.group(2).replace('−', '-')})"
    m = re.search(r'обе (?:команды )?забьют,? (да|нет)', t)
    if m:
        return f'обе забьют {m.group(1)}'
    m = re.search(r'(?:победа|исход матча,?|победитель,?|результат основного времени,?) (.+)', t)   # 24.11: 1WIN «Результат основного времени, Арсенал»
    if m:
        w = m.group(1).strip()
        if w in ('ничья', 'x'):
            return 'ничья'
        return f'победа {w}'
    m = re.search(r'двойной шанс,? (.+)', t)
    if m:
        w = m.group(1)
        return '1X' if ('1x' in w or 'или ничья' in w and a.lower()[:4] in w) else 'X2' if ('x2' in w or 'или ничья' in w) else '12'
    return txt.strip()


def parse(ocr: str) -> dict | None:
    """OCR-текст картинки → купон или None (не купон)."""
    low = ocr.lower()
    if not re.search(r'ординар|экспресс', low):
        return None
    sid = re.search(r'id\s*(\d{7,})', low)
    head = '\n'.join(ln for ln in low.split('\n')[:4] if re.search(r'ординар|экспресс', ln)) or low
    status = next((s for s, rx in STATUS if re.search(rx, head)), '') or next((s for s, rx in STATUS if re.search(rx, low)), '')
    if not status and re.search(r'продать за|возможный выигрыш', low):   # 21.11: открытый купон 1WIN без слова «открыта»
        status = 'open'                                                   # (Green Line 19831: «Продать за 30 000» → был «после матча»)
    kind = 'express' if 'экспресс' in low else 'single'
    lines = [x.strip() for x in ocr.split('\n') if x.strip()]
    # кф: «50 000,00 RUB » 72 500,00 RUB» или «Сумма ставки … / (Возможный) выигрыш …»
    stake = win = None
    for ln in lines:
        m = re.search(r'(\d[\d ]*[.,]\d{2})\s*\S{0,4}\s*[»>›]+\s*(\d[\d ]*[.,]\d{2})', ln)
        if m:
            stake, win = _amount(m.group(1)), _amount(m.group(2))
            break
    if stake is None:
        for n, ln in enumerate(lines):
            if 'сумма ставки' in ln.lower():
                stake = _amount(ln)
            if 'выигрыш' in ln.lower() and win is None:
                win = _amount(ln) or (_amount(lines[n + 1]) if n + 1 < len(lines) else None)
    k = round(win / stake, 3) if stake and win and win > stake else None
    if k is None:
        m = re.search(r'(?:ординар|экспресс)[^\n]*?(\d\.\d{2})', low)
        k = float(m.group(1)) if m else None
    # события
    events = []
    sport = ''
    for n, ln in enumerate(lines):
        s = ln.lower()
        m = re.match(r'([а-яё ]+?)\s*[»•*·]\s*', s)
        if m and m.group(1).strip() in SPORT_WORDS:
            sport = SPORT_WORDS[m.group(1).strip()]
            # блок «Событие»: команда 1, команда 2, дата, рынок (может занимать 2 строки, кф между ними)
            a, b = (lines[n + 1] if n + 1 < len(lines) else ''), (lines[n + 2] if n + 2 < len(lines) else '')
            rest = [x for x in lines[n + 3:n + 7] if not re.search(r'\d{1,2} [а-я]+,? \d{1,2}:\d{2}|^\d\.\d{2}$', x.lower())]
            mk = ' '.join(re.sub(r'\b\d\.\d{2}\b', '', x).strip() for x in rest[:2])
            events.append(dict(sport=sport, a=a, b=b, market=mk.strip(' ,'), short=_market(mk, a, b)))
        elif ' — ' in ln or ' - ' in ln:
            parts = re.split(r'\s+[—-]\s+', ln, maxsplit=1)
            if len(parts) == 2 and all(len(re.findall(r'[а-яёa-z]', x.lower())) >= 3 for x in parts) and n + 1 < len(lines):
                a, b = (re.sub(r'^\W+', '', parts[0]).strip(), parts[1].strip())
                mk = re.sub(r'\b\d\.\d{2}\b', '', lines[n + 1]).strip(' ,|[]')
                if re.search(r'тотал|фора|победа|исход|забьют|шанс|больше|меньше|выигра|тайм|ничья|результат', mk.lower()):   # 21.11: «Выиграет оба тайма»
                    events.append(dict(sport=sport, a=a, b=b, market=mk, short=_market(mk, a, b)))
    if not events:
        return None
    return dict(id=sid.group(1) if sid else '', status=status, kind=kind, k=k, stake=stake, events=events)
