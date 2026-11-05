"""Quant Engine v5.0 — шаг 3: точная марковская цепь тенниса.

Вход: pa, pb — вероятности выиграть очко на СВОЕЙ подаче (игрок A и игрок B).
Цепь: очко → гейм → тай-брейк → сет → матч. Выход — точное распределение исходов матча
с полной историей сетов, из которого считаются все рынки 1xBet (раздел markets()).

Правила:
* Гейм: до 4 очков с разницей 2 (ровно — замкнутая формула).
* Сет: до 6 геймов с разницей 2, при 6:6 — тай-брейк до 7 с разницей 2.
* Подача чередуется по геймам; тай-брейк считается одним геймом. Первым в следующем сете
  подаёт тот, чья очередь подавать следующий гейм (после тай-брейка — игрок, который
  принимал первым в тай-брейке).
* Тай-брейк: подающий первое очко, затем по два очка поочерёдно (A, B, B, A, A, ...).
* Решающий сет: final_tb_to = 7 (обычный тай-брейк) или 10 (тай-брейк до 10 на Шлемах).
* Тотал геймов матча: тай-брейк = 1 гейм (счёт сета 7:6 = 13 геймов), как у букмекеров.
* Первый подающий матча — жребий 50/50.

Momentum: вероятность гейма зависит от отрыва в текущем сете (параметр mom, по умолчанию 0 = i.i.d.).
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache

SetScore = tuple[int, int]
MOM_CAP = 3          # отрыв в геймах, выше которого momentum не растёт


def game_prob(p: float) -> float:
    """P(подающий выигрывает гейм) при вероятности очка p."""
    q = 1.0 - p
    return p ** 4 * (1 + 4 * q + 10 * q * q) + 20 * p ** 3 * q ** 3 * p * p / (1 - 2 * p * q)


def tiebreak_prob(pa: float, pb: float, a_serves_first: bool, target: int = 7) -> float:
    """P(A выигрывает тай-брейк до target с разницей 2)."""
    x_a = pa            # A выигрывает очко на своей подаче
    x_b = 1.0 - pb      # A выигрывает очко на подаче B

    def a_wins_point(k: int) -> float:
        first = (((k + 1) // 2) % 2) == 0          # очко подаёт тот, кто начинал тай-брейк
        a_serving = first == a_serves_first
        return x_a if a_serving else x_b

    @lru_cache(maxsize=None)
    def f(a: int, b: int) -> float:
        if a >= target and a - b >= 2:
            return 1.0
        if b >= target and b - a >= 2:
            return 0.0
        if a == b and a >= target - 1:              # «больше-меньше»: каждая пара очков — по одной подаче
            u, v = a_wins_point(a + b), a_wins_point(a + b + 1)
            return u * v / (u * v + (1 - u) * (1 - v))
        p = a_wins_point(a + b)
        return p * f(a + 1, b) + (1 - p) * f(a, b + 1)

    return f(0, 0)


def _shift(w: float, s: float) -> float:
    """Сдвиг вероятности на логит-шкале: logit(w') = logit(w) + s."""
    if s == 0.0 or w <= 0.0 or w >= 1.0:
        return w
    return 1.0 / (1.0 + (1.0 - w) / w * math.exp(-s))


def set_distribution(pa: float, pb: float, a_serves_first: bool, tb_to: int = 7,
                     mom: float = 0.0, bias: float = 0.0) -> dict[SetScore, float]:
    """Распределение итогового счёта сета (геймы A, геймы B). 7:6 / 6:7 — сет через тай-брейк.

    mom — momentum: logit P(A берёт гейм) сдвигается на mom · (геймы A − геймы B) в текущем сете,
    отрыв ограничен ±MOM_CAP. mom > 0 — лидер чаще берёт следующий гейм (сжатие счёта сета).
    bias — постоянный сдвиг логита P(A берёт гейм) на весь сет (используется для momentum между сетами).
    """
    ga, gb = game_prob(pa), 1.0 - game_prob(pb)      # P(A выигрывает гейм) на подаче A / B
    out: dict[SetScore, float] = defaultdict(float)
    state = {(0, 0): 1.0}
    while state:
        nxt: dict[SetScore, float] = defaultdict(float)
        for (a, b), pr in state.items():
            if a == 6 and b == 6:
                a_first_tb = a_serves_first          # 12 геймов сыграно — чётно, подаёт начинавший сет
                t = tiebreak_prob(pa, pb, a_first_tb, tb_to)
                out[(7, 6)] += pr * t
                out[(6, 7)] += pr * (1 - t)
                continue
            a_serving = ((a + b) % 2 == 0) == a_serves_first
            lead = max(-MOM_CAP, min(MOM_CAP, a - b))
            w = _shift(ga if a_serving else gb, mom * lead + bias)
            for (na, nb, q) in ((a + 1, b, w), (a, b + 1, 1 - w)):
                if (na >= 6 and na - nb >= 2) or (nb >= 6 and nb - na >= 2):
                    out[(na, nb)] += pr * q
                else:
                    nxt[(na, nb)] += pr * q
        state = nxt
    return dict(out)


def next_set_a_serves(score: SetScore, a_served_first: bool) -> bool:
    """Кто подаёт первым в следующем сете: тай-брейк считается одним геймом."""
    games = score[0] + score[1]                      # 7:6 → 13 «геймов»
    return a_served_first if games % 2 == 0 else not a_served_first


@dataclass(frozen=True)
class Outcome:
    """Исход матча с полной историей сетов."""
    sets: tuple[SetScore, ...]

    @property
    def a_won(self) -> bool:
        return sum(s[0] > s[1] for s in self.sets) > sum(s[1] > s[0] for s in self.sets)

    @property
    def games_a(self) -> int:
        return sum(s[0] for s in self.sets)

    @property
    def games_b(self) -> int:
        return sum(s[1] for s in self.sets)


def match_distribution(pa: float, pb: float, best_of: int = 3, final_tb_to: int = 7,
                       mom: float = 0.0, set_mom: float = 0.0) -> dict[Outcome, float]:
    """Точное распределение исходов матча (жребий подачи 50/50).

    mom — momentum внутри сета (см. set_distribution).
    set_mom — momentum между сетами: в каждом следующем сете логит P(A берёт гейм) сдвигается
    на set_mom · (сеты A − сеты B). set_mom > 0 — победитель сета сильнее в следующем.
    """
    need = best_of // 2 + 1
    cache: dict[tuple[bool, int, int], dict[SetScore, float]] = {}

    def sd(a_first: bool, deciding: bool, set_lead: int) -> dict[SetScore, float]:
        key = (a_first, final_tb_to if deciding else 7, set_lead)
        if key not in cache:
            cache[key] = set_distribution(pa, pb, a_first, key[1], mom, set_mom * set_lead)
        return cache[key]

    out: dict[Outcome, float] = defaultdict(float)

    def rec(history: tuple[SetScore, ...], a_first: bool, prob: float) -> None:
        wa = sum(s[0] > s[1] for s in history)
        wb = len(history) - wa
        if wa == need or wb == need:
            out[Outcome(history)] += prob
            return
        deciding = wa == need - 1 and wb == need - 1
        for score, q in sd(a_first, deciding, wa - wb).items():
            rec(history + (score,), next_set_a_serves(score, a_first), prob * q)

    rec((), True, 0.5)
    rec((), False, 0.5)
    return dict(out)


def swap(dist: dict[Outcome, float]) -> dict[Outcome, float]:
    """Распределение с переставленными игроками (для теста зеркальности)."""
    return {Outcome(tuple((b, a) for a, b in o.sets)): p for o, p in dist.items()}
