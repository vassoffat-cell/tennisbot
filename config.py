"""Настройки бота. Секреты (токен бота, ID таблицы) читаются из файла .env рядом с этим файлом."""
from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
SACKMANN = DATA / 'sackmann'          # wta_matches_2019.csv … , wta_players.csv, atp_…
USE_TML = True                        # T29: статистика подачи TennisMyLife после обрыва Sackmann (data/tml/)
TENNIS_DATA = DATA / 'tennis-data'    # 2019_WTA.xlsx … 2026_WTA.xlsx, 2019_ATP.xlsx …
STATE = ROOT / 'state'                # собранное состояние игроков и веса модели
LOGS = ROOT / 'logs'                  # запасной журнал CSV, если Google Таблица не настроена
FIRST_SEASON, LAST_SEASON = 2019, max(2026, _dt.date.today().year)   # новый сезон подхватывается сам

# ядро: K-фактор Elo, вес покрытия, momentum (в сете, между сетами) — см. документ по теннису, шаги 2–5
ELO_A, ELO_C = 75.0, 0.2
ELO_W_SURF = {'wta': 0.25, 'atp': 0.5}
MOMENTUM = {'wta': (0.071, 0.182), 'atp': (0.061, 0.187)}
# Elo по геймам (пакет №1, 08.11.2026; research/tennis_exp2/3.py): масштаб 300, K, вес покрытия; очки рейтинга — только WTA
GAMES_ELO = {'wta': dict(scale=300, K=32, w_surf=0.4, points=True), 'atp': dict(scale=300, K=48, w_surf=0.4, points=False)}
# модель очка на подаче T21/V4 (пакет №2; engine/research/T21.md): полураспад свежести, разброс сил игроков и поправок
# на покрытие (логит), прайор по рейтингу — всегда включён; параметры подобраны на 2021–22
SERVE_MODEL = {t: dict(half_days=365.0, sigma=0.10, sigma_s=0.03) for t in ('wta', 'atp')}

# линии: значение форы/тоталов — там, где P стороны ближе всего к TARGET_P (правило выбора, не фильтр);
# система независима от букмекеров (решение 07.11.2026): кф рынка в прогнозе не используются
EV_THRESHOLD = 0.05        # только для справочных столбцов kmin1/kmin2 журнала и модуля скриншотов (отключён)
MIN_PROB = 0.15
TARGET_P = 0.75
MIN_ODDS = 1.25            # линии с низким кф не показываются: 1/P ≥ 1,25 (P ≤ 0,80); исход — в каждом матче
P_SHOW = (0.50, 1 / MIN_ODDS)
# Неопределённость модели: σ расхождения logit(P_модели) − logit(P_Pinnacle без маржи), вне выборки 2023–2024,
# по меньшему из чисел матчей игроков в базе → уверенность P_low = σ(logit P − SIGMA_Z·σ)
SIGMA_BY_N = ((15, 0.76), (40, 0.52), (120, 0.46), (10 ** 9, 0.44))
SIGMA_Z = 1.0
# уверенность линии P_low: ≥ 0,70 — высокая 🟢, ≥ 0,65 — средняя 🟡, ниже — низкая ⚪
CONF_TIERS = (0.70, 0.65)
CALIB_MIN = 30             # самокалибровка рынка включается с 30 рассчитанных показанных линий (победа — 30 матчей)

# ступени точности (%, ориентиры, не потолок): следующая — когда нижняя граница 95% на последних 100 ≥ ступени
STEPS = {'outcome': (65.4, 67.9, 70.0, 72.5, 75.0, 80.0, 85.0, 90.0),
         'lines': (70.0, 72.5, 75.0, 77.5, 80.0, 85.0, 90.0, 95.0),
         'daypick': (78.0, 80.0, 85.0, 90.0, 95.0, 100.0)}
BREAKER_N, BREAKER_Z, BREAKER_BACK_Z = 30, -2.0, -1.0   # ⛔ рынок: z ≤ −2 на последних 30 → выключить; включить при z > −1
ALARM_N = 50                                            # 🚨 исход: z ≤ −2 на последних 50
TARGET_N = 550                                          # «до выводов»: матчей для точности ±4 п.п.
DAY_PICK_SWITCH = 0.02     # ⭐ ставка дня: замена только если новая лучше на ≥ 2 п.п.
DAY_PICK_LOCK_HOUR = 17    # после 17:00 (Ташкент) и после начала матча ставки — без замен
DAILY_HOUR = (0, 2)        # суточный отчёт, самообучение и переобучение: первый запуск после 00:00 (окно до 02:00)
FRESH_DAYS = 14            # файл tennis-data, где последний матч старше 14 дней, — устаревший
TENNIS_DATA_PAGE = 'http://www.tennis-data.co.uk/alldata.php'
NEWS = DATA / 'news.csv.gz'   # новости ESPN (травмы, снятия) — копятся для будущей проверки (T22)

# авто-режим: часовой пояс сообщений, горизонт прогноза, источники
TZ_OFFSET_HOURS = 5                     # Ташкент, UTC+5
HORIZON_HOURS = 24                      # список и прогноз — матчи, начинающиеся в ближайшие 24 часа
LEVELS = {'Grand Slam', '1000', '500', '250', 'Finals'}   # категории турниров для авто-прогноза
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
