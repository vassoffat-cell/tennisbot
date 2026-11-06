"""Расписание и результаты WTA/ATP из открытого API ESPN (бесплатно, без ключа).

  https://site.api.espn.com/apis/site/v2/sports/tennis/{wta|atp}/scoreboard?dates=YYYYMMDD

Ответ: events[] (турниры) → groupings[] (разряды) → competitions[] (матчи). Запрос на дату возвращает все матчи
турниров, идущих в этот день, — поэтому несколько дат подряд дают полную сетку; дубли убираются по id матча.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import urllib.request
from dataclasses import dataclass, field

import pandas as pd

URL = 'https://site.api.espn.com/apis/site/v2/sports/tennis/{tour}/scoreboard?dates={d}'
SINGLES = {'mens-singles', 'womens-singles'}
DONE = {'STATUS_FINAL', 'STATUS_RETIRED'}            # доигран или снят по ходу (отказ)


@dataclass
class EspnMatch:
    id: str
    tour: str
    event: str
    major: bool
    location: str                   # город: первая часть venue.fullName («Paris, France» → «Paris»)
    round: str
    ridx: int                       # номер раунда в шкале tennis-data: 1–4, 5 — 1/4, 6 — 1/2, 7 — финал
    qualifying: bool
    start: pd.Timestamp             # UTC
    state: str                      # pre / in / post
    status: str                     # STATUS_SCHEDULED, STATUS_FINAL, STATUS_RETIRED, …
    names: tuple[str, str]
    winner: int | None = None       # 0 / 1 — индекс победителя в names
    sets: list[tuple[int, int]] = field(default_factory=list)   # счёт по сетам с позиции names[0]

    @property
    def completed(self) -> bool:
        return self.status in DONE and self.winner is not None

    @property
    def tbd(self) -> bool:
        return any(not n or n.strip().upper() in ('TBD', 'BYE') for n in self.names)


def round_index(name: str) -> int:
    s = name.lower()
    if 'semi' in s:
        return 6
    if 'quarter' in s:
        return 5
    if 'final' in s and 'qualif' not in s:
        return 7
    if 'robin' in s:
        return 4
    m = re.search(r'(\d+)', s)
    return min(int(m.group(1)), 4) if m else 1


def _get(url: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (tennis-model)'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def parse(js: dict, tour: str) -> list[EspnMatch]:
    out = []
    for ev in js.get('events', []) or []:
        groups = ev.get('groupings') or [{'grouping': {'slug': 'singles'}, 'competitions': ev.get('competitions', [])}]
        for g in groups:
            slug = (g.get('grouping') or {}).get('slug', '')
            if slug not in SINGLES and slug != 'singles':
                continue
            for c in g.get('competitions', []) or []:
                comp = c.get('competitors') or []
                if len(comp) != 2:
                    continue
                comp = sorted(comp, key=lambda x: int(x.get('order', 0) or 0))
                names = tuple(((x.get('athlete') or {}).get('displayName') or (x.get('athlete') or {}).get('fullName') or '').strip()
                              for x in comp)
                st = (c.get('status') or {}).get('type') or {}
                rnd = (c.get('round') or {}).get('displayName', '') or ''
                venue = (c.get('venue') or {}).get('fullName') or (ev.get('venue') or {}).get('fullName') or ''
                winner = next((i for i, x in enumerate(comp) if x.get('winner') is True), None)
                la, lb = comp[0].get('linescores') or [], comp[1].get('linescores') or []
                sets = []
                for a, b in zip(la, lb):
                    try:
                        sets.append((int(float(a.get('value'))), int(float(b.get('value')))))
                    except (TypeError, ValueError):
                        pass
                start = pd.Timestamp(c.get('startDate') or c.get('date') or ev.get('date'))
                start = start.tz_convert('UTC').tz_localize(None) if start.tzinfo else start
                out.append(EspnMatch(
                    id=str(c.get('id')), tour=tour, event=ev.get('name') or ev.get('shortName') or '',
                    major=bool(ev.get('major')), location=venue.split(',')[0].strip(), round=rnd,
                    ridx=round_index(rnd), qualifying='qualif' in rnd.lower(), start=start,
                    state=st.get('state', ''), status=st.get('name', ''), names=names, winner=winner, sets=sets))
    return out


def fetch(tour: str, days: list[dt.date]) -> list[EspnMatch]:
    """Матчи тура за перечисленные даты; ошибки отдельных дат пропускаются, дубли убираются."""
    seen: dict[str, EspnMatch] = {}
    errors = []
    for d in days:
        try:
            for m in parse(_get(URL.format(tour=tour, d=d.strftime('%Y%m%d'))), tour):
                seen[m.id] = m
        except Exception as e:   # сеть, формат — не роняем весь прогон
            errors.append(f'{d}: {e.__class__.__name__}')
            if not seen and len(errors) >= 2:                  # два отказа подряд с самого начала — источник недоступен
                break
    if errors and not seen:
        raise RuntimeError(f'ESPN {tour}: нет данных ({"; ".join(errors)})')
    return sorted(seen.values(), key=lambda m: (m.start, m.id))


RANK_URL = 'https://site.api.espn.com/apis/site/v2/sports/tennis/{tour}/rankings'


def rankings(tour: str) -> list[tuple[str, int, float]]:
    """Текущий рейтинг ATP/WTA: [(имя, место, очки)]. Пустой список, если источник недоступен."""
    try:
        js = _get(RANK_URL.format(tour=tour))
    except Exception:
        return []
    out = []
    for block in js.get('rankings', []) or []:
        for r in block.get('ranks', []) or []:
            a = r.get('athlete') or {}
            name = a.get('displayName') or f"{a.get('firstName', '')} {a.get('lastName', '')}".strip()
            try:
                out.append((name, int(r.get('current')), float(r.get('points') or 0)))
            except (TypeError, ValueError):
                pass
        if out:
            break
    return out
