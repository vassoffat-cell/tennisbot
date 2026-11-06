"""Автономный режим в облаке (GitHub Actions каждые 2 часа). Работает без участия человека.

Цикл одного запуска:
  1. входящие файлы tennis-data из папки Google Диска (их кладёт туда бот) → переобучение;
  2. переобучение через день в 00:00 по Ташкенту;
  3. расписание и результаты ESPN → форма игроков, рейтинги ATP/WTA;
  4. прогноз на матчи Большого шлема, 1000, 500, 250 и итоговых в ближайшие 24 ч: 7 линий с диапазоном кф;
     до начала матча прогноз обновляется каждые 2 часа (в таблице — последний перед матчем);
  5. расчёт доигранных матчей и всех линий → самокалибровка по каждому рынку;
  6. в Telegram: свежий список на 24 ч, прошлый список удаляется; без матчей — тишина;
     отчёт для Claude после каждых 10 доигранных матчей; итоги самообучения в 00:00.

  python autorun.py auto [--dry]
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
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
import notify
import pipeline as P
import runtime
from market import devig
from markov import Outcome

TOURS = ('wta', 'atp')
REPORT_EVERY = 10
BACKTEST = {'ll': 0.616, 'acc': 0.654}           # проверка 2023–2026 (WTA 0.6158 / 65.1 %, ATP 0.6155 / 65.7 %)
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
    """k для фор и тоталов по рассчитанным линиям (W/L) этого рынка."""
    out = {}
    done = lines_df[lines_df.result.astype(str).isin(['W', 'L'])]
    for m in ('HCP', 'IT1', 'IT2', 'TOTAL'):
        sub = done[done.market.astype(str) == m]
        out[m] = LN.calibrate(sub.p_raw.map(J.num).values.astype(float), (sub.result.astype(str) == 'W').values)
    return out


def confidence(p: float, n_min: int, auto: pd.DataFrame) -> int:
    """Уверенность в прогнозе 1–5: объём данных по игрокам + фактическая точность модели на живых матчах
    в той же корзине вероятности + сравнение с рынком (когда есть коэффициенты)."""
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
        a1, a2 = fin.avg_1.map(J.num), fin.avg_2.map(J.num)
        ok = (a1 > 1) & (a2 > 1)
        if ok.sum() >= 100:
            pm = np.array([devig([x, y])[0] for x, y in zip(a1[ok], a2[ok])])
            wm = w1[ok.values]
            ll_m = np.mean(-np.log(np.where(wm, pm, 1 - pm)))
            ll_s = np.mean(-np.log(np.where(wm, p1[ok.values], 1 - p1[ok.values])))
            s += -1 if ll_s > ll_m + 0.02 else 1 if ll_s < ll_m else 0
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
    for name, rank, _ in rk:
        pid = live.identify(name, P._PLAYERS[tour], act)[0]
        if pid is not None:
            md.state.rank[pid] = float(rank)
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
        lns = LN.build(D, p1, kc)
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
        store.update('auto_lines', pd.DataFrame.from_dict(upd_lines, orient='index'), J.LINE_FCST_COLS)
    return len(new_rows), len(upd_rows)


# ======================================================================== список в Telegram
def _last(name: str) -> str:
    return str(name).split()[-1] if str(name).strip() else str(name)


LINE_TITLE = {1: 'П1 {p1}', 2: 'П2 {p2}', 3: 'Фора по геймам 1 ({line})', 4: 'Фора по геймам 2 ({line})',
              5: 'Инд. тотал 1 · {label}', 6: 'Инд. тотал 2 · {label}', 7: 'Тотал · {label}'}


def shown(x: dict) -> bool:
    return not _blank(x.get('z_lo'))


def match_card(row: dict, lns: list[dict]) -> str | None:
    """Карточка матча: только линии, диапазон которых попадает в зону кф ODDS_ZONE. Нет таких — матч не показывается."""
    e = notify.esc
    vis = [x for x in sorted(lns, key=lambda x: x['slot']) if shown(x)]
    if not vis:
        return None
    p1 = float(row['p1'])
    fav = row['player1'] if p1 >= 0.5 else row['player2']
    conf = int(row.get('confidence') or 3)
    lvl = LEVEL_RU.get(str(row.get('level')), str(row.get('level') or ''))
    out = [f"🎾 <b>{e(row['tour'])} {lvl}</b> · {e(row['event'])}",
           f"🕒 {e(row['start_local'])} · {e(_round_ru(str(row['round'])))} · {SURF_RU.get(row['surface'], row['surface'])}",
           f"<b>{e(row['player1'])}</b> — <b>{e(row['player2'])}</b>", '']
    for x in vis:
        line = f"{float(x['line']):+g}" if not _blank(x.get('line')) else ''
        title = LINE_TITLE[x['slot']].format(p1=_last(row['player1']), p2=_last(row['player2']), line=line, label=x['label'])
        out.append(f"{e(title)}: если кф <b>{float(x['z_lo']):.2f} – {float(x['z_hi']):.2f}</b>")
    out.append(f"🎯 Уверенность {'●' * conf}{'○' * (5 - conf)} {CONF_WORD[conf]} · модель: {e(_last(fav))} {max(p1, 1 - p1) * 100:.0f}%")
    return '\n'.join(out)


def list_blocks(items: list[tuple]) -> list[str]:
    """Список на 24 ч: матчи одного турнира подряд, турниры — по времени первого матча."""
    if not items:
        return []
    first = {}
    for m, row, _ in items:
        k = (row['tour'], row['event'])
        first[k] = min(first.get(k, row['start_utc']), row['start_utc'])
    items = sorted(items, key=lambda t: (first[(t[1]['tour'], t[1]['event'])], t[1]['tour'], t[1]['event'], t[1]['start_utc']))
    cards = [c for c in (match_card(row, lns) for _, row, lns in items) if c]
    if not cards:
        return []
    z = C.ODDS_ZONE
    head = (f"📋 <b>Прогноз на 24 ч</b> · обновлено {_local().strftime('%d.%m %H:%M')} · {len(cards)} матч.\n"
            f"Линии подобраны под кф {z[0]:.2f}–{z[1]:.2f}. Ставить, только если кф 1xBet <b>внутри диапазона</b>: ниже — нет "
            f"выгоды, выше — расхождение с рынком слишком велико. Список обновляется каждые 2 часа, прошлый удаляется.")
    return [head] + cards


def extra_messages(store: J.Store) -> list[tuple[int, int]]:
    """Списки, запрошенные кнопкой в боте (лист tg_messages): удаляются вместе с плановым списком."""
    df = store.read('tg_messages')
    out = [(int(J.num(c)), int(J.num(m))) for c, m in zip(df.chat_id, df.message_id)
           if not np.isnan(J.num(c)) and not np.isnan(J.num(m))]
    if len(df):
        store.write_all('tg_messages', pd.DataFrame(columns=J.SHEETS['tg_messages']))
    return out


def publish_list(rt: dict, items: list[tuple], dry: bool = False, extra: list | None = None) -> str:
    """Удалить прошлый список и отправить свежий. Нет матчей — тишина. Звук — только если появились новые матчи."""
    items = [t for t in items if any(shown(x) for x in t[2])]
    ids = sorted(m.id for m, _, _ in items)
    if dry:
        print('\n\n'.join(list_blocks(items)))
        return 'dry'
    removed = notify.delete([tuple(x) for x in rt.get('list_messages', [])] + list(extra or []))
    sent = []
    if items:
        silent = set(ids) <= set(rt.get('list_match_ids', []))
        sent = notify.send_blocks(list_blocks(items), silent=silent)
    rt['list_messages'] = [list(x) for x in sent]
    rt['list_match_ids'] = ids
    return f'список: удалено {removed}, отправлено {len(sent)} сообщ., матчей {len(ids)}'


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
    """Закрывающие коэффициенты tennis-data к рассчитанным прогнозам и проверочная ставка на победителя (1 ед.):
    сторона, где кф Bet365 внутри диапазона [1,05/P; 1,20/P] (без Bet365 — по среднему рынку); результат по Bet365, среднему и максимуму."""
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
            odds = {}
            for bk, (cw, cl) in {'b365': ('B365W', 'B365L'), 'avg': ('AvgW', 'AvgL'), 'max': ('MaxW', 'MaxL'),
                                 'bfe': ('BFEW', 'BFEL')}.items():
                kw, kl = J.num(x.get(cw)), J.num(x.get(cl))
                odds[bk] = (kw, kl) if a_won else (kl, kw)
                df.at[i, f'{bk}_1'], df.at[i, f'{bk}_2'] = odds[bk]
            p1 = J.num(r.p1)
            ref = odds['b365'] if not np.isnan(odds['b365'][0]) else odds['avg']
            side = ''
            if not np.isnan(ref[0]):
                ev1, ev2 = p1 * ref[0] - 1, (1 - p1) * ref[1] - 1
                inside = lambda ev: C.EV_THRESHOLD <= ev <= C.EV_MAX          # кф внутри диапазона из списка
                if inside(ev1) and p1 >= C.MIN_PROB:
                    side = 1
                elif inside(ev2) and 1 - p1 >= C.MIN_PROB:
                    side = 2
            df.at[i, 'bet_side'] = side
            win = int(J.num(r.winner)) if str(r.winner).strip() else None
            for bk in ('b365', 'avg', 'max'):
                k = odds[bk][side - 1] if side else np.nan
                df.at[i, f'pnl_{bk}'] = '' if (not side or np.isnan(k)) else round((k - 1) if win == side else -1.0, 3)
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
        k = J.num(r.z_lo) if not _blank(r.z_lo) else J.num(r.k_lo)          # нижняя граница показанного диапазона
        df.loc[i, J.LINE_RESULT_COLS] = [status, res, today, round({'W': k - 1, 'L': -1.0, 'P': 0.0}[res], 3)]
        upd.append(i)
    store.update('auto_lines', df.loc[upd], J.LINE_RESULT_COLS)
    return len(upd)


# ======================================================================== статистика
def line_stats(lines_df: pd.DataFrame, kc: dict | None = None) -> list[str]:
    """По рынкам (только показанные в списке линии): число, попадание факт / модель, ROI при ставке по нижнему кф, k."""
    done = lines_df[lines_df.result.astype(str).isin(['W', 'L']) & ~lines_df.z_lo.map(_blank).astype(bool)]   # только показанные
    out = []
    for mk in MARKETS:
        sub = done[done.market.astype(str) == mk]
        if sub.empty:
            continue
        won = (sub.result.astype(str) == 'W').values
        p = sub.p.map(J.num).values
        roi = np.nanmean(sub.pnl_lo.map(J.num).values) * 100
        k = '' if not kc or mk not in kc else f' · k={kc[mk][0]:.3f}'
        out.append(f'{LN.MARKET_RU[mk]}: {len(sub)} · попадание {won.mean() * 100:.0f}% (модель {np.nanmean(p) * 100:.0f}%) · '
                   f'ROI при мин. кф {roi:+.1f}%{k}')
    return out


def _ll(p1: np.ndarray, w1: np.ndarray) -> float:
    return float(np.mean(-np.log(np.clip(np.where(w1, p1, 1 - p1), 1e-6, 1))))


def match_stats(auto: pd.DataFrame) -> list[str]:
    fin = auto[auto.status.astype(str) == 'FIN']
    if fin.empty:
        return []
    p1, w1 = fin.p1.map(J.num).values, (fin.winner.map(J.num) == 1).values
    out = [f'Матчей: {len(fin)} · угадано победителей {np.mean((p1 >= 0.5) == w1) * 100:.1f}% (бэктест {BACKTEST["acc"] * 100:.1f}%) · '
           f'лог-лосс {_ll(p1, w1):.4f} (бэктест {BACKTEST["ll"]:.3f})']
    a1, a2 = fin.avg_1.map(J.num), fin.avg_2.map(J.num)
    okm = ((a1 > 1) & (a2 > 1)).values
    if okm.sum() >= 10:
        pm = np.array([devig([x, y])[0] for x, y in zip(a1[okm], a2[okm])])
        ll_m, ll_s = _ll(pm, w1[okm]), _ll(p1[okm], w1[okm])
        out.append(f'Против рынка (n={int(okm.sum())}): модель {ll_s:.4f} · рынок {ll_m:.4f} → '
                   f'{"модель точнее" if ll_s < ll_m else "рынок точнее"}')
    for bk, nm in (('b365', 'Bet365'), ('avg', 'средний'), ('max', 'максимум')):
        pnl = auto[f'pnl_{bk}'].map(J.num).dropna()
        if len(pnl) >= 2:
            ci = 1.96 * pnl.std(ddof=1) / len(pnl) ** 0.5 * 100
            out.append(f'ROI на победителя по {nm} (кф в диапазоне): {pnl.mean() * 100:+.1f}% ± {ci:.1f}% · ставок {len(pnl)}')
    return out


def summary(store: J.Store) -> tuple[pd.DataFrame, str]:
    auto, ldf = store.read('auto_forecasts'), store.read('auto_lines')
    k, n = calibration(auto)
    kc = market_calibration(ldf)
    ms, ls = match_stats(auto), line_stats(ldf, {'ML': (k, n), **kc})
    rows = [('обновлено (UTC)', J.now_utc()), ('авто-прогнозов', len(auto)), ('линий в журнале', len(ldf))]
    rows += [('матчи', x) for x in ms] + [('линии', x) for x in ls]
    return pd.DataFrame(rows, columns=['показатель', 'значение']), '\n'.join(ms + ls)


def progress_report(auto: pd.DataFrame, ldf: pd.DataFrame) -> list[str]:
    """Отчёт для пересылки Claude: последние 10 матчей, качество, калибровка, линии по рынкам."""
    e = notify.esc
    fin = auto[auto.status.astype(str) == 'FIN'].copy()
    if fin.empty:
        return []
    fin['p1n'] = fin.p1.map(J.num); fin['w1'] = fin.winner.map(J.num) == 1
    fin = fin.sort_values('start_utc')
    p1, w1 = fin.p1n.values, fin.w1.values
    head = (f'📈 <b>Отчёт модели · {len(fin)} доигранных матчей</b>\n'
            f'Период: {str(fin.start_utc.iloc[0])[:10]} — {str(fin.start_utc.iloc[-1])[:10]}. '
            f'Перешлите этот отчёт в чат с Claude для разбора.')
    recent = ['<b>Последние 10:</b>']
    for r in fin.tail(REPORT_EVERY).itertuples(index=False):
        fav = 1 if r.p1n >= 0.5 else 2
        sc = str(r.score) if fav == 1 else ' '.join('-'.join(x.split('-')[::-1]) for x in str(r.score).split())
        recent.append(f"{'✅' if (fav == 1) == bool(r.w1) else '❌'} {e(r.tour)} {e(r.player1 if fav == 1 else r.player2)} "
                      f"{max(r.p1n, 1 - r.p1n) * 100:.0f}% · {e(sc)}")
    q = ['<b>Качество:</b>'] + [e(x) for x in match_stats(auto)]
    for t in ('WTA', 'ATP'):
        msk = (fin.tour.astype(str) == t).values
        if msk.sum():
            q.append(f'{t}: n={int(msk.sum())}, лог-лосс {_ll(p1[msk], w1[msk]):.4f}, угадано {np.mean((p1[msk] >= 0.5) == w1[msk]) * 100:.1f}%')
    pf, hit = np.maximum(p1, 1 - p1), (p1 >= 0.5) == w1
    cal = ['<b>Калибровка победителя (прогноз / факт / n):</b>']
    for lo in (0.5, 0.6, 0.7, 0.8, 0.9):
        msk = (pf >= lo) & (pf < lo + 0.1 + (1e-9 if lo == 0.9 else 0))
        if msk.sum():
            cal.append(f'{lo * 100:.0f}–{lo * 100 + 10:.0f}%: {pf[msk].mean() * 100:.0f}% / {hit[msk].mean() * 100:.0f}% / {int(msk.sum())}')
    k, nk = calibration(auto)
    kc = market_calibration(ldf)
    ln = ['<b>Линии по рынкам:</b>'] + [e(x) for x in line_stats(ldf, {'ML': (k, nk), **kc})]
    ln.append('Самокалибровка: ' + ', '.join(f'{LN.MARKET_RU[m]} k={v[0]:.3f} (n={v[1]})' for m, v in {'ML': (k, nk), **kc}.items()))
    return [head, '\n'.join(recent), '\n'.join(q), '\n'.join(cal), '\n'.join(ln)]


def daily_learning(auto: pd.DataFrame, ldf: pd.DataFrame, retrain_log: list[str]) -> list[str]:
    """Итоги самообучения за сутки (00:00 по Ташкенту)."""
    e = notify.esc
    day = _local().strftime('%d.%m.%Y')
    since = (_now() - pd.Timedelta(hours=24)).strftime('%Y-%m-%d %H:%M')
    fin = auto[(auto.status.astype(str) == 'FIN')]
    last = fin[fin.start_utc.astype(str) >= since]
    blocks = [f'🧠 <b>Итоги самообучения · {day}</b>']
    if len(last):
        p1, w1 = last.p1.map(J.num).values, (last.winner.map(J.num) == 1).values
        ok = int(np.sum((p1 >= 0.5) == w1))
        blocks.append(f'За сутки доиграно {len(last)} матч. · фаворит модели выиграл {ok} из {len(last)}')
    else:
        blocks.append('За сутки доигранных матчей нет.')
    allm = match_stats(auto)
    if allm:
        blocks.append('<b>Всего:</b>\n' + '\n'.join(e(x) for x in allm))
    k, nk = calibration(auto)
    kc = market_calibration(ldf)
    ls = line_stats(ldf, {'ML': (k, nk), **kc})
    if ls:
        blocks.append('<b>Линии по рынкам:</b>\n' + '\n'.join(e(x) for x in ls))
    blocks.append('<b>Самокалибровка</b> (1 — без поправки; включается после 30 рассчитанных):\n'
                  + '\n'.join(f'{LN.MARKET_RU[m]}: k={v[0]:.3f} · n={v[1]}' for m, v in {'ML': (k, nk), **kc}.items()))
    blocks.append('<b>Переобучение:</b> ' + (e('; '.join(retrain_log)) if retrain_log else 'сегодня не было (через день)'))
    return blocks


# ======================================================================== режимы
def retrain_due(rt: dict, now_utc: pd.Timestamp | None = None) -> bool:
    """Через день в 00:00 по Ташкенту, начиная с RETRAIN_ANCHOR; один раз за дату."""
    loc = _local(now_utc)
    return (loc.hour < 2 and (loc.date() - C.RETRAIN_ANCHOR).days % 2 == 0
            and rt.get('last_retrain') != loc.date().isoformat())


def retrain(store: J.Store | None) -> list[str]:
    import updater
    log = updater.rebuild()
    P._MODELS.clear()
    if store is not None:
        log.append(f'коэффициенты tennis-data подтянуты к {attach_odds(store)} матчам')
    return log


def take_inbox() -> tuple[bool, list[str]]:
    """Файлы tennis-data из папки Google Диска (кладёт бот). Возвращает (были ли новые файлы, журнал)."""
    import inbox
    import updater
    try:
        files, note = inbox.fetch()
    except Exception as e:
        return False, [f'папка входящих недоступна: {e}']
    log, got = ([note] if note else []), False
    for fid, name, raw in files:
        try:
            rep = updater.save_file(raw)
            got = True
            log.append(f'файл принят: {rep}')
        except Exception as e:
            log.append(f'файл {name} не принят: {e}')
        inbox.mark_done(fid, name)
    if files:
        notify.send('Файлы из бота обработаны:\n' + '\n'.join(log))
    return got, log


def run(dry: bool = False) -> str:
    store, rt = J.Store(), runtime.load()
    log = []
    got, ilog = (False, []) if dry else take_inbox()
    log += ilog
    retrain_log = []
    due = retrain_due(rt)
    if got or due:
        retrain_log = retrain(None if dry else store)
        log += retrain_log
        if due:
            rt['last_retrain'] = _local().date().isoformat()
    auto, ldf = store.read('auto_forecasts'), store.read('auto_lines')
    matches = collect(auto)
    for tour in TOURS:
        n = live.refresh(P.model(tour), matches[tour], P._PLAYERS[tour])
        r = apply_rankings(tour)
        log.append(f'{tour.upper()}: матчей ESPN {len(matches[tour])}, докатано в форму {n}, рейтинг обновлён у {r}')
    k_ml, n_ml = calibration(auto)
    kcal = market_calibration(ldf)
    kc = {m: v[0] for m, v in kcal.items()}
    items, skipped = [], []
    for tour in TOURS:
        it, sk = forecast_all(tour, matches[tour], auto, k_ml, kc)
        items += it; skipped += sk
    if dry:
        print('\n'.join(log)); publish_list(rt, items, dry=True); print('\n'.join(skipped))
        return 'dry'
    nn, nu = save_forecasts(store, items, auto, ldf)
    by_id = {m.id: m for tour in TOURS for m in matches[tour]}
    n_before = int((auto.status.astype(str) == 'FIN').sum())
    after = settle_auto(store, by_id)
    nl = settle_lines(store, by_id)
    n_after = int((after.status.astype(str) == 'FIN').sum())
    auto, ldf = store.read('auto_forecasts'), store.read('auto_lines')
    if n_after // REPORT_EVERY > n_before // REPORT_EVERY:
        notify.send_blocks(progress_report(auto, ldf))
        log.append(f'отчёт для Claude: {n_after} доигранных матчей')
    log.append(publish_list(rt, items, extra=extra_messages(store)))
    today = _local().date().isoformat()
    if _local().hour < 2 and rt.get('last_daily') != today:
        notify.send_blocks(daily_learning(auto, ldf, retrain_log))
        rt['last_daily'] = today
        log.append('итоги самообучения отправлены')
    summ, _ = summary(store)
    store.write_all('summary', summ)
    runtime.save(rt)
    log += [f'k: победа {k_ml} (n={n_ml}), ' + ', '.join(f'{m} {v[0]} (n={v[1]})' for m, v in kcal.items()),
            f'прогнозов: новых {nn}, обновлено {nu}; рассчитано линий {nl}; пропущено {len(skipped)}']
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
