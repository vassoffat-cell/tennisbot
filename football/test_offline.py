"""Офлайн-проверка полного цикла: ESPN подменяется синтетическими ответами (структура как у настоящего API),
Telegram и Google Таблица отключены (CSV в logs_test). Запуск: python test_offline.py"""
from __future__ import annotations

import datetime as dt
import os
import shutil
import sys
from pathlib import Path

import pandas as pd

os.environ['FOOT_BOT_TOKEN'] = ''
os.environ['FOOT_SHEET_ID'] = ''
import config as C  # noqa: E402

TMP = Path(__file__).parent / 'logs_test'
shutil.rmtree(TMP, ignore_errors=True); TMP.mkdir()
C.LOGS = TMP
C.ESPN_STORE = TMP / 'espn_results.csv'
C.LINEUPS = TMP / 'lineups.csv.gz'
import runtime  # noqa: E402
runtime.PATH = TMP / 'runtime.json'
import fespn  # noqa: E402
import run  # noqa: E402

now = pd.Timestamp.now(tz='UTC').tz_localize(None).floor('h')
T = lambda h: (now + pd.Timedelta(hours=h)).strftime('%Y-%m-%dT%H:%MZ')


def ev(eid, home, away, start, state='pre', status='STATUS_SCHEDULED', hs=None, as_=None, odds=None, slug='regular-season'):
    c = {'date': start, 'status': {'type': {'state': state, 'name': status}},
         'competitors': [{'homeAway': 'home', 'team': {'displayName': home}, 'score': hs},
                         {'homeAway': 'away', 'team': {'displayName': away}, 'score': as_}],
         'odds': [odds] if odds else [None]}
    return {'id': eid, 'date': start, 'season': {'slug': slug}, 'competitions': [c]}


def dk(h, d, a, line, o, u):
    return {'provider': {'name': 'DraftKings'}, 'overUnder': line,
            'moneyline': {'home': {'close': {'odds': h}}, 'away': {'close': {'odds': a}}, 'draw': {'close': {'odds': d}}},
            'total': {'over': {'close': {'line': f'o{line}', 'odds': o}}, 'under': {'close': {'line': f'u{line}', 'odds': u}}}}


EVENTS = {
    'eng.1': [ev('1', 'Arsenal', 'Hull City', T(5), odds=dk('-650', '+700', '+1600', 3.5, '+125', '-160')),
              ev('2', 'Leeds United', 'Tottenham Hotspur', T(3), odds=dk('+150', '+235', '+175', 2.5, '-125', '+100')),
              ev('3', 'Manchester City', 'Liverpool', T(-30), 'post', 'STATUS_FULL_TIME', '2', '1')],
    'ita.1': [ev('4', 'Internazionale', 'Como', T(6)), ev('5', 'Napoli', 'Lazio', T(8))],
    'uefa.champions': [ev('6', 'Arsenal', 'Borussia Dortmund', T(70), slug='round-of-16'),
                       ev('7', 'Real Madrid', 'PSV Eindhoven', T(30), slug='league-phase')],
    'esp.copa_del_rey': [ev('8', 'Barcelona', 'Real Madrid', T(-26), 'post', 'STATUS_FINAL_PEN', '2', '2')],
    'rus.1': [ev('9', 'CSKA Moscow', 'Krasnodar', T(4)), ev('10', 'Zenit St Petersburg', 'Unknown FC', T(7))],
}
CAL = {'calendar': [(now + pd.Timedelta(days=k)).strftime('%Y-%m-%dT07:00Z') for k in range(-3, 4)]}


def fake_get(url, timeout=20):
    if 'summary' in url:
        return {'rosters': [{'team': {'displayName': 'X'}, 'homeAway': 'home',
                             'roster': [{'athlete': {'id': 1, 'displayName': 'A'}, 'starter': True}]}]}
    comp = url.split('/soccer/')[1].split('/')[0]
    d = url.split('dates=')[1]
    today = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d')
    evs = [e for e in EVENTS.get(comp, []) if pd.Timestamp(e['date']).strftime('%Y%m%d') == d or
           (d == today and False)]
    return {'leagues': [CAL], 'events': evs}


fespn._get = fake_get
print(run.run(dry=False))
af = pd.read_csv(TMP / 'auto_forecasts.csv', sep=';')
print(af[['comp', 'home', 'away', 'source', 'lam_h', 'lam_a', 'p_h', 'p_d', 'p_a', 'p_o25', 'dk_h', 'f_home', 'f_away']].to_string())
ld = pd.read_csv(TMP / 'auto_lines.csv', sep=';')
print(ld[['espn_id', 'label', 'p', 'k_lo', 'k_hi', 'z_lo', 'z_hi']].head(22).to_string())
st = pd.read_csv(C.ESPN_STORE)
print(st[['id', 'comp', 'home_fd', 'away_fd', 'hg', 'ag', 'status']].to_string())
print('--- второй запуск: матч 2 доигран 1:1')
EVENTS['eng.1'][1] = ev('2', 'Leeds United', 'Tottenham Hotspur', T(-2), 'post', 'STATUS_FULL_TIME', '1', '1')
EVENTS['eng.1'][1]['date'] = T(3)
print(run.run(dry=False))
af = pd.read_csv(TMP / 'auto_forecasts.csv', sep=';')
print(af[['espn_id', 'status', 'hg', 'ag', 'logloss']].to_string())
ld = pd.read_csv(TMP / 'auto_lines.csv', sep=';')
print(ld[ld.espn_id == 2][['label', 'status', 'result', 'pnl_lo']].to_string())
