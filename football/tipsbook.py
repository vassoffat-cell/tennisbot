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
VERSION = 12                                        # версия правил разбора (смена → пересборка книги из архива)
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
AD_TAIL = re.compile(r'\s*(?:Ставлю|Ставим|Промокод|Связь со мной|ПОДПИСАТЬСЯ|https?://).*$', re.I | re.S)   # 14.11: рекламный хвост поста
ODDS_TAIL = re.compile(r'\s*[-–—]\s*(?:кф|коэф\w*|да|нет)\b.*$', re.I)   # 15.11: «… (3.5) - Да 1.75» — не пара команд
MARKET = re.compile(r'шайб|минимум|забь[её]т|забьют|обоих тайм|оба тайма|не проиграет|обе команды забьют|победит|выиграет|\bт[бм]\b|\bт[бм]\s*\(|\bи?т[12]?\s*[бм]|\bф[12]?\s*\(|\bп[12]\b|\b(1x|x2|2x|1х|х2|2х)\b|обе забьют|тотал|фора|победа',
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
RESULT_LN = re.compile(r'(?i)пробил|зашл|зашёл|зашел|прош[её]л|прошла|забрал|мимо|сыграл[аи]?\b')   # 16.11: строка-итог («пробила наш тотал») — не ставка


def _real_pair(s: str) -> bool:
    """Пара команд «A — B», где ни одна сторона не слово рынка («Победа - Португалия» — ставка, не пара)."""
    import tipswide as W
    return any(not MARKET.search(m.group(1)) and not MARKET.search(m.group(2)) for m in W.PAIR.finditer(s))


def tips_of(src: str, p: dict) -> list[dict]:
    """Все прогнозы поста: по парам «A — B» (фрагмент до следующей пары) или одна ставка без пары."""
    import tipswide as W
    if src in OCR_CH and p.get('ocr'):
        rows = slip_rows(src, p)
        if rows:
            return rows
    txt = (p['text'] + ('\n' + p['ocr'] if p.get('ocr') else '')).strip()
    sport = sport_of(txt)
    # 15.11: номер сегмента = номер пары в W.pairs (uid прежних прогнозов не меняется);
    # пара из прозы («в пяти матчах - ожидать …») — не пара команд (названия команд с заглавной буквы)
    # 16.11 аудит: «Победа - Португалия» — не пара команд (сторона пары — слово рынка)
    ps = [(n, x) for n, x in enumerate(W.pairs(txt)) if x[1][:1].isupper() and x[2][:1].isupper()
          and not MARKET.search(x[1]) and not MARKET.search(x[2]) and len(x[1].split()) <= 4 and len(x[2].split()) <= 4]
    out = []
    segs = [(n, a, b, txt[pos:ps[i + 1][1][0] if i + 1 < len(ps) else len(txt)]) for i, (n, (pos, a, b)) in enumerate(ps)] or [(0, '', '', txt)]
    if ps and ps[0][1][0] > 0:                                 # 15.11: прогноз ДО первой пары («✅ Прогноз: 1Х+ТМ(3.5)», потом разбор)
        segs = [('h', '', '', txt[:ps[0][1][0]])] + segs
    # 15.11: «✅ПРОГНОЗ НА МАТЧ:» и ставка на СЛЕДУЮЩЕЙ строке («Обе команды забьют - Да») — склеить
    segs = [(n, a, b, re.sub(r'(?i)((?:ставк\w*|прогноз\w*(?: на матч)?)\s*:)\s*\n\s*', r'\1 ', f)) for n, a, b, f in segs]
    for n, a, b, frag in segs:
        pick = ''
        for ln in frag.split('\n'):
            m = PICK.search(ln)
            if m and MARKET.search(m.group(1)):
                pick = m.group(1).strip()
                break
        if not pick:
            # 16.11 аудит (❔ недели): пост одной строкой без переносов («🔥 Обе забьют - ДА  Ставлю тут: 1WIN…») —
            # строки режутся и по двойному пробелу, рекламный хвост срезается ДО проверки длины; своя пара сегмента не мешает
            # («Косово — Израиль 🟨 Жёлтые карточки ТБ 2.5 — кф. 1.53»)
            own = re.compile(re.escape(a) + r'\s*[-–—]\s*' + re.escape(b)) if a and b else None
            for ln in re.split(r'\n|\s{2,}', frag):
                ln = AD_TAIL.sub('', ln).strip()
                chk = ODDS_TAIL.sub('', own.sub('', ln, count=1) if own else ln)
                # 14.11: «ТБ 2.5 — кф. 1.53» — тире перед кф не пара команд (Хитрости, Косово — Израиль)
                if not MARKET.search(ln) or len(ln) >= 140 or RESULT_LN.search(ln):
                    continue
                if not _real_pair(chk):
                    pick = ln
                    break
                ms = [x for x in W.PAIR.finditer(chk) if not MARKET.search(x.group(1)) and not MARKET.search(x.group(2))]
                m = ms[-1] if ms else None               # 16.11 (❔ → 0): «Франция — Лига 1 ⚽️ Ренн — Осер 🎯 ТБ 7.5» — ставка после ПОСЛЕДНЕЙ пары
                tail = chk[m.end():] if m else ''        # 16.11: «⚽️ Косово — Израиль 🟨 Жёлтые карточки ТБ 2.5» — пара, затем ставка
                if not own and MARKET.search(tail) and not _real_pair(tail):
                    pick = ln
                    break
        if not pick:
            continue
        if src in OCR_IMG:                                     # 15.11: OCR путает «1» с «Л», «l», «|», «I» («П1» → «ПЛ»)
            pick = re.sub(r'\bП[ЛlI|]\b', 'П1', pick)
        if src == 'kapper_uk' and not T.odds_in(pick):        # 14.11: картинка Каппер UK — «Победа в матче. П2 1.675» (кф в конце, 2–3 знака)
            m = re.search(r'\s(\d\.\d{2,3})\s*$', pick)
            if m and 1.01 < float(m.group(1)) < 30:
                pick = f"{pick[:m.start()].strip()} кф {m.group(1)}"
        mk = self_mark(pick)                                   # только в строке ставки: «✅ Прогноз:» у части каналов — маркер списка
        out.append(dict(uid=f"{src}:{p['id']}:{n}", src=src, post_id=str(p['id']), post_ts=str(p['ts'])[:16],
                        sport=next((x for x in (sport_of(frag), sport, DEFAULT.get(src, 'other')) if x != 'other'), 'other'), a=a, b=b,
                        pick=AD_TAIL.sub('', re.sub(r'[✅❌✖️⛔↩🔄✔]+', '', pick)).strip()[:100], k=f'{T.odds_in(pick):.2f}' if T.odds_in(pick) else '',
                        self=mk, self_ts=pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M') if mk else '', raw=frag[:200].replace('\n', ' ')))
    if not out and _candidate(p):                               # 15.11 «страховочная сеть» (Г0): не разобрали — НЕ теряем
        body = re.sub(r'\s+', ' ', AD_TAIL.sub('', p['text'])).strip()
        bet = [ln.strip() for ln in str(p.get('ocr', '')).split('\n')            # ставка с картинки (строки рынка, без «Ставка: 2000 Р»)
               if MARKET.search(ln) and not re.search(r'(?i)ставка\s*:|выигрыш|статус|коэффициент', ln)][:2]
        if bet:
            body = ' · '.join(bet) + ' — ' + body
        a0, b0 = next(((x[1], x[2]) for x in W.pairs(p['text']) if x[1][:1].isupper() and x[2][:1].isupper()), ('', ''))
        out.append(dict(uid=f"{src}:{p['id']}:r", src=src, post_id=str(p['id']), post_ts=str(p['ts'])[:16],
                        sport=next((x for x in (sport, DEFAULT.get(src, 'other')) if x != 'other'), 'other'), a=a0, b=b0,
                        pick=('❔ ' + body)[:240], k=f'{T.odds_in(body):.2f}' if T.odds_in(body) else '', self='', self_ts='',
                        raw=body[:200]))
    return out


# 15.11 «страховочная сеть»: пост капера, который разбор не понял, но он похож на прогноз → в книгу строкой «❔» и пользователю
# (текст поста + ссылка). Не прогноз: реклама, итоги, отзывы, новости, анонсы «🔜», платные «послеоплатники».
CAND_POS = re.compile(r'(?i)прогноз|ставк[аиу]\b|ставлю|ставим|экспресс|\bкф\b|коэф|\bп\s?[12]\b|\bт[бм]\b|\bит\s?[12]?\s?[бм]|фора|тотал'
                      r'|победа|победит|забь|обе команды|\b1[хx]\b|\b[хx]2\b|минимум|ординар|паровоз|карточ|углов|шайб|не проиграет')
CAND_NEG = re.compile(r'(?i)отзывы|бонус|регистрир|приложени|ден[её]жный|заход|зашел|зашёл|зашла|прош[её]л|прошла|не прош|проход|возврат'
                      r'|статистика с|итоги|видео-?отч|поздравля|доброе утро|расписание|замен|в личку|послеоплат|стоимость|гарантии'
                      r'|мимо по ставкам|забрали одну|забрали ставк|подпиш|согласны|легальн|🔜|кнопк|на связи|прошли|реакци|позор|горит|отработали|цена\s*\d|завершение дня|вчера забрал'
                      r'|ставка\s+в\s*\W*\s*\d+\s*млн|хотите разобрать|на подходе|где сегодня искать')   # 16.11: анонсы-тизеры Green Line, вопросы, «экспресс на подходе»


def _candidate(p: dict) -> bool:
    t = AD_TAIL.sub('', str(p.get('text', ''))).strip()
    if not t and not p.get('photos'):
        return False
    if CAND_NEG.search(t):
        return False
    return bool(CAND_POS.search(t)) or (bool(p.get('photos')) and len(t) < 80 and bool(re.search(r'(?i)экспресс|ставк|прогноз|🔥', t)))


def _rows(add: list[dict]) -> pd.DataFrame:
    """Новые строки книги: все колонки, пустое = '' (14.11: NaN в slip/seen скрывал свежий прогноз от alert до следующего прогона)."""
    return pd.DataFrame(add, columns=COLS).fillna('').astype(str)


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
                p = dict(id=x['id'], ts=pd.Timestamp(x['ts']), text=x['text'], ocr=x.get('ocr', ''), photos=x.get('photos', []))
                r = tips_of(src, p)
                rows += [t for t in r if not t.get('slip')]
                srows += [t for t in r if t.get('slip')]
                if src in OCR_CH:
                    wide += wide_rows(src, r, have_w)
        if wide:
            W.save(pd.concat([wdf, pd.DataFrame(wide)], ignore_index=True))
        old = {r.uid: (r.self, r.self_ts) for r in df.itertuples(index=False)}
        df, _ = merge_slips(_rows(rows), srows)
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
        df = pd.concat([df, _rows(add)], ignore_index=True)
    try:                                                           # 📷 Green Line, Каппер UK: прогноз на картинке → OCR
        df = ocr_round(rt, df, log)                                # 14.11: ПОСЛЕ чтения каналов — новый пост распознаётся в том же прогоне
    except Exception as e:
        log.append(f'👥 OCR: сбой {e.__class__.__name__}: {e}')
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
        if not rt.get('tips_test_1111'):                          # разовый тест доставки (просьба пользователя 11.11)
            alert(rt, df, log, test=True)
            rt['tips_test_1111'] = 1
    except Exception as e:
        log.append(f'👥 сигнал: сбой {e.__class__.__name__}: {e}')
    log.append(f"👥 книга ({'история 30 дн' if first else '3 дн'}): " + ', '.join(stat)
               + f'; отметок обновлено {upd}; всего {len(df)}; запросов {req}')


# ------------------------------------------------------------------ 📷 картинки постов (OCR)
OCR_CH = {'stavki_na_sport_betting1'}                  # каналы, где прогноз — на картинке (Green Line)
OCR_IMG = OCR_CH | {'kapper_uk'}                       # 14.11: OCR картинок — и Каппер UK (ставка только на картинке под разбором матча)
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
    edge = pd.Timestamp.now(tz='UTC').tz_localize(None) - pd.Timedelta(days=30)
    if not any(x.get('photos') and 'ocr' not in x and pd.Timestamp(x['ts']) >= edge
               for src in OCR_IMG for x in _arch(src).values()):
        return df                                                    # 14.11: нечего распознавать — Tesseract не ставим (−15…20 с)
    err = shots.ensure_ocr()
    if err:
        log.append(f'👥 OCR: {err}')
        return df
    done, add, wide, budget = 0, [], [], OCR_MAX
    sample = T.DIR / 'img_sample'
    wdf = W.load()
    have_w = set(wdf.uid)
    have_b = set(df.uid)
    for src in sorted(OCR_IMG):
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
            p = dict(id=pid, ts=pd.Timestamp(x['ts']), text=x['text'], ocr=x['ocr'], photos=x.get('photos', []))
            new_rows = tips_of(src, p)
            df, n_upd = merge_slips(df, [t for t in new_rows if t.get('slip')])
            add += [t for t in new_rows if t['uid'] not in have_b and not t.get('slip')]
            have_b |= {t['uid'] for t in new_rows}
            if src in OCR_CH:
                wide += wide_rows(src, new_rows, have_w)
        _save_arch(src, a)
    if len(list(sample.glob('*.jpg'))) >= 6:
        rt['ocr_samples'] = 1
    if add:
        df = pd.concat([df, _rows(add)], ignore_index=True)
    if wide:
        W.save(pd.concat([wdf, pd.DataFrame(wide)], ignore_index=True))
    left = sum(1 for src in OCR_IMG for x in _arch(src).values()
               if x.get('photos') and 'ocr' not in x and pd.Timestamp(x['ts']) >= edge)
    log.append(f'👥 OCR: постов {done}, прогнозов +{len(add)}, футбол на табло +{len(wide)}, осталось {left}')
    return df


def _short(s: str, n: int) -> str:
    s = re.sub(r'\s*\((ж|w)\)', ' Ж', str(s)).strip()
    return s if len(s) <= n else s[:n - 1].rstrip() + '.'


def _context(raw: str, pick: str) -> str:
    """Пост без распознанной пары команд: короткий контекст из текста (турнир / команды до слова «прогноз»), без рекламы."""
    s = re.split(r'Ставлю|Ставим|Промокод|Связь со мной|ПОДПИСАТЬСЯ|https?://', str(raw))[0]
    s = re.sub(r'[🔴➡️👍🎫📢✅☑️🏆🇪🇺⚽🏒🎾]+', ' ', s)
    s = re.sub(r'(?i)прогноз( на матч)?\s*:?', ' ', s)
    s = s.replace(str(pick), ' ')
    s = re.sub(r'\s+', ' ', s).strip(' :—-·,.')
    return (s[:60] + '…') if len(s) > 60 else (s or 'матч не указан')


def _nb_new(sent: set, now) -> pd.DataFrame:
    """Новые прогнозы ленты nb-bet (tips_all.csv) за 6 ч, ещё не присланные."""
    try:
        w = pd.read_csv(T.DIR / 'tips_all.csv', dtype=str, keep_default_na=False)
    except Exception:
        return pd.DataFrame()
    w = w[(w.src == 'nb-bet') & ~w.uid.isin(sent)]
    return w[pd.to_datetime(w.post_ts, errors='coerce') >= now - pd.Timedelta(hours=6)].head(25)


def _pick_short(pick: str, a: str, b: str, sport: str = '') -> str:
    """«авангард ИТБ(2.0)» → «ИТ2Б2», «барселона фора (0)» → «Ф2(0)», «победа ланс» → «П1».
    15.11: формы Каппер UK — «Победа в матче. П1 кф 1.67» → «П1 с ОТ» (хоккей) / «П1»; «Обе забьют. Да» → «ОЗ да»;
    «Индивидуальный тотал 2-го. (1) Б» → «ИТ2Б1»; «Тотал. (2) Б» → «ТБ2»; «Фора. 1 (-1.5)» → «Ф1(-1.5)». Кф — только в @."""
    pick = re.sub(r'\s*кф\.?\s*\d+[.,]\d+\s*$', '', str(pick)).strip()
    pick = re.sub(r'(?i)^первый,?\s*результат\s*\+\s*тотал\.?\s*', '', pick)          # «1X и ТМ (3.5) - Да»
    pick = re.sub(r'(?i)\s*[-–—]\s*да\s*$', '', pick).replace('иТМ', 'и ТМ').replace('иТБ', 'и ТБ')
    m = re.match(r'(?i)победа в матче\.?\s*П\s*([12])\b', pick)
    if m:
        return f"П{m.group(1)}" + (' с ОТ' if sport == 'hockey' else '')
    m = re.match(r'(?i)обе (?:команды )?забьют\.?\s*[-–—]?\s*(да|нет)', pick)
    if m:
        return f"ОЗ {m.group(1).lower()}"
    m = re.match(r'(?i)индивидуальный тотал\s*([12])-го\.?\s*\(\s*([\d.]+)\s*\)\s*([БМ])', pick)
    if m:
        return f"ИТ{m.group(1)}{m.group(3).upper()}{float(m.group(2)):g}" + pick[m.end():].rstrip()
    m = re.match(r'(?i)тотал\.?\s*\(\s*([\d.]+)\s*\)\s*([БМ])', pick)
    if m:
        return f"Т{m.group(2).upper()}{float(m.group(1)):g}" + pick[m.end():].rstrip()
    m = re.match(r'(?i)фора\.?\s*([12])\s*\(\s*([+-]?[\d.]+)\s*\)', pick)
    if m:
        return f"Ф{m.group(1)}({float(m.group(2)):+g})" + pick[m.end():].rstrip()
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


ROUTE = {'tennis': 'tennis', 'hockey': 'tennis'}     # хоккей — в теннисный бот (решение 11.11)                          # вид спорта → бот; остальное — футбольный бот (решение пользователя 11.11)
ICON = {'football': '⚽', 'hockey': '🏒', 'tennis': '🎾', 'basketball': '🏀', 'volleyball': '🏐', 'esports': '🎮', 'other': '•'}


def _relations() -> dict:
    """uid прогноза → строка «согласна ли система» (14.11): футбол — tips.csv (наши матчи), хоккей — hockey/data/tips.csv."""
    out = {}
    try:
        t = T.load()
        for r in t.itertuples(index=False):
            p = r.p_model
            try:
                p = float(p)
            except (TypeError, ValueError):
                continue
            out[r.uid] = (f'🔥 система согласна (P {p * 100:.0f}%)' if p >= 0.55 else f'⚔️ система против (P {p * 100:.0f}%)' if p <= 0.45
                          else f'· система нейтральна (P {p * 100:.0f}%)')
    except Exception:
        pass
    try:
        from pathlib import Path
        h = pd.read_csv(Path(__file__).resolve().parent.parent / 'hockey' / 'data' / 'tips.csv', dtype=str, keep_default_na=False)
        for r in h.itertuples(index=False):
            if str(r.rel) not in ('1', '-1', '0'):               # 15.11: нет оценки (после начала / мало свежих данных) — не «нейтральна»
                continue
            try:
                p = float(r.p_model)
            except (TypeError, ValueError):
                continue
            out[r.uid] = {'1': '🔥 система согласна', '-1': '⚔️ система против'}.get(str(r.rel), '· система нейтральна') + f' (P {p * 100:.0f}%)'
    except Exception:
        pass
    return out


def _starts(d: pd.DataFrame) -> dict:
    """uid строки книги → начало матча (UTC, без пояса), если матч найден: футбол — табло ESPN (tips_all.csv),
    хоккей — наша база (hockey/data/games.csv.gz, hcappers.match). 14.11 20:26: пользователь должен видеть, успевает ли он."""
    out = {}
    if d.empty:
        return out
    try:
        w = pd.read_csv(T.DIR / 'tips_all.csv', dtype=str, keep_default_na=False)
        st = dict(zip(w.uid.str.rsplit(':', n=1).str[0], w.start_utc))
        for r in d.itertuples(index=False):
            key = f"tg:{r.src}:" + (f"s{r.slip}" if r.slip and r.slip != '-' else str(r.post_id))
            s = next((v for k, v in st.items() if k == key or (r.slip and k.startswith(key))), '')
            if s:
                out[r.uid] = pd.Timestamp(s)
    except Exception:
        pass
    try:
        h = d[d.sport == 'hockey']
        if len(h):
            import sys
            from pathlib import Path
            root = Path(__file__).resolve().parent.parent
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            from hockey import hcappers as HC
            g = pd.read_csv(root / 'hockey' / 'data' / 'games.csv.gz', dtype=str, keep_default_na=False)
            for r in h.itertuples(index=False):
                ts = pd.to_datetime(r.post_ts, errors='coerce')
                mt = HC.match(r.a, r.b, ts, g) if r.a and r.b and ts == ts else None
                if mt is not None:
                    out[r.uid] = pd.Timestamp(mt[0].start)
    except Exception:
        pass
    return out


def _hockey_rel(d: pd.DataFrame, rel: dict) -> dict:
    """15.11: оценка системы для свежего прогноза капера на хоккей — сразу, в момент сообщения (раньше — только в прогоне
    тенниса/хоккея, позже сообщения → «без оценки»). Матч из нашей базы (НХЛ/КХЛ), модель Пуассона того дня."""
    out = {}
    h = d[(d.sport == 'hockey') & (d.a != '') & (d.b != '') & ~d.uid.isin(list(rel))]
    if h.empty:
        return out
    try:
        import sys
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from hockey import hcappers as HC, hdata as HD, hmodel as HM
        g = HD.load()
        models = {}
        for r in h.itertuples(index=False):
            ts = pd.to_datetime(r.post_ts, errors='coerce')
            mt = HC.match(r.a, r.b, ts, g) if ts == ts else None
            if mt is None:
                continue
            gm, hf = mt
            if pd.to_datetime(gm.start) < ts - pd.Timedelta(minutes=5):   # после начала — не оценивается (К8)
                continue
            leg = HC.parse(r.pick, r.a, r.b)
            if leg is None:
                continue
            mk, pk, L = HC._orient(leg, hf)
            ok, nh, na = HC.fresh(g, gm.league, gm.home, gm.away, ts)
            if not ok:
                out[r.uid] = f'⬜ мало свежих матчей {gm.league} ({nh} и {na} из {HC.FRESH_N}) — без оценки'
                continue
            key = (gm.league, pd.Timestamp(pd.to_datetime(gm.start).date()))
            if key not in models:
                models[key] = HM.fit(g, gm.league, key[1])
            p = HC.p_model(models[key], gm.home, gm.away, mk, pk, L)
            if p is not None:
                out[r.uid] = (f'🔥 система согласна (P {p * 100:.0f}%)' if p >= 0.55 else
                              f'⚔️ система против (P {p * 100:.0f}%)' if p <= 0.45 else f'· система нейтральна (P {p * 100:.0f}%)')
    except Exception as e:
        print(f'👥 оценка хоккея: {e.__class__.__name__}: {e}')
    return out


def _when(uid: str, post_ts: str, starts: dict, now) -> str:
    """Строка времени: ⏰ начало / ⚠️ матч уже идёт / 🔴 опубликован после начала (время — Ташкент)."""
    s = starts.get(uid)
    if s is None:
        return ''
    loc = lambda t: (t + pd.Timedelta(hours=5)).strftime('%H:%M')
    p = pd.to_datetime(post_ts, errors='coerce')
    if p == p and p > s:
        return f"🔴 опубликован в {loc(p)}, матч идёт с {loc(s)}"
    if now > s:
        return f"⚠️ матч уже идёт (с {loc(s)})"
    return f"⏰ начало {loc(s)}"


def alert(rt: dict, df: pd.DataFrame, log: list[str], test: bool = False) -> None:
    """👥 Новые прогнозы каперов (за 6 ч, ещё не присланные) → бот своего вида спорта: теннис и хоккей — «Заметки Т», остальное — «Заметки Ф».
    14.11 (решение пользователя): каждый прогноз — канал, матч, ставка, кф и согласна ли система (🔥 / ⚔️); матчи вне нашей базы тоже.
    Купоны (Green Line) — только показанные до матча."""
    import config as _C
    if not getattr(_C, 'CAPPER_ALERTS', True) and not test:      # 11.11.2026: карточки каперов — в фоне (учёт и итоги — в отчётах)
        rt['tips_alerted'] = (list(rt.get('tips_alerted', [])) + list(df.uid))[-600:]
        log.append('👥 сигнал: в фоне (CAPPER_ALERTS = False)')
        return
    import notify
    import rtable
    import tipswide as W
    now = pd.Timestamp.now(tz='UTC').tz_localize(None)
    sent = set(rt.get('tips_alerted', [])) | set(rt.get('gl_alerted', []))
    fresh = pd.to_datetime(df.post_ts, errors='coerce') >= now - pd.Timedelta(hours=6)
    d = df[fresh & ~df.uid.isin(sent) & ((df.slip == '') | ((df.seen == 'pre') & (df.self == '')))]
    d = d.assign(pick=d.pick.astype(str).map(lambda x: AD_TAIL.sub('', x).strip()))   # 14.11 20:40: рекламный хвост срезается, прогноз остаётся
    slip_posts = set(df[(df.slip != '') & (df.slip != '-')].apply(lambda r: f'{r.src}:{r.post_id}', axis=1)) if len(df) else set()
    d = d[~((d.slip == '') & (d.a == '') & d.apply(lambda r: f'{r.src}:{r.post_id}' in slip_posts, axis=1))] if len(d) else d   # 15.11: текст поста + его купон — одно сообщение (купон: с матчем)
    if len(d):                                                    # 15.11: «❔» поста, у которого есть разобранная ставка (картинка распознана) — не нужен
        parsed = set(df[~df.pick.astype(str).str.startswith('❔')].apply(lambda r: f'{r.src}:{r.post_id}', axis=1))
        d = d[~(d.pick.str.startswith('❔') & d.apply(lambda r: f'{r.src}:{r.post_id}' in parsed, axis=1))]
    d = d[((d.a != '') | (d.pick != ''))                             # 14.11: и без распознанной пары команд (матч — из текста поста)
          & ~d.pick.str.lower().str.contains('реклам|подписат', regex=True)]   # все, кроме рекламы
    nb = pd.DataFrame()                                           # 14.11 19:35: nb-bet отключён пользователем (мусор)
    if test:                                                      # 🧪 разовая проверка доставки: последние прогнозы, в оба бота
        d = df[(df.a != '') & ((df.slip == '') | (df.seen == 'pre'))].copy()
        okt = d.apply(lambda r: bool(re.match(r'(П[12]|ИТ[12]?[БМ]|Т[БМ]|Ф[12]|ОЗ|1X|X2|12|победа)',
                                              _pick_short(r['pick'], r['a'], r['b']))) and not T.SKIP.search(str(r['pick']).lower()), axis=1)
        d = d[okt].tail(4)
    if d.empty and nb.empty:
        return
    st = W.stats()
    rel = _relations()
    rel.update(_hockey_rel(d, rel))
    starts = _starts(d)
    n = 0
    for bot in (['main', 'tennis'] if test else sorted({ROUTE.get(x, 'main') for x in d.sport} | ({'main'} if len(nb) else set()))):
        g = d[d.sport.map(lambda x: ROUTE.get(x, 'main')) == bot]
        if test:                                                  # тест: последние прогнозы СВОЕГО бота (хоккей/теннис → «Заметки Т»)
            g = df[(df.a != '') & (df.sport.map(lambda x: ROUTE.get(x, 'main')) == bot)].tail(4)
        if g.empty and not (bot == 'main' and len(nb)):
            continue
        # 14.11 (замечание пользователя): КАЖДЫЙ прогноз — отдельным сообщением, с вердиктом системы (🔥 / ⚔️ / нейтрально / вне базы)
        msgs = []
        for r in g.itertuples(index=False):
            r0 = st[st.src == r.src] if len(st) else st
            roi = (f" · ROI {'+' if r0.roi.iloc[0] > 0 else ''}{rtable.pct(r0.roi.iloc[0], 0)}%" if len(r0) and r0.nk.iloc[0] >= 10 else '')
            k = str(r.k or '').replace('.', ',')
            match = f"{notify.esc(r.a)} — {notify.esc(r.b)}" if r.a else notify.esc(_context(r.raw, r.pick))
            wh = _when(r.uid, r.post_ts, starts, now)
            link = f'<a href="https://t.me/{r.src}/{r.post_id}">пост ↗</a>'   # 15.11: всегда можно открыть пост целиком
            if str(r.pick).startswith('❔'):                    # 15.11 страховочная сеть: ставку не разобрали — текст поста как есть
                msgs.append(f"👥 <b>{notify.esc(T.TG.get(r.src, r.src))}</b>{roi} · {ICON.get(r.sport, '•')}\n"
                            + (f"{notify.esc(r.a)} — {notify.esc(r.b)}\n" if r.a else '')
                            + f"❔ <i>{notify.esc(str(r.pick)[2:220])}</i>" + (f"\n{wh}" if wh else '') + f"\n⬜ ставка не разобрана · {link}")
                continue
            verdict = rel.get(r.uid) or ('· после начала — не оценивается' if wh.startswith('🔴') else
                                         '⬜ без оценки системы' if wh else '⬜ вне базы системы')
            msgs.append(f"👥 <b>{notify.esc(T.TG.get(r.src, r.src))}</b>{roi} · {ICON.get(r.sport, '•')}\n{match}\n"
                        f"<b>{notify.esc(_pick_short(r.pick, r.a, r.b, r.sport) or str(r.pick)[:40])}</b>" + (f' @{k}' if k else '')
                        + (f"\n{wh}" if wh else '') + f"\n{verdict} · {link}")
        if bot == 'main':
            for r in nb.itertuples(index=False):                  # nb-bet: автор, матч, ставка, кф
                k = str(r.k or '').replace('.', ',')
                msgs.append(f"👥 <b>nb-bet</b> · {notify.esc(str(r.author)[:20])} · ⚽\n{notify.esc(r.home)} — {notify.esc(r.away)}\n"
                            f"<b>{notify.esc(str(r.label or r.raw)[:40])}</b>" + (f' @{k}' if k else '')
                            + f"\n{rel.get(r.uid, '⬜ вне базы системы')}")
            n += len(nb)
        for m in msgs:
            notify.send_blocks([('🧪 <b>ТЕСТ</b>\n' if test else '') + m], bot=bot)
        n += len(g)
    if test:
        log.append(f'👥 тест доставки: {n} строк в оба бота')
        return
    rt['tips_alerted'] = (list(sent) + list(d.uid) + (list(nb.uid) if len(nb) else []))[-900:]
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
           + (f'\nЧестность отметок: {hon}' if hon else ''))
    ss = slip_stats()
    if len(ss):                                                   # 📷 купоны: только показанные до матча
        r2 = [rtable.row(f"{r.name[:8]}·{SPORT_RU.get(r.sport, r.sport)[:4]}", r.w, r.pre, str(r.none))
              for r in ss.itertuples(index=False) if r.pre]
        out += ('\n📷 <b>Купоны до матча</b>\n' + rtable.render(r2, ('Канал·вид (б/и)', 'W/до', '%')))
    return out


def card() -> str:
    """👥 Одна карточка каперов для суточного отчёта и аудита (стандарт: таблица ≤ 35 знаков, легенда одной строкой).
    Строки — каналы по табло ESPN (все их футбольные матчи); ниже — купоны Green Line до матча и синтез с моделью."""
    import notify
    import rtable
    import tipswide as W
    st = W.stats()
    lines = []
    if len(st):
        lines.append(f"{'Канал':<10} {'Угад.':>6} {'%':>3} {'ROI':>4}")
        lines.append('─' * 26)
        for r in st.itertuples(index=False):
            roi = f"{'+' if r.roi > 0 else ''}{r.roi * 100:.0f}" if r.nk >= 10 and r.roi == r.roi else '—'
            mark = '✔' if (r.nk >= W.TRUST_NK and r.t >= W.TRUST_T) else ''
            lines.append(f"{(mark + r.name)[:10]:<10} {f'{r.won}/{r.n}':>6} {r.won / r.n * 100:>3.0f} {roi:>4}")
    out = '👥 <b>Каперы</b>' + (('\n<pre>' + notify.esc('\n'.join(lines)) + '</pre>') if lines
                               else '\nрассчитанных ставок пока нет')
    ss = slip_stats()
    if len(ss):
        g = ss[ss.sport == 'football']
        if len(g) and int(g.pre.iloc[0]):
            out += f"\n📷 Green Line до матча: {int(g.w.iloc[0])}/{int(g.pre.iloc[0])} показаны W · без итога {int(g.none.iloc[0])}"
    ts = T.stats()
    if len(ts):
        an = int((ts.n - ts.dis_n).sum()); aw = int((ts.won - (ts.dis_n - ts.dis_sys)).sum())
        dn, ds = int(ts.dis_n.sum()), int(ts.dis_sys.sum())
        out += f"\n🔥 модель+капер {aw}/{an} · ⚔️ права модель {ds}/{dn}"
    return out
