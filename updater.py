"""Обновление данных tennis-data и переобучение модели.

Сайт tennis-data.co.uk закрыт для запросов с облачных серверов (ответ 403), поэтому облако его не скачивает и
обходить это не пытается. Свежие файлы приходят так:
  • вы скачиваете 2026.xlsx в браузере и отправляете боту (или кладёте в data/tennis-data);
  • бот на компьютере сохраняет файл и, если настроен GitHub, сам отправляет его в репозиторий;
  • облако при следующем запуске переобучается на файлах из репозитория.
Без свежих файлов система продолжает работать: форма игроков обновляется по результатам ESPN (data/espn).
"""
from __future__ import annotations

import datetime as dt
import io
import os
import urllib.request

import pandas as pd

import config as C

USER_AGENT = 'tennisbot/2.0 (personal research; weekly)'


def save_file(raw: bytes) -> str:
    """Проверить и сохранить файл tennis-data (тур и сезон определяются по содержимому). Возвращает отчёт."""
    df = pd.read_excel(io.BytesIO(raw))
    tour = str(df.columns[0]).upper()
    if tour not in ('WTA', 'ATP') or 'Date' not in df:
        raise ValueError('это не файл tennis-data.co.uk (первый столбец должен быть WTA или ATP)')
    dates = pd.to_datetime(df.Date)
    year = int(dates.dt.year.max())
    dst = C.TENNIS_DATA / f'{year}_{tour}.xlsx'
    old = len(pd.read_excel(dst)) if dst.exists() else 0
    if len(df) < old:
        return f'{tour} {year}: в файле {len(df)} матчей, у нас уже {old} — оставлен прежний'
    dst.write_bytes(raw)
    return f'{tour} {year}: {len(df)} матчей (+{len(df) - old}), по {dates.max().date()}'


def download(tour: str, year: int) -> str:
    """Обычный запрос с компьютера. Если сайт отвечает отказом — не обходим, просим скачать в браузере."""
    url = C.TENNIS_DATA_URL[tour].format(y=year)
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        return save_file(r.read())


def update_all() -> list[str]:
    """Текущий сезон (в январе — и прошлый). В облаке (CLOUD=1) не скачивает: сайт закрыт для серверов."""
    if os.getenv('CLOUD'):
        return ['tennis-data: в облаке не скачивается (сайт закрыт для серверов) — используются файлы из репозитория']
    today = dt.date.today()
    years = [today.year] + ([today.year - 1] if today.month == 1 else [])
    out = []
    for tour in ('wta', 'atp'):
        for y in years:
            try:
                out.append(download(tour, y))
            except Exception as e:
                out.append(f'{tour.upper()} {y}: не скачан ({e.__class__.__name__}). Скачайте в браузере '
                           f'{C.TENNIS_DATA_URL[tour].format(y=y)} и отправьте файл боту')
    return out


def rebuild() -> list[str]:
    import state as ST
    out = []
    for tour in ('wta', 'atp'):
        m = ST.build(tour, verbose=False)
        ST.save(m)
        out.append(m.test_report)
    return out
