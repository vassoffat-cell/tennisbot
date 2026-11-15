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
SCHED_MIN = {'daily.yml': 7, 'football.yml': 37}       # плановые прогоны: :07 / :37
SCHED_H = {'daily.yml': set(range(1, 24, 2)),                       # часы UTC (14.11: теннис/хоккей круглые сутки, футбол ночью реже)
           'football.yml': {3, 5, 7, 9, 11, 13, 15, 17, 19}}
MAX_EXTRA = 8                                         # внеплановых запусков в сутки на бота (кредиты API, квота таблиц)
SOON = pd.Timedelta(minutes=30)


def _next_sched(now: pd.Timestamp, minute: int, hours: set[int] | None = None) -> pd.Timestamp:
    hours = hours or set(range(1, 24, 2))
    t = now.floor('h') + pd.Timedelta(minutes=minute)
    while t < now or t.hour not in hours:
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
        nxt = _next_sched(now, SCHED_MIN[wf], SCHED_H.get(wf))
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
    try:                                                         # 15.11: 🔒 контрольная точка каперов в оба бота, каждый час
        import json
        import notify
        import config as C
        leg = C.DATA / 'cover.json'                             # разово: старая заглушка каперов в «Заметки Т» (ключ 'tennis')
        lo = json.loads(leg.read_text()) if leg.exists() else {}
        if 'tennis' in lo:
            for chat, mid in lo.pop('tennis'):
                try:
                    notify._call('deleteMessages', {'chat_id': int(chat), 'message_ids': [int(mid)]}, bot='tennis')
                except Exception:
                    pass
            leg.write_text(json.dumps(lo))
        n_now = now.to_pydatetime()
        last = rt.get('chk_cap')
        prev = notify.slot(n_now - dt.timedelta(minutes=45), 22, None, back=True)
        missed = bool(last) and dt.datetime.fromisoformat(str(last)) < prev - dt.timedelta(minutes=40)
        rt['chk_cap'] = n_now.isoformat(timespec='seconds')
        nxt = notify.slot(n_now + dt.timedelta(minutes=5), 22)
        mk = lambda n: notify.status('👥 каперы', 'новых нет', n, nxt, missed)
        log.append(f'🔒 контрольная точка: {notify.cover(C.DATA / "cover_cap.json", {"main": mk, "tennis": mk})}')
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
