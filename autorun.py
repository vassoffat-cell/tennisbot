"""Автономный режим в облаке (GitHub Actions каждые 2 часа). Работает без участия человека.

Цикл одного запуска:
  1. входящие файлы из папки Google Диска (кладёт бот) → проверка ПО СОДЕРЖИМОМУ → переобучение;
  2. ежедневно в 00:00 по Ташкенту: переобучение модели, самокалибровка k по рынкам, один суточный отчёт,
     сбор новостей; затем ⭐ ставка дня (закреплённое сообщение);
  3. расписание и результаты ESPN → форма игроков, рейтинг и очки ATP/WTA;
  4. прогноз на матчи Большого шлема, 1000, 500, 250 и итоговых в ближайшие 24 ч — только модель (без кф букмекеров):
     исход в каждом матче + 7 линий (показываются с кф ≥ 1,25); до начала матча прогноз обновляется каждые 2 часа;
  5. расчёт доигранных матчей и всех линий; ⛔ рынок при значимой просадке, 🚨 тревога;
  6. в Telegram: свежий список на 24 ч (прошлый удаляется); напоминание о файлах по понедельникам с прямыми ссылками.

  python autorun.py auto [--dry]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
import traceback

import numpy as np
import pandas as pd

import config as C
import espn
import forecast as FC
import journal as J
import lines as LN
import live
import daypick
import news
import notify
import pipeline as P
import report as R
import runtime
from markov import Outcome

TOURS = ('wta', 'atp')
CONF_WORD = {1: 'очень низкая', 2: 'низкая', 3: 'средняя', 4: 'высокая', 5: 'очень высокая'}
MARKETS = ('ML', 'HCP', 'IT1', 'IT2', 'TOTAL')


def _now() -> pd.Timestamp:
    return pd.Timestamp.now(tz='UTC').tz_localize(None)


def _local(ts: pd.Timestamp | None = None) -> pd.Timestamp:
    return (ts if ts is not None else _now()) + pd.Timedelta(hours=C.TZ_OFFSET_HOURS)


def _days(a: dt.date, b: dt.date) -> list[dt.date]:
    return [a + dt.timedelta(days=i) for i in range((b - a).days + 1)]


def _id(x) -> str:
    try:
        return str(int(float(x)))
    except (TypeError, ValueError):
        return str(x).strip()


def _blank(x) -> bool:
    return str(x).strip() in ('', 'nan', 'None')


# ======================================================================== сбор данных
def collect(auto: pd.DataFrame) -> dict[str, list[espn.EspnMatch]]:
    """Матчи ESPN: от последней даты tennis-data (не дальше 21 дня) до завтра + даты нерассчитанных авто-прогнозов."""
    today = live.utc_today()
    out = {}
    for tour in TOURS:
        st = P.model(tour).state
        td_last = getattr(st, 'td_last', st.last_date).date()
        stored = live.load_store(tour)
        if not stored.empty:
            td_last = max(td_last, stored.date.max().date())                 # уже сохранённое не скачиваем повторно
        start = max(min(td_last, today - dt.timedelta(days=3)), today - dt.timedelta(days=21))
        days = set(_days(start, today + dt.timedelta(days=1)))
        pend = auto[(auto.tour.astype(str).str.lower() == tour) & (auto.settled.astype(str).str.strip() == '')]
        for s in pend.start_utc:
            try:
                d = pd.Timestamp(str(s)).date()
                if d >= today - dt.timedelta(days=30):
                    days.add(d)
            except ValueError:
                pass
        out[tour] = espn.fetch(tour, sorted(days))
    return out


# ======================================================================== самокалибровка
def calibration(auto: pd.DataFrame) -> tuple[float, int]:
    """k для победителя матча по доигранным авто-прогнозам: P' = σ(k·logit P)."""
    fin = auto[auto.status.astype(str) == 'FIN']
    if fin.empty:
        return 1.0, 0
    raw = fin.p1_raw.map(J.num)
    p = np.where(raw.notna(), raw, fin.p1.map(J.num)).astype(float)
    return LN.calibrate(p, (fin.winner.map(J.num) == 1).values)


def market_calibration(lines_df: pd.DataFrame) -> dict[str, tuple[float, int]]:
    """k для фор и тоталов по рассчитанным ПОКАЗАННЫМ линиям (W/L) этого рынка; n — число линий."""
    out = {}
    done = R.lines(lines_df)
    for m in ('HCP', 'IT1', 'IT2', 'TOTAL'):
        sub = done[done.market.astype(str) == m]
        out[m] = LN.calibrate(sub.p_raw.map(J.num).values.astype(float), sub.won.values.astype(bool))
    return out


def daily_k(rt: dict, auto: pd.DataFrame, ldf: pd.DataFrame, due: bool) -> tuple[dict, dict, str]:
    """k на сутки: пересчёт только в суточном цикле (00:00); днём — зафиксированные. ML — победа матча."""
    have = rt.get('kcal')
    if due or not have:
        prev = have or {}
        new = {'ML': calibration(auto), **market_calibration(ldf)}
        rt['kcal'] = {k: list(v) for k, v in new.items()}
        rt['kcal_prev'] = {k: list(v) for k, v in prev.items()} if prev else {k: [1.0, 0] for k in new}
        rt['kcal_date'] = _local().date().isoformat()
        return new, {k: tuple(v) for k, v in rt['kcal_prev'].items()}, 'k пересчитан'
    return ({k: tuple(v) for k, v in have.items()}, {k: tuple(v) for k, v in rt.get('kcal_prev', {}).items()},
            f"k от {rt.get('kcal_date', '?')}")


def confidence(p: float, n_min: int, auto: pd.DataFrame) -> int:
    """Уверенность в прогнозе 1–5: объём данных по игрокам + фактическая точность модели на живых матчах
    в той же корзине вероятности (без сравнения с рынком — система независима от букмекеров)."""
    s = 3
    s += -2 if n_min < 15 else -1 if n_min < 40 else 1 if n_min >= 120 else 0
    fin = auto[auto.status.astype(str) == 'FIN']
    if len(fin) >= 30:
        p1 = fin.p1.map(J.num).values
        w1 = (fin.winner.map(J.num) == 1).values
        pf = np.maximum(p1, 1 - p1); hit = np.where(p1 >= 0.5, w1, ~w1)
        lo = np.floor(max(p, 1 - p) * 10) / 10
        m = (pf >= lo) & (pf < lo + 0.1)
        if m.sum() >= 30:
            gap = abs(pf[m].mean() - hit[m].mean())
            s += 1 if gap < 0.03 else -1 if gap > 0.08 else 0
    return int(np.clip(s, 1, 5))


LEVEL_RU = {'Grand Slam': 'Большой шлем', '1000': '1000', '500': '500', '250': '250', 'Finals': 'Итоговый турнир'}
ROUND_RU = {'Final': 'финал', 'Semifinal': '1/2', 'Quarterfinal': '1/4', 'Round Robin': 'группа'}
SURF_RU = {'Hard': 'хард', 'Clay': 'грунт', 'Grass': 'трава'}


def _round_ru(r: str) -> str:
    if r in ROUND_RU:
        return ROUND_RU[r]
    m = __import__('re').search(r'(\d+)', r)
    return f'{m.group(1)}-й круг' if m else r




# ======================================================================== рейтинги и прогноз
def apply_rankings(tour: str) -> int:
    """Свежий рейтинг ATP/WTA из ESPN → признак «рейтинг» в модели (в памяти, на каждый запуск)."""
    md = P.model(tour)
    rk = espn.rankings(tour)
    act = live.active_ids(md.state)
    n = 0
    for name, rank, pts in rk:
        pid = live.identify(name, P._PLAYERS[tour], act)[0]
        if pid is not None:
            md.state.rank[pid] = float(rank)
            if pts:
                md.state.pts[pid] = float(pts)                       # очки рейтинга — признак модели WTA
            n += 1
    return n


def forecast_all(tour: str, matches: list[espn.EspnMatch], auto: pd.DataFrame, k_ml: float,
                 kc: dict[str, float]) -> tuple[list[tuple], list[str]]:
    """Прогноз и линии для всех матчей тура, начинающихся в ближайшие HORIZON_HOURS часов: [(матч, строка, линии)]."""
    md = P.model(tour)
    st, players = md.state, P._PLAYERS[tour]
    act = live.active_ids(st)
    now = _now()
    settled = set(auto[~auto.settled.map(_blank).astype(bool)].espn_id.map(_id))
    out, skipped = [], []
    for m in matches:
        if m.state != 'pre' or m.tbd or m.qualifying or m.id in settled:
            continue
        if not (now < m.start <= now + pd.Timedelta(hours=C.HORIZON_HOURS)):
            continue
        level = live.level_for(tour, m.location, m.start, m.major)
        if level not in C.LEVELS:
            continue
        ids = [live.identify(n, players, act) for n in m.names]
        if any(i[0] is None for i in ids):
            skipped.append(f'{m.names[0]} — {m.names[1]} ({m.event}): игрок не опознан '
                           + ', '.join(f'{n}→{i[1]} {i[2]:.2f}' for n, i in zip(m.names, ids) if i[0] is None))
            continue
        date = m.start.normalize()
        surface = live.surface_for(tour, m.location, date)
        best_of, tb = (5 if (m.major and tour == 'atp') else 3), (10 if m.major else 7)
        snap = st.snapshot(ids[0][0], ids[1][0], date, surface, m.location, date.year)
        p_raw = md.p_win(snap)
        p1 = LN.scale(p_raw, k_ml)
        _, D = FC.distribution(md, snap, best_of, tb, p_override=p1)
        daypick.DISTS[str(m.id)] = (D, ids[0][1], ids[1][1])          # для линий ставки дня по правилу C
        f = FC.fair(md, snap, best_of, tb, p_override=p1)
        n1, n2 = int(st.n.get(ids[0][0], 0)), int(st.n.get(ids[1][0], 0))
        row = dict(created=J.now_utc(), espn_id=m.id, tour=tour.upper(), event=m.event, location=m.location, surface=surface,
                   round=m.round, best_of=best_of, start_utc=m.start.strftime('%Y-%m-%d %H:%M'), start_local=J.to_local(m.start),
                   player1=ids[0][1], player2=ids[1][1], player1_id=float(ids[0][0]), player2_id=float(ids[1][0]),
                   n1=n1, n2=n2, id_score1=round(ids[0][2], 3), id_score2=round(ids[1][2], 3),
                   p1=round(p1, 4), p2=round(1 - p1, 4), kfair1=round(1 / p1, 3), kfair2=round(1 / (1 - p1), 3),
                   kmin1=round((1 + C.EV_THRESHOLD) / p1, 3), kmin2=round((1 + C.EV_THRESHOLD) / (1 - p1), 3),
                   exp_total=round(f['exp_total'], 2), total_line=f['total_line'], p_over=round(f['p_over'], 4),
                   hcp1_line=f['hcp1_line'], p_hcp1=round(f['p_hcp1'], 4), set1_p1=round(f['set1_p1'], 4),
                   level=level, p1_raw=round(p_raw, 4), calib_k=k_ml, confidence=confidence(p1, min(n1, n2), auto))
        for k in J.EXACT_COLS:
            row[k] = round(f[k], 4) if k in f else ''
        lns = LN.build(D, p1, kc, LN.sigma_for(min(n1, n2)))
        for x in lns:
            x.update(espn_id=m.id, tour=row['tour'], event=m.event, level=level, round=m.round, surface=surface,
                     start_utc=row['start_utc'], start_local=row['start_local'], player1=row['player1'], player2=row['player2'],
                     created=row['created'], status='', result='', settled='', pnl_lo='')
        out.append((m, row, lns))
    return out, skipped


def save_forecasts(store: J.Store, items: list[tuple], auto: pd.DataFrame, lines_df: pd.DataFrame) -> tuple[int, int]:
    """Новые матчи — добавить; уже записанные и не начавшиеся — обновить прогноз (в таблице — последний перед матчем)."""
    pos = {_id(x): i for i, x in zip(auto.index, auto.espn_id)}
    lpos = {(_id(e), str(int(J.num(s)))): i for i, e, s in zip(lines_df.index, lines_df.espn_id, lines_df.slot)
            if not np.isnan(J.num(s))}
    new_rows, new_lines, upd_rows, upd_lines = [], [], {}, {}
    for m, row, lns in items:
        if m.id in pos:
            upd_rows[pos[m.id]] = row
        else:
            new_rows.append(row)
        for x in lns:
            k = (m.id, str(x['slot']))
            if k in lpos:
                upd_lines[lpos[k]] = x
            else:
                new_lines.append(x)
    store.append('auto_forecasts', new_rows)
    store.append('auto_lines', new_lines)
    if upd_rows:
        df = pd.DataFrame.from_dict(upd_rows, orient='index')
        store.update('auto_forecasts', df, J.AUTO_FCST_COLS)
        store.update('auto_forecasts', df, J.AUTO_TAIL_COLS)
    if upd_lines:
        dfl = pd.DataFrame.from_dict(upd_lines, orient='index')
        store.update('auto_lines', dfl, J.LINE_FCST_COLS)
        store.update('auto_lines', dfl, ['p_low', 'shown'])
    return len(new_rows), len(upd_rows)


# ======================================================================== список в Telegram
def _last(name: str) -> str:
    return str(name).split()[-1] if str(name).strip() else str(name)


LINE_TITLE = {1: 'П1 {p1}', 2: 'П2 {p2}', 3: 'Ф1 ({line})', 4: 'Ф2 ({line})', 5: 'ИТ1 {label}', 6: 'ИТ2 {label}',
              7: 'Т{label}'}


def visible(x: dict, off: dict) -> bool:
    return int(J.num(x.get('shown'), 0) or 0) == 1 and x['market'] not in off


def title(row: dict, x: dict) -> str:
    """Подпись линии с фамилиями: «Ф1 (−3.5)», «ИТ2 Б 9.5», «ТМ27.5»."""
    line = f"{float(x['line']):+g}" if not _blank(x.get('line')) else ''
    label = str(x['label']).replace(' ', '') if int(x['slot']) == 7 else x['label']
    return LINE_TITLE[int(x['slot'])].format(p1=_last(row['player1']), p2=_last(row['player2']), line=line, label=label)


def match_block(row: dict, lns: list[dict], today: str, off: dict, star: tuple | None) -> str:
    """Матч: время · раунд · игроки; исход (всегда); линии с кф ≥ 1,25 от самой уверенной:
    🟢 жирным — высокая, 🟡 обычным — средняя, ⚪ курсивом — низкая уверенность; ⭐ — ставка дня."""
    e = notify.esc
    when = row['start_local'][6:] if row['start_local'][:5] == today else row['start_local']   # «14:30» или «07.11 00:30»
    p1 = float(row['p1'])
    fav, pf = (row['player1'], p1) if p1 >= 0.5 else (row['player2'], 1 - p1)
    out = [f"🕒 <b>{e(when)}</b> · {e(_round_ru(str(row['round'])))} · <b>{e(row['player1'])}</b> — <b>{e(row['player2'])}</b>",
           f"🎯 Исход: <b>{e(_last(fav))} — {pf * 100:.0f}%</b>"]
    tier = lambda x: LN.tier(float(x.get('p_low') or x['p']))
    for x in sorted([x for x in lns if visible(x, off)], key=lambda x: (-tier(x), -float(x['p']), x['slot'])):
        t = conf_line(f"{title(row, x)} — {float(x['p']) * 100:.0f}%", float(x.get('p_low') or x['p']))
        if star and (str(row['espn_id']), int(x['slot'])) == star[:2]:
            t = '⭐ ' + t
        out.append(t)
    if star and str(row['espn_id']) == star[0] and not star[1]:          # ставка дня вне 7 линий списка
        out.append(f'⭐ <b>{notify.esc(star[2])}</b> — {star[3] * 100:.0f}%')
    return '\n'.join(out)


def conf_line(text: str, p_low: float) -> str:
    t = LN.tier(p_low)
    t_html = notify.esc(text)
    return {3: f'🟢 <b>{t_html}</b>', 2: f'🟡 {t_html}', 1: f'⚪ <i>{t_html}</i>'}[t]


def list_blocks(items: list[tuple], off: dict, star: tuple | None) -> list[str]:
    """Список на 24 ч: заголовок турнира один раз, под ним матчи по времени; турниры — по времени первого матча."""
    today = _local().strftime('%d.%m')
    groups: dict[tuple, list] = {}
    for m, row, lns in sorted(items, key=lambda t: t[1]['start_utc']):
        groups.setdefault((row['tour'], row['event'], row.get('level'), row['surface']), []).append(
            match_block(row, lns, today, off, star))
    if not groups:
        return []
    n = sum(len(v) for v in groups.values())
    blocks = [f"📋 <b>Прогноз на 24 ч</b> · {_local().strftime('%d.%m %H:%M')} · {n} {notify.plural(n, 'матч', 'матча', 'матчей')}"
              + ('\n⭐ Ставка дня — в закреплённом сообщении' if star else '')]
    for (tour, event, level, surface), ms in groups.items():
        head = (f"🏆 <b>{notify.esc(tour)} {LEVEL_RU.get(str(level), str(level or ''))} · {notify.esc(event)}</b> · "
                f"{SURF_RU.get(surface, surface)}")
        blocks += notify.folded(head, ms)
    return blocks


def publish_list(rt: dict, store: J.Store | None, items: list[tuple], off: dict, dry: bool = False,
                 extra: list | None = None) -> str:
    """Удалить прошлый список и отправить свежий. Нет матчей — тишина. Звук — только если появились новые матчи."""
    dp = rt.get('day_pick') or {}
    star = ((str(dp['espn_id']), int(dp.get('slot') or 0), str(dp.get('label', '')), float(dp.get('p', 0)))
            if dp.get('espn_id') and dp.get('date') == _local().date().isoformat() else None)
    blocks = list_blocks(items, off, star)
    ids = sorted(m.id for m, _, _ in items)
    if dry:
        print('\n\n'.join(blocks))
        return 'dry'
    cache = list(blocks)
    while cache and len(json.dumps(cache, ensure_ascii=False)) > 45000:   # ячейка Google Таблицы — до 50 000 символов
        cache.pop()
    dpt = (rt.get('day_pick') or {}).get('text', '') if (rt.get('day_pick') or {}).get('date') == _local().date().isoformat() else ''
    store.write_all('tg_cache', pd.DataFrame([dict(key='list', text=json.dumps(cache, ensure_ascii=False), updated=J.now_utc()),
                                              dict(key='daypick', text=dpt, updated=J.now_utc()),
                                              dict(key='daypick_accepted', text=(rt.get('day_pick') or {}).get('accepted', ''),
                                                   updated=J.now_utc())]))
    removed = notify.delete([tuple(x) for x in rt.get('list_messages', [])] + list(extra or []))
    sent = []
    if items:
        silent = set(ids) <= set(rt.get('list_match_ids', []))
        sent = notify.send_blocks(blocks, silent=silent)
    rt['list_messages'] = [list(x) for x in sent]
    rt['list_match_ids'] = ids
    return f'список: удалено {removed}, отправлено {len(sent)} сообщ., матчей {len(ids)}'


def extra_messages(store: J.Store) -> list[tuple[int, int]]:
    """Списки, запрошенные кнопкой в боте (лист tg_messages): удаляются вместе с плановым списком."""
    df = store.read('tg_messages')
    out = [(int(J.num(c)), int(J.num(m))) for c, m in zip(df.chat_id, df.message_id)
           if not np.isnan(J.num(c)) and not np.isnan(J.num(m))]
    if len(df):
        store.write_all('tg_messages', pd.DataFrame(columns=J.SHEETS['tg_messages']))
    return out


# ======================================================================== расчёт
def settle_auto(store: J.Store, by_id: dict[str, espn.EspnMatch]) -> pd.DataFrame:
    df = store.read('auto_forecasts')
    todo = df[df.settled.astype(str).str.strip() == '']
    upd = []
    today = dt.date.today().isoformat()
    for i, r in todo.iterrows():
        m = by_id.get(_id(r.espn_id))
        if m is None:
            continue
        if m.completed:
            retired = m.status != 'STATUS_FINAL'
            w = 1 if m.winner == 0 else 2
            p1 = J.num(r.p1)
            pw = p1 if w == 1 else 1 - p1
            df.loc[i, J.AUTO_RESULT_COLS] = ['RET' if retired else 'FIN', w, ' '.join(f'{a}-{b}' for a, b in m.sets), today,
                                             '' if retired else round(-math.log(max(pw, 1e-6)), 4)]
            upd.append(i)
        elif m.state == 'post':                                   # снят до начала, отменён
            df.loc[i, J.AUTO_RESULT_COLS] = ['VOID', '', '', today, '']
            upd.append(i)
    store.update('auto_forecasts', df.loc[upd], J.AUTO_RESULT_COLS)
    return df


def attach_odds(store: J.Store) -> int:
    """Закрывающие коэффициенты tennis-data к рассчитанным прогнозам — только как данные для исследований
    (в прогнозе и отчётах не участвуют: система независима от букмекеров)."""
    import dataset
    df = store.read('auto_forecasts')
    todo = df[(df.status.astype(str).isin(['FIN', 'RET'])) & (df.avg_1.astype(str).str.strip() == '')]
    if todo.empty:
        return 0
    upd = []
    for tour in TOURS:
        sub = todo[todo.tour.astype(str).str.lower() == tour]
        if sub.empty:
            continue
        h = dataset.build(tour)
        for i, r in sub.iterrows():
            a, b = J.num(r.player1_id), J.num(r.player2_id)
            d = pd.Timestamp(str(r.start_utc)).normalize()
            m = h[(((h.winner_id == a) & (h.loser_id == b)) | ((h.winner_id == b) & (h.loser_id == a)))
                  & ((h.Date - d).abs().dt.days <= 4)]
            if m.empty:
                continue
            x = m.iloc[-1]
            a_won = x.winner_id == a
            for bk, (cw, cl) in {'b365': ('B365W', 'B365L'), 'avg': ('AvgW', 'AvgL'), 'max': ('MaxW', 'MaxL'),
                                 'bfe': ('BFEW', 'BFEL')}.items():
                kw, kl = J.num(x.get(cw)), J.num(x.get(cl))
                df.at[i, f'{bk}_1'], df.at[i, f'{bk}_2'] = (kw, kl) if a_won else (kl, kw)
            upd.append(i)
    store.update('auto_forecasts', df.loc[upd], J.AUTO_ODDS_COLS)
    return len(upd)


def settle_lines(store: J.Store, by_id: dict[str, espn.EspnMatch]) -> int:
    """Расчёт всех линий доигранных матчей: W / L / P (отказ, отмена — возврат) и прибыль при ставке по нижнему кф."""
    df = store.read('auto_lines')
    todo = df[df.settled.map(_blank).astype(bool)]
    upd = []
    today = dt.date.today().isoformat()
    for i, r in todo.iterrows():
        m = by_id.get(_id(r.espn_id))
        if m is None or m.state != 'post':
            continue
        if m.completed and m.status == 'STATUS_FINAL':
            res, status = LN.settle(str(r.market), str(r.pick), r.line, Outcome(tuple(m.sets))), 'FIN'
        else:
            res, status = 'P', ('RET' if m.completed else 'VOID')
        k = J.num(r.z_lo) if not _blank(r.z_lo) else J.num(r.k_lo)          # старые — нижний кф зоны, новые — 1/P
        df.loc[i, J.LINE_RESULT_COLS] = [status, res, today, round({'W': k - 1, 'L': -1.0, 'P': 0.0}[res], 3) if k == k else '']
        upd.append(i)
    store.update('auto_lines', df.loc[upd], J.LINE_RESULT_COLS)
    return len(upd)


# ======================================================================== сводка, файлы, цикл
def summary(auto: pd.DataFrame, ldf: pd.DataFrame, kcal: dict, off: dict) -> pd.DataFrame:
    M, L = R.matches(auto), R.lines(ldf)
    rows = [('обновлено (UTC)', J.now_utc()), ('авто-прогнозов', len(auto)), ('доиграно', len(M)),
            ('исход угадан', f'{int(M.hit.sum())}/{len(M)}' if len(M) else '—'),
            ('показанных линий рассчитано', len(L)), ('линий верных', f'{int(L.won.sum())}/{len(L)}' if len(L) else '—'),
            ('k по рынкам', '; '.join(f'{m} {v[0]} (n={v[1]})' for m, v in kcal.items())), ('⛔ выключены', ', '.join(off) or 'нет')]
    return pd.DataFrame(rows, columns=['показатель', 'значение'])


def retrain(store: J.Store | None) -> list[str]:
    import tml
    import updater
    log = tml.download() if os.getenv('CLOUD') else []        # T29 этап A: статистика подачи TennisMyLife — копится, в модель пока не идёт
    log += updater.rebuild()
    P._MODELS.clear()
    if store is not None:
        n = attach_odds(store)
        log.append(f'кф tennis-data (данные) к {n} матчам')
    return log


def _links() -> list[tuple[str, str]]:
    """[(тур, прямая ссылка)] на текущий сезон; в январе — ещё финальные файлы прошлого года."""
    y = _local().year
    out = [(t.upper(), C.TENNIS_DATA_URL[t].format(y=y)) for t in ('wta', 'atp')]
    if _local().month == 1:
        out += [(f'{t.upper()} {y - 1}', C.TENNIS_DATA_URL[t].format(y=y - 1)) for t in ('wta', 'atp')]
    return out


def _need_text() -> str:
    return '\n'.join(f'• {t}: {u}' for t, u in _links()) + f'\nЕсли ссылка не открывается — страница со списком: {C.TENNIS_DATA_PAGE}'


SHOTS: list[tuple] = []          # скриншоты 1xBet из входящих этого запуска (id, имя, байты, описание)


def take_inbox(rt: dict) -> tuple[bool, list[str]]:
    """Файлы из папки Google Диска (кладёт бот). Проверка по содержимому; ответ в чат: принят / не принят и почему."""
    import inbox
    import updater
    try:
        files, note = inbox.fetch()
    except Exception as e:
        return False, [f'папка входящих недоступна: {e}']
    log, got, msgs = ([note] if note else []), False, []
    import shots
    for fid, name, raw in files:
        if shots.is_image(raw) or str(name).startswith('shot_'):
            SHOTS.append((fid, name, raw, inbox.DESC.get(fid, '')))   # скриншот 1xBet — разбор после прогноза (нужны матчи)
            continue
        try:
            tour, rep = updater.save_file(raw)
            rt.setdefault('files', {})[tour] = _local().date().isoformat()
            got = True
            log.append(f'файл принят: {rep}')
            msgs.append(f'✅ Файл <b>{notify.esc(name)}</b> принят: {notify.esc(rep)}. Модель переобучается.')
        except updater.WrongFile as e:
            log.append(f'файл {name} не принят: {e}')
            msgs.append(f'❌ Файл <b>{notify.esc(name)}</b> не принят: {notify.esc(str(e))}.\nОтправьте корректный:\n'
                        + notify.esc(_need_text()))
        except Exception as e:
            log.append(f'файл {name} не принят: {e.__class__.__name__}: {e}')
            msgs.append(f'❌ Файл <b>{notify.esc(name)}</b> не прочитан ({notify.esc(e.__class__.__name__)}).\n'
                        'Отправьте корректный:\n' + notify.esc(_need_text()))
        inbox.mark_done(fid, name)
    if msgs:
        notify.send_blocks(msgs)
    return got, log


def reminder_due(rt: dict) -> bool:
    """С понедельника в 12:00 по Ташкенту, раз в день, пока на этой неделе не пришли ОБА файла (WTA и ATP)."""
    loc = _local()
    if not (12 <= loc.hour < 14):
        return False
    monday = (loc.date() - dt.timedelta(days=loc.weekday())).isoformat()
    have = rt.get('files', {})
    return any((have.get(t) or '2000-01-01') < monday for t in ('WTA', 'ATP')) and rt.get('last_reminder') != loc.date().isoformat()


def reminder_text(rt: dict) -> str:
    loc = _local()
    monday = (loc.date() - dt.timedelta(days=loc.weekday())).isoformat()
    have = rt.get('files', {})
    rows = []
    for t, u in _links():
        ok = (have.get(t.split()[0]) or '2000-01-01') >= monday and ' ' not in t
        rows.append(f'✅ {t} — получен' if ok else f'⬜ {t} — {u}')
    return ('⏰ <b>Пора прислать файлы tennis-data</b>\n' + '\n'.join(rows) +
            f'\nЕсли ссылка не открывается — страница со списком: {C.TENNIS_DATA_PAGE}\n'
            'Скачайте и отправьте сюда (название не важно — бот проверит содержимое; второй файл браузер назовёт «… (1).xlsx» — это нормально). '
            'Напоминание повторится завтра, пока не придут оба файла.')


def run(dry: bool = False) -> str:
    store, rt = J.Store(), runtime.load()
    if 'files' not in rt:                                              # переход со старого учёта (одна дата на оба файла)
        rt['files'] = {t: rt.get('last_files', '') for t in ('WTA', 'ATP')}
    log = []
    got, ilog = (False, []) if dry else take_inbox(rt)
    log += ilog
    if os.getenv('CLOUD') and not (C.DATA / 'tml').exists():          # T29: первая загрузка TennisMyLife сразу, дальше — раз в сутки
        import tml
        log += tml.download()
    if os.getenv('CLOUD') and os.getenv('ODDS_API_KEY') and not (C.DATA / 'odds' / 'probe.json').exists():
        import odds                                                    # пакет №3: разовая проба покрытия котировок
        log += odds.probe()
    if os.getenv('CLOUD') and os.getenv('ODDS_API_KEY') and not (C.DATA / 'odds' / 'probe_event.json').exists():
        import odds                                                    # пакет №3: разовая проба альтернативных линий (матч РПЛ 08.11)
        log += odds.probe_event('soccer_russia_premier_league', '4963f0a1fdba101362e25a8ad678af74',
                                ('alternate_totals', 'alternate_spreads', 'team_totals', 'alternate_team_totals', 'btts', 'double_chance'))
    loc = _local(); today = loc.date().isoformat()
    daily_due = C.DAILY_HOUR[0] <= loc.hour < C.DAILY_HOUR[1] and rt.get('last_daily') != today
    retrain_log = []
    if got or (daily_due and rt.get('last_retrain') != today):
        retrain_log = retrain(None if dry else store)
        log += retrain_log
        rt['last_retrain'] = today
    auto, ldf = store.read('auto_forecasts'), store.read('auto_lines')
    matches = collect(auto)
    for tour in TOURS:
        n = live.refresh(P.model(tour), matches[tour], P._PLAYERS[tour])
        r = apply_rankings(tour)
        log.append(f'{tour.upper()}: матчей ESPN {len(matches[tour])}, докатано в форму {n}, рейтинг обновлён у {r}')
    by_id = {m.id: m for tour in TOURS for m in matches[tour]}
    if not dry:                                                        # итоги прошедших матчей — до пересчёта k
        settle_auto(store, by_id)
        nl = settle_lines(store, by_id)
        nd = daypick.settle(store)
        log.append(f'рассчитано линий {nl}, ставок дня {nd}')
        auto, ldf = store.read('auto_forecasts'), store.read('auto_lines')
    kcal, kprev, knote = daily_k(rt, auto, ldf, daily_due and not dry)
    k_ml = kcal.get('ML', (1.0, 0))[0]
    kc = {m: v[0] for m, v in kcal.items() if m != 'ML'}
    off, bmsg = R.breaker(ldf, rt.get('breaker', {}))
    items, skipped = [], []
    for tour in TOURS:
        it, sk = forecast_all(tour, matches[tour], auto, k_ml, kc)
        items += it; skipped += sk
    if dry:
        print('\n'.join(log)); publish_list(rt, None, items, off, dry=True); print('\n'.join(skipped))
        return 'dry'
    rt['breaker'] = off
    alarms = bmsg + R.outcome_alarm(auto, rt)
    if alarms:
        notify.send_blocks(alarms)
    nn, nu = save_forecasts(store, items, auto, ldf)
    auto, ldf = store.read('auto_forecasts'), store.read('auto_lines')
    if daily_due:
        notes = []
        for tour in TOURS:
            last = getattr(P.model(tour).state, 'td_last', None)
            if last is not None and (loc.normalize() - last).days > 9:
                notes.append(f'tennis-data {tour.upper()} по {last:%d.%m} — ждём файл недели (форма до него — по ESPN)')
        if retrain_log:
            notes.append('переобучено: ' + '; '.join(x.split(':')[0] + ' ' + x.split('точность')[-1].split(';')[0].strip()
                                                  for x in retrain_log if 'точность' in x))
        yday = (loc - pd.Timedelta(days=1)).date()
        notify.send_blocks(R.build(auto, ldf, store.read('day_pick'), f'📈 Теннис · отчёт · {yday:%d.%m}', kcal=kcal,
                                   kprev=kprev, off=off, notes=notes, daily_day=yday))
        rt['last_daily'] = today
        log.append('суточный отчёт отправлен')
        try:
            log.append(news.collect())
        except Exception as e:
            log.append(f'новости: сбой {e.__class__.__name__}')
    daypick.TITLE = title
    try:
        import dpcore
        import inbox
        import odds as OD
        import shots
        actions = dpcore.read_actions(store)
        offers_s, replies, slog = shots.process(SHOTS, items, OD.cached_events('tennis', _local().date().isoformat()))
        log += slog + ([f'кнопки: {", ".join(actions)}'] if actions else [])
        if replies:
            notify.send_blocks(replies)
        for fid, name, _, _ in SHOTS:
            inbox.mark_done(fid, name)
        log.append(daypick.process(rt, store, items, off, daily_done=rt.get('last_daily') == today,
                                   shots=offers_s, actions=actions))
    except Exception as e:
        log.append(f'ставка дня: сбой {e.__class__.__name__}: {e}')
        log.append(traceback.format_exc()[-800:])
    log.append(publish_list(rt, store, items, off, extra=extra_messages(store)))
    if reminder_due(rt):
        notify.send_blocks([reminder_text(rt)])
        rt['last_reminder'] = today
        log.append('напоминание о файлах отправлено')
    store.write_all('summary', summary(auto, ldf, kcal, off))
    store.write_all('reports', R.all_reports(auto, ldf, store.read('day_pick'), kcal, off))
    runtime.save(rt)
    log += [f'{knote}: ' + ', '.join(f'{m} {v[0]} (n={v[1]})' for m, v in kcal.items()) + f'; ⛔ {", ".join(off) or "нет"}',
            f'прогнозов: новых {nn}, обновлено {nu}; пропущено {len(skipped)}']
    return '\n'.join(log + skipped[:20])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('mode', choices=['auto', 'daily'], nargs='?', default='auto')
    ap.add_argument('--dry', action='store_true')
    a = ap.parse_args()
    try:
        print(run(a.dry))
    except Exception:
        tb = traceback.format_exc()
        print(tb)
        if not a.dry:
            notify.send(f'Сбой авто-режима:\n{tb[-1500:]}')
        sys.exit(1)


if __name__ == '__main__':
    main()
