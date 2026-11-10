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
SHOT_W, SHOT_CONV = 0.5, 0.351   # F2b: вес голов в разнице сил и конверсия «голы / удары в створ» (57 278 матчей)

# линии: значение форы/тоталов выбирается там, где вероятность стороны ближе всего к TARGET_P (правило выбора, не фильтр);
# система независима от букмекеров (решение 07.11.2026): кф рынка в прогнозе не используются.
TARGET_P = 0.75
MIN_ODDS = 1.25            # линии с низким кф не показываются: справедливый кф 1/P ≥ 1,25 (P ≤ 0,80); исход — в каждом матче
P_SHOW = (0.50, 1 / MIN_ODDS)
# ошибка модели (σ, logit; отложенная выборка 10 874 матча) — для уверенности P_low = σ(logit P − σ)
SIGMA = {'1X2': 0.38, 'DC': 0.38, 'AH': 0.38, 'TOT': 0.24, 'IT1': 0.24, 'IT2': 0.24, 'BTTS': 0.24}
CALIB_PRIOR = 300
CALIB_MIN = 30             # самокалибровка рынка включается с 30 рассчитанных показанных линий
# уверенность линии P_low: ≥ 0,70 — высокая 🟢, ≥ 0,65 — средняя 🟡, ниже — низкая ⚪
CONF_TIERS = (0.70, 0.65)

# ступени точности (%, ориентиры, не потолок): следующая берётся, когда нижняя граница 95% интервала на последних 100 ≥ ступени
STEPS = {'outcome': (49.5, 50.8, 53.0, 55.0, 58.0, 60.0, 65.0, 70.0),
         'lines': (70.0, 72.5, 75.0, 77.5, 80.0, 85.0, 90.0, 95.0),   # показанные линии: P 0,50–0,80, в среднем ≈ 0,70
         'daypick': (78.0, 80.0, 85.0, 90.0, 95.0, 100.0)}
BREAKER_N, BREAKER_Z, BREAKER_BACK_Z = 30, -2.0, -1.0   # ⛔ рынок: z ≤ −2 на последних 30 → выключить; включить при z > −1
ALARM_N = 50                                            # 🚨 исход: z ≤ −2 на последних 50
TARGET_N = 550                                          # «до выводов»: матчей для точности ±4 п.п.

# ⭐ ставка дня (решение 08.11.2026): одна самая уверенная показанная линия на матчи текущих суток, кф ≥ MIN_ODDS
DAY_PICK_SWITCH = 0.02     # заменить только если новая лучше на ≥ 2 п.п.
DAY_PICK_LOCK_HOUR = 17    # после 17:00 (Ташкент) и после начала матча ставки — без замен


TZ_OFFSET_HOURS = 5
HORIZON_HOURS = 24

# турниры ESPN (19 по списку пользователя 07.11.2026; Кубков России и Турции в ESPN нет — 17 запросов):
# код → (название, страна, дивизион football-data для уровня голов и домашнего поля, тип)
COMPETITIONS = {
    'eng.1': ('АПЛ', 'ENG', 'E0', 'league'), 'eng.fa': ('Кубок Англии', 'ENG', 'E0', 'cup'),
    'esp.1': ('Ла Лига', 'ESP', 'SP1', 'league'), 'esp.copa_del_rey': ('Кубок Испании', 'ESP', 'SP1', 'cup'),
    'fra.1': ('Лига 1', 'FRA', 'F1', 'league'), 'fra.coupe_de_france': ('Кубок Франции', 'FRA', 'F1', 'cup'),
    'ger.1': ('Бундеслига', 'GER', 'D1', 'league'), 'ger.dfb_pokal': ('Кубок Германии', 'GER', 'D1', 'cup'),
    'ita.1': ('Серия A', 'ITA', 'I1', 'league'), 'ita.coppa_italia': ('Кубок Италии', 'ITA', 'I1', 'cup'),
    'por.1': ('Примейра', 'POR', 'P1', 'league'), 'por.taca.portugal': ('Кубок Португалии', 'POR', 'P1', 'cup'),
    'ned.1': ('Эредивизи', 'NED', 'N1', 'league'), 'tur.1': ('Суперлига Турции', 'TUR', 'T1', 'league'),
    'uefa.champions': ('Лига чемпионов', None, None, 'uefa'), 'uefa.europa': ('Лига Европы', None, None, 'uefa'),
    'uefa.europa.conf': ('Лига конференций', None, None, 'uefa'),
}
# 10.11.2026: РПЛ исключена (football-data RUS не обновляется с 02.08, нет ударов в створ) → Эредивизи (N1 из all-euro-data:
# фаворит Pinnacle 56,8% — 3-я по предсказуемости лига файла, 3,14 гола/матч, удары в створ 100%). Данные RUS остаются в базе.
# данные BEL/SCO/GRE из football-data остаются в модели — для силы соперников в еврокубках

# априорная сила стран (лог-шкала голов) для еврокубков; уточняется по результатам еврокубков из ESPN
COUNTRY_PRIOR = {'ENG': 0.30, 'ESP': 0.25, 'ITA': 0.22, 'GER': 0.22, 'FRA': 0.12, 'POR': 0.08, 'NED': 0.05,
                 'BEL': 0.0, 'TUR': -0.04, 'SCO': -0.12, 'GRE': -0.06, 'RUS': 0.0}
UEFA_BACKFILL_FROM = _dt.date(2025, 7, 1)   # еврокубки сезона 2025/26 из ESPN (раньше — openfootball, CC0)
UEFA_HIST = DATA / 'uefa_hist.csv'          # openfootball/champions-league (CC0), 2019/20–2025/26, счёт 90 минут
FD_FILES_REQUIRED = False                  # T30 (10.11.2026): файл football-data не обязателен — ESPN (результаты + удары)
SHOTS = DATA / 'shots.csv.gz'              # T30: удары в створ из ESPN summary (замена football-data)
LINEUPS = DATA / 'lineups.csv.gz'           # составы ESPN доигранных матчей (этап 2: проверка фактора «основа»)

# ситуационные факторы (research/oos_gain.py, 41 480 матчей 08.2020–01.2026, совместная пуассоновская регрессия)
# высшие дивизионы для «нечего играть»: (последнее еврокубковое место, мест в зоне вылета вкл. стыки)
TOPDIV = {'E0': (6, 3), 'SP1': (6, 3), 'I1': (6, 3), 'D1': (6, 3), 'F1': (4, 3), 'P1': (4, 3), 'N1': (4, 3), 'T1': (4, 3),
          'RUS': (4, 4)}
# поправка log λ к модели (против модели): соперник команды «нечего играть» +16,0% (p<0,001) и др.
FACTORS_MODEL = {'const': 0.0078, 'own_eu_prev': -0.0406, 'opp_eu_prev': 0.0345, 'own_eu_next_ko': -0.0340,
                 'opp_eu_next_ko': 0.0034, 'own_dead': -0.0118, 'opp_dead': 0.1483, 'own_short': 0.0103,
                 'opp_short': -0.0415, 'both_short': -0.0216}

# файлы football-data: прямые ссылки (сезон с июля) и страница со списком — на случай, если ссылку сменят
FD_LINKS = {'EURO': ('all-euro-data-{y}-{y1}.xlsx', 'https://www.football-data.co.uk/mmz4281/{yy}{yy1}/all-euro-data-{y}-{y1}.xlsx')}
# RUS.xlsx больше не нужен (10.11.2026: Россия исключена)
FD_PAGE = 'https://www.football-data.co.uk/downloadm.php'
FRESH_DAYS = 14            # файл, где последний матч старше 14 дней, — устаревший
NEWS = DATA / 'news.csv.gz'  # новости ESPN (смена тренера, травмы) — копятся для будущей проверки (F4)
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
TESSERACT_CMD = os.getenv('TESSERACT_CMD', 'tesseract')     # скриншоты 1xBet (пакет №3)
INBOX_FOLDER = 'footbot_inbox'

# сутки системы (решение пользователя 08.11.2026): 08:00 → 08:00 Ташкента. 08:00 (футбол 08:30) — переобучение, ⭐ ставка дня
# первым сообщением, список на сутки (сужается по мере игры, правится каждые 2 ч); 11:00 / 11:30 — отчёт за прошлые сутки,
# самокалибровка k и самообучение (3 ч запаса на доигрывание матчей, начатых до 08:00).
DAY_START_HOUR = 8
START_HOURS = (8, 11)      # первый запуск новых суток (с запасом на задержку расписания GitHub)
DAILY_HOUR = (11, 13)      # суточный отчёт: теннис 11:00, футбол 11:30


def wday(loc) -> _dt.date:
    """Дата суток системы для местного времени (00:00–07:59 — ещё прошлые сутки)."""
    return (loc - _dt.timedelta(hours=DAY_START_HOUR)).date()


def window_end_utc(now_utc):
    """Конец текущих суток системы (ближайшие 08:00 Ташкента) в UTC — горизонт списка прогноза."""
    loc = now_utc + _dt.timedelta(hours=TZ_OFFSET_HOURS)
    end = _dt.datetime.combine(wday(loc) + _dt.timedelta(days=1), _dt.time(DAY_START_HOUR))
    return end - _dt.timedelta(hours=TZ_OFFSET_HOURS)
