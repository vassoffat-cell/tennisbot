"""🧠 Самообучение и развитие — ежедневный цикл после каждого суточного отчёта (решение пользователя 14.11.2026:
«никаких отмазок — учимся на своих ошибках и берём положительный и отрицательный опыт каперов; по каждому дневному отчёту —
обучение и развитие; еженедельный аудит — по этим отчётам»). Кодекс — engine/CODEX.md.

Общий модуль тенниса, футбола и хоккея (без проектных импортов). Состояние — JSON вида спорта (коммитится облаком):
  теннис data/lessons.json · футбол football/data/lessons.json · хоккей hockey/data/lessons.json.

ЦИКЛ (learn, раз в сутки, сразу перед суточным отчётом):
  1. Свои ставки дня (все: ⭐1, ⭐2/⭐3, заменённые версии) → сегменты: рынок, ступень, синтез с каперами (🔥/⚔️/без),
     корзина P, новые линии. Проверка «итог против ожидания модели»: W против E = ΣP, z = (W − E) / √Σ P(1 − P).
       z ≤ −1,96 при n ≥ 8 и хуже остальных ставок (zd ≤ −1,64 по остатку W − P) → сегмент ПОНИЖЕН (кандидаты сегмента —
       в конец очереди ставки дня); снятие — при z ≥ −1,0 или zd > −0,5.
       z ≥ +1,96 при n ≥ 12 и лучше остальных (zd ≥ +1,64) → сегмент СИЛЬНЫЙ (положительный опыт: понижение не применяется).
       Все ставки в целом хуже обещанного (n ≥ 15, z ≤ −1,96) → «общая переоценка P» — урок и вопрос калибровки аудиту.
  2. Правки первого приговора по причинам (сравнение / каперы за / каперы против / скриншот): права итоговая против права первая.
       ошибочных ≥ 3 и ≥ 2 × верных + 1 → правки этой причины ОТКЛЮЧЕНЫ на 30 дней (затем повторная проверка);
       общий перевес ошибочных правок → запас правки +1 п.п. за каждые 3 (до +5 п.п.) — «чем реже правки, тем лучше».
  3. Лимит ставок дня — динамический (max_picks): нагрузка дня (день недели × месяц, история, или факт матчей суток) → 1 + ⌊N / шаг⌋,
     потолок 5; вторым планом — опыт новых линий: понижены −1, сильные +1. Вчерашний итог лимит не режет.
  4. Каперы (канал × вид спорта; только прогнозы до матча с НЕЗАВИСИМЫМ итогом — табло ESPN / наша база, не отметки канала;
     их кф без ~4% маржи):
       n ≥ 20, z ≥ +1,64 и ROI > 0 → ✔ ДОКАЗАН: его ⚔️ — вето линии, его 🔥 — в силе (снятие при z < 0,5);
       n ≥ 15, z ≤ −1,64 → ⛔ СЛАБЫЙ: его 🔥 и ⚔️ системой не учитываются (снятие при z > −0,5).
Каждое новое и снятое правило — строка истории (дата, +/−, текст) → блок «🧠 Обучение» в отчёте и лист learning (kind «урок»).
Применение: dpcore / hpick (порядок кандидатов, better(), MAX_PICKS), football/tips.relation и hockey/hpick._syn (каперы).
"""
from __future__ import annotations

import datetime as dt
import html
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
PATH = {'tennis': ROOT / 'data' / 'lessons.json', 'football': ROOT / 'football' / 'data' / 'lessons.json',
        'hockey': ROOT / 'hockey' / 'data' / 'lessons.json'}
N_SEG, Z_DEMOTE, Z_RESTORE = 8, -1.96, -1.0
N_STRONG, Z_STRONG = 12, 1.96
Z_DIFF = 1.64                       # сегмент против остальных ставок (остаток W − P)
N_PROVEN, Z_PROVEN, Z_PROVEN_KEEP = 20, 1.64, 0.5
N_WEAK, Z_WEAK, Z_WEAK_KEEP = 15, -1.64, -0.5
MARGIN = 0.96                       # кф капера → вероятность без ~4% маржи
BLOCK_DAYS = 30
N_CLV, T_CLV = 10, 2.0               # CLV: n ≥ 10 и t ≥ 2 (доказан / слабый по цене закрытия)
N_SEG_CLV = 15                       # сегмент: CLV ≥ 15 ставок, t ≤ −2 → понижен по цене закрытия
MKT = {'ML': 'победа', 'HCP': 'фора', 'AH': 'фора', 'IT1': 'инд. тотал 1', 'IT2': 'инд. тотал 2', 'TOTAL': 'тотал', 'TOT': 'тотал',
       '1X2': 'исход', 'DC': 'двойной шанс', 'BTTS': 'обе забьют', 'EXACT': 'точный счёт'}
REASONS = ('сравнение', 'каперы за', 'каперы против', 'скриншот')
SRC = {'stavki_na_sport_betting1': 'Green Line', 'stavki': 'Прогнозы', 'xs_stavkii': 'Хитрости', 'groznyi_ruslan': 'Грозный',
       'kapper_uk': 'Каппер UK', 'nb-bet': 'nb-bet'}
_CACHE: dict[str, dict] = {}


# ───────────────────────────── состояние ─────────────────────────────
def load(sport: str) -> dict:
    if sport not in _CACHE:
        try:
            _CACHE[sport] = json.loads(PATH[sport].read_text(encoding='utf-8'))
        except Exception:
            _CACHE[sport] = {}
    return _CACHE[sport]


def save(sport: str, st: dict) -> None:
    PATH[sport].parent.mkdir(parents=True, exist_ok=True)
    PATH[sport].write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding='utf-8')
    _CACHE[sport] = st


# ───────────────────────────── применение (ставка дня, каперы) ─────────────────────────────
def seg_keys(c: dict) -> list[str]:
    """Сегменты кандидата / ставки: рынок, ступень, синтез, корзина P (+ новые линии для rank ≥ 2)."""
    out = [f"market:{c.get('market', '')}"]
    t = _num(c.get('tier'))
    if t == t:
        out.append(f'tier:{int(t)}')
    out.append('syn:fire' if _flag(c.get('fire')) else 'syn:clash' if _flag(c.get('clash')) else 'syn:none')
    p = _num(c.get('p'))
    if p == p:
        out.append(f'p:{_band(p)}')
    r = _num(c.get('rank'))
    if r == r and r >= 2:
        out.append('rank:extra')
    g = str(c.get('group', '') or '').strip()
    if g in ('value', 'fallback'):
        out.append(f'grp:{g}')                                    # 14.11: 💰 выгодная / 🎯 уверенная
    return out


def penalty(sport: str, c: dict) -> int:
    """1 — кандидат из пониженного сегмента (сильный сегмент понижение отменяет), 0 — нет."""
    st = load(sport)
    keys = set(seg_keys(c))
    if keys & set(st.get('strong', [])):
        return 0
    return int(bool(keys & set(st.get('demoted', []))))


def reorder(sport: str, cands: list[dict]) -> list[dict]:
    """Пониженные сегменты — в конец очереди (порядок внутри групп сохраняется)."""
    for c in cands:
        c['sl_pen'] = penalty(sport, c)
    return sorted(cands, key=lambda c: c['sl_pen'])


def reason_class(why: str) -> str:
    w = str(why).replace('правка: ', '').replace('новая: ', '').strip()
    if w.startswith('каперы за'):
        return 'каперы за'
    if w.startswith('каперы против'):
        return 'каперы против'
    if w.startswith('скриншот'):
        return 'скриншот'
    if w.startswith('исправление') or w.startswith('исправлена'):
        return 'исправление'
    return 'сравнение'


def allow(sport: str, why: str, a: dict | None = None, b: dict | None = None) -> bool:
    """Можно ли править / добавлять ставку по причине why: причина не отключена; кандидат не из пониженного сегмента,
    если текущая ставка — нет."""
    if not why:
        return False
    rc = reason_class(why)
    if rc != 'исправление' and rc in (load(sport).get('blocked') or {}):
        return False
    if a is not None and b is not None and penalty(sport, a) > penalty(sport, b):
        return False
    return True


def switch_extra(sport: str) -> float:
    return float(load(sport).get('switch_extra', 0.0))


# 14.11 (решение пользователя): лимит ставок дня — ДИНАМИЧЕСКИЙ. Сначала нагрузка дня — день недели и сезон (сколько матчей обычно
# в этот день недели этого месяца, история 2022–26) или фактическое число матчей суток, что больше; затем, вторым планом, опыт
# новых линий (самообучение ±1). Вчерашняя неудача лимит не режет. Лимит — потолок: ⭐2… появляется, только если линия лучше ⭐1.
VOLUME = {"tennis": {"1": [25.0, 27.0, 22.9, 17.0, 10.2, 6.8, 8.1], "2": [20.0, 36.3, 25.9, 19.0, 13.8, 8.1, 8.6], "3": [9.1, 10.8, 14.9, 18.2, 17.2, 16.8, 10.9], "4": [12.6, 22.7, 23.1, 21.0, 17.8, 10.6, 6.9], "5": [22.5, 21.6, 20.8, 19.7, 11.1, 8.0, 16.4], "6": [24.0, 29.9, 16.9, 15.2, 10.6, 6.9, 5.5], "7": [23.7, 29.8, 22.9, 20.1, 14.0, 8.8, 6.1], "8": [23.7, 29.5, 24.4, 19.3, 13.6, 10.0, 12.1], "9": [10.7, 12.0, 15.0, 17.0, 12.1, 10.1, 6.9], "10": [19.7, 26.1, 22.0, 19.2, 15.8, 9.8, 6.5], "11": [4.1, 5.9, 4.8, 4.0, 3.7, 2.5, 2.6], "12": [1.5, 1.7, 0.0, 0.0, 0.0, 0.0, 1.2]}, "football": {"1": [4.1, 4.1, 8.0, 3.4, 5.7, 24.7, 23.1], "2": [5.3, 2.4, 4.2, 4.9, 7.1, 29.8, 29.1], "3": [2.8, 1.8, 2.4, 4.9, 5.3, 23.2, 23.4], "4": [5.2, 3.4, 4.6, 4.7, 7.1, 28.4, 28.2], "5": [3.7, 2.3, 3.6, 2.7, 4.1, 22.4, 30.2], "6": [0.0, 0.2, 0.4, 0.0, 0.1, 1.4, 1.1], "7": [0.0, 0.2, 0.2, 1.9, 0.0, 0.0, 0.0], "8": [4.7, 1.5, 2.0, 6.0, 5.3, 20.5, 18.4], "9": [2.9, 3.1, 3.8, 5.3, 5.2, 25.2, 22.7], "10": [3.7, 3.5, 5.1, 7.7, 5.4, 25.0, 25.3], "11": [3.0, 3.9, 4.3, 7.2, 4.4, 22.7, 21.8], "12": [4.7, 3.4, 5.4, 5.8, 4.9, 18.2, 17.9]}, "hockey": {"1": [7.9, 12.2, 7.2, 13.4, 8.3, 15.1, 8.6], "2": [5.9, 10.3, 5.3, 10.5, 4.9, 11.8, 6.7], "3": [6.2, 12.9, 5.3, 13.7, 5.5, 15.6, 8.5], "4": [5.5, 7.9, 4.5, 8.4, 3.2, 8.5, 5.6], "5": [1.2, 1.2, 1.3, 1.6, 1.3, 1.2, 1.2], "6": [0.2, 0.3, 0.1, 0.3, 0.1, 0.4, 0.1], "7": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], "8": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], "9": [2.3, 1.4, 2.1, 2.6, 1.6, 3.3, 1.3], "10": [6.9, 11.6, 6.0, 12.7, 6.1, 15.0, 5.5], "11": [8.3, 10.8, 8.7, 13.1, 8.2, 16.1, 6.6], "12": [7.3, 11.7, 7.2, 11.6, 7.7, 13.7, 8.2]}}
STEP = {'tennis': 8, 'football': 6, 'hockey': 5}         # матчей на одну ставку дня сверх первой
CAP = 5


def max_picks(sport: str, default: int = 3, n_today: int = 0, when=None) -> int:
    """Лимит ставок дня: 1 + ⌊max(ожидание дня недели и месяца, матчей сегодня) / шаг⌋, потолок 5; ± опыт новых линий."""
    w = pd.Timestamp(when) if when is not None else pd.Timestamp.now(tz='UTC').tz_localize(None) + pd.Timedelta(hours=5)
    exp = float((VOLUME.get(sport, {}).get(str(w.month)) or [0] * 7)[w.weekday()])
    base = min(CAP, 1 + int(max(exp, float(n_today)) // STEP.get(sport, 10)))
    return max(1, min(CAP, base + int(load(sport).get('extra_adj', 0))))


def limit_text(sport: str, n_today: int = 0, when=None) -> str:
    w = pd.Timestamp(when) if when is not None else pd.Timestamp.now(tz='UTC').tz_localize(None) + pd.Timedelta(hours=5)
    exp = float((VOLUME.get(sport, {}).get(str(w.month)) or [0] * 7)[w.weekday()])
    adj = int(load(sport).get('extra_adj', 0))
    return (f"лимит {max_picks(sport, 3, n_today, w)} (обычно в этот день {exp:.0f} матчей, сегодня {n_today}"
            + (f", опыт новых линий {adj:+d}" if adj else '') + ')')


def capper(sport: str, src: str) -> str:
    """Статус канала: 'proven' ✔ / 'weak' ⛔ / '' — наблюдение."""
    return str(((load(sport).get('cappers') or {}).get(str(src)) or {}).get('status', ''))


N_SOFT, Z_SOFT = 50, 1.0          # 21.11 Г3б (решение пользователя «оба»): мягкое влияние — канал близко к ✔


def capper_soft(sport: str, src: str) -> bool:
    """Канал влияет на выбор ⭐ (🔥 поднимает, ⚔️ опускает, без вето): ✔ доказан ИЛИ не ⛔ и n ≥ 50, z ≥ 1,0, ROI > 0."""
    x = (load(sport).get('cappers') or {}).get(str(src)) or {}
    st = str(x.get('status', ''))
    if st == 'proven':
        return True
    return st != 'weak' and int(x.get('n', 0) or 0) >= N_SOFT and float(x.get('z', 0) or 0) >= Z_SOFT and float(x.get('roi', 0) or 0) > 0


# ───────────────────────────── данные ─────────────────────────────
def _num(x) -> float:
    try:
        return float(str(x).replace(',', '.'))
    except Exception:
        return float('nan')


def _flag(x) -> bool:
    v = _num(x)
    return bool(v == v and v >= 1) or str(x).strip().lower() == 'true'


def _band(p: float) -> str:
    return '0.80+' if p >= 0.80 else '0.70-0.80' if p >= 0.70 else '0.60-0.70' if p >= 0.60 else '<0.60'


def _rank(x) -> int:
    v = _num(x)
    return int(v) if v == v and v >= 1 else 1


def frame_dp(dp: pd.DataFrame, ch: pd.DataFrame | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Теннис / футбол: day_pick (итоговые версии) + dp_changes (заменённые версии с итогом).
    → (P: ставки с итогом W/L — date, rank, market, label, p, tier, fire, clash, result, final),
      (E: правки — date, rank, cls, old, new)."""
    P, E = [], []
    if dp is not None and len(dp):
        for r in dp.itertuples(index=False):
            res = str(getattr(r, 'result', ''))
            if str(getattr(r, 'status', '')) == 'OFF' or res not in ('W', 'L'):
                continue
            P.append(dict(date=str(r.date)[:10], rank=_rank(getattr(r, 'rank', 1)), market=str(r.market), label=str(r.label),
                          p=_num(r.p), tier=_num(getattr(r, 'tier', '')), fire=_flag(getattr(r, 'fire', 0)),
                          clash=_flag(getattr(r, 'clash', 0)), result=res, final=True,
                          group=str(getattr(r, 'group', '') or 'fallback').strip() or 'fallback', clv=_num(getattr(r, 'clv', '')),
                          k=_num(getattr(r, 'k', ''))))
    fin = {(x['date'], x['rank']): x['result'] for x in P}
    if ch is not None and len(ch):
        for r in ch.itertuples(index=False):
            why, res = str(r.reason), str(r.result)
            if not why.startswith('правка') or res not in ('W', 'L'):
                continue
            d, rk = str(r.date)[:10], _rank(getattr(r, 'rank', 1))
            E.append(dict(date=d, rank=rk, cls=reason_class(why), old=res, new=fin.get((d, rk), ''), label=str(r.label)))
    return pd.DataFrame(P), pd.DataFrame(E)


def frame_hk(dp: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Хоккей: hk_day_pick — итоговая версия (status пусто) и заменённые (status «правка», reason — причина)."""
    P, E = [], []
    if dp is None or not len(dp):
        return pd.DataFrame(), pd.DataFrame()
    for r in dp.itertuples(index=False):
        res, sts = str(r.result), str(r.status).strip()
        if res not in ('W', 'L') or sts not in ('', 'nan', 'None'):
            continue
        P.append(dict(date=str(r.date)[:10], rank=_rank(r.rank), market=str(r.market), label=str(r.label), p=_num(r.p),
                      tier=_num(getattr(r, 'tier', '')), fire=_flag(r.fire), clash=_flag(r.clash), result=res, final=True,
                      group='fallback', clv=_num(getattr(r, 'clv', '')), k=_num(getattr(r, 'k', ''))))
    fin = {(x['date'], x['rank']): x['result'] for x in P}
    for r in dp.itertuples(index=False):
        if str(r.status).strip() == 'правка' and str(r.result) in ('W', 'L'):
            d, rk = str(r.date)[:10], _rank(r.rank)
            E.append(dict(date=d, rank=rk, cls=reason_class(r.reason), old=str(r.result), new=fin.get((d, rk), ''), label=str(r.label)))
    return pd.DataFrame(P), pd.DataFrame(E)


def capper_rows(sport: str, today: dt.date) -> pd.DataFrame:
    try:
        import caprep
        return caprep.rows(sport, dt.date(2020, 1, 1), today, 12 if sport == 'hockey' else 8)
    except Exception:
        return pd.DataFrame()


# ───────────────────────────── статистика ─────────────────────────────
def _test(won, p) -> tuple[int, int, float, float]:
    """(W, n, E, z) — итог против ожидания."""
    won, p = list(won), list(p)
    n, w, e = len(won), int(sum(won)), float(sum(p))
    v = float(sum(q * (1 - q) for q in p))
    return w, n, e, ((w - e) / math.sqrt(v) if v > 0 else 0.0)


def _segments(P: pd.DataFrame) -> dict[str, dict]:
    """Сегмент: (W, n, E, z против ожидания модели, zd — против остальных ставок по остатку W − P, n_out)."""
    recs = [(int(r['result'] == 'W'), float(r['p']), set(seg_keys(r))) for r in P.to_dict('records') if r['p'] == r['p']]
    cvr = [(_num(r.get('clv')), set(seg_keys(r))) for r in P.to_dict('records') if r['p'] == r['p']]
    keys = sorted({k for _, _, ks in recs for k in ks})
    res = {}
    for k in keys:
        cv = [c for c, ks in cvr if k in ks and c == c]
        co = [c for c, ks in cvr if k not in ks and c == c]
        sd = float(np.std(cv, ddof=1)) if len(cv) > 2 else 0.0
        td = 0.0                                                   # CLV сегмента против остальных ставок
        if len(cv) > 2 and len(co) >= 5:
            se = math.sqrt(np.var(cv, ddof=1) / len(cv) + np.var(co, ddof=1) / len(co))
            td = (float(np.mean(cv)) - float(np.mean(co))) / se if se > 0 else 0.0
        clv_s = dict(n_clv=len(cv), clv=round(float(np.mean(cv)), 4) if cv else 0.0,
                     t_clv=round(float(np.mean(cv)) / (sd / math.sqrt(len(cv))), 2) if sd > 0 else 0.0, td_clv=round(td, 2))
        a = [(w, p) for w, p, ks in recs if k in ks]
        o = [(w, p) for w, p, ks in recs if k not in ks]
        w, n, e, z = _test([x for x, _ in a], [y for _, y in a])
        zd = 0.0
        if len(o) >= 5:
            va = sum(y * (1 - y) for _, y in a) / n ** 2
            vo = sum(y * (1 - y) for _, y in o) / len(o) ** 2
            ra = sum(x - y for x, y in a) / n
            ro = sum(x - y for x, y in o) / len(o)
            zd = (ra - ro) / math.sqrt(va + vo) if va + vo > 0 else 0.0
        res[k] = dict(w=w, n=n, e=round(e, 2), z=round(z, 2), zd=round(zd, 2), n_out=len(o), **clv_s)
    return res


def seg_name(k: str) -> str:
    kind, v = k.split(':', 1)
    if kind == 'market':
        return f'рынок «{MKT.get(v, v)}»'
    if kind == 'tier':
        return f'ступень {v}'
    if kind == 'syn':
        return {'fire': '🔥 вместе с каперами', 'clash': '⚔️ против каперов', 'none': 'без каперов'}[v]
    if kind == 'grp':
        return '💰 выгодные' if v == 'value' else '🎯 уверенные'
    if kind == 'p':
        return f"P {v.replace('0.', '').replace('-', '–')}%".replace('80+%', '80%+').replace('<60%', '<60%')
    return '➕ новые линии дня'


def _pc(x: float) -> str:
    return f'{x * 100:+.1f}%'.replace('.', ',')


def _fmt(x: float, d: int = 1) -> str:
    return f'{x:.{d}f}'.replace('.', ',')


# ───────────────────────────── ежедневный цикл ─────────────────────────────
def learn(sport: str, P: pd.DataFrame, E: pd.DataFrame, today: dt.date, max_default: int = 3) -> dict:
    """Пересчитать правила по всем данным; вернуть {'added': [...], 'removed': [...], 'state': st}. Состояние сохраняется."""
    old = load(sport)
    st = dict(old)
    hist = list(old.get('history', []))
    added, removed = [], []
    today_s = today.isoformat()

    def note(sign: str, text: str) -> None:
        (added if sign == '+' else removed).append(text)
        hist.append(dict(date=today_s, sign=sign, text=text))

    # 1. сегменты
    segs = _segments(P) if len(P) else {}
    dem, strong = set(old.get('demoted', [])), set(old.get('strong', []))
    for k, s in segs.items():                     # итоги хуже ожидания И хуже остальных ставок; ИЛИ цена закрытия против нас (CLV)
        bad_res = s['n'] >= N_SEG and s['z'] <= Z_DEMOTE and s['n_out'] >= 5 and s['zd'] <= -Z_DIFF
        bad_clv = s.get('n_clv', 0) >= N_SEG_CLV and s.get('t_clv', 0) <= -T_CLV and s.get('td_clv', 0) <= -Z_DIFF
        ok = (s['z'] >= Z_RESTORE or s['zd'] > -0.5) and (s.get('n_clv', 0) < N_SEG_CLV or s.get('t_clv', 0) > -0.5 or s.get('td_clv', 0) > -0.5)
        if k not in dem and (bad_res or bad_clv):
            dem.add(k)
            note('+', f"{seg_name(k)}: " + (f"{s['w']}/{s['n']} при ожидании {_fmt(s['e'])} (z {_fmt(s['z'])}), хуже остальных ставок"
                                            if bad_res else f"CLV {_pc(s['clv'])} на {s['n_clv']} (t {_fmt(s['t_clv'])}) — цена закрытия против нас")
                 + ' → понижен в выборе ставки дня')
        elif k in dem and ok:
            dem.discard(k)
            note('−', f"{seg_name(k)}: {s['w']}/{s['n']} (z {_fmt(s['z'])}) — вернулся к норме, понижение снято")
        good_res = s['n'] >= N_STRONG and s['z'] >= Z_STRONG and s['n_out'] >= 5 and s['zd'] >= Z_DIFF
        good_clv = s.get('n_clv', 0) >= N_SEG_CLV and s.get('t_clv', 0) >= T_CLV and s.get('td_clv', 0) >= Z_DIFF
        if k not in strong and (good_res or good_clv):
            strong.add(k)
            note('+', f"{seg_name(k)}: " + (f"{s['w']}/{s['n']} при ожидании {_fmt(s['e'])} (z +{_fmt(s['z'])})" if good_res
                                            else f"CLV {_pc(s['clv'])} на {s['n_clv']} (t +{_fmt(s['t_clv'])}) — цена на нашей стороне")
                 + ' → сильный, закреплён')
        elif k in strong and not good_res and not good_clv and (s['z'] < 1.0 or s['zd'] < 0.5) and \
                (s.get('n_clv', 0) < N_SEG_CLV or s.get('t_clv', 0) < 0.5):
            strong.discard(k)
            note('−', f"{seg_name(k)}: z {_fmt(s['z'])} — преимущество не подтвердилось, отметка «сильный» снята")
    st['segments'], st['demoted'], st['strong'] = segs, sorted(dem), sorted(strong)
    over = False                                                  # общий сдвиг: ставки дня в целом хуже обещанной P
    if len(P) >= 15:
        w, n, ex, z = _test((P.result == 'W').astype(int), P.p.fillna(0.6))
        over = z <= Z_DEMOTE
        if over != bool(old.get('overconf')):
            note('+' if over else '−', f'ставки дня в целом: {w}/{n} при ожидании {_fmt(ex)} (z {_fmt(z)}) — '
                 + ('модель завышает P ставки дня → вопрос аудиту: калибровка P' if over else 'общий сдвиг ушёл'))
    st['overconf'] = over
    allc = [c for c in (P.clv.map(_num) if len(P) and 'clv' in P.columns else []) if c == c]   # общий CLV ставок дня
    neg = False
    if len(allc) >= N_SEG_CLV:
        m_, s_ = float(np.mean(allc)), float(np.std(allc, ddof=1))
        tt = m_ / (s_ / math.sqrt(len(allc))) if s_ > 0 else 0.0
        neg = tt <= -T_CLV
        if neg != bool(old.get('clv_neg')):
            note('+' if neg else '−', f'ставки дня в целом: CLV {_pc(m_)} на {len(allc)} (t {_fmt(tt)}) — '
                 + ('цена закрытия против нас: кф берём хуже рынка → вопрос аудиту (время ставки, источник кф)' if neg else 'цена закрытия больше не против нас'))
    st['clv_neg'] = neg
    adj = -1 if 'rank:extra' in dem else 1 if 'rank:extra' in strong else 0      # второй план: опыт новых линий
    if adj != int(old.get('extra_adj', 0)):
        note('+' if adj else '−', {-1: 'новые линии дня хуже ⭐1 → динамический лимит ставок −1',
                                   1: 'новые линии дня сильные → динамический лимит ставок +1',
                                   0: 'поправка лимита ставок по опыту новых линий снята'}[adj])
    st['extra_adj'] = adj
    st.pop('max_picks', None)

    # 2. правки по причинам
    blocked = {k: v for k, v in (old.get('blocked') or {}).items() if v >= today_s}
    for k in set(old.get('blocked') or {}) - set(blocked):
        note('−', f'правки «{k}» — срок отключения истёк, снова разрешены (повторная проверка)')
    ed = {}
    if len(E):
        x = E[E.new.isin(['W', 'L']) & (E.new != E.old)]
        for cls, g in x.groupby('cls'):
            ed[cls] = dict(right=int((g.new == 'W').sum()), wrong=int((g.old == 'W').sum()))
    for cls, v in ed.items():
        if cls == 'исправление' or cls in blocked:
            continue
        if v['wrong'] >= 3 and v['wrong'] >= 2 * v['right'] + 1 and cls not in (old.get('blocked') or {}):
            blocked[cls] = (today + dt.timedelta(days=BLOCK_DAYS)).isoformat()
            note('+', f"правки «{cls}»: права первая {v['wrong']}, итоговая {v['right']} → отключены на {BLOCK_DAYS} дней")
    st['blocked'], st['edits'] = blocked, ed
    net = sum(v['wrong'] - v['right'] for v in ed.values())
    extra = round(min(0.05, 0.01 * (max(0, net) // 3)), 2)
    if abs(extra - float(old.get('switch_extra', 0.0))) > 1e-9:
        note('+' if extra > float(old.get('switch_extra', 0.0)) else '−',
             f'запас для правки: +{extra * 100:.0f} п.п. к базовому (ошибочных правок больше верных на {max(0, net)})')
    st['switch_extra'] = extra

    # 3. каперы: итог по независимому табло + CLV (их кф против цены закрытия — вдвое-вдесятеро быстрее итогов)
    cap = dict(old.get('cappers') or {})
    df = capper_rows(sport, today)
    new_cap = {}
    try:
        import clv as CLV
        cvs = CLV.capper_values(sport)
    except Exception:
        cvs = {}
    if len(df) or cvs:
        d = df[df.ind.isin(['W', 'L'])].copy() if len(df) and 'ind' in df.columns else pd.DataFrame(columns=['src', 'res', 'k', 'rel'])
        d['res'] = d['ind'] if len(d) else d.get('res')
        d['kk'] = d.k.map(_num) if len(d) else pd.Series(dtype=float)
        d = d[d.kk > 1.0] if len(d) else d
        for src in sorted(set(d.src) | set(cvs)):
            g = d[d.src == src]
            if len(g):
                won = (g.res == 'W').astype(int).tolist()
                p = [min(0.99, MARGIN / k) for k in g.kk]
                w, n, e, z = _test(won, p)
                roi = float(sum((k - 1) if r == 'W' else -1.0 for r, k in zip(g.res, g.kk)) / n)
            else:
                w, n, e, z, roi = 0, 0, 0.0, 0.0, 0.0
            cv = cvs.get(src, [])
            nc = len(cv); mc = float(np.mean(cv)) if nc else 0.0
            tc = mc / (float(np.std(cv, ddof=1)) / math.sqrt(nc)) if nc > 2 and float(np.std(cv, ddof=1)) > 0 else 0.0
            prev = str((cap.get(src) or {}).get('status', ''))
            by_res_p = n >= N_PROVEN and z >= Z_PROVEN and roi > 0
            by_clv_p = nc >= N_CLV and tc >= T_CLV and mc > 0
            by_res_w = n >= N_WEAK and z <= Z_WEAK
            by_clv_w = nc >= N_CLV and tc <= -T_CLV
            stt = prev
            if prev != 'proven' and (by_res_p or by_clv_p) and not by_clv_w:
                stt = 'proven'
            elif prev == 'proven' and (z < Z_PROVEN_KEEP and n >= N_PROVEN or nc >= N_CLV and tc < 0.5) and not (by_res_p or by_clv_p):
                stt = ''
            if stt != 'proven':
                if prev != 'weak' and (by_res_w or by_clv_w) and not by_clv_p:
                    stt = 'weak'
                elif prev == 'weak' and not (by_res_w or by_clv_w) and (z > Z_WEAK_KEEP or nc >= N_CLV and tc > -0.5):
                    stt = ''
            fire = g[g.rel == '1'] if len(g) else g; clash = g[g.rel == '-1'] if len(g) else g
            new_cap[src] = dict(n=n, w=w, e=round(e, 1), z=round(z, 2), roi=round(roi, 3), status=stt,
                                n_clv=nc, clv=round(mc, 4), t_clv=round(tc, 2),
                                fire=[int((fire.res == 'W').sum()) if len(fire) else 0, len(fire)],
                                clash=[int((clash.res == 'L').sum()) if len(clash) else 0, len(clash)])
            nm = SRC.get(src, src)
            why = (f'{w}/{n}, ROI {roi * 100:+.0f}% (z {_fmt(z)})' if n else '') + \
                  ((' · ' if n else '') + f'CLV {_pc(mc)} на {nc} (t {_fmt(tc)})' if nc else '')
            if stt != prev:
                if stt == 'proven':
                    note('+', f'капер {nm}: {why} → ✔ доказан: его ⚔️ — вето, 🔥 — в силе')
                elif stt == 'weak':
                    note('+', f'капер {nm}: {why} → ⛔ слабый: его 🔥 и ⚔️ не учитываются')
                else:
                    note('−', f"капер {nm}: статус «{'доказан' if prev == 'proven' else 'слабый'}» снят ({why})")
    st['cappers'] = new_cap or cap
    st['updated'], st['sport'] = today_s, sport
    st['history'] = hist[-300:]
    save(sport, st)
    return dict(added=added, removed=removed, state=st)


# ───────────────────────────── блоки отчётов ─────────────────────────────
def day_block(sport: str, P: pd.DataFrame, E: pd.DataFrame, day: dt.date, res: dict) -> str:
    """«🧠 Обучение» под отчётом ставок дня: разбор ошибок суток, новые и снятые правила, действующие правила."""
    e = html.escape
    st = res.get('state') or load(sport)
    segs = st.get('segments') or {}
    ds = day.isoformat()
    out = []
    lost = P[(P.date == ds) & (P.result == 'L')] if len(P) else P
    for r in lost.to_dict('records'):
        s = segs.get(f"market:{r['market']}") or {}
        p = r['p']
        if s.get('n', 0) >= 5:
            verdict = (f"рынок «{MKT.get(r['market'], r['market'])}» {s['w']}/{s['n']} при ожидании {_fmt(s['e'])}"
                       + (' — сегмент понижен' if f"market:{r['market']}" in st.get('demoted', [])
                          else ' — ниже ожидания, под наблюдением' if s['z'] <= -1.0 else ' — в норме, ожидаемый риск'))
        else:
            verdict = 'данных по рынку мало — копим'
        cause, note = classify(r, st)                              # 20.11 Ц12: промах ⭐ — инцидент с причиной
        out.append(f"🔴 {e(r['label'])} (P {p * 100:.0f}%): {e(cause)} — {e(note)}; {e(verdict)}")
    if len(E):
        for r in E[(E.date == ds) & E.new.isin(['W', 'L']) & (E.new != E.old)].to_dict('records'):
            v = (st.get('edits') or {}).get(r['cls'], {})
            out.append(f"✏️ правка «{e(r['cls'])}» {'ошиблась' if r['old'] == 'W' else 'оказалась верной'}"
                       f" — по этой причине: верных {v.get('right', 0)}, ошибочных {v.get('wrong', 0)}")
    if not out:
        won = P[(P.date == ds)] if len(P) else P
        out.append('ошибок за сутки нет' if len(won) else 'рассчитанных ставок за сутки нет')
    if len(P):
        out.append(f'⭐ серия без промаха: {streak(P)}')
    dd = P[(P.date == ds)] if len(P) else P
    cvd = [c for c in (dd.clv.map(_num) if len(dd) and 'clv' in dd.columns else []) if c == c]
    if cvd:
        out.append(f'📈 CLV ставок суток: {_pc(np.mean(cvd))} ({len(cvd)})')
    allc = [c for c in (P.clv.map(_num) if len(P) and 'clv' in P.columns else []) if c == c]
    if len(allc) >= 5:
        out.append(f'📈 CLV всего: {_pc(np.mean(allc))} на {len(allc)} — ' + ('цена на нашей стороне' if np.mean(allc) > 0 else 'цена против нас'))
    out += [f'📘 {e(t)}' for t in res.get('added', []) if not t.startswith('капер ')]
    out += [f'↩️ {e(t)}' for t in res.get('removed', []) if not t.startswith('капер ')]
    act = rules_line(sport)
    if act:
        out.append(f'Действует: {e(act)}')
    return '🧠 <b>Обучение</b>\n' + '\n'.join(out)


def cap_block(sport: str, res: dict) -> str:
    """«🧠 Обучение на каперах» под отчётом каперов: изменения доверия и действующие статусы (пусто — нечего сказать)."""
    e = html.escape
    st = res.get('state') or load(sport)
    ch = [f'📘 {e(t)}' for t in res.get('added', []) if t.startswith('капер ')] + \
         [f'↩️ {e(t)}' for t in res.get('removed', []) if t.startswith('капер ')]
    cap = st.get('cappers') or {}
    good = [SRC.get(s, s) for s, v in cap.items() if v.get('status') == 'proven']
    bad = [SRC.get(s, s) for s, v in cap.items() if v.get('status') == 'weak']
    watch = sum(1 for v in cap.values() if not v.get('status'))
    if not cap and not ch:
        return ''
    line = ' · '.join(x for x in ([f"✔ {', '.join(good)}"] if good else []) + ([f"⛔ {', '.join(bad)}"] if bad else [])
                      + ([f'наблюдение {watch}'] if watch else []))
    fw = sum(v.get('fire', [0, 0])[0] for v in cap.values()); fn = sum(v.get('fire', [0, 0])[1] for v in cap.values())
    cw = sum(v.get('clash', [0, 0])[0] for v in cap.values()); cn = sum(v.get('clash', [0, 0])[1] for v in cap.values())
    syn = (f'\nвсего: 🔥 угадано {fw}/{fn}' if fn else '') + (f' · ⚔️ права система {cw}/{cn}' if cn else '')
    cl = [f"{SRC.get(s, s)} {_pc(v['clv'])} ({v['n_clv']})" for s, v in cap.items() if v.get('n_clv', 0) >= 3]
    if cl:
        syn += '\n📈 CLV: ' + e(' · '.join(cl))
    return '🧠 <b>Обучение на каперах</b>\n' + '\n'.join(ch + [f'Доверие: {e(line)}' if line else '']).strip() + syn


def rules_line(sport: str) -> str:
    st = load(sport)
    parts = [f'{seg_name(k)} — понижен' for k in st.get('demoted', [])]
    parts += [f'{seg_name(k)} — сильный' for k in st.get('strong', [])]
    parts += [f'правки «{k}» — откл. до {v[8:10]}.{v[5:7]}' for k, v in (st.get('blocked') or {}).items()]
    if st.get('switch_extra'):
        parts.append(f"запас правки +{st['switch_extra'] * 100:.0f} п.п.")
    if st.get('overconf'):
        parts.append('общая переоценка P — на калибровке')
    if st.get('clv_neg'):
        parts.append('CLV ставок дня отрицательный — на разборе')
    if st.get('extra_adj'):
        parts.append(f"лимит ставок {int(st['extra_adj']):+d} (опыт новых линий)")
    return '; '.join(parts)


def week_block(sport: str, P: pd.DataFrame, E: pd.DataFrame, d0: dt.date, d1: dt.date) -> str:
    """«🧠 Аудит недели» под недельным отчётом ставок дня: калибровка, контроль качества, развитие."""
    e = html.escape
    st = load(sport)
    a, b = d0.isoformat(), d1.isoformat()
    W = P[(P.date >= a) & (P.date <= b)] if len(P) else P
    out = ['🧠 <b>Аудит недели</b>']
    rows = []                                                     # калибровка: корзины P — неделя / всего
    for band in ('0.60-0.70', '0.70-0.80', '0.80+'):
        def cell(df):
            g = df[df.p.map(_band) == band] if len(df) else df
            return f"{int((g.result == 'W').sum())}/{len(g)} ({_fmt(g.p.sum(), 1)})" if len(g) else '—'
        rows.append(f"{band.replace('0.', '').replace('-', '–').replace('80+', '80+'):<7}{cell(W):>12} {cell(P):>13}")
    out.append('Калибровка — угадано (ожидалось):\n<pre>' + e(f"{'P, %':<7}{'неделя':>12} {'всего':>13}\n" + '\n'.join(rows)) + '</pre>')
    q = []
    if len(W):
        w, n, ex, z = _test((W.result == 'W').astype(int), W.p.fillna(0.6))
        q.append(f"ставки недели {w}/{n} при ожидании {_fmt(ex)} (z {'+' if z >= 0 else ''}{_fmt(z)})")
        q.append(f"дней со ставкой {W.date.nunique()} · в среднем {_fmt(len(W) / max(1, W.date.nunique()))} в сутки")
    if len(E):
        we = E[(E.date >= a) & (E.date <= b)]
        if len(we):
            q.append(f"правок {len(we)}: верных {int(((we.new == 'W') & (we.old == 'L')).sum())}, ошибочных {int(((we.new == 'L') & (we.old == 'W')).sum())}")
    cw = [c for c in (W.clv.map(_num) if len(W) and 'clv' in W.columns else []) if c == c]
    if cw:
        q.append(f'CLV недели {_pc(np.mean(cw))} на {len(cw)}')
    if q:
        out.append('Контроль качества: ' + e('; '.join(q)))
    hist = [h for h in st.get('history', []) if a <= h['date'] <= (d1 + dt.timedelta(days=1)).isoformat()]
    out.append('Развитие за неделю:\n' + ('\n'.join(f"{'📘' if h['sign'] == '+' else '↩️'} {e(h['text'])}" for h in hist[-12:])
                                         if hist else 'новых правил нет — данные копятся'))
    act = rules_line(sport)
    out.append(f'Действует: {e(act)}' if act else 'Действующих поправок нет.')
    return '\n'.join(out)


def to_learning(store, sport_ru: str, res: dict) -> int:
    """Новые и снятые правила → лист learning (kind «урок»): история самообучения в кнопке «🧠 Самообучение»."""
    rows = [(f'{sport_ru}: урок', t) for t in res.get('added', [])] + [(f'{sport_ru}: правило снято', t) for t in res.get('removed', [])]
    if not rows:
        return 0
    now = (pd.Timestamp.now(tz='UTC').tz_localize(None) + pd.Timedelta(hours=5)).strftime('%Y-%m-%d %H:%M')
    store.append('learning', [dict(when=now, kind='урок', title=t, text=x, sent='') for t, x in rows])
    return len(rows)



# ───────────────────────────── 🔴 инциденты: каждый промах ⭐ (решение пользователя 20.11, CONTROL Ц12) ─────────────────────────────
INC_COLS = ['date', 'rank', 'label', 'market', 'p', 'k', 'clv', 'cause', 'note', 'at']


def _inc_path(sport: str) -> Path:
    return PATH[sport].parent / 'incidents.csv'


def classify(r: dict, st: dict) -> tuple[str, str]:
    """Причина промаха ⭐ — первая подходящая: системная (без цены, рынок против, сегмент ниже ожидания, против капера) или разброс."""
    p, k, cv = r.get('p'), r.get('k'), r.get('clv')
    seg = (st.get('segments') or {}).get(f"market:{r.get('market')}") or {}
    if not (k == k and k):
        return 'без цены рынка', 'ставка без проверки ценой конторы — перевес неизвестен (С19/С26а: такие ⭐ больше не выпускаются)'
    if cv == cv and cv < -0.02:
        return 'рынок против', f'цена закрытия против нас (CLV {cv * 100:+.1f}%) — рынок знал больше: новости, состав, травма'
    if seg.get('n', 0) >= 5 and seg.get('z', 0) <= -1.0:
        return 'сегмент ниже ожидания', f"рынок «{MKT.get(r.get('market'), r.get('market'))}» {seg.get('w')}/{seg.get('n')} при ожидании {_fmt(seg.get('e', 0))}"
    if r.get('clash'):
        return 'против капера', 'ставка шла против прогноза капера (⚔️) — проверить его итог'
    return 'разброс', f"обещано {p * 100:.0f}% — промах ожидаем в {100 - p * 100:.0f}% таких ставок" + (f'; CLV {cv * 100:+.1f}% — по цене ставка верная' if cv == cv else '')


def incidents(sport: str, sport_ru: str, P: pd.DataFrame, store, log: list[str] | None = None) -> list[dict]:
    """Каждый новый промах ⭐ → incidents.csv вида + лист learning (kind «инцидент»). Возвращает новые инциденты."""
    if P is None or not len(P):
        return []
    f = _inc_path(sport)
    old = pd.read_csv(f, dtype=str, keep_default_na=False) if f.exists() else pd.DataFrame(columns=INC_COLS)
    seen = set(zip(old.date, old['rank'].astype(str), old.label))
    st = load(sport)
    now = (pd.Timestamp.now(tz='UTC').tz_localize(None) + pd.Timedelta(hours=5)).strftime('%Y-%m-%d %H:%M')
    new = []
    for r in P[P.result == 'L'].to_dict('records'):
        if (str(r['date']), str(r['rank']), str(r['label'])) in seen:
            continue
        cause, note = classify(r, st)
        new.append(dict(date=r['date'], rank=r['rank'], label=r['label'], market=r['market'], p=round(float(r['p']), 4) if r['p'] == r['p'] else '',
                        k='' if r.get('k') != r.get('k') else r.get('k'), clv='' if r.get('clv') != r.get('clv') else round(float(r['clv']), 4),
                        cause=cause, note=note, at=now))
    if new:
        pd.concat([old, pd.DataFrame(new, columns=INC_COLS)], ignore_index=True).to_csv(f, index=False)
        if store is not None:
            try:
                store.append('learning', [dict(when=now, kind='инцидент', title=f"{sport_ru}: 🔴 промах ⭐ {x['date'][8:10]}.{x['date'][5:7]} {x['label']}",
                                               text=f"- причина: {x['cause']}\n- {x['note']}", sent='') for x in new])
            except Exception:
                pass
    if log is not None:
        log.append(f'🔴 {sport}: инцидентов (промахов ⭐) новых {len(new)}, всего {len(old) + len(new)}')
    return new


def streak(P: pd.DataFrame) -> int:
    """Текущая серия ⭐ без промаха (по дате и номеру)."""
    if P is None or not len(P):
        return 0
    n = 0
    for res in P.sort_values(['date', 'rank'], ascending=False).result:
        if res != 'W':
            break
        n += 1
    return n


def cycle(sport: str, sport_ru: str, store, dp: pd.DataFrame, ch: pd.DataFrame | None, today: dt.date,
          log: list[str] | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Один вызов из прогона: данные → правила → лист learning. Сбой не ломает отчёт (пустой результат)."""
    try:
        P, E = frame_hk(dp) if sport == 'hockey' else frame_dp(dp, ch)
        res = learn(sport, P, E, today)
        n = to_learning(store, sport_ru, res) if store is not None else 0
        try:
            incidents(sport, sport_ru, P, store, log)               # 20.11 Ц12: каждый промах ⭐ — инцидент с причиной
        except Exception as ex:
            if log is not None:
                log.append(f'🔴 {sport}: инциденты — сбой {ex.__class__.__name__}: {ex}')
        if log is not None:
            log.append(f"🧠 {sport}: правил +{len(res['added'])} −{len(res['removed'])}; в лист learning {n}; действует: {rules_line(sport) or 'нет'}")
        return P, E, res
    except Exception as ex:
        if log is not None:
            log.append(f'🧠 {sport}: сбой самообучения {ex.__class__.__name__}: {ex}')
        return pd.DataFrame(), pd.DataFrame(), {}


def safe(fn, *a) -> str:
    """Блок отчёта без риска уронить отправку."""
    try:
        return fn(*a) or ''
    except Exception as ex:
        return f'🧠 блок самообучения: сбой {ex.__class__.__name__}'
