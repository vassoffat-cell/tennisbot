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

# ставка: EV по модели не ниже порога, вероятность стороны не меньше минимума
EV_THRESHOLD = 0.05
ODDS_ZONE = (1.35, 1.65)   # зона кф, в которой предлагаются ставки: линии подбираются так, чтобы диапазон попадал сюда
TARGET_P = 0.75            # целевая вероятность стороны при выборе линии: 1,05/0,75 = 1,40 — внутри зоны
# Неопределённость модели: σ расхождения logit(P_модели) − logit(P_Pinnacle без маржи), вне выборки 2023–2024,
# по меньшему из чисел матчей игроков в базе. Верхняя граница кф = 1 / σ(logit P − SIGMA_Z·σ): большее расхождение
# с рынком, чем обычная ошибка модели, — скорее нехватка информации у модели (травма, мотивация), чем выгода.
SIGMA_BY_N = ((15, 0.76), (40, 0.52), (120, 0.46), (10 ** 9, 0.44))
SIGMA_Z = 1.0
MIN_PROB = 0.15

# авто-режим: часовой пояс сообщений, горизонт прогноза, источники
TZ_OFFSET_HOURS = 5                     # Ташкент, UTC+5
HORIZON_HOURS = 24                      # список и прогноз — матчи, начинающиеся в ближайшие 24 часа
LEVELS = {'Grand Slam', '1000', '500', '250', 'Finals'}   # категории турниров для авто-прогноза
RETRAIN_ANCHOR = _dt.date(2026, 11, 6)  # переобучение через день в 00:00 по Ташкенту, начиная с этой даты
CALIB_PRIOR = 300                       # сила априорного «доверия» исходной модели при самокалибровке (матчей)
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
GITHUB_TOKEN = os.getenv('GITHUB_TOKEN', '')      # отправка файлов tennis-data с компьютера в облако (ghsync.py)
GITHUB_REPO = os.getenv('GITHUB_REPO', '')        # например vassoffat-cell/tennisbot
