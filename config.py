"""Настройки бота. Секреты (токен бота, ID таблицы) читаются из файла .env рядом с этим файлом."""
from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
SACKMANN = DATA / 'sackmann'          # wta_matches_2019.csv … , wta_players.csv, atp_…
TENNIS_DATA = DATA / 'tennis-data'    # 2019_WTA.xlsx … 2026_WTA.xlsx, 2019_ATP.xlsx …
STATE = ROOT / 'state'                # собранное состояние игроков и веса модели
LOGS = ROOT / 'logs'                  # запасной журнал CSV, если Google Таблица не настроена
FIRST_SEASON, LAST_SEASON = 2019, max(2026, _dt.date.today().year)   # новый сезон подхватывается сам

# ядро: K-фактор Elo, вес покрытия, momentum (в сете, между сетами) — см. документ по теннису, шаги 2–5
ELO_A, ELO_C = 75.0, 0.2
ELO_W_SURF = {'wta': 0.25, 'atp': 0.5}
MOMENTUM = {'wta': (0.071, 0.182), 'atp': (0.061, 0.187)}

# бумажные ставки: сигнал, если EV по модели не ниже порога и вероятность стороны не меньше минимума
EV_THRESHOLD = 0.05
MIN_PROB = 0.15

# авто-режим: часовой пояс сообщений, горизонт прогноза, источники
TZ_OFFSET_HOURS = 5                     # Ташкент, UTC+5
HORIZON_HOURS = 36                      # прогнозировать матчи, начинающиеся в ближайшие 36 часов
TENNIS_DATA_URL = {'wta': 'http://www.tennis-data.co.uk/{y}w/{y}.xlsx', 'atp': 'http://www.tennis-data.co.uk/{y}/{y}.xlsx'}


def _load_env() -> None:
    env = ROOT / '.env'
    if env.exists():
        for line in env.read_text(encoding='utf-8-sig').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env()
BOT_TOKEN = os.getenv('BOT_TOKEN', '')
SHEET_ID = os.getenv('SHEET_ID', '')                                  # ID Google Таблицы (из адреса)
GOOGLE_CREDENTIALS = os.getenv('GOOGLE_CREDENTIALS', str(ROOT / 'service_account.json'))
ALLOWED_USERS = {int(x) for x in os.getenv('ALLOWED_USERS', '').replace(' ', '').split(',') if x}
TESSERACT_CMD = os.getenv('TESSERACT_CMD', 'tesseract')
