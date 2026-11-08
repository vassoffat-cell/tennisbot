"""T29 · этап A: статистика подачи из TennisMyLife (stats.tennismylife.org) — замена устаревшего Sackmann на 2026+.

Sackmann 2026 обрывается на 25.05.2026 (Roland Garros), поэтому признак «подача/приём» и модель очка в 2026 устаревают.
TennisMyLife — бесплатная база в формате Sackmann (ATP и WTA, обновление ежедневно; лицензия — некоммерческое использование).

Этап A (этот модуль): раз в сутки скачать файлы текущего и прошлого сезона + текущие турниры в data/tml/ —
в модель пока НЕ подаются. Этап B — после проверки данных (связка игроков, сверка с Sackmann на январе–мае 2026).
"""
from __future__ import annotations

import datetime as dt
import urllib.request

import config as C

BASE = 'https://stats.tennismylife.org/data/'
FILES = {'atp': ('{y}.csv', 'ongoing_tourneys.csv'), 'wta': ('{y}_wta.csv', 'wta_ongoing_tourneys.csv')}
DIR = C.DATA / 'tml'


def _get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (tennis-model; personal non-commercial)'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def download(today: dt.date | None = None) -> list[str]:
    """Скачать сезоны (текущий и прошлый) и текущие турниры обоих туров. Ошибка одного файла не мешает остальным."""
    y = (today or dt.date.today()).year
    DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for tour, (season, ongoing) in FILES.items():
        names = [season.format(y=y - 1), season.format(y=y), ongoing]
        for name in names:
            try:
                data = _get(BASE + name)
                if not data.startswith(b'tourney_id'):
                    raise ValueError('не CSV TennisMyLife')
                (DIR / name).write_bytes(data)
                rows = data.count(b'\n') - 1
                out.append(f'TennisMyLife {name}: {rows} строк')
            except Exception as e:                       # сеть / формат — оставляем прошлую копию
                out.append(f'TennisMyLife {name}: не скачан ({e.__class__.__name__})')
    return out
