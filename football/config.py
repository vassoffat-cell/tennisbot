"""Футбол: настройки. Секреты — из переменных окружения (облако) или football/.env."""
from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
MATCHES = DATA / 'matches.csv.gz'          # история football-data (+ результаты ESPN после последней даты файлов)
ESPN_STORE = DATA / 'espn_results.csv'     # все доигранные матчи ESPN (чемпионаты, кубки, еврокубки)
LOGS = ROOT / 'logs'

# модель Dixon–Coles (подобрано на 2023/24, проверено на 2024/25–2025/26)
XI = 0.002                 # затухание веса матча в день (полураспад ≈ 1 год)
RIDGE_SUP, RIDGE_TOT = 1.0, 12.0   # сжатие: разница сил / общий уровень голов
RHO = -0.06                # поправка Dixon–Coles на 0:0, 1:0, 0:1, 1:1
WINDOW_DAYS = 1460         # окно истории для подгонки

# ставки: зона кф, нижняя граница EV, ошибка модели против Pinnacle (σ, logit; отложенная выборка 10 874 матча)
EV_THRESHOLD = 0.05
ODDS_ZONE = (1.35, 1.65)
TARGET_P = 0.75
SIGMA = {'1X2': 0.38, 'DC': 0.38, 'AH': 0.38, 'TOT': 0.24, 'IT1': 0.24, 'IT2': 0.24, 'BTTS': 0.24}
# режим «рынок»: шум одной конторы против Pinnacle (σ logit; средний рынок против Pinnacle 2023–26: 1X2 0,05, тотал 0,04;
# одна контора шумнее — взято с запасом; производные рынки без котировок — 0,10)
SIGMA_MARKET = {'1X2': 0.08, 'DC': 0.08, 'TOT': 0.06, 'AH': 0.10, 'IT1': 0.10, 'IT2': 0.10, 'BTTS': 0.10}
CALIB_PRIOR = 300

TZ_OFFSET_HOURS = 5
HORIZON_HOURS = 24
RETRAIN_ANCHOR = _dt.date(2026, 11, 6)

# турниры ESPN: код → (название, страна, дивизион football-data для уровня голов и домашнего поля, тип)
# кубков Турции, России, Бельгии и Кубка лиги Португалии в ESPN нет (проверено по списку лиг 06.11.2026)
COMPETITIONS = {
    'eng.1': ('АПЛ', 'ENG', 'E0', 'league'), 'esp.1': ('Ла Лига', 'ESP', 'SP1', 'league'),
    'ita.1': ('Серия A', 'ITA', 'I1', 'league'), 'ger.1': ('Бундеслига', 'GER', 'D1', 'league'),
    'fra.1': ('Лига 1', 'FRA', 'F1', 'league'), 'por.1': ('Португалия', 'POR', 'P1', 'league'),
    'tur.1': ('Турция', 'TUR', 'T1', 'league'), 'rus.1': ('РПЛ', 'RUS', 'RUS', 'league'),
    'ned.1': ('Эредивизи', 'NED', 'N1', 'league'), 'bel.1': ('Бельгия', 'BEL', 'B1', 'league'),
    'sco.1': ('Шотландия', 'SCO', 'SC0', 'league'),
    'eng.fa': ('Кубок Англии', 'ENG', 'E0', 'cup'), 'eng.league_cup': ('Кубок лиги Англии', 'ENG', 'E0', 'cup'),
    'eng.charity': ('Суперкубок Англии', 'ENG', 'E0', 'super'),
    'esp.copa_del_rey': ('Кубок Испании', 'ESP', 'SP1', 'cup'), 'esp.super_cup': ('Суперкубок Испании', 'ESP', 'SP1', 'super'),
    'ita.coppa_italia': ('Кубок Италии', 'ITA', 'I1', 'cup'), 'ita.super_cup': ('Суперкубок Италии', 'ITA', 'I1', 'super'),
    'ger.dfb_pokal': ('Кубок Германии', 'GER', 'D1', 'cup'), 'ger.super_cup': ('Суперкубок Германии', 'GER', 'D1', 'super'),
    'fra.coupe_de_france': ('Кубок Франции', 'FRA', 'F1', 'cup'), 'fra.super_cup': ('Суперкубок Франции', 'FRA', 'F1', 'super'),
    'por.taca.portugal': ('Кубок Португалии', 'POR', 'P1', 'cup'),
    'ned.cup': ('Кубок Нидерландов', 'NED', 'N1', 'cup'), 'ned.supercup': ('Суперкубок Нидерландов', 'NED', 'N1', 'super'),
    'sco.tennents': ('Кубок Шотландии', 'SCO', 'SC0', 'cup'), 'sco.cis': ('Кубок лиги Шотландии', 'SCO', 'SC0', 'cup'),
    'uefa.champions': ('Лига чемпионов', None, None, 'uefa'), 'uefa.europa': ('Лига Европы', None, None, 'uefa'),
    'uefa.europa.conf': ('Лига конференций', None, None, 'uefa'), 'uefa.super_cup': ('Суперкубок УЕФА', None, None, 'uefa_super'),
}
# априорная сила стран (лог-шкала голов) для еврокубков; уточняется по результатам еврокубков из ESPN
COUNTRY_PRIOR = {'ENG': 0.30, 'ESP': 0.25, 'ITA': 0.22, 'GER': 0.22, 'FRA': 0.12, 'POR': 0.08, 'NED': 0.05,
                 'BEL': 0.0, 'TUR': -0.04, 'SCO': -0.12, 'GRE': -0.06, 'RUS': 0.0}
UEFA_BACKFILL_FROM = _dt.date(2025, 7, 1)   # еврокубки сезона 2025/26 из ESPN (раньше — openfootball, CC0)
UEFA_HIST = DATA / 'uefa_hist.csv'          # openfootball/champions-league (CC0), 2019/20–2025/26, счёт 90 минут
LINEUPS = DATA / 'lineups.csv.gz'           # составы ESPN доигранных матчей (этап 2: проверка фактора «основа»)

# ситуационные факторы (research/oos_gain.py, 41 480 матчей 08.2020–01.2026, совместная пуассоновская регрессия)
# высшие дивизионы для «нечего играть»: (последнее еврокубковое место, мест в зоне вылета вкл. стыки)
TOPDIV = {'E0': (6, 3), 'SP1': (6, 3), 'I1': (6, 3), 'D1': (6, 3), 'F1': (4, 3), 'P1': (4, 3), 'N1': (4, 3), 'T1': (4, 3),
          'RUS': (4, 4)}
# поправка log λ к модели (против модели): соперник команды «нечего играть» +16,0% (p<0,001) и др.
FACTORS_MODEL = {'const': 0.0078, 'own_eu_prev': -0.0406, 'opp_eu_prev': 0.0345, 'own_eu_next_ko': -0.0340,
                 'opp_eu_next_ko': 0.0034, 'own_dead': -0.0118, 'opp_dead': 0.1483, 'own_short': 0.0103,
                 'opp_short': -0.0415, 'both_short': -0.0216}
EU_PREV_MARKET = -0.0464    # против Pinnacle: команда после еврокубка (≤4 дня) забивает на 4,5% меньше (p=0,005)

FD_PAGES = ('https://www.football-data.co.uk/downloadm.php', 'https://www.football-data.co.uk/russia.php')
LEAGUE_BACKFILL_DAYS = 120   # результаты чемпионатов из ESPN после последней даты файлов football-data (не глубже)


def _load_env() -> None:
    env = ROOT / '.env'
    if env.exists():
        for line in env.read_text(encoding='utf-8-sig').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env()
BOT_TOKEN = os.getenv('FOOT_BOT_TOKEN', '')
SHEET_ID = os.getenv('FOOT_SHEET_ID', '')
GOOGLE_CREDENTIALS = os.getenv('GOOGLE_CREDENTIALS', str(ROOT.parent / 'service_account.json'))
ALLOWED_USERS = {int(x) for x in os.getenv('ALLOWED_USERS', '').replace(' ', '').split(',') if x}
INBOX_FOLDER = 'footbot_inbox'
