"""Теннис: единый отчёт (суточный и по кнопке «Аналитика» — один шаблон), ступени точности, предохранитель ⛔, тревоги 🚨.

Счёт: доигранные матчи (FIN; отказы не в счёт) и показанные линии (shown = 1; у старых записей — по тому же правилу 0,50–0,80).
  • Исход — игрок с большей вероятностью; ожидание угаданных = Σ max(p, 1−p); E[LL] = среднее энтропий ± σ/√n.
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
import lines as FL
import journal as J
import notify
import rtable as RT

NB = ' '                       # неразрывный пробел: ведущие пробелы Telegram иначе съедает
MARKETS = ['HCP', 'IT1', 'IT2', 'TOTAL']
BUCKETS = ((0.50, 0.60), (0.60, 0.70), (0.70, 0.80), (0.80, 1.01))
COLOR = {3: '🟢', 2: '🟡', 1: '🔴'}


def _blank(x) -> bool:
    return str(x).strip() in ('', 'nan', 'None', 'NaN')


def fmt(x: float, d: int = 0) -> str:
    return f'{x:.{d}f}'.replace('.', ',')


# ======================================================================== выборки
def matches(af: pd.DataFrame) -> pd.DataFrame:
    """Доигранные матчи: p1, w1, hit, pmax, ll, ent, day (местная дата начала)."""
    f = af[af.status.astype(str) == 'FIN'].copy()
    if f.empty:
        return pd.DataFrame(columns=['p1', 'w1', 'hit', 'pmax', 'll', 'ent', 'day', 'event', 'start_utc', 'tour'])
    p1 = f.p1.map(J.num).astype(float).values
    w1 = (f.winner.map(J.num) == 1).values
    f['p1n'] = p1
    f['hit'] = ((p1 >= 0.5) == w1).astype(int)
    f['pmax'] = np.maximum(p1, 1 - p1)
    f['ll'] = -np.log(np.clip(np.where(w1, p1, 1 - p1), 1e-6, 1))
    f['ent'] = -(p1 * np.log(np.clip(p1, 1e-9, 1)) + (1 - p1) * np.log(np.clip(1 - p1, 1e-9, 1)))
    f['day'] = (pd.to_datetime(f.start_utc) + pd.Timedelta(hours=C.TZ_OFFSET_HOURS)).dt.date
    return f.sort_values('start_utc')


def _shown(r) -> bool:
    v = str(r.shown).strip()
    if v in ('1', '1.0'):
        return True
    if v in ('0', '0.0'):
        return False
    return FL.visible(J.num(r.p), str(r.market))          # старые записи — по тому же правилу


def lines(ld: pd.DataFrame) -> pd.DataFrame:
    """Рассчитанные показанные линии: market, p, won, day."""
    x = ld[ld.result.astype(str).isin(['W', 'L'])].copy()
    if len(x):
        x = x[[_shown(r) for r in x.itertuples()]]
    x['p'] = x.p.map(J.num).astype(float)
    x['won'] = (x.result.astype(str) == 'W').astype(int)
    x['day'] = (pd.to_datetime(x.start_utc) + pd.Timedelta(hours=C.TZ_OFFSET_HOURS)).dt.date
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
            msgs.append(f'🚨 <b>Теннис · ⛔ рынок «{ru}» выключен</b>\nПоследние {len(s)}: верных {int(s.won.sum())}, '
                        f'ожидание {fmt(s.p.sum(), 1)} (z = {fmt(z, 1)}). Линии рынка не показываются и не идут в ставку дня, '
                        f'но считаются дальше; включится сам, когда z > −1.')
        elif mk in new and z > C.BREAKER_BACK_Z:
            new.pop(mk)
            msgs.append(f'✅ <b>Теннис · рынок «{ru}» снова включён</b> (последние {len(s)}: {int(s.won.sum())}, '
                        f'ожидание {fmt(s.p.sum(), 1)}, z = {fmt(z, 1)}).')
    return new, msgs


def outcome_alarm(af: pd.DataFrame, rt: dict) -> list[str]:
    """🚨 исход: на последних 50 матчах угаданных значимо меньше ожидания (z ≤ −2) — один раз до восстановления."""
    M = matches(af).tail(C.ALARM_N)
    if len(M) < C.ALARM_N:
        return []
    z = zscore(M.hit.values, M.pmax.values)
    on = rt.get('alarm_outcome', False)
    if z <= -2 and not on:
        rt['alarm_outcome'] = True
        return [f'🚨 <b>Теннис · исход ниже ожидания</b>\nПоследние {len(M)}: угадано {int(M.hit.sum())}, ожидание '
                f'{fmt(M.pmax.sum(), 1)} (z = {fmt(z, 1)}). Разбор — в чате отчётов (ТЗ с гипотезами).']
    if z > -1 and on:
        rt['alarm_outcome'] = False
        return [f'✅ <b>Теннис · исход восстановился</b> (последние {len(M)}: {int(M.hit.sum())}, ожидание {fmt(M.pmax.sum(), 1)}).']
    return []


# ======================================================================== единый отчёт
def _day_items(d: pd.DataFrame, lc: dict) -> list[str]:
    """Каждый прогноз: ✅/❌ (в счёт), ⚪ снятие/не состоялся (не в счёт), ⏳ ждёт итога, 🕒 не начат; фаворит модели, P, счёт,
    линии угадано/рассчитано (lc: espn_id → (угадано, рассчитано, показано))."""
    e, out = notify.esc, []
    now = pd.Timestamp.now(tz='UTC').tz_localize(None)
    for r in d.itertuples():
        p1 = J.num(r.p1); fav = 1 if p1 >= 0.5 else 2
        name, pm = (r.player1 if fav == 1 else r.player2), max(p1, 1 - p1)
        st, sc = str(r.status), str(r.score) if not _blank(r.score) else ''
        if fav == 2 and sc:
            sc = ' '.join('-'.join(x.split('-')[::-1]) for x in sc.split())
        won, ln, shown = lc.get(str(J.num(r.espn_id)), (0, 0, 0))
        lt = f' · линии {won}/{ln}' if ln else (f' · линий {shown}' if shown else '')
        if st == 'FIN':
            mark, tail = ('✅' if J.num(r.winner) == fav else '❌'), sc
        elif st == 'RET':
            mark, tail = '⚪', f'{sc} снятие — не в счёт'.strip()
        elif st in ('VOID', 'W/O'):
            mark, tail = '⚪', 'не состоялся — не в счёт'
        elif pd.to_datetime(r.start_utc, errors='coerce') > now:
            mark, tail = '🕒', 'не начат (перенос)'
        else:
            mark, tail = '⏳', 'ждёт итога'
        out.append(f'{mark} {e(r.tour)} {e(name)} {fmt(pm * 100)}% · {e(tail)}{lt}')
    return out


def day_lists(af: pd.DataFrame, ld: pd.DataFrame, daily_day, late: set | None = None) -> list[str]:
    """Свёрнутые списки суточного отчёта (охват — каждый прогноз под контролем):
    ▸ Матчи за сутки — все прогнозы суток со статусом; ▸ Дорасчитано за прошлые дни — доигранные после своего отчёта;
    ▸ Ждут результата — прогнозы прошлых дней без итога (> 36 ч)."""
    if not len(af):
        return []
    A = af[af.source.astype(str) == 'model'].copy() if 'source' in af.columns else af.copy()
    A['day'] = (pd.to_datetime(A.start_utc, errors='coerce') + pd.Timedelta(hours=C.TZ_OFFSET_HOURS)).dt.date
    A['sid'] = A.espn_id.map(lambda x: str(J.num(x)))
    A = A.sort_values('start_utc')
    lc = {}
    if len(ld):
        X = ld.copy(); X['sid'] = X.espn_id.map(lambda x: str(J.num(x)))
        X = X[[_shown(r) for r in X.itertuples()]]
        for sid, g in X.groupby('sid'):
            res = g.result.astype(str)
            lc[sid] = (int((res == 'W').sum()), int(res.isin(['W', 'L']).sum()), len(g))
    out = []
    d = A[A.day == daily_day]
    if len(d):
        it = _day_items(d, lc)
        cnt = {m: sum(x.startswith(m) for x in it) for m in ('✅', '❌', '⏳', '🕒', '⚪')}
        head = ' · '.join(f'{m} {n}' for m, n in cnt.items() if n)
        out += notify.folded(f'▸ Матчи за сутки ({len(d)}: {head})', it)
    if late:
        x = A[A.sid.isin(late)]
        if len(x):
            out += notify.folded(f'▸ Дорасчитано за прошлые дни ({len(x)})',
                                 [f'{r.day:%d.%m} {s}' for r, s in zip(x.itertuples(), _day_items(x, lc))])
    now = pd.Timestamp.now(tz='UTC').tz_localize(None)
    old = A[(A.day < daily_day) & A.settled.map(_blank).astype(bool)
            & (pd.to_datetime(A.start_utc, errors='coerce') < now - pd.Timedelta(hours=36))]
    if len(old):
        out += notify.folded(f'▸ Ждут результата ({len(old)})',
                             [f'{r.day:%d.%m} {s}' for r, s in zip(old.itertuples(), _day_items(old, lc))])
    return out


def build(af: pd.DataFrame, ld: pd.DataFrame, dp: pd.DataFrame, title: str, kcal: dict | None = None,
          kprev: dict | None = None, off: dict | None = None, notes: list[str] | None = None,
          daily_day=None, period: str = 'всего', extra: list[str] | None = None,
          late: set | None = None) -> list[str]:
    """Блоки отчёта (HTML): заголовок со статусом, таблица «Линия (цель) | Кол-во | %» (rtable), короткие примечания;
    у суточного (daily_day) — строки «сутки / 7 дней / всего» и свёрнутый список матчей суток."""
    e = notify.esc
    M, L = matches(af), lines(ld)
    today = daily_day or (pd.Timestamp.now(tz='UTC').tz_localize(None) + pd.Timedelta(hours=C.TZ_OFFSET_HOURS)).date()
    off = off or {}
    T, tail, worst = [], [], 3
    d7 = (daily_day - pd.Timedelta(days=7).to_pytimedelta()) if daily_day is not None else None

    # исход
    if len(M):
        acc, n = M.hit.mean(), len(M)
        tgt, _ = step_target(M.hit.values, C.STEPS['outcome'])
        c = color_vs(acc, n, tgt); worst = min(worst, c)
        g = RT.num(tgt) + '%'
        if daily_day is not None:
            T.append((f'Исход ({g})', '', '', ''))
            d = M[M.day == daily_day]; w = M[M.day > d7]
            T += [RT.row(' сутки', d.hit.sum(), len(d)), RT.row(' 7 дней', w.hit.sum(), len(w)),
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
    if len(L):
        tgt, _ = step_target(L.won.values, C.STEPS['lines'])
        c = color_vs(L.won.mean(), len(L), tgt); worst = min(worst, c)
        T.append(RT.row('Линии', L.won.sum(), len(L), RT.num(tgt) + '%', COLOR[c] + trend(L, 'won', today)))
        if daily_day is not None:
            d = L[L.day == daily_day]; w = L[L.day > d7]
            T += [RT.row(' сутки', d.won.sum(), len(d)), RT.row(' 7 дней', w.won.sum(), len(w))]
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
    else:
        T.append(('Линии — рассчитанных пока нет', '', '', ''))

    # ставка дня
    D = dp[dp.result.astype(str).isin(['W', 'L'])].copy() if len(dp) else dp
    if len(D):
        D['won'] = (D.result.astype(str) == 'W').astype(int); D['p'] = D.p.map(J.num).astype(float)
        D['day'] = pd.to_datetime(D.date).dt.date
        tgt, _ = step_target(D.won.values, C.STEPS['daypick'])
        c = color_vs(D.won.mean(), len(D), tgt)
        T.append(RT.row('Ставка дня', D.won.sum(), len(D), RT.num(tgt, 0) + '%', COLOR[c]))
        if daily_day is not None:
            yy, w = D[D.day == daily_day], D[D.day > d7]
            if len(yy):
                tail.append(f'⭐ Вчера: {"✅" if yy.won.iloc[-1] else "❌"} {e(yy.label.iloc[-1])}')
            T.append(RT.row(' 7 дней', w.won.sum(), len(w)))
    ops = ops_rows(dp)
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
        tail.append('🧠 k: ' + (' · '.join(kr) if kr else f'1,00 (меньше {C.CALIB_MIN} линий)'))
    tail += [e(x) for x in (extra or [])]                       # охват: дорасчитано за прошлые дни, ждут результата
    if notes is not None:
        tail.append('⚠️ Данные: ' + ('; '.join(e(x) for x in notes) if notes else 'ок'))

    status = {3: '🟢 цели доказаны', 2: '🟡 цели ещё не доказаны', 1: '🔴 нужен разбор'}[worst]
    blocks = [f'<b>{e(title)}</b> · {status}\n' + RT.render(T) + ('\n' + '\n'.join(tail) if tail else '')]
    if daily_day is not None:
        blocks += day_lists(af, ld, daily_day, late)
    return blocks


def ops_rows(dp: pd.DataFrame) -> list:
    """Пакет №3 в таблицу: замены ставки дня, скриншоты, принятия, отдача по кф 1xBet, выгодные (цель — скриншотов 0)."""
    if not len(dp):
        return []
    days = dp.date.astype(str).nunique()
    ch = dp.changes.map(J.num).fillna(0)
    out = [RT.row(' дни с заменой', int((ch > 0).sum()), days)]
    if 'screenshots' in dp.columns:
        out.append((' скриншотов', str(int(dp.screenshots.map(J.num).fillna(0).sum())), '', ''))
    if 'accepted' in dp.columns:
        acc = dp.accepted.astype(str)
        asked = int(acc.isin(['yes', 'no']).sum())
        if asked:
            out.append(RT.row(' принято', int((acc == 'yes').sum()), asked))
    if 'k' in dp.columns:
        d = dp[dp.k.map(J.num).notna() & dp.result.astype(str).isin(['W', 'L', 'P'])]
        if len(d):
            pnl = [(J.num(r.k) - 1) if str(r.result) == 'W' else (0.0 if str(r.result) == 'P' else -1.0) for r in d.itertuples()]
            out.append((f' отдача (кф {RT.num(d.k.map(J.num).mean(), 2)})', str(len(d)), RT.pct(float(np.mean(pnl))),
                        '🟢' if np.mean(pnl) > 0 else '🔴'))
    if 'value_ok' in dp.columns:
        vo = dp.value_ok.map(J.num)
        if vo.notna().any():
            out.append(RT.row(' выгодных (90%)', int((vo == 1).sum()), int(vo.notna().sum())))
    return out


def filtered(af: pd.DataFrame, ld: pd.DataFrame, dp: pd.DataFrame, month: str | None = None, comp: str | None = None):
    def f(df, col_date='start_utc'):
        if df.empty:
            return df
        x = df
        if month:
            x = x[x[col_date].astype(str).str[:7] == month]
        if comp:
            x = x[x.event.astype(str) == comp]
        return x
    return f(af), f(ld), f(dp)


MONTHS = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь']


def all_reports(af: pd.DataFrame, ld: pd.DataFrame, dp: pd.DataFrame, kcal: dict, off: dict) -> pd.DataFrame:
    """Готовые отчёты для кнопки «Аналитика»: всё время, каждый месяц, каждый турнир (лист reports)."""
    now = J.now_utc()
    out = [dict(key='all', kind='all', title='Всё время',
                text='\n\n'.join(build(af, ld, dp, '📊 Теннис · всё время', kcal=kcal, off=off)), updated=now)]
    for m in sorted({str(x)[:7] for x in af.start_utc if str(x)[:4].isdigit()}, reverse=True)[:12]:
        a, l_, d = filtered(af, ld, dp, month=m)
        t = f'{MONTHS[int(m[5:7]) - 1]} {m[:4]}'
        out.append(dict(key=f'm:{m}', kind='month', title=t,
                        text='\n\n'.join(build(a, l_, d, f'📊 Теннис · {t}', off=off, period='за месяц')), updated=now))
    last = af.groupby(af.event.astype(str)).start_utc.max().sort_values(ascending=False) if len(af) else pd.Series(dtype=str)
    for ev in list(last.index)[:30]:
        a, l_, d = filtered(af, ld, dp, comp=ev)
        tour = str(a.tour.iloc[0]) if len(a) else ''
        out.append(dict(key=f't:{ev}', kind='event', title=f'{tour} {ev}'.strip(),
                        text='\n\n'.join(build(a, l_, d, f'📊 Теннис · {tour} {ev}', off=off, period='в турнире')), updated=now))
    return pd.DataFrame(out)
