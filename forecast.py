"""Независимый прогноз матча по всем распознанным линиям 1xBet (режим бумажных ставок).

1. Модель (без котировок) даёт P(A выиграет матч) и вероятности очка на подаче по Barnett–Clarke.
2. Разница подач подгоняется так, чтобы цепь с momentum дала ровно P(A) модели; уровень подачи — из данных.
3. Из одной цепи считаются все линии: P_model, K_fair, EV по цене 1xBet. Сигнал — EV ≥ порога.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import brentq

import config as C
import market as K
import markets as MK
from markov import match_distribution
from ocr import Parsed
from state import Model, Snapshot

TAB_SET = {'set1': 0, 'set2': 1}
MARKET_RU = {'ML': 'Победитель', 'TOTAL': 'Тотал', 'HCP': 'Фора', 'IT1': 'ИТ1', 'IT2': 'ИТ2', 'EXACT': 'Точный счёт'}


@dataclass
class Row:
    tab: str
    market: str
    line: object
    side: str
    p_model: float
    p_push: float
    k_fair: float
    k_book: float
    ev: float
    signal: bool


@dataclass
class Forecast:
    p_a: float
    pa: float
    pb: float
    exp_games: float
    rows: list[Row] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _settles(tab: str, market: str, line):
    """(название стороны 1, расчёт 1, название стороны 2, расчёт 2) для рынка 1xBet."""
    if tab == 'main':
        if market == 'ML':
            return 'П1', MK.mk_winner(True), 'П2', MK.mk_winner(False)
        if market == 'TOTAL':
            return f'Б {line:g}', MK.mk_total(line, True), f'М {line:g}', MK.mk_total(line, False)
        if market == 'HCP':
            return f'Ф1({line:+g})', MK.mk_handicap(True, line), f'Ф2({-line:+g})', MK.mk_handicap(False, -line)
        if market in ('IT1', 'IT2'):
            a = market == 'IT1'
            return f'Б {line:g}', MK.mk_ind_total(a, line, True), f'М {line:g}', MK.mk_ind_total(a, line, False)
    i = TAB_SET.get(tab)
    if i is not None:
        if market == 'ML':
            return 'П1', MK.mk_set_winner(i, True), 'П2', MK.mk_set_winner(i, False)
        if market == 'TOTAL':
            return f'Б {line:g}', MK.mk_set_total(i, line, True), f'М {line:g}', MK.mk_set_total(i, line, False)
        if market == 'HCP':
            return (f'Ф1({line:+g})', MK.mk_set_handicap_games(i, True, line),
                    f'Ф2({-line:+g})', MK.mk_set_handicap_games(i, False, -line))
        if market in ('IT1', 'IT2'):
            a = market == 'IT1'
            return f'Б {line:g}', MK.mk_set_ind_total(i, a, line, True), f'М {line:g}', MK.mk_set_ind_total(i, a, line, False)
    return None


def distribution(model: Model, snap: Snapshot, best_of: int = 3, final_tb_to: int = 7,
                 p_override: float | None = None) -> tuple[Forecast, dict]:
    """Распределение исходов матча, согласованное с моделью: разница подач подбирается так, чтобы P(A) цепи = P(A) модели
    (или p_override — вероятность после самокалибровки)."""
    mom, smom = C.MOMENTUM[model.tour]
    p_star = model.p_win(snap) if p_override is None else p_override
    dist = lambda d: match_distribution(np.clip(snap.pa_bc + d, 0.30, 0.95), np.clip(snap.pb_bc - d, 0.30, 0.95),
                                        best_of, final_tb_to, mom, smom)
    f = lambda d: MK.price(dist(d), MK.mk_winner(True))[0] - p_star
    d = brentq(f, -0.3, 0.3, xtol=1e-9) if f(-0.3) * f(0.3) < 0 else 0.0
    D = dist(d)
    fc = Forecast(p_star, float(np.clip(snap.pa_bc + d, 0.30, 0.95)), float(np.clip(snap.pb_bc - d, 0.30, 0.95)),
                  sum(p * (o.games_a + o.games_b) for o, p in D.items()))
    return fc, D


def fair(model: Model, snap: Snapshot, best_of: int = 3, final_tb_to: int = 7, p_override: float | None = None) -> dict:
    """Справедливая роспись без котировок: победитель, тотал и фора по геймам на линии, ближайшей к 50/50,
    победитель 1-го сета, счёт по сетам. K_min — минимальный коэффициент, при котором EV ≥ порога."""
    fc, D = distribution(model, snap, best_of, final_tb_to, p_override)
    pr = lambda s: MK.price(D, s)[0]
    tl = min((x + 0.5 for x in range(12, 50)), key=lambda L: abs(pr(MK.mk_total(L, True)) - 0.5))
    hl = min((x + 0.5 for x in range(-15, 15)), key=lambda h: abs(pr(MK.mk_handicap(True, h)) - 0.5))
    out = dict(p1=fc.p_a, pa=fc.pa, pb=fc.pb, exp_total=fc.exp_games, total_line=tl, p_over=pr(MK.mk_total(tl, True)),
               hcp1_line=hl, p_hcp1=pr(MK.mk_handicap(True, hl)), set1_p1=pr(MK.mk_set_winner(0, True)))
    need = best_of // 2 + 1
    for a in range(need + 1):
        for b in range(need + 1):
            if (a == need) != (b == need):
                out[f'p_{a}_{b}'] = pr(MK.mk_exact_sets(a, b))
    return out


def run(model: Model, snap: Snapshot, parsed: Parsed, best_of: int = 3, final_tb_to: int = 7) -> Forecast:
    fc, D = distribution(model, snap, best_of, final_tb_to)
    order = {'ML': 0, 'TOTAL': 1, 'HCP': 2, 'IT1': 3, 'IT2': 4, 'EXACT': 5}
    tab_order = {'main': 0, 'set1': 1, 'set2': 2}
    def sort_key(kv):
        tab, market, line = kv[0]
        num = line if isinstance(line, (int, float)) else (-1 if line is None else 0)
        return tab_order.get(tab, 9), order.get(market, 9), num, str(line)
    for key, odds in sorted(parsed.lines.items(), key=sort_key):
        tab, market, line = key
        if market == 'EXACT':
            sa, sb = map(int, line.split('-'))
            pw = MK.price(D, MK.mk_exact_sets(sa, sb))[0]
            ev = pw * odds - 1
            fc.rows.append(Row(tab, market, line, line, pw, 0.0, 1 / pw, odds, ev,
                               ev >= C.EV_THRESHOLD and pw >= C.MIN_PROB))
            continue
        s = _settles(tab, market, line)
        if s is None:
            continue
        n1, s1, n2, s2 = s
        for name, settle, k in ((n1, s1, odds[0]), (n2, s2, odds[1])):
            pw, pp, _ = MK.price(D, settle)
            ev = MK.ev(pw, pp, k)
            fc.rows.append(Row(tab, market, line, name, pw, pp, MK.fair_odds(pw, pp), k, ev,
                               ev >= C.EV_THRESHOLD and pw >= C.MIN_PROB))
    # один сигнал на рынок: линии одного рынка коррелированы, оставляем лучшую по EV
    best: dict[tuple, Row] = {}
    for r in fc.rows:
        if r.signal:
            k = (r.tab, r.market)
            if k not in best or r.ev > best[k].ev:
                best[k] = r
    for r in fc.rows:
        r.signal = r.signal and best.get((r.tab, r.market)) is r
    return fc


def table_text(fc: Forecast, players: tuple[str, str]) -> str:
    """Текст для Telegram (моноширинный блок)."""
    tabs = {'main': 'МАТЧ', 'set1': '1-Й СЕТ', 'set2': '2-Й СЕТ'}
    out = [f'{players[0]} — {players[1]}',
           f'Модель: П1 {fc.p_a*100:.1f}% / П2 {(1-fc.p_a)*100:.1f}%; очко на подаче {fc.pa:.3f} / {fc.pb:.3f}; '
           f'ожид. тотал {fc.exp_games:.1f}', '']
    cur = None
    for r in fc.rows:
        if r.tab != cur:
            cur = r.tab; out.append(f'— {tabs.get(r.tab, r.tab)} —')
            out.append(f'{"линия":11s}{"P,%":>5s}{"Kfair":>6s}{"K1xB":>6s}{"EV,%":>6s}')
        short = {'ML': '', 'TOTAL': 'Т', 'HCP': '', 'IT1': 'ИТ1', 'IT2': 'ИТ2', 'EXACT': 'Счёт'}[r.market]
        name = f'{short} {r.side}'.strip()
        flag = '◀' if r.signal else ''
        out.append(f'{name[:11]:11s}{r.p_model*100:5.1f}{r.k_fair:6.2f}{r.k_book:6.2f}{r.ev*100:6.1f}{flag}')
    out += [''] + fc.notes
    return '\n'.join(out)
