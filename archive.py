"""🗂 Архив отчётов для меню ботов (ТЗ-006, решение пользователя 14.11.2026 10:08: «действуй»).

Лист archive (таблица своего бота): одна строка — один готовый отчёт за срез.
  key = '<вид>|<раздел>|<срез>|<период>'
    вид:    fb — футбол, tn — теннис, hk — хоккей (теннисный бот: tn и hk);
    раздел: rep — ① отчёт · cap — ② каперы · dp — ③ ставки дня · hist — ставки дня даты (История) ·
            learn — 🧠 самообучение · audit — 🔎 аудит;
    срез:   d — сутки (ГГГГ-ММ-ДД) · w — неделя (понедельник ГГГГ-ММ-ДД) · m — месяц (ГГГГ-ММ) · y — год (ГГГГ).
  text = JSON-список готовых HTML-блоков (≤ 3800 знаков каждый, теги целые) — Apps Script отправляет как есть.
Суточные и недельные — тем же текстом, что ушёл в чат; месяц и год — ёмко (только таблица), пересчёт раз в сутки.
Apps Script меню (год → раздел → срез → период) строит только по ключам листа: облако на нажатие не запускается.
"""
from __future__ import annotations

import datetime as dt
import html
import json
from typing import Callable, Iterable

import pandas as pd

COLS = ['key', 'sport', 'sec', 'cut', 'period', 'title', 'text', 'updated', 'img']   # 27.11: img — file_id картинок (В9)
MAX_CELL = 45000                       # лимит ячейки Google Таблиц — 50 000 знаков
SEC_RU = {'rep': '📈 Отчёт', 'cap': '👥 Каперы', 'dp': '⭐ Ставки дня', 'hist': '🗂 История', 'learn': '🧠 Самообучение',
          'audit': '🔎 Аудит'}


def monday(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


def put(store, items: Iterable[tuple], split_fn: Callable[[str], list[str]] | None = None) -> int:
    """items: (вид, раздел, срез, период, заголовок, блоки | текст). Новая строка или замена по ключу."""
    now = (pd.Timestamp.now(tz='UTC').tz_localize(None) + pd.Timedelta(hours=5)).strftime('%Y-%m-%d %H:%M')
    rows = []
    for sp, sec, cut, per, title, blocks in items:
        bl = [blocks] if isinstance(blocks, str) else [b for b in blocks if b]
        if not bl:
            continue
        if split_fn:
            bl = [p for b in bl for p in split_fn(b)]
        js = json.dumps(bl, ensure_ascii=False)
        while len(js) > MAX_CELL and len(bl) > 1:
            bl = bl[:-1]
            js = json.dumps(bl + ['…'], ensure_ascii=False)
        rows.append(dict(key=f'{sp}|{sec}|{cut}|{per}', sport=sp, sec=sec, cut=cut, period=str(per), title=title, text=js, updated=now,
                         img=_img(bl)))
    if not rows:
        return 0
    df = store.read('archive')
    have = {str(k): i for i, k in zip(df.index, df.key)} if len(df) else {}
    upd, new = [], []
    for r in rows:
        if r['key'] in have:
            i = have[r['key']]
            img = r['img'] or (str(df.at[i, 'img']) if 'img' in df.columns else '')   # картинка уже была — не терять
            df.loc[i, ['title', 'text', 'updated', 'img']] = [r['title'], r['text'], r['updated'], img]
            upd.append(i)
        else:
            new.append(r)
    if upd:
        store.update('archive', df.loc[sorted(set(upd))], ['title', 'text', 'updated', 'img'])
    if new:
        store.append('archive', new)
    return len(rows)


def _img(blocks: list[str]) -> str:
    """27.11 (В9): file_id картинок, если этот отчёт в этом прогоне ушёл картинкой (notify.CARD_IDS по заголовку) — для меню."""
    try:
        import notify
        ids = notify.CARD_IDS.get(notify.card_key(notify.card_title(blocks)))
        return json.dumps(ids) if ids else ''
    except Exception:
        pass
    return ''


def learn_text(sport: str, title: str, d0: dt.date, d1: dt.date, rules: bool) -> list[str]:
    """🧠 Уроки самообучения периода (история lessons.json) + действующие правила (для месяца и года)."""
    try:
        import selflearn as SL
        st = SL.load(sport)
        rl = SL.rules_line(sport)
    except Exception:
        st, rl = {}, ''
    e = html.escape
    hist = [h for h in st.get('history', []) if d0.isoformat() <= str(h.get('date', '')) <= d1.isoformat()]
    out = [f'🧠 <b>{e(title)}</b>']
    out += [f"{'📘' if h['sign'] == '+' else '↩️'} {e(h['date'][8:10] + '.' + h['date'][5:7])} {e(h['text'])}" for h in hist[-30:]]
    if not hist:
        out.append('Новых правил за период нет.')
    if rules:
        out.append(f'Действует: {e(rl)}' if rl else 'Действующих поправок нет.')
    return ['\n'.join(out)]


def audit_text(store, title: str, d0: dt.date, d1: dt.date, card_fn) -> list[str]:
    """🔎 Карточки еженедельного аудита (лист learning, вид «неделя»), датированные в периоде."""
    try:
        df = store.read('learning')
    except Exception:
        return []
    if not len(df):
        return []
    w = df[df.kind.astype(str) == 'неделя'].copy()
    when = pd.to_datetime(w.when.astype(str).str[:10], errors='coerce')
    w = w[(when >= pd.Timestamp(d0)) & (when <= pd.Timestamp(d1 + dt.timedelta(days=7)))]
    if not len(w):
        return [f'🔎 <b>{html.escape(title)}</b>\nКарточек аудита за период нет.']
    return [card_fn(r) for r in w.itertuples()]


def periods(day: dt.date) -> dict[str, tuple[dt.date, dt.date, str]]:
    """Текущие месяц и год до дня day включительно: {'m': (d0, d1, 'ГГГГ-ММ'), 'y': (d0, d1, 'ГГГГ')}."""
    return {'m': (day.replace(day=1), day, f'{day:%Y-%m}'), 'y': (day.replace(month=1, day=1), day, f'{day:%Y}')}


def std_items(sp: str, sport: str, ru: str, icon: str, R, store, af, ld, dp, kcal, off, days: list[dt.date],
              weeks: list[tuple[dt.date, dt.date]], month_year: dt.date | None, card_fn=None, log: list[str] | None = None) -> list[tuple]:
    """Теннис / футбол: тексты архива. days — сутки (d), weeks — (понедельник, конец), month_year — день для месяца и года.
    R — модуль report вида спорта (build, week, dp_report, DP_LIST); каперы — caprep; уроки — selflearn."""
    import caprep
    out: list[tuple] = []

    def safe(name, fn):
        try:
            return fn()
        except Exception as ex:
            if log is not None:
                log.append(f'🗂 архив {sp} {name}: сбой {ex.__class__.__name__}: {ex}')
            return []

    for d in days:
        t = f'{d:%d.%m.%Y}'
        out.append((sp, 'rep', 'd', d.isoformat(), f'{icon} {ru} · отчёт · {t}', safe('rep', lambda: R.build(
            af, ld, dp, f'📈 {ru} · отчёт · {t}', kcal=kcal, off=off, daily_day=d, split=True))))
        out.append((sp, 'cap', 'd', d.isoformat(), f'👥 {ru} · каперы · {t}', safe('cap', lambda: caprep.blocks(sport, d, d, t))))
        out.append((sp, 'dp', 'd', d.isoformat(), f'{icon} {ru} · ставки дня · {t}',
                    safe('dp', lambda: R.dp_report(dp, f'{icon} {ru} · ставки дня · {t}', d))))
        out.append((sp, 'hist', 'd', d.isoformat(), f'🗂 {ru} · ставки дня · {t}',
                    safe('hist', lambda: [R.DP_LIST(dp, d) or f'🗂 <b>{ru} · ставки дня · {t}</b>\nСтавок дня не было.'])))
        out.append((sp, 'learn', 'd', d.isoformat(), f'🧠 {ru} · самообучение · {t}',
                    safe('learn', lambda: learn_text(sport, f'{ru} · самообучение · {t}', d, d, False))))
    spans = [(m0, m1, 'w', m0.isoformat(), f'неделя {m0:%d.%m}–{m1:%d.%m}') for m0, m1 in weeks]
    if month_year is not None:
        for cut, (d0, d1, per) in periods(month_year).items():
            spans.append((d0, d1, cut, per, f'{d0:%m.%Y}' if cut == 'm' else f'{d0:%Y} год'))
    for d0, d1, cut, per, lab in spans:
        def rep():
            a_, l_, d_ = R.week(af, ld, dp, d0, d1)
            b = R.build(a_, l_, d_, f'📈 {ru} · {lab}', kcal=kcal, off=off, period='за период', split=True)
            return b if cut == 'w' else b[:1]                     # месяц / год — ёмко: только таблица
        out.append((sp, 'rep', cut, per, f'{icon} {ru} · отчёт · {lab}', safe('rep', rep)))
        out.append((sp, 'cap', cut, per, f'👥 {ru} · каперы · {lab}', safe('cap', lambda: caprep.blocks(sport, d0, d1, lab))))

        def dpr():
            _, _, d_ = R.week(af.iloc[:0], ld.iloc[:0], dp, d0, d1)
            b = R.dp_report(d_, f'{icon} {ru} · ставки дня · {lab}', d1, d0)
            return b if cut == 'w' else b[:1]
        out.append((sp, 'dp', cut, per, f'{icon} {ru} · ставки дня · {lab}', safe('dp', dpr)))
        out.append((sp, 'learn', cut, per, f'🧠 {ru} · самообучение · {lab}',
                    safe('learn', lambda: learn_text(sport, f'{ru} · самообучение · {lab}', d0, d1, cut != 'w'))))
        if card_fn is not None:
            out.append((sp, 'audit', cut, per, f'🔎 {ru} · аудит · {lab}',
                        safe('audit', lambda: audit_text(store, f'{ru} · аудит · {lab}', d0, d1, card_fn))))
    return out


def backfill_plan(today: dt.date, start: dt.date) -> tuple[list[dt.date], list[tuple[dt.date, dt.date]]]:
    """Все сутки с start по вчера и все недели (пн–вс, текущая — по вчера)."""
    y = today - dt.timedelta(days=1)
    days = [start + dt.timedelta(days=k) for k in range((y - start).days + 1)]
    weeks, m0 = [], monday(start)
    while m0 <= y:
        weeks.append((m0, min(m0 + dt.timedelta(days=6), y)))
        m0 += dt.timedelta(days=7)
    return days, weeks
