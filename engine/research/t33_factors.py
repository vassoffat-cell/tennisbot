"""T33 (12.11.2026, гипотезы пользователя): «ненужной информации не бывает» — скрытые факторы тенниса против модели.

Метод: для каждого среза берём матчи, где признак есть ровно у одного игрока («отмеченный»), и сравниваем
  факт  = доля побед отмеченного игрока,
  модель = средняя P модели на его победу (прогноз до матча, V4 T21+рейтинг, без утечки),
  z = Σ(факт − P) / √ΣP(1−P).   z ≥ +2 — модель недооценивает срез (фактор есть и не учтён), z ≤ −2 — переоценивает.
Проверок много → значимым считаем |z| ≥ 2,5 и одинаковый знак в двух половинах периода (2023–24 и 2025–26).
Запуск: python3 t33_factors.py  (данные — /home/claude/research/out, игроки и турниры — data/sackmann).
"""
import glob
import re

import numpy as np
import pandas as pd

OUT = '/home/claude/research/out'
SACK = '/home/claude/tennisbot/data/sackmann'
CTRY = {'australian open': 'AUS', 'roland garros': 'FRA', 'wimbledon': 'GBR', 'us open': 'USA', 'indian wells': 'USA', 'miami': 'USA',
        'madrid': 'ESP', 'rome': 'ITA', 'dubai': 'UAE', 'doha': 'QAT', 'beijing': 'CHN', 'washington': 'USA', 'shanghai': 'CHN',
        'cincinnati': 'USA', 'canada': 'CAN', 'montreal': 'CAN', 'toronto': 'CAN', 'charleston': 'USA', 'hamburg': 'GER',
        'eastbourne': 'GBR', 'stuttgart': 'GER', 'brisbane': 'AUS', 's hertogenbosch': 'NED', 'tokyo': 'JPN', 'auckland': 'NZL',
        'paris': 'FRA', 'barcelona': 'ESP', 'winston-salem': 'USA', 'hong kong': 'HKG', 'adelaide': 'AUS', 'bogota': 'COL',
        'acapulco': 'MEX', 'rotterdam': 'NED', 'rabat': 'MAR', "queen's club": 'GBR', 'strasbourg': 'FRA', 'seoul': 'KOR',
        'dallas': 'USA', 'munich': 'GER', 'lyon': 'FRA', 'guadalajara': 'MEX', 'marrakech': 'MAR', 'santiago': 'CHI',
        'montpellier': 'FRA', 'houston': 'USA', 'buenos aires': 'ARG', 'geneva': 'SUI', 'delray beach': 'USA', 'vienna': 'AUT',
        'prague': 'CZE', 'austin': 'USA', 'hobart': 'AUS', 'basel': 'SUI', 'cleveland': 'USA', 'nottingham': 'GBR', 'halle': 'GER',
        'bad homburg': 'GER', 'berlin': 'GER', 'merida': 'MEX', 'monterrey': 'MEX', 'linz': 'AUT', 'wuhan': 'CHN', 'stockholm': 'SWE',
        'abu dhabi': 'UAE', 'umag': 'CRO', 'los cabos': 'MEX', 'san diego': 'USA', 'gstaad': 'SUI', 'bastad': 'SWE', 'marseille': 'FRA',
        'mallorca': 'ESP', 'metz': 'FRA', 'kitzbuhel': 'AUT', 'cluj-napoca': 'ROU', 'cluj napoca': 'ROU', 'palermo': 'ITA',
        'rouen': 'FRA', 'osaka': 'JPN', 'monastir': 'TUN', 'guangzhou': 'CHN', 'birmingham': 'GBR', 'budapest': 'HUN',
        'rio de janeiro': 'BRA', 'ningbo': 'CHN', 'antwerp': 'BEL', 'chengdu': 'CHN', 'estoril': 'POR', 'atlanta': 'USA',
        'newport': 'USA', 'bucharest': 'ROU', 'cordoba': 'ARG', 'hua hin': 'THA', 'jiujiang': 'CHN', 'lausanne': 'SUI',
        'warsaw': 'POL', 'chennai': 'IND', 'iasi': 'ROU', 'astana': 'KAZ', 'ostrava': 'CZE', 'sydney': 'AUS', 'pune': 'IND',
        'sofia': 'BUL', 'almaty': 'KAZ', 'hangzhou': 'CHN', 'zhuhai': 'CHN', 'sao paulo': 'BRA', 'parma': 'ITA', 'melbourne': 'AUS',
        'nanchang': 'CHN', 'portoroz': 'SLO', 'singapore': 'SGP', 'granby': 'CAN', 'st. petersburg': 'RUS', 'istanbul': 'TUR',
        'tallinn': 'EST', 'belgrade': 'SRB', 'naples': 'ITA', 'banja luka': 'BIH', 'brussels': 'BEL', 'tel aviv': 'ISR',
        'zhengzhou': 'CHN', 'san jose': 'USA', 'gijon': 'ESP', 'florence': 'ITA', 'athens': 'GRE'}
ASIA_P = {'CHN', 'JPN', 'KOR', 'TPE', 'THA', 'IND', 'HKG', 'INA', 'PHI'}
ASIA_T = {'CHN', 'JPN', 'KOR', 'HKG', 'THA', 'IND', 'SGP'}
DEEP = {'SF', 'F'}


def norm(name: str) -> str:
    s = re.sub(r'\s+(masters|\d+|125)$', '', str(name).strip().lower())
    return re.sub(r'\s+', ' ', s).strip()


def load(tour: str) -> pd.DataFrame:
    m = pd.read_pickle(f'{OUT}/{tour}_matches.pkl')
    z = pd.read_pickle(f'{OUT}/{tour}_z.pkl')['V4 T21+рейтинг вместо BC']
    m = m.assign(p=1 / (1 + np.exp(-np.asarray(z, float))), row=np.arange(len(m)))
    s = pd.concat([pd.read_csv(f, low_memory=False) for f in sorted(glob.glob(f'{SACK}/{tour}_matches_*.csv'))], ignore_index=True)
    s['date'] = pd.to_datetime(s.tourney_date.astype(str), format='%Y%m%d', errors='coerce')
    s = s.drop_duplicates(['date', 'winner_id', 'loser_id'])
    cols = ['date', 'winner_id', 'loser_id', 'tourney_name', 'tourney_level', 'round', 'winner_ht', 'loser_ht', 'winner_ioc', 'loser_ioc',
            'winner_age', 'loser_age', 'winner_hand', 'loser_hand']
    s = s[cols].rename(columns={'date': 'tdate'})                 # в истории — дата матча, в справочнике — дата начала турнира
    d = m.merge(s, left_on=['w', 'l'], right_on=['winner_id', 'loser_id'], how='left')
    gap = (d.date - d.tdate).dt.days
    d = d[d.tdate.isna() | ((gap >= -1) & (gap <= 16))]
    d = d.assign(gap=gap.reindex(d.index).abs()).sort_values(['row', 'gap']).drop_duplicates('row')
    d = m[['row']].merge(d.drop(columns=[c for c in m.columns if c != 'row']), on='row', how='left').join(m.drop(columns='row'))
    d['tn'] = d.tourney_name.map(norm)
    d['tc'] = d.tn.map(CTRY)
    d['tour'] = tour
    return d


def prior_stats(d: pd.DataFrame) -> pd.DataFrame:
    """До матча (без утечки): доля побед игрока всего и на покрытии; «глубокий» прошлогодний результат на этом турнире."""
    d = d.sort_values(['date', 'row']).reset_index(drop=True)
    tot, surf = {}, {}
    deep_prev = {}                                          # (игрок, турнир, сезон) → дошёл до SF/F
    out = {k: [] for k in ('w_sw', 'w_sn', 'w_aw', 'w_an', 'l_sw', 'l_sn', 'l_aw', 'l_an', 'w_def', 'l_def')}
    for r in d.itertuples(index=False):
        for side, pid in (('w', r.w), ('l', r.l)):
            a = tot.get(pid, (0, 0)); s = surf.get((pid, r.surface), (0, 0))
            out[f'{side}_aw'].append(a[0]); out[f'{side}_an'].append(a[1])
            out[f'{side}_sw'].append(s[0]); out[f'{side}_sn'].append(s[1])
            out[f'{side}_def'].append(int(deep_prev.get((pid, r.tn, r.season - 1), 0)))
        for pid, won in ((r.w, 1), (r.l, 0)):
            a = tot.get(pid, (0, 0)); tot[pid] = (a[0] + won, a[1] + 1)
            s = surf.get((pid, r.surface), (0, 0)); surf[(pid, r.surface)] = (s[0] + won, s[1] + 1)
        if str(r.round) in DEEP:
            deep_prev[(r.w, r.tn, r.season)] = 1
            deep_prev[(r.l, r.tn, r.season)] = 1
    for k, v in out.items():
        d[k] = v
    return d


def test(d: pd.DataFrame, fw, fl, name: str, rows: list, extra=None) -> None:
    """fw / fl — булевы ряды: признак у победителя / у проигравшего. Берём матчи, где он ровно у одного."""
    q = d if extra is None else d[extra]
    a, b = fw.reindex(q.index).fillna(False).astype(bool), fl.reindex(q.index).fillna(False).astype(bool)
    q = q[a ^ b]
    if len(q) < 60:
        rows.append((name, len(q), np.nan, np.nan, np.nan, np.nan, np.nan)); return
    y = a.reindex(q.index).astype(float).values             # отмеченный — победитель
    p = np.where(y == 1, q.p.values, 1 - q.p.values)
    z = (y - p).sum() / np.sqrt((p * (1 - p)).sum())
    h1, h2 = q.season.values <= 2024, q.season.values >= 2025

    def zz(msk):
        return (y[msk] - p[msk]).sum() / np.sqrt((p[msk] * (1 - p[msk])).sum()) if msk.sum() >= 30 else np.nan
    rows.append((name, len(q), y.mean() * 100, p.mean() * 100, (y.mean() - p.mean()) * 100, z, f'{zz(h1):+.1f} / {zz(h2):+.1f}'))


def slam_weeks(d: pd.DataFrame) -> set:
    g = d[d.tourney_level.astype(str) == 'G'].groupby(['season', 'tn']).date.min()
    return {dt for dt in g.values}


def run(tour: str) -> pd.DataFrame:
    d = prior_stats(load(tour))
    d = d[d.use & (d.season >= 2023) & d.tourney_name.notna()].copy()
    rows: list = []
    T = lambda fw, fl, name, extra=None: test(d, fw, fl, name, rows, extra)
    reg = d.tc.notna()
    T(d.winner_ioc == d.tc, d.loser_ioc == d.tc, 'Домашний турнир (страна игрока = страна турнира)', reg)
    T((d.winner_ioc == d.tc), (d.loser_ioc == d.tc), '  дом, Большой шлем', reg & (d.tourney_level.astype(str) == 'G'))
    T((d.winner_ioc == d.tc), (d.loser_ioc == d.tc), '  дом, не шлем', reg & (d.tourney_level.astype(str) != 'G'))
    hw, hl = d.winner_ht.astype(float), d.loser_ht.astype(float)
    hk = hw.notna() & hl.notna()
    T(hw - hl >= 10, hl - hw >= 10, 'Выше соперника на ≥ 10 см', hk)
    for sf in ('Hard', 'Clay', 'Grass'):
        T(hw - hl >= 10, hl - hw >= 10, f'  выше на ≥ 10 см, {sf}', hk & (d.surface == sf))
    T(d.winner_hand == 'L', d.loser_hand == 'L', 'Левша против правши')
    aw, al = d.winner_age.astype(float), d.loser_age.astype(float)
    T(aw >= 30, al >= 30, 'Возраст 30+ против младше 30')
    for sf in ('Grass', 'Clay', 'Hard'):
        T(aw >= 30, al >= 30, f'  30+ против младше, {sf}', d.surface == sf)
    T(aw - al >= 6, al - aw >= 6, 'Старше соперника на ≥ 6 лет, трава', d.surface == 'Grass')
    T(d.winner_ioc.isin(ASIA_P), d.loser_ioc.isin(ASIA_P), 'Азиатские игроки, хард', d.surface == 'Hard')
    T(d.winner_ioc.isin(ASIA_P), d.loser_ioc.isin(ASIA_P), 'Азиатские игроки, грунт и трава', d.surface != 'Hard')
    T(d.winner_ioc.isin(ASIA_P), d.loser_ioc.isin(ASIA_P), 'Азиатские игроки, турниры Азии', d.tc.isin(ASIA_T))
    slams = slam_weeks(d)
    pre = d.date.map(lambda x: any(pd.Timedelta(days=5) <= (np.datetime64(s) - np.datetime64(x)) <= pd.Timedelta(days=9) for s in slams)) \
        & (d.tourney_level.astype(str) != 'G')
    T(d.wrank <= 20, d.lrank <= 20, 'Неделя перед шлемом: игрок топ-20 против остальных', pre)
    T(d.wrank <= 10, d.lrank <= 10, '  неделя перед шлемом: топ-10', pre)
    T(d.wrank <= 20, d.lrank <= 20, 'Обычные недели (не шлем, не перед шлемом): топ-20', ~pre & (d.tourney_level.astype(str) != 'G'))
    grass_warm = pre & (d.surface == 'Grass')
    T(d.wrank <= 20, d.lrank <= 20, '  перед Уимблдоном (трава): топ-20', grass_warm)
    T(d.w_def == 1, d.l_def == 1, 'Защита очков: в прошлом году здесь полуфинал+')
    sok = (d.w_sn >= 20) & (d.l_sn >= 20) & (d.w_an >= 50) & (d.l_an >= 50)
    wd = d.w_sw / d.w_sn.clip(lower=1) - d.w_aw / d.w_an.clip(lower=1)
    ld = d.l_sw / d.l_sn.clip(lower=1) - d.l_aw / d.l_an.clip(lower=1)
    T(wd - ld >= 0.08, ld - wd >= 0.08, 'Любимое покрытие (доля побед на нём выше общей на 8 п.п. против соперника)', sok)
    for sf in ('Grass', 'Clay'):
        T(wd - ld >= 0.08, ld - wd >= 0.08, f'  любимое покрытие, {sf}', sok & (d.surface == sf))
    return pd.DataFrame(rows, columns=['срез', 'n', 'факт %', 'модель %', 'Δ п.п.', 'z', 'z 2023–24 / 2025–26']).assign(tour=tour.upper())


if __name__ == '__main__':
    pd.set_option('display.width', 250); pd.set_option('display.max_colwidth', 80); pd.set_option('display.max_rows', 200)
    for t in ('wta', 'atp'):
        r = run(t)
        print(f'\n===== {t.upper()} =====')
        print(r.drop(columns='tour').to_string(index=False, float_format=lambda x: f'{x:.1f}'))
        r.to_csv(f'{OUT}/t33_{t}.csv', index=False)
