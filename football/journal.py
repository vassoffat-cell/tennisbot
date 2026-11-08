"""Хранилище футбола: Google Таблица «Football» (основной режим) или CSV в папке logs (запасной, на компьютере).

Листы:
  auto_forecasts — прогноз каждого матча: источник (модель; «рынок» — только старые записи до 08.11.2026), λ, вероятности,
                   факторы, результат (кф DraftKings / football-data пишутся только как данные, в прогнозе не участвуют);
  auto_lines     — 11 линий матча (вероятность, уверенность, показана ли) и их расчёт (основа самообучения по рынкам);
  summary        — сводка (пересчитывается каждый запуск);
  tg_messages    — списки, отправленные кнопкой бота (облако удалит их при следующем обновлении);
  day_pick       — ⭐ ставка дня (одна линия на сутки) и её итог;
  reports        — готовые отчёты (всё время / месяц / турнир) для кнопки «Аналитика»;
  tg_cache       — последний список прогноза для кнопки «Прогноз 24 ч».
Числа пишутся как числа (RAW). Новые столбцы — только в конец.
"""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

import config as C

AUTO_COLS = ['created', 'espn_id', 'comp', 'comp_name', 'kind', 'stage', 'start_utc', 'start_local', 'home', 'away',
             'home_fd', 'away_fd', 'source', 'lam_h', 'lam_a', 'p_h', 'p_d', 'p_a', 'p_o25', 'p_btts',
             'dk_h', 'dk_d', 'dk_a', 'dk_line', 'dk_o', 'dk_u', 'f_home', 'f_away',
             'status', 'hg', 'ag', 'settled', 'logloss',
             'avg_h', 'avg_d', 'avg_a', 'bf_h', 'bf_d', 'bf_a', 'avg_o25', 'avg_u25']
AUTO_FCST_COLS = AUTO_COLS[AUTO_COLS.index('start_utc'):AUTO_COLS.index('f_away') + 1]
AUTO_RESULT_COLS = ['status', 'hg', 'ag', 'settled', 'logloss']
AUTO_ODDS_COLS = ['avg_h', 'avg_d', 'avg_a', 'bf_h', 'bf_d', 'bf_a', 'avg_o25', 'avg_u25']
LINE_COLS = ['created', 'espn_id', 'slot', 'comp', 'comp_name', 'source', 'start_utc', 'start_local', 'home', 'away',
             'market', 'pick', 'line', 'label', 'p_raw', 'p', 'k_lo', 'k_hi', 'z_lo', 'z_hi', 'status', 'result', 'settled',
             'pnl_lo', 'p_low', 'shown']              # новые столбцы — только в конец
LINE_FCST_COLS = LINE_COLS[LINE_COLS.index('source'):LINE_COLS.index('z_hi') + 1]
LINE_RESULT_COLS = ['status', 'result', 'settled', 'pnl_lo']
DAY_COLS = ['date', 'espn_id', 'slot', 'comp_name', 'start_utc', 'start_local', 'home', 'away', 'market', 'label', 'p',
            'p_low', 'published', 'changes', 'history', 'status', 'result', 'settled',
            # пакет №3 (правило C): линия, кф 1xBet, источник P, уверенность в выгодности, принятие, скриншоты — только в конец
            'pick', 'line', 'k', 'k_src', 'p_src', 'conf', 'value_ok', 'tier', 'source', 'p_model', 'accepted', 'accepted_at',
            'screenshots']
SHEETS = {'auto_forecasts': AUTO_COLS, 'auto_lines': LINE_COLS, 'summary': ['показатель', 'значение'],
          'tg_messages': ['chat_id', 'message_id'], 'day_pick': DAY_COLS, 'dp_actions': ['time', 'action', 'chat_id', 'message_id', 'done'], 'learning': ['when', 'kind', 'title', 'text', 'sent'],
          'reports': ['key', 'kind', 'title', 'text', 'updated'],     # готовые отчёты для кнопки «Аналитика» (бот только читает)
          'tg_cache': ['key', 'text', 'updated']}                     # последний список прогноза для кнопки «Прогноз 24 ч»


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M')


def to_local(ts: pd.Timestamp) -> str:
    return (ts + pd.Timedelta(hours=C.TZ_OFFSET_HOURS)).strftime('%d.%m %H:%M')


def num(x, default=np.nan) -> float:
    try:
        return float(str(x).replace(',', '.'))
    except (TypeError, ValueError):
        return default


def _clean(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ''
    if isinstance(v, np.floating):
        return float(v)
    if isinstance(v, (np.integer, np.bool_)):
        return int(v)
    return v


class Store:
    def __init__(self) -> None:
        self.sh, self.ws = None, {}
        if C.SHEET_ID and Path(C.GOOGLE_CREDENTIALS).exists():
            import gspread
            self.sh = gspread.service_account(filename=C.GOOGLE_CREDENTIALS).open_by_key(C.SHEET_ID)
        C.LOGS.mkdir(exist_ok=True)

    @property
    def backend(self) -> str:
        return 'Google Таблица' if self.sh else f'CSV ({C.LOGS})'

    def _sheet(self, name: str):
        if name not in self.ws:
            cols = SHEETS[name]
            try:
                ws = self.sh.worksheet(name)
            except Exception:
                ws = self.sh.add_worksheet(title=name, rows=1000, cols=len(cols))
            if ws.row_values(1) != cols:
                if ws.col_count < len(cols):
                    ws.add_cols(len(cols) - ws.col_count)
                ws.update([cols], 'A1', value_input_option='RAW')
            self.ws[name] = ws
        return self.ws[name]

    def read(self, name: str) -> pd.DataFrame:
        """Таблица листа; индекс — номер строки листа (данные со 2-й строки)."""
        cols = SHEETS[name]
        if self.sh:
            vals = self._sheet(name).get_all_values(value_render_option='UNFORMATTED_VALUE')
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
        df = df.astype(object)
        df.index = range(2, len(df) + 2)
        return df

    def append(self, name: str, rows: list[dict]) -> None:
        if not rows:
            return
        cols = SHEETS[name]
        if self.sh:
            self._sheet(name).append_rows([[_clean(r.get(c, '')) for c in cols] for r in rows], value_input_option='RAW',
                                          insert_data_option='INSERT_ROWS', table_range='A1')
            return
        f = C.LOGS / f'{name}.csv'
        new = not f.exists()
        with open(f, 'a', newline='', encoding='utf-8-sig') as fh:
            w = csv.DictWriter(fh, fieldnames=cols, delimiter=';', extrasaction='ignore')
            if new:
                w.writeheader()
            w.writerows([{c: _clean(r.get(c, '')) for c in cols} for r in rows])

    def update(self, name: str, df: pd.DataFrame, cols: list[str]) -> None:
        """Записать в строки df.index значения столбцов cols (столбцы идут подряд)."""
        if df.empty:
            return
        allc = SHEETS[name]
        i0, i1 = allc.index(cols[0]), allc.index(cols[-1])
        span = allc[i0:i1 + 1]
        if self.sh:
            from gspread.utils import rowcol_to_a1
            ws = self._sheet(name)
            data = [{'range': f'{rowcol_to_a1(r, i0 + 1)}:{rowcol_to_a1(r, i1 + 1)}',
                     'values': [[_clean(df.at[r, c]) if c in df.columns else '' for c in span]]} for r in df.index]
            for k in range(0, len(data), 500):
                ws.batch_update(data[k:k + 500], value_input_option='RAW')
            return
        full = self.read(name)
        for r in df.index:
            for c in span:
                if c in df.columns:
                    full.at[r, c] = _clean(df.at[r, c])
        self.write_all(name, full)

    def write_all(self, name: str, df: pd.DataFrame) -> None:
        cols = SHEETS[name]
        df = df.reindex(columns=cols)
        if self.sh:
            ws = self._sheet(name)
            ws.clear()
            ws.update([cols] + [[_clean(v) for v in row] for row in df.astype(object).values.tolist()], 'A1',
                      value_input_option='RAW')
            return
        df.map(_clean).to_csv(C.LOGS / f'{name}.csv', sep=';', index=False, encoding='utf-8-sig')
