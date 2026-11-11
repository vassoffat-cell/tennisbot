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
VERSION = 4                                         # версия правил разбора (смена → пересборка книги из архива)
PDIR = T.DIR / 'posts'
COLS = ['uid', 'src', 'post_id', 'post_ts', 'sport', 'a', 'b', 'pick', 'k', 'self', 'self_ts', 'raw', 'slip', 'stake', 'seen']
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


_AC: dict[str, dict] = {}


def _arch_cache(src: str) -> dict[int, dict]:
    if src not in _AC:
        _AC[src] = _arch(src)
    return _AC[src]


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
            if old is None or old['text'] != p['text'] or (p.get('photos') and not old.get('photos')):
                a[p['id']] = dict(id=p['id'], ts=str(p['ts'])[:16], text=p['text'], photos=p.get('photos', []),
                                  first=old['first'] if old else pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M'),
                                  **({'ocr': old['ocr']} if old and 'ocr' in old else {}))
                changed.append(dict(p, ocr=(old or {}).get('ocr', '')))
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
    if src in OCR_CH and p.get('ocr'):
        rows = slip_rows(src, p)
        if rows:
            return rows
    txt = (p['text'] + ('\n' + p['ocr'] if p.get('ocr') else '')).strip()
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
        import tipswide as W
        rows, srows, wide = [], [], []
        wdf = W.load()
        have_w = set(wdf.uid)
        for src in T.TG:
            for pid in sorted(_arch(src)):                           # по времени: открытый купон раньше рассчитанного
                x = _arch_cache(src)[pid]
                p = dict(id=x['id'], ts=pd.Timestamp(x['ts']), text=x['text'], ocr=x.get('ocr', ''))
                r = tips_of(src, p)
                rows += [t for t in r if not t.get('slip')]
                srows += [t for t in r if t.get('slip')]
                if src in OCR_CH:
                    wide += wide_rows(src, r, have_w)
        if wide:
            W.save(pd.concat([wdf, pd.DataFrame(wide)], ignore_index=True))
        old = {r.uid: (r.self, r.self_ts) for r in df.itertuples(index=False)}
        df, _ = merge_slips(pd.DataFrame(rows, columns=COLS), srows)
        for i, u in enumerate(df.uid):
            if u in old and old[u][0] and not df.at[i, 'self']:
                df.loc[i, ['self', 'self_ts']] = list(old[u])
        log.append(f'👥 книга: пересборка v{VERSION} — {len(df)} прогнозов')
    rt['tips_book_v'] = VERSION
    try:                                                           # 📷 Green Line и др.: прогноз на картинке → OCR
        df = ocr_round(rt, df, log)
    except Exception as e:
        log.append(f'👥 OCR: сбой {e.__class__.__name__}: {e}')
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
            ts_ = tips_of(src, p)
            sl = [t for t in ts_ if t.get('slip')]
            if sl:
                df, u_ = merge_slips(df, sl)
                upd += u_
                idx = {u: i for i, u in enumerate(df.uid)}
            for t in ts_:
                if t.get('slip'):
                    continue
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
    try:
        to_ours(df, log)
    except Exception as e:
        log.append(f'👥 купоны → наши матчи: сбой {e.__class__.__name__}: {e}')
    try:
        if first:                                                  # история не рассылается — только новое
            rt['tips_alerted'] = list(df.uid)[-600:]
        alert(rt, df, log)
    except Exception as e:
        log.append(f'👥 сигнал: сбой {e.__class__.__name__}: {e}')
    log.append(f"👥 книга ({'история 30 дн' if first else '3 дн'}): " + ', '.join(stat)
               + f'; отметок обновлено {upd}; всего {len(df)}; запросов {req}')


# ------------------------------------------------------------------ 📷 картинки постов (OCR)
OCR_CH = {'stavki_na_sport_betting1'}                  # каналы, где прогноз — на картинке (Green Line)
OCR_MAX = 60                                           # картинок за прогон (≈ 1–2 с каждая; история догоняется за несколько прогонов)


def _ocr_image(url: str) -> str:
    import io
    import urllib.request
    from PIL import Image, ImageOps
    import ocr
    req = urllib.request.Request(url, headers={'User-Agent': T.UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        img = Image.open(io.BytesIO(r.read())).convert('L')
    if img.width < 1000:
        img = img.resize((img.width * 2, img.height * 2))
    txt = ocr._tesseract(img, psm=6)
    if sum(c.isalpha() for c in txt) < 15:                     # светлый текст на тёмном фоне — инверсия
        txt = ocr._tesseract(ImageOps.invert(img), psm=6)
    return re.sub(r'[ \t]+', ' ', txt).strip()


def ocr_round(rt: dict, df: pd.DataFrame, log: list[str]) -> pd.DataFrame:
    """Картинки постов каналов OCR_CH без распознанного текста (30 суток): текст → архив → прогнозы (книга и все матчи)."""
    import shots
    import tipswide as W
    if not rt.get('ocr_photos_v1'):                                  # разово: история 30 дн заново — ссылки на картинки
        for src in OCR_CH:
            scan(src, 30, 30)
        rt['ocr_photos_v1'] = 1
    err = shots.ensure_ocr()
    if err:
        log.append(f'👥 OCR: {err}')
        return df
    edge = pd.Timestamp.now(tz='UTC').tz_localize(None) - pd.Timedelta(days=30)
    done, add, wide, budget = 0, [], [], OCR_MAX
    sample = T.DIR / 'img_sample'
    wdf = W.load()
    have_w = set(wdf.uid)
    have_b = set(df.uid)
    for src in OCR_CH:
        a = _arch(src)
        for pid in sorted(a, reverse=True):
            x = a[pid]
            if budget <= 0 or 'ocr' in x or not x.get('photos') or pd.Timestamp(x['ts']) < edge:
                continue
            parts = []
            for u in x['photos'][:3]:
                try:
                    parts.append(_ocr_image(u))
                    if not rt.get('ocr_samples') and len(list(sample.glob('*.jpg'))) < 6:   # образцы для настройки разбора
                        import urllib.request
                        sample.mkdir(parents=True, exist_ok=True)
                        req = urllib.request.Request(u, headers={'User-Agent': T.UA})
                        (sample / f'{src}_{pid}_{len(parts)}.jpg').write_bytes(urllib.request.urlopen(req, timeout=20).read())
                except Exception:
                    parts.append('')
                budget -= 1
            x['ocr'] = '\n§\n'.join(t for t in parts if t)
            done += 1
            p = dict(id=pid, ts=pd.Timestamp(x['ts']), text=x['text'], ocr=x['ocr'])
            new_rows = tips_of(src, p)
            df, n_upd = merge_slips(df, [t for t in new_rows if t.get('slip')])
            add += [t for t in new_rows if t['uid'] not in have_b and not t.get('slip')]
            have_b |= {t['uid'] for t in new_rows}
            wide += wide_rows(src, new_rows, have_w)
        _save_arch(src, a)
    if len(list(sample.glob('*.jpg'))) >= 6:
        rt['ocr_samples'] = 1
    if add:
        df = pd.concat([df, pd.DataFrame(add)], ignore_index=True)
    if wide:
        W.save(pd.concat([wdf, pd.DataFrame(wide)], ignore_index=True))
    left = sum(1 for src in OCR_CH for x in _arch(src).values()
               if x.get('photos') and 'ocr' not in x and pd.Timestamp(x['ts']) >= edge)
    log.append(f'👥 OCR: постов {done}, прогнозов +{len(add)}, футбол на табло +{len(wide)}, осталось {left}')
    return df


def _short(s: str, n: int) -> str:
    s = re.sub(r'\s*\((ж|w)\)', ' Ж', str(s)).strip()
    return s if len(s) <= n else s[:n - 1].rstrip() + '.'


def _pick_short(pick: str, a: str, b: str) -> str:
    """«авангард ИТБ(2.0)» → «ИТ2Б2», «барселона фора (0)» → «Ф2(0)», «победа ланс» → «П1»."""
    def side(team: str) -> str:
        t = team.lower().strip()[:4]
        return '1' if t and t in a.lower() else '2' if t and t in b.lower() else ''
    m = re.match(r'(.+?) ИТ([БМ])\(?([\d.]+)\)?', pick)
    if m and side(m.group(1)):
        return f"ИТ{side(m.group(1))}{m.group(2)}{float(m.group(3)):g}"
    m = re.match(r'(.+?) фора \(?([+-]?[\d.]+)\)?', pick)
    if m and side(m.group(1)):
        return f"Ф{side(m.group(1))}({float(m.group(2)):+g})"
    m = re.match(r'победа (.+)', pick)
    if m and side(m.group(1)):
        return f"П{side(m.group(1))}"
    return pick.replace('(', '').replace(')', '').replace('обе забьют да', 'ОЗ да')


ROUTE = {'tennis': 'tennis'}                          # вид спорта → бот; остальное — футбольный бот (решение пользователя 11.11)
ICON = {'football': '⚽', 'hockey': '🏒', 'tennis': '🎾', 'basketball': '🏀', 'volleyball': '🏐', 'esports': '🎮', 'other': '•'}


def alert(rt: dict, df: pd.DataFrame, log: list[str]) -> None:
    """👥 Новые прогнозы каперов (за 6 ч, ещё не присланные) → бот своего вида спорта: теннис — теннисный, остальное — футбольный.
    Купоны (Green Line) — только показанные до матча; таблица «канал → матч · ставка · кф» + ROI канала по табло."""
    import notify
    import rtable
    import tipswide as W
    now = pd.Timestamp.now(tz='UTC').tz_localize(None)
    sent = set(rt.get('tips_alerted', [])) | set(rt.get('gl_alerted', []))
    fresh = pd.to_datetime(df.post_ts, errors='coerce') >= now - pd.Timedelta(hours=6)
    d = df[fresh & ~df.uid.isin(sent) & ((df.slip == '') | ((df.seen == 'pre') & (df.self == '')))]
    ok = d.apply(lambda r: bool(r['a']) and bool(re.match(r'(П[12]|ИТ[12]?[БМ]|Т[БМ]|Ф[12]|ОЗ|1X|X2|12|ничья|победа)',
                                                         _pick_short(r['pick'], r['a'], r['b']).replace('Х', 'X')))
                 and not T.SKIP.search(str(r['pick']).lower()), axis=1) if len(d) else d.index == d.index
    d = d[ok]
    if d.empty:
        return
    st = W.stats()
    n = 0
    for bot in sorted({ROUTE.get(x, 'main') for x in d.sport}):
        g = d[d.sport.map(lambda x: ROUTE.get(x, 'main')) == bot]
        lines = []
        for src, h in g.groupby('src', sort=False):
            r0 = st[st.src == src] if len(st) else st
            roi = (f" ROI {'+' if r0.roi.iloc[0] > 0 else ''}{rtable.pct(r0.roi.iloc[0], 0)}%" if len(r0) and r0.nk.iloc[0] >= 10 else '')
            lines.append(f"{T.TG.get(src, src)}{roi}")
            for r in h.itertuples(index=False):
                m = f"{_short(r.a, 6)}–{_short(r.b, 6)}" if r.a else '—'
                lines.append(f"{ICON.get(r.sport, '•')}{m:<13} {_short(_pick_short(r.pick, r.a, r.b), 9):<9} {r.k or '':>4}".rstrip())
        notify.send_blocks(['👥 <b>Каперы · новые прогнозы</b>\n<pre>' + notify.esc('\n'.join(lines[:40])) + '</pre>'
                            '\n<i>ROI — по табло ESPN, их кф (если ≥ 10 ставок)</i>'], bot=bot)
        n += len(g)
    rt['tips_alerted'] = (list(sent) + list(d.uid))[-600:]
    rt.pop('gl_alerted', None)
    log.append(f'👥 сигнал каперов: {n}')


def slip_rows(src: str, p: dict) -> list[dict]:
    """Купоны 1WIN на картинках поста → строки книги (одна на событие купона)."""
    import slips as S
    out = []
    for part in str(p['ocr']).split('§'):
        c = S.parse(part)
        if not c:
            continue
        for i, e in enumerate(c['events']):
            key = f"slip:{c['id']}:{i}" if c['id'] else f"{p['id']}:s{i}"
            sp = e['sport'] or next((x for x in (sport_of(e['a'] + ' ' + e['b']), sport_of(part)) if x != 'other'), 'football')
            st = '' if c['status'] == 'open' else c['status']
            out.append(dict(uid=f'{src}:{key}', src=src, post_id=str(p['id']), post_ts=str(p['ts'])[:16], sport=sp, a=e['a'], b=e['b'],
                            pick=e['short'][:100], k=f"{c['k']:.2f}" if c['k'] else '', self=st,
                            self_ts=pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M') if st else '',
                            raw=f"{c['kind']} · {e['market']}"[:200], slip=c['id'] or '-', stake=f"{c['stake']:.0f}" if c['stake'] else '',
                            seen='pre' if c['status'] == 'open' else 'post'))
    return out


def merge_slips(df: pd.DataFrame, rows: list[dict]) -> tuple[pd.DataFrame, int]:
    """Купон с тем же ID: итог (W/L/P) дописывается к ранее показанному; показанный открытым — «pre» (до матча)."""
    df = df.reset_index(drop=True)
    idx = {u: i for i, u in enumerate(df.uid)}
    upd = 0
    for r in rows:
        i = idx.get(r['uid'])
        if i is None:
            idx[r['uid']] = len(df)
            df.loc[len(df)] = [r.get(c, '') for c in COLS]
            continue
        if r['self'] and df.at[i, 'self'] != r['self']:
            df.loc[i, ['self', 'self_ts']] = [r['self'], r['self_ts']]
            upd += 1
        if r['seen'] == 'pre' and df.at[i, 'seen'] != 'pre':
            df.loc[i, ['seen', 'post_id', 'post_ts']] = ['pre', r['post_id'], r['post_ts']]
        if r['pick'] and not re.search(r'ИТ|Т[БМ]|фора|победа|обе|ничья|1X|X2|12', df.at[i, 'pick']):
            df.at[i, 'pick'] = r['pick']
    return df, upd


def to_ours(df: pd.DataFrame, log: list[str]) -> int:
    """Купоны до матча (Green Line) на матчи наших турниров → tips.csv: строка 👥 под матчем с P модели, учёт против модели, вето."""
    now = pd.Timestamp.now(tz='UTC').tz_localize(None)
    fx = T.LAST_FIXTURES
    if not fx:
        return 0
    d = df[df.src.isin(OCR_CH) & (df.seen == 'pre') & (df.slip != '') & (df.a != '')
           & (pd.to_datetime(df.post_ts, errors='coerce') >= now - pd.Timedelta(hours=48))]
    tdf = T.load()
    have = set(tdf.uid)
    new = []
    for r in d.itertuples(index=False):
        for f in fx:
            m, row = f
            if len(T.find_teams(f'{r.a} — {r.b}', [row['home'], row['away']])) != 2:
                continue
            uid = f'tg:{r.src}:{r.uid.split(":", 1)[1]}:{m.id}'
            if uid in have:
                break
            legs = T.parse_legs(r.pick, None, (row['home'], row['away']), True)
            k = float(r.k) if r.k else None
            x = T._row(r.src, T.TG.get(r.src, r.src), r.post_ts, f, legs, k, f'📷 {r.pick}', uid)
            if x:
                new.append(x); have.add(uid)
            break
    if new:
        T.save(pd.concat([tdf, pd.DataFrame(new)], ignore_index=True))
    log.append(f'👥 купоны → наши матчи: +{len(new)}')
    return len(new)


def wide_rows(src: str, rows: list[dict], have_w: set) -> list[dict]:
    """Купоны, показанные ДО матча (открытые), → сверка с табло ESPN (tipswide); один купон — одна строка (uid по ID)."""
    import tipswide as W
    out = []
    for r in rows:
        if not r.get('slip') or r['self'] or not (r['a'] and r['b']):
            continue                                              # постфактум (уже рассчитан) — не в счёт
        sid = r['slip'] if r['slip'] != '-' else r['uid'].split(':')[-2] + r['uid'].split(':')[-1]
        p = dict(id=f"s{sid}{r['uid'].split(':')[-1]}", ts=pd.Timestamp(r['post_ts']),
                 text=f"{r['a']} — {r['b']}\nСтавка: {r['pick']}" + (f" кф. {r['k']}" if r['k'] else ''))
        out += W.from_post(src, T.TG.get(src, src), p, {}, have_w)
    return out


# ------------------------------------------------------------------ учёт
def _key(s: str) -> str:
    """Ключ строки ставки для сверки книги с итогами tipswide: только буквы и цифры, первые 20."""
    return re.sub(r'[^0-9a-zа-яё]', '', str(s).lower())[:20]


def stats() -> pd.DataFrame:
    """Канал × вид: прогнозов, с отметкой, угадано по отметкам; честность — совпадение отметок с нашим итогом (футбол)."""
    import tipswide as W
    d = load()
    if d.empty:
        return pd.DataFrame()
    w = W.load()
    w = w[w.result.isin(['W', 'L', 'P']) & w.uid.str.startswith('tg:')]
    fact = {(r.uid.split(':')[1], r.uid.split(':')[2], _key(r.raw)): r.result for r in w.itertuples(index=False)}
    rows = []
    d = d[~d.src.isin(OCR_CH)]                                     # купоны (Green Line) — отдельная честная таблица «до матча»
    for (src, sp), g in d.groupby(['src', 'sport']):
        m = g[g.self.isin(['W', 'L'])]
        chk = [(r.self, fact[(src, r.post_id, _key(r.pick))]) for r in m.itertuples(index=False)
               if (src, r.post_id, _key(r.pick)) in fact]
        rows.append(dict(src=src, sport=sp, name=T.TG.get(src, src), n=len(g), nm=len(m), won=int((m.self == 'W').sum()),
                         chk=len(chk), ok=sum(a == b for a, b in chk)))
    return pd.DataFrame(rows).sort_values(['n'], ascending=False)


def slip_stats() -> pd.DataFrame:
    """Купоны каналов OCR_CH: показано до матча (pre) — итог по их же ID (W/L/P), без итога; постфактум — отдельно."""
    d = load()
    d = d[d.src.isin(OCR_CH) & (d.slip != '')]
    rows = []
    for (src, sp), g in d.groupby(['src', 'sport']):
        pre = g[g.seen == 'pre']
        rows.append(dict(src=src, sport=sp, name=T.TG.get(src, src), pre=len(pre), w=int((pre.self == 'W').sum()),
                         l=int((pre.self == 'L').sum()), none=int((pre.self == '').sum()), post=int((g.seen == 'post').sum()),
                         post_w=int(((g.seen == 'post') & (g.self == 'W')).sum())))
    return pd.DataFrame(rows)


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
    out = ('👥 <b>Каперы · все виды (самоотчёт)</b>\n' + rtable.render(rows, ('Канал·вид (всего)', 'Кол-во', '%'))
           + '\n<i>угадано / с их отметкой ✅❌; (всего) — прогнозов</i>' + (f'\n<i>Честность отметок: {hon}</i>' if hon else ''))
    ss = slip_stats()
    if len(ss):                                                   # 📷 купоны: только показанные до матча
        r2 = [rtable.row(f"{r.name[:8]}·{SPORT_RU.get(r.sport, r.sport)[:4]}", r.w, r.pre, str(r.none))
              for r in ss.itertuples(index=False) if r.pre]
        post = int(ss.post.sum())
        out += ('\n📷 <b>Купоны до матча</b>\n' + rtable.render(r2, ('Канал·вид (б/и)', 'W/до', '%'))
                + f'\n<i>W — показан выигранным; (б/и) без итога — вероятно проигрыш; постфактум {post} — не в счёт</i>')
    return out
