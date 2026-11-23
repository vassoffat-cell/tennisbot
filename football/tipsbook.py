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
from pathlib import Path

import numpy as np
import pandas as pd

import tips as T

BOOK = T.DIR / 'book.csv'
ALERTS = T.DIR / 'alerts.csv'     # 23.11 аудит (AUDIT 0ж④, CODEX 2а): журнал присланных сообщений каперов с вердиктом — проверка «каждое сообщение»
VERSION = 17                                        # версия правил разбора (смена → пересборка книги из архива)
PDIR = T.DIR / 'posts'
COLS = ['uid', 'src', 'post_id', 'post_ts', 'sport', 'a', 'b', 'pick', 'k', 'self', 'self_ts', 'raw', 'slip', 'stake', 'seen']
SPORT_RU = {'football': 'футбол', 'hockey': 'хоккей', 'tennis': 'теннис', 'basketball': 'баскет', 'volleyball': 'волейб',
            'esports': 'кибер', 'other': 'другое'}
SPORTS = [
    ('hockey', r'🏒|хокке|\bкхл\b|\bнхл\b|\bвхл\b|\bмхл\b|шайб|\bnhl\b|\bkhl\b|авангард|ак барс|салават|барыс|трактор|северсталь|'
               r'\bамур\b|адмирал|куньлунь|драконы|нефтехимик|автомобилист|'
               # 18.11: купоны Green Line с НХЛ/КХЛ уходили в футбол — только однозначные (нет одноимённых футбольных клубов)  21.11: «Локомотив Ярославль» (купон 402008542 ушёл в ⚽)
               r'\bхк\b|\bска\b(?!-? ?хабаровск)|локомотив ярославль|торпедо нн|торпедо н\.? ?новгород|\bлада\b|металлург мг|'
               r'эвеланш|лайтнинг|блэкхокс|\bкингз|кэпиталз|пингвинз|флайерз|айлендерс|девилз|брюинз|сэйбрз|канадиенс|'
               r'сенаторз|мейпл ?лифс|ред ?уингз|пантерз|харрикейнз|блю ?джекетс|предаторз|ойлерз|флэймз|кэнакс|кракен|'
               r'голден ?найтс|\bдакс\b|шаркс|\bджетс\b|\bуайлд\b|мамонтс'),
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


EXPLICIT = [('hockey', r'🏒|хокке|\bкхл\b|\bнхл\b|\bвхл\b|\bмхл\b|шайб|\bnhl\b|\bkhl\b'),
            ('tennis', r'🎾|теннис|\batp\b|\bwta\b|\bitf\b|тай-?брейк|по геймам|\bгейм'),
            ('basketball', r'🏀|баскет|\bnba\b|\bнба\b'),
            ('volleyball', r'🏐|волейб'),
            ('esports', r'кибер|\bcs ?2\b|\bdota\b|дота|valorant'),
            ('football', r'⚽|футбол|премьер[- ]?лиг|\bапл\b|ла лиг|серия a|бундеслиг|лига чемпионов|лига европы|\bрпл\b|\bфнл\b|\bгол')]


def _whole(rx: str, low: str) -> bool:
    """Совпадение целым словом (21.11: «трактористы» — прозвище «Ипсвича» — совпадало с хоккейным «Трактором»)."""
    for m in re.finditer(rx, low):
        a, b = m.start(), m.end()
        if (a == 0 or not low[a - 1].isalpha()) and (b == len(low) or not low[b].isalpha() or not m.group(0)[-1:].isalpha()):
            return True
    return False


def sport_of(text: str) -> str:
    """21.11: сначала явные признаки вида (значок ⚽/🏒/🎾, «хоккей», «Премьер-лига», КХЛ …) — один вид → он; затем названия
    клубов — только целым словом. Раньше клуб по части слова перебивал явный ⚽ (Тоттенхэм — Ипсвич ушёл в хоккей)."""
    low = text.lower()
    found = [sp for sp, rx in EXPLICIT if re.search(rx, low)]
    if len(found) == 1:
        return found[0]
    for sp, rx in SPORTS:
        if (sp in found or not found) and (_whole(rx, low) if sp == 'hockey' else re.search(rx, low)):   # клубы НХЛ/КХЛ — целым словом
            return sp
    return found[0] if found else 'other'


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


REVIEW_POST = re.compile(r'(?i)^\s*(?:отзывы? клиент|видео-?отч[её]т|отч[её]т (?:по )?экспресс|всех с победой)')


def tips_of(src: str, p: dict) -> list[dict]:
    """Все прогнозы поста: по парам «A — B» (фрагмент до следующей пары) или одна ставка без пары."""
    import tipswide as W
    if REVIEW_POST.search(p.get('text') or ''):                 # 19.11: отзывы / отчёты о выигрыше — скрины прошлых ставок, не прогноз
        return []
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
    chg: dict[str, list[int]] = {}
    for src, name in T.TG.items():
        try:
            changed, r = scan(src, days, pages)
            chg[src] = [int(p['id']) for p in changed]
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
    try:                                                           # 21.11: «все матчи» (tips_all, независимая сверка с табло)
        wide_feed(rt, chg, log)
    except Exception as e:
        log.append(f'👥 все матчи: сбой {e.__class__.__name__}: {e}')
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
OCR_IMG = OCR_CH | set(T.TG)                          # 23.11 (решение пользователя): OCR картинок всех 5 каналов (было: Green Line + Каппер UK)
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


def _dists() -> dict:
    """Матрицы счёта модели наших матчей (run.save_dists, data/tips/dists.json): espn_id → home, away, start, comp, P."""
    f = T.DIR / 'dists.json'
    try:
        return json.loads(f.read_text()) if f.exists() else {}
    except ValueError:
        return {}


def ours(r, fx: dict) -> tuple | None:
    """20.11: строка книги каперов → наш матч. (espn_id, матч, ноги в порядке хозяева/гости или [] — рынок вне модели).
    None — матча нет в нашей базе (лига вне охвата, другой вид спорта, пара не распознана)."""
    import tipswide as W
    if r.sport in ('hockey', 'tennis') or not r.a or not r.b or str(r.pick).startswith('❔'):
        return None
    for eid, e in fx.items():
        pos = T.find_teams(f'{r.a} — {r.b}', [e['home'], e['away']])
        if len(pos) != 2:
            continue
        flip = pos[e['home']] > pos[e['away']]                   # в посте гости названы первыми: «1» — первая названная
        tm = (e['away'], e['home']) if flip else (e['home'], e['away'])
        pick = str(r.pick)
        legs = []
        if not _off_score(pick):                                  # фолы, угловые — не счёт матча (таймы — К17, по счёту таймов)
            legs = T.parse_legs(pick, None, tm, True) or ([] if 'тайм' in pick.lower() else
                                                          T.parse_legs(_pick_short(pick, r.a, r.b, 'football'), None, tm, True))
        if legs and any(mk in ('TOT', 'IT1', 'IT2') and isinstance(L, float) and L > 7.5 for mk, _, L in legs):
            legs = []
        return eid, e, (W._flip(legs) if flip and legs else legs)
    return None


def pair_of(r, df: pd.DataFrame) -> tuple[str, str]:
    """21.11 (пример пользователя: Green Line 19831 «Эспаньол, ТБ (0.5)» — матч только на картинке-купоне): пара команд строки
    книги; нет в тексте — из купона / другой строки того же поста."""
    if r.a and r.b:
        return r.a, r.b
    sib = df[(df.src == r.src) & (df.post_id.astype(str) == str(r.post_id)) & (df.a != '') & (df.b != '')]
    return (sib.a.iloc[0], sib.b.iloc[0]) if len(sib) else ('', '')


def _pair_text(raw: str, events) -> tuple[str, str]:
    """21.11 (Грозный 13133: «Победа МанСити и ТБ (1.5)», в тексте «Манчестер Сити» и «Фулхэм» без тире): пара команд одного
    матча, обе названные в тексте поста. Одно событие — пара; несколько или ни одного — пусто (без догадок)."""
    hits = []
    for e in events:
        pos = T.find_teams(raw, [e['home'], e['away']])
        if len(pos) == 2:
            hits.append((e['home'], e['away']))
    return hits[0] if len(set(hits)) == 1 else ('', '')


LIVE = re.compile(r'(?<![A-Za-z])LIVE(?![A-Za-z])|(?<![а-яё])лайв(?![а-яё])', re.I)   # 21.11: «🔴 ПРОГНОЗ LIVE» — пост во время матча


BET_WORDS = {'победа', 'победитель', 'тотал', 'фора', 'обе', 'ставка', 'индивидуальный', 'двойной', 'первый', 'второй', 'исход',
             'матч', 'экспресс', 'ординар', 'больше', 'меньше', 'да', 'нет', 'ничья'}


def _person(pick: str) -> list[tuple[str, str]]:
    """21.11 (Green Line 19845 «Победитель, Александр Топурия» — UFC привязался к «Crewe Alexandra» по транслиту одного слова):
    два подряд слова с заглавной вне справочника команд и вне слов ставки — имя и фамилия (или название из двух слов).
    Такая пара совпадает с командой табло, только если ОБА слова совпали с одной командой."""
    out = []
    for m in re.finditer(r'(?<![А-ЯЁа-яё])([А-ЯЁ][а-яё\-]{2,})\s+([А-ЯЁ][а-яё\-]{2,})(?![А-ЯЁа-яё])', pick):
        x, y = m.group(1).lower(), m.group(2).lower()
        if not ({x, y} & BET_WORDS or T._alias(x) or T._alias(y) or T._alias(f'{x} {y}')):
            out.append((x, y))
    return out


def _event_of(a: str, b: str, pick: str, ts, cache: dict, raw: str = '') -> tuple[dict, bool] | None:
    """Событие табло ESPN (все лиги): по паре команд, иначе — по единственной команде в ставке («Эспаньол, ТБ (0.5)»)."""
    import tipswide as W
    evs = W._events_around(ts, cache)
    if not evs:
        return None
    if a and b:
        return W.match_pair(a, b, evs)
    if raw:                                                       # 21.11: обе команды названы в тексте поста — это матч
        ha, hb = _pair_text(raw, evs)
        if ha:
            return next(((e, False) for e in evs if e['home'] == ha and e['away'] == hb), None)
    caps = {w.lower() for w in re.findall(r'\b[А-ЯЁA-Z][а-яёa-z\-]{3,}', pick)}
    pairs = _person(pick)
    pers = {w for pr in pairs for w in pr}
    ws = [w for w in T._words(pick) if ((w in caps and len(w) >= 5) or T._alias(w)) and w not in pers]
    ws += [f'{x} {y}' for x, y in zip(T._words(pick), T._words(pick)[1:]) if T._alias(f'{x} {y}')]
    hits = {}
    for w in ws:
        ru = W._ru_info(w)
        if not ru[0] and not ru[1]:
            continue
        if ru[0]:                                                 # есть в справочнике — только точное имя (без транслита:
            ru = (ru[0], [], ru[2])                               # «аль-наср» ≈ «Alanyaspor» по сходству букв — ложная привязка)
        for e in evs:
            h, aw = e.setdefault('_h', W._en_info(e['home'])), e.setdefault('_a', W._en_info(e['away']))
            if W._same(ru, h) or W._same(ru, aw):
                hits[e['id']] = (e, False)
    for x, y in pairs:                                            # пара слов — одна команда: совпасть должны оба слова
        rx, ry = W._ru_info(x), W._ru_info(y)
        for e in evs:
            for t in (e.setdefault('_h', W._en_info(e['home'])), e.setdefault('_a', W._en_info(e['away']))):
                if W._same(rx, t) and W._same(ry, t):
                    hits[e['id']] = (e, False)
    if len(hits) > 1:                                             # несколько событий команды — ближайшее по времени к посту
        near = sorted(hits.values(), key=lambda v: abs((pd.Timestamp(v[0]['start']) - pd.Timestamp(ts)).total_seconds()))
        return near[0]
    return next(iter(hits.values())) if hits else None


def _legs_for(pick: str, a: str, b: str, tm: tuple[str, str], flip: bool) -> list:
    """Ноги ставки в порядке хозяева/гости; [] — рынок вне модели счёта (таймы, фолы, угловые …)."""
    import tipswide as W
    if _off_score(pick):
        return []
    legs = T.parse_legs(pick, None, tm, True) or (T.parse_legs(_pick_short(pick, a, b, 'football'), None, tm, True)
                                                  if a and b and 'тайм' not in pick.lower() else [])
    if legs and any(mk in ('TOT', 'IT1', 'IT2') and isinstance(L, float) and L > 7.5 for mk, _, L in legs):
        return []
    import tipswide as W2
    return W2._flip(legs) if flip and legs else legs


def _off_score(pick: str) -> bool:
    """Рынок не по счёту голов (фолы, угловые, карточки …). 21.11 К17: таймы — по счёту таймов, не «вне модели»."""
    import tipswide as W
    import halves as HV
    p = str(pick).lower()
    return bool(W.EXOT.search(p) or HV.OTHER.search(p) or (T.SKIP.search(p) and 'тайм' not in p))


def _verdict(p: float, src: str = 'система') -> str:
    if p != p:                                                    # nan — сочетание таймов, которое модель не считает
        return '⬜ рынок вне модели'
    tag = {'система': ('🔥 система согласна', '⚔️ система против', '· система нейтральна'),
           'рынок': ('🔥 рынок за', '⚔️ рынок против', '· рынок нейтрален')}[src]
    return (f'{tag[0]} (P {p * 100:.0f}%)' if p >= 0.55 else f'{tag[1]} (P {p * 100:.0f}%)' if p <= 0.45 else f'{tag[2]} (P {p * 100:.0f}%)')


def _football_rel(d: pd.DataFrame, rel: dict, df: pd.DataFrame | None = None) -> tuple[dict, dict]:
    """Вердикт каждому прогнозу капера на футбол (и «другое» с командами), по уровням (20–21.11, замечания пользователя
    «все прогнозы — с оценкой и вердиктом системы», «не может быть вне системы»):
      ① наш матч (матрица модели, dists.json) → 🔥/⚔️/· система (P модели);
      ② матч другой лиги на табло ESPN с ценой DraftKings → λ по рынку (fmarket.invert, Шин) → 🔥/⚔️/· рынок (P);
      ③ матч найден, рынок не считается по счёту (таймы, фолы, угловые) → «⬜ рынок вне модели»;
      ④ матч найден, цен нет → «⬜ лига вне модели, цен нет»; ⑤ не найден → «⬜ матч не найден».
    Пара команд — из текста или купона того же поста (pair_of); иначе — единственная команда ставки на табло.
    Возвращает (uid → вердикт, uid → начало UTC): по началу сообщение получает ⏰ / ⚠️ / 🔴 (после начала — без вердикта, К8)."""
    import dc
    import fmarket as FM
    rel_o, st_o = {}, {}
    if d.empty:
        return rel_o, st_o
    df = d if df is None else df
    fx = _dists()
    now = pd.Timestamp.now(tz='UTC').tz_localize(None)
    cache: dict = {}
    for r in d[~d.uid.isin(list(rel)) & ~d.sport.isin(['hockey', 'tennis', 'basketball', 'volleyball', 'esports', 'tabletennis'])
               & ~d.pick.astype(str).str.startswith('❔')].itertuples(index=False):
        a, b = pair_of(r, df)
        if not (a and b) and fx:                                  # 21.11: пара из текста поста — сначала среди НАШИХ матчей
            a, b = _pair_text(str(getattr(r, 'raw', '') or ''), list(fx.values()))
        pick = str(r.pick)
        try:
            ts = pd.Timestamp(r.post_ts)
        except ValueError:
            continue
        hit = None
        rr = r._replace(a=a, b=b) if hasattr(r, '_replace') else r
        if a and b:                                               # ① наш матч
            o = ours(rr, fx)
            if o is not None:
                eid, e, legs = o
                st_o[r.uid] = pd.Timestamp(e['start'])
                if st_o[r.uid] >= now:
                    rel_o[r.uid] = _verdict(T.p_legs(np.asarray(e['P'], float), legs)) if legs else \
                        '⬜ наш матч, рынок вне модели (фолы, угловые…)'
                continue
        try:
            hit = _event_of(a, b, pick, ts, cache, str(getattr(r, 'raw', '') or ''))
        except Exception:
            hit = None
        if hit is None:
            raw = str(getattr(r, 'raw', '') or '')
            rel_o[r.uid] = ('🔴 LIVE — опубликован во время матча, не оценивается' if not (a and b) and LIVE.search(raw) else
                            '⬜ вид спорта вне модели (ставка на спортсмена)' if re.match(r'\s*победитель\b', pick, re.I) and _person(pick) else
                            '⬜ матч не найден на табло')
            continue
        e, flip = hit
        st_o[r.uid] = pd.Timestamp(e['start'])
        if st_o[r.uid] < now:
            continue
        if str(e['id']) in fx:                                    # ① наш матч, найденный по одной команде
            legs = _legs_for(pick, a, b, (e['away'], e['home']) if flip else (e['home'], e['away']), flip)
            P = np.asarray(fx[str(e['id'])]['P'], float)
            rel_o[r.uid] = _verdict(T.p_legs(P, legs)) if legs else '⬜ наш матч, рынок вне модели (фолы, угловые…)'
            continue
        legs = _legs_for(pick, a, b, (e['away'], e['home']) if flip else (e['home'], e['away']), flip)
        od = e.get('odds') or {}
        inv = FM.invert(od) if od.get('h') and od.get('d') and od.get('a') else None
        if inv is None:
            rel_o[r.uid] = '⬜ лига вне модели, цен нет'
        elif not legs:
            rel_o[r.uid] = '⬜ рынок вне модели (фолы, угловые…)'
        else:
            rel_o[r.uid] = _verdict(T.p_legs(dc.score_matrix(inv['lh'], inv['la'], C_RHO()), legs), 'рынок')
    return rel_o, st_o


def C_RHO() -> float:
    import config as C
    return C.RHO


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
    import os
    resend = {x.strip() for x in os.getenv('CAP_RESEND', '').split(',') if x.strip()}   # 21.11: повтор по просьбе пользователя
    if resend and len(df):
        ru = set(df.uid[(df.src.astype(str) + ':' + df.post_id.astype(str)).isin(resend)])
        sent -= ru
        rt['tips_alerted'] = [u for u in rt.get('tips_alerted', []) if u not in ru]
        log.append(f'👥 повтор по просьбе: постов {len(resend)}, строк {len(ru)}')
    if len(df):                                                   # 21.11: пересборка книги дала посту новый uid (купон вместо текста) —
        pk = df.src.astype(str) + ':' + df.post_id.astype(str)    # пост уже присылался → новый uid тоже считается присланным
        old = pd.to_datetime(df.post_ts, errors='coerce') < now - pd.Timedelta(hours=14)
        sp = set(pk[df.uid.isin(sent)]) | {':'.join(u.split(':')[:2]) for u in sent if u.count(':') >= 2 and u.split(':')[1].isdigit()}
        add_ = set(df.uid[old & pk.isin(sp) & ~df.uid.isin(sent)])
        if add_:
            sent |= add_
            rt['tips_alerted'] = list(rt.get('tips_alerted', [])) + sorted(add_)
    fresh = pd.to_datetime(df.post_ts, errors='coerce') >= now - pd.Timedelta(hours=14)   # 19.11 Г0б: посты тихого окна 03–15 приходят в 15:22
    d = df[fresh & ~df.uid.isin(sent) & ((df.slip == '') | ((df.seen == 'pre') & (df.self == '')))]
    d = d.assign(pick=d.pick.astype(str).map(lambda x: AD_TAIL.sub('', x).strip()))   # 14.11 20:40: рекламный хвост срезается, прогноз остаётся
    slip_posts = set(df[(df.slip != '') & (df.slip != '-')].apply(lambda r: f'{r.src}:{r.post_id}', axis=1)) if len(df) else set()
    d = d[~((d.slip == '') & (d.a == '') & d.apply(lambda r: f'{r.src}:{r.post_id}' in slip_posts, axis=1))] if len(d) else d   # 15.11: текст поста + его купон — одно сообщение (купон: с матчем)
    if len(d):                                                    # 15.11: «❔» поста, у которого есть разобранная ставка (картинка распознана) — не нужен
        parsed = set(df[~df.pick.astype(str).str.startswith('❔')].apply(lambda r: f'{r.src}:{r.post_id}', axis=1))
        d = d[~(d.pick.str.startswith('❔') & d.apply(lambda r: f'{r.src}:{r.post_id}' in parsed, axis=1))]
    if len(d):                                                    # 20.11: один пост — одна ставка: дубль (строки «h» и «0» одного поста) не шлём
        key = lambda x: x.src.astype(str) + ':' + x.post_id.astype(str) + ':' + x.pick.astype(str).str.lower().str.strip()
        done = set(key(df[df.uid.isin(sent)])) if len(sent) else set()
        kd = key(d)
        dup = d[kd.isin(done) | kd.duplicated()]
        sent |= set(dup.uid)
        d = d[~d.uid.isin(dup.uid)]
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
    frel, fst = _football_rel(d, rel, df)
    rel.update(frel)
    try:                                                          # 21.11: теннис — вердикт по нашим линиям матча
        trel, tst = _tennis_rel(d, df)
    except Exception as ex:
        trel, tst = {}, {}
        log.append(f'👥 теннис: вердикт — сбой {ex.__class__.__name__}: {ex}')
    rel.update(trel)
    starts = _starts(d)
    for u, s0 in list(fst.items()) + list(tst.items()):
        starts.setdefault(u, s0)
    n = 0
    for bot in (['main', 'tennis'] if test else sorted({ROUTE.get(x, 'main') for x in d.sport} | ({'main'} if len(nb) else set()))):
        g = d[d.sport.map(lambda x: ROUTE.get(x, 'main')) == bot]
        if test:                                                  # тест: последние прогнозы СВОЕГО бота (хоккей/теннис → «Заметки Т»)
            g = df[(df.a != '') & (df.sport.map(lambda x: ROUTE.get(x, 'main')) == bot)].tail(4)
        if g.empty and not (bot == 'main' and len(nb)):
            continue
        # 14.11 (замечание пользователя): КАЖДЫЙ прогноз — отдельным сообщением, с вердиктом системы (🔥 / ⚔️ / нейтрально / вне базы)
        msgs = []
        jr: list[dict] = []                                       # 23.11 аудит: журнал вердиктов (alerts.csv)
        no_reason: list[str] = []
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
                jr.append(dict(uid=r.uid, sent=str(now)[:16], bot=bot, sport=r.sport, match=f'{r.a} — {r.b}' if r.a else '',
                               pick=str(r.pick)[:60], when=wh.split(' ')[0] if wh else '', verdict='⬜ ставка не разобрана'))
                continue
            verdict = rel.get(r.uid) or ('· после начала — не оценивается' if wh.startswith('🔴') else None)
            if verdict is None:                                   # 21.11 (К16): «без оценки» / «вне базы» без причины — запрещено
                verdict = '⬜ матч не найден на табло'
                no_reason.append(r.uid)
            jr.append(dict(uid=r.uid, sent=str(now)[:16], bot=bot, sport=r.sport, match=f'{r.a} — {r.b}' if r.a else '',
                           pick=str(_pick_short(r.pick, r.a, r.b, r.sport) or r.pick)[:60], when=wh.split(' ')[0] if wh else '',
                           verdict=re.sub(r'<[^>]+>', '', str(verdict))[:80]))
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
        if jr and not test:
            try:
                pd.DataFrame(jr).to_csv(ALERTS, mode='a', header=not ALERTS.exists(), index=False)
            except Exception as e:
                log.append(f'👥 журнал вердиктов — сбой записи ({e.__class__.__name__})')
        if no_reason:                                             # сторож health: «каперы: сбои вердикта без причины»
            log.append(f"👥 вердикт без причины (К16): {len(no_reason)} — {', '.join(no_reason[:5])}")
    if test:
        log.append(f'👥 тест доставки: {n} строк в оба бота')
        return
    rt['tips_alerted'] = (list(sent) + list(d.uid) + (list(nb.uid) if len(nb) else []))[-900:]
    rt.pop('gl_alerted', None)
    log.append(f'👥 сигнал каперов: {n}')


_TN: dict = {}


def _tennis_book() -> tuple[dict, pd.DataFrame]:
    """Имена игроков и линии наших матчей тенниса (снимок суток С16: data/seal/tennis_*.csv + tennis_names.json)."""
    if 'b' not in _TN:
        import importlib.util
        root = Path(__file__).resolve().parent.parent
        try:
            nm = json.loads((root / 'data/seal/tennis_names.json').read_text(encoding='utf-8'))
        except Exception:
            nm = {}
        fs = sorted((root / 'data/seal').glob('tennis_20*.csv'))[-3:]
        ln = pd.concat([pd.read_csv(f, dtype=str, keep_default_na=False) for f in fs]) if fs else pd.DataFrame(
            columns=['match_id', 'start_utc', 'group', 'market', 'pick', 'line', 'p'])
        spec = importlib.util.spec_from_file_location('tennis_names', root / 'names.py')
        NM = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(NM)
        _TN.update(b=(nm, ln), NM=NM)
    return _TN['b']


def _tennis_match(a: str, b: str) -> tuple[str, bool, list] | None:
    """21.11: пара игроков (по-русски) → наш матч тенниса: (match_id, перевёрнуто, [игрок 1, игрок 2, начало]). Совпасть должны ОБА."""
    if not a or not b:
        return None
    try:
        nm, _ = _tennis_book()
        NM = _TN['NM']
    except Exception:
        return None
    def sim(ru: str, la: str) -> float:
        return NM._sim(NM.translit(ru.split()[-1]), NM._plain(la.split()[-1])) if ru.split() and la.split() else 0.0
    best = None
    for mid, (p1, p2, st) in nm.items():
        for flip, (x, y) in ((False, (p1, p2)), (True, (p2, p1))):
            sc = min(sim(a, x), sim(b, y))
            if sc >= 0.7 and (best is None or sc > best[0]):
                best = (sc, mid, flip, [p1, p2, st])
    return (best[1], best[2], best[3]) if best else None


def _tennis_players(a: str, b: str) -> bool:
    """21.11: оба имени — игроки тенниса 2025–26 (Sackmann ATP/WTA): совпадают и фамилия, и имя одного игрока (по транслиту).
    Для купонов без слова «теннис» вне наших текущих матчей; команды («Реал Мадрид», «ЦСКА Москва») не проходят."""
    if 'people' not in _TN:
        root = Path(__file__).resolve().parent.parent
        people = set()
        try:                                                      # прогон каперов скачивает только data/seal (sparse checkout)
            people = {(v.split()[0], v.split()[-1]) for v in json.loads((root / 'data/seal/tennis_people.json').read_text(encoding='utf-8'))}
        except Exception:
            for t in ('atp', 'wta'):
                for y in (2025, 2026):
                    f = root / f'data/sackmann/{t}_matches_{y}.csv'
                    if f.exists():
                        x = pd.read_csv(f, usecols=['winner_name', 'loser_name'], dtype=str).stack().dropna()
                        people |= {(v.split()[0], v.split()[-1]) for v in map(str, x) if len(v.split()) >= 2}
        _tennis_book()
        NM = _TN['NM']
        _TN['people'] = [(NM._plain(f), NM._plain(l)) for f, l in people]
    NM, people = _TN['NM'], _TN['people']
    def ok(ru: str) -> bool:
        w = ru.split()
        if len(w) < 2 or not all(re.fullmatch(r'[А-ЯЁа-яё\-]{2,}', x) for x in (w[0], w[-1])):
            return False
        fv, lv = NM.translit(w[0]), NM.translit(w[-1])
        return any(NM._sim(lv, l) >= 0.7 and NM._sim(fv, f) >= 0.7 for f, l in people)
    return ok(a) and ok(b)


def _tennis_rel(d: pd.DataFrame, df: pd.DataFrame) -> tuple[dict, dict]:
    """21.11: вердикт прогнозу капера по теннису — по нашим линиям матча (снимок суток): исход, фора и тоталы по геймам."""
    rel, st = {}, {}
    t = d[d.sport == 'tennis'] if len(d) else d
    if not len(t):
        return rel, st
    _, ln = _tennis_book()
    for r in t.itertuples(index=False):
        a, b = pair_of(r, df)
        hit = _tennis_match(a, b)
        if hit is None:
            rel[r.uid] = '⬜ матч не найден среди наших матчей тенниса'
            continue
        mid, flip, (p1, p2, start) = hit
        try:
            st[r.uid] = pd.Timestamp(start)
        except ValueError:
            pass
        pk = str(r.pick).lower().replace(',', '.')
        nums = re.findall(r'[-+]?\d+(?:\.\d+)?', pk)
        name_a, name_b = a.split()[-1].lower(), b.split()[-1].lower()
        side = '1' if name_a in pk or re.search(r'(?:ф|фора|п)\s*1\b|\bфора 1', pk) else '2' if name_b in pk or re.search(r'(?:ф|фора|п)\s*2\b', pk) else ''
        if side and flip:
            side = {'1': '2', '2': '1'}[side]
        if 'фор' in pk and side and nums:
            key = ('HCP', side, f'{float(nums[-1]):g}')
        elif re.search(r'тотал|\bт[бм]', pk) and nums:
            ou = 'O' if re.search(r'больше|\bб\b|тб', pk) else 'U'
            it = re.search(r'инд|\bит', pk)
            key = ((f'IT{side}' if it and side else 'TOTAL'), ou, f'{float(nums[-1]):g}')
        elif side and re.search(r'побед|выигра|\bп[12]\b|исход', pk):
            key = ('ML', side, '')
        else:
            rel[r.uid] = '⬜ наш матч тенниса, рынок вне модели'
            continue
        m = ln[(ln.match_id.astype(str) == str(mid)) & (ln.market == key[0]) & (ln.pick == key[1])]
        hitp = m[m.line.map(lambda v: f'{float(v):g}' if str(v) not in ('', 'nan') else '') == key[2]]
        if not len(hitp) and key[2] and not float(key[2]).is_integer():   # противоположная сторона той же линии: P = 1 − P
            opp = {'1': '2', '2': '1', 'O': 'U', 'U': 'O'}.get(key[1])
            ol = f'{-float(key[2]):g}' if key[0] == 'HCP' else key[2]
            mo = ln[(ln.match_id.astype(str) == str(mid)) & (ln.market == key[0]) & (ln.pick == opp)]
            mo = mo[mo.line.map(lambda v: f'{float(v):g}' if str(v) not in ('', 'nan') else '') == ol]
            if len(mo):
                hitp = mo.assign(p=1 - mo.p.astype(float))
        if len(hitp):
            rel[r.uid] = _verdict(float(hitp.p.iloc[-1]))
        else:
            near = '; '.join(f"{key[0]} {x.line} P {float(x.p) * 100:.0f}%" for x in m.itertuples(index=False))[:60]
            rel[r.uid] = '⬜ наш матч тенниса, такой линии нет в модели' + (f' (есть {near})' if near else '')
    return rel, st


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
            sp = e['sport'] or next((x for x in (sport_of(e['a'] + ' ' + e['b']), sport_of(part), sport_of(p.get('text') or ''))
                                     if x != 'other'), '')
            if sp in ('', 'football') and (_tennis_match(e['a'], e['b']) or _tennis_players(e['a'], e['b'])):   # 21.11: «Алькарас — Шелтон» без «теннис»
                sp = 'tennis'
            sp = sp or 'football'
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


WIDE_CATCHUP = '2026-11-08'                # 21.11: догон — ставки на таймы (К17), формат 1xBet, «Кёльн»


def wide_feed(rt: dict, chg: dict, log: list[str]) -> int:
    """21.11 (проверка «все ли журналы заполняются»): «все матчи» (tipswide → tips_all.csv, сверка каждого прогноза с табло ESPN)
    брал посты из T.LAST_POSTS, а их заполнял только tips.collect прогона футбола; с 14.11 18:50 tipswide.collect вызывается
    в прогоне каперов, где LAST_POSTS пуст → журнал стоял (Грозный с 13.11, Каппер UK с 12.11). Теперь — посты, прочитанные
    этим прогоном (текст + распознанная картинка; купоны Green Line идут своим путём — wide_rows), и разово — архив с WIDE_CATCHUP."""
    import tipswide as W
    since = None if rt.get('wide_catchup') == WIDE_CATCHUP else pd.Timestamp(WIDE_CATCHUP)
    T.LAST_POSTS.clear()
    for src, name in T.TG.items():
        a = _arch(src)
        ids = set(chg.get(src, []))
        if since is not None:
            ids |= {k for k, x in a.items() if pd.Timestamp(x['ts']) >= since}
        for k in sorted(ids):
            x = a.get(k)
            if not x or REVIEW_POST.search(x.get('text') or ''):
                continue
            txt = x.get('text') or ''
            if src not in OCR_CH and x.get('ocr'):               # Каппер UK: ставка на картинке под разбором
                txt = (txt + '\n' + x['ocr']).strip()
            T.LAST_POSTS.append((src, name, dict(id=int(x['id']), ts=pd.Timestamp(x['ts']), text=txt)))
    n = W.collect(log)
    log[-1] += f' · постов {len(T.LAST_POSTS)}' + (f' (догон с {WIDE_CATCHUP})' if since is not None else '')
    rt['wide_catchup'] = WIDE_CATCHUP
    return n


def to_ours(df: pd.DataFrame, log: list[str]) -> int:
    """Прогнозы каперов на НАШИ матчи → tips.csv (синтез 🔥/⚔️ в выборе ⭐ — Г3а, учёт против модели, вето ✔).
    20.11: раньше — только купоны Green Line и только при матчах прогона футбола (T.LAST_FIXTURES) → в прогоне каперов
    0 матчей, журнал пуст с 15.11. Теперь — все строки книги (текст, картинки, купоны до матча) по dists.json."""
    import config as C
    fx = _dists()
    if not fx:
        log.append('👥 каперы → наши матчи: нет матриц модели (dists.json) — ❌')
        return 0
    now = pd.Timestamp.now(tz='UTC').tz_localize(None)
    d = df[((df.slip == '') | (df.seen == 'pre'))
           & (pd.to_datetime(df.post_ts, errors='coerce') >= now - pd.Timedelta(hours=48))]
    d = d.assign(**dict(zip(('a', 'b'), zip(*[pair_of(r, df) for r in d.itertuples(index=False)])))) if len(d) else d   # 21.11: пара из купона поста
    d = d[d.a != ''] if len(d) else d
    tdf = T.load()
    bad = tdf.uid.map(lambda u: u.startswith('tg:') and u.count(':') >= 3 and not u.split(':')[2].isdigit())
    if bad.any():                                                 # 21.11: старый формат uid купона (tg:src:slip:…) — без номера поста
        tdf = tdf[~bad].reset_index(drop=True)                    # → переписать в новом (номер поста третьим полем)
        T.save(tdf)
    have = set(tdf.uid)
    same = {(u.split(':')[1], u.split(':')[2], u.split(':')[-1]) for u in tdf.uid if u.startswith('tg:') and u.count(':') >= 3}
    new, nours, nout = [], 0, 0
    for r in d.itertuples(index=False):
        hit = ours(r, fx)
        if hit is None:
            continue
        eid, e, legs = hit
        nours += 1
        if not legs:
            nout += 1
            continue
        if pd.to_datetime(r.post_ts, errors='coerce') >= pd.Timestamp(e['start']):   # опубликован после начала — не прогноз
            continue
        uid = f'tg:{r.src}:{r.post_id}:{r.uid.split(":", 1)[1].replace(":", "_")}:{eid}'   # 21.11: номер поста — третьим полем
        if uid in have or (r.src, str(r.post_id), eid) in same:   # тот же пост на тот же матч уже записан (tips.collect)
            continue
        same.add((r.src, str(r.post_id), eid))
        P = np.asarray(e['P'], float)
        p = T.p_legs(P, legs)
        k = float(r.k) if r.k else None
        new.append(dict(uid=uid, src=r.src, author=T.TG.get(r.src, r.src), post_ts=r.post_ts,
                        seen_ts=now.strftime('%Y-%m-%d %H:%M'), espn_id=eid, comp_name=C.COMPETITIONS.get(e['comp'], [e['comp']])[0],
                        start_utc=e['start'], home=e['home'], away=e['away'], legs=T._enc(legs),
                        label=' + '.join(T.leg_label(lg) for lg in legs), k=f'{k:.2f}' if k else '', p_model=f'{p:.4f}',
                        agree='1' if ((p * k >= 1) if k else (p >= 0.5)) else '0', raw=str(r.pick)[:160], status='', result='', settled=''))
        have.add(uid)
    if new:
        T.save(pd.concat([tdf, pd.DataFrame(new)], ignore_index=True))
    log.append(f'👥 каперы → наши матчи: на наши матчи {nours} (рынок вне модели {nout}), в журнал +{len(new)}, '
               f'журнал {len(tdf) + len(new)} · матриц {len(fx)}')
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
