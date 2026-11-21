"""👥 Каперы · все матчи (ТЗ-005, п. 3 «насколько стоит прислушиваться» — 11.11.2026).

Точность каперов по ВСЕМ их футбольным матчам, а не только по 17 турнирам бота: оценка надёжности копится и в паузы.
  • Матчи и итоги — открытое табло ESPN «все турниры» (soccer/all/scoreboard, кэш по датам в data/tips/espn/).
  • Пары «Команда — Команда» из поста (или из nb-bet) → событие ESPN в окне [пост − 3 ч, пост + 48 ч]: обе команды,
    единственное совпадение, молодёжь/женщины не путаются со взрослыми.
  • Ставка — тот же разбор, что в tips.py; итог — по счёту 90 минут (овертайм/пенальти — как в линиях бота).
  • Без модели: база сравнения — их собственный кф. ROI = среднее(выигрыш·кф − 1), ошибка = σ/√n;
    ROI/ошибка ≥ 1,64 при ≥ 50 ставках с кф — источник обыгрывает рынок → ✔️ надёжный (право вето, tips.trusted).
  • Разовая догрузка истории каналов (веб-лента t.me/s/<канал>?before=<id>) за 30 дней — оценка сразу.
Файл: data/tips/tips_all.csv.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
import time

import pandas as pd

import fespn
import tips as T

FILE = T.DIR / 'tips_all.csv'
VERSION = 4                                         # версия разбора: смена → история каналов пересчитывается (18.11: матч по разбору)
ESPN_DIR = T.DIR / 'espn'
ALL = 'https://site.api.espn.com/apis/site/v2/sports/soccer/all/scoreboard?dates={d:%Y%m%d}&limit=1000'
COLS = ['uid', 'src', 'author', 'post_ts', 'edate', 'espn_id', 'start_utc', 'home', 'away', 'legs', 'label', 'k', 'raw',
        'status', 'result', 'settled']
TRUST_NK, TRUST_T = 50, 1.64
EXOT = re.compile(r'углов|карточ|жёлт|желт|фол|удар|офсайд|аут[ыо]|\bxg\b|сэйв|сейв|вброс|🟨|🚩|🥅', re.I)   # 18.11: рынки без счёта
YOUTH = re.compile(r'\bu-?\d{2}\b|\(ж\)|\bжен|\bwomen\b|\(w\)|\bmolod|\bмолод|\bii\b|\bb\b', re.I)

COUNTRY = {
    'италия': 'italy', 'бельгия': 'belgium', 'германия': 'germany', 'франция': 'france', 'испания': 'spain', 'англия': 'england',
    'португалия': 'portugal', 'нидерланды': 'netherlands', 'голландия': 'netherlands', 'греция': 'greece', 'турция': 'turkiye',
    'польша': 'poland', 'чехия': 'czechia', 'швейцария': 'switzerland', 'австрия': 'austria', 'дания': 'denmark',
    'швеция': 'sweden', 'норвегия': 'norway', 'финляндия': 'finland', 'шотландия': 'scotland', 'уэльс': 'wales',
    'ирландия': 'republic of ireland', 'россия': 'russia', 'мадагаскар': 'madagascar', 'гвинея-бисау': 'guinea bissau',
    'венесуэла': 'venezuela', 'гамбия': 'gambia', 'узбекистан': 'uzbekistan', 'северная ирландия': 'northern ireland', 'хорватия': 'croatia', 'сербия': 'serbia',
    'словения': 'slovenia', 'словакия': 'slovakia', 'венгрия': 'hungary', 'румыния': 'romania', 'болгария': 'bulgaria',
    'украина': 'ukraine', 'беларусь': 'belarus', 'грузия': 'georgia', 'армения': 'armenia', 'азербайджан': 'azerbaijan',
    'казахстан': 'kazakhstan', 'узбекистан': 'uzbekistan', 'исландия': 'iceland', 'израиль': 'israel', 'кипр': 'cyprus',
    'мальта': 'malta', 'албания': 'albania', 'босния': 'bosnia', 'черногория': 'montenegro', 'македония': 'macedonia',
    'косово': 'kosovo', 'люксембург': 'luxembourg', 'литва': 'lithuania', 'латвия': 'latvia', 'эстония': 'estonia',
    'молдова': 'moldova', 'фарерские': 'faroe', 'андорра': 'andorra', 'гибралтар': 'gibraltar', 'сан-марино': 'san marino',
    'лихтенштейн': 'liechtenstein', 'бразилия': 'brazil', 'аргентина': 'argentina', 'уругвай': 'uruguay', 'колумбия': 'colombia',
    'чили': 'chile', 'перу': 'peru', 'эквадор': 'ecuador', 'парагвай': 'paraguay', 'боливия': 'bolivia', 'венесуэла': 'venezuela',
    'мексика': 'mexico', 'сша': 'united states', 'канада': 'canada', 'япония': 'japan', 'корея': 'korea', 'австралия': 'australia',
    'марокко': 'morocco', 'египет': 'egypt', 'нигерия': 'nigeria', 'сенегал': 'senegal', 'гана': 'ghana', 'камерун': 'cameroon',
    'алжир': 'algeria', 'тунис': 'tunisia', 'саудовская аравия': 'saudi arabia', 'иран': 'iran', 'катар': 'qatar',
    'оаэ': 'united arab emirates', 'ирак': 'iraq', 'иордания': 'jordan', 'китай': 'china',
}


# ------------------------------------------------------------------ табло ESPN (все турниры)
def day_events(d: dt.date, fresh_min: int = 90) -> list[dict]:
    """События дня из кэша; запрос, если кэша нет или есть незавершённые и кэш старше fresh_min минут."""
    ESPN_DIR.mkdir(parents=True, exist_ok=True)
    f = ESPN_DIR / f'{d:%Y%m%d}.json'
    now = time.time()
    if f.exists():
        c = json.loads(f.read_text())
        done = all(e['kind'] for e in c['ev'])
        if done or now - c['t'] < fresh_min * 60:
            return c['ev']
    ev = [dict(id=m.id, start=m.start.strftime('%Y-%m-%d %H:%M'), home=m.home, away=m.away, kind=m.result_kind,
               hg=m.hg, ag=m.ag, odds=m.odds or {}) for m in fespn.parse(fespn._get(ALL.format(d=d)), 'all')]   # 21.11: + цены DK
    f.write_text(json.dumps(dict(t=now, ev=ev), ensure_ascii=False))
    return ev


# ------------------------------------------------------------------ сопоставление пары команд
def _ru_info(name: str) -> tuple[str | None, list[str], bool]:
    low = name.lower().strip(' .,:;!⚽️-–—')
    al = COUNTRY.get(low) or T._alias(low)
    ws = T._words(low)
    if not al:
        for n in (2, 1):
            for i in range(len(ws) - n + 1):
                al = al or COUNTRY.get(' '.join(ws[i:i + n])) or T._alias(' '.join(ws[i:i + n]))
    return al, [T._tr(w) for w in ws if len(w) >= 4], bool(YOUTH.search(low))


def _en_info(name: str) -> tuple[str, list[str], bool]:
    k = T._fold(name)
    return k, [x for x in k.split() if len(x) >= 4 and x not in T.STOP], bool(YOUTH.search(name.lower()))


def _same(ru, en) -> bool:
    al, ws, y1 = ru
    k, toks, y2 = en
    if y1 != y2:
        return False
    if al and any(re.search(r'\b' + re.escape(T._fold(x)) + r'\b', k)
                  for x in (al,) + T.EN_ALT.get(T._fold(al), ())):       # 21.11: «Кёльн» = «FC Cologne» (EN_ALT, как в tips)
        return True
    return any(T._like(w, x) for w in ws for x in toks)


def msk_time(text: str) -> int | None:
    """Время начала из поста «19:30 МСК» → минуты от полуночи UTC."""
    m = re.search(r'(\d{1,2})[:.](\d{2})\s*\(?мск', text.lower())
    return ((int(m.group(1)) - 3) * 60 + int(m.group(2))) % 1440 if m else None


def match_pair(a: str, b: str, events: list[dict], when: int | None = None) -> tuple[dict, bool] | None:
    """Единственное событие с обеими командами: (событие, перевёрнуто ли). Несколько (мужчины/женщины с одинаковым
    названием у ESPN) — выбирается по времени начала из поста (±20 мин)."""
    ra, rb = _ru_info(a), _ru_info(b)
    hits = {}
    for e in events:
        h, w = e.setdefault('_h', _en_info(e['home'])), e.setdefault('_a', _en_info(e['away']))
        if _same(ra, h) and _same(rb, w):
            hits[e['id']] = (e, False)
        elif _same(ra, w) and _same(rb, h):
            hits.setdefault(e['id'], (e, True))
    if len(hits) > 1:                                              # 18.11: «Атлетико — Барселона» — несколько «Атлетико …»:
        def sc(v):                                                 # берём событие, где совпало больше слов названий
            e, fl = v
            h, w = (e['_a'], e['_h']) if fl else (e['_h'], e['_a'])
            return sum(any(T._like(x, y) for y in h[1]) for x in ra[1]) + sum(any(T._like(x, y) for y in w[1]) for x in rb[1])
        best = max(sc(v) for v in hits.values())
        top = {k: v for k, v in hits.items() if sc(v) == best}
        if len(top) == 1:
            hits = top
    if len(hits) > 1 and when is not None:
        def mins(e):
            t = pd.Timestamp(e['start'])
            return t.hour * 60 + t.minute
        hits = {k: v for k, v in hits.items() if min(abs(mins(v[0]) - when), 1440 - abs(mins(v[0]) - when)) <= 20}
    return next(iter(hits.values())) if len(hits) == 1 else None


PAIR = re.compile(r'([A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9 .\'()\-]{1,38}?)\s+[-–—]\s+([A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9 .\'()\-]{1,38})')


PICKY = re.compile(r'ставк|прогноз|кф|коэф|тотал|\bи?т[бм]|\bит\d|фор[аы]|\bп[12]\b|обе забьют|промокод|http')


def pairs(text: str) -> list[tuple[int, str, str]]:
    """«Брайтон - Брентфорд» → (позиция, A, B) по строкам поста."""
    out, pos = [], 0
    for ln in text.split('\n'):
        m = None if PICKY.search(ln.lower()) else PAIR.search(ln)
        if m and len(re.findall(r'[A-Za-zА-Яа-яЁё]', m.group(2))) >= 3:
            out.append((pos + m.start(), m.group(1).strip(), m.group(2).strip()))
        pos += len(ln) + 1
    return out


FLIP = {('1X2', 'H'): 'A', ('1X2', 'A'): 'H', ('DC', '1X'): 'X2', ('DC', 'X2'): '1X', ('AH', 'H'): 'A', ('AH', 'A'): 'H'}


def _flip(legs):
    out = []
    import halves as HV
    for mk, s, L in legs:
        if HV.is_half(mk):                                         # 21.11 К17: ставки на таймы
            out.append(HV.flip((mk, s, L), lambda lg: _flip([lg])[0]))
        elif mk in ('IT1', 'IT2'):
            out.append(('IT2' if mk == 'IT1' else 'IT1', s, L))
        else:
            out.append((mk, FLIP.get((mk, s), s), L))
    return out


# ------------------------------------------------------------------ хранилище
def load() -> pd.DataFrame:
    if FILE.exists():
        return pd.read_csv(FILE, dtype=str, keep_default_na=False).reindex(columns=COLS, fill_value='')
    return pd.DataFrame(columns=COLS)


def save(df: pd.DataFrame) -> None:
    T.DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(FILE, index=False)


def _events_around(ts: pd.Timestamp, cache: dict) -> list[dict]:
    out = []
    for d in {(ts - pd.Timedelta(hours=3)).date(), ts.date(), (ts + pd.Timedelta(days=1)).date(), (ts + pd.Timedelta(days=2)).date()}:
        if d > dt.date.today() + dt.timedelta(days=2):
            continue
        if d not in cache:
            try:
                cache[d] = [dict(e, edate=f'{d:%Y%m%d}') for e in day_events(d)]
            except Exception:
                cache[d] = []
        out += cache[d]
    lo, hi = ts - pd.Timedelta(hours=3), ts + pd.Timedelta(hours=48)
    return [e for e in out if lo <= pd.Timestamp(e['start']) <= hi]


def _row(src, author, ts, e, flip, legs, k, raw, uid) -> dict | None:
    if not legs:
        return None
    legs = _flip(legs) if flip else legs
    return dict(uid=uid, src=src, author=author, post_ts=str(ts)[:16], edate=e['edate'], espn_id=str(e['id']),
                start_utc=e['start'], home=e['home'], away=e['away'], legs=T._enc(legs),
                label=' + '.join(T.leg_label(lg) for lg in legs), k=f'{k:.2f}' if k else '', raw=raw[:160].replace('\n', ' '),
                status='', result='', settled='')


def from_post(src: str, name: str, p: dict, cache: dict, have: set) -> list[dict]:
    out = []
    # 18.11: «✅ПРОГНОЗ НА МАТЧ:» отдельной строкой, ставка — на следующих (до пустой строки) → одна строка (как tipsbook v7)
    txt = re.sub(r'(прогноз[^\n:]{0,20}:)[ \t]*\n+((?:[^\n]+\n?){1,2})', lambda m: m.group(1) + ' ' + m.group(2).replace('\n', ' ') + '\n',
                 p['text'], flags=re.I)
    p = dict(p, text=txt)
    ps = pairs(txt)
    if not ps:                                                     # пары нет («Прогноз: Фора Бари (0)») — одна команда в ставке
        return _single(src, name, p, cache, have)
    evs = _events_around(p['ts'], cache)
    found = []                                                     # 18.11: «пара» из прозы разбора («… — Рандалем Коло-Муани»)
    for n, (pos, a, b) in enumerate(ps):                           # обрезала текст до прогноза → фрагмент ставки тянется
        frag0 = txt[pos:ps[n + 1][0] if n + 1 < len(ps) else len(txt)]   # до следующей НАЙДЕННОЙ на табло пары
        hit = match_pair(a, b, evs, msk_time(frag0))
        if hit or (len(a.split()) <= 4 and len(b.split()) <= 4):   # заголовок матча (короткие названия) — граница фрагмента,
            found.append((pos, hit))                               # даже если матча нет на табло; длинная «пара» из прозы — нет
    for n, (pos, hit) in enumerate(found):
        if not hit:
            continue
        e, flip = hit
        uid = f"tg:{src}:{p['id']}:{e['id']}"
        if uid in have:
            continue
        frag = txt[pos:found[n + 1][0] if n + 1 < len(found) else len(txt)]
        tm = (e['away'], e['home']) if flip else (e['home'], e['away'])
        side, legs, sel = None, [], ''
        row = dict(home=tm[0], away=tm[1])
        side = T._side_before(frag, row)
        for cand, nat in T._pick_lines(frag):
            if EXOT.search(cand) or len(cand) > 90:               # 18.11: угловые / карточки / фолы / удары — не голы;
                continue                                           # длинная строка — проза разбора, а не ставка
            legs = T.parse_legs(cand, side, tm, nat)
            if legs and all(not (mk in ('TOT', 'IT1', 'IT2') and isinstance(L, float) and L > 7.5) for mk, _, L in legs):
                sel = cand
                break
            legs = []
        r = _row(src, name, p['ts'], e, flip, legs, T.odds_in(sel), sel, uid)
        if r:
            out.append(r); have.add(uid)
    return out


def _single(src: str, name: str, p: dict, cache: dict, have: set) -> list[dict]:
    """Ставка с одной командой: единственное событие окна, где эта команда играет (слова ≥ 5 букв или псевдоним)."""
    sel = next((c for c, nat in T._pick_lines(p['text']) if nat), '')
    if not sel or T.SKIP.search(sel.lower()):
        return []
    # 18.11: пост одной строкой — «строка ставки» захватывала весь разбор, и обычные слова («команда» → Richmond) становились
    # «командой». Ставка — до конца фразы ставки; командой считается только слово с заглавной буквы (имя собственное).
    sel = re.split(r'☑|✔|\s{2,}|\.\s+[А-ЯЁA-Z]|!|\n|(?<=\d)\s+[А-ЯЁA-Z][а-яё]+,', sel)[0].strip()
    caps = {w.lower() for w in re.findall(r'\b[А-ЯЁA-Z][а-яёa-z\-]{3,}', sel)}
    ws = [w for w in T._words(sel) if (w in caps and len(w) >= 5) or T._alias(w)]
    evs = _events_around(p['ts'], cache)
    if not ws:                                                     # «Прогноз: ТБ (2.5)» — команд в ставке нет: ищем в разборе
        return _prose(src, name, p, cache, have, sel, evs)
    hits = {}
    for w in ws:
        ru = _ru_info(w)
        if not ru[0] and not ru[1]:
            continue
        for e in evs:
            h, a = e.setdefault('_h', _en_info(e['home'])), e.setdefault('_a', _en_info(e['away']))
            if _same(ru, h) or _same(ru, a):
                hits[e['id']] = e
    if len(hits) != 1:
        return _prose(src, name, p, cache, have, sel, evs)
    e = next(iter(hits.values()))
    uid = f"tg:{src}:{p['id']}:{e['id']}"
    if uid in have:
        return []
    legs = T.parse_legs(sel, None, (e['home'], e['away']), True)
    r = _row(src, name, p['ts'], e, False, legs, T.odds_in(sel), sel, uid)
    if r:
        have.add(uid)
        return [r]
    return []


def _prose(src: str, name: str, p: dict, cache: dict, have: set, sel: str, evs: list[dict]) -> list[dict]:
    """18.11 (пользователь: «не может быть, что Грозный ничего не угадывает»): Грозный пишет «Лига. ✅ Прогноз: ТБ (2.5).
    ☑️ Ювентус дома силён против Наполи…» — пары в строке нет, команда в ставке не названа → 29 из 36 прогнозов ноября не
    попадали в статистику. Матч ищется по тексту разбора: ОБЕ команды события названы в посте и такое событие в окне одно
    (прошлые соперники из разбора отсекаются требованием обеих команд и единственности)."""
    ws0 = T._words(p['text'])
    words = [w for w in ws0 if len(w) >= 4 or T._alias(w)]
    words += [f'{a} {b}' for a, b in zip(ws0, ws0[1:]) if T._alias(f'{a} {b}')]   # 21.11: «Крылья Советов», «Северная Ирландия»
    info = [_ru_info(w) for w in dict.fromkeys(words)]
    info = [(x[0], [], x[2]) for x in info if x[0]]                # только команды из справочника (без транслита: проверка 18.11 —
    #                                                                 «Ребят…» давало Richmond — Fordham, «сборные» — Chapecoense)
    if not info or not evs:
        return []
    hits = {}
    for e in evs:
        h, a = e.setdefault('_h', _en_info(e['home'])), e.setdefault('_a', _en_info(e['away']))
        if any(_same(x, h) for x in info) and any(_same(x, a) for x in info):
            hits[e['id']] = e
    if len(hits) != 1:
        return []
    e = next(iter(hits.values()))
    uid = f"tg:{src}:{p['id']}:{e['id']}"
    if uid in have:
        return []
    legs = T.parse_legs(sel, None, (e['home'], e['away']), True)
    parts = len(re.findall(r'\s(?:и|\+)\s', ' ' + sel.lower() + ' ')) + 1
    if len(legs or []) < parts:                                    # «не проиграет и ТБ (1.5)» разобрано наполовину — не считать
        return []
    r = _row(src, name, p['ts'], e, False, legs, T.odds_in(sel), sel, uid)
    if r:
        have.add(uid)
        return [r]
    return []


def collect(log: list[str]) -> int:
    """Посты и лента nb-bet, уже прочитанные tips.collect в этом прогоне (T.LAST_POSTS, T.LAST_FEED)."""
    df = load()
    have = set(df.uid)
    cache: dict = {}
    new = []
    now = pd.Timestamp.now(tz='UTC').tz_localize(None)
    for src, name, p in T.LAST_POSTS:
        new += from_post(src, name, p, cache, have)
    evs = _events_around(now, cache) if T.LAST_FEED else []
    for t in T.LAST_FEED:
        uid = f"nb:{t['id']}"
        if uid in have:
            continue
        hit = match_pair(t['home'], t['away'], evs)
        if hit:
            e, flip = hit
            tm = (e['away'], e['home']) if flip else (e['home'], e['away'])
            r = _row(T.NB, t['author'], now, e, flip, T.parse_legs(t['sel'], None, tm), t['k'], t['sel'], uid)
            if r:
                new.append(r); have.add(uid)
    if new:
        df = pd.concat([df, pd.DataFrame(new)], ignore_index=True)
    save(df)
    log.append(f'👥 все матчи: новых {len(new)}, всего {len(df)}')
    return len(new)


def backfill(rt: dict, log: list[str], days: int = 30, pages: int = 25) -> None:
    """Разово: история каналов за days дней (веб-лента, страницы по 20 постов) → прогнозы → итоги."""
    df = load()
    if rt.get('tips_wide_v') != VERSION:                           # правила разбора изменились — история каналов заново
        df = df[~df.uid.str.startswith('tg:')]
        save(df)
        rt.pop('tips_backfill', None)
        rt['tips_wide_v'] = VERSION
    if rt.get('tips_backfill'):
        return
    have = set(df.uid)
    cache: dict = {}
    new, stat = [], []
    edge = pd.Timestamp.now(tz='UTC').tz_localize(None) - pd.Timedelta(days=days)
    for ch, name in T.TG.items():
        before, got, seen = None, 0, 0
        for _ in range(pages):
            try:
                posts = T.tg_posts(ch, before)
            except Exception:
                break
            if not posts:
                break
            for p in posts:
                if p['ts'] >= edge:
                    seen += 1
                    rows = from_post(ch, name, p, cache, have)
                    new += rows
                    got += len(rows)
            before = min(p['id'] for p in posts)
            if min(p['ts'] for p in posts) < edge:
                break
            time.sleep(1.0)
        stat.append(f'{name} {got}/{seen}')
    if not any(cache.values()):                                    # табло ESPN не ответило — повторить в следующем прогоне
        log.append('👥 история: табло ESPN недоступно — повтор позже; ' + ', '.join(stat))
        return
    if new:
        df = pd.concat([df, pd.DataFrame(new)], ignore_index=True)
    save(df)
    rt['tips_backfill'] = pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M')
    log.append(f'👥 история {days} дн (ставок/постов): ' + ', '.join(stat) + f'; дней табло ESPN {len(cache)}')


def settle(log: list[str], max_days: int = 40) -> int:
    """Итоги по табло ESPN: матчи, начавшиеся > 2,5 ч назад; запрос даты — только если в кэше не всё завершено."""
    df = load()
    if df.empty:
        return 0
    now = pd.Timestamp.now(tz='UTC').tz_localize(None)
    todo = df[(df.settled == '') & (pd.to_datetime(df.start_utc, errors='coerce') < now - pd.Timedelta(hours=2.5))]
    n, today = 0, now.strftime('%Y-%m-%d')
    for ed in list(dict.fromkeys(todo.edate))[:max_days]:
        try:
            evs = {str(e['id']): e for e in day_events(dt.datetime.strptime(ed, '%Y%m%d').date(), fresh_min=60)}
        except Exception:
            continue
        for i, r in todo[todo.edate == ed].iterrows():
            e = evs.get(str(r.espn_id))
            if not e or not e['kind']:
                if e is None or pd.Timestamp(r.start_utc) < now - pd.Timedelta(days=4):
                    df.loc[i, ['status', 'result', 'settled']] = ['VOID', 'P', today]   # пропал / не доигран 4 дня — возврат
                continue
            ft = e['kind'] == 'FT'
            v = T.settle_legs(T._dec(r.legs), e['kind'], int(e['hg']) if ft else None, int(e['ag']) if ft else None, r.espn_id)
            if not v:                                              # таймы: счёта таймов ещё нет — следующим прогоном
                if pd.Timestamp(r.start_utc) >= now - pd.Timedelta(days=4):
                    continue
                v = 'P'                                            # 4 дня без счёта таймов — возврат (не висеть вечно)
            df.loc[i, ['status', 'result', 'settled']] = [e['kind'], v, today]
            n += 1
    save(df)
    log.append(f'👥 все матчи: рассчитано {n}')
    return n


# ------------------------------------------------------------------ учёт
def stats() -> pd.DataFrame:
    d = load()
    d = d[d.result.isin(['W', 'L'])].copy()
    if d.empty:
        return pd.DataFrame(columns=['src', 'name', 'n', 'won', 'nk', 'kavg', 'roi', 'se', 't'])
    d['w'] = (d.result == 'W').astype(int)
    d['kk'] = pd.to_numeric(d.k, errors='coerce')
    rows = []
    for src, g in d.groupby('src'):
        gk = g[g.kk.notna()]
        r = gk.w * gk.kk - 1
        roi = float(r.mean()) if len(gk) else float('nan')
        se = float(r.std(ddof=1) / math.sqrt(len(gk))) if len(gk) > 1 else float('nan')
        rows.append(dict(src=src, name=T.TG.get(src, src), n=len(g), won=int(g.w.sum()), nk=len(gk),
                         kavg=float(gk.kk.mean()) if len(gk) else float('nan'), roi=roi, se=se,
                         t=roi / se if se and se == se and se > 0 else 0.0))
    return pd.DataFrame(rows).sort_values('n', ascending=False)


def trusted() -> set[str]:
    st = stats()
    return set(st[(st.nk >= TRUST_NK) & (st.t >= TRUST_T)].src) if len(st) else set()


def report_block() -> str:
    """«👥 Все матчи»: источник (ср. кф) | угадано/n | % ; ROI ± ошибка по их кф."""
    import rtable
    st = stats()
    if st.empty:
        return '👥 <b>Каперы · все матчи</b>: рассчитанных ставок пока нет'
    rows = []
    for r in st.itertuples(index=False):
        mark = '✔️' if (r.nk >= TRUST_NK and r.t >= TRUST_T) else ('🟢' if r.t >= TRUST_T else '🔴' if r.t <= -TRUST_T else '🟡')
        goal = rtable.num(r.kavg, 2) if r.kavg == r.kavg else '—'
        rows.append(rtable.row(r.name[:10], r.won, r.n, goal, mark if r.nk else ''))
    roi = ' · '.join(f"{r.name[:10]} {'+' if r.roi > 0 else ''}{rtable.pct(r.roi, 0)}±{rtable.pct(r.se, 0)}% (n{r.nk})"
                     for r in st.itertuples(index=False) if r.nk > 1)
    return ('👥 <b>Каперы · все матчи</b>\n' + rtable.render(rows, ('Источник (кф)', 'Кол-во', '%'))
            + (f'\nROI: {roi}' if roi else ''))


# ------------------------------------------------------------------ 18.11 независимая сверка строк книги (К13)
# Книга каналов (tipsbook) знает пару команд и текст ставки, но итог ставила только по отметке ✅/❌ самого канала. Здесь каждая
# футбольная строка книги сверяется с табло ESPN: пара → событие, ставка (форматы Каппер UK и проза → обычная запись) → итог по
# счёту 90 минут. Файл data/tips/book_ind.csv (uid книги → событие, ноги, итог); отчёт каперов (caprep) берёт итог отсюда.
BOOK = T.DIR / 'book.csv'
BOOK_IND = T.DIR / 'book_ind.csv'
IND_COLS = ['uid', 'espn_id', 'home', 'away', 'legs', 'label', 'why', 'status', 'result', 'settled']
NICK = {'горожан': 'манчестер сити', 'шмел': 'боруссия', 'сине-гранатов': 'барселона', 'сливочн': 'реал', 'канонир': 'арсенал',
        'красных дьявол': 'манчестер юнайтед', 'матрасник': 'атлетико', 'нерадзурри': 'интер', 'россонери': 'милан',
        'парижан': 'псж', 'баварц': 'бавария', 'подводник': 'вильярреал', 'бьянконер': 'ювентус', 'старой синьор': 'ювентус',
        'аспирин': 'байер', 'фармацевт': 'байер', 'вилланов': 'астон вилла', 'аристократ': 'челси', 'пенсионер': 'челси',
        'красных': 'ливерпуль', 'ирискин': 'эвертон', 'чаек': 'брайтон', 'чаёк': 'брайтон', 'сорок': 'ньюкасл', 'шпор': 'тоттенхэм'}
NUM = {'один': 1, 'одного': 1, 'два': 2, 'двух': 2, 'три': 3, 'трёх': 3, 'трех': 3, 'четыре': 4, 'четырёх': 4, 'четырех': 4, 'пять': 5}
MKT = r'\bт[бм]\b|\bит[12]?[бм]|ф\d?\(|\bф[12]\b|победа|не проиграет|обе забьют|\bп[12]\b|\b1x\b|\bx2\b|\b12\b'
NODATA = re.compile(r'тайм|углов|карточ|жёлт|желт|фол|удар|офсайд|\bxg\b|сейв|сэйв|вброс|🟨|🚩|🥅|🎯', re.I)


def _n(x: str) -> str:
    return str(NUM.get(x, x))


def norm_pick(sel: str) -> str:
    """Форматы Каппер UK («Тотал. (2) Б кф 1.965», «Фора. 1 (-1.5)», «1Х2. П1») и проза («минимум три гола», «победа
    «горожан» минимум в два мяча», «не больше 4 голов») → запись, которую понимает tips.parse_legs."""
    s = ' ' + sel.lower().replace('ё', 'е').replace('«', ' ').replace('»', ' ').replace('—', '-').replace('–', '-') + ' '
    s = re.split(r'\.\s*для ставки|\bкф\b|@|логичн|рабоч|основн(?:ой)? сценар|считаю', s)[0]
    s = re.sub(r'\btb\b', 'тб', re.sub(r'\btm\b', 'тм', s))                  # «TB (1.5)» с картинки (латиница)
    s = re.sub(r'[^.]*(?:дома|на выезде) слабее\.?', ' ', s)          # «Вилла на выезде слабее. Победа …»
    s = re.sub(r'\s+', ' ', s)
    s = re.sub(r'(\b1x|\bx2|\b12|\bп1|\bп2|\bх2|\b1х)\s*и(тм|тб)\b', r'\1 и \2', s)   # «1X иТМ (3.5)» (не «ИТБ»)
    s = re.sub(r'ничья или победа (\S+)', r' \1 не проиграет ', s)       # «Ничья или победа «Хоффенхайма»» — X2/1X, не П
    s = re.sub(r'тотал и обе команды забьют,?\s*(больше|меньше)\s*([\d.,]+)\s*и\s*да',
               lambda m: f" т{'б' if m.group(1) == 'больше' else 'м'} {m.group(2)} и обе забьют да ", s)   # купон Green Line
    s = re.sub(r'результат и обе команды забьют,?\s*(.+?)\s+и\s+обе забьют', r' победа \1 и обе забьют да ', s)
    s = re.sub(r'результат основного времени,?\s*', ' победа ', s)
    s = re.sub(r'(?:первый|второй),\s*результат\s*\+\s*тотал\.?', ' ', s)
    s = re.sub(r'(?:двойной шанс|тотал)\s*(?:и|\+)\s*обе забьют\.?', ' ', s)
    s = re.sub(r'индивидуальный тотал\s*([12])-го\.?\s*\(([\d.,]+)\)\s*([бм])', lambda m: f' ит{m.group(1)}{m.group(3)}({m.group(2)}) ', s)
    s = re.sub(r'тотал\.?\s*\(([\d.,]+)\)\s*([бм])', lambda m: f' т{m.group(2)}({m.group(1)}) ', s)
    s = re.sub(r'фора\.?\s*([12])\s*\(([-+\d.,]+)\)', r' ф\1(\2) ', s)
    s = re.sub(r'1x2\.?\s*|1х2\.?\s*', ' ', s)
    s = re.sub(r'обе забьют\.?\s*да', ' обе забьют да', s)
    s = re.sub(r'минимум (\w+) гол\w*', lambda m: f' тб {int(_n(m.group(1))) - 0.5} ' if _n(m.group(1)).isdigit() else m.group(0), s)
    s = re.sub(r'не больше (\w+) гол\w*', lambda m: f' тм {int(_n(m.group(1))) + 0.5} ' if _n(m.group(1)).isdigit() else m.group(0), s)
    for k, v in NICK.items():
        s = re.sub(k + r'\w*', v, s)
    s = re.sub(r'победа (.+?) минимум в (\w+) мяч\w*', lambda m: f' {m.group(1)} ф(-{int(_n(m.group(2))) - 0.5}) '
               if _n(m.group(2)).isdigit() else m.group(0), s)
    seg = [x for x in re.split(r'\.\s', s) if re.search(MKT, x)]   # несколько предложений — только те, где есть ставка
    if seg and len(seg) < len(re.split(r'\.\s', s)):                   # («Вердер дома не всегда побеждает. Ничья или победа …»)
        s = ' . '.join(seg)
    if 'победа' in s and 'тб' not in s and 'тм' not in s:          # «Вилла на выезде слабее. Победа …» — важна только победа
        s = s[s.index('победа'):]
    return re.sub(r'\s+', ' ', s).strip()


def _stems(name: str) -> set[str]:
    return {w[:5] for w in T._words(name.lower().replace('ё', 'е')) if len(w) >= 3}


def _side_ru(s: str, a: str, b: str) -> bool | None:
    """Команда, названная в ставке: True — первая в паре поста, False — вторая, None — не названа / обе."""
    ws = {w[:5] for w in T._words(s) if len(w) >= 3}
    ia, ib = bool(ws & _stems(a)), bool(ws & _stems(b))
    return True if ia and not ib else False if ib and not ia else None


def _ind_load() -> pd.DataFrame:
    if BOOK_IND.exists():
        return pd.read_csv(BOOK_IND, dtype=str, keep_default_na=False).reindex(columns=IND_COLS, fill_value='')
    return pd.DataFrame(columns=IND_COLS)


def book_ind(log: list[str], days: int = 45) -> int:
    """Сверка строк книги с табло: новые и ещё не рассчитанные (не старше days дней)."""
    try:
        b = pd.read_csv(BOOK, dtype=str, keep_default_na=False)
    except Exception:
        return 0
    now = pd.Timestamp.now(tz='UTC').tz_localize(None)
    b = b[(b.sport == 'football') & (b.a != '') & (b.seen != 'post') & ~b.pick.str.startswith('❔')
          & (pd.to_datetime(b.post_ts, errors='coerce') > now - pd.Timedelta(days=days))]
    ind = _ind_load()
    done = {u for u, r, w in zip(ind.uid, ind.result, ind.why) if r in ('W', 'L', 'P') or w in ('нет данных', 'не разобрано')}
    rows = {u: i for i, u in enumerate(ind.uid)}
    cache: dict = {}
    out = ind.to_dict('records')
    n = 0
    for r in b.itertuples(index=False):
        if r.uid in done:
            continue
        sel = str(r.pick)
        rec = dict(uid=r.uid, espn_id='', home='', away='', legs='', label='', why='', status='', result='', settled='')
        if NODATA.search(sel):
            rec['why'] = 'нет данных'
        else:
            ts = pd.Timestamp(r.post_ts)
            hit = match_pair(r.a, r.b, _events_around(ts, cache), msk_time(str(r.raw)))
            if not hit:
                rec['why'] = 'матч не найден'
            else:
                e, flip = hit
                tm = (e['away'], e['home']) if flip else (e['home'], e['away'])
                s = norm_pick(sel)
                legs = T.parse_legs(s, _side_ru(s, r.a, r.b), tm, True)
                parts = len(re.findall(r'\s(?:и|\+)\s', ' ' + s + ' ')) + 1
                if 'победа' in s and re.search(r'\bт[бм]\b', s) and ' и ' not in s:   # «не больше 4 голов. Победа «шмелей»»
                    pw = s[s.index('победа'):]
                    lw = T.parse_legs(pw, _side_ru(pw, r.a, r.b), tm, True)
                    legs = (legs or []) + [x for x in (lw or []) if x[0] == '1X2' and not any(y[0] == '1X2' for y in (legs or []))]
                    parts = 2
                if not legs or len(legs) < parts:
                    rec.update(espn_id=str(e['id']), home=e['home'], away=e['away'], why='не разобрано')
                else:
                    legs = _flip(legs) if flip else legs
                    rec.update(espn_id=str(e['id']), home=e['home'], away=e['away'], legs=T._enc(legs),
                               label=' + '.join(T.leg_label(lg) for lg in legs))
                    v = T.settle_legs(legs, e['kind'], int(e['hg']) if e['kind'] == 'FT' else None,
                                      int(e['ag']) if e['kind'] == 'FT' else None, e['id']) if e.get('kind') else ''
                    if v:
                        rec.update(status=e['kind'], result=v, settled=f'{now:%Y-%m-%d}')
                        n += 1
        if r.uid in rows:
            out[rows[r.uid]] = rec
        else:
            rows[r.uid] = len(out)
            out.append(rec)
    pd.DataFrame(out, columns=IND_COLS).to_csv(BOOK_IND, index=False)
    log.append(f'👥 книга × табло: рассчитано {n}, строк {len(out)}')
    return n
