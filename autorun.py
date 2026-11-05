"""Автономный режим: работает без участия человека (в облаке GitHub Actions или на компьютере по расписанию).

  python autorun.py auto     — основной (облако, 00:00 / 06:00 / 12:00 / 18:00 по Ташкенту): входящие из Telegram
                               (файлы tennis-data, /stats) → переобучение через день в 00:00 или при новых файлах →
                               прогноз на матчи Большого шлема, 1000, 500, 250 и итоговых на 36 ч → таблица + Telegram →
                               расчёт доигранных, самокалибровка по собственным ошибкам
  python autorun.py daily    — только прогноз и расчёт
  python autorun.py weekly   — только переобучение и отчёт
  python autorun.py all      — weekly, затем daily
Ключ --dry: ничего не писать в таблицу и Telegram, только напечатать.
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
import live
import notify
import pipeline as P
from market import devig
from markov import Outcome

TOURS = ('wta', 'atp')


def _now() -> pd.Timestamp:
    return pd.Timestamp.now(tz='UTC').tz_localize(None)


def _days(a: dt.date, b: dt.date) -> list[dt.date]:
    return [a + dt.timedelta(days=i) for i in range((b - a).days + 1)]


def _id(x) -> str:
    try:
        return str(int(float(x)))
    except (TypeError, ValueError):
        return str(x).strip()


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


# ======================================================================== самокалибровка и уверенность
def _logit(p):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def calibration(auto: pd.DataFrame) -> tuple[float, int]:
    """Самокалибровка по собственным доигранным прогнозам: P' = σ(k·logit P).
    k > 1 — модель была слишком осторожной, k < 1 — слишком самоуверенной. Оценка сжимается к 1 с силой
    CALIB_PRIOR матчей, поэтому на малой выборке поправка мала и растёт по мере накопления результатов."""
    fin = auto[auto.status.astype(str) == 'FIN']
    if fin.empty:
        return 1.0, 0
    raw = fin.p1_raw.map(J.num)
    p = np.where(raw.notna(), raw, fin.p1.map(J.num))
    y = (fin.winner.map(J.num) == 1).values.astype(float)
    ok = ~np.isnan(p)
    x, y = _logit(p[ok]), y[ok]
    n = len(x)
    if n < 30:
        return 1.0, n
    ks = np.linspace(0.5, 1.6, 221)
    ll = [np.sum(np.logaddexp(0, k * x) - y * k * x) for k in ks]
    k_hat = float(ks[int(np.argmin(ll))])
    w = n / (n + C.CALIB_PRIOR)
    return round(1 + w * (k_hat - 1), 4), n


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


CONF_WORD = {1: 'очень низкая', 2: 'низкая', 3: 'средняя', 4: 'высокая', 5: 'очень высокая'}


# ======================================================================== авто-прогноз
def forecast_upcoming(tour: str, matches: list[espn.EspnMatch], done_ids: set[str], auto: pd.DataFrame,
                      k_cal: float) -> tuple[list[dict], list[str]]:
    md = P.model(tour)
    st, players = md.state, P._PLAYERS[tour]
    act = live.active_ids(st)
    now = _now()
    rows, skipped = [], []
    for m in matches:
        if m.state != 'pre' or m.tbd or m.qualifying or m.id in done_ids:
            continue
        if not (now < m.start <= now + pd.Timedelta(hours=C.HORIZON_HOURS)):
            continue
        level = live.level_for(tour, m.location, m.start, m.major)
        if level not in C.LEVELS:
            continue                                            # 125 / челленджеры / ITF / выставки — не нужны
        ids = [live.identify(n, players, act) for n in m.names]
        if any(i[0] is None for i in ids):
            skipped.append(f'{m.names[0]} — {m.names[1]} ({m.event}): игрок не опознан '
                           + ', '.join(f'{n}→{i[1]} {i[2]:.2f}' for n, i in zip(m.names, ids) if i[0] is None))
            continue
        date = m.start.normalize()
        surface = live.surface_for(tour, m.location, date)
        best_of = 5 if (m.major and tour == 'atp') else 3
        snap = st.snapshot(ids[0][0], ids[1][0], date, surface, m.location, date.year)
        p_raw = md.p_win(snap)
        p1 = float(1 / (1 + np.exp(-k_cal * _logit(p_raw))))
        f = FC.fair(md, snap, best_of, 10 if m.major else 7, p_override=p1)
        n1, n2 = int(st.n.get(ids[0][0], 0)), int(st.n.get(ids[1][0], 0))
        row = dict(created=J.now_utc(), espn_id=m.id, tour=tour.upper(), event=m.event, location=m.location, surface=surface,
                   round=m.round, best_of=best_of, start_utc=m.start.strftime('%Y-%m-%d %H:%M'), start_local=J.to_local(m.start),
                   player1=ids[0][1], player2=ids[1][1], player1_id=float(ids[0][0]), player2_id=float(ids[1][0]),
                   n1=n1, n2=n2, id_score1=round(ids[0][2], 3), id_score2=round(ids[1][2], 3),
                   p1=round(p1, 4), p2=round(1 - p1, 4), kfair1=round(1 / p1, 3), kfair2=round(1 / (1 - p1), 3),
                   kmin1=round((1 + C.EV_THRESHOLD) / p1, 3), kmin2=round((1 + C.EV_THRESHOLD) / (1 - p1), 3),
                   exp_total=round(f['exp_total'], 2), total_line=f['total_line'], p_over=round(f['p_over'], 4),
                   hcp1_line=f['hcp1_line'], p_hcp1=round(f['p_hcp1'], 4), set1_p1=round(f['set1_p1'], 4),
                   level=level, p1_raw=round(p_raw, 4), calib_k=k_cal, confidence=confidence(p1, min(n1, n2), auto))
        for k in J.EXACT_COLS:
            if k in f:
                row[k] = round(f[k], 4)
        rows.append(row)
    return rows, skipped


LEVEL_RU = {'Grand Slam': 'Большой шлем', '1000': '1000', '500': '500', '250': '250', 'Finals': 'Итоговый турнир'}
ROUND_RU = {'Final': 'финал', 'Semifinal': '1/2', 'Quarterfinal': '1/4', 'Round Robin': 'группа'}
SURF_RU = {'Hard': 'хард', 'Clay': 'грунт', 'Grass': 'трава'}


def _round_ru(r: str) -> str:
    if r in ROUND_RU:
        return ROUND_RU[r]
    m = __import__('re').search(r'(\d+)', r)
    return f'{m.group(1)}-й круг' if m else r


def match_block(r: dict) -> str:
    """Карточка матча (HTML)."""
    e = notify.esc
    p1, p2 = float(r['p1']), float(r['p2'])
    fav, dog = (1, 2) if p1 >= p2 else (2, 1)
    name = {1: r['player1'], 2: r['player2']}
    prob = {1: p1, 2: p2}
    kmin = {1: float(r['kmin1']), 2: float(r['kmin2'])}
    po = float(r['p_over'])
    t_side, t_p = ('Б', po) if po >= 0.5 else ('М', 1 - po)
    conf = int(r.get('confidence') or 3)
    lvl = LEVEL_RU.get(str(r.get('level')), str(r.get('level') or ''))
    head = f"🎾 <b>{e(r['tour'])} {lvl}</b> · {e(r['event'])}"
    sub = f"🕒 {e(r['start_local'])} · {e(_round_ru(str(r['round'])))} · {SURF_RU.get(r['surface'], r['surface'])}"
    lines = [head, sub, f"<b>{e(r['player1'])}</b> — <b>{e(r['player2'])}</b>", '',
             f"📊 Модель: {e(name[fav])} <b>{prob[fav] * 100:.0f}%</b> · {e(name[dog])} {prob[dog] * 100:.0f}%",
             f"✅ Ставка: <b>П{fav} {e(name[fav])}</b> при кф ≥ <b>{kmin[fav]:.2f}</b>",
             f"↔️ П{dog} {e(name[dog])} — только при кф ≥ {kmin[dog]:.2f}",
             f"📏 Тотал {float(r['total_line']):g}: <b>{t_side}</b> {t_p * 100:.0f}% · кф ≥ {(1 + C.EV_THRESHOLD) / t_p:.2f}",
             f"🎯 Уверенность: {'●' * conf}{'○' * (5 - conf)} {CONF_WORD[conf]}"]
    if min(int(r['n1']), int(r['n2'])) < 15:
        lines.append(f"⚠️ Мало данных: {min(int(r['n1']), int(r['n2']))} матч. у одного из игроков")
    return '\n'.join(lines)


def digest_blocks(rows: list[dict], k_cal: float, n_cal: int) -> list[str]:
    if not rows:
        return []
    head = (f"<b>Прогноз на ближайшие {C.HORIZON_HOURS} ч · {len(rows)} матч.</b>\n"
            f"Время — Ташкент. «кф ≥» — минимальный коэффициент 1xBet, при котором ставка выгодна по модели "
            f"(EV ≥ {C.EV_THRESHOLD * 100:.0f}%). Если в 1xBet меньше — пропуск."
            + (f"\nСамокалибровка: k = {k_cal:.3f} по {n_cal} доигранным матчам." if n_cal >= 30 else ''))
    rows = sorted(rows, key=lambda r: (r['start_utc'], r['tour'], r['event']))
    return [head] + [match_block(r) for r in rows]


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


def manual_results(matches: dict[str, list[espn.EspnMatch]], store: J.Store) -> list:
    """Доигранные матчи ESPN в виде (id1, id2, дата, исход, примечание) — только для игроков из ручных ставок."""
    bets = store.read('paper_bets')
    pend = bets[bets.settled.astype(str).str.strip() == '']
    out = []
    for tour in TOURS:
        sub = pend[pend.tour.astype(str).str.lower() == tour]
        if sub.empty:
            continue
        ids = set(sub.player1_id.map(J.num)) | set(sub.player2_id.map(J.num))
        players = P._PLAYERS[tour]
        for m in matches.get(tour, []):
            if m.state != 'post':
                continue
            a, b = (live.identify(n, players, ids)[0] for n in m.names)
            if a is None or b is None:
                continue
            if m.completed and m.status == 'STATUS_FINAL':
                out.append((a, b, m.start.normalize(), Outcome(tuple(m.sets)), ''))
            else:
                out.append((a, b, m.start.normalize(), Outcome(()), 'матч не доигран'))
    return out


def attach_odds(store: J.Store) -> int:
    """Закрывающие коэффициенты tennis-data к рассчитанным авто-прогнозам и бумажная ставка на победителя:
    сторона, где EV модели ≥ порога по Bet365 (без Bet365 — по среднему рынку); результат по Bet365, среднему и максимуму."""
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
                if ev1 >= C.EV_THRESHOLD and p1 >= C.MIN_PROB and ev1 >= ev2:
                    side = 1
                elif ev2 >= C.EV_THRESHOLD and 1 - p1 >= C.MIN_PROB:
                    side = 2
            df.at[i, 'bet_side'] = side
            win = int(J.num(r.winner)) if str(r.winner).strip() else None
            for bk in ('b365', 'avg', 'max'):
                k = odds[bk][side - 1] if side else np.nan
                df.at[i, f'pnl_{bk}'] = '' if (not side or np.isnan(k)) else round((k - 1) if win == side else -1.0, 3)
            upd.append(i)
    store.update('auto_forecasts', df.loc[upd], J.AUTO_ODDS_COLS)
    return len(upd)


# ======================================================================== сводка
def summary(store: J.Store) -> tuple[pd.DataFrame, str]:
    df = store.read('auto_forecasts')
    k_cal, n_cal = calibration(df)
    rows = [('обновлено (UTC)', J.now_utc()), ('авто-прогнозов всего', len(df)),
            ('самокалибровка k (1 — без поправки)', k_cal), ('матчей для самокалибровки', n_cal)]
    fin = df[df.status.astype(str) == 'FIN'].copy()
    lines = []
    if not fin.empty:
        p1 = fin.p1.map(J.num); w = fin.winner.map(J.num)
        pw = np.where(w == 1, p1, 1 - p1)
        ll = float(np.mean(-np.log(np.clip(pw, 1e-6, 1))))
        acc = float(np.mean(pw > 0.5))
        brier = float(np.mean((p1 - (w == 1)) ** 2))
        rows += [('доиграно матчей', len(fin)), ('лог-лосс модели', round(ll, 4)), ('точность модели', round(acc, 4)),
                 ('Brier модели', round(brier, 4))]
        lines.append(f'Модель на {len(fin)} доигранных матчах: лог-лосс {ll:.4f}, угадан победитель {acc * 100:.1f}%.')
        a1, a2 = fin.avg_1.map(J.num), fin.avg_2.map(J.num)
        ok = a1.notna() & a2.notna() & (a1 > 1) & (a2 > 1)
        if ok.sum() >= 10:
            pm = np.array([devig([x, y])[0] for x, y in zip(a1[ok], a2[ok])])
            wm = (w[ok] == 1).values
            ll_m = float(np.mean(-np.log(np.where(wm, pm, 1 - pm))))
            ll_s = float(np.mean(-np.log(np.where(wm, p1[ok], 1 - p1[ok]))))
            rows += [('матчей с коэффициентами', int(ok.sum())), ('лог-лосс модели на них', round(ll_s, 4)),
                     ('лог-лосс рынка (средний, без маржи)', round(ll_m, 4))]
            lines.append(f'Против рынка на {int(ok.sum())} матчах: модель {ll_s:.4f}, рынок {ll_m:.4f} '
                         f'({"модель точнее" if ll_s < ll_m else "рынок точнее"}; меньше — лучше).')
        for bk, nm in (('b365', 'Bet365'), ('avg', 'средний'), ('max', 'максимум')):
            pnl = df[f'pnl_{bk}'].map(J.num).dropna()
            if len(pnl):
                n = len(pnl); roi = pnl.mean() * 100
                ci = 1.96 * pnl.std(ddof=1) / n ** 0.5 * 100 if n > 1 else float('nan')
                rows += [(f'ставок на победителя ({nm})', n), (f'ROI % ({nm})', round(roi, 2)), (f'± 95 % ({nm})', round(ci, 2))]
                lines.append(f'Бумажные ставки на победителя по {nm}: {n} шт., ROI {roi:+.1f}% ± {ci:.1f}%.')
        bins = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
        pf = np.maximum(p1, 1 - p1); hit = pw >= 0.5
        for lo, hi in zip(bins[:-1], bins[1:]):
            mask = (pf >= lo) & (pf < hi + (1e-9 if hi == 1 else 0))
            if mask.sum():
                rows.append((f'калибровка {lo:.0%}–{hi:.0%}: прогноз / факт / n',
                             f'{pf[mask].mean() * 100:.1f}% / {hit[mask].mean() * 100:.1f}% / {int(mask.sum())}'))
    bets = store.read('paper_bets')
    if not bets.empty:
        rows.append(('ручные ставки', J.bets_summary(bets)))
    return pd.DataFrame(rows, columns=['показатель', 'значение']), '\n'.join(lines)


# ======================================================================== отчёт каждые 10 матчей
REPORT_EVERY = 10
BACKTEST = {'ll': 0.616, 'acc': 0.654}           # проверка 2023–2026 (WTA 0.6158 / 65.1 %, ATP 0.6155 / 65.7 %)


def _ll(p1: np.ndarray, w1: np.ndarray) -> float:
    return float(np.mean(-np.log(np.clip(np.where(w1, p1, 1 - p1), 1e-6, 1))))


def progress_report(df: pd.DataFrame) -> list[str]:
    """Отчёт для Telegram (HTML-блоки): последние 10 доигранных матчей + качество модели на всех доигранных."""
    e = notify.esc
    fin = df[df.status.astype(str) == 'FIN'].copy()
    if fin.empty:
        return []
    fin['p1n'] = fin.p1.map(J.num); fin['w1'] = fin.winner.map(J.num) == 1
    fin = fin.sort_values('start_utc')
    n = len(fin)
    p1, w1 = fin.p1n.values, fin.w1.values
    first, last = str(fin.start_utc.iloc[0])[:10], str(fin.start_utc.iloc[-1])[:10]
    head = (f'📈 <b>Отчёт модели · {n} доигранных матчей</b>\n'
            f'Период: {first} — {last}. Перешлите этот отчёт в чат с Claude для разбора.')
    lines = ['<b>Последние 10:</b>']
    for r in fin.tail(REPORT_EVERY).itertuples(index=False):
        fav = 1 if r.p1n >= 0.5 else 2
        pf = r.p1n if fav == 1 else 1 - r.p1n
        ok = (fav == 1) == bool(r.w1)
        name = r.player1 if fav == 1 else r.player2
        sc = str(r.score) if fav == 1 else ' '.join('-'.join(x.split('-')[::-1]) for x in str(r.score).split())
        lines.append(f"{'✅' if ok else '❌'} {e(r.tour)} {e(name)} {pf * 100:.0f}% · {e(sc)}")      # счёт с позиции фаворита
    recent = '\n'.join(lines)
    ll, acc = _ll(p1, w1), float(np.mean((p1 >= 0.5) == w1))
    brier = float(np.mean((p1 - w1) ** 2))
    q = [f'<b>Качество на всех {n}:</b>',
         f'Лог-лосс: <b>{ll:.4f}</b> (бэктест {BACKTEST["ll"]:.3f}; меньше — лучше)',
         f'Угадано победителей: <b>{acc * 100:.1f}%</b> (бэктест {BACKTEST["acc"] * 100:.1f}%)',
         f'Brier: {brier:.4f}']
    for t in ('WTA', 'ATP'):
        m = fin.tour.astype(str) == t
        if m.sum():
            q.append(f'{t}: n={int(m.sum())}, лог-лосс {_ll(p1[m.values], w1[m.values]):.4f}, '
                     f'угадано {np.mean((p1[m.values] >= 0.5) == w1[m.values]) * 100:.1f}%')
    pf = np.maximum(p1, 1 - p1); hit = (p1 >= 0.5) == w1
    cal = ['<b>Калибровка (прогноз / факт / n):</b>']
    for lo in (0.5, 0.6, 0.7, 0.8, 0.9):
        msk = (pf >= lo) & (pf < lo + 0.1 + (1e-9 if lo == 0.9 else 0))
        if msk.sum():
            cal.append(f'{lo * 100:.0f}–{lo * 100 + 10:.0f}%: {pf[msk].mean() * 100:.0f}% / {hit[msk].mean() * 100:.0f}% / {int(msk.sum())}')
    k, nk = calibration(df)
    cal.append(f'Самокалибровка k = {k:.3f} ' + (f'(по {nk} матчам)' if nk >= 30 else '(включится после 30 матчей)'))
    mk = []
    a1, a2 = fin.avg_1.map(J.num), fin.avg_2.map(J.num)
    okm = ((a1 > 1) & (a2 > 1)).values
    if okm.sum() >= 10:
        pm = np.array([devig([x, y])[0] for x, y in zip(a1[okm], a2[okm])])
        ll_m, ll_s = _ll(pm, w1[okm]), _ll(p1[okm], w1[okm])
        mk.append(f'<b>Против рынка</b> (n={int(okm.sum())}): модель {ll_s:.4f} · рынок {ll_m:.4f} → '
                  f'{"модель точнее" if ll_s < ll_m else "рынок точнее"}')
    for bk, nm in (('b365', 'Bet365'), ('avg', 'средний'), ('max', 'максимум')):
        pnl = df[f'pnl_{bk}'].map(J.num).dropna()
        if len(pnl) >= 2:
            ci = 1.96 * pnl.std(ddof=1) / len(pnl) ** 0.5 * 100
            mk.append(f'ROI ({nm}): {pnl.mean() * 100:+.1f}% ± {ci:.1f}% · ставок {len(pnl)}')
    if not mk:
        mk.append('Сравнение с рынком и ROI появятся после недельных файлов tennis-data с этими матчами.')
    return [head, recent, '\n'.join(q), '\n'.join(cal), '\n'.join(mk)]


# ======================================================================== режимы
def retrain_due(now_utc: pd.Timestamp | None = None) -> bool:
    """Переобучение через день в 00:00 по Ташкенту, начиная с RETRAIN_ANCHOR (запуск в 00:00 может прийти с задержкой)."""
    loc = (now_utc or _now()) + pd.Timedelta(hours=C.TZ_OFFSET_HOURS)
    return loc.hour < 3 and (loc.date() - C.RETRAIN_ANCHOR).days % 2 == 0


def retrain(store: J.Store | None) -> list[str]:
    import updater
    log = updater.rebuild()
    P._MODELS.clear()
    if store is not None:
        log.append(f'Коэффициенты tennis-data подтянуты к {attach_odds(store)} авто-прогнозам.')
    return log


def daily(dry: bool = False, store: J.Store | None = None) -> str:
    store = store or J.Store()
    auto = store.read('auto_forecasts')
    matches = collect(auto)
    log = []
    for tour in TOURS:
        n = live.refresh(P.model(tour), matches[tour], P._PLAYERS[tour])
        log.append(f'{tour.upper()}: матчей ESPN {len(matches[tour])}, докатано в форму {n}')
    k_cal, n_cal = calibration(auto)
    done = set(auto.espn_id.map(_id))
    new_rows, skipped = [], []
    for tour in TOURS:
        r, s = forecast_upcoming(tour, matches[tour], done, auto, k_cal)
        new_rows += r; skipped += s
    by_id = {m.id: m for tour in TOURS for m in matches[tour]}
    blocks = digest_blocks(new_rows, k_cal, n_cal)
    if dry:
        print('\n'.join(log)); print('\n\n'.join(blocks)); print('\n'.join(skipped))
        return 'dry'
    store.append('auto_forecasts', new_rows)
    n_before = int((auto.status.astype(str) == 'FIN').sum())
    after = settle_auto(store, by_id)
    n_after = int((after.status.astype(str) == 'FIN').sum())
    if n_after // REPORT_EVERY > n_before // REPORT_EVERY:            # пройдена очередная десятка доигранных матчей
        notify.send_blocks(progress_report(store.read('auto_forecasts')))
        log.append(f'отчёт отправлен: {n_after} доигранных матчей')
    manual = J.Journal(store).settle(manual_results(matches, store))
    if blocks:
        notify.send_blocks(blocks)
    summ, _ = summary(store)
    store.write_all('summary', summ)
    log += [f'самокалибровка k={k_cal} (n={n_cal})', f'новых прогнозов {len(new_rows)}, пропущено {len(skipped)}', manual]
    log += skipped[:20]
    return '\n'.join(log)


def weekly(dry: bool = False) -> str:
    log = retrain(None if dry else J.Store())
    if dry:
        return '\n'.join(log)
    store = J.Store()
    _, text = summary(store)
    report = 'Модель переобучена\n' + '\n'.join(log) + '\n\n' + (text or 'Доигранных авто-прогнозов пока нет.')
    notify.send(report)
    return report


def handle_inbox(store: J.Store) -> tuple[bool, list[str]]:
    """Файлы tennis-data и команды из Telegram. Возвращает (нужно ли переобучение, журнал)."""
    import updater
    files, cmds, note = notify.inbox()
    log = [note] if note else []
    need = False
    for chat, name, raw in files:
        try:
            rep = updater.save_file(raw)
            need = True
            notify.reply(chat, f'Файл принят: {rep}. Модель переобучится в этом запуске.')
            log.append(f'принят файл {name}: {rep}')
        except Exception as e:
            notify.reply(chat, f'Файл {name} не принят: {e}')
    for chat, text in cmds:
        t = text.lower().split('@')[0]
        if t in ('/stats', '/статистика'):
            _, s = summary(store)
            notify.reply(chat, s or 'Доигранных авто-прогнозов пока нет.')
        elif t == '/start':
            notify.reply(chat, 'Бот работает в облаке: 4 раза в сутки присылает прогнозы на матчи Большого шлема, '
                               '1000, 500 и 250. Раз в неделю пришлите сюда два файла 2026.xlsx с tennis-data.co.uk '
                               '(WTA и ATP) — их заберёт ближайший запуск. /stats — качество модели.')
        elif t.startswith('#ошибка'):
            notify.reply(chat, text)
    return need, log


def auto_mode(dry: bool = False) -> str:
    """Единый запуск 4 раза в сутки: входящие из Telegram → переобучение (через день в 00:00 или при новых файлах)
    → прогноз и расчёт."""
    store = J.Store()
    need, log = (False, []) if dry else handle_inbox(store)
    if need or retrain_due():
        rep = retrain(None if dry else store)
        log += rep
        if not dry:
            _, text = summary(store)
            notify.send('Модель переобучена\n' + '\n'.join(rep) + ('\n\n' + text if text else ''))
    log.append(daily(dry, store))
    return '\n'.join(log)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('mode', choices=['auto', 'daily', 'weekly', 'all'])
    ap.add_argument('--dry', action='store_true')
    a = ap.parse_args()
    try:
        if a.mode == 'auto':
            print(auto_mode(a.dry))
        if a.mode in ('weekly', 'all'):
            print(weekly(a.dry))
        if a.mode in ('daily', 'all'):
            print(daily(a.dry))
    except Exception:
        tb = traceback.format_exc()
        print(tb)
        if not a.dry:
            notify.send(f'Сбой авто-режима ({a.mode}):\n{tb[-1500:]}')
        sys.exit(1)


if __name__ == '__main__':
    main()
