"""ТЗ-007 шаг 2 (17.11.2026): прогоны по расписанию матчей (общий модуль: теннис + хоккей и футбол).

Каждый полный прогон бота пересчитывает план на 30 ч вперёд. Для каждого матча — два окна:
  • «T−3ч»  — с 3 ч до 1 ч 50 мин до начала: линии и ⭐ по свежей цене рынка;
  • «T−40м» — с 60 до 25 мин до начала: цена закрытия (CLV), последняя правка ⭐ до старта.
Окна, которые пересекаются, сливаются в ОДИН прогон (жадное покрытие интервалов: минимум запусков, каждое окно покрыто).
Окно, внутри которого идёт текущий прогон, уже выполнено и в план не попадает.

План → лист plan таблицы бота (at, hi, why, made; время UTC «ГГГГ-ММ-ДД ЧЧ:ММ»). Apps Script бота (триггер tick, каждые 5 мин)
запускает прогон, когда наступило at и прогона бота после at − 15 мин ещё нет. GitHub cron — резерв.
История (3 суток) — <data>/plan.json: health.py сверяет «окно плана → был прогон».
Флаг PLAN_ON — откат одним переключателем; LIVE — «далее ЧЧ:ММ» в 🔒 берётся из плана (включать после проверки Apps Script).
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Iterable

import pandas as pd

PLAN_ON = True
LIVE = False                    # 🔒 «далее» по плану — после того, как Apps Script с planTick_ установлен и проверен
WINDOWS = ((180, 110, 'T−3ч'), (60, 25, 'T−40м'))    # минут до начала: окно [начало − a, начало − b]
HORIZON_H = 30
KEEP_H = 72
COLS = ['at', 'hi', 'why', 'made']
FMT = '%Y-%m-%d %H:%M'


def _now() -> pd.Timestamp:
    return pd.Timestamp.now(tz='UTC').tz_localize(None).floor('min')


def _ts(x) -> pd.Timestamp | None:
    t = pd.to_datetime(x, errors='coerce')
    if t is None or pd.isna(t):
        return None
    return t.tz_convert(None) if t.tzinfo else t


def windows(starts: Iterable, now: pd.Timestamp) -> list[tuple[pd.Timestamp, pd.Timestamp, str]]:
    """Будущие окна (lo, hi, метка) по временам начала матчей (UTC). Уже прошедшие и идущие сейчас — не нужны."""
    out, seen = [], set()
    for x in starts:
        s = _ts(x)
        if s is None or not (now < s <= now + pd.Timedelta(hours=HORIZON_H)):
            continue
        for a, b, lab in WINDOWS:
            lo, hi = s - pd.Timedelta(minutes=a), s - pd.Timedelta(minutes=b)
            if lo <= now or (lo, hi) in seen:          # окно уже идёт — его выполняет этот прогон
                continue
            seen.add((lo, hi))
            out.append((lo, hi, lab))
    return out


def groups(ws: list[tuple[pd.Timestamp, pd.Timestamp, str]]) -> list[dict]:
    """Минимум прогонов, покрывающих все окна: сортировка по концу окна, точка — конец первого непокрытого;
    прогон группы — в самый ранний момент, общий для всех её окон (max lo ≤ min hi)."""
    rem = sorted(ws, key=lambda w: (w[1], w[0]))
    out = []
    while rem:
        p = rem[0][1]                                  # самый ранний конец окна — все окна с началом ≤ p покрыты точкой p
        grp = [w for w in rem if w[0] <= p]
        rem = [w for w in rem if w[0] > p]
        at = max(w[0] for w in grp)
        cnt: dict[str, int] = {}
        for w in grp:
            cnt[w[2]] = cnt.get(w[2], 0) + 1
        out.append(dict(at=at, hi=p, why=' · '.join(f'{k} ×{v}' for k, v in sorted(cnt.items(), reverse=True))))
    return sorted(out, key=lambda g: g['at'])


def _load(folder: Path) -> list[dict]:
    try:
        return json.loads((Path(folder) / 'plan.json').read_text(encoding='utf-8')).get('groups', [])
    except Exception:
        return []


def build(starts: Iterable, folder: Path, now: pd.Timestamp | None = None) -> tuple[list[dict], list[dict]]:
    """(будущие группы, история+будущее для plan.json). История — прошлые группы прежнего плана (at ≤ сейчас), 72 ч."""
    now = now if now is not None else _now()
    fut = [dict(at=g['at'].strftime(FMT), hi=g['hi'].strftime(FMT), why=g['why']) for g in groups(windows(starts, now))]
    old = [g for g in _load(folder) if g.get('at', '') <= now.strftime(FMT)
           and g['at'] >= (now - pd.Timedelta(hours=KEEP_H)).strftime(FMT)]
    keep = {g['at']: g for g in old}
    for g in fut:
        keep[g['at']] = g
    return fut, [keep[k] for k in sorted(keep)]


def next_at(rt: dict, now: dt.datetime) -> dt.datetime | None:
    """Ближайший прогон по плану (UTC, naive) — для «далее ЧЧ:ММ» в 🔒 (только при LIVE)."""
    if not (PLAN_ON and LIVE):
        return None
    for s in rt.get('plan_at', []):
        t = dt.datetime.strptime(s, FMT)
        if t > now:
            return t
    return None


def update(store, rt: dict, folder: Path, starts: Iterable) -> str:
    """Пересчитать план, записать plan.json (всегда) и лист plan (только если план изменился — квота Google)."""
    if not PLAN_ON:
        return '📅 план: выключен (PLAN_ON)'
    now = _now()
    fut, hist = build(starts, folder, now)
    made = now.strftime(FMT)
    Path(folder, 'plan.json').write_text(json.dumps(dict(made=made, groups=hist), ensure_ascii=False, indent=0),
                                         encoding='utf-8')
    sig = [f"{g['at']}|{g['hi']}" for g in fut]
    rt['plan_at'] = [g['at'] for g in fut]
    if sig != rt.get('plan_sig'):
        store.write_all('plan', pd.DataFrame([dict(g, made=made) for g in fut], columns=COLS))
        rt['plan_sig'] = sig
        note = 'лист обновлён'
    else:
        note = 'без изменений'
    off = pd.Timedelta(hours=5)
    nxt = ', '.join(f"{(pd.Timestamp(g['at']) + off):%H:%M}" for g in fut[:6]) or 'нет'
    return f'📅 план: прогонов по матчам {len(fut)} (ближайшие {nxt}) — {note}'
