"""🧠 Самообучение — журнал (лист learning) и отчёт для кнопки «Аналитика → 🧠 Самообучение». Общий для обоих ботов
(копия — football/learning.py). Решение пользователя 08.11.2026: система учится и развивается сама, пользователь время от
времени смотрит историю, результаты и достижения.

Записи (kind): версия — новая версия модели с цифрами проверки; переобучение — итог переобучения; калибровка — k по рынкам;
ступень — достигнута ступень точности (нижняя граница 95% на последних 100 ≥ ступени); внедрено — улучшение системы;
неделя — итог еженедельного аудита (пишет запланированная задача Claude; облако пересылает в Telegram); тревога — 🚨/⛔;
вопрос — вопрос аудита пользователю (файлы не пришли — проводить ли аудит), тоже в Telegram; ответ — ответ пользователя (пишет чат разработки).
"""
from __future__ import annotations

import pandas as pd

import config as C
import journal as J
import notify

COLS = ['when', 'kind', 'title', 'text', 'sent']
ICON = {'версия': '🧬', 'переобучение': '🔁', 'калибровка': '🎯', 'ступень': '🏆', 'внедрено': '🚀', 'неделя': '📅', 'тревога': '🚨',
        'вопрос': '❓', 'ответ': '💬'}


def _now() -> str:
    return (pd.Timestamp.now(tz='UTC').tz_localize(None) + pd.Timedelta(hours=C.TZ_OFFSET_HOURS)).strftime('%Y-%m-%d %H:%M')


def when_str(x) -> str:
    """Время записи строкой «ГГГГ-ММ-ДД ЧЧ:ММ» (Таблица могла сохранить дату числом-серийником)."""
    try:
        v = float(x)
        if 20000 < v < 80000:
            return (pd.Timestamp('1899-12-30') + pd.Timedelta(days=v)).strftime('%Y-%m-%d %H:%M')
    except (TypeError, ValueError):
        pass
    return str(x)


def add(store: J.Store, kind: str, title: str, text: str = '') -> None:
    store.append('learning', [dict(when=_now(), kind=kind, title=title, text=text, sent='')])


def backfill(store: J.Store, rows: list[tuple[str, str, str, str]]) -> int:
    """Начальная история (если журнал пуст): [(когда, вид, заголовок, текст)]."""
    df = store.read('learning')
    if len(df):
        return 0
    store.append('learning', [dict(when=w, kind=k, title=t, text=x, sent='old') for w, k, t, x in rows])
    return len(rows)


def version(store: J.Store, rt: dict, ver: int, title: str, text: str) -> str:
    """Запись о новой версии модели — один раз на версию."""
    if rt.get('model_version') == ver:
        return ''
    if rt.get('model_version') is not None:
        add(store, 'версия', title, text)
    rt['model_version'] = ver
    return f'журнал: версия модели {ver}'


def steps(store: J.Store, rt: dict, R, af: pd.DataFrame, ld: pd.DataFrame, dp: pd.DataFrame, sport: str) -> list[str]:
    """Новые достигнутые ступени точности (исход, линии, ставка дня) → записи «ступень»."""
    out = []
    M, L = R.matches(af), R.lines(ld)
    D = dp[dp.result.astype(str).isin(['W', 'L'])] if len(dp) else dp
    data = {'исход': (M.hit.values if len(M) else [], C.STEPS['outcome']),
            'линии': (L.won.values if len(L) else [], C.STEPS['lines']),
            'ставка дня': ((D.result.astype(str) == 'W').astype(int).values if len(D) else [], C.STEPS['daypick'])}
    have = rt.setdefault('steps', {})
    for name, (hits, ladder) in data.items():
        if len(hits) < 30:
            continue
        _, reached = R.step_target(pd.Series(hits).values, ladder)
        if reached is not None and (have.get(name) is None or reached > have[name]):
            if have.get(name) is not None or reached > ladder[0]:
                add(store, 'ступень', f'{sport}: {name} — ступень {reached:g}%'.replace('.', ','),
                    f'нижняя граница 95% на последних 100 ≥ {reached:g}% ({int(sum(hits[-100:]))}/{len(hits[-100:])})')
                out.append(f'достижение: {name} {reached:g}%')
            have[name] = reached
    return out


def to_telegram(store: J.Store, table_fn=None) -> int:
    """Новые итоги недели, «внедрено» и вопросы от задачи Claude — в Telegram карточками (раз), отметка sent.
    table_fn(d0, d1) → HTML-таблица недели (report.build) — для карточки «неделя»."""
    df = store.read('learning')
    if not len(df):
        return 0
    todo = df[df.kind.astype(str).isin(['неделя', 'внедрено', 'вопрос']) & df.sent.astype(str).str.strip().isin(['', 'nan'])]
    if todo.empty:
        return 0
    notify.send_blocks([card(r, table_fn) for r in todo.itertuples()])
    df.loc[todo.index, 'sent'] = J.now_utc()
    store.update('learning', df.loc[todo.index], ['sent'])
    return len(todo)


def report(store: J.Store, rt: dict, title: str, ladders: dict | None = None) -> str:
    """Отчёт «🧠 Самообучение»: сейчас (версия, калибровка, ступени), достижения, история."""
    df = store.read('learning')
    e = notify.esc
    out = [f'🧠 <b>{e(title)}</b>']
    if len(df):
        df = df.assign(when=df.when.map(when_str)).sort_values('when', kind='stable')
        ver = df[df.kind.astype(str) == 'версия'].tail(1)
        if len(ver):
            out.append(f"🧬 Модель: <b>{e(str(ver.title.iloc[0]))}</b>\n{e(str(ver.text.iloc[0]))}")
        rt_ = df[df.kind.astype(str) == 'переобучение'].tail(1)
        if len(rt_):
            out.append(f"🔁 Последнее переобучение {e(str(rt_.when.iloc[0]))}: {e(str(rt_.text.iloc[0]))}")
    k = rt.get('kcal') or {}
    if k:
        out.append('🎯 Калибровка k: ' + ', '.join(f'{m} {v[0]:.2f} (n={v[1]})'.replace('.', ',') for m, v in k.items()))
    st = rt.get('steps') or {}
    lad = ladders or {'исход': C.STEPS['outcome'], 'линии': C.STEPS['lines'], 'ставка дня': C.STEPS['daypick']}
    rows = []
    for name, ladder in lad.items():
        r = st.get(name)
        nxt = [s for s in ladder if r is None or s > r]
        rows.append((f"{name}: {'достигнута ' + format(r, 'g') + '%' if r is not None else 'ступеней пока нет'}"
                     f" → следующая {nxt[0] if nxt else ladder[-1]:g}%").replace('.', ','))
    out.append('🏆 Ступени: ' + '; '.join(rows))
    if len(df):
        ach = df[df.kind.astype(str).isin(['версия', 'ступень', 'внедрено'])].tail(10)
        if len(ach):
            out.append('🚀 Достижения:\n' + '\n'.join(f"{ICON.get(str(r.kind), '•')} {e(str(r.when)[:10])} · {e(str(r.title))}"
                                                     for r in ach.iloc[::-1].itertuples()))
        wk = df[df.kind.astype(str) == 'неделя'].tail(1)
        if len(wk):
            out.append(f"📅 Итог недели ({e(str(wk.when.iloc[0])[:10])}): {e(str(wk.text.iloc[0]))[:1500]}")
        hist = df.tail(15).iloc[::-1]
        out.append('🗂 История:\n<blockquote expandable>' + '\n'.join(
            f"{ICON.get(str(r.kind), '•')} {e(str(r.when))} · {e(str(r.title))}" for r in hist.itertuples()) + '</blockquote>')
    else:
        out.append('Журнал пока пуст — записи появятся после ближайшего переобучения.')
    return '\n\n'.join(out)

# ---- структурированный вид записей аудита (решение пользователя 10.11.2026: красиво, по разделам, итоги аудита — в ботах)
# Разметка текста записи «неделя» / «внедрено» (её пишет аудит): строки «[раздел]» — заголовки, «- пункт» — пункты.
SECTIONS = {'данные': '📥 Данные', 'итоги': '📊 Итоги', 'выводы': '🔎 Выводы', 'охват': '🧮 Охват', 'сделано': '🛠 Сделано',
            'внедрено': '🚀 Внедрено', 'дальше': '➡️ Дальше', 'предложения': '💡 Предложения'}


def fmt_text(text: str) -> str:
    """Разметка аудита → HTML Telegram: разделы жирным с иконкой, пункты «•». Старый сплошной текст — по «; » в пункты."""
    e = notify.esc
    raw = str(text or '').replace('\\n', '\n').strip()
    lines = [x.strip() for x in raw.split('\n') if x.strip()]
    if len(lines) <= 1 and '[' not in raw:                       # старый формат: одна строка через «; »
        parts = [p.strip() for p in raw.replace('. ', '; ').split('; ') if p.strip()]
        return '\n'.join(f'• {e(p)}' for p in parts)
    out = []
    for x in lines:
        if x.startswith('[') and x.endswith(']'):
            k = x[1:-1].strip()
            out.append(('\n' if out else '') + f'<b>{SECTIONS.get(k.lower(), k)}</b>')
        elif x.startswith(('- ', '• ')):
            out.append(f'• {e(x[2:].strip())}')
        else:
            out.append(e(x))
    return '\n'.join(out)


def week_dates(title: str):
    """«Итог недели 02.11–08.11» → (date 02.11, date 08.11) текущего года (декабрь → январь — прошлый год) или None."""
    import re
    m = re.search(r'(\d{2})\.(\d{2})\s*[–-]\s*(\d{2})\.(\d{2})', str(title))
    if not m:
        return None
    now = pd.Timestamp.now()
    y1 = now.year if int(m.group(4)) <= now.month else now.year - 1
    y0 = y1 if int(m.group(2)) <= int(m.group(4)) else y1 - 1
    return (pd.Timestamp(y0, int(m.group(2)), int(m.group(1))).date(), pd.Timestamp(y1, int(m.group(4)), int(m.group(3))).date())


def card(r, table_fn=None) -> str:
    """Запись журнала карточкой: заголовок, (для «неделя») таблица недели, разделы."""
    kind = str(r.kind)
    head = f"{ICON.get(kind, '•')} <b>{notify.esc(str(r.title))}</b>"
    if kind == 'неделя':
        head = f"📅 <b>Аудит · {notify.esc(str(r.title))}</b>\n<i>{notify.esc(when_str(r.when))}</i>"
    parts = [head]
    if kind == 'неделя' and table_fn is not None:
        wd = week_dates(r.title)
        if wd:
            try:
                parts.append(table_fn(*wd))
            except Exception:
                pass
    body = fmt_text(r.text)
    if body:
        parts.append(body)
    return '\n\n'.join(parts)


def audit_report(store: J.Store, table_fn=None) -> str:
    """Раздел «Аналитика → 🔎 Аудит»: последний итог недели (таблица + выводы) и последние внедрения."""
    df = store.read('learning')
    if not len(df):
        return '🔎 <b>Аудит</b>\nАудитов пока не было.'
    df = df.assign(when=df.when.map(when_str)).sort_values('when', kind='stable')
    wk = df[df.kind.astype(str) == 'неделя'].tail(1)
    out = [card(wk.iloc[0], table_fn)] if len(wk) else ['🔎 <b>Аудит</b>\nИтогов недели пока нет.']
    imp = df[df.kind.astype(str) == 'внедрено'].tail(5).iloc[::-1]
    if len(imp):
        out.append('🚀 <b>Последние внедрения</b>\n' + '\n'.join(
            f"• {notify.esc(str(r.when)[:10])} — {notify.esc(str(r.title))}" for r in imp.itertuples()))
    return '\n\n'.join(out)
