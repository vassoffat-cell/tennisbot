"""💤 Умный шлюз резервов (решение пользователя 28.11 01:47, CONTROL Р10).

Резервные прогоны GitHub (футбол :37, теннис+хоккей :07 — каждые 2 ч, Ц19) остаются как страховка, но пропускаются, если делать
нечего. Каждый полный прогон пишет подсказку <data>/idle.json: {made, until, why} — время (UTC), раньше которого резерву нечего делать.
Шлюз workflow (bash, до checkout) читает её через GitHub API; подсказка старше STALE_H или битая — резерв идёт как обычно.

Что считается делом (раньше него — пропуск):
  • окно плана по матчам (T−3 ч / T−40 мин) — резерв страхует planTick_;
  • ⭐1 объявлена в этом прогоне — следующий прогон добирает ⭐2/⭐3 (С23а) → не пропускать;
  • футбол: итог матча — начало + 2 ч 05 мин; ночные итоги (00:00–08:00 Ташкента) ждут до 08:00 (отчёт — 11:15);
  • хоккей: обновление цен НХЛ (кэш 10 ч) до последнего матча — после него могут появиться 🎯;
  • не реже раза в HORIZON_H — свежие данные и сторожа.
Ключевые слоты (11:15/11:30/12:15/12:30, пн 17:00), репетиция 21:13, planTick_, запуски каперов и ручные — шлюз не трогает.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Iterable

FMT = '%Y-%m-%d %H:%M'
TZ_H = 5                      # Ташкент
HORIZON_H = 6                 # хотя бы один полный прогон за 6 ч
STALE_H = 8                   # подсказка старше — шлюз её не слушает (bash в workflow)
NIGHT = (0, 8)                # футбол: итоги матчей, закончившихся ночью, — к 08:00 (отчёт 11:15)
ANNOUNCE = re.compile(r'ставка дня: объявлена|⭐🏒 \d\d\.\d\d: ⭐1 |⭐1 появилась')


def _ts(x) -> dt.datetime | None:
    try:
        import pandas as pd
        t = pd.Timestamp(x)
        if t is pd.NaT or t != t:
            return None
        if t.tzinfo is not None:
            t = t.tz_convert('UTC').tz_localize(None)
        return t.to_pydatetime()
    except Exception:
        return None


def _morning(t: dt.datetime) -> dt.datetime:
    """Итог, готовый ночью по Ташкенту, переносится на 08:00 (UTC 03:00)."""
    loc = t + dt.timedelta(hours=TZ_H)
    if NIGHT[0] <= loc.hour < NIGHT[1]:
        loc = loc.replace(hour=NIGHT[1], minute=0, second=0, microsecond=0)
    return loc - dt.timedelta(hours=TZ_H)


def hockey_prices(folder: Path, starts: Iterable, now: dt.datetime, refresh_h: float = 10.0) -> list[dt.datetime]:
    """Когда кэш цен НХЛ (The Odds API, 10 ч) обновится до последнего матча ближайших суток — после этого возможны новые 🎯."""
    try:
        files = sorted(Path(folder, 'odds').glob('hockey_2*.json'))
        got = json.loads(files[-1].read_text(encoding='utf-8')).get('sports', {}).get('icehockey_nhl') if files else None
        t0 = _ts(got)
    except Exception:
        t0 = None
    if t0 is None:
        return []
    due = t0 + dt.timedelta(hours=refresh_h)
    st = [t for t in (_ts(x) for x in starts) if t is not None and now < t < now + dt.timedelta(hours=30)]
    return [max(due, now)] if st and due < max(st) else []


def football_settle(starts: Iterable, settled: Iterable, now: dt.datetime) -> list[dt.datetime]:
    """Когда появятся итоги наших матчей: начало + 2 ч 05 мин (ночью — 08:00). Просроченный — повтор через 2 ч."""
    out = []
    for s, ok in zip(starts, settled):
        t = _ts(s)
        if t is None or str(ok).strip().lower() not in ('', 'nan', 'none'):      # settled заполнен — итог уже есть
            continue
        end = t + dt.timedelta(minutes=125)
        if end < now - dt.timedelta(hours=30):               # старые без итога (перенос, ошибка табло) — не держат резервы
            continue
        out.append(_morning(end if end > now else now + dt.timedelta(hours=2)))
    return out


def hint(folder: Path, rt: dict, now: dt.datetime, needs: Iterable, log: list[str]) -> str:
    """Записать подсказку резервам. needs — моменты (UTC), когда прогону будет что делать."""
    why = 'запас 6 ч'
    until = now + dt.timedelta(hours=HORIZON_H)
    if any(ANNOUNCE.search(str(x)) for x in log):
        until, why = now, '⭐1 объявлена — следующий прогон добирает ⭐2/⭐3'
    else:
        for t, w in needs:
            t = _ts(t)
            if t is not None and t > now - dt.timedelta(minutes=1) and t < until:
                until, why = t, w
    for s in rt.get('plan_at', []):                          # окна плана (UTC) — резерв страхует planTick_
        t = _ts(s)
        if t is not None and now < t < until:
            until, why = t, 'окно плана по матчам'
    rt['idle_prev'] = rt.get('idle_until')                  # для 🔒: был ли прошлый резерв пропущен «делать нечего»
    rt['idle_until'] = until.strftime(FMT)
    rec = dict(made=now.strftime(FMT), until=until.strftime(FMT), why=why)
    try:
        (Path(folder) / 'idle.json').write_text(json.dumps(rec, ensure_ascii=False), encoding='utf-8')
    except Exception as ex:
        return f'💤 резервы: сбой подсказки {ex.__class__.__name__}'
    loc = until + dt.timedelta(hours=TZ_H)
    return f'💤 резервы: делать нечего до {loc:%d.%m %H:%M} ({why})' if until > now else f'💤 резервы: следующий нужен сразу ({why})'


def after(rt: dict, t: dt.datetime) -> dt.datetime:
    """Для «далее ЧЧ:ММ» в 🔒: резерв раньше idle_until пропускается — следующий настоящий прогон не раньше него."""
    u = _ts(rt.get('idle_until'))
    return max(t, u) if u else t


def was_idle(rt: dict, slot_t: dt.datetime) -> bool:
    """Пропуск резерва slot_t шлюзом «делать нечего» — не ⚠️ «прошлая проверка пропущена»."""
    u = _ts(rt.get('idle_prev'))
    return bool(u and slot_t < u)
