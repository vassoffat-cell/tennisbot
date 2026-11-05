"""Сквозной конвейер: скриншоты → разбор → игроки → независимый прогноз → строки журнала."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pandas as pd

import config as C
import dataset
import forecast as FC
import names as NM
import state as ST
from ocr import Parsed, parse_screenshot

_MODELS: dict[str, ST.Model] = {}
_PLAYERS: dict[str, pd.DataFrame] = {}


def model(tour: str) -> ST.Model:
    if tour not in _MODELS:
        try:
            _MODELS[tour] = ST.load(tour)
        except Exception as e:   # нет файла или другая версия numpy/pandas — пересобрать из данных (1–2 минуты)
            print(f'state/{tour}.pkl не загружен ({e.__class__.__name__}); пересобираю модель…')
            _MODELS[tour] = ST.build(tour, False)
            ST.save(_MODELS[tour])
        _PLAYERS[tour] = dataset.players(tour)
    return _MODELS[tour]


@dataclass
class Match:
    parsed: Parsed
    tour: str | None = None
    candidates: list = field(default_factory=list)      # [кандидаты игрока 1, кандидаты игрока 2]
    ids: tuple | None = None
    names_lat: tuple | None = None


def recognize(paths: list[str]) -> Match:
    p = Parsed()
    for path in paths:
        parse_screenshot(path, p)
    m = Match(p, (p.tour or '').lower() or None)
    if m.tour and p.players:
        identify(m)
    return m


def identify(m: Match) -> None:
    md = model(m.tour)
    st = md.state
    last = st.last_date
    active = {pid for pid, ds in st.dates.items() if ds and (last - ds[-1]).days <= 540}
    m.candidates = [NM.find_player(n, _PLAYERS[m.tour], active) for n in m.parsed.players]
    m.ids = tuple(c[0][0] for c in m.candidates)
    m.names_lat = tuple(c[0][1] for c in m.candidates)


def sure(c: list) -> bool:
    """Кандидат уверенный: (сходство ≥ 0,80 и отрыв от второго ≥ 0,05) или (сходство ≥ 0,65 и отрыв ≥ 0,10)."""
    if not c:
        return False
    gap = c[0][2] - c[1][2] if len(c) > 1 else 1.0
    return (c[0][2] >= 0.80 and gap >= 0.05) or (c[0][2] >= 0.65 and gap >= 0.10)


def confident(m: Match) -> bool:
    """Оба игрока найдены уверенно (см. sure)."""
    return bool(m.candidates) and all(sure(c) for c in m.candidates)


_FRESH: dict[str, pd.Timestamp] = {}


def fresh(tour: str) -> str:
    """Докатить в форму игроков результаты ESPN после последней даты tennis-data (не чаще раза в 3 часа)."""
    import espn
    import live
    md = model(tour)
    now = pd.Timestamp.now()
    if tour in _FRESH and now - _FRESH[tour] < pd.Timedelta(hours=3):
        return ''
    st = md.state
    td_last = getattr(st, 'td_last', st.last_date).date()
    today = live.utc_today()
    start = max(td_last, today - dt.timedelta(days=21))
    try:
        ms = espn.fetch(tour, [start + dt.timedelta(days=i) for i in range((today - start).days + 1)])
        n = live.refresh(md, ms, _PLAYERS[tour])
        _FRESH[tour] = now
        return f'форма докатана по ESPN: +{n} матчей'
    except Exception as e:
        _FRESH[tour] = now - pd.Timedelta(hours=2)          # повторить не раньше чем через час
        n = live.apply_store(md)
        return f'ESPN недоступен ({e.__class__.__name__}); из сохранённых результатов +{n} матчей'


def forecast(m: Match) -> tuple[FC.Forecast, str]:
    md = model(m.tour)
    fresh_note = fresh(m.tour)
    st = md.state
    p = m.parsed
    date = pd.Timestamp(dt.datetime.strptime(p.date, '%d.%m.%Y')) if p.date else pd.Timestamp.today().normalize()
    loc = NM.city(p.tournament)
    slam = NM.is_slam(p.tournament)
    best_of = 5 if (slam and m.tour == 'atp') else 3
    snap = st.snapshot(m.ids[0], m.ids[1], date, p.surface or 'Hard', loc, date.year)
    fc = FC.run(md, snap, p, best_of=best_of, final_tb_to=10 if slam else 7)
    for pid, nm in zip(m.ids, m.names_lat):
        q = st.serve.get(pid)
        last_stat = q[-1][0].date() if q else None
        n = st.n.get(pid, 0)
        fc.notes.append(f'{nm}: матчей в базе {n}; статистика подачи по {last_stat or "—"}')
    td_last = getattr(st, 'td_last', st.last_date)
    fc.notes.append(f'tennis-data по {td_last.date()}, последние результаты по {st.last_date.date()}'
                    + (f' ({fresh_note})' if fresh_note else '') + '. '
                    f'Город турнира: {loc or "не определён"}; формат: до {best_of // 2 + 1} побед'
                    + ('; тай-брейк до 10 в решающем сете' if slam else ''))
    fc.notes.append(f'Режим: бумажные ставки. ◀ — сигнал (EV ≥ {C.EV_THRESHOLD*100:.0f}%, P ≥ {C.MIN_PROB*100:.0f}%).')
    return fc, FC.table_text(fc, m.names_lat)
