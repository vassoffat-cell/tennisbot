"""Автономный режим: работает без участия человека (в облаке GitHub Actions или на компьютере по расписанию).

  python autorun.py daily    — 4 раза в сутки: расписание ESPN → свежая форма → прогноз на матчи ближайших 36 ч →
                               лист auto_forecasts + сообщение в Telegram; расчёт доигранных матчей (авто и ручных)
  python autorun.py weekly   — по понедельникам: скачать tennis-data → переобучить модель → подтянуть закрывающие
                               коэффициенты к авто-прогнозам → бумажный результат → недельный отчёт
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


# ======================================================================== авто-прогноз
def forecast_upcoming(tour: str, matches: list[espn.EspnMatch], done_ids: set[str]) -> tuple[list[dict], list[str]]:
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
        if not (m.major or live.known_location(tour, m.location)):
            continue                                            # 125 / челленджеры / выставки — вне модели
        ids = [live.identify(n, players, act) for n in m.names]
        if any(i[0] is None for i in ids):
            skipped.append(f'{m.names[0]} — {m.names[1]} ({m.event}): игрок не опознан '
                           + ', '.join(f'{n}→{i[1]} {i[2]:.2f}' for n, i in zip(m.names, ids) if i[0] is None))
            continue
        date = m.start.normalize()
        surface = live.surface_for(tour, m.location, date)
        best_of = 5 if (m.major and tour == 'atp') else 3
        snap = st.snapshot(ids[0][0], ids[1][0], date, surface, m.location, date.year)
        f = FC.fair(md, snap, best_of, 10 if m.major else 7)
        p1 = f['p1']
        row = dict(created=J.now_utc(), espn_id=m.id, tour=tour.upper(), event=m.event, location=m.location, surface=surface,
                   round=m.round, best_of=best_of, start_utc=m.start.strftime('%Y-%m-%d %H:%M'), start_local=J.to_local(m.start),
                   player1=ids[0][1], player2=ids[1][1], player1_id=float(ids[0][0]), player2_id=float(ids[1][0]),
                   n1=int(st.n.get(ids[0][0], 0)), n2=int(st.n.get(ids[1][0], 0)),
                   id_score1=round(ids[0][2], 3), id_score2=round(ids[1][2], 3),
                   p1=round(p1, 4), p2=round(1 - p1, 4), kfair1=round(1 / p1, 3), kfair2=round(1 / (1 - p1), 3),
                   kmin1=round((1 + C.EV_THRESHOLD) / p1, 3), kmin2=round((1 + C.EV_THRESHOLD) / (1 - p1), 3),
                   exp_total=round(f['exp_total'], 2), total_line=f['total_line'], p_over=round(f['p_over'], 4),
                   hcp1_line=f['hcp1_line'], p_hcp1=round(f['p_hcp1'], 4), set1_p1=round(f['set1_p1'], 4))
        for k in J.EXACT_COLS:
            if k in f:
                row[k] = round(f[k], 4)
        rows.append(row)
    return rows, skipped


def digest(rows: list[dict]) -> str:
    """Сообщение в Telegram: прогноз на каждый матч и минимальный коэффициент для ставки (EV ≥ порога)."""
    if not rows:
        return ''
    out = [f'Авто-прогноз: {len(rows)} матч(ей). Время — Ташкент. K≥ — минимальный коэффициент 1xBet, '
           f'при котором ставка даёт EV ≥ {C.EV_THRESHOLD * 100:.0f}% по модели.', '']
    for r in sorted(rows, key=lambda r: (r['start_utc'], r['tour'], r['event'])):
        fav = 1 if r['p1'] >= 0.5 else 2
        out.append(f"{r['start_local']} {r['tour']} {r['event']} · {r['round']} · {r['surface']}")
        out.append(f"  {r['player1']} {r['p1'] * 100:.0f}% (K≥{r['kmin1']:.2f}) — {r['player2']} {r['p2'] * 100:.0f}% (K≥{r['kmin2']:.2f})")
        h = r['hcp1_line'] if fav == 1 else -r['hcp1_line']
        out.append(f"  тотал {r['total_line']:g}: Б {r['p_over'] * 100:.0f}% / М {(1 - r['p_over']) * 100:.0f}% · "
                   f"фора Ф{fav}({h:+g}) ≈50% · 1-й сет П1 {r['set1_p1'] * 100:.0f}%"
                   + (f" · мало данных ({min(r['n1'], r['n2'])} матч.)" if min(r['n1'], r['n2']) < 15 else ''))
    return '\n'.join(out)


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
    rows = [('обновлено (UTC)', J.now_utc()), ('авто-прогнозов всего', len(df))]
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


# ======================================================================== режимы
def daily(dry: bool = False) -> str:
    store = J.Store()
    auto = store.read('auto_forecasts')
    matches = collect(auto)
    log = []
    for tour in TOURS:
        n = live.refresh(P.model(tour), matches[tour], P._PLAYERS[tour])
        log.append(f'{tour.upper()}: матчей ESPN {len(matches[tour])}, докатано в форму {n}')
    done = set(auto.espn_id.map(_id))
    new_rows, skipped = [], []
    for tour in TOURS:
        r, s = forecast_upcoming(tour, matches[tour], done)
        new_rows += r; skipped += s
    by_id = {m.id: m for tour in TOURS for m in matches[tour]}
    if dry:
        print('\n'.join(log)); print(digest(new_rows)); print('\n'.join(skipped))
        return 'dry'
    store.append('auto_forecasts', new_rows)
    settled = settle_auto(store, by_id)
    j = J.Journal(store)
    manual = j.settle(manual_results(matches, store))
    msg = digest(new_rows)
    if msg:
        if skipped:
            msg += '\n\nПропущено (игрок не опознан): ' + str(len(skipped))
        notify.send(notify.esc(msg), pre=False)
    summ, _ = summary(store)
    store.write_all('summary', summ)
    log += [f'новых прогнозов {len(new_rows)}, пропущено {len(skipped)}', manual]
    log += skipped[:20]
    return '\n'.join(log)


def weekly(dry: bool = False) -> str:
    import updater
    log = updater.update_all()
    log += updater.rebuild()
    P._MODELS.clear()
    if dry:
        return '\n'.join(log)
    store = J.Store()
    n = attach_odds(store)
    summ, text = summary(store)
    store.write_all('summary', summ)
    report = ('Еженедельное обновление\n' + '\n'.join(log) + f'\nКоэффициенты подтянуты к {n} авто-прогнозам.\n\n'
              + (text or 'Доигранных авто-прогнозов пока нет.'))
    notify.send(notify.esc(report))
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('mode', choices=['daily', 'weekly', 'all'])
    ap.add_argument('--dry', action='store_true')
    a = ap.parse_args()
    try:
        if a.mode in ('weekly', 'all'):
            print(weekly(a.dry))
        if a.mode in ('daily', 'all'):
            print(daily(a.dry))
    except Exception:
        tb = traceback.format_exc()
        print(tb)
        if not a.dry:
            notify.send(notify.esc(f'Сбой авто-режима ({a.mode}):\n{tb[-1500:]}'))
        sys.exit(1)


if __name__ == '__main__':
    main()
