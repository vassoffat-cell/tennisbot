"""⭐ Ставка дня · правило C (решение пользователя 08.11.2026). Общий модуль тенниса и футбола (копия — football/pick.py).

Предложение (offer) — конкретная ставка: рынок, сторона, линия, кф 1xBet и справедливая вероятность.
  • Справедливая вероятность P: Pinnacle без маржи (метод Шина по той же линии) — на истории 2023–26 точнее модели
    (исход тенниса: WTA 67,6% против 66,4%, ATP 68,7% против 66,0%); нет линии Pinnacle — модель.
  • Кф 1xBet: из API (точно), со скриншота (точно) или оценка = r × кф Pinnacle (r — отношение 1xBet / Pinnacle,
    начально 0,98 по сверке 08.11, уточняется каждым скриншотом); нет ни того, ни другого — кф неизвестен.
  • Уверенность в выгодности = P(истинная вероятность > 1/кф) при ошибке оценки σ на логит-шкале:
        conf = Φ((logit P − logit(1/K)) / σ);   σ: Pinnacle 0,15, модель 0,45 (σ модели против Pinnacle — HISTORY 05.11).
    «Уверенность 90% в выгодности» ⇔ conf ≥ 0,90 ⇔ σ(logit P − 1,2816·σ) · K ≥ 1.
Выбор (по приоритету пользователя: кф ≥ 1,35 → максимальная справедливая вероятность → уверенность 90%):
  1) кф известен (точно или оценка), K ≥ 1,35, conf ≥ 0,90 и P ≥ 0,60 — максимум P;
  2) иначе кф известен, K ≥ 1,35 и P ≥ 0,60 — максимум P (уверенность в выгодности показывается честно);
  3) иначе кф неизвестен — линия модели с 0,60 ≤ P ≤ 0,71 (справедливый кф ≥ 1,41 ⇒ у букмекера ожидаемо ≥ 1,35);
  4) последний резерв — кф известен, K ≥ 1,35, но P < 0,60 (основные линии около 50/50).
Цель пользователя — 72–75% угаданных: ступени 1–3 держат P ≥ 0,60, максимум P внутри ступени.
"""
from __future__ import annotations

import math
from typing import Iterable

MIN_K = 1.35          # кф 1xBet не ниже (требование пользователя)
CONF = 0.90           # уверенность в выгодности
MIN_P_VALUE = 0.60    # ступени 1–3 — не ниже этой вероятности (цель — 72–75% угаданных)
P_UNKNOWN_MAX = 0.71  # ступень 3 — кф неизвестен: справедливый кф ≥ 1,41
SIGMA = {'pinnacle': 0.15, 'blend': 0.25, 'model': 0.45}   # blend — 1xBet без маржи (скриншот, нет Pinnacle)
R_DEFAULT = 0.98      # кф 1xBet / кф Pinnacle (сверка 08.11.2026: тотал РПЛ, 8 цен, медиана 0,98)
Z90 = 1.2815515655446004
# ТЗ-004 (решение пользователя 11.11.2026), СОВМЕЩЁННОЕ правило: сначала ПЕРЕВЕС над Pinnacle — перевес = P_Pinnacle(Шин) × кф 1xBet − 1
# ≥ 3% при кф 1,70–2,50, рынок П1/П2, кф точный (API / скриншот); история 2023–25 (14 825 матчей, кф закрытия Bet365/Pinnacle):
# ROI +23,5% ± 9,0 на 134 ставках (реалистично +3…+10%); прежнее «максимум P при кф ≥ 1,35» — ROI −4,6% на 4 839 → только запасное,
# с пометкой «без перевеса · матожидание −X%». Включается флагом VALUE_ON (теннис — да; футбол — после проверки на истории).
VALUE_ON = False
VALUE_EDGE = 0.03
VALUE_EDGE_MAX = 0.20      # перевес > 20% над Pinnacle — почти наверняка ошибка данных (порядок игроков, OCR) → не «с перевесом»
VALUE_K = (1.70, 2.50)
VALUE_MARKETS = ('ML',)
# 17.11 ТЗ-007 v2 (решение пользователя «внедряй»), шаг 0 — tz007_retro: max P при кф ≥ 1,35 — ROI −4,8% (n 4901, z −4,8) и −1,2% вне выборки.
DP_V2 = True                # R1: ⭐ только с ценой 1xBet (API / скриншот) и справедливой P рынка; без цены — не ⭐ (только теневой портфель)
DP_STRICT = False           # R5: True — нет 💰 → «выгодной ставки нет»; False («мягкий», по умолчанию до решения пользователя) — одна 🎯
EV_FLOOR = -0.03            # R5 мягкий: 🎯 только при EV ≥ −3% (по кф 1xBet и P рынка)
# 17.11 R5 «лестница» (пользователь: «объедини строгий и мягкий — ставка дня каждый день и с пользой»; engine/research/daily_hybrid.py):
# 💰 есть → она (ставка 1 ед.); нет → 🎯 с МАКСИМАЛЬНЫМ EV среди P ≥ 0,60, кф ≥ 1,35 (не max P), ставка ¼ ед.
# История тенниса, одна ⭐ в сутки, ⭐ в 92% дней: max P −6,2% (2019–22) / −4,5% (2023–25); лестница по EV 1xBet −0,2% / −0,7%;
# + ставки 1 / ¼ ед. +3,1% / +5,3%; 🎯 по лучшей из Bet365/Pinnacle +2,7% / +1,2% (+ ставки +5,1% / +6,7%).
DP_LADDER = True
# 19.11 (решение пользователя: «считай, что у меня несколько контор, которые ты предложил»; заменяет С25 «только 1xBet»):
# кф ставки = лучшая цена среди контор The Odds API (биржи — за вычетом комиссии); EV, 💰, лестница, банк — по ней
BEST_PRICE = True
BOOK_RU = {'onexbet': '1xBet', 'pinnacle': 'Pinnacle', 'marathonbet': 'Marathon', 'williamhill': 'William Hill',
           'unibet_eu': 'Unibet', 'betsson': 'Betsson', 'sport888': '888sport', 'betclic': 'Betclic',
           'betfair_ex_eu': 'Betfair', 'matchbook': 'Matchbook'}


def book_ru(o: dict) -> str:
    return BOOK_RU.get(str(o.get('book') or 'onexbet'), str(o.get('book') or '1xBet'))
STAKE_MONEY, STAKE_SAFE = 1.0, 0.25


def stake(o: dict) -> float:
    """Ставка в единицах банка: 💰 (ступень 0) — 1, 🎯 — ¼ (лестница R5)."""
    return STAKE_MONEY if str(o.get('tier', '')) in ('0', '0.0') else STAKE_SAFE


def edge(o: dict) -> float | None:
    """Перевес над Pinnacle: P_Pinnacle × кф − 1 (нет цены Pinnacle или кф — None)."""
    pp, k = o.get('p_pin'), o.get('k')
    return pp * k - 1 if pp and k else None


def is_value(o: dict) -> bool:
    e = edge(o)
    return bool(VALUE_ON and e is not None and VALUE_EDGE <= e <= VALUE_EDGE_MAX and o.get('market') in VALUE_MARKETS
                and o.get('k_src') in ('api', 'screenshot') and VALUE_K[0] <= o['k'] <= VALUE_K[1])


def logit(p: float) -> float:
    p = min(max(p, 1e-9), 1 - 1e-9)
    return math.log(p / (1 - p))


def phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def shin(prices: list[float]) -> list[float]:
    """Справедливые вероятности по методу Шина для n-исходного рынка (решение для z — бисекция)."""
    q = [1 / k for k in prices]
    S = sum(q)
    if S <= 1:
        return [x / S for x in q]

    def probs(z: float) -> list[float]:
        return [(math.sqrt(z * z + 4 * (1 - z) * x * x / S) - z) / (2 * (1 - z)) for x in q]

    lo, hi = 0.0, 0.4
    for _ in range(80):
        mid = (lo + hi) / 2
        if sum(probs(mid)) > 1:
            lo = mid
        else:
            hi = mid
    p = probs((lo + hi) / 2)
    s = sum(p)
    return [x / s for x in p]


def conf_value(p: float, k: float | None, sigma: float) -> float | None:
    """Вероятность того, что ставка выгодна (EV > 0) при ошибке оценки σ. Нет кф — None."""
    if not k or k <= 1:
        return None
    return phi((logit(p) - logit(1 / k)) / sigma)


def ev(p: float, k: float | None, p_push: float = 0.0) -> float | None:
    """EV на 1 ед. с учётом возврата при целых линиях. P здесь — вероятность выигрыша БЕЗ возврата (рынок Pinnacle на целой
    линии и смесь A1/A2 — условные: возврат исключён), поэтому EV = (1 − P_возврата) · (P · K − 1).
    21.11 (разбор ⭐ 20.11 ТБ2 и 21.11 ТБ3, Ф2(0)): раньше EV = P·K + P_возврата − 1 — возврат учитывался дважды, EV целых линий
    завышался на P_возврата (≈ 0,20–0,25) → лестница (максимум EV) выбирала целые тоталы и форы без настоящего перевеса."""
    return None if not k else (1 - (p_push or 0.0)) * (p * k - 1)


def finish(o: dict) -> dict:
    """Дополнить предложение: σ, уверенность в выгодности, EV, ступень выбора."""
    sigma = SIGMA[o['p_src']] if o.get('p_src') in ('pinnacle', 'blend') else float(o.get('sigma') or SIGMA['model'])
    o['conf'] = conf_value(o['p'], o.get('k'), sigma)
    o['ev'] = ev(o['p'], o.get('k'), o.get('p_push', 0.0))
    o['value_ok'] = bool(o['conf'] is not None and o['conf'] >= CONF)
    k = o.get('k')
    o['edge'] = edge(o)
    o['group'] = 'value' if is_value(o) else 'fallback'
    priced = bool(k) and o.get('k_src') in ('api', 'screenshot') and o.get('p_src') in ('pinnacle', 'blend')
    if o['group'] == 'value':
        o['tier'] = 0                                   # ТЗ-004: ставка с перевесом над Pinnacle — первой
    elif DP_V2:                                         # 17.11 ТЗ-007 R1/R2/R5: только цена рынка, EV ≥ EV_FLOOR, без 3–4 ступеней
        ok = priced and k >= MIN_K and o['p'] >= MIN_P_VALUE and o['ev'] is not None and o['ev'] >= EV_FLOOR and not DP_STRICT
        o['tier'] = (1 if o['value_ok'] else 2) if ok else 9
    elif k and k >= MIN_K and o['value_ok'] and o['p'] >= MIN_P_VALUE:
        o['tier'] = 1
    elif k and k >= MIN_K and o['p'] >= MIN_P_VALUE:
        o['tier'] = 2
    elif not k and MIN_P_VALUE <= o['p'] <= P_UNKNOWN_MAX:
        o['tier'] = 3
    elif k and k >= MIN_K:
        o['tier'] = 4
    else:
        o['tier'] = 9                                   # не подходит (кф < 1,35 или P без кф вне 0,60–0,71)
    return o


def choose(offers: Iterable[dict]) -> list[dict]:
    """Подходящие предложения по порядку выбора: ступень, затем максимум P, затем уверенность, затем раньше матч."""
    ok = [finish(dict(o)) for o in offers]
    ok = [o for o in ok if o['tier'] < 9]
    ok.sort(key=lambda o: (o['tier'], 0 if o.get('fire') else 1, 1 if o.get('clash') else 0,   # 🔥 синтез с каперами — выше
                           -(o['edge'] or 0) if o['tier'] == 0 else                              # 💰 — по перевесу
                           -(o['ev'] if o['ev'] is not None else -9) if DP_V2 and DP_LADDER else    # 🎯 — по EV (лестница R5)
                           -o['p'], -(o['conf'] or 0), str(o.get('start_utc', ''))))
    return ok


# веса справедливой P на логит-шкале (обучение 2023, проверка 2024–26 на исходах тенниса, research 08.11.2026):
#   Pinnacle без маржи: модель −0,11 / рынок 1,11 (WTA −0,11/1,12, ATP −0,12/1,10) — лог-лосс как у Pinnacle, точность выше;
#   мягкий букмекер (Bet365 ≈ 1xBet) без маржи: модель −0,09 / рынок 1,08 (WTA −0,09/1,10, ATP −0,09/1,06).
#   Смесь 50/50 хуже рынка (WTA 0,5888 против 0,5842) — модель рынку почти ничего не добавляет.
WEIGHTS = {'pinnacle': (-0.11, 1.11), 'blend': (-0.09, 1.08)}


def fair(p_model: float, p_market: float, src: str) -> float:
    """Справедливая P из рынка без маржи (Pinnacle или 1xBet) и модели — по весам истории; нет модели — только рынок."""
    wm, wb = WEIGHTS[src]
    z = wb * logit(p_market) + (wm * logit(p_model) if p_model == p_model else 0.0)
    return 1 / (1 + math.exp(-z))


def blend(p_model: float, p_book: float) -> float:
    """Справедливая P без Pinnacle (скриншот 1xBet): 1xBet без маржи с поправкой по истории."""
    return fair(p_model, p_book, 'blend')


def k_text(o: dict) -> str:
    """«кф 1xBet 1,42» / «кф 1xBet ≈ 1,42 (оценка по Pinnacle)» / «кф 1xBet неизвестен»."""
    k = o.get('k')
    if not k:
        return 'кф 1xBet неизвестен — пришлите скриншот, если он ниже 1,35'
    s = f'{k:.2f}'.replace('.', ',')
    return {'api': f'кф 1xBet {s}', 'screenshot': f'кф 1xBet {s} (скриншот)'}.get(o.get('k_src'), f'кф 1xBet ≈ {s} (оценка по Pinnacle)')


def group_text(o: dict) -> str:
    """ТЗ-004: «📈 С перевесом +4,1% над Pinnacle (справедливый кф 1,92)» / «⚠️ Без перевеса · матожидание −5,2%»."""
    e = o.get('edge')
    if o.get('group') == 'value':
        return f"📈 С перевесом {e * 100:+.1f}% над Pinnacle (справедливый кф {1 / o['p_pin']:.2f})".replace('.', ',')
    x = e if e is not None else o.get('ev')
    return (f'⚠️ Без перевеса · матожидание {x * 100:+.1f}%'.replace('.', ',') if x is not None
            else '⚠️ Без перевеса · кф 1xBet неизвестен')


def conf_text(o: dict) -> str:
    c = o.get('conf')
    if c is None:
        return 'уверенность в выгодности — после кф'
    return (f'✅ выгодна с уверенностью {c * 100:.0f}%' if o['value_ok']
            else f'⚠️ уверенность в выгодности {c * 100:.0f}% (ниже 90%)')
