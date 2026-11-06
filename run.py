"""Футбол: автономный цикл в облаке (GitHub Actions каждые 2 часа).

  1. входящие файлы football-data из папки Google Диска (кладёт бот) → база матчей;
  2. ESPN: расписание, результаты, кф DraftKings — только игровые дни турниров (календарь), параллельно;
  3. подгонка модели (Dixon–Coles по странам + сила стран в еврокубках) — каждый запуск, ~2 с;
  4. прогноз матчей ближайших 24 ч:
       режим «рынок»  — есть кф: Шин + инверсия (исход + тотал) → λ; поправка «после еврокубка» −4,5%;
       режим «модель» — кф нет: модель + факторы (нечего играть, плей-офф ЕК впереди, после ЕК, короткий отдых);
     11 линий с диапазоном кф в зоне 1,35–1,65; самокалибровка по рынку и источнику;
  5. расчёт по 90 минутам, закрывающие кф football-data для проверки, сбор составов (этап 2);
  6. Telegram: свежий список (прошлый удаляется, без матчей — тишина), итоги в 00:00, отчёт каждые 10 матчей,
     напоминание о файлах по понедельникам.

  python run.py [--dry]
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
import traceback

import numpy as np
import pandas as pd

import config as C
import dc
import fdata
import ffactors as FF
import flines as FL
import fmarket
import fespn
import journal as J
import model
import names
import notify
import runtime

REPORT_EVERY = 10
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
        self.missed: dict[str, str] = {}
        self.missed_uefa = 0

    def __call__(self, name: str, comp: str) -> str | None:
        _, country, _, kind = C.COMPETITIONS[comp]
        k = (name, country)
        if k not in self.cache:
            cand = self.all if country is None else self.by_c.get(country, set())
            r = names.match(name, cand)
            self.cache[k] = r
            if r is None and country is not None:          # в еврокубках неопознанные — в основном клубы других стран
                self.missed[name] = C.COMPETITIONS[comp][0]
            elif r is None:
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
        if str(comp) in out and d >= today - dt.timedelta(days=10):
            out[str(comp)] |= {d, d + dt.timedelta(days=1)}
    if not rt.get('uefa_backfill'):                                      # один раз: еврокубки 2025/26 без ЛЧ (она есть в CC0)
        for comp in ('uefa.europa', 'uefa.europa.conf', 'uefa.super_cup'):
            for d in _days(C.UEFA_BACKFILL_FROM, today):
                if d.weekday() in (1, 2, 3) or (d.weekday() == 5 and d.month in (5, 6)) or comp == 'uefa.super_cup':
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


# ======================================================================== самокалибровка
def calibration(ldf: pd.DataFrame) -> dict[str, tuple[float, int]]:
    """k по рынку и источнику прогноза: P' = σ(k·logit P), включается после 30 рассчитанных линий."""
    out = {}
    done = ldf[ldf.result.astype(str).isin(['W', 'L'])]
    for mk in FL.MARKET_RU:
        for src in SRC_RU:
            sub = done[(done.market.astype(str) == mk) & (done.source.astype(str) == src)]
            out[f'{mk}|{src}'] = FL.calibrate(sub.p_raw.map(J.num).values.astype(float), (sub.result.astype(str) == 'W').values)
    return out


# ======================================================================== прогноз
def forecast(matches: list[fespn.FMatch], mp: Mapper, world: model.World, sched: FF.Schedule, train: pd.DataFrame,
             auto: pd.DataFrame, kcal: dict) -> tuple[list[tuple], list[str]]:
    now = _now()
    settled = set(auto[~auto.settled.map(_blank).astype(bool)].espn_id.map(_id))
    season_start = pd.Timestamp(now.year if now.month >= 7 else now.year - 1, 7, 1)
    seasons: dict[str, pd.DataFrame] = {}
    out, skipped = [], []
    for m in matches:
        if m.state != 'pre' or m.id in settled or not (now < m.start <= now + pd.Timedelta(hours=C.HORIZON_HOURS)):
            continue
        name, country, div, kind = C.COMPETITIONS[m.comp]
        h, a = mp(m.home, m.comp), mp(m.away, m.comp)
        mk = fmarket.invert(m.odds) if m.odds else None
        fh, fa = {}, {}
        if kind in LEAGUE_KINDS and h and a:
            fh, fa = sched.features(h, m.start), sched.features(a, m.start)
            if div in C.TOPDIV:
                if div not in seasons:
                    seasons[div] = train[(train['div'] == div) & (train.date >= season_start) & train.hg.notna()]
                dd = FF.dead_flags(seasons[div], div, m.start)
                fh['dead'], fa['dead'] = dd.get(h, 0), dd.get(a, 0)
        if mk is not None and mk['fit_err'] < 0.03:
            src = 'market'
            lh, la = FF.adjust(mk['lh'], mk['la'], fh, fa, 'market') if fh else (mk['lh'], mk['la'])
        else:
            lam = world.lambdas(m.comp, h, a, m.neutral) if (h and a) else None
            if lam is None:
                skipped.append(f'{m.home} — {m.away} ({name}): нет кф и ' +
                               ('команда не опознана' if not (h and a) else 'нет оценки силы'))
                continue
            src = 'model'
            lh, la = FF.adjust(*lam, fh, fa, 'model') if fh else lam
        P = dc.score_matrix(lh, la, C.RHO)
        kc = {k.split('|')[0]: v[0] for k, v in kcal.items() if k.endswith('|' + src)}
        lns = FL.build(P, kc, src)
        i, j = np.indices(P.shape)
        o = m.odds or {}
        row = dict(created=J.now_utc(), espn_id=m.id, comp=m.comp, comp_name=name, kind=kind, stage=m.stage,
                   start_utc=m.start.strftime('%Y-%m-%d %H:%M'), start_local=J.to_local(m.start), home=m.home, away=m.away,
                   home_fd=h or '', away_fd=a or '', source=src, lam_h=round(lh, 3), lam_a=round(la, 3),
                   p_h=round(float(P[i > j].sum()), 4), p_d=round(float(np.trace(P)), 4), p_a=round(float(P[i < j].sum()), 4),
                   p_o25=round(float(P[(i + j) > 2.5].sum()), 4), p_btts=round(float(P[(i >= 1) & (j >= 1)].sum()), 4),
                   dk_h=o.get('h', ''), dk_d=o.get('d', ''), dk_a=o.get('a', ''), dk_line=o.get('line', ''),
                   dk_o=o.get('over', ''), dk_u=o.get('under', ''), f_home=FF.describe(fh), f_away=FF.describe(fa))
        for x in lns:
            x.update(created=row['created'], espn_id=m.id, comp=m.comp, comp_name=name, source=src,
                     start_utc=row['start_utc'], start_local=row['start_local'], home=m.home, away=m.away,
                     status='', result='', settled='', pnl_lo='')
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
        store.update('auto_lines', pd.DataFrame.from_dict(upd_lines, orient='index'), J.LINE_FCST_COLS)
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
    store.update('auto_forecasts', af.loc[upd], J.AUTO_RESULT_COLS)
    ld = store.read('auto_lines')
    lu = []
    for i, r in ld[ld.settled.map(_blank).astype(bool)].iterrows():
        x = res.get(_id(r.espn_id))
        if x is None:
            continue
        st, hg, ag = x
        v = FL.settle(str(r.market), str(r.pick), r.line, st, int(hg) if st == 'FT' else None, int(ag) if st == 'FT' else None)
        k = J.num(r.z_lo) if not _blank(r.z_lo) else J.num(r.k_lo)
        ld.loc[i, J.LINE_RESULT_COLS] = [st, v, today, round({'W': k - 1, 'L': -1.0, 'P': 0.0}[v], 3)]
        lu.append(i)
    store.update('auto_lines', ld.loc[lu], J.LINE_RESULT_COLS)
    return len(upd), len(lu)


def attach_odds(store: J.Store, base: pd.DataFrame) -> int:
    """Закрывающие кф football-data (средний рынок, биржа Betfair) к рассчитанным матчам — проверка против рынка."""
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
        return fespn.lineups(comp, ev)
    except Exception:
        return None


# ======================================================================== Telegram: список
def shown(x: dict) -> bool:
    return not _blank(x.get('z_lo'))


def match_block(row: dict, lns: list[dict], today: str) -> str | None:
    e = notify.esc
    vis = [x for x in sorted(lns, key=lambda x: x['slot']) if shown(x)]
    if not vis:
        return None
    when = row['start_local'][6:] if row['start_local'][:5] == today else row['start_local']
    out = [f"🕒 <b>{e(when)}</b> · <b>{e(row['home'])}</b> — <b>{e(row['away'])}</b> · {SRC_RU[row['source']]}"]
    for x in vis:
        out.append(f"{e(x['label'])}: <b>{float(x['z_lo']):.2f}–{float(x['z_hi']):.2f}</b>")
    return '\n'.join(out)


def list_blocks(items: list[tuple]) -> list[str]:
    today = _local().strftime('%d.%m')
    groups: dict[str, list] = {}
    for m, row, lns in sorted(items, key=lambda t: t[1]['start_utc']):
        b = match_block(row, lns, today)
        if b:
            groups.setdefault(row['comp_name'], []).append(b)
    if not groups:
        return []
    n = sum(len(v) for v in groups.values())
    blocks = [f"⚽ <b>Прогноз на 24 ч</b> · {_local().strftime('%d.%m %H:%M')} · {n} матч."]
    for comp, ms in groups.items():
        head = f'🏆 <b>{notify.esc(comp)}</b>'
        chunk = head
        for mb in ms:
            if len(chunk) + len(mb) + 2 > 3700:
                blocks.append(chunk); chunk = head + ' (продолжение)'
            chunk += '\n\n' + mb
        blocks.append(chunk)
    return blocks


def publish_list(rt: dict, items: list[tuple], dry: bool = False, extra: list | None = None) -> str:
    items = [t for t in items if any(shown(x) for x in t[2])]
    ids = sorted(m.id for m, _, _ in items)
    if dry:
        print('\n\n'.join(list_blocks(items)))
        return 'dry'
    removed = notify.delete([tuple(x) for x in rt.get('list_messages', [])] + list(extra or []))
    sent = []
    if items:
        silent = set(ids) <= set(rt.get('list_match_ids', []))
        sent = notify.send_blocks(list_blocks(items), silent=silent)
    rt['list_messages'] = [list(x) for x in sent]
    rt['list_match_ids'] = ids
    return f'список: удалено {removed}, отправлено {len(sent)} сообщ., матчей {len(ids)}'


def extra_messages(store: J.Store) -> list[tuple[int, int]]:
    df = store.read('tg_messages')
    out = [(int(J.num(c)), int(J.num(m))) for c, m in zip(df.chat_id, df.message_id)
           if not np.isnan(J.num(c)) and not np.isnan(J.num(m))]
    if len(df):
        store.write_all('tg_messages', pd.DataFrame(columns=J.SHEETS['tg_messages']))
    return out


# ======================================================================== статистика
def _outcome(r) -> int | None:
    if str(r.status) == 'ET':
        return 1
    if str(r.status) != 'FT':
        return None
    hg, ag = J.num(r.hg), J.num(r.ag)
    return 0 if hg > ag else 1 if hg == ag else 2


def match_stats(af: pd.DataFrame) -> list[str]:
    fin = af[af.status.astype(str).isin(['FT', 'ET'])]
    if fin.empty:
        return []
    out = []
    for src in SRC_RU:
        s = fin[fin.source.astype(str) == src]
        if s.empty:
            continue
        o = np.array([_outcome(r) for r in s.itertuples()])
        p = s[['p_h', 'p_d', 'p_a']].map(J.num).values.astype(float)
        ll = -np.log(np.clip(p[np.arange(len(s)), o], 1e-6, 1)).mean()
        hit = np.mean(p.argmax(1) == o)
        line = f'{SRC_RU[src].capitalize()}: матчей {len(s)} · исход угадан {hit * 100:.0f}% · лог-лосс 1X2 {ll:.4f}'
        a = s[['avg_h', 'avg_d', 'avg_a']].map(J.num)
        ok = (a > 1.01).all(axis=1).values
        if ok.sum() >= 10:
            pm = np.array([fmarket.shin(x) for x in a.values[ok]])
            llm = -np.log(np.clip(pm[np.arange(ok.sum()), o[ok]], 1e-6, 1)).mean()
            lls = -np.log(np.clip(p[ok][np.arange(ok.sum()), o[ok]], 1e-6, 1)).mean()
            line += f' · против закрытия рынка (n={int(ok.sum())}): {lls:.4f} vs {llm:.4f}'
        out.append(line)
    return out


def line_stats(ldf: pd.DataFrame, kcal: dict | None = None) -> list[str]:
    done = ldf[ldf.result.astype(str).isin(['W', 'L']) & ~ldf.z_lo.map(_blank).astype(bool)]
    out = []
    for mk, ru in FL.MARKET_RU.items():
        sub = done[done.market.astype(str) == mk]
        if sub.empty:
            continue
        won = (sub.result.astype(str) == 'W').values
        roi = np.nanmean(sub.pnl_lo.map(J.num).values) * 100
        out.append(f'{ru}: {len(sub)} · попадание {won.mean() * 100:.0f}% (модель {np.nanmean(sub.p.map(J.num).values) * 100:.0f}%)'
                   f' · ROI при мин. кф {roi:+.1f}%')
    return out


def calib_line(kcal: dict) -> str:
    act = [f'{FL.MARKET_RU[k.split("|")[0]]} ({SRC_RU[k.split("|")[1]]}) k={v[0]:.3f} n={v[1]}' for k, v in kcal.items() if v[1] > 0]
    return 'Самокалибровка (включается после 30 линий): ' + ('; '.join(act) if act else 'данных пока нет')


def summary(store: J.Store, kcal: dict) -> pd.DataFrame:
    af, ld = store.read('auto_forecasts'), store.read('auto_lines')
    rows = [('обновлено (UTC)', J.now_utc()), ('прогнозов', len(af)), ('линий', len(ld))]
    rows += [('матчи', x) for x in match_stats(af)] + [('линии', x) for x in line_stats(ld)] + [('калибровка', calib_line(kcal))]
    return pd.DataFrame(rows, columns=['показатель', 'значение'])


def progress_report(af: pd.DataFrame, ld: pd.DataFrame, kcal: dict) -> list[str]:
    e = notify.esc
    fin = af[af.status.astype(str).isin(['FT', 'ET'])].sort_values('start_utc')
    if fin.empty:
        return []
    head = (f'📈 <b>Футбол · отчёт модели · {len(fin)} доигранных матчей</b>\n'
            f'Период: {str(fin.start_utc.iloc[0])[:10]} — {str(fin.start_utc.iloc[-1])[:10]}. Перешлите этот отчёт в чат с Claude.')
    rec = ['<b>Последние 10:</b>']
    for r in fin.tail(REPORT_EVERY).itertuples():
        p = [J.num(r.p_h), J.num(r.p_d), J.num(r.p_a)]
        k = int(np.argmax(p)); o = _outcome(r)
        sc = f'{int(J.num(r.hg))}:{int(J.num(r.ag))}' if str(r.status) == 'FT' else 'ничья в осн. время'
        rec.append(f"{'✅' if k == o else '❌'} {e(r.home)} — {e(r.away)} · {['П1', 'X', 'П2'][k]} {p[k] * 100:.0f}% · {sc} · {SRC_RU.get(str(r.source), '')}")
    q = ['<b>Качество:</b>'] + [e(x) for x in match_stats(af)]
    ln = ['<b>Линии по рынкам:</b>'] + [e(x) for x in line_stats(ld)] + [e(calib_line(kcal))]
    return [head, '\n'.join(rec), '\n'.join(q), '\n'.join(ln)]


def daily(af: pd.DataFrame, ld: pd.DataFrame, kcal: dict, notes: list[str]) -> list[str]:
    e = notify.esc
    since = (_now() - pd.Timedelta(hours=24)).strftime('%Y-%m-%d %H:%M')
    fin = af[af.status.astype(str).isin(['FT', 'ET'])]
    last = fin[fin.start_utc.astype(str) >= since]
    b = [f"🧠 <b>Футбол · итоги самообучения · {_local().strftime('%d.%m.%Y')}</b>"]
    if len(last):
        o = np.array([_outcome(r) for r in last.itertuples()])
        p = last[['p_h', 'p_d', 'p_a']].map(J.num).values.astype(float)
        b.append(f'За сутки доиграно {len(last)} матч. · исход угадан в {int(np.sum(p.argmax(1) == o))}')
    else:
        b.append('За сутки доигранных матчей нет.')
    ms, ls = match_stats(af), line_stats(ld)
    if ms:
        b.append('<b>Всего:</b>\n' + '\n'.join(e(x) for x in ms))
    if ls:
        b.append('<b>Линии по рынкам:</b>\n' + '\n'.join(e(x) for x in ls))
    b.append(e(calib_line(kcal)))
    if notes:
        b.append('<b>Данные:</b> ' + e('; '.join(notes)))
    return b


# ======================================================================== файлы и напоминание
def take_inbox() -> tuple[bool, list[str]]:
    import inbox
    try:
        files, note = inbox.fetch()
    except Exception as ex:
        return False, [f'папка входящих недоступна: {ex}']
    log, got = ([note] if note else []), False
    for fid, name, raw in files:
        try:
            log.append(f'файл принят: {fdata.save_file(raw)}')
            got = True
        except Exception as ex:
            log.append(f'файл {name} не принят: {ex}')
        inbox.mark_done(fid, name)
    if files:
        notify.send('Футбол: файлы обработаны\n' + '\n'.join(log))
    return got, log


def reminder_due(rt: dict) -> bool:
    loc = _local()
    if not (12 <= loc.hour < 14):
        return False
    monday = (loc.date() - dt.timedelta(days=loc.weekday())).isoformat()
    return (rt.get('last_files') or '2000-01-01') < monday and rt.get('last_reminder') != loc.date().isoformat()


def reminder_text() -> str:
    y = _local().year if _local().month >= 7 else _local().year - 1
    return ('⏰ <b>Пора прислать файлы football-data</b>\n'
            f'1) all-euro-data-{y}-{y + 1}.xlsx — {C.FD_PAGES[0]}\n'
            f'2) RUS.xlsx — {C.FD_PAGES[1]}\n'
            'Отправьте оба файла сюда. Они обновляют базу и проверку прогнозов по закрывающим кф. '
            'Напоминание повторится завтра, если файлов не будет.')


# ======================================================================== цикл
def run(dry: bool = False) -> str:
    t0 = time.time()
    store, rt = J.Store(), runtime.load()
    log = []
    got, ilog = (False, []) if dry else take_inbox()
    log += ilog
    if got:
        rt['last_files'] = _local().date().isoformat()
    base = fdata.load()
    mp = Mapper(base)
    st = load_store()
    af, ld = store.read('auto_forecasts'), store.read('auto_lines')
    matches, errs = fespn.fetch(plan(base, st, af, rt))
    st = persist(st, matches, mp)
    if not dry:
        save_store(st)
        rt['uefa_backfill'] = rt.get('uefa_backfill') or J.now_utc()
    n_odds = sum(1 for m in matches if m.odds and m.state == 'pre')
    log.append(f'ESPN: матчей {len(matches)}, с кф {n_odds}, ошибок запросов {len(errs)}; в базе результатов {len(st)}')
    train = fdata.training_set(st)
    ufit, usched = uefa_frames(st, matches, mp)
    world = model.build(train, ufit, _now())
    log.append(f"модель: стран {len(world.sup)}, команд {len(world.team_country)}, еврокубков в оценке силы стран {world.uefa.get('n', 0)}")
    sched = FF.Schedule(train[['date', 'home', 'away']], usched)
    kcal = calibration(ld)
    items, skipped = forecast(matches, mp, world, sched, train, af, kcal)
    if dry:
        print('\n'.join(log)); publish_list(rt, items, dry=True); print('\n'.join(skipped))
        return f'dry {time.time() - t0:.0f} с'
    nn, nu = save_forecasts(store, items, af, ld)
    n_before = int(af.status.astype(str).isin(['FT', 'ET']).sum())
    ns, nl = settle(store, results_index(st))
    no = attach_odds(store, base)
    af, ld = store.read('auto_forecasts'), store.read('auto_lines')
    n_after = int(af.status.astype(str).isin(['FT', 'ET']).sum())
    if n_after // REPORT_EVERY > n_before // REPORT_EVERY:
        notify.send_blocks(progress_report(af, ld, kcal))
        log.append(f'отчёт для Claude: {n_after} матчей')
    log.append(publish_list(rt, items, extra=extra_messages(store)))
    try:
        log.append(collect_lineups(st))
    except Exception as ex:
        log.append(f'составы: сбой {ex.__class__.__name__}')
    today = _local().date().isoformat()
    if _local().hour < 2 and rt.get('last_daily') != today:
        notes = [f'не опознаны: {", ".join(list(mp.missed)[:8])}'] if mp.missed else []
        notify.send_blocks(daily(af, ld, kcal, notes))
        rt['last_daily'] = today
        log.append('итоги отправлены')
    if reminder_due(rt):
        notify.send_blocks([reminder_text()])
        rt['last_reminder'] = today
        log.append('напоминание о файлах отправлено')
    store.write_all('summary', summary(store, kcal))
    runtime.save(rt)
    by_src = pd.Series([r['source'] for _, r, _ in items]).value_counts().to_dict()
    log += [f'прогнозов: новых {nn}, обновлено {nu} ({by_src}); рассчитано матчей {ns}, линий {nl}; кф football-data {no}',
            f'пропущено {len(skipped)}; не опознано команд: в чемпионатах и кубках {len(mp.missed)}, в еврокубках {mp.missed_uefa} (клубы других стран); время {time.time() - t0:.0f} с']
    return '\n'.join(log + skipped[:15] + [f'не опознано: {k} ({v})' for k, v in list(mp.missed.items())[:15]] + errs[:10])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry', action='store_true')
    a = ap.parse_args()
    try:
        print(run(a.dry))
    except Exception:
        tb = traceback.format_exc()
        print(tb)
        if not a.dry:
            notify.send(f'Футбол: сбой авто-режима\n{tb[-1500:]}')
        sys.exit(1)


if __name__ == '__main__':
    main()
