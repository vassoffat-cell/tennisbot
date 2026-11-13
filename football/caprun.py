"""👥 Каперы — каждый час (решение пользователя 11.11.2026 23:53: «продолжаешь каждый час следить за каперами»).

Лёгкий прогон (.github/workflows/cappers.yml, :22 каждого часа) — без прогноза и сообщений:
  1. Все матчи каперов по табло ESPN (tipswide) и книга прогнозов всех каналов и видов спорта (tipsbook): посты, купоны
     Green Line (OCR), отметки ✅/❌; итоги рассчитанных.
  2. Свежая ставка капера (пост за 3 ч) → сразу запуск прогона бота её вида спорта (футбол → football.yml, теннис и хоккей →
     daily.yml): ставка дня пересчитывает 🔥/⚔️ и по правилам dpcore правится / получает новую линию в пределах часа.
     Не запускается, если плановый прогон бота и так начнётся в ближайшие 30 мин; не больше MAX_EXTRA в сутки на бота.
Карточки каперов в чат не идут (CAPPER_ALERTS = False); итоги каперов — в отчётах (карточка «👥 Каперы», строки 🔥/⚔️).
"""
from __future__ import annotations

import datetime as dt
import os

import pandas as pd

import runtime
import tipsbook
import tipswide

ROUTE = {'football': 'football.yml', 'tennis': 'daily.yml', 'hockey': 'daily.yml'}
SCHED_MIN = {'daily.yml': 7, 'football.yml': 37}       # плановые прогоны: :07 / :37 нечётных часов UTC
MAX_EXTRA = 8                                         # внеплановых запусков в сутки на бота (кредиты API, квота таблиц)
SOON = pd.Timedelta(minutes=30)


def _next_sched(now: pd.Timestamp, minute: int) -> pd.Timestamp:
    t = now.floor('h') + pd.Timedelta(minutes=minute)
    while t < now or t.hour % 2 == 0:
        t += pd.Timedelta(hours=1)
    return t


def main() -> None:
    rt = runtime.load()
    log: list[str] = []
    before = set(tipsbook.load().uid)
    for name, fn in (('все матчи', lambda: (tipswide.collect(log), tipswide.settle(log))),
                     ('книга', lambda: (tipsbook.run(rt, log), tipswide.settle(log)))):
        try:
            fn()
        except Exception as e:
            log.append(f'👥 {name}: сбой {e.__class__.__name__}: {e}')
    now = pd.Timestamp.now(tz='UTC').tz_localize(None)
    df = tipsbook.load()
    new = df[~df.uid.isin(before)]
    fresh = new[pd.to_datetime(new.post_ts, errors='coerce') >= now - pd.Timedelta(hours=3)]
    day = str(now.date())
    cnt = rt.get('cap_extra') if (rt.get('cap_extra') or {}).get('day') == day else {'day': day}
    dispatch = []
    for wf in sorted({ROUTE[s] for s in fresh.sport if s in ROUTE}):
        nxt = _next_sched(now, SCHED_MIN[wf])
        if nxt - now <= SOON:
            log.append(f'👥 {wf}: плановый прогон через {int((nxt - now).total_seconds() // 60)} мин — без доп. запуска')
        elif int(cnt.get(wf, 0)) >= MAX_EXTRA:
            log.append(f'👥 {wf}: лимит доп. запусков за сутки ({MAX_EXTRA})')
        else:
            dispatch.append(wf)
            cnt[wf] = int(cnt.get(wf, 0)) + 1
    rt['cap_extra'] = cnt
    if os.getenv('CAP_TEST'):                                   # 14.11: тест вида уведомления (последние прогнозы, в оба бота)
        try:
            tipsbook.alert(rt, tipsbook.load(), log, test=True)
        except Exception as e:
            log.append(f'👥 тест: сбой {e.__class__.__name__}: {e}')
    try:                                                         # уведомления ушли → заглушка 🔒 (превью скрыто)
        import notify
        import config as C
        log.append(f'🔒 заглушка: {notify.cover(C.DATA / "cover.json")}')
    except Exception as e:
        log.append(f'🔒 заглушка: сбой {e.__class__.__name__}')
    runtime.save(rt)
    log.append(f'👥 новых ставок {len(new)} (свежих {len(fresh)}: '
               + ', '.join(f'{s} {n}' for s, n in fresh.sport.value_counts().items()) + f'); запуск: {" ".join(dispatch) or "нет"}')
    print('\n'.join(log))
    out = os.getenv('GITHUB_OUTPUT')
    if out:
        with open(out, 'a', encoding='utf-8') as f:
            f.write(f'dispatch={" ".join(dispatch)}\n')


if __name__ == '__main__':
    main()
