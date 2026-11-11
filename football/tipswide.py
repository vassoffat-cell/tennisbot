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
ESPN_DIR = T.DIR / 'espn'
ALL = 'https://site.api.espn.com/apis/site/v2/sports/soccer/all/scoreboard?dates={d:%Y%m%d}&limit=1000'
COLS = ['uid', 'src', 'author', 'post_ts', 'edate', 'espn_id', 'start_utc', 'home', 'away', 'legs', 'label', 'k', 'raw',
        'status', 'result', 'settled']
TRUST_NK, TRUST_T = 50, 1.64
YOUTH = re.compile(r'\bu-?\d{2}\b|\(ж\)|\bжен|\bwomen\b|\(w\)|\bmolod|\bмолод|\bii\b|\bb\b', re.I)

COUNTRY = {
    'италия': 'italy', 'бельгия': 'belgium', 'германия': 'germany', 'франция': 'france', 'испания': 'spain', 'англия': 'england',
    'португалия': 'portugal', 'нидерланды': 'netherlands', 'голландия': 'netherlands', 'греция': 'greece', 'турция': 'turkiye',
    'польша': 'poland', 'чехия': 'czechia', 'швейцария': 'switzerland', 'австрия': 'austria', 'дания': 'denmark',
    'швеция': 'sweden', 'норвегия': 'norway', 'финляндия': 'finland', 'шотландия': 'scotland', 'уэльс': 'wales',
    'ирландия': 'republic of ireland', 'северная ирландия': 'northern ireland', 'хорватия': 'croatia', 'сербия': 'serbia',
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
               hg=m.hg, ag=m.ag) for m in fespn.parse(fespn._get(ALL.format(d=d)), 'all')]
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
    if al and re.search(r'\b' + re.escape(T._fold(al)) + r'\b', k):
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
    for mk, s, L in legs:
        if mk in ('IT1', 'IT2'):
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
    txt = p['text']
    ps = pairs(txt)
    if not ps:                                                     # пары нет («Прогноз: Фора Бари (0)») — одна команда в ставке
        return _single(src, name, p, cache, have)
    evs = _events_around(p['ts'], cache)
    for n, (pos, a, b) in enumerate(ps):
        frag0 = txt[pos:ps[n + 1][0] if n + 1 < len(ps) else len(txt)]
        hit = match_pair(a, b, evs, msk_time(frag0))
        if not hit:
            continue
        e, flip = hit
        uid = f"tg:{src}:{p['id']}:{e['id']}"
        if uid in have:
            continue
        frag = txt[pos:ps[n + 1][0] if n + 1 < len(ps) else len(txt)]
        tm = (e['away'], e['home']) if flip else (e['home'], e['away'])
        side, legs, sel = None, [], ''
        row = dict(home=tm[0], away=tm[1])
        side = T._side_before(frag, row)
        for cand, nat in T._pick_lines(frag):
            legs = T.parse_legs(cand, side, tm, nat)
            if legs:
                sel = cand
                break
        r = _row(src, name, p['ts'], e, flip, legs, T.odds_in(sel), sel, uid)
        if r:
            out.append(r); have.add(uid)
    return out


def _single(src: str, name: str, p: dict, cache: dict, have: set) -> list[dict]:
    """Ставка с одной командой: единственное событие окна, где эта команда играет (слова ≥ 5 букв или псевдоним)."""
    sel = next((c for c, nat in T._pick_lines(p['text']) if nat), '')
    if not sel or T.SKIP.search(sel.lower()):
        return []
    ws = [w for w in T._words(sel) if len(w) >= 5 or T._alias(w)]
    if not ws:
        return []
    evs = _events_around(p['ts'], cache)
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
        return []
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
    if rt.get('tips_backfill'):
        return
    df = load()
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


def settle(log: list[str], max_days: int = 6) -> int:
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
            v = T.settle_legs(T._dec(r.legs), e['kind'], int(e['hg']) if ft else None, int(e['ag']) if ft else None)
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
            + '\n<i>(кф) средний кф · ✔️ обыгрывает рынок (≥50) · 🟢🟡🔴 ROI выше/≈/ниже 0</i>'
            + (f'\n<i>ROI: {roi}</i>' if roi else ''))
