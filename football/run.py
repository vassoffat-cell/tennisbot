"""Футбол: автономный цикл в облаке (GitHub Actions каждые 2 часа, в :30 по Ташкенту).

  1. входящие файлы football-data из папки Google Диска (кладёт бот) → проверка ПО СОДЕРЖИМОМУ → база матчей;
  2. ESPN: расписание и результаты 17 турниров — только игровые дни (календарь), параллельно;
  3. подгонка модели (Dixon–Coles по странам: разница сил — голы 50% + удары в створ 50%, уровень голов — голы;
     + сила стран в еврокубках) — каждый запуск, ~2 с;
  4. прогноз матчей ближайших 24 ч — ТОЛЬКО модель + факторы (кф букмекеров не используются):
     исход в каждом матче + 11 линий (показываются с кф ≥ 1,25), самокалибровка k по рынкам — раз в сутки;
  5. расчёт по 90 минутам, сбор составов (этап 2) и новостей (раз в сутки);
  6. Telegram: свежий список (прошлый удаляется); в 00:30 — один суточный отчёт (итоги + самообучение);
     ⭐ ставка дня (закреплённое сообщение); ⛔/🚨 по рынкам; напоминание о файлах по понедельникам с прямыми ссылками.

  python run.py [--dry]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import traceback

import numpy as np
import pandas as pd

import config as C
import daypick
import dc
import fdata
import ffactors as FF
import flines as FL
import fespn
import journal as J
import model
import learning
import names
import news
import notify
import report as R
import runtime
import tips
import tipsbook
import tipswide

sys.path.append(str(__import__('pathlib').Path(__file__).resolve().parent.parent))   # общий модуль отчёта каперов
import caprep  # noqa: E402

LEAGUE_KINDS = ('league',)
SRC_RU = {'market': 'рынок', 'model': 'модель'}
STORE_COLS = ['id', 'comp', 'kind', 'country', 'div', 'date', 'home', 'away', 'home_fd', 'away_fd', 'hg', 'ag', 'status',
              'stage', 'ko']


def _now() -> pd.Timestamp:
    return pd.Timestamp.now(tz='UTC').tz_localize(None)


def _local(ts: pd.Timestamp | None = None) -> pd.Timestamp:
    return (ts if ts is not None else _now()) + pd.Timedelta(hours=C.TZ_OFFSET_HOURS)


def _blank(x) -> bool:
    return str(x).strip() in ('', 'nan', 'None', 'NaN')


def _id(x) -> str:
    try:
        return str(int(float(x)))
    except (TypeError, ValueError):
        return str(x).strip()


def _days(a: dt.date, b: dt.date) -> list[dt.date]:
    return [a + dt.timedelta(days=i) for i in range((b - a).days + 1)]


# ======================================================================== хранилище результатов ESPN
def load_store() -> pd.DataFrame:
    if C.ESPN_STORE.exists():
        df = pd.read_csv(C.ESPN_STORE, dtype={'id': str}, keep_default_na=False, na_values=[''])
        df['date'] = pd.to_datetime(df.date)
        return df
    return pd.DataFrame(columns=STORE_COLS)


def save_store(df: pd.DataFrame) -> None:
    C.ESPN_STORE.parent.mkdir(parents=True, exist_ok=True)
    df.sort_values(['date', 'id']).to_csv(C.ESPN_STORE, index=False, date_format='%Y-%m-%d %H:%M')


# ======================================================================== команды
class Mapper:
    """ESPN → football-data: внутри страны турнира; еврокубки — среди команд всех стран модели."""

    def __init__(self, base: pd.DataFrame) -> None:
        t = pd.concat([base[['home', 'country']].rename(columns={'home': 't'}),
                       base[['away', 'country']].rename(columns={'away': 't'})]).dropna().drop_duplicates()
        last = base.groupby('home').date.max().combine(base.groupby('away').date.max(), max, fill_value=pd.Timestamp(0))
        t['last'] = t.t.map(last)
        t = t.sort_values('last').drop_duplicates('t', keep='last')       # команда — в стране последнего матча
        self.country = dict(zip(t.t, t.country))
        self.by_c = {c: set(g.t) for c, g in t.groupby('country')}
        self.all = set(t.t)
        self.cache: dict[tuple, str | None] = {}
        self.missed: dict[str, str] = {}          # ошибка сопоставления: лига или похожий кандидат (сходство ≥ 0,60)
        self.out_base = 0                          # вне базы (кубки/еврокубки, кандидата нет) — пропуск штатный
        self.missed_uefa = 0

    def __call__(self, name: str, comp: str) -> str | None:
        _, country, _, kind = C.COMPETITIONS[comp]
        k = (name, country)
        if k not in self.cache:
            cand = self.all if country is None else self.by_c.get(country, set())
            r = names.match(name, cand)
            self.cache[k] = r
            if r is None:
                near, ratio = names.nearest(name, cand)
                if kind == 'league' or (country is not None and ratio >= 0.60):
                    self.missed[name] = f'{C.COMPETITIONS[comp][0]}' + (f' → {near}? {ratio:.2f}' if near and ratio >= 0.60 else '')
                else:
                    self.out_base += 1
                    if country is None:
                        self.missed_uefa += 1
        return self.cache[k]


# ======================================================================== план запросов ESPN
def plan(base: pd.DataFrame, store: pd.DataFrame, auto: pd.DataFrame, rt: dict) -> dict[str, list[dt.date]]:
    today = _now().date()
    fd_last = base.groupby('country').date.max()
    st_last = store.groupby('comp').date.max() if len(store) else pd.Series(dtype='datetime64[ns]')
    out = {}
    for comp, (_, country, _, kind) in C.COMPETITIONS.items():
        uefa = kind.startswith('uefa')
        lo, hi = (today - dt.timedelta(days=5), today + dt.timedelta(days=5)) if uefa else \
                 (today - dt.timedelta(days=2), today + dt.timedelta(days=2))
        days = set(_days(lo, hi))
        if kind == 'league' and country in fd_last:                       # результаты после последней даты файлов
            have = st_last.get(comp, pd.NaT)
            start = fd_last[country].date() if pd.isna(have) else max(fd_last[country].date(), have.date())
            start = max(start - dt.timedelta(days=1), today - dt.timedelta(days=C.LEAGUE_BACKFILL_DAYS))
            days |= set(_days(start, today))
        out[comp] = days
    pend = auto[auto.settled.map(_blank).astype(bool)]
    for comp, s in zip(pend.comp, pend.start_utc):
        try:
            d = pd.Timestamp(str(s)).date()
        except ValueError:
            continue
        if str(comp) in out and d >= today - dt.timedelta(days=30):        # зависшие — до 30 дней, с переносом на 1–2 дня
            out[str(comp)] |= {d, d + dt.timedelta(days=1), d + dt.timedelta(days=2)}
    if not rt.get('uefa_backfill'):                                      # один раз: еврокубки 2025/26 без ЛЧ (она есть в CC0)
        for comp in ('uefa.europa', 'uefa.europa.conf'):
            for d in _days(C.UEFA_BACKFILL_FROM, today):
                if d.weekday() in (1, 2, 3) or (d.weekday() == 5 and d.month in (5, 6)):
                    out[comp].add(d)
    return {k: sorted(v) for k, v in out.items()}


def persist(store: pd.DataFrame, matches: list[fespn.FMatch], mp: Mapper) -> pd.DataFrame:
    rows = []
    for m in matches:
        kind_r = m.result_kind
        if not kind_r:
            continue
        _, country, div, kind = C.COMPETITIONS[m.comp]
        h, a = mp(m.home, m.comp), mp(m.away, m.comp)
        rows.append(dict(id=m.id, comp=m.comp, kind=kind, country=country if country else '', div=div or '',
                         date=m.start, home=m.home, away=m.away, home_fd=h, away_fd=a, hg=m.hg, ag=m.ag, status=kind_r,
                         stage=m.stage, ko=int(m.knockout)))
    if not rows:
        return store
    new = pd.DataFrame(rows)
    out = pd.concat([store[~store.id.astype(str).isin(set(new.id))], new], ignore_index=True)
    out['date'] = pd.to_datetime(out.date)
    return out


def remap(store: pd.DataFrame, mp: Mapper) -> tuple[pd.DataFrame, int]:
    """ТЗ-001 A: уже сохранённые результаты ESPN с неопознанной командой — сопоставить заново (после новых алиасов);
    новые запросы к ESPN не нужны. Матчи турниров, которых больше нет в списке, сопоставляются по стране турнира."""
    if store.empty:
        return store, 0
    n = 0
    for col, name in (('home_fd', 'home'), ('away_fd', 'away')):
        bad = store[col].map(_blank) & store.comp.astype(str).isin(C.COMPETITIONS)
        for i in store.index[bad]:
            r = mp(str(store.at[i, name]), str(store.at[i, 'comp']))
            if r:
                store.at[i, col] = r; n += 1
    return store, n


# ======================================================================== еврокубки: история + ESPN
def uefa_frames(store: pd.DataFrame, matches: list[fespn.FMatch], mp: Mapper) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(матчи для силы стран: date, comp, home_fd, away_fd, hg, ag, status) и (календарь команд: date, team, ko)."""
    h = pd.read_csv(C.UEFA_HIST, parse_dates=['date'])
    h = h[(h.date < '2025-07-01') | (h.comp == 'uefa.cl')]                # остальное 2025/26 — из ESPN
    hist = pd.DataFrame(dict(date=h.date, comp='uefa.' + h.comp.str[5:], home_fd=h.home_fd, away_fd=h.away_fd, hg=h.hg,
                             ag=h.ag, status='FT', ko=h.ko.astype(bool)))
    e = store[store.comp.astype(str).str.startswith('uefa')]
    e = e[~((e.comp == 'uefa.champions') & (e.date < '2026-07-01'))]
    esp = pd.DataFrame(dict(date=e.date, comp=e.comp, home_fd=e.home_fd, away_fd=e.away_fd, hg=e.hg, ag=e.ag,
                            status=e.status, ko=e.ko.astype(float).fillna(0).astype(bool)))
    fit = pd.concat([hist, esp], ignore_index=True)
    up = [dict(date=m.start, home_fd=mp(m.home, m.comp), away_fd=mp(m.away, m.comp), ko=m.knockout)
          for m in matches if m.comp.startswith('uefa') and not m.result_kind]
    cal = pd.concat([fit[['date', 'home_fd', 'away_fd', 'ko']], pd.DataFrame(up, columns=['date', 'home_fd', 'away_fd', 'ko'])])
    sched = pd.concat([cal[['date', 'home_fd', 'ko']].rename(columns={'home_fd': 'team'}),
                       cal[['date', 'away_fd', 'ko']].rename(columns={'away_fd': 'team'})]).dropna(subset=['team'])
    return fit, sched


# ======================================================================== самокалибровка (раз в сутки)
def calibration(ldf: pd.DataFrame) -> dict[str, tuple[float, int]]:
    """k по рынку: P' = σ(k·logit P) по рассчитанным ПОКАЗАННЫМ линиям модели; включается с 30 линий (n = число линий)."""
    L = R.lines(ldf)
    out = {}
    for mk in FL.MARKET_RU:
        sub = L[L.market.astype(str) == mk]
        if mk == 'TOT' and C.T32_FIXED_TOTAL:       # T32: k тотала — только по линиям нового режима
            sub = sub[sub.start_utc.astype(str) >= C.FIXED_TOTAL_FROM]
        out[mk] = FL.calibrate(sub.p_raw.map(J.num).values.astype(float), sub.won.values.astype(bool))
    return out


def daily_k(rt: dict, ld: pd.DataFrame, due: bool) -> tuple[dict, dict, str]:
    """Зафиксированные на сутки k (пересчёт только в суточном цикле 00:30). Возвращает (k, k вчера, отметка)."""
    have = rt.get('kcal')
    if due or not have:
        prev = {k: tuple(v) for k, v in (have or {}).items()}
        new = calibration(ld)
        rt['kcal'] = {k: list(v) for k, v in new.items()}
        rt['kcal_prev'] = {k: list(v) for k, v in prev.items()} if prev else {k: [1.0, 0] for k in new}
        rt['kcal_date'] = _local().date().isoformat()
        return new, {k: tuple(v) for k, v in rt['kcal_prev'].items()}, 'k пересчитан'
    return ({k: tuple(v) for k, v in have.items()}, {k: tuple(v) for k, v in rt.get('kcal_prev', {}).items()},
            f"k от {rt.get('kcal_date', '?')}")


# ======================================================================== A2: Pinnacle из кэша
_PIN: dict[str, list[dict]] = {}
A2_HITS = [0, 0]                                                     # матчей с Pinnacle / всего в прогнозе


def pin_quotes(m) -> dict:
    """A2: кф Pinnacle {'1x2': (П1, X, П2), 'tot25': (Б, М)} из уже скачанного кэша The Odds API (день матча и 7 дней до него,
    новейший первым) — без новых запросов; нет — пустой словарь."""
    import odds as OD
    day = C.wday(m.start + pd.Timedelta(hours=C.TZ_OFFSET_HOURS))
    out: dict = {}
    for back in range(8):
        d = (day - dt.timedelta(days=back)).isoformat()
        if d not in _PIN:
            try:
                _PIN[d] = OD.cached_events('football', d)
            except Exception:
                _PIN[d] = []
        if not _PIN[d]:
            continue
        try:
            ev, flip = OD.find_event(_PIN[d], m.home, m.away, m.start.strftime('%Y-%m-%d %H:%M'))
        except Exception:
            ev = None
        if not ev:
            continue
        q = OD.quotes(ev)
        hn, an = (ev['away_team'], ev['home_team']) if flip else (ev['home_team'], ev['away_team'])
        k = {name: b.get('pinnacle') for (mk, name, _), b in q.items() if mk == 'h2h'}
        if '1x2' not in out and all(k.get(n) for n in (hn, 'Draw', an)):
            out['1x2'] = (k[hn], k['Draw'], k[an])
        t = {name: b.get('pinnacle') for (mk, name, pt), b in q.items()
             if mk == 'totals' and pt is not None and abs(float(pt) - 2.5) < 1e-9}
        if 'tot25' not in out and t.get('Over') and t.get('Under'):
            out['tot25'] = (t['Over'], t['Under'])
        if len(out) == 2:
            break
    return out


def pool(p_model, odds, w) -> np.ndarray:
    """p ∝ p_мод^w0 · p_Pin^w1 (Pinnacle — без маржи по Шину)."""
    import fmarket
    pm = np.clip(np.asarray(p_model, float), 1e-6, 1.0); pp = np.clip(np.asarray(fmarket.shin(list(odds)), float), 1e-6, 1.0)
    z = w[0] * np.log(pm) + w[1] * np.log(pp)
    p = np.exp(z - z.max())
    return p / p.sum()


def _set_p(x: dict, p: float) -> None:
    """P линии из смеси с рынком: p_raw (модель) не трогаем — по нему учится k."""
    x.update(p=round(float(p), 4), k_lo=round(1 / max(p, 1e-6), 2),
             p_low=round(float(1 / (1 + np.exp(-(FL.logit(p) - C.SIGMA[x['market']])))), 4),
             shown=1 if (C.T32_FIXED_TOTAL and x['market'] == 'TOT') else int(FL.visible(p, x['market'])))


def apply_pinnacle(m, row: dict, lns: list[dict], P: np.ndarray) -> int:
    """A2: исход 1X2 и тотал 2,5 по смеси Pinnacle + модель. Возвращает 1, если что-то изменено."""
    if not (C.A2_PIN_1X2 or C.A2_PIN_TOT25):
        return 0
    q = pin_quotes(m)
    hit = 0
    if C.A2_PIN_1X2 and '1x2' in q:
        ph, pd_, pa = (float(v) for v in pool([row['p_h'], row['p_d'], row['p_a']], q["1x2"], C.A2_W_1X2))
        row.update(p_h=round(ph, 4), p_d=round(pd_, 4), p_a=round(pa, 4))
        for x in lns:
            if x['market'] == '1X2':
                _set_p(x, {'H': ph, 'D': pd_, 'A': pa}[x['pick']])
        hit = 1
    if C.A2_PIN_TOT25 and 'tot25' in q:
        po = float(pool([row['p_o25'], 1 - row['p_o25']], q['tot25'], C.A2_W_TOT25)[0])
        row['p_o25'] = round(po, 4)
        for x in lns:
            if x['market'] == 'TOT' and abs(float(x['line']) - 2.5) < 1e-9:
                s = 'O' if po >= 0.5 else 'U'
                if s != x['pick']:                                      # смена стороны: p_raw — модель для той же стороны
                    x.update(pick=s, label=f'Т{"Б" if s == "O" else "М"}2.5', p_raw=round(1 - float(x['p_raw']), 4))
                _set_p(x, max(po, 1 - po))
        hit = 1
    return hit


# ======================================================================== прогноз (модель + A2)
def forecast(matches: list[fespn.FMatch], mp: Mapper, world: model.World, sched: FF.Schedule, train: pd.DataFrame,
             auto: pd.DataFrame, kcal: dict) -> tuple[list[tuple], list[str]]:
    now = _now()
    settled = set(auto[~auto.settled.map(_blank).astype(bool)].espn_id.map(_id))
    season_start = pd.Timestamp(now.year if now.month >= 7 else now.year - 1, 7, 1)
    seasons: dict[str, pd.DataFrame] = {}
    kc = {k: v[0] for k, v in kcal.items()}
    out, skipped = [], []
    for m in matches:
        if m.state != 'pre' or m.id in settled or not (now < m.start <= C.window_end_utc(now)):
            continue
        name, country, div, kind = C.COMPETITIONS[m.comp]
        h, a = mp(m.home, m.comp), mp(m.away, m.comp)
        lam = world.lambdas(m.comp, h, a, m.neutral) if (h and a) else None
        if lam is None:
            skipped.append(f'{m.home} — {m.away} ({name}): ' + ('команда не опознана' if not (h and a) else 'нет оценки силы'))
            continue
        fh, fa = {}, {}
        if kind in LEAGUE_KINDS:
            fh, fa = sched.features(h, m.start), sched.features(a, m.start)
            if div in C.TOPDIV:
                if div not in seasons:
                    seasons[div] = train[(train['div'] == div) & (train.date >= season_start) & train.hg.notna()]
                dd = FF.dead_flags(seasons[div], div, m.start)
                fh['dead'], fa['dead'] = dd.get(h, 0), dd.get(a, 0)
        lh, la = FF.adjust(*lam, fh, fa) if fh else lam
        P = dc.score_matrix(lh, la, C.RHO)
        daypick.DISTS[str(m.id)] = P                                  # для линий ставки дня по правилу C
        lns = FL.build(P, kc)
        i, j = np.indices(P.shape)
        o = m.odds or {}
        row = dict(created=J.now_utc(), espn_id=m.id, comp=m.comp, comp_name=name, kind=kind, stage=m.stage,
                   start_utc=m.start.strftime('%Y-%m-%d %H:%M'), start_local=J.to_local(m.start), home=m.home, away=m.away,
                   home_fd=h, away_fd=a, source='model', lam_h=round(lh, 3), lam_a=round(la, 3),
                   p_h=round(float(P[i > j].sum()), 4), p_d=round(float(np.trace(P)), 4), p_a=round(float(P[i < j].sum()), 4),
                   p_o25=round(float(P[(i + j) > 2.5].sum()), 4), p_btts=round(float(P[(i >= 1) & (j >= 1)].sum()), 4),
                   dk_h=o.get('h', ''), dk_d=o.get('d', ''), dk_a=o.get('a', ''), dk_line=o.get('line', ''),
                   dk_o=o.get('over', ''), dk_u=o.get('under', ''), f_home=FF.describe(fh), f_away=FF.describe(fa))
        for x in lns:
            x.update(created=row['created'], espn_id=m.id, comp=m.comp, comp_name=name, source='model',
                     start_utc=row['start_utc'], start_local=row['start_local'], home=m.home, away=m.away,
                     status='', result='', settled='', pnl_lo='')
        try:
            A2_HITS[0] += apply_pinnacle(m, row, lns, P)
        except Exception as ex:                                          # сбой A2 не ломает прогноз — остаётся модель
            skipped.append(f'A2 {m.home} — {m.away}: {type(ex).__name__}')
        A2_HITS[1] += 1
        out.append((m, row, lns))
    return out, skipped


def save_forecasts(store: J.Store, items: list[tuple], auto: pd.DataFrame, ldf: pd.DataFrame) -> tuple[int, int]:
    """Новые матчи — добавить; записанные и не начавшиеся — обновить (в таблице — последний прогноз перед матчем)."""
    pos = {_id(x): i for i, x in zip(auto.index, auto.espn_id)}
    lpos = {(_id(e), _id(s)): i for i, e, s in zip(ldf.index, ldf.espn_id, ldf.slot)}
    new_rows, new_lines, upd_rows, upd_lines = [], [], {}, {}
    for m, row, lns in items:
        (upd_rows.__setitem__(pos[m.id], row) if m.id in pos else new_rows.append(row))
        for x in lns:
            k = (m.id, str(x['slot']))
            (upd_lines.__setitem__(lpos[k], x) if k in lpos else new_lines.append(x))
    store.append('auto_forecasts', new_rows)
    store.append('auto_lines', new_lines)
    if upd_rows:
        store.update('auto_forecasts', pd.DataFrame.from_dict(upd_rows, orient='index'), J.AUTO_FCST_COLS)
    if upd_lines:
        dfl = pd.DataFrame.from_dict(upd_lines, orient='index')
        store.update('auto_lines', dfl, J.LINE_FCST_COLS)
        store.update('auto_lines', dfl, ['p_low', 'shown'])
    return len(new_rows), len(upd_rows)


# ======================================================================== расчёт
def results_index(store_df: pd.DataFrame) -> dict[str, tuple[str, float, float]]:
    return {str(r.id): (str(r.status), r.hg, r.ag) for r in store_df.itertuples(index=False)}


def settle(store: J.Store, res: dict) -> tuple[int, int]:
    today = dt.date.today().isoformat()
    af = store.read('auto_forecasts')
    upd = []
    for i, r in af[af.settled.map(_blank).astype(bool)].iterrows():
        x = res.get(_id(r.espn_id))
        if x is None:
            continue
        st, hg, ag = x
        if st == 'FT':
            out = 0 if hg > ag else 1 if hg == ag else 2
        elif st == 'ET':
            out = 1
        else:
            out = None
        p = [J.num(r.p_h), J.num(r.p_d), J.num(r.p_a)]
        ll = round(-float(np.log(max(p[out], 1e-6))), 4) if out is not None else ''
        af.loc[i, J.AUTO_RESULT_COLS] = [st, hg if st == 'FT' else '', ag if st == 'FT' else '', today, ll]
        upd.append(i)
    # охват: прогнозы турниров, которые больше не отслеживаются (старые строки «рынок» Бельгии/Шотландии, РПЛ после 10.11.2026),
    # через 48 ч без итога закрываются статусом OFF — в статистику не входят (её считают только FT/ET модели), «зависшими» не висят
    old = pd.Timestamp.now(tz='UTC').tz_localize(None) - pd.Timedelta(hours=48)
    for i, r in af[af.settled.map(_blank).astype(bool)].iterrows():
        if i not in upd and str(r.comp) not in C.COMPETITIONS and pd.to_datetime(r.start_utc, errors='coerce') < old:
            af.loc[i, J.AUTO_RESULT_COLS] = ['OFF', '', '', today, '']
            upd.append(i)
    store.update('auto_forecasts', af.loc[upd], J.AUTO_RESULT_COLS)
    ld = store.read('auto_lines')
    lu = []
    for i, r in ld[ld.settled.map(_blank).astype(bool)].iterrows():
        x = res.get(_id(r.espn_id))
        if x is None:
            continue
        st, hg, ag = x
        v = FL.settle(str(r.market), str(r.pick), r.line, st, int(hg) if st == 'FT' else None, int(ag) if st == 'FT' else None)
        k = J.num(r.z_lo) if not _blank(r.z_lo) else J.num(r.k_lo)          # старые записи — нижний кф зоны, новые — 1/P
        ld.loc[i, J.LINE_RESULT_COLS] = [st, v, today, round({'W': k - 1, 'L': -1.0, 'P': 0.0}[v], 3) if k == k else '']
        lu.append(i)
    store.update('auto_lines', ld.loc[lu], J.LINE_RESULT_COLS)
    return len(upd), len(lu)


def attach_odds(store: J.Store, base: pd.DataFrame) -> int:
    """Закрывающие кф football-data к рассчитанным матчам — только как данные для исследований (в прогнозе и отчёте не участвуют)."""
    af = store.read('auto_forecasts')
    todo = af[af.status.astype(str).isin(['FT', 'ET']) & af.avg_h.map(_blank).astype(bool) & ~af.home_fd.map(_blank).astype(bool)]
    if todo.empty:
        return 0
    idx = {(h, a): g for (h, a), g in base.groupby(['home', 'away'])}
    upd = []
    for i, r in todo.iterrows():
        g = idx.get((str(r.home_fd), str(r.away_fd)))
        if g is None:
            continue
        d = pd.Timestamp(str(r.start_utc)).normalize()
        g = g[(g.date - d).abs().dt.days <= 1]
        if g.empty:
            continue
        x = g.iloc[-1]
        for c in J.AUTO_ODDS_COLS:
            af.at[i, c] = x.get(c, '') if pd.notna(x.get(c, np.nan)) else ''
        upd.append(i)
    store.update('auto_forecasts', af.loc[upd], J.AUTO_ODDS_COLS)
    return len(upd)


# ======================================================================== составы (этап 2)
def collect_lineups(store_df: pd.DataFrame) -> str:
    done = set()
    if C.LINEUPS.exists():
        old = pd.read_csv(C.LINEUPS, dtype={'event': str})
        done = set(old.event)
    else:
        old = None
    recent = store_df[(store_df.status == 'FT') & (store_df.date >= _now() - pd.Timedelta(days=3))
                      & (store_df.kind.astype(str) == 'league')]
    todo = [(c, str(e)) for c, e in zip(recent.comp, recent.id) if str(e) not in done][:150]
    if not todo:
        return 'составы: новых нет'
    from concurrent.futures import ThreadPoolExecutor
    rows, err = [], 0
    with ThreadPoolExecutor(8) as ex:
        for r in ex.map(lambda t: _safe_lineups(*t), todo):
            if r is None:
                err += 1
            else:
                rows += r
    if rows:
        new = pd.DataFrame(rows)
        new['date'] = new.event.map(dict(zip(store_df.id.astype(str), store_df.date.dt.strftime('%Y-%m-%d'))))
        out = new if old is None else pd.concat([old, new], ignore_index=True)
        out.to_csv(C.LINEUPS, index=False, compression='gzip')
    got = len({r['event'] for r in rows})
    return f'составы: матчей {got} из {len(todo)}' + (f', ошибок {err}' if err else '')


def _safe_lineups(comp: str, ev: str):
    try:
        js = fespn.summary(comp, ev)
        sot = fespn.shots_on_target(js)
        if sot is not None:
            _SOT[ev] = sot
        return fespn.lineups(comp, ev, js)
    except Exception:
        return None


_SOT: dict[str, tuple[float, float]] = {}


def verify_shots_sample() -> str:
    """T30, проверка один раз: удары в створ ESPN по матчам 03.10–02.11.2026 восьми лиг (они же есть в football-data) →
    data/shots_check.csv.gz (event, date, comp, home, away, hg, ag, hst, ast) для сверки с HST/AST football-data."""
    f = C.DATA / 'shots_check.csv.gz'
    if f.exists():
        return ''
    comps = ['eng.1', 'esp.1', 'ger.1', 'ita.1', 'fra.1', 'ned.1', 'por.1', 'tur.1']
    days = [dt.date(2026, 10, 3) + dt.timedelta(days=i) for i in range(31)]
    ms, _ = fespn.fetch({c: days for c in comps})
    ms = [m for m in ms if m.result_kind == 'FT'][:400]
    from concurrent.futures import ThreadPoolExecutor
    def one(m):
        try:
            return m, fespn.shots_on_target(fespn.summary(m.comp, m.id))
        except Exception:
            return m, None
    rows = []
    with ThreadPoolExecutor(8) as ex:
        for m, sot in ex.map(one, ms):
            if sot is not None:
                rows.append(dict(event=m.id, date=m.start.strftime('%Y-%m-%d'), comp=m.comp, home=m.home, away=m.away,
                                 hg=m.hg, ag=m.ag, hst=sot[0], ast=sot[1]))
    pd.DataFrame(rows).to_csv(f, index=False, compression='gzip')
    return f'удары ESPN (проверка T30): {len(rows)} из {len(ms)} матчей'


def collect_shots(store_df: pd.DataFrame) -> str:
    """T30 (футбол): удары в створ ESPN — к матчам этого запуска (из summary составов) + догрузка истории сезона
    (лиги, FT, с 01.08.2026), до 300 матчей за запуск. Файл data/shots.csv.gz: event, date, comp, hst, ast."""
    old = pd.read_csv(C.SHOTS, dtype={'event': str}) if C.SHOTS.exists() else pd.DataFrame(columns=['event', 'date', 'comp', 'hst', 'ast'])
    have = set(old.event.astype(str)) | set(_SOT)
    lg = store_df[(store_df.status == 'FT') & (store_df.kind.astype(str) == 'league') & (store_df.date >= pd.Timestamp('2026-08-01'))]
    todo = [(c, str(e)) for c, e in zip(lg.comp, lg.id) if str(e) not in have][:300]
    if todo:
        from concurrent.futures import ThreadPoolExecutor
        def one(t):
            try:
                return t[1], fespn.shots_on_target(fespn.summary(*t))
            except Exception:
                return t[1], None
        with ThreadPoolExecutor(8) as ex:
            for ev, sot in ex.map(one, todo):
                if sot is not None:
                    _SOT[ev] = sot
    if not _SOT:
        return 'удары ESPN: новых нет'
    meta = store_df.assign(id=store_df.id.astype(str)).set_index('id')
    new = pd.DataFrame([dict(event=e, date=meta.date.get(e, pd.NaT), comp=meta.comp.get(e, ''), hst=h, ast=a)
                        for e, (h, a) in _SOT.items() if e not in set(old.event.astype(str))])
    out = pd.concat([old, new], ignore_index=True) if len(new) else old
    out['date'] = pd.to_datetime(out.date).dt.strftime('%Y-%m-%d')
    out.to_csv(C.SHOTS, index=False, compression='gzip')
    n = len(new); _SOT.clear()
    return f'удары ESPN: +{n} (всего {len(out)}, осталось догрузить {max(len(lg) - len(out), 0)})'


# ======================================================================== Telegram: список
def visible(x: dict, off: dict) -> bool:
    return int(J.num(x.get('shown'), 0) or 0) == 1 and x['market'] not in off


def match_block(row: dict, lns: list[dict], today: str, off: dict, star: tuple | None) -> str:
    """Матч: время · команды; исход (всегда); линии с кф ≥ 1,25 от самой уверенной:
    🟢 жирным — высокая, 🟡 обычным — средняя, ⚪ курсивом — низкая уверенность; ⭐ — ставка дня."""
    e = notify.esc
    when = row['start_local'][6:] if row['start_local'][:5] == today else row['start_local']
    p3 = [row['p_h'], row['p_d'], row['p_a']]
    k = int(np.argmax(p3))
    out = [f"🕒 <b>{e(when)}</b> · <b>{e(row['home'])}</b> — <b>{e(row['away'])}</b>",
           f"🎯 Исход: <b>{['П1', 'X', 'П2'][k]} — {p3[k] * 100:.0f}%</b> · {notify.fair(p3[k])}"]
    tier = lambda x: FL.tier(float(x.get('p_low') or x['p']))
    P = daypick.DISTS.get(str(row['espn_id']))
    syn = {}
    for x in lns:                                                     # 👥 синтез: 🔥 модель + капер за линию, ⚔️ конфликт
        try:
            syn[x['slot']] = tips.mark(str(row['espn_id']), P, x['market'], x['pick'], x['line']) if P is not None else ''
        except Exception:
            syn[x['slot']] = ''
    for x in sorted([x for x in lns if visible(x, off)], key=lambda x: (syn.get(x['slot']) != '🔥', -tier(x), -float(x['p']), x['slot'])):
        t = e(f"{x['label']} — {float(x['p']) * 100:.0f}% · {notify.fair(x['p'])}")
        s = {3: f'🟢 <b>{t}</b>', 2: f'🟡 {t}', 1: f'⚪ <i>{t}</i>'}[FL.tier(float(x.get('p_low') or x['p']))]
        if syn.get(x['slot']) == '🔥':
            s = f'🔥 <b>{t}</b>'
        elif syn.get(x['slot']) == '⚔️':
            s += ' ⚔️'
        if star and (str(row['espn_id']), int(x['slot'])) == star[:2]:
            s = '⭐ ' + s
        out.append(s)
    try:
        tl = tips.match_line(str(row['espn_id']))
        if tl:
            out.append(e(tl))
    except Exception:
        pass
    if star and str(row['espn_id']) == star[0] and not star[1]:          # ставка дня вне линий списка
        out.append(f'⭐ <b>{e(star[2])}</b> — {star[3] * 100:.0f}% · {notify.fair(star[3])}')
    return '\n'.join(out)


def list_blocks(items: list[tuple], off: dict, star: tuple | None) -> list[str]:
    today = _local().strftime('%d.%m')
    groups: dict[str, list] = {}
    for m, row, lns in sorted(items, key=lambda t: t[1]['start_utc']):
        groups.setdefault(row['comp_name'], []).append(match_block(row, lns, today, off, star))
    if not groups:
        return []
    n = sum(len(v) for v in groups.values())
    blocks = [f"⚽ <b>Прогноз до {(C.wday(_local()) + dt.timedelta(days=1)):%d.%m} 08:00</b> · {_local().strftime('%H:%M')} · {n} {notify.plural(n, 'матч', 'матча', 'матчей')}"
              + ('\n⭐ Ставка дня — в закреплённом сообщении' if star else '')]
    for comp, ms in groups.items():
        blocks += notify.folded(f'🏆 <b>{notify.esc(comp)}</b>', ms)
    return blocks


def publish_list(rt: dict, store: J.Store | None, items: list[tuple], off: dict, dry: bool = False,
                 extra: list | None = None) -> str:
    dp = rt.get('day_pick') or {}
    star = ((str(dp['espn_id']), int(dp.get('slot') or 0), str(dp.get('label', '')), float(dp.get('p', 0)))
            if dp.get('espn_id') and dp.get('date') == C.wday(_local()).isoformat() else None)
    blocks = list_blocks(items, off, star)
    ids = sorted(m.id for m, _, _ in items)
    if dry:
        print('\n\n'.join(blocks))
        return 'dry'
    show = bool(getattr(C, 'PUBLISH_LISTS', True))             # 11.11.2026: списки не публикуются — всё в фоне
    cache = list(blocks) if show else ['📋 Списки прогнозов не публикуются (решение 11.11.2026): анализ идёт в фоне. Смотрите ⭐ Ставка дня и 📊 Аналитика.']
    while cache and len(json.dumps(cache, ensure_ascii=False)) > 45000:   # ячейка Google Таблицы — до 50 000 символов
        cache.pop()
    cur_dp = rt.get('day_pick') or {}
    dpt = ('\n\n'.join([cur_dp.get('text', '')] + [x.get('text', '') for x in (cur_dp.get('extras') or []) if x.get('text')])
           if cur_dp.get('date') == C.wday(_local()).isoformat() else '')
    store.write_all('tg_cache', pd.DataFrame([dict(key='list', text=json.dumps(cache, ensure_ascii=False), updated=J.now_utc()),
                                              dict(key='daypick', text=dpt, updated=J.now_utc()),
                                              dict(key='daypick_accepted', text='yes',   # кнопок ✅/🔄 больше нет (11.11)
                                                   updated=J.now_utc()),
                                              dict(key='stats_menu', text=json.dumps(STATS_MENU, ensure_ascii=False),
                                                   updated=J.now_utc())]))
    removed = notify.delete([tuple(x) for x in rt.get('list_messages', [])] + list(extra or []))
    sent = []
    if items and show:
        silent = set(ids) <= set(rt.get('list_match_ids', []))
        sent = notify.send_blocks(blocks, silent=silent)
    rt['list_messages'] = [list(x) for x in sent]
    rt['list_match_ids'] = ids
    return (f'список: удалено {removed}, отправлено {len(sent)} сообщ., матчей {len(ids)}'
            + ('' if show else ' (в фоне — не публикуется)'))


def extra_messages(store: J.Store) -> list[tuple[int, int]]:
    df = store.read('tg_messages')
    out = [(int(J.num(c)), int(J.num(m))) for c, m in zip(df.chat_id, df.message_id)
           if not np.isnan(J.num(c)) and not np.isnan(J.num(m))]
    if len(df):
        store.write_all('tg_messages', pd.DataFrame(columns=J.SHEETS['tg_messages']))
    return out


def summary(af: pd.DataFrame, ld: pd.DataFrame, kcal: dict, off: dict) -> pd.DataFrame:
    M, L = R.matches(af), R.lines(ld)
    rows = [('обновлено (UTC)', J.now_utc()), ('прогнозов модели', int((af.source.astype(str) == 'model').sum())),
            ('доиграно (модель)', len(M)), ('исход угадан', f'{int(M.hit.sum())}/{len(M)}' if len(M) else '—'),
            ('показанных линий рассчитано', len(L)), ('линий верных', f'{int(L.won.sum())}/{len(L)}' if len(L) else '—'),
            ('k по рынкам', '; '.join(f'{m} {v[0]} (n={v[1]})' for m, v in kcal.items())),
            ('⛔ выключены', ', '.join(off) or 'нет')]
    return pd.DataFrame(rows, columns=['показатель', 'значение'])


# ======================================================================== файлы и напоминание
def _season_links() -> list[tuple[str, str, str]]:
    """[(ключ, имя файла, прямая ссылка)] на текущий сезон (с июля — новый)."""
    loc = _local()
    y = loc.year if loc.month >= 7 else loc.year - 1
    sub = dict(y=y, y1=y + 1, yy=f'{y % 100:02d}', yy1=f'{(y + 1) % 100:02d}')
    return [(k, n.format(**sub), u.format(**sub)) for k, (n, u) in C.FD_LINKS.items()]


def _need_text() -> str:
    return '\n'.join(f'• {n} — {u}' for _, n, u in _season_links()) + f'\nЕсли ссылка не открывается — страница со списком: {C.FD_PAGE}'


STATS_MENU = [[{'text': '📊 Всё время', 'data': 'r:all'}, {'text': '📅 По месяцу', 'data': 'r:month'},
               {'text': '🏆 По турниру', 'data': 'r:event'}],
              [{'text': '🔎 Аудит', 'data': 'r:audit'}, {'text': '🧠 Самообучение', 'data': 'r:learn'}]]
SHOTS: list[tuple] = []          # скриншоты 1xBet из входящих этого запуска (id, имя, байты, описание)


def take_inbox(rt: dict) -> tuple[bool, list[str]]:
    import inbox
    import shots
    try:
        files, note = inbox.fetch()
    except Exception as ex:
        return False, [f'папка входящих недоступна: {ex}']
    log, got, msgs = ([note] if note else []), False, []
    for fid, name, raw in files:
        if shots.is_image(raw) or str(name).startswith('shot_'):
            SHOTS.append((fid, name, raw, inbox.DESC.get(fid, '')))   # скриншот 1xBet — разбор после прогноза (нужны матчи)
            continue
        try:
            kind, text = fdata.save_file(raw)
            rt.setdefault('files', {})[kind] = _local().date().isoformat()
            log.append(f'файл принят: {text}')
            msgs.append(f'✅ Файл <b>{notify.esc(name)}</b> принят: {notify.esc(text)}')
            got = True
        except fdata.WrongFile as ex:
            log.append(f'файл {name} не принят: {ex}')
            msgs.append(f'❌ Файл <b>{notify.esc(name)}</b> не принят: {notify.esc(str(ex))}.\nОтправьте корректный:\n'
                        + notify.esc(_need_text()))
        except Exception as ex:
            log.append(f'файл {name} не принят: {ex.__class__.__name__}: {ex}')
            msgs.append(f'❌ Файл <b>{notify.esc(name)}</b> не прочитан ({notify.esc(ex.__class__.__name__)}).\n'
                        'Отправьте корректный:\n' + notify.esc(_need_text()))
        inbox.mark_done(fid, name)
    if msgs:
        notify.send_blocks(msgs)
    return got, log


def reminder_due(rt: dict) -> bool:
    """С понедельника — сразу после суточного отчёта (11:30), раз в день, пока не придёт файл недели."""
    loc = _local()
    if not C.FD_FILES_REQUIRED:                # T30: результаты и удары в створ — из ESPN, файл football-data не нужен
        return False
    if loc.hour < 11 or rt.get('last_daily') != C.wday(loc).isoformat():
        return False
    monday = (loc.date() - dt.timedelta(days=loc.weekday())).isoformat()
    have = rt.get('files', {})
    missing = [k for k in C.FD_LINKS if (have.get(k) or '2000-01-01') < monday]
    return bool(missing) and rt.get('last_reminder') != loc.date().isoformat()


def reminder_text(rt: dict) -> str:
    loc = _local()
    monday = (loc.date() - dt.timedelta(days=loc.weekday())).isoformat()
    have = rt.get('files', {})
    rows = []
    for k, n, u in _season_links():
        ok = (have.get(k) or '2000-01-01') >= monday
        rows.append(f'✅ {notify.esc(n)} — получен' if ok else f'⬜ {notify.esc(n)} — {u}')
    return ('⏰ <b>Пора прислать файл football-data</b> (один — все лиги сезона; Россия больше не нужна)\n' + '\n'.join(rows) +
            f'\nЕсли ссылка не открывается — страница со списком: {C.FD_PAGE}\n'
            'Скачайте и отправьте сюда (название не важно — бот проверит содержимое). '
            'Напоминание повторится завтра, пока не придёт файл.')


# ======================================================================== цикл
def run(dry: bool = False) -> str:
    t0 = time.time()
    store, rt = J.Store(), runtime.load()
    if 'files' not in rt:                                              # переход со старого учёта (одна дата на оба файла)
        rt['files'] = {k: rt.get('last_files', '') for k in C.FD_LINKS}
    log = []
    got, ilog = (False, []) if dry else take_inbox(rt)
    log += ilog
    base = fdata.load()
    mp = Mapper(base)
    st = load_store()
    af, ld = store.read('auto_forecasts'), store.read('auto_lines')
    matches, errs = fespn.fetch(plan(base, st, af, rt))
    st = persist(st, matches, mp)
    st, n_remap = remap(st, mp)
    if n_remap:
        log.append(f'досопоставлено сохранённых результатов: {n_remap}')
    if not dry:
        save_store(st)
        rt['uefa_backfill'] = rt.get('uefa_backfill') or J.now_utc()
    log.append(f'ESPN: матчей {len(matches)}, ошибок запросов {len(errs)}; в базе результатов {len(st)}')
    train = fdata.training_set(st)
    ufit, usched = uefa_frames(st, matches, mp)
    world = model.build(train, ufit, _now())
    log.append(f"модель: стран {len(world.sup)}, команд {len(world.team_country)}, еврокубков в оценке силы стран {world.uefa.get('n', 0)}")
    sched = FF.Schedule(train[['date', 'home', 'away']], usched)

    loc = _local(); today = C.wday(loc).isoformat()                  # дата суток системы (08:00 → 08:00)
    daily_due = C.DAILY_HOUR[0] <= loc.hour < C.DAILY_HOUR[1] and rt.get('last_daily') != today
    if not dry:                                                        # итоги прошедших матчей — до пересчёта k
        ns, nl = settle(store, results_index(st))
        nd = daypick.settle(store)
        try:                                                       # 🔁 заменённые ставки дня — тоже с итогом
            daypick.settle(store, "dp_changes")
        except Exception as ex_:
            print(f"dp_changes: {ex_}")
        log.append(f'рассчитано матчей {ns}, линий {nl}, ставок дня {nd}')
        af, ld = store.read('auto_forecasts'), store.read('auto_lines')
    kcal, kprev, knote = daily_k(rt, ld, daily_due and not dry)
    off, bmsg = R.breaker(ld, rt.get('breaker', {}))
    items, skipped = forecast(matches, mp, world, sched, train, af, kcal)
    log.append(f'A2 Pinnacle: {A2_HITS[0]} из {A2_HITS[1]} матчей')
    if not dry:                                                        # 👥 каперы: итоги прошлых, новые посты (ТЗ-005)
        try:
            nt = tips.settle(results_index(st))
            tips.collect(items, log)
            log[-1] += f'; рассчитано {nt}'
        except Exception as ex:
            log.append(f'👥 каперы: сбой {ex.__class__.__name__}: {ex}')
        try:                                                           # 👥 все матчи каперов: история (разово), новые, итоги
            tipswide.backfill(rt, log)
            tipswide.collect(log)
            tipswide.settle(log)
        except Exception as ex:
            log.append(f'👥 все матчи: сбой {ex.__class__.__name__}: {ex}')
        try:                                                           # 👥 книга прогнозов: все виды спорта, отметки ✅/❌
            tipsbook.run(rt, log)
            tipswide.settle(log)                                       # купоны с картинок — итог сразу
        except Exception as ex:
            log.append(f'👥 книга: сбой {ex.__class__.__name__}: {ex}')
    if dry:
        print('\n'.join(log)); publish_list(rt, None, items, off, dry=True); print('\n'.join(skipped))
        return f'dry {time.time() - t0:.0f} с'
    rt['breaker'] = off
    off = {**off, **{mk: 'A5' for mk in C.MARKETS_OFF}}               # A5: рынки, выключенные автопилотом (в состояние ⛔ не пишутся)
    alarms = bmsg + R.outcome_alarm(af, rt) + R.daypick_alarms(store.read('day_pick'), rt)
    if alarms:
        notify.send_blocks(alarms)
    nn, nu = save_forecasts(store, items, af, ld)
    no = attach_odds(store, base)
    af, ld = store.read('auto_forecasts'), store.read('auto_lines')
    dpk = store.read('day_pick')
    rday = os.getenv('RESEND_DAY', '').strip()                         # повтор отчёта за выбранные сутки (запуск с RESEND_DAY=ГГГГ-ММ-ДД)
    wk0 = os.getenv('RESEND_WEEK', '').strip()
    resend = not dry and (not rt.get('resend_1111b') or bool(rday and rt.get('resend_done') != rday)
                          or bool(wk0 and rt.get('resend_week_done') != wk0))
    only_week = resend and bool(wk0) and not (rday and rt.get('resend_done') != rday) and not daily_due
    if daily_due or resend:
        notes = []
        if mp.missed:
            notes.append(f'не опознаны: {len(mp.missed)} {notify.plural(len(mp.missed), "команда", "команды", "команд")} (лог)')
        if mp.out_base:
            notes.append(f'вне базы: {mp.out_base} {notify.plural(mp.out_base, "команда", "команды", "команд")} (штатно)')
        last = base.date.max()
        if (loc.normalize() - last).days > 9:
            notes.append(f'football-data по {last:%d.%m}; дальше — результаты и удары ESPN') if not C.FD_FILES_REQUIRED \
                else notes.append(f'файлы football-data по {last:%d.%m} — ждём файлы недели')
        yday = C.wday(loc) - dt.timedelta(days=1)                     # прошлые сутки 08:00 → 08:00
        if rday and resend and not daily_due:
            yday = dt.date.fromisoformat(rday)
        import coverage
        try:                                                      # дорасчитано / ждут результата (сбой не ломает отчёт)
            late = coverage.late_ids(R.matches(af), yday, rt)
            cov = coverage.report_rows(R.matches(af), R.lines(ld), af, yday, rt, R.fmt)
        except Exception as ex:
            late, cov = set(), [f'⚠️ контроль охвата: {type(ex).__name__}']

        try:
            import dpcore as DPC
            R.EXTRA_OPS = DPC.changes_rows(store, dpk)
        except Exception as ex:
            log.append(f'🔁 смены: сбой {ex.__class__.__name__}')
        rep_tag = ' · повтор' if resend and not daily_due else ''      # 14.11: три отдельных сообщения — отчёт, каперы, ставки дня
        import selflearn as SL                                    # 🧠 14.11: обучение по каждому суточному отчёту
        try:                                                      # 📈 цена закрытия (CLV) рассчитанных ставок дня — до обучения
            import clv as CLV
            log.append(CLV.update(store, 'football', last=rt.get('dp_last')))
            dpk = store.read('day_pick')
        except Exception as ex:
            log.append(f'📈 CLV football: сбой {ex.__class__.__name__}: {ex}')
        slP, slE, slR = SL.cycle('football', 'Футбол', store, dpk, store.read('dp_changes'), loc.date(), log)
        if not only_week:
            notify.send_blocks(R.build(af, ld, dpk, f'📈 Футбол · отчёт · {yday:%d.%m}' + rep_tag, kcal=kcal, kprev=kprev, off=off,
                                       notes=notes, daily_day=yday, extra=cov, late=late, split=True))
            try:
                notify.send_blocks([caprep.report('football', yday, yday, f'{yday:%d.%m}' + rep_tag), tipsbook.card(),
                                    SL.safe(SL.cap_block, 'football', slR)])
            except Exception as ex:
                log.append(f'👥 отчёт каперов: сбой {type(ex).__name__}')
            notify.send_blocks(R.dp_report(dpk, f'⚽ Футбол · ставки дня · {yday:%d.%m}' + rep_tag, yday)
                               + [SL.safe(SL.day_block, 'football', slP, slE, yday, slR)])
        if daily_due:                                             # повтор не сдвигает плановый отчёт (14.11)
            rt['last_daily'] = today
        rt['resend_1111b'] = 1
        if rday:
            rt['resend_done'] = rday
        log.append('суточный отчёт отправлен' if not only_week else 'суточный — пропущен (только недельный)')
        wk = os.getenv('RESEND_WEEK', '').strip()                 # тест / повтор недельного: неделя, оканчивающаяся этой датой
        if (daily_due and C.wday(loc).weekday() == 0) or (wk and rt.get('resend_week_done') != wk):
            if wk and rt.get('resend_week_done') != wk:
                yday = dt.date.fromisoformat(wk); rt['resend_week_done'] = wk
            d0 = yday - dt.timedelta(days=6)
            a_, l_, d_ = R.week(af, ld, dpk, d0, yday)
            notify.send_blocks(R.build(a_, l_, d_, f'📅 Футбол · неделя {d0:%d.%m}–{yday:%d.%m}', kcal=kcal, off=off,
                                       period='за неделю', split=True) + R.week_lines(ld, d0, yday))
            try:
                notify.send_blocks([caprep.report('football', d0, yday, f'неделя {d0:%d.%m}–{yday:%d.%m}')])
            except Exception as ex:
                log.append(f'👥 недельный отчёт каперов: сбой {type(ex).__name__}')
            notify.send_blocks(R.dp_report(d_, f'⚽ Футбол · ставки дня · неделя {d0:%d.%m}–{yday:%d.%m}', yday, d0)
                               + [SL.safe(SL.week_block, 'football', slP, slE, d0, yday)])
            log.append('недельный отчёт отправлен')
        archive_run(store, rt, af, ld, kcal, off, yday, log)
        if reminder_due(rt):                                      # запрос файла — сразу после отчётов
            notify.send_blocks([reminder_text(rt)])
            rt['last_reminder'] = today
            log.append('напоминание о файлах отправлено')
        try:
            log.append(news.collect())
        except Exception as ex:
            log.append(f'новости: сбой {ex.__class__.__name__}')
    if not rt.get('arch_v1') and not dry:                          # 🗂 ТЗ-006: первый прогон — архив за прошлые сутки и недели
        archive_run(store, rt, af, ld, kcal, off, C.wday(loc) - dt.timedelta(days=1), log)
    try:
        import dpcore
        import inbox
        import odds as OD
        import shots
        actions = dpcore.read_actions(store)
        offers_s, replies, slog = shots.process(SHOTS, items, OD.cached_events('football', today))
        log += slog + ([f'кнопки: {", ".join(actions)}'] if actions else [])
        if replies:
            notify.send_blocks(replies)
        for fid, name, _, _ in SHOTS:
            inbox.mark_done(fid, name)
        log.append(daypick.process(rt, store, items, off, daily_done=rt.get('last_daily') == today,
                                   shots=offers_s, actions=actions))
    except Exception as ex:
        log.append(f'ставка дня: сбой {ex.__class__.__name__}: {ex}')
        log.append(traceback.format_exc()[-800:])
    log.append(publish_list(rt, store, items, off, extra=extra_messages(store)))
    try:
        log.append(collect_lineups(st))
    except Exception as ex:
        log.append(f'составы: сбой {ex.__class__.__name__}')
    try:
        log.append(verify_shots_sample())
    except Exception as ex:
        log.append(f'удары ESPN (проверка): сбой {ex.__class__.__name__}: {ex}')
    try:
        log.append(collect_shots(st))
    except Exception as ex:
        log.append(f'удары ESPN: сбой {ex.__class__.__name__}: {ex}')
    if reminder_due(rt):
        notify.send_blocks([reminder_text(rt)])
        rt['last_reminder'] = today
        log.append('напоминание о файлах отправлено')
    store.write_all('summary', summary(af, ld, kcal, off))
    try:                                                               # 🧠 журнал самообучения (лист learning)
        log += learn_cycle(store, rt, af, ld, kcal, knote, daily_due and not dry)
    except Exception as ex:
        log.append(f'журнал самообучения: сбой {ex.__class__.__name__}: {ex}')
    reps = R.all_reports(af, ld, store.read('day_pick'), kcal, off)
    try:
        reps = pd.concat([reps, pd.DataFrame([dict(key='learn', kind='learn', title='🧠 Самообучение',
                                                   text=learning.report(store, rt, 'Самообучение · футбол'), updated=J.now_utc()),
                                              dict(key='audit', kind='audit', title='🔎 Аудит',
                                                   text=learning.audit_report(store, week_table(store, af, ld))
                                                   + '\n' + tipsbook.card(),
                                                   updated=J.now_utc())])],
                         ignore_index=True)
    except Exception as ex:
        log.append(f'отчёт самообучения: сбой {ex.__class__.__name__}: {ex}')
    store.write_all('reports', reps)
    try:                                                               # 🔒 заглушка последним сообщением (превью скрыто)
        log.append(f'🔒 заглушка: {notify.cover(C.DATA / "cover.json")}')
    except Exception as ex:
        log.append(f'🔒 заглушка: сбой {ex.__class__.__name__}')
    runtime.save(rt)
    log += [f'{knote}; ⛔ {", ".join(off) or "нет"}; прогнозов: новых {nn}, обновлено {nu}; кф football-data (данные) {no}',
            f'пропущено {len(skipped)}; ошибка сопоставления {len(mp.missed)}, вне базы {mp.out_base}; '
            f'время {time.time() - t0:.0f} с']
    return '\n'.join(log + skipped[:15] + [f'не опознано: {k} ({v})' for k, v in list(mp.missed.items())[:15]] + errs[:10])


# ======================================================================== 🧠 самообучение
MODEL_VERSION = 2
VERSION_NOTES = {2: ('V2: Dixon–Coles, разница сил — голы 50% + удары в створ 50%',
                     'исход 07.2023–01.2026: 49,45% (было 49,05%); тоталы, ИТ, форы откалиброваны ≈ P (74–77%)')}
HISTORY = [
    ('2026-11-06 12:00', 'версия', 'V1: Dixon–Coles по странам + факторы', 'исход 49,05%; сила стран по еврокубкам'),
    ('2026-11-08 08:00', 'внедрено', 'Пакет №1: удары в створ в разнице сил, 17 турниров',
     'исход 49,05% → 49,45%; ⭐ ставка дня (бэктест 78,4%), отчёты, ступени, ⛔/🚨'),
    ('2026-11-08 14:35', 'внедрено', 'Пакет №3: ставка дня по правилу C',
     'кф 1xBet ≥ 1,35, справедливая P (Pinnacle / 1xBet без маржи), уверенность в выгодности; скриншоты; кнопки ✅/🔄'),
    ('2026-11-11 03:00', 'внедрено', 'ТЗ-005: 👥 каперы — 5 Telegram-каналов и nb-bet.com',
     'сбор раз в 2 ч, ставки на наши матчи, P модели; точность и z против модели; спор; вето надёжного (n≥30, z≥1,64)'),    ('2026-11-11 21:40', 'внедрено', 'T32: тотал — одна линия 2,5',
     'больше или меньше, всегда показан; история: модель 57,3%, с Pinnacle 59,8%; строка «Тотал фикс.» в отчёте'),

]


def week_table(store: J.Store, af: pd.DataFrame, ld: pd.DataFrame):
    """Таблица недели (как недельный отчёт понедельника) для карточки аудита: (d0, d1) → HTML."""
    dp = store.read('day_pick')
    return lambda d0, d1: R.build(*R.week(af, ld, dp, d0, d1), f'📊 Футбол · неделя {d0:%d.%m}–{d1:%d.%m}',
                                  period='за неделю')[0]


def learn_cycle(store: J.Store, rt: dict, af: pd.DataFrame, ld: pd.DataFrame, kcal: dict, knote: str, daily: bool) -> list[str]:
    out = []
    n = learning.backfill(store, HISTORY)
    if n:
        out.append(f'журнал: начальная история {n}')
    title, text = VERSION_NOTES[MODEL_VERSION]
    v = learning.version(store, rt, MODEL_VERSION, title, text)
    if v:
        out.append(v)
    if daily and knote == 'k пересчитан':
        learning.add(store, 'калибровка', 'Самокалибровка рынков', ', '.join(f'{m} {v[0]} (n={v[1]})' for m, v in kcal.items()))
    if daily:
        out += learning.steps(store, rt, R, af, ld, store.read('day_pick'), 'футбол')
    sent = learning.to_telegram(store, week_table(store, af, ld))
    if sent:
        out.append(f'журнал: в Telegram {sent}')
    return out



def archive_run(store, rt: dict, af, ld, kcal, off, yday: dt.date, log: list[str]) -> None:
    """🗂 ТЗ-006: архив отчётов для меню бота (сутки, неделя, месяц, год). Первый раз — все сутки и недели с 08.11."""
    try:
        import archive as A
        if not rt.get('arch_v1'):
            a_days, a_weeks = A.backfill_plan(yday + dt.timedelta(days=1), dt.date(2026, 11, 8))
        else:
            a_days, a_weeks = [yday], [(A.monday(yday), yday)]
        n_ = A.put(store, A.std_items('fb', 'football', 'Футбол', '⚽', R, store, af, ld, store.read('day_pick'), kcal, off,
                                      a_days, a_weeks, yday, learning.card, log), notify._split_html)
        rt['arch_v1'] = 1
        log.append(f'🗂 архив: {n_} отчётов')
    except Exception as ex:
        log.append(f'🗂 архив: сбой {ex.__class__.__name__}: {ex}')

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry', action='store_true')
    a = ap.parse_args()
    try:
        print(run(a.dry))
    except Exception:
        tb = traceback.format_exc()
        print(tb)                                               # трейсбек — только в журнале GitHub
        if not a.dry:
            notify.crash('Футбол', tb)
        sys.exit(1)
    if not a.dry:
        try:
            notify.crash_clear()                                # успешный прогон — уведомления о сбоях убрать
        except Exception as e:
            print(f'crash_clear: {e}')


if __name__ == '__main__':
    main()
