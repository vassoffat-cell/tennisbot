"""Хранилище: Google Таблица (основной режим) или CSV в папке logs (запасной, только на компьютере).

Листы:
  auto_lines      — все 7 линий каждого матча (вероятность, уверенность, показана ли) и их расчёт (самообучение по рынкам);
  day_pick        — ⭐ ставка дня (одна линия на сутки) и её итог;  reports — готовые отчёты для «Аналитики»;
  tg_cache        — последний список прогноза для кнопки «Прогноз 24 ч»;
  (forecasts, paper_bets — модуль скриншотов, отключён)
  auto_forecasts  — авто-прогнозы на все матчи основного тура из расписания ESPN, сделанные ДО начала матча,
                    их результат и проверочная ставка на победителя по закрывающим коэффициентам tennis-data;
  summary         — сводка качества модели (пересчитывается каждый день).
Числа пишутся как числа (RAW), поэтому десятичный разделитель таблицы (запятая/точка) не важен.
Расчёт меняет только ячейки результата в нужных строках — строки, добавленные ботом в это же время, не теряются.
"""
from __future__ import annotations

import csv
import datetime as dt
import uuid
from pathlib import Path

import numpy as np
import pandas as pd

import config as C
import forecast as FC
import markets as MK
from markov import Outcome

FORECAST_COLS = ['created', 'match_id', 'tour', 'tournament', 'round', 'surface', 'match_date', 'player1', 'player2',
                 'player1_id', 'player2_id', 'tab', 'market', 'line', 'side', 'p_model', 'p_push', 'k_fair', 'k_1xbet',
                 'ev', 'signal']
BET_COLS = FORECAST_COLS + ['stake', 'result', 'pnl', 'settled']
EXACT_COLS = ['p_2_0', 'p_2_1', 'p_1_2', 'p_0_2', 'p_3_0', 'p_3_1', 'p_3_2', 'p_2_3', 'p_1_3', 'p_0_3']
AUTO_COLS = (['created', 'espn_id', 'tour', 'event', 'location', 'surface', 'round', 'best_of', 'start_utc', 'start_local',
              'player1', 'player2', 'player1_id', 'player2_id', 'n1', 'n2', 'id_score1', 'id_score2',
              'p1', 'p2', 'kfair1', 'kfair2', 'kmin1', 'kmin2', 'exp_total', 'total_line', 'p_over', 'hcp1_line', 'p_hcp1',
              'set1_p1'] + EXACT_COLS +
             ['status', 'winner', 'score', 'settled', 'logloss',
              'b365_1', 'b365_2', 'avg_1', 'avg_2', 'max_1', 'max_2', 'bfe_1', 'bfe_2', 'bet_side', 'pnl_b365', 'pnl_avg', 'pnl_max',
              'level', 'p1_raw', 'calib_k', 'confidence'])   # новые столбцы — только в конец, чтобы не сдвигать старые строки
AUTO_RESULT_COLS = ['status', 'winner', 'score', 'settled', 'logloss']
AUTO_ODDS_COLS = ['b365_1', 'b365_2', 'avg_1', 'avg_2', 'max_1', 'max_2', 'bfe_1', 'bfe_2', 'bet_side', 'pnl_b365', 'pnl_avg', 'pnl_max']
LINE_COLS = ['created', 'espn_id', 'slot', 'tour', 'event', 'level', 'round', 'surface',
             'start_utc', 'start_local', 'player1', 'player2', 'market', 'pick', 'line', 'label', 'p_raw', 'p', 'k_lo', 'k_hi',
             'z_lo', 'z_hi', 'status', 'result', 'settled', 'pnl_lo', 'p_low', 'shown']   # новые — только в конец
LINE_FCST_COLS = ['start_utc', 'start_local', 'player1', 'player2', 'market', 'pick', 'line', 'label', 'p_raw', 'p', 'k_lo', 'k_hi',
                  'z_lo', 'z_hi']
LINE_RESULT_COLS = ['status', 'result', 'settled', 'pnl_lo']
AUTO_FCST_COLS = AUTO_COLS[AUTO_COLS.index('start_utc'):AUTO_COLS.index('p_0_3') + 1]
AUTO_TAIL_COLS = ['level', 'p1_raw', 'calib_k', 'confidence']
DAY_COLS = ['date', 'espn_id', 'slot', 'tour', 'event', 'start_utc', 'start_local', 'player1', 'player2', 'market', 'label', 'p',
            'p_low', 'published', 'changes', 'history', 'status', 'result', 'settled',
            # пакет №3 (правило C): линия, кф 1xBet, источник P, уверенность в выгодности, принятие, скриншоты — только в конец
            'pick', 'line', 'k', 'k_src', 'p_src', 'conf', 'value_ok', 'tier', 'source', 'p_model', 'accepted', 'accepted_at',
            'screenshots', 'edge', 'group', 'p_pin',                 # ТЗ-004 — перевес над Pinnacle, группа, P Pinnacle
            'rank', 'fire', 'clash',                                 # 11.11: ⭐2/⭐3 (rank), синтез с каперами 🔥/⚔️
            'p_close', 'clv']                                        # 14.11: цена закрытия (Pinnacle без маржи), CLV = кф × P_закр − 1
CHG_COLS = ['date', 'time', 'reason', 'rank', 'espn_id', 'market', 'pick', 'line', 'label', 'match', 'p', 'k', 'to_label',
            'to_key', 'slot', 'status', 'result', 'settled']   # журнал смен ставки дня (заменённые ставки тоже получают итог)
SHEETS = {'forecasts': FORECAST_COLS, 'paper_bets': BET_COLS, 'auto_forecasts': AUTO_COLS, 'auto_lines': LINE_COLS,
          'summary': ['показатель', 'значение'], 'tg_messages': ['chat_id', 'message_id'], 'day_pick': DAY_COLS, 'dp_changes': CHG_COLS, 'dp_actions': ['time', 'action', 'chat_id', 'message_id', 'done'], 'learning': ['when', 'kind', 'title', 'text', 'sent'],
          'reports': ['key', 'kind', 'title', 'text', 'updated'],     # готовые отчёты для кнопки «Аналитика» (бот только читает)
          'tg_cache': ['key', 'text', 'updated']}                     # последний список прогноза для кнопки «Прогноз 24 ч»


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M')


def to_local(ts: pd.Timestamp) -> str:
    return (ts + pd.Timedelta(hours=C.TZ_OFFSET_HOURS)).strftime('%d.%m %H:%M')


def _clean(v):
    """Значение для записи: числа — числами, NaN/None — пусто."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ''
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    return v


def _retry(fn, *a, **k):
    """Вызов Google Sheets с повтором при превышении квоты (429: 60 чтений/мин на сервисный аккаунт; два бота делят её)."""
    import time
    for i in range(5):
        try:
            return fn(*a, **k)
        except Exception as e:
            s = str(e)                                            # 14.11: и временные сбои Google (500/502/503/504)
            if not any(x in s for x in ('429', 'Quota exceeded', '[500]', '[502]', '[503]', '[504]', 'unavailable')) or i == 4:
                raise
            time.sleep(20 * (i + 1))


class Store:
    def __init__(self) -> None:
        self.sh, self.ws, self._cache = None, {}, {}
        if C.SHEET_ID and Path(C.GOOGLE_CREDENTIALS).exists():
            import gspread
            self.sh = _retry(gspread.service_account(filename=C.GOOGLE_CREDENTIALS).open_by_key, C.SHEET_ID)   # 14.11: 503 при открытии
        C.LOGS.mkdir(exist_ok=True)

    @property
    def backend(self) -> str:
        return 'Google Таблица' if self.sh else f'CSV ({C.LOGS})'

    def _sheet(self, name: str):
        if name not in self.ws:
            cols = SHEETS[name]
            try:
                ws = _retry(self.sh.worksheet, name)
            except Exception:
                ws = self.sh.add_worksheet(title=name, rows=1000, cols=len(cols))
            head = _retry(ws.row_values, 1)
            if head != cols:                          # новая или старая шапка — привести к текущей
                if ws.col_count < len(cols):
                    ws.add_cols(len(cols) - ws.col_count)
                ws.update([cols], 'A1', value_input_option='RAW')
            self.ws[name] = ws
        return self.ws[name]

    def read(self, name: str) -> pd.DataFrame:
        """Таблица листа; индекс — номер строки листа (данные начинаются со 2-й строки)."""
        cols = SHEETS[name]
        if name in self._cache:                   # кэш на время запуска (квота чтений); сбрасывается при записи
            return self._cache[name].copy()
        if self.sh:
            vals = _retry(_retry(self._sheet, name).get_all_values, value_render_option='UNFORMATTED_VALUE')
            rows = [r + [''] * (len(cols) - len(r)) for r in vals[1:]]
            df = pd.DataFrame([r[:len(cols)] for r in rows], columns=cols)
        else:
            f = C.LOGS / f'{name}.csv'
            df = (pd.read_csv(f, sep=';', encoding='utf-8-sig', dtype=str, keep_default_na=False) if f.exists()
                  else pd.DataFrame(columns=cols))
            for c in cols:
                if c not in df:
                    df[c] = ''
            df = df[cols]
        df = df.astype(object)                    # pandas 3: иначе столбцы строкового типа не примут числа
        df.index = range(2, len(df) + 2)
        self._cache[name] = df
        return df.copy()

    def append(self, name: str, rows: list[dict]) -> None:
        self._cache.pop(name, None)
        if not rows:
            return
        cols = SHEETS[name]
        if self.sh:
            ws = self._sheet(name)
            ws.append_rows([[_clean(r.get(c, '')) for c in cols] for r in rows], value_input_option='RAW',
                           insert_data_option='INSERT_ROWS', table_range='A1')
            return
        f = C.LOGS / f'{name}.csv'
        old = self.read(name) if f.exists() else None
        if old is not None and list(pd.read_csv(f, sep=';', encoding='utf-8-sig', nrows=0).columns) != cols:
            self.write_all(name, pd.concat([old, pd.DataFrame(rows)], ignore_index=True))    # шапка изменилась
            return
        with open(f, 'a', newline='', encoding='utf-8-sig') as fh:
            w = csv.DictWriter(fh, fieldnames=cols, delimiter=';', extrasaction='ignore')
            if old is None:
                w.writeheader()
            w.writerows([{c: _clean(r.get(c, '')) for c in cols} for r in rows])

    def update(self, name: str, df: pd.DataFrame, cols: list[str]) -> None:
        """Записать в строки df.index значения столбцов cols (столбцы должны идти подряд)."""
        self._cache.pop(name, None)
        if df.empty:
            return
        allc = SHEETS[name]
        i0, i1 = allc.index(cols[0]), allc.index(cols[-1])
        span = allc[i0:i1 + 1]
        if self.sh:
            from gspread.utils import rowcol_to_a1
            ws = self._sheet(name)
            data = [{'range': f'{rowcol_to_a1(r, i0 + 1)}:{rowcol_to_a1(r, i1 + 1)}',
                     'values': [[_clean(df.at[r, c]) for c in span]]} for r in df.index]
            for k in range(0, len(data), 500):
                _retry(ws.batch_update, data[k:k + 500], value_input_option='RAW')
            return
        full = self.read(name).astype(object)
        for r in df.index:
            for c in span:
                full.at[r, c] = _clean(df.at[r, c])
        self.write_all(name, full)

    def write_all(self, name: str, df: pd.DataFrame) -> None:
        self._cache.pop(name, None)
        cols = SHEETS[name]
        df = df.reindex(columns=cols)
        if self.sh:
            ws = self._sheet(name)
            _retry(ws.clear)
            _retry(ws.update, [cols] + [[_clean(v) for v in row] for row in df.astype(object).values.tolist()], 'A1',
                   value_input_option='RAW')
            return
        df.map(_clean).to_csv(C.LOGS / f'{name}.csv', sep=';', index=False, encoding='utf-8-sig')


def _fresh_after(fn):
    """Запись в лист сбрасывает кэш чтений этого листа и до, и после (CSV-режим перечитывает файл внутри записи)."""
    def wrapper(self, name, *a, **k):
        self._cache.pop(name, None)
        try:
            return fn(self, name, *a, **k)
        finally:
            self._cache.pop(name, None)
    return wrapper


Store.append = _fresh_after(Store.append)
Store.update = _fresh_after(Store.update)
Store.write_all = _fresh_after(Store.write_all)


# ======================================================================== ручные прогнозы (бот)
def num(x, default=np.nan) -> float:
    try:
        return float(str(x).replace(',', '.'))
    except (TypeError, ValueError):
        return default


class Journal:
    def __init__(self, store: Store | None = None) -> None:
        self.store = store or Store()

    @property
    def backend(self) -> str:
        return self.store.backend

    def log(self, m, fc: FC.Forecast) -> tuple[int, int]:
        p = m.parsed
        mid = uuid.uuid4().hex[:8]
        date = p.date or dt.date.today().strftime('%d.%m.%Y')
        base = dict(created=now_utc(), match_id=mid, tour=m.tour.upper(), tournament=p.tournament or '', round=p.round or '',
                    surface=p.surface or '', match_date=date, player1=m.names_lat[0], player2=m.names_lat[1],
                    player1_id=float(m.ids[0]), player2_id=float(m.ids[1]))
        rows = [dict(base, tab=r.tab, market=r.market, line='' if r.line is None else r.line, side=r.side,
                     p_model=round(r.p_model, 4), p_push=round(r.p_push, 4), k_fair=round(r.k_fair, 3), k_1xbet=r.k_book,
                     ev=round(r.ev, 4), signal=int(r.signal)) for r in fc.rows]
        bets = [dict(r, stake=1, result='', pnl='', settled='') for r in rows if r['signal']]
        self.store.append('forecasts', rows)
        self.store.append('paper_bets', bets)
        return len(rows), len(bets)

    def settle(self, results: list[tuple[float, float, pd.Timestamp, Outcome, str]] | None = None) -> str:
        """Рассчитать ставки. results — доигранные матчи (id1, id2, дата, исход с позиции id1, примечание);
        если не заданы, исходы ищутся в базе tennis-data."""
        df = self.store.read('paper_bets')
        if df.empty:
            return 'Ручных бумажных ставок пока нет.'
        todo = df[df.settled.astype(str).str.strip() == '']
        upd = []
        for i, r in todo.iterrows():
            a, b = num(r.player1_id), num(r.player2_id)
            try:
                d = pd.Timestamp(dt.datetime.strptime(str(r.match_date), '%d.%m.%Y'))
            except ValueError:
                d = pd.Timestamp(str(r.created)[:10])
            o, note = _lookup(results, a, b, d) if results is not None else _find_outcome_td(str(r.tour).lower(), a, b, d)
            if o is None:
                continue
            res = 'P' if note else _settle_row(r, o)
            k = num(r.k_1xbet)
            df.loc[i, ['result', 'pnl', 'settled']] = [res + (f' ({note})' if note else ''),
                                                      round({'W': k - 1, 'P': 0.0, 'L': -1.0}[res], 3),
                                                      dt.date.today().isoformat()]
            upd.append(i)
        self.store.update('paper_bets', df.loc[upd], ['result', 'pnl', 'settled'])
        return f'Рассчитано сейчас: {len(upd)}. ' + bets_summary(df)


def bets_summary(df: pd.DataFrame) -> str:
    s = df[df.settled.astype(str).str.strip() != '']
    if s.empty:
        return f'Рассчитанных ставок пока нет (ожидают: {len(df)}).'
    pnl = s.pnl.map(num)
    n = len(pnl)
    ci = 1.96 * pnl.std(ddof=1) / n ** 0.5 * 100 if n > 1 else float('nan')
    res = s.result.astype(str).str[0]
    return (f'Ручные ставки: рассчитано {n} из {len(df)} (выигрыш {int((res == "W").sum())}, проигрыш {int((res == "L").sum())}, '
            f'возврат {int((res == "P").sum())}); прибыль {pnl.sum():+.2f} ед., ROI {pnl.mean() * 100:+.1f}% ± {ci:.1f}% (95 %).'
            + ('' if n >= 200 else ' Для выводов нужно хотя бы 200 ставок.'))


def _lookup(results, a: float, b: float, d: pd.Timestamp):
    for x, y, date, o, note in results:
        if abs((date - d).days) > 3:
            continue
        if (x, y) == (a, b):
            return o, note
        if (x, y) == (b, a):
            return Outcome(tuple((s[1], s[0]) for s in o.sets)), note
    return None, ''


def _find_outcome_td(tour: str, a: float, b: float, d: pd.Timestamp):
    """Исход по базе tennis-data (запасной путь, если ESPN недоступен)."""
    import dataset
    h = _td_cache.get(tour)
    if h is None:
        h = _td_cache[tour] = dataset.build(tour)
    m = h[((h.winner_id == a) & (h.loser_id == b)) | ((h.winner_id == b) & (h.loser_id == a))]
    m = m[(m.Date - d).abs().dt.days <= 3]
    if m.empty:
        return None, ''
    r = m.iloc[-1]
    if r.Comment != 'Completed':
        return Outcome(()), f'матч не доигран: {r.Comment}'
    sets = []
    for k in range(1, 6):
        w, l = r.get(f'W{k}'), r.get(f'L{k}')
        if pd.notna(w) and pd.notna(l):
            sets.append((int(w), int(l)) if r.winner_id == a else (int(l), int(w)))
    return Outcome(tuple(sets)), ''


_td_cache: dict[str, pd.DataFrame] = {}


def _settle_row(r: pd.Series, o: Outcome) -> str:
    tab, market, side = r.tab, r.market, r.side
    if market == 'EXACT':
        sa, sb = map(int, str(r.line).split('-'))
        return MK.mk_exact_sets(sa, sb)(o)
    line = num(r.line) if str(r.line) not in ('', 'None', 'nan') else None
    n1, s1, n2, s2 = FC._settles(tab, market, line)
    return (s1 if side == n1 else s2)(o)
