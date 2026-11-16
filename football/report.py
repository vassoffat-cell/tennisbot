"""Футбол: единый отчёт (суточный и по кнопке «Аналитика» — один шаблон), ступени точности, предохранитель ⛔, тревоги 🚨.

Счёт ведётся с перехода на «только модель» (08.11.2026): матчи с источником «модель» и показанные линии (shown = 1).
  • Исход — сторона с наибольшей вероятностью; ожидание угаданных = Σ max p; E[LL] = среднее −Σ p·ln p ± σ/√n.
  • Линии — доля верных по рынку: коротко (последние 10) и длинно (последние 100); ожидание = Σ p.
  • Цвет итоговых строк: 🟢 цель ступени достигнута; 🟡 ниже цели в пределах 2σ; 🔴 ниже более чем на 2σ.
    Цвет рынка линий — против его ожидания Σ p на последних 100: 🟢 z > −1, 🟡 −2 < z ≤ −1, 🔴 z ≤ −2.
    Тренд ▲▼▶ — 7 дн против прошлых 7 (разница больше 2 п.п.).
  • Ступень: наибольшая из C.STEPS, у которой нижняя граница 95% интервала на последних 100 ≥ ступени; цель — следующая.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config as C
import flines as FL
import journal as J
import dpcore
import notify
import rtable as RT

NB = ' '                       # неразрывный пробел: ведущие пробелы Telegram иначе съедает
MARKETS = [m for m in FL.MARKET_RU if m != '1X2']
BUCKETS = ((0.40, 0.50), (0.50, 0.60), (0.60, 0.70), (0.70, 1.01))
COLOR = {3: '🟢', 2: '🟡', 1: '🔴'}


def _blank(x) -> bool:
    return str(x).strip() in ('', 'nan', 'None', 'NaN')


def fmt(x: float, d: int = 0) -> str:
    return f'{x:.{d}f}'.replace('.', ',')


# ======================================================================== выборки
def outcome(r) -> int | None:
    if str(r.status) == 'ET':
        return 1
    if str(r.status) != 'FT':
        return None
    hg, ag = J.num(r.hg), J.num(r.ag)
    return 0 if hg > ag else 1 if hg == ag else 2


def matches(af: pd.DataFrame) -> pd.DataFrame:
    """Доигранные матчи модели: p (3 вероятности), o (исход), hit, pmax, ll, ent, day (местная дата начала)."""
    f = af[af.status.astype(str).isin(['FT', 'ET']) & (af.source.astype(str) == 'model')].copy()
    if f.empty:
        return pd.DataFrame(columns=['p_h', 'p_d', 'p_a', 'o', 'hit', 'pmax', 'll', 'ent', 'day', 'comp_name', 'start_utc'])
    P = f[['p_h', 'p_d', 'p_a']].map(J.num).values.astype(float)
    o = np.array([outcome(r) for r in f.itertuples()])
    f['o'] = o
    f['hit'] = (P.argmax(1) == o).astype(int)
    f['pmax'] = P.max(1)
    f['ll'] = -np.log(np.clip(P[np.arange(len(f)), o], 1e-6, 1))
    f['ent'] = -(P * np.log(np.clip(P, 1e-9, 1))).sum(1)
    f['day'] = (pd.to_datetime(f.start_utc) + pd.Timedelta(hours=C.TZ_OFFSET_HOURS - C.DAY_START_HOUR)).dt.date
    return f.sort_values('start_utc')


def _shown(r) -> bool:
    v = str(r.shown).strip()
    if v in ('1', '1.0'):
        return True
    if v in ('0', '0.0'):
        return False
    return FL.visible(J.num(r.p), str(r.market))          # старые записи модели — по тому же правилу


def lines(ld: pd.DataFrame) -> pd.DataFrame:
    """Рассчитанные показанные линии модели: market, p, won, day."""
    x = ld[ld.result.astype(str).isin(['W', 'L']) & (ld.source.astype(str) == 'model')].copy()
    if len(x):
        x = x[[_shown(r) for r in x.itertuples()]]
    x['p'] = x.p.map(J.num).astype(float)
    x['won'] = (x.result.astype(str) == 'W').astype(int)
    x['day'] = (pd.to_datetime(x.start_utc) + pd.Timedelta(hours=C.TZ_OFFSET_HOURS - C.DAY_START_HOUR)).dt.date
    return x.sort_values('start_utc')


# ======================================================================== ступени и цвет
def step_target(hits: np.ndarray, ladder: tuple) -> tuple[float, float | None]:
    """(цель, достигнутая ступень). Достигнута — нижняя граница 95% на последних 100 ≥ ступени."""
    h = hits[-100:]
    reached = None
    if len(h) >= 30:
        p = h.mean(); lo = (p - 1.96 * np.sqrt(p * (1 - p) / len(h))) * 100
        for s in ladder:
            if lo >= s:
                reached = s
    nxt = [s for s in ladder if reached is None or s > reached]
    return (nxt[0] if nxt else ladder[-1]), reached


def wilson(acc: float, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% интервал Уилсона для доли (в %)."""
    if n == 0:
        return 0.0, 100.0
    d = 1 + z * z / n
    c = (acc + z * z / (2 * n)) / d
    h = z * np.sqrt(acc * (1 - acc) / n + z * z / (4 * n * n)) / d
    return (c - h) * 100, (c + h) * 100


def color_vs(acc: float, n: int, target: float) -> int:
    """Высокая планка (решение пользователя 08.11): 🟢 — цель ДОКАЗАНА (нижняя граница 95% ≥ цели);
    🔴 — значимо ниже (верхняя граница < цели); 🟡 — не доказано ни то, ни другое (мало данных или около цели)."""
    lo, hi = wilson(acc, n)
    return 3 if lo >= target else 1 if hi < target else 2


def trend(df: pd.DataFrame, col: str, today) -> str:
    a = df[(df.day > today - pd.Timedelta(days=7).to_pytimedelta())]
    b = df[(df.day <= today - pd.Timedelta(days=7).to_pytimedelta()) & (df.day > today - pd.Timedelta(days=14).to_pytimedelta())]
    if len(a) < 5 or len(b) < 5:
        return '▶'
    d = a[col].mean() - b[col].mean()
    return '▲' if d > 0.02 else '▼' if d < -0.02 else '▶'


def zscore(won: np.ndarray, p: np.ndarray) -> float:
    v = float(np.sum(p * (1 - p)))
    return float((won.sum() - p.sum()) / np.sqrt(v)) if v > 0 else 0.0


# ======================================================================== предохранитель и тревоги
def breaker(ld: pd.DataFrame, state: dict) -> tuple[dict, list[str]]:
    """⛔ рынок выключается, если на последних 30 показанных линиях попаданий значимо меньше ожидания (z ≤ −2);
    включается обратно при z > −1. Возвращает (новое состояние {рынок: с какой даты}, сообщения 🚨/✅)."""
    L = lines(ld)
    new, msgs = dict(state), []
    for mk in MARKETS:
        s = L[L.market.astype(str) == mk].tail(C.BREAKER_N)
        if len(s) < C.BREAKER_N:
            continue
        z = zscore(s.won.values, s.p.values)
        ru = FL.MARKET_RU[mk]
        if mk not in new and z <= C.BREAKER_Z:
            new[mk] = J.now_utc()[:10]
            msgs.append(f'🚨 <b>Футбол · ⛔ рынок «{ru}» выключен</b>\nПоследние {len(s)}: верных {int(s.won.sum())}, '
                        f'ожидание {fmt(s.p.sum(), 1)} (z = {fmt(z, 1)}). Линии рынка не показываются и не идут в ставку дня, '
                        f'но считаются дальше; включится сам, когда z > −1.')
        elif mk in new and z > C.BREAKER_BACK_Z:
            new.pop(mk)
            msgs.append(f'✅ <b>Футбол · рынок «{ru}» снова включён</b> (последние {len(s)}: {int(s.won.sum())}, '
                        f'ожидание {fmt(s.p.sum(), 1)}, z = {fmt(z, 1)}).')
    return new, msgs


def daypick_alarms(dp: pd.DataFrame, rt: dict) -> list[str]:
    """ТЗ-004, контроль качества ставки дня: 🚨 ROI «с перевесом» значимо < 0 после ≥ 30 ставок; 🚨 доля «без перевеса» > 80% за 14 дней."""
    if not len(dp) or 'group' not in dp.columns:
        return []
    out, st = [], rt.setdefault('dp_alarm', {})
    d = dp[(dp.group.astype(str) == 'value') & dp.result.astype(str).isin(['W', 'L']) & dp.k.map(J.num).notna()]
    if len(d) >= 30:
        k = d.k.map(J.num).astype(float).values; w = (d.result.astype(str) == 'W').values
        pp = d.p_pin.map(J.num).astype(float).values if 'p_pin' in d.columns else 1 / k
        pnl = np.where(w, k - 1, -1.0); exp = pp * k - 1
        sd = np.sqrt(np.sum(pp * (1 - pp) * k * k)) / len(d)
        z = (pnl.mean() - 0.0) / sd if sd > 0 else 0.0
        bad = pnl.mean() < 0 and z <= -2
        if bad and not st.get('value_bad'):
            out.append(f'🚨 Ставка дня 💰 выгодная: ROI {pnl.mean() * 100:+.1f}% на {len(d)} ставках (ожидалось {exp.mean() * 100:+.1f}%) — '
                       'значимо ниже нуля, нужен разбор')
        st['value_bad'] = bool(bad)
    rec = dp[pd.to_datetime(dp.date, errors='coerce') >= pd.Timestamp.now() - pd.Timedelta(days=14)]
    rec = rec[rec.group.astype(str).isin(['value', 'fallback'])]
    if len(rec) >= 7:
        share = float((rec.group.astype(str) == 'fallback').mean())
        if share > 0.8 and not st.get('fallback_hi'):
            out.append(f'🚨 За 14 дней {share * 100:.0f}% ставок дня — 🎯 уверенные: перевес над Pinnacle почти не находится '
                       '(проверить котировки Pinnacle / скриншоты)')
        st['fallback_hi'] = share > 0.8
    return out


def outcome_alarm(af: pd.DataFrame, rt: dict) -> list[str]:
    """🚨 исход: на последних 50 матчах угаданных значимо меньше ожидания (z ≤ −2) — один раз до восстановления."""
    M = matches(af).tail(C.ALARM_N)
    if len(M) < C.ALARM_N:
        return []
    z = zscore(M.hit.values, M.pmax.values)
    on = rt.get('alarm_outcome', False)
    if z <= -2 and not on:
        rt['alarm_outcome'] = True
        return [f'🚨 <b>Футбол · исход ниже ожидания</b>\nПоследние {len(M)}: угадано {int(M.hit.sum())}, ожидание '
                f'{fmt(M.pmax.sum(), 1)} (z = {fmt(z, 1)}). Разбор — в чате отчётов (ТЗ с гипотезами).']
    if z > -1 and on:
        rt['alarm_outcome'] = False
        return [f'✅ <b>Футбол · исход восстановился</b> (последние {len(M)}: {int(M.hit.sum())}, ожидание {fmt(M.pmax.sum(), 1)}).']
    return []


# ======================================================================== единый отчёт
LMARK = {'W': '✅', 'L': '❌', 'P': '⚪'}


def lab(market: str, label: str) -> str:
    """Полная запись линии: у тенниса в листе «Б 6.5» / «М 14.5» без рынка → «ИТ1 Б 6.5», «ТМ 14.5»."""
    t = str(label).strip()
    if t[:1] in ('Б', 'М'):
        return {'TOTAL': 'Т', 'TOT': 'Т', 'IT1': 'ИТ1 ', 'IT2': 'ИТ2 '}.get(str(market), '') + t
    return t


def _line_rows(ld: pd.DataFrame) -> dict:
    """Показанные линии по матчу: espn_id → [(метка, линия, P)] — для полного списка в суточном отчёте."""
    out: dict = {}
    if not len(ld):
        return out
    X = ld.copy()
    X['sid'] = X.espn_id.map(lambda x: str(J.num(x)))
    X = X[[_shown(r) for r in X.itertuples()]]
    for r in X.itertuples():
        res = str(r.result).strip()
        out.setdefault(r.sid, []).append((LMARK.get(res, '⏳'), lab(r.market, r.label), J.num(r.p)))
    return out


def _day_items(d: pd.DataFrame, lc: dict, lx: dict | None = None) -> list[str]:
    """Каждый прогноз: ✅/❌ (в счёт), ⚪ отмена (не в счёт), ⏳ ждёт итога, 🕒 не начат; исход модели, P, счёт,
    линии угадано/рассчитано (lc: espn_id → (угадано, рассчитано, показано))."""
    e, out = notify.esc, []
    now = pd.Timestamp.now(tz='UTC').tz_localize(None)
    for r in d.itertuples():
        p = [J.num(r.p_h), J.num(r.p_d), J.num(r.p_a)]; k = int(np.argmax(p))
        st = str(r.status)
        won, ln, shown = lc.get(str(J.num(r.espn_id)), (0, 0, 0))
        lt = f' · линии {won}/{ln}' if ln else (f' · линий {shown}' if shown else '')
        if st == 'FT':
            hg, ag = int(J.num(r.hg)), int(J.num(r.ag))
            mark, tail = ('✅' if k == (0 if hg > ag else 1 if hg == ag else 2) else '❌'), f'{hg}:{ag}'
        elif st == 'ET':
            mark, tail = ('✅' if k == 1 else '❌'), 'ничья в осн. время'
        elif not _blank(r.settled):
            mark, tail = '⚪', ('турнир не отслеживается — не в счёт' if st == 'OFF' else 'отменён / перенесён — не в счёт')
        elif pd.to_datetime(r.start_utc, errors='coerce') > now:
            mark, tail = '🕒', 'не начат (перенос)'
        else:
            mark, tail = '⏳', 'ждёт итога'
        item = (f"{mark} {e(r.home)} — {e(r.away)} · {['П1', 'X', 'П2'][k]} {fmt(p[k] * 100)}% · {tail}{lt}")
        for m_, lab, p_ in (lx or {}).get(str(J.num(r.espn_id)), []):
            item += f'\n   {m_} {e(lab)} · {fmt(p_ * 100)}%'
        out.append(item)
    return out


def day_lists(af: pd.DataFrame, ld: pd.DataFrame, daily_day, late: set | None = None) -> list[str]:
    """Свёрнутые списки суточного отчёта (охват — каждый прогноз под контролем):
    ▸ Матчи за сутки — все прогнозы суток со статусом; ▸ Дорасчитано за прошлые дни — доигранные после своего отчёта;
    ▸ Ждут результата — прогнозы прошлых дней без итога (> 36 ч)."""
    if not len(af):
        return []
    A = af[af.source.astype(str) == 'model'].copy() if 'source' in af.columns else af.copy()
    A['day'] = (pd.to_datetime(A.start_utc, errors='coerce') + pd.Timedelta(hours=C.TZ_OFFSET_HOURS - C.DAY_START_HOUR)).dt.date
    A['sid'] = A.espn_id.map(lambda x: str(J.num(x)))
    A = A.sort_values('start_utc')
    lc = {}
    if len(ld):
        X = ld.copy(); X['sid'] = X.espn_id.map(lambda x: str(J.num(x)))
        X = X[[_shown(r) for r in X.itertuples()]]
        for sid, g in X.groupby('sid'):
            res = g.result.astype(str)
            lc[sid] = (int((res == 'W').sum()), int(res.isin(['W', 'L']).sum()), len(g))
    lx = _line_rows(ld)
    out = []
    d = A[A.day == daily_day]
    if len(d):
        it = _day_items(d, lc, lx)
        cnt = {m: sum(x.startswith(m) for x in it) for m in ('✅', '❌', '⏳', '🕒', '⚪')}
        head = ' · '.join(f'{m} {n}' for m, n in cnt.items() if n)
        out += notify.folded(f'▸ Матчи за сутки ({len(d)}: {head})', it)
    if late:
        x = A[A.sid.isin(late)]
        if len(x):
            out += notify.folded(f'▸ Дорасчитано за прошлые дни ({len(x)})',
                                 [f'{r.day:%d.%m} {s}' for r, s in zip(x.itertuples(), _day_items(x, lc, lx))])
    now = pd.Timestamp.now(tz='UTC').tz_localize(None)
    old = A[(A.day < daily_day) & A.settled.map(_blank).astype(bool)
            & (pd.to_datetime(A.start_utc, errors='coerce') < now - pd.Timedelta(hours=36))]
    if len(old):
        out += notify.folded(f'▸ Ждут результата ({len(old)})',
                             [f'{r.day:%d.%m} {s}' for r, s in zip(old.itertuples(), _day_items(old, lc, lx))])
    return out


def build(af: pd.DataFrame, ld: pd.DataFrame, dp: pd.DataFrame, title: str, kcal: dict | None = None,
          kprev: dict | None = None, off: dict | None = None, notes: list[str] | None = None,
          daily_day=None, period: str = 'всего', extra: list[str] | None = None,
          late: set | None = None, split: bool = False) -> list[str]:
    """Блоки отчёта (HTML): заголовок со статусом, таблица «Линия (цель) | Кол-во | %» (rtable), короткие примечания;
    у суточного (daily_day) — строки «сутки / 7 дней / всего» и свёрнутый список матчей суток."""
    e = notify.esc
    M, L = matches(af), lines(ld)
    today = daily_day or C.wday(pd.Timestamp.now(tz='UTC').tz_localize(None) + pd.Timedelta(hours=C.TZ_OFFSET_HOURS))
    off = off or {}
    T, tail, worst, S = [], [], 3, []
    d7 = (daily_day - pd.Timedelta(days=7).to_pytimedelta()) if daily_day is not None else None
    W7 = ' 7 дней'
    if split and daily_day is not None:                        # 14.11: «за эту неделю» — с понедельника, а не скользящие 7 дней
        d7 = daily_day - pd.Timedelta(days=daily_day.weekday() + 1).to_pytimedelta()
        W7 = ' эта неделя'

    # исход
    if len(M):
        acc, n = M.hit.mean(), len(M)
        tgt, _ = step_target(M.hit.values, C.STEPS['outcome'])
        c = color_vs(acc, n, tgt); worst = min(worst, c)
        g = RT.num(tgt) + '%'
        if daily_day is not None:
            T.append((f'Исход ({g})', '', '', ''))
            d = M[M.day == daily_day]; w = M[M.day > d7]
            T += [RT.row(' сутки', d.hit.sum(), len(d)), RT.row(W7, w.hit.sum(), len(w)),
                  RT.row(' всего', M.hit.sum(), n, mark=COLOR[c] + trend(M, 'hit', today))]
        else:
            T.append(RT.row('Исход', M.hit.sum(), n, g, COLOR[c] + trend(M, 'hit', today)))
        ll, ent = M.ll.mean(), M.ent.mean()
        se = M.ll.std(ddof=1) / np.sqrt(n) if n > 1 else 0.0
        z = (ll - ent) / se if se > 0 else 0.0
        c = 3 if z <= 0 else 2 if z <= 2 else 1; worst = min(worst, c)          # 🟢 — не хуже ожидаемого
        T.append((f'Лог-лосс ({RT.num(ent, 3)})', str(n), RT.num(ll, 3), COLOR[c]))
        T.append(('Калибровка', '', '', ''))
        for lo, hi in BUCKETS:
            b = M[(M.pmax >= lo) & (M.pmax < hi)]
            if len(b) == 0:
                continue
            pf, fa = b.pmax.mean(), b.hit.mean()
            sd = np.sqrt(pf * (1 - pf) / len(b))
            cc = 3 if abs(fa - pf) <= sd else 2 if abs(fa - pf) <= 2 * sd else 1
            if len(b) >= 10:                                          # цвет корзины — с 10 матчей (меньше — шум)
                worst = min(worst, cc)
            rng = f'{lo * 100:.0f}+' if hi > 1 else f'{lo * 100:.0f}–{hi * 100:.0f}'
            T.append(RT.row(f' {rng}', b.hit.sum(), len(b), RT.pct(pf, 0) + '%', COLOR[cc] if len(b) >= 10 else ''))
    else:
        T.append(('Исход — доигранных пока нет', '', '', ''))

    # линии
    FX = L.iloc[0:0]
    if len(L) and getattr(C, 'T32_FIXED_TOTAL', False):          # T32: тотал с фиксированной линией — отдельной строкой,
        fx = L.market.astype(str).isin(['TOTAL', 'TOT']) & (L.start_utc.astype(str) >= C.FIXED_TOTAL_FROM)
        FX, L = L[fx], L[~fx]                                     # в общую цель «Линии» не входит (P ≈ 50–65%, не 65–75%)
    if len(L):
        tgt, _ = step_target(L.won.values, C.STEPS['lines'])
        c = color_vs(L.won.mean(), len(L), tgt); worst = min(worst, c)
        T.append(RT.row('Линии', L.won.sum(), len(L), RT.num(tgt) + '%', COLOR[c] + trend(L, 'won', today)))
        if daily_day is not None:
            d = L[L.day == daily_day]; w = L[L.day > d7]
            T += [RT.row(' сутки', d.won.sum(), len(d)), RT.row(W7, w.won.sum(), len(w))]
        for mk in MARKETS:
            s = L[L.market.astype(str) == mk]
            if s.empty:
                continue
            z = zscore(s.won.values, s.p.values)                    # рынок — против своего ожидания Σp
            cc = 3 if z >= 0 else 2 if z > -2 else 1                 # 🟢 — не ниже обещанного моделью
            T.append(RT.row(' ' + RT.SHORT.get(mk, mk), s.won.sum(), len(s), RT.pct(s.p.mean(), 0) + '%',
                            COLOR[cc] + (' ⛔' if mk in off else '')))
            if daily_day is None and mk in ('TOT', 'TOTAL', 'IT1', 'IT2'):   # ТЗ-003 п. 3: Б / М отдельно (не в суточном)
                for pk, ru in (('O', 'Б'), ('U', 'М')):
                    q = s[s.pick.astype(str) == pk]
                    if len(q):
                        T.append(RT.row(f'  {ru}', q.won.sum(), len(q), RT.pct(q.p.mean(), 0) + '%'))
    elif not len(FX):
        T.append(('Линии — рассчитанных пока нет', '', '', ''))
    if len(FX):                                                   # цель — среднее обещанное P; цвет — против Σp
        z = zscore(FX.won.values, FX.p.values)
        cc = 3 if z >= 0 else 2 if z > -2 else 1
        worst = min(worst, cc) if len(FX) >= 30 else worst
        T.append(RT.row('Тотал фикс.', FX.won.sum(), len(FX), RT.pct(FX.p.mean(), 0) + '%', COLOR[cc]))
        if daily_day is not None:
            d = FX[FX.day == daily_day]
            T.append(RT.row(' сутки', d.won.sum(), len(d)))
        for pk, ru in (('O', ' Б'), ('U', ' М')):
            q = FX[FX.pick.astype(str) == pk]
            if len(q):
                T.append(RT.row(ru, q.won.sum(), len(q), RT.pct(q.p.mean(), 0) + '%'))
        hi = FX[FX.p >= 0.60]
        if len(hi):
            T.append(RT.row(' P ≥ 60%', hi.won.sum(), len(hi), RT.pct(hi.p.mean(), 0) + '%'))

    # ставка дня (split — отдельным сообщением: dp_report)
    D = dp[dp.result.astype(str).isin(['W', 'L'])].copy() if len(dp) else dp
    if split:
        D = D.iloc[0:0]
    if len(D) and 'rank' in D.columns:                           # 14.11: строка «Ставка дня» — только ⭐1 (первый приговор и его правки);
        D = D[D['rank'].astype(str).str.strip().isin(['', 'nan', '1', '1.0'])]   # ⭐2/⭐3 — строкой «➕ новые линии»
    if len(D):
        D['won'] = (D.result.astype(str) == 'W').astype(int); D['p'] = D.p.map(J.num).astype(float)
        D['day'] = pd.to_datetime(D.date).dt.date
        tgt, _ = step_target(D.won.values, C.STEPS['daypick'])
        c = color_vs(D.won.mean(), len(D), tgt)
        T.append(RT.row('Ставка дня ⭐1', D.won.sum(), len(D), RT.num(tgt, 0) + '%', COLOR[c]))
        if daily_day is not None:
            w = D[D.day > d7]
            T.append(RT.row(W7, w.won.sum(), len(w)))
    ops = ops_rows(dp) if not split else []
    if ops and not len(D):
        T.append(('Ставка дня — итогов пока нет', '', '', ''))
    T += ops

    # набор данных и самокалибровка
    T.append(('До выводов', '', '', ''))
    T.append(RT.row(' калибровка', min(len(L), C.CALIB_PRIOR), C.CALIB_PRIOR))
    T.append(RT.row(' точность', min(len(M), C.TARGET_N), C.TARGET_N))
    if kcal is not None:
        act = [(mk, v) for mk, v in kcal.items() if v[1] >= C.CALIB_MIN]
        kr = [f'{RT.SHORT.get(mk, mk)} {RT.num((kprev or {}).get(mk, (1.0, 0))[0], 2)}→{RT.num(k, 2)}' for mk, (k, n) in act]
        for mk, (k, n) in act:                                     # k самокалибровки — строкой на рынок
            S.append((f'k {RT.SHORT.get(mk, mk)}', f'{RT.num((kprev or {}).get(mk, (1.0, 0))[0], 2)}→{RT.num(k, 2)}'))
        if not act:
            S.append(('k', f'1,00 (< {C.CALIB_MIN} линий)'))
    S += [RT.note_row(x) for x in (extra or [])]                # охват: дорасчитано за прошлые дни, ждут результата
    if notes is not None:
        S += [RT.note_row(x) for x in notes] if notes else [('Данные', '✅ ок')]

    status = {3: '🟢 цели доказаны', 2: '🟡 цели ещё не доказаны', 1: '🔴 нужен разбор'}[worst]
    blocks = [f'<b>{e(title)}</b> · {status}\n' + RT.render(T) + ('\n' + RT.kv(S) if S and not split else '')
              + ('\n' + '\n'.join(tail) if tail else '')]
    if daily_day is not None:
        lst = DP_LIST(dp, daily_day) if not split else ''
        if lst:
            blocks.append(lst)
        blocks += day_lists(af, ld, daily_day, late)
    return blocks


def dp_report(dp: pd.DataFrame, title: str, daily_day=None, d0=None) -> list[str]:
    """14.11: «другим сообщением — отчёт по ставкам дня». Таблица: ⭐1 сутки / эта неделя (с пн) / всего, ➕ новые линии, ✏️ правки
    первого приговора, отдача по кф, 🔥/⚔️; ниже — каждая ставка дня суток (или каждого дня недели) отдельной строкой."""
    e = notify.esc
    T = []
    wl = dp[dp.result.astype(str).isin(['W', 'L'])].copy() if len(dp) else dp
    rk = (wl['rank'].astype(str).str.strip() if 'rank' in wl.columns else pd.Series('1', index=wl.index)) if len(wl) else pd.Series(dtype=str)
    D = wl[rk.isin(['', 'nan', '1', '1.0'])].copy() if len(wl) else wl
    if len(D):
        D['won'] = (D.result.astype(str) == 'W').astype(int)
        D['day'] = pd.to_datetime(D.date, errors='coerce').dt.date
        tgt, _ = step_target(D.won.values, C.STEPS['daypick'])
        c = color_vs(D.won.mean(), len(D), tgt)
        if daily_day is not None and d0 is None:
            mon = daily_day - pd.Timedelta(days=daily_day.weekday()).to_pytimedelta()
            d1, w = D[D.day == daily_day], D[D.day >= mon]
            T += [(f'⭐1 Ставка дня ({RT.num(tgt, 0)}%)', '', '', ''), RT.row(' сутки', d1.won.sum(), len(d1)),
                  RT.row(' эта неделя', w.won.sum(), len(w)), RT.row(' всего', D.won.sum(), len(D), mark=COLOR[c])]
        else:
            T.append(RT.row('⭐1 Ставка дня', D.won.sum(), len(D), RT.num(tgt, 0) + '%', COLOR[c]))
    else:
        T.append(('⭐1 — итогов пока нет', '', '', ''))
    T += ops_rows(dp)
    blocks = [f'<b>{e(title)}</b>\n' + RT.render(T)]
    days = [daily_day] if (daily_day is not None and d0 is None) else \
        ([d0 + pd.Timedelta(days=k).to_pytimedelta() for k in range((daily_day - d0).days + 1)] if d0 is not None else [])
    for d in days:
        lst = DP_LIST(dp, d)
        if lst:
            blocks.append(lst)
    return blocks


def DP_LIST(dp: pd.DataFrame, day) -> str:
    """14.11 (замечание пользователя: «вчера было три ставки дня — в отчёте не увидел ни одной»): каждая ставка дня суток
    отдельной строкой — номер, линия, матч, P, кф, итог; правки первого приговора — под ней. Стоит сразу под таблицей отчёта."""
    if not len(dp):
        return ''
    x = dp[pd.to_datetime(dp.date, errors='coerce').dt.date == day].copy()
    if not len(x):
        return ''
    if 'status' in x.columns:
        x = x[x.status.astype(str) != 'OFF']
    rk = x['rank'].astype(str).str.strip().replace({'': '1', 'nan': '1'}).map(lambda v: v[:-2] if v.endswith('.0') else v) if 'rank' in x.columns \
        else pd.Series('1', index=x.index)
    x = x.assign(rk=rk).sort_values('rk')
    e = notify.esc
    mark = {'W': '✅', 'L': '❌', 'P': '⚪'}
    w = int((x.result.astype(str) == 'W').sum()); n = int(x.result.astype(str).isin(['W', 'L']).sum())
    out = [f'<b>{dpcore.ICON} Ставки дня · {day:%d.%m}</b> — угадано {w}/{n}' if n else f'<b>{dpcore.ICON} Ставки дня · {day:%d.%m}</b>']
    for r in x.itertuples(index=False):
        p = J.num(r.p); k = J.num(getattr(r, 'k', ''))
        a, b = (getattr(r, 'player1', '') or getattr(r, 'home', ''), getattr(r, 'player2', '') or getattr(r, 'away', ''))
        line = (f"{mark.get(str(r.result), '⏳')} {dpcore.ICON}{r.rk} <b>{e(str(r.label))}</b> · {e(str(a))} — {e(str(b))}"
                + (f" · {p * 100:.0f}%" if p == p else '') + (f" · кф {k:.2f}".replace('.', ',') if k == k else '')
                + (f" · EV {(p * k - 1) * 100:+.1f}%".replace('.', ',').replace('-', '−') if p == p and k == k and k > 1 else ''))   # 16.11
        out.append(line)
        h = str(getattr(r, 'history', '') or '')
        if h and h != 'nan':
            out += [f'   <i>{e(s.strip())}</i>' for s in h.split(' | ') if s.strip()]
    return '\n'.join(out)


EXTRA_OPS: list = []                                         # строки «🔁 смены» — задаёт прогон перед отчётом


def ops_rows(dp: pd.DataFrame) -> list:
    """Пакет №3 в таблицу: замены ставки дня, скриншоты, принятия, отдача по кф 1xBet, выгодные (цель — скриншотов 0)."""
    if not len(dp):
        return []
    out = []                                                    # дни с правкой ⭐1 — в EXTRA_OPS (dpcore.changes_rows)
    if 'screenshots' in dp.columns and getattr(C, 'R4_SHOT_CYCLE', True):    # 14.11: без цикла скриншотов строка не нужна
        out.append((' скриншотов', str(int(dp.screenshots.map(J.num).fillna(0).sum())), '', ''))
    if 'k' in dp.columns:
        d = dp[dp.k.map(J.num).notna() & dp.result.astype(str).isin(['W', 'L', 'P'])]
        if len(d):
            pnl = [(J.num(r.k) - 1) if str(r.result) == 'W' else (0.0 if str(r.result) == 'P' else -1.0) for r in d.itertuples()]
            out.append((f' отдача (кф {RT.num(d.k.map(J.num).mean(), 2)})', str(len(d)), RT.pct(float(np.mean(pnl))),
                        '🟢' if np.mean(pnl) > 0 else '🔴'))
    # 14.11 (решение пользователя): две категории — 💰 выгодная (перевес над Pinnacle) и 🎯 уверенная (без перевеса), не путать;
    # у каждой — угадано, ROI по кф и CLV (цена закрытия: кф × P_закр − 1, лучший ранний показатель плюса)
    grp = (dp.group.astype(str).str.strip() if 'group' in dp.columns else pd.Series('', index=dp.index)).replace({'': 'fallback', 'nan': 'fallback'})
    for g, ru in (('value', ' 💰 выгодные'), ('fallback', ' 🎯 уверенные')):
        d = dp[(grp == g) & dp.result.astype(str).isin(['W', 'L', 'P'])]
        if not len(d):
            continue
        out.append(RT.row(ru, int((d.result.astype(str) == 'W').sum()), int(d.result.astype(str).isin(['W', 'L']).sum())))
        dk = d[d.k.map(J.num).notna()] if 'k' in d.columns else d.iloc[:0]
        if len(dk):
            pnl = [(J.num(r.k) - 1) if str(r.result) == 'W' else (0.0 if str(r.result) == 'P' else -1.0) for r in dk.itertuples()]
            out.append(('  ROI', str(len(dk)), RT.pct(float(np.mean(pnl))), '🟢' if np.mean(pnl) > 0 else '🔴'))
        cv = dp.loc[d.index, 'clv'].map(J.num).dropna() if 'clv' in dp.columns else pd.Series(dtype=float)
        if len(cv):
            out.append(('  CLV', str(len(cv)), RT.pct(float(cv.mean())), '🟢' if cv.mean() > 0 else '🔴'))
    if 'value_ok' in dp.columns:
        vo = dp.value_ok.map(J.num)
        if vo.notna().any():
            out.append(RT.row(' выгодных (90%)', int((vo == 1).sum()), int(vo.notna().sum())))
    wl = dp.result.astype(str).isin(['W', 'L'])
    if 'rank' in dp.columns:                                    # 11.11: доп. ставки ⭐2/⭐3 и синтез с каперами
        ex = dp[wl & (pd.to_numeric(dp['rank'], errors='coerce') >= 2)]          # 14.11: лимит динамический — ⭐2…⭐5
        if len(ex):
            out.append(RT.row(' ➕ новые линии', int((ex.result.astype(str) == 'W').sum()), len(ex)))
    for col, ru in (('fire', ' 🔥 с каперами'), ('clash', ' ⚔️ против каперов')):
        if col in dp.columns:
            d = dp[wl & (dp[col].map(J.num) == 1)]
            if len(d):
                out.append(RT.row(ru, int((d.result.astype(str) == 'W').sum()), len(d)))
    out += list(EXTRA_OPS)                                      # 🔁 смены ставки дня (dpcore.changes_rows; задаёт прогон)
    return out


def week_lines(ld: pd.DataFrame, d0, d1) -> list[str]:
    """Недельный отчёт: все предложенные линии недели, сгруппированные по типу (свёрнутые списки) — дата, матч, линия, P, итог."""
    if not len(ld):
        return []
    X = ld[[_shown(r) for r in ld.itertuples()]].copy()
    X['day'] = (pd.to_datetime(X.start_utc, errors='coerce') + pd.Timedelta(hours=C.TZ_OFFSET_HOURS - C.DAY_START_HOUR)).dt.date
    X = X[(X.day >= d0) & (X.day <= d1)].sort_values('start_utc')
    e, out = notify.esc, []
    for mk, g in X.groupby('market', sort=False):
        res = g.result.astype(str)
        cnt = ' · '.join(f'{m} {n}' for m, n in (('✅', int((res == 'W').sum())), ('❌', int((res == 'L').sum())),
                                                   ('⚪', int((res == 'P').sum())), ('⏳', int((~res.isin(['W', 'L', 'P'])).sum()))) if n)
        items = []
        for day, gd in g.groupby('day', sort=False):                # компактно: по дням, линии подряд
            rows = []
            for r in gd.itertuples():
                who = f"{r.home} — {r.away}"
                rows.append(f"{LMARK.get(str(r.result).strip(), '⏳')} {e(who)} · {e(lab(r.market, r.label))} · {fmt(J.num(r.p) * 100)}%")
            items.append(f'<b>{day:%d.%m}</b>\n' + '\n'.join(rows))
        out += notify.folded(f'▸ {RT.SHORT.get(mk, mk)} ({len(g)}: {cnt})', items)
    return out


def week(af: pd.DataFrame, ld: pd.DataFrame, dp: pd.DataFrame, d0, d1):
    """Выборка недели по суткам системы (08:00 → 08:00) с d0 по d1 включительно — для недельного отчёта по понедельникам."""
    def f(df):
        if df.empty:
            return df
        dd = (pd.to_datetime(df.start_utc, errors='coerce') + pd.Timedelta(hours=C.TZ_OFFSET_HOURS - C.DAY_START_HOUR)).dt.date
        return df[(dd >= d0) & (dd <= d1)]
    if len(dp):
        dd = pd.to_datetime(dp.date, errors='coerce').dt.date
        dp = dp[(dd >= d0) & (dd <= d1)]
    return f(af), f(ld), dp


def filtered(af: pd.DataFrame, ld: pd.DataFrame, dp: pd.DataFrame, month: str | None = None, comp: str | None = None):
    def f(df, col_date='start_utc'):
        if df.empty:
            return df
        x = df
        if month:
            x = x[x[col_date].astype(str).str[:7] == month]
        if comp:
            x = x[x.comp_name.astype(str) == comp]
        return x
    return f(af), f(ld), f(dp)


MONTHS = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь']


def all_reports(af: pd.DataFrame, ld: pd.DataFrame, dp: pd.DataFrame, kcal: dict, off: dict) -> pd.DataFrame:
    """Готовые отчёты для кнопки «Аналитика»: всё время, каждый месяц, каждый турнир (лист reports)."""
    now = J.now_utc()
    out = [dict(key='all', kind='all', title='Всё время',
                text='\n\n'.join(build(af, ld, dp, '📊 Футбол · всё время', kcal=kcal, off=off)), updated=now)]
    mod = af[af.source.astype(str) == 'model']
    for m in sorted({str(x)[:7] for x in mod.start_utc if str(x)[:4].isdigit()}, reverse=True)[:12]:
        a, l_, d = filtered(af, ld, dp, month=m)
        t = f'{MONTHS[int(m[5:7]) - 1]} {m[:4]}'
        out.append(dict(key=f'm:{m}', kind='month', title=t,
                        text='\n\n'.join(build(a, l_, d, f'📊 Футбол · {t}', off=off, period='за месяц')), updated=now))
    for comp in sorted(set(mod.comp_name.astype(str))):
        a, l_, d = filtered(af, ld, dp, comp=comp)
        out.append(dict(key=f't:{comp}', kind='event', title=comp,
                        text='\n\n'.join(build(a, l_, d, f'📊 Футбол · {comp}', off=off, period='в турнире')), updated=now))
    return pd.DataFrame(out)
