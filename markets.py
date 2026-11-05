"""Quant Engine v5.0 — рынки 1xBet из одного распределения матча.

Каждый рынок — функция исхода матча → 'W' (выигрыш), 'P' (возврат), 'L' (проигрыш).
Вероятности считаются суммированием по точному распределению, поэтому все рынки
автоматически согласованы между собой (зеркальность, монотонность хвостов).
"""
from __future__ import annotations

from typing import Callable

from markov import Outcome

Settle = Callable[[Outcome], str]


def over_under(value: float, line: float, over: bool) -> str:
    if value == line:
        return 'P'
    return 'W' if (value > line) == over else 'L'


def handicap(margin: float, h: float) -> str:
    v = margin + h
    return 'P' if v == 0 else ('W' if v > 0 else 'L')


def set_games(o: Outcome, i: int) -> tuple[int, int] | None:
    return o.sets[i] if len(o.sets) > i else None


# ---------- рынки матча ----------
def mk_winner(a: bool) -> Settle:
    return lambda o: 'W' if o.a_won == a else 'L'


def mk_total(line: float, over: bool) -> Settle:
    return lambda o: over_under(o.games_a + o.games_b, line, over)


def mk_handicap(player_a: bool, h: float) -> Settle:
    return lambda o: handicap((o.games_a - o.games_b) if player_a else (o.games_b - o.games_a), h)


def mk_ind_total(player_a: bool, line: float, over: bool) -> Settle:
    return lambda o: over_under(o.games_a if player_a else o.games_b, line, over)


def mk_exact_sets(sa: int, sb: int) -> Settle:
    return lambda o: 'W' if (sum(s[0] > s[1] for s in o.sets), sum(s[1] > s[0] for s in o.sets)) == (sa, sb) else 'L'


def mk_result_total(player_a: bool, line: float, over: bool) -> Settle:
    """«Первый / Второй, результат + тотал»: игрок выигрывает матч И тотал геймов Б/М."""
    def f(o: Outcome) -> str:
        if o.a_won != player_a:
            return 'L'
        return over_under(o.games_a + o.games_b, line, over)
    return f


def mk_result_total_sets(player_a: bool, over: bool) -> Settle:
    """«Результат + тотал сетов»: победа игрока И тотал сетов Б/М 2,5 (для BO3)."""
    return lambda o: 'W' if (o.a_won == player_a and (len(o.sets) > 2.5) == over) else 'L'


def mk_tiebreak_in_match(yes: bool) -> Settle:
    return lambda o: 'W' if any(max(s) == 7 and min(s) == 6 for s in o.sets) == yes else 'L'


def mk_odd_total(odd: bool) -> Settle:
    return lambda o: 'W' if ((o.games_a + o.games_b) % 2 == 1) == odd else 'L'


def mk_european_handicap(h: int, sel: str) -> Settle:
    """Трёхисходная фора на целой линии для A: sel = '1' | 'X' | '2'."""
    def f(o: Outcome) -> str:
        v = (o.games_a - o.games_b) + h
        res = '1' if v > 0 else ('X' if v == 0 else '2')
        return 'W' if res == sel else 'L'
    return f


def mk_three_way_total(line: int, sel: str) -> Settle:
    """Трёхисходный тотал на целой линии: sel = 'M' | '=' | 'B'."""
    def f(o: Outcome) -> str:
        t = o.games_a + o.games_b
        res = 'M' if t < line else ('=' if t == line else 'B')
        return 'W' if res == sel else 'L'
    return f


def mk_set_handicap(player_a: bool, h: float) -> Settle:
    def f(o: Outcome) -> str:
        sa = sum(s[0] > s[1] for s in o.sets); sb = len(o.sets) - sa
        return handicap((sa - sb) if player_a else (sb - sa), h)
    return f


def mk_total_sets(line: float, over: bool) -> Settle:
    return lambda o: over_under(len(o.sets), line, over)


def mk_bagel(yes: bool = True) -> Settle:
    """«В сете игрок не выиграет ни одного гейма»: хотя бы один сет 6:0 (любым игроком)."""
    return lambda o: 'W' if any(min(s) == 0 for s in o.sets) == yes else 'L'


def mk_comeback(player_a: bool) -> Settle:
    """Игрок выигрывает матч, проиграв первый сет."""
    def f(o: Outcome) -> str:
        s1_a = o.sets[0][0] > o.sets[0][1]
        return 'W' if (o.a_won == player_a and s1_a != player_a) else 'L'
    return f


def mk_max_set_total(line: float, over: bool) -> Settle:
    """«Тотал самого результативного сета»."""
    return lambda o: over_under(max(a + b for a, b in o.sets), line, over)


def mk_set_match(set1_a: bool, match_a: bool) -> Settle:
    """«Сет / Матч»: победитель 1-го сета и победитель матча."""
    return lambda o: 'W' if ((o.sets[0][0] > o.sets[0][1]) == set1_a and o.a_won == match_a) else 'L'


# ---------- рынки отдельного сета (i = 0 — первый сет) ----------
def mk_set_winner(i: int, a: bool) -> Settle:
    def f(o: Outcome) -> str:
        s = set_games(o, i)
        if s is None:
            return 'P'                       # сет не сыгран (для 3-го сета) — возврат
        return 'W' if (s[0] > s[1]) == a else 'L'
    return f


def mk_set_total(i: int, line: float, over: bool) -> Settle:
    def f(o: Outcome) -> str:
        s = set_games(o, i)
        return 'P' if s is None else over_under(s[0] + s[1], line, over)
    return f


def mk_set_handicap_games(i: int, player_a: bool, h: float) -> Settle:
    def f(o: Outcome) -> str:
        s = set_games(o, i)
        if s is None:
            return 'P'
        return handicap((s[0] - s[1]) if player_a else (s[1] - s[0]), h)
    return f


def mk_set_ind_total(i: int, player_a: bool, line: float, over: bool) -> Settle:
    def f(o: Outcome) -> str:
        s = set_games(o, i)
        return 'P' if s is None else over_under(s[0] if player_a else s[1], line, over)
    return f


def mk_set_exact(i: int, ga: int, gb: int) -> Settle:
    def f(o: Outcome) -> str:
        s = set_games(o, i)
        return 'P' if s is None else ('W' if s == (ga, gb) else 'L')
    return f


def price(dist: dict[Outcome, float], settle: Settle) -> tuple[float, float, float]:
    """(P_win, P_push, P_lose) по точному распределению."""
    acc = {'W': 0.0, 'P': 0.0, 'L': 0.0}
    for o, p in dist.items():
        acc[settle(o)] += p
    return acc['W'], acc['P'], acc['L']


def fair_odds(pw: float, pp: float) -> float:
    """Справедливый коэффициент с учётом возврата: EV = pw·K + pp − 1 = 0 → K = (1 − pp) / pw."""
    return (1 - pp) / pw if pw > 0 else float('inf')


def ev(pw: float, pp: float, odds: float) -> float:
    """EV = P_win · K + P_push − 1."""
    return pw * odds + pp - 1
