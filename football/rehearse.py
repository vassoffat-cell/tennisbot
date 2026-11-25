"""Генеральная репетиция футбольных суток (16.11.2026, подготовка к возвращению клубов 21–23.11).

Весь боевой путь прогона football/run.py — ESPN → модель → прогноз суток → кандидаты ставки дня (кф, EV, каперы 🔥/⚔️) —
для БУДУЩИХ суток, без отправок в бот и без записи в таблицы (кэш кф пишется только с флагом odds).
Запуск (облако): football.yml, вход rehearse = «ГГГГ-ММ-ДД» (без кф) или «ГГГГ-ММ-ДД,odds» (кф The Odds API, ≈ 2 кредита на лигу).
Локально сеть к ESPN закрыта — только облако.
"""
from __future__ import annotations

import sys
import time

import pandas as pd

import config as C
import daypick
import dpcore
import fdata
import fespn
import journal as J
import notify
import odds as OD
import report as R
import run as RN
import runtime


def main(arg: str) -> None:
    t0 = time.time()
    day_s, *flags = [x.strip() for x in arg.split(',')]
    day = pd.Timestamp(day_s).date()
    use_odds = 'odds' in flags
    auto = 'auto' in flags                                          # 16.11: плановая репетиция 21:13 — ⚠️ в бот только при проблеме
    send = notify.send_blocks
    pa = getattr(C, 'PICK_AT', (11, 30))                            # 25.11 Р8: ⚽1 — в 11:30, репетиция — как в этот момент
    fake = pd.Timestamp(day) + pd.Timedelta(hours=pa[0] - C.TZ_OFFSET_HOURS, minutes=pa[1] + 5)
    RN._now = lambda: fake                                          # прогноз и окно суток — как в 11:35 того дня
    C.REPORT_FIRST = False                                          # отчёта в репетиции нет — ⚽1 не ждёт его
    notify.send_blocks = notify.send = lambda *a, **k: []           # страховка: репетиция ничего не отправляет
    if not use_odds:
        OD.day_events = lambda *a, **k: ([], ['кф: репетиция без кф'])
        OD.add_alternates = lambda *a, **k: (None, '')
        OD.sports = lambda: []
    store, rt = J.Store(), runtime.load()
    base = fdata.load()
    mp = RN.Mapper(base)
    st = RN.load_store()
    af, ld = store.read('auto_forecasts'), store.read('auto_lines')
    matches, errs = fespn.fetch(RN.plan(base, st, af, rt))
    st = RN.persist(st, matches, mp)
    train = fdata.training_set(st)
    ufit, usched = RN.uefa_frames(st, matches, mp)
    world = RN.model.build(train, ufit, fake)
    sched = RN.FF.Schedule(train[['date', 'home', 'away']], usched)
    kcal, _, knote = RN.daily_k(rt, ld, False)
    off, _ = R.breaker(ld, rt.get('breaker', {}))
    off = {**off, **{mk: 'A5' for mk in C.MARKETS_OFF}}
    items, skipped = RN.forecast(matches, mp, world, sched, train, af, kcal)
    print(f'🎬 Репетиция футбола: сутки {day:%d.%m} (08:00 → 08:00), кф: {"да" if use_odds else "нет"}')
    print(f'ESPN: матчей {len(matches)}, ошибок {len(errs)}; модель: стран {len(world.sup)}, команд {len(world.team_country)}')
    days = pd.Series([C.wday(m.start + pd.Timedelta(hours=C.TZ_OFFSET_HOURS)) for m in matches if m.state == 'pre'])
    print('матчей «pre» по суткам:', days.value_counts().sort_index().to_dict())
    lg = pd.Series([str(row.get('comp_name') or row.get('comp')) for _, row, _ in items]).value_counts()
    print(f'прогноз суток: {len(items)} матчей; пропущено {len(skipped)}')
    print('  лиги:', ' · '.join(f'{k} {v}' for k, v in lg.items()))
    for s in skipped[:12]:
        print('  пропуск:', s)
    log: list[str] = []
    raw = [o for o in dpcore._dedupe(daypick.offers(items, off, day, log)) if o['market'] not in off]
    cands = [o for o in dpcore.PK.choose(raw)]
    print('\n'.join(log))
    n_k = sum(1 for c in cands if c.get('k'))
    # 19.11: без кф (вечерняя репетиция, экономия кредитов) ⭐ невозможна по правилу С19 «только с ценой» → проверяются линии-кандидаты модели
    print(f'линий-кандидатов модели: {len(raw)}; кандидатов ставки дня: {len(cands)}, с кф {n_k}; 🔥 {sum(1 for c in cands if c.get("fire"))}, '
          f'⚔️ {sum(1 for c in cands if c.get("clash"))}; ⛔ рынки: {", ".join(off) or "нет"}')
    for c in cands[:10]:
        ev = OD.ev(c['p'], c.get('k'))
        print(f"  {c['label']:<14} {c['home'][:14]} — {c['away'][:14]} · P {c['p']:.3f} · tier {c.get('tier')} · "
              f"кф {c.get('k') or '—'} ({c.get('k_src') or '—'}) · лучш. {c.get('k_best') or '—'} {c.get('book_best') or ''} · "
              f"EV {'—' if ev is None else f'{ev * 100:+.1f}%'}{' 🔥' if c.get('fire') else ''}{' ⚔️' if c.get('clash') else ''}")
    print('\n⭐ так выглядела бы ⚽1 (без строки 🔎):')
    print(daypick.text(cands[0] if cands else None, day, [], '', 'подходящей линии нет'))
    print(f'\nрепетиция {time.time() - t0:.0f} с; {knote}')
    n_pre = int((days == day).sum()) if len(days) else 0
    # 25.11: еврокубки с командой вне модели — штатно (кандидаты ⭐ по цене рынка, MARKET_ONLY_UEFA), не тревога репетиции
    cup = {v[0] for k_, v in C.COMPETITIONS.items() if k_.startswith('uefa.')}
    own = [x for x in skipped if not any(f'({n})' in x for n in cup)]
    if len(own) < len(skipped):
        print(f'еврокубки вне модели: {len(skipped) - len(own)} матчей — ⭐ по цене Pinnacle')
    probs = ([f'ESPN: ошибок запросов {len(errs)}'] if errs else []) \
        + ([f'в расписании {n_pre} матчей, а прогноз — 0'] if n_pre and not items else []) \
        + ([f'прогноз {len(items)} матчей, а линий-кандидатов 0'] if items and not raw else []) \
        + ([f'прогноз {len(items)} матчей, а кандидатов ставки дня с ценой 0'] if use_odds and items and not cands else []) \
        + ([f'не опознано {len(own)} из {len(own) + len(items)} матчей наших лиг'] if len(own) > max(5, len(items)) else [])
    print('🎬 итог:', '; '.join(probs) if probs else 'проблем нет')
    if auto and probs:
        send([f'⚠️ <b>Футбол · репетиция {day:%d.%m}</b>\n' + '\n'.join(f'• {notify.esc(p)}' for p in probs)])


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else '')
