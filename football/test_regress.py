"""Тесты регрессии каперов и доставки (ORG IMP-05, 23.11.2026).

Каждый случай — реальная ошибка из журнала CONTROL §3: если она вернётся, тест упадёт ДО push.
Запуск (сеть не нужна, файлы данных не меняются):  cd football && PYTHONDONTWRITEBYTECODE=1 python3 test_regress.py
Новая ошибка каперов → новый случай сюда, в том же коммите, что и исправление (ORG §2, быстрая дорога).
"""
from __future__ import annotations

import importlib.util
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
B._SHADOW.clear()

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

if FAILS:
    print('\n'.join(FAILS))
    print(f'тесты регрессии: провалов {len(FAILS)}')
    sys.exit(1)
print('тесты регрессии: все ✅')
