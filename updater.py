"""Еженедельное обновление данных: свежие файлы сезона с tennis-data.co.uk → пересборка (переобучение) модели."""
from __future__ import annotations

import datetime as dt
import io
import urllib.request

import pandas as pd

import config as C


def download(tour: str, year: int) -> str:
    """Скачать файл сезона; сохранить, только если он корректен и не короче имеющегося. Возвращает отчёт."""
    url = C.TENNIS_DATA_URL[tour].format(y=year)
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (tennis-model)'})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
    df = pd.read_excel(io.BytesIO(raw))
    if str(df.columns[0]).upper() != tour.upper() or 'Date' not in df:
        raise ValueError(f'{url}: это не файл {tour.upper()} tennis-data')
    dst = C.TENNIS_DATA / f'{year}_{tour.upper()}.xlsx'
    old = len(pd.read_excel(dst)) if dst.exists() else 0
    if len(df) < old:
        return f'{tour.upper()} {year}: на сайте {len(df)} матчей, у нас {old} — оставлен прежний файл'
    dst.write_bytes(raw)
    last = pd.to_datetime(df.Date).max().date()
    return f'{tour.upper()} {year}: {len(df)} матчей (+{len(df) - old}), по {last}'


def update_all() -> list[str]:
    """Текущий сезон; в январе — ещё и прошлый (дозаписи последних недель)."""
    today = dt.date.today()
    years = [today.year] + ([today.year - 1] if today.month == 1 else [])
    out = []
    for tour in ('wta', 'atp'):
        for y in years:
            try:
                out.append(download(tour, y))
            except Exception as e:
                out.append(f'{tour.upper()} {y}: не скачан ({e.__class__.__name__}: {e})')
    return out


def rebuild() -> list[str]:
    import state as ST
    out = []
    for tour in ('wta', 'atp'):
        m = ST.build(tour, verbose=False)
        ST.save(m)
        out.append(m.test_report)
    return out
