"""Тесты регрессии каперов и доставки (ORG IMP-05, 23.11.2026).

Каждый случай — реальная ошибка из журнала CONTROL §3: если она вернётся, тест упадёт ДО push.
Запуск (сеть не нужна, файлы данных не меняются):  cd football && PYTHONDONTWRITEBYTECODE=1 python3 test_regress.py
Новая ошибка каперов → новый случай сюда, в том же коммите, что и исправление (ORG §2, быстрая дорога).
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import tips as T          # noqa: E402
import tipsbook as B      # noqa: E402

FAILS: list[str] = []


def check(name: str, got, want) -> None:
    """Сравнение «получено ↔ ожидается»; провал копится, итог — в конце."""
    if got != want:
        FAILS.append(f'❌ {name}: получено {got!r}, ожидалось {want!r}')


def _notify(path: Path):
    """notify.py корня и футбола — разные модули с одним именем: грузим по пути."""
    spec = importlib.util.spec_from_file_location(f'notify_{path.parent.name}', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# 21.11 · Тоттенхэм — Ипсвич ушёл в хоккей («трактористы» → «Трактор»): клубы КХЛ — только целым словом
check('вид спорта: Тоттенхэм — Ипсвич не хоккей', B.sport_of('Тоттенхэм — Ипсвич ТБ 2.5') != 'hockey', True)
check('вид спорта: Трактор — Авангард', B.sport_of('Трактор — Авангард П1'), 'hockey')
# 21.11 · «Локомотив Ярославль» — хоккей, не футбольный «Локомотив»
check('вид спорта: Локомотив Ярославль', B.sport_of('Локомотив Ярославль — СКА П1'), 'hockey')
# 21.11 · теннисный купон Green Line ушёл в ⚽: «по геймам» — явный признак тенниса
check('вид спорта: фора по геймам', B.sport_of('Алькарас — Шелтон Фора 1 по геймам (-2.5)'), 'tennis')
check('вид спорта: явный ⚽', B.sport_of('⚽ Динамо — Спартак П1'), 'football')
check('вид спорта: явный 🏒', B.sport_of('🏒 Динамо — Спартак П1'), 'hockey')

# 21.11 · теннисные игроки по имени и фамилии (data/seal/tennis_people.json — есть в sparse checkout каперов)
check('теннис: Алькарас — Шелтон', B._tennis_players('Карлос Алькарас', 'Бен Шелтон'), True)
check('теннис: клубы не игроки', B._tennis_players('Реал Мадрид', 'ЦСКА Москва'), False)

# 21.11 · «МанСити» не найден → псевдонимы клубов
for a in ('мансити', 'манюнайтед', 'манчестер юнайтед'):
    check(f'псевдоним «{a}»', a in T.ALIAS, True)

# 21.11 · формат 1xBet (Каппер UK): точка после слова рынка
check('1xBet «Тотал. больше 2.5»', T.parse_legs('Тотал. больше 2.5'), [('TOT', 'O', 2.5)])
check('1xBet «Тотал. (2) Б»', T.parse_legs('Тотал. (2) Б', True, ('Арсенал', 'Челси')), [('TOT', 'O', 2.0)])
check('1xBet «Фора. 2 (1.5)»', T.parse_legs('Фора. 2 (1.5)', True, ('Арсенал', 'Челси')), [('AH', 'A', 1.5)])
check('ОЗ «Обе забьют да»', T.parse_legs('Обе забьют да'), [('BTTS', 'Y', '')])

# 21.11 · ставки на таймы (К17) — рынок тайма не теряется
check('тайм: «1-й тайм ТБ 1.5»', T.parse_legs('1-й тайм ТБ 1.5'), [('TOT@1', 'O', 1.5)])
check('тайм: «2-й тайм П1»', T.parse_legs('2-й тайм П1', True, ('Арсенал', 'Челси')), [('1X2@2', 'H', '')])
check('тайм: «Гол в обоих таймах да»', T.parse_legs('Гол в обоих таймах да', True, ('Арсенал', 'Челси')), [('GBH', 'Y', '')])

# 19.11 · отзывы клиентов и видео-отчёты — не прогнозы
p = dict(id=1, ts=pd.Timestamp('2026-11-23'), text='Отзывы клиентов 🔥 П1', ocr='', photos=[])
check('отзыв — не прогноз', B.tips_of('stavki', p), [])

# 23.11 · OCR картинок — все 5 каналов (решение пользователя)
check('OCR: все каналы', B.OCR_IMG >= set(T.TG), True)

# 23.11 · скрытый текст (В3б): <pre>/<code> — простым текстом, всё под spoiler, в обоих ботах
for path in (HERE.parent / 'notify.py', HERE / 'notify.py'):
    N = _notify(path)
    out = N.hide('<b>📈 отчёт</b>\n<pre>сутки 5/5</pre>\n<blockquote expandable>матчи</blockquote>')
    check(f'{path.parent.name}/notify: нет <pre>', '<pre>' in out, False)
    check(f'{path.parent.name}/notify: всё скрыто', out.count('<tg-spoiler>'), 2)

# 24.11 · хоккейные купоны 1xBet (Каппер UK, КХЛ) шли «⬜ матч не найден»: парсер хоккея не знал «Тотал. (5.5) М»
sys.path.insert(0, str(HERE.parent))
from hockey import hcappers as HC   # noqa: E402
check('хоккей 1xBet «Тотал. (5.5) М»', HC.parse('Тотал. (5.5) М кф 1.53', 'Сибирь', 'Северсталь'), ('TOT', 'U', 5.5))
check('хоккей 1xBet «Инд. тотал 2-го. (2) Б»', HC.parse('Индивидуальный тотал 2-го. (2) Б кф 1.465', 'Сибирь', 'Северсталь'),
      ('IT2', 'O', 2.0))
check('хоккей «ТМ 5.5» не сломан', HC.parse('ТМ 5.5', 'Сибирь', 'Северсталь'), ('TOT', 'U', 5.5))

# 24.11 · Грозный «Инд. Тотал Наполи больше (0.5)» шёл «рынок вне модели»: команда между «тотал» и «больше»
check('ИТ с командой внутри', T.parse_legs('Инд. Тотал Наполи больше (0.5)', None, ('Manchester City', 'Napoli'), True),
      [('IT2', 'O', 0.5)])
check('ИТ без команды не сломан', T.parse_legs('Инд. тотал больше 1.5', True, ('A', 'B'), True), [('IT1', 'O', 1.5)])
# 24.11 · купон 1WIN Green Line «Результат основного времени, Арсенал» шёл «❔ ставка не разобрана»
import slips as S  # noqa: E402
c = S.parse('24 ноября 2026 г. в 07:12 ID 411649759 &\nОрдинар 1.43\nАрсенал — Боруссия Дортмунд\n'
            'Результат основного времени, Арсенал\nСтавка / Возможный выигрыш\n30 000,00 RUB » 42 900,00 RUB\nПродать за 30 000,00 RUB')
check('1WIN «Результат основного времени»', (c or {}).get('events', [{}])[0].get('short'), 'победа арсенал')
check('1WIN открытый купон', (c or {}).get('status'), 'open')
# 24.11 · рассчитанный купон на картинке («выплачена», «Выигрыш») — отчёт, не прогноз (Грозный 13149)
p = dict(id=2, ts=pd.Timestamp('2026-11-24'), text='Вчерашний послеоплатник залетел без валидола. Поздравляю всех',
         ocr='Экспресс\nСтатус: © выплачена\nМонца 3:0 Фиорентина\nТотал. (2.5) Б 1.631\nСтатус: © Выигрыш', photos=['x'])
check('рассчитанный купон — не прогноз', B.tips_of('groznyi_ruslan', p), [])

# 24.11 · каперы «матч не найден» при матче на табло: транслит, женские команды, «Сити»
import tipswide as W  # noqa: E402
def _pair(a, b, h, w):
    ev = [dict(id='1', home=h, away=w, start='2026-11-22 14:00')]
    return W.match_pair(a, b, ev) is not None
check('табло: Халл Сити = Hull City', _pair('Халл Сити', 'Брайтон энд Хав Альбион', 'Hull City', 'Brighton & Hove Albion'), True)
check('табло: Бадахос = Badajoz', _pair('Бадахос', 'Тенерифе', 'Badajoz', 'Tenerife'), True)
check('табло: Хеккен (жен) = BK Häcken', _pair('Хеккен (жен)', 'Манчестер Сити (жен)', 'BK Häcken', 'Manchester City'), True)
check('табло: Лион (ж) = OL Lyonnes', _pair('Арсенал Лондон (ж)', 'Лион (ж)', 'Arsenal', 'OL Lyonnes'), True)
check('табло: Халл Сити ≠ Man City', _pair('Халл Сити', 'Тоттенхэм', 'Manchester City', 'Tottenham Hotspur'), False)
check('табло: U21 не путается со взрослыми', _pair('Англия U21', 'Франция U21', 'England', 'France'), False)

# 24.11 · Green Line «GREEN СТАВКА - 🔴🔴🔴🔴 … помойка Марсель» — реакция на проигрыш, не прогноз (было «❔»)
p = dict(id=3, ts=pd.Timestamp('2026-11-23'), text='✖️✖️✖️ GREEN СТАВКА - 🔴🔴🔴🔴 Какая же помойка Марсель!', ocr='', photos=['x'])
check('реакция на проигрыш — не кандидат', B._candidate(p), False)
# 24.11 · «Статистика платных ставок: 21 победа | 6 минусов» стала прогнозом «21 победа» (14.11)
p = dict(id=4, ts=pd.Timestamp('2026-11-14'), text='Статистика платных ставок:\n21 победа | 6 минусов⬇️\n1.91✅1.39✅', ocr='', photos=[])
check('статистика канала — не прогноз', [t['pick'] for t in B.tips_of('stavki_na_sport_betting1', p) if not t['pick'].startswith('❔')], [])

# 24.11 вечер · пост-статистика «О ЧЁМ ГОВОРЯТ ЦИФРЫ» дал 2 «прогноза» (Хитрости 8051)
p = dict(id=5, ts=pd.Timestamp('2026-11-24'), text='🏆 ЛИГА ЧЕМПИОНОВ | О ЧЁМ ГОВОРЯТ ЦИФРЫ?\nФейеноорд — Порту\n• забивалось минимум 3 гола', ocr='', photos=[])
check('пост-статистика — не прогноз', B.tips_of('xs_stavkii', p), [])
# 24.11 вечер · экспресс 1WIN на двух картинках: шапка «Футбол e Европа», вторая картинка без слова «Экспресс», «1-й тайм» в рынке
ocr = ('Экспресс 3.22\nСумма ставки 100 000,00 RUB\nВозможный выигрыш\n322 000,00 RUB\nПродать 3a 88 000,00 RUB\nСобытия 2\n'
       'Футбол e Европа e Лига Чемпионов УЕФА\nРеал Мадрид\nПСВ Эйндховен\n24 ноября, 23:00\n1-й тайм. Индивидуальный тотал Реал\n'
       'Мадрид, Больше 1.0\n§\nФутбол « Европа « Лига Чемпионов УЕФА\nКомо\nАЕК Афины\n24 ноября, 23:00\nФора, Комо -1')
rows = B.slip_rows('stavki_na_sport_betting1', dict(id=6, ts=pd.Timestamp('2026-11-24'), text='', ocr=ocr))
check('1WIN экспресс: обе картинки', [(r['a'], r['pick']) for r in rows],
      [('Реал Мадрид', '1-й тайм реал мадрид ИТБ(1.0)'), ('Комо', 'комо фора (-1)')])
# 24.11 вечер · экспресс 1xBet «A VS B» (Каппер UK 22772): обе ноги с парами
ocr = 'Футбол. Лига Чемпионов УЕФА\nБудё-Глимт VS til ЛАСК\nТотал. (4.5) М 1.504\nСтатус: Принята\nс AVFC\nГалатасарай VS Астон Вилла\nТотал. (2.5) Б 1.8\nСтатус: Принята'
rows = B.tips_of('kapper_uk', dict(id=7, ts=pd.Timestamp('2026-11-24'), text='ЭКСПРЕСС С КОЭФ 2.5 🔥', ocr=ocr, photos=['x']))
check('1xBet экспресс VS: пары', [(r['a'], r['b']) for r in rows], [('Будё-Глимт', 'ЛАСК'), ('Галатасарай', 'Астон Вилла')])

# 24.11 · вердикт капера на наш матч — та же P, что у ставки дня (Комо Ф1(−1): ⭐-кандидат 77%, вердикт показывал «⚔️ 34%»)
import numpy as np  # noqa: E402
B._SHADOW['m'] = {'1': [dict(market='AH', pick='H', line='-1.0', p='0.7748', p_mkt='0.733', mkt_src='pinnacle')]}
Pm = np.full((6, 6), 1 / 36)
check('вердикт: P ставки дня для той же линии', round(B._sys_p('1', Pm, [('AH', 'H', -1.0)]), 4), 0.7748)
check('вердикт: без цен — модель', round(B._sys_p('2', Pm, [('1X2', 'H', '')]), 4), round(T.p_legs(Pm, [('1X2', 'H', '')]), 4))
B._SHADOW['h'] = {'nhl:9': [dict(market='TOT', pick='O', line='5.5', p='0.5647')]}
check('хоккей: P ставки дня для той же линии', B._hk_sys_p('nhl:9', 'TOT', 'O', 5.5), 0.5647)
check('хоккей: нет цены — модель', B._hk_sys_p('khl:1', 'TOT', 'O', 5.5), None)
B._SHADOW.clear()

# 24.11 · Каппер UK: кф одиночного купона из «Коэффициент: 1.57» (OCR прочитал «1:57»)
p = dict(id=8, ts=pd.Timestamp('2026-11-22'), text='Брентфорд — Эвертон | Футбол. Англия', ocr='Коэффициент: 1.57\nБрентфорд VS Эвертон\nТотал. (2.5) Б 1:57\nСтатус: Принята', photos=['x'])
check('Каппер UK: кф из «Коэффициент»', [t['pick'] for t in B.tips_of('kapper_uk', p)], ['Тотал. (2.5) Б 1:57 кф 1.57'])

# 23.11 · сверка ⭐ ↔ снимок суток (ORG IMP-08): та же линия — ✅, чужая — «⭐ не в снимке»
import tempfile                    # noqa: E402
for seal_path in (HERE.parent / 'seal.py', HERE / 'seal.py'):     # у футбола своя копия seal.py (урок 23.11)
  spec = importlib.util.spec_from_file_location(f'seal_{seal_path.parent.name}', seal_path)
  SEAL = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(SEAL)
  with tempfile.TemporaryDirectory() as tmp:
      (Path(tmp) / 'seal').mkdir()
      (Path(tmp) / 'seal' / 'football_2026-11-23.csv').write_text(
          'match_id,start_utc,group,market,pick,line,p\n401888370,2026-11-23 17:00,Суперлига Турции,TOT,O,2.5,0.61\n', encoding='utf-8')
      dp = pd.DataFrame([dict(date='2026-11-23', espn_id='401888370', market='TOT', pick='O', line='2.5')])
      check(f'{seal_path.parent.name}/seal: ⭐ в снимке', SEAL.verify(dp, Path(tmp), 'football', 'espn_id').endswith('1 из 1 ✅'), True)
      dp.loc[0, 'line'] = '3.5'
      check(f'{seal_path.parent.name}/seal: ⭐ вне снимка', SEAL.verify(dp, Path(tmp), 'football', 'espn_id').startswith('🔏 ⭐ не в снимке'), True)

# 25.11 · купон после начала (⚠️) без вердикта получал «⬜ матч не найден» (Комо — АЕК 24.11 20:31) → точная причина
check('⚠️ матч уже идёт — причина', B.no_verdict('⚠️ матч уже идёт'), '· матч уже идёт — не оценивается')
check('🔴 после начала — причина', B.no_verdict('🔴 опубликован в 21:16'), '· после начала — не оценивается')
check('⏰ до матча — без причины', B.no_verdict('⏰ начало 22:45'), None)

# 25.11 · Р8 (решение 11:37): до 11:00 итоги, 11:15 отчёт + самообучение, 11:30 ⭐1; хоккей 12:15 / 12:30; обе копии dpcore
for dpc_path in (HERE.parent / 'dpcore.py', HERE / 'dpcore.py'):
    spec = importlib.util.spec_from_file_location(f'dpc_{dpc_path.parent.name}', dpc_path)
    DPC = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(DPC)
    tag = dpc_path.parent.name
    T_ = lambda hm: pd.Timestamp(f'2026-11-26 {hm}')               # четверг
    PW = lambda rt, hm: DPC.pick_wait(rt, T_(hm), '2026-11-26', (11, 15), (11, 30), 2, True)
    check(f'{tag} Р8: 09:00 — ждём 11:30', PW({}, '09:00').startswith('ждём старт'), True)
    check(f'{tag} Р8: 11:20 отчёт ушёл — всё равно ждём 11:30', PW({'last_daily': '2026-11-26'}, '11:20').startswith('ждём старт'), True)
    check(f'{tag} Р8: 11:31 отчёт ушёл — выбирать', PW({'last_daily': '2026-11-26'}, '11:31'), '')
    check(f'{tag} Р8: 11:31 отчёта нет — ждём отчёт', PW({}, '11:31').startswith('ждём отчёт'), True)
    check(f'{tag} Р8: 13:30 отчёта нет — ⭐ без него', PW({}, '13:30').startswith('⚠️'), True)
    check(f'{tag} Р8: 11:31 отчёт упал — ⭐ сразу', PW({'rep_fail': '2026-11-26'}, '11:31').startswith('⚠️'), True)
    check(f'{tag} Р8: ⭐ уже объявлена — не ждём', PW({'day_pick': {'date': '2026-11-26'}}, '09:00'), '')
    check(f'{tag} Р8: ночь пт 02:05 (сутки чт без ⭐) — ⚠️', DPC.pick_wait({}, pd.Timestamp('2026-11-27 02:05'), '2026-11-26', (11, 15), (11, 30), 2, True).startswith('⚠️'), True)
    check(f'{tag} Р8: флаг выключен — не ждём', DPC.pick_wait({}, T_('09:00'), '2026-11-26', (11, 15), (11, 30), 2, False), '')
import datetime as _dt2  # noqa: E402
from hockey import hrun as HR  # noqa: E402
_c, _y = _dt2.date(2026, 11, 26), _dt2.date(2026, 11, 25)
check('🏒 Р8: 12:20 — ждём 12:30', HR.hk_hold({}, _c, _y, pd.Timestamp('2026-11-26 12:20')).startswith('ждём старт'), True)
check('🏒 Р8: 12:31 отчёт ушёл — выбирать', HR.hk_hold({'hk_last_daily': '2026-11-25'}, _c, _y, pd.Timestamp('2026-11-26 12:31')), '')
check('🏒 Р8: 12:31 отчёта нет — ждём', HR.hk_hold({}, _c, _y, pd.Timestamp('2026-11-26 12:31')).startswith('ждём отчёт'), True)
check('🏒 Р8: 14:30 отчёта нет — ⚠️', HR.hk_hold({}, _c, _y, pd.Timestamp('2026-11-26 14:30')).startswith('⚠️'), True)
check('🏒 Р8: 🏒1 уже есть — не ждём', HR.hk_hold({'hk_dp': {'date': '2026-11-26'}}, _c, _y, pd.Timestamp('2026-11-26 12:20')), '')
_src = (HERE.parent / 'hockey' / 'hrun.py').read_text(encoding='utf-8')
check('🏒 Р8: теневой портфель после HP.process (SHADOW_ROWS заполняет process)', _src.index('HP.shadow(pd.Timestamp(now)') > _src.index('HP.process(store'), True)
check('🏒 Р8: health не путает сбой отчёта хоккея с отправкой', bool(re.search(r'🏒 суточный отчёт \d\d\.\d\d отправлен', '🏒 суточный отчёт: сбой KeyError')), False)

# 25.11 · Р8 (проверка 09): исключение в отчёте не блокирует ⭐1, повтор отчёта не чаще раза в час
sys.path.insert(0, str(HERE))
import run as FR  # noqa: E402
lg = []
_now = pd.Timestamp.now(tz='UTC')
check('Р8: сбой только что — повтор позже', FR._rep_hold({'rep_fail': 'd', 'rep_fail_at': _now.isoformat()}, 'd', lg), True)
check('Р8: сбой час назад — повторить', FR._rep_hold({'rep_fail': 'd', 'rep_fail_at': (_now - pd.Timedelta(minutes=61)).isoformat()}, 'd', lg), False)
check('Р8: сбоя не было — отчёт', FR._rep_hold({}, 'd', lg), False)

# 25.11 · вердикт капера: команд в ставке нет, обе названы в разборе → матч найден (как итог _prose); одна команда — не угадываем
_evs = [dict(id='1', start='2026-11-22 17:30', home='Juventus', away='Napoli', kind='', hg=None, ag=None),
        dict(id='2', start='2026-11-22 20:00', home='Brentford', away='Everton', kind='', hg=None, ag=None)]
check('разбор: обе команды → событие', (W.prose_event('Чемпионат Италии. ✅ Прогноз: ТБ (2.5) ☑️ Ювентус дома силён против Наполи', _evs) or {}).get('id'), '1')
check('разбор: одна команда → нет', W.prose_event('✅ Прогноз: ТБ (2.5) ☑️ Ювентус дома силён', _evs), None)

check('книга: текст поста не режется на 200 знаках (соперник в разборе)', B.RAW_MAX >= 1000, True)

# 25.11 · еврокубок с командой вне модели → кандидат ⭐ по чистой P Pinnacle (без весов модели), итог — по табло ESPN
import types as _types  # noqa: E402
import daypick as FDP   # noqa: E402
import pick as FPK      # noqa: E402
_st = pd.Timestamp.now(tz='UTC').tz_localize(None).normalize() + pd.Timedelta(hours=20)
_m = _types.SimpleNamespace(id='999001', comp='uefa.europa', start=_st, home='Marseille', away='Levski Sofia')
_row = dict(espn_id='999001', comp='uefa.europa', comp_name='Лига Европы', start_utc=_st.strftime('%Y-%m-%d %H:%M'), start_local='', home='Marseille', away='Levski Sofia')
_ev = dict(id='e1', sport_key='soccer_uefa_europa_league', home_team='Marseille', away_team='Levski Sofia', commence_time=_st.strftime('%Y-%m-%dT%H:%M:%SZ'),
           bookmakers=[dict(key=b, markets=[dict(key='h2h', outcomes=[dict(name='Marseille', price=ph), dict(name='Draw', price=pd_), dict(name='Levski Sofia', price=pa)])])
                       for b, ph, pd_, pa in (('pinnacle', 1.40, 4.90, 8.00), ('onexbet', 1.42, 4.80, 7.50))])
_save = (FDP.OD.sports, FDP.OD.day_events, FDP.OD.r_ratio, FDP.C.wday)
FDP.OD.sports = lambda: [dict(key='soccer_uefa_europa_league', active=True)]
FDP.OD.day_events = lambda *a, **k: ([_ev], [])
FDP.OD.add_alternates = lambda *a, **k: (None, '')   # без лишних кредитов API в тесте
FDP.MARKET_ONLY[:] = [(_m, _row)]
_day = FDP.C.wday(_st + pd.Timedelta(hours=FDP.C.TZ_OFFSET_HOURS))
_lg = []
try:
    _o = [o for o in FDP.offers([], {}, _day, _lg) if o['market'] == '1X2' and o['pick'] == 'H']
finally:
    FDP.OD.sports, FDP.OD.day_events = _save[0], _save[1]
    FDP.MARKET_ONLY.clear()
_shin = FPK.shin([1.40, 4.90, 8.00])[0]
check('еврокубок вне модели: кандидат П1 есть', len(_o), 1)
check('еврокубок вне модели: P = чистый Pinnacle (Шин)', round(_o[0]['p'], 4) if _o else None, round(_shin, 4))
check('еврокубок вне модели: строка в логе', any('еврокубки вне модели' in x for x in _lg), True)
with tempfile.TemporaryDirectory() as _tmp:
    _f = Path(_tmp) / 'er.csv'
    _f.write_text('id,comp,kind,country,div,date,home,away,home_fd,away_fd,hg,ag,status,stage,ko\n'
                  '999001,uefa.europa,uefa,,,2026-11-26 20:00,Marseille,Levski Sofia,,,2,0,FT,league-phase,0\n'
                  '999002,uefa.europa,uefa,,,2026-11-26 20:00,A,B,,,,,VOID,league-phase,0\n', encoding='utf-8')
    _old = FDP.C.ESPN_STORE
    FDP.C.ESPN_STORE = _f
    try:
        _r = FDP.espn_results()
    finally:
        FDP.C.ESPN_STORE = _old
check('еврокубок вне модели: итог по табло FT и VOID', (_r.get(str(FDP.J.num('999001')), ('',))[0], _r.get(str(FDP.J.num('999002')), ('',))[0]), ('FT', 'VOID'))

# 25.11 · OddsPapi (Ц6б): теннис в приоритете — КХЛ только из остатка; учёт каждого запроса; пустой ответ не жжёт квоту
import datetime as _dt     # noqa: E402
import json as _json       # noqa: E402
spec = importlib.util.spec_from_file_location('oddspapi_t', HERE.parent / 'oddspapi.py')
OPT = importlib.util.module_from_spec(spec)
spec.loader.exec_module(OPT)
with tempfile.TemporaryDirectory() as tmp:
    OPT.DIR = Path(tmp)
    OPT.USAGE = Path(tmp) / 'oddspapi_usage.json'
    check('Ц6б: межсезонье — резерв тенниса только на 28–31.12', OPT.tennis_reserve(_dt.date(2026, 12, 1)), 7 * 4)
    check('Ц6б: январь 1-го — резерв 7 × 31', OPT.tennis_reserve(_dt.date(2027, 1, 1)), 7 * 31)
    OPT.USAGE.write_text(_json.dumps({'2027-01': {'tennis': 40}}), encoding='utf-8')
    check('Ц6б: январь 10-го, потрачено 40 — остаток КХЛ', OPT.spare(_dt.date(2027, 1, 10)), 250 - 5 - 40 - 7 * 22)
    (Path(tmp) / 'oddspapi_2027-01-10.json').write_text(_json.dumps({'calls': 7, 'fixtures': [], 'odds': {}}), encoding='utf-8')
    check('Ц6б: сегодняшние 7 тенниса уже потрачены — резерв без сегодня', OPT.tennis_reserve(_dt.date(2027, 1, 10)), 7 * 21)
    OPT.USAGE.write_text(_json.dumps({'2027-01': {'tennis': 245}}), encoding='utf-8')
    check('Ц6б: месяц исчерпан — КХЛ 0', OPT.spare(_dt.date(2027, 1, 10)), 0)
    _uo = OPT.urllib.request.urlopen
    OPT.urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(OSError('нет сети'))
    try:
        OPT._get('/x', {}, who='khl')
    except OSError:
        pass
    finally:
        OPT.urllib.request.urlopen = _uo
    check('Ц6б: неудачный запрос тоже в учёте', _json.loads(OPT.USAGE.read_text())[_dt.date.today().strftime('%Y-%m')].get('khl'), 1)
    OPT.USAGE.write_text('{битый', encoding='utf-8')
    check('Ц6б: учёт битый — КХЛ 0, история не перезаписана', (OPT.spare(_dt.date(2027, 1, 10)), OPT.USAGE.read_text()), (0, '{битый'))
    OPT.USAGE.unlink()
    # снимок КХЛ: остатка нет → ни одного запроса; пустой ответ дважды → третьей попытки нет
    import os as _os  # noqa: E402
    _os.environ.setdefault('ODDSPAPI_KEY', 'test')
    sys.path.insert(0, str(HERE.parent))
    from hockey import hkhl_odds as KO  # noqa: E402
    KO.PAUSE = 0
    KO.OP, KO.DIR, KO.TEAMS, KO.MARKETS = OPT, Path(tmp), Path(tmp) / 't.json', Path(tmp) / 'm.json'
    got = []
    OPT._get = lambda path, params, who='tennis': got.append(path) or []
    OPT.spare = lambda day=None: 1
    lg = []
    check('КХЛ: остаток 1 < нужно 3 — пропуск без запросов', (KO.snapshot('2026-11-25', pd.Timestamp('2026-11-25 07:00'), lg), got), (0, []))
    OPT.spare = lambda day=None: 100
    KO.MARKETS.write_text('{}', encoding='utf-8')
    for _ in range(3):
        KO.snapshot('2026-11-25', pd.Timestamp('2026-11-25 07:00'), lg)
    check('КХЛ: пустой ответ — не больше 2 попыток (2 × 2 конторы)', len(got), 4)
    fx = dict(fixtureId='f1', startTime='2026-11-25T14:00:00.000Z', participant1Id=1, participant2Id=2,
              bookmakerOdds={'pinnacle': {'markets': {'151': {'outcomes': {'151': {'players': {'0': {'price': 1.87}}},
                                                                          '152': {'players': {'0': {'price': 1.95}}}}}}}})
    old = dict(fixtureId='f0', startTime='2026-11-25T06:00:00.000Z', participant1Id=3, participant2Id=4, bookmakerOdds=fx['bookmakerOdds'])
    OPT._get = lambda path, params, who='tennis': ([fx, old] if path == '/odds-by-tournaments' else
                                                  [dict(participant1Id=1, participant1Name='HC Barys', participant2Id=2,
                                                        participant2Name='Severstal Cherepovets')])
    KO.snapshot('2026-11-26', pd.Timestamp('2026-11-25 07:00'), lg)
    snap = _json.loads((Path(tmp) / 'khl_2026-11-26.json').read_text())
    n0 = len(lg)
    OPT._get = lambda path, params, who='tennis': got.append(path) or []
    got.clear(); KO.snapshot('2026-11-26', pd.Timestamp('2026-11-25 08:00'), lg)
    check('КХЛ: нет 1xBet — повтор только недостающей конторы', got, ['/odds-by-tournaments'])
    got.clear(); KO.snapshot('2026-11-26', pd.Timestamp('2026-11-25 09:00'), lg)
    check('КХЛ: 2 попытки — дальше без запросов', got, [])
    check('КХЛ: снимок — только будущий матч, имена, кф Pinnacle',
          [(r['home'], r['away'], r['books']['pinnacle']['151']) for r in snap['fixtures']],
          [('HC Barys', 'Severstal Cherepovets', {'151': 1.87, '152': 1.95})])

# 26.11 IMP-12: недельный отчёт — своя отметка, повтор после сбоя (суточный в пн уже ушёл)
_R = Path(__file__).resolve().parent.parent
for _f, _k in (('autorun.py', "rt['week_done'] = yday.isoformat()"), ('football/run.py', "rt['week_done'] = yday.isoformat()"),
               ('hockey/hrun.py', "rt['hk_week_done'] = str(yday)")):
    _s = (_R / _f).read_text()
    check(f'IMP-12 {_f}: отметка недельного после отправки', _k in _s, True)
    check(f'IMP-12 {_f}: повтор недельного в условии отчёта',
          ('or week_pend) and not _rep_hold' in _s) if 'hrun' not in _f else ('if wk_pend:\n        due = True' in _s), True)
_s = (_R / 'hockey/hrun.py').read_text()
check('IMP-12 хоккей: повтор недельного не шлёт суточный и не сдвигает калибровку',
      ('if not wk_pend:\n                notify.send_card(report(' in _s) and ('(due and not wk_pend)' in _s), True)

# 26.11 решение пользователя: правило «одна ⭐ на 2 ч» (С14) отменено во всех видах — одновременные матчи можно
for _f in ('dpcore.py', 'football/dpcore.py', 'hockey/hpick.py'):
    check(f'С14 отменено: в {_f} нет проверки отрезка', 'SEGMENT_H' not in (_R / _f).read_text(), True)

# 27.11 картинки отчётов (cards.py): таблица и ставки — в картинку, свёрнутые списки — текстом; сбой отрисовки — текст
import cards as CD
_dp = ["<b>⚽ Футбол · ставки дня · 26.11.2026</b>\n<pre>Линия (цель)      Кол-во      %\n────\n💼 Банк с 17.11, ед.\n сутки                 3  +0,46 🟢\n всего                13  −0,34 🔴</pre>",
       "<b>⚽ Ставки дня · 26.11</b> — угадано 1/1\n✅ ⚽1 <b>ТБ2.5</b> · Crystal Palace — Real Sociedad · 64% · кф 1,61 · EV +2,9%",
       "▸ Матчи (1)\n<blockquote expandable>✅ A — B · 1:0</blockquote>"]
_png, _tx, _ti = CD.render(_dp)
check('🖼 картинка: PNG есть, заголовок', (len(_png) == 1 and _png[0][:4] == b'\x89PNG', _ti), (True, '⚽ Футбол · ставки дня · 26.11.2026'))
check('🖼 свёрнутый список — текстом вместе с заголовком', [t.split('\n')[0] for t in _tx], ['▸ Матчи (1)'])
check('🖼 сводка каперов — таблица', bool(CD._CAP.match('Green Line — 7/7 · прогнозов 8 · ROI +84%')), True)
import notify as NT
_r0 = CD.render
CD.render = lambda b: (_ for _ in ()).throw(ValueError('x'))
check('🖼 сбой отрисовки — отчёт текстом (без исключения)', NT.send_card(['<b>t</b>']), [])
CD.render = _r0

# 27.11 этапы 2–3: закреп ⭐ картинкой, правка — замена картинки; график банка; стиль Montserrat
_pk = CD.render_pick("⚽1 <b>Футбол · ставка дня</b> · 🎯 уверенная\n🏆 Лига · 🕒 26.11 23:00\n<b>A — B</b>\n<b>ТБ2.5</b> 🔥 — 64% · честный кф 1,56\n💵 1xBet 1,61 · EV +2,9%")
check('🖼 ⭐ карточка — PNG', _pk[:4], b'\x89PNG')
check('🖼 ⭐ снята — PNG', CD.render_pick("⚽3 <b>Футбол · ставка дня снята</b> ❌\nA — B\n<s>ТБ2</s> — рынок против: P 72% → 58%")[:4], b'\x89PNG')
import datetime as _dt
check('🖼 график банка — PNG', CD.bank_chart([(_dt.date(2026, 11, 17), 0.2), (_dt.date(2026, 11, 18), -0.1)], 'Футбол · банк')[:4], b'\x89PNG')
check('🖼 шрифт Montserrat, ⭐N → №N', (CD.FONT['r'].name, CD._num('⭐1 Ставка')), ('Montserrat-Regular.ttf', '№1 Ставка'))
NT.PIN_CARDS, _pc = True, NT.PIN_CARDS
check('🖼 ⭐ без токена — без исключения', NT.send_pick("⚽1 <b>Футбол · ставка дня</b>\nтекст"), [])

# 27.11 меню картинками: ключ заголовка одинаков в сообщении и в архиве (без года, «повтор», значков)
check('🖼 ключ отчёта: сообщение = архив', NT.card_key(NT.card_title(['<b>📈 Футбол · отчёт · 26.11 · повтор</b> · x'])),
      NT.card_key(NT.card_title(['<b>📈 Футбол · отчёт · 26.11.2026</b>'])))
import archive as AR
NT.CARD_IDS.clear(); NT.CARD_IDS[NT.card_key('⚽ Футбол · ставки дня · 26.11')] = ['F1', 'F2']
check('🖼 архив: img по заголовку', (AR._img(['<b>⚽ Футбол · ставки дня · 26.11.2026</b>\n…']), AR._img(['<b>Другой</b>'])), ('["F1", "F2"]', ''))
NT.CARD_IDS.clear()

if FAILS:
    print('\n'.join(FAILS))
    print(f'тесты регрессии: провалов {len(FAILS)}')
    sys.exit(1)
print('тесты регрессии: все ✅')
