"""👥 Книга прогнозов каперов — ВСЕ виды спорта (решение пользователя 11.11.2026: «слежение всех их прогнозов,
рано или поздно переедем на другие чемпионаты и виды»).

Что делает каждый прогон (раз в 2 ч):
  1. Архив постов каналов (data/tips/posts/<канал>.jsonl): каждый пост с текстом; посты последних 3 суток перечитываются —
     каналы правят посты после матча (✅ / ❌ / возврат), правка сохраняется.
  2. Прогнозы из постов (data/tips/book.csv): вид спорта (⚽ 🏒 🎾 🏀 🏐 киберспорт по эмодзи и словам), пара «A — B»,
     строка ставки как написана, кф, отметка самого канала (✅ W · ❌ L · ↩️ P) — по виду спорта и каналу.
  3. Проверка честности: где итог считаем сами (футбол — табло ESPN, tipswide), сверяем отметку канала с фактом.
Учёт по виду спорта: самоотчёт (угадано по отметкам), подтверждено нами, честность (совпадение отметок с фактом).
Новые виды спорта: прогнозы уже копятся — для проверки достаточно добавить источник итогов (табло ESPN вида).
"""
from __future__ import annotations

import json
import re
import time

import pandas as pd

import tips as T

BOOK = T.DIR / 'book.csv'
VERSION = 2                                         # версия правил разбора (смена → пересборка книги из архива)
PDIR = T.DIR / 'posts'
COLS = ['uid', 'src', 'post_id', 'post_ts', 'sport', 'a', 'b', 'pick', 'k', 'self', 'self_ts', 'raw']
SPORT_RU = {'football': 'футбол', 'hockey': 'хоккей', 'tennis': 'теннис', 'basketball': 'баскет', 'volleyball': 'волейб',
            'esports': 'кибер', 'other': 'другое'}
SPORTS = [
    ('hockey', r'🏒|хокке|\bкхл\b|\bнхл\b|\bвхл\b|\bмхл\b|шайб|\bnhl\b|\bkhl\b|авангард|ак барс|салават|барыс|трактор|северсталь|'
               r'\bамур\b|адмирал|куньлунь|драконы|нефтехимик|автомобилист'),
    ('tennis', r'🎾|теннис|\batp\b|\bwta\b|\bitf\b|челленджер|\bгейм|\bсет[ауов]?\b|тай-?брейк'),
    ('basketball', r'🏀|баскет|\bnba\b|\bнба\b|евролиг|единая лига'),
    ('volleyball', r'🏐|волейб|партии\b'),
    ('esports', r'кибер|\bcs ?2\b|\bcs:go\b|\bdota\b|дота|valorant|\blol\b|карт[аеуы]\b'),
    ('football', r'⚽|футбол|\bгол|\bапл\b|ла лиг|серия a|бундеслиг|лига чемпионов|угловы|обе забьют|\bрпл\b|\bфнл\b'),
]
DEFAULT = {'stavki': 'football', 'xs_stavkii': 'football', 'groznyi_ruslan': 'football'}   # профиль канала, если вид не назван
PICK = re.compile(r'(?:ставк\w*|прогноз\w*(?: на матч)?|бер[её]м|играем|вариант)\s*[:\-—]\s*(.{2,140})', re.I)
MARKET = re.compile(r'шайб|минимум|забь[её]т|победит|выиграет|\bт[бм]\b|\bт[бм]\s*\(|\bи?т[12]?\s*[бм]|\bф[12]?\s*\(|\bп[12]\b|\b(1x|x2|2x|1х|х2|2х)\b|обе забьют|тотал|фора|победа',
                    re.I)


def sport_of(text: str) -> str:
    low = text.lower()
    for sp, rx in SPORTS:
        if re.search(rx, low):
            return sp
    return 'other'


def self_mark(s: str) -> str:
    """Отметка канала в строке ставки: ✅ — W, ❌/✖️ — L, ↩️/возврат — P."""
    if re.search(r'↩|🔄|возврат', s, re.I):
        return 'P'
    if '❌' in s or '✖' in s or '⛔' in s:
        return 'L'
    if '✅' in s or '✔' in s:
        return 'W'
    return ''


# ------------------------------------------------------------------ архив постов
def _arch(src: str) -> dict[int, dict]:
    f = PDIR / f'{src}.jsonl'
    out = {}
    if f.exists():
        for ln in f.read_text(encoding='utf-8').splitlines():
            if ln.strip():
                x = json.loads(ln)
                out[int(x['id'])] = x
    return out


def _save_arch(src: str, a: dict[int, dict]) -> None:
    PDIR.mkdir(parents=True, exist_ok=True)
    (PDIR / f'{src}.jsonl').write_text('\n'.join(json.dumps(a[k], ensure_ascii=False) for k in sorted(a)), encoding='utf-8')


def scan(src: str, days: float, pages: int) -> tuple[list[dict], int]:
    """Посты канала за days суток (страницы веб-ленты): архив + список изменённых/новых. Возвращает (посты, запросов)."""
    a = _arch(src)
    edge = pd.Timestamp.now(tz='UTC').tz_localize(None) - pd.Timedelta(days=days)
    before, changed, req = None, [], 0
    for _ in range(pages):
        posts = T.tg_posts(src, before)
        req += 1
        if not posts:
            break
        for p in posts:
            old = a.get(p['id'])
            if old is None or old['text'] != p['text']:
                a[p['id']] = dict(id=p['id'], ts=str(p['ts'])[:16], text=p['text'],
                                  first=old['first'] if old else pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M'))
                changed.append(p)
        before = min(p['id'] for p in posts)
        if min(p['ts'] for p in posts) < edge:
            break
        time.sleep(1.0)
    _save_arch(src, a)
    return changed, req


# ------------------------------------------------------------------ прогнозы из поста
def tips_of(src: str, p: dict) -> list[dict]:
    """Все прогнозы поста: по парам «A — B» (фрагмент до следующей пары) или одна ставка без пары."""
    import tipswide as W
    txt = p['text']
    sport = sport_of(txt)
    ps = W.pairs(txt)
    out = []
    segs = [(a, b, txt[pos:ps[n + 1][0] if n + 1 < len(ps) else len(txt)]) for n, (pos, a, b) in enumerate(ps)] or [('', '', txt)]
    for n, (a, b, frag) in enumerate(segs):
        pick = ''
        for ln in frag.split('\n'):
            m = PICK.search(ln)
            if m and MARKET.search(m.group(1)):
                pick = m.group(1).strip()
                break
        if not pick:
            for ln in frag.split('\n'):
                if MARKET.search(ln) and not W.PAIR.search(ln) and len(ln) < 140:
                    pick = ln.strip()
                    break
        if not pick:
            continue
        mk = self_mark(pick)                                   # только в строке ставки: «✅ Прогноз:» у части каналов — маркер списка
        out.append(dict(uid=f"{src}:{p['id']}:{n}", src=src, post_id=str(p['id']), post_ts=str(p['ts'])[:16],
                        sport=next((x for x in (sport_of(frag), sport, DEFAULT.get(src, 'other')) if x != 'other'), 'other'), a=a, b=b,
                        pick=re.sub(r'[✅❌✖️⛔↩🔄✔]+', '', pick).strip()[:100], k=f'{T.odds_in(pick):.2f}' if T.odds_in(pick) else '',
                        self=mk, self_ts=pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M') if mk else '', raw=frag[:200].replace('\n', ' ')))
    return out


def load() -> pd.DataFrame:
    if BOOK.exists():
        return pd.read_csv(BOOK, dtype=str, keep_default_na=False).reindex(columns=COLS, fill_value='')
    return pd.DataFrame(columns=COLS)


def run(rt: dict, log: list[str]) -> None:
    """Прогон: архив и прогнозы всех каналов. Первый раз — история 30 суток, далее — последние 3 суток (правки ✅/❌)."""
    first = not rt.get('tips_book')
    days, pages = (30, 30) if first else (3, 4)
    df = load()
    if rt.get('tips_book_v') != VERSION and not df.empty:          # правила разбора изменились — пересборка из архива
        rows = []
        for src in T.TG:
            for x in _arch(src).values():
                rows += tips_of(src, dict(id=x['id'], ts=pd.Timestamp(x['ts']), text=x['text']))
        old = {r.uid: (r.self, r.self_ts) for r in df.itertuples(index=False)}
        df = pd.DataFrame(rows, columns=COLS)
        for i, u in enumerate(df.uid):
            if u in old and old[u][0] and not df.at[i, 'self']:
                df.loc[i, ['self', 'self_ts']] = list(old[u])
        log.append(f'👥 книга: пересборка v{VERSION} — {len(df)} прогнозов')
    rt['tips_book_v'] = VERSION
    idx = {u: i for i, u in enumerate(df.uid)}
    add, upd, stat, req = [], 0, [], 0
    for src, name in T.TG.items():
        try:
            changed, r = scan(src, days, pages)
        except Exception as e:
            stat.append(f'{name} ✖ {e.__class__.__name__}')
            continue
        req += r
        n0 = len(add)
        for p in changed:
            for t in tips_of(src, p):
                i = idx.get(t['uid'])
                if i is None:
                    idx[t['uid']] = -1
                    add.append(t)
                elif t['self'] and df.at[i, 'self'] != t['self']:
                    df.loc[i, ['self', 'self_ts']] = [t['self'], t['self_ts']]
                    upd += 1
        stat.append(f'{name} +{len(add) - n0}')
    if add:
        df = pd.concat([df, pd.DataFrame(add)], ignore_index=True)
    T.DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(BOOK, index=False)
    rt['tips_book'] = pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M')
    log.append(f"👥 книга ({'история 30 дн' if first else '3 дн'}): " + ', '.join(stat)
               + f'; отметок обновлено {upd}; всего {len(df)}; запросов {req}')


# ------------------------------------------------------------------ учёт
def stats() -> pd.DataFrame:
    """Канал × вид: прогнозов, с отметкой, угадано по отметкам; честность — совпадение отметок с нашим итогом (футбол)."""
    import tipswide as W
    d = load()
    if d.empty:
        return pd.DataFrame()
    w = W.load()
    w = w[w.result.isin(['W', 'L', 'P']) & w.uid.str.startswith('tg:')]
    fact = {(r.uid.split(':')[1], r.uid.split(':')[2]): r.result for r in w.itertuples(index=False)}
    rows = []
    for (src, sp), g in d.groupby(['src', 'sport']):
        m = g[g.self.isin(['W', 'L'])]
        chk = [(r.self, fact[(src, r.post_id)]) for r in m.itertuples(index=False) if (src, r.post_id) in fact]
        rows.append(dict(src=src, sport=sp, name=T.TG.get(src, src), n=len(g), nm=len(m), won=int((m.self == 'W').sum()),
                         chk=len(chk), ok=sum(a == b for a, b in chk)))
    return pd.DataFrame(rows).sort_values(['n'], ascending=False)


def report_block() -> str:
    """«👥 Все виды»: канал·вид | угадано/с отметкой | % ; честность — строкой."""
    import rtable
    st = stats()
    if st.empty:
        return '👥 <b>Каперы · все виды</b>: прогнозов пока нет'
    rows = [rtable.row(f"{r.name[:9]}·{SPORT_RU.get(r.sport, r.sport)[:6]}", r.won, r.nm, str(r.n)) for r in st.itertuples(index=False)
            if r.n >= 3]
    hon = ' · '.join(f"{r.name[:9]} {r.ok}/{r.chk}" for r in st.groupby('name', as_index=False)[['chk', 'ok']].sum().itertuples(index=False)
                     if r.chk)
    return ('👥 <b>Каперы · все виды (самоотчёт)</b>\n' + rtable.render(rows, ('Канал·вид (всего)', 'Кол-во', '%'))
            + '\n<i>угадано / с их отметкой ✅❌; (всего) — прогнозов</i>' + (f'\n<i>Честность отметок: {hon}</i>' if hon else ''))
