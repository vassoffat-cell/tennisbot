"""Расписание, результаты, коэффициенты и составы футбола из открытого API ESPN.

Счёт ESPN включает дополнительное время. Ставки считаются по 90 минутам, поэтому:
  • STATUS_FULL_TIME — счёт = итог 90 минут;
  • овертайм / пенальти (AET, PEN) — после 90 минут была ничья, точный счёт неизвестен: исход 1X2 известен (ничья),
    остальные линии — возврат.
Коэффициенты: ESPN показывает линию DraftKings (П1/X/П2, тотал, фора; открытие и текущая) — не для всех турниров.
Экономия запросов: у каждого турнира есть календарь игровых дней (leagues[0].calendar) — запрашиваются только они.
"""
from __future__ import annotations

import datetime as dt
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import pandas as pd

BASE = 'https://site.api.espn.com/apis/site/v2/sports/soccer/{comp}/'
KO_WORDS = ('round-of', 'quarter', 'semi', 'final')


@dataclass
class FMatch:
    id: str
    comp: str
    start: pd.Timestamp       # UTC
    state: str                # pre / in / post
    status: str               # STATUS_FULL_TIME, STATUS_FINAL_AET, STATUS_FINAL_PEN, STATUS_SCHEDULED, STATUS_POSTPONED …
    home: str
    away: str
    hg: int | None
    ag: int | None
    neutral: bool = False
    stage: str = ''           # slug стадии: league-phase, round-of-16, quarterfinals …
    odds: dict = field(default_factory=dict)   # h, d, a (десятичные), line, over, under, provider

    @property
    def knockout(self) -> bool:
        s = self.stage.lower()
        return any(k in s for k in KO_WORDS) and 'playoff' not in s

    @property
    def result_kind(self) -> str:
        """'FT' — счёт 90 минут известен; 'ET' — ничья в основное время (овертайм/пенальти); 'VOID' — не сыгран; '' — не завершён."""
        s = self.status.upper()
        if self.state != 'post':
            return ''
        if 'AET' in s or 'PEN' in s or 'EXTRA' in s or 'SHOOTOUT' in s:
            return 'ET'
        if self.hg is not None and self.ag is not None and ('FULL_TIME' in s or s in ('STATUS_FINAL', 'STATUS_FT')):
            return 'FT'
        return 'VOID'


def _get(url: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (football-model)'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def american(x) -> float | None:
    """Американский кф (+185 / −150) → десятичный (2.85 / 1.667)."""
    try:
        s = str(x).strip().replace('−', '-')
        if s.upper() in ('EVEN', 'EV'):
            return 2.0
        v = float(s)
    except (TypeError, ValueError):
        return None
    if v == 0:
        return None
    return round(1 + v / 100, 4) if v > 0 else round(1 + 100 / -v, 4)


def _price(side: dict | None, key: str = 'odds') -> tuple[float | None, str]:
    side = side or {}
    for when in ('close', 'current', 'open'):
        v = (side.get(when) or {})
        if v.get(key) is not None:
            return american(v.get(key)), str(v.get('line', ''))
    return None, ''


def parse_odds(c: dict) -> dict:
    for o in c.get('odds') or []:
        if not o:
            continue
        ml, tot = o.get('moneyline') or {}, o.get('total') or {}
        h, _ = _price(ml.get('home')); a, _ = _price(ml.get('away')); d, _ = _price(ml.get('draw'))
        if d is None and (o.get('drawOdds') or {}).get('moneyLine') is not None:
            d = american(o['drawOdds']['moneyLine'])
        ov, ln = _price(tot.get('over')); un, _ = _price(tot.get('under'))
        try:
            line = float(str(ln).lstrip('ouOU')) if ln else float(o.get('overUnder'))
        except (TypeError, ValueError):
            line = None
        if h and d and a:
            return dict(h=h, d=d, a=a, line=line, over=ov, under=un,
                        provider=str((o.get('provider') or {}).get('name', '')))
    return {}


def parse(js: dict, comp: str) -> list[FMatch]:
    out = []
    for ev in js.get('events', []) or []:
        for c in ev.get('competitions', []) or []:
            comp_ = c.get('competitors') or []
            if len(comp_) != 2:
                continue
            h = next((x for x in comp_ if x.get('homeAway') == 'home'), comp_[0])
            a = next((x for x in comp_ if x.get('homeAway') == 'away'), comp_[1])
            st = (c.get('status') or ev.get('status') or {}).get('type') or {}

            def score(x):
                try:
                    return int(float(x.get('score')))
                except (TypeError, ValueError):
                    return None
            start = pd.Timestamp(c.get('date') or ev.get('date'))
            start = start.tz_convert('UTC').tz_localize(None) if start.tzinfo else start
            out.append(FMatch(id=str(ev.get('id') or c.get('id')), comp=comp, start=start, state=st.get('state', ''),
                              status=st.get('name', ''), home=((h.get('team') or {}).get('displayName') or '').strip(),
                              away=((a.get('team') or {}).get('displayName') or '').strip(), hg=score(h), ag=score(a),
                              neutral=bool(c.get('neutralSite')), stage=str((ev.get('season') or {}).get('slug', '')),
                              odds=parse_odds(c)))
    return out


def _scoreboard(comp: str, d: dt.date) -> dict:
    return _get(BASE.format(comp=comp) + f'scoreboard?dates={d:%Y%m%d}')


def calendar(js: dict) -> set[dt.date]:
    """Игровые дни турнира из ответа scoreboard (пусто — календаря нет)."""
    out = set()
    for lg in js.get('leagues', []) or []:
        for x in lg.get('calendar', []) or []:
            s = x if isinstance(x, str) else (x.get('startDate') or x.get('value') or '') if isinstance(x, dict) else ''
            try:
                out.add(pd.Timestamp(s).date())
            except (ValueError, TypeError):
                pass
    return out


def fetch(plan: dict[str, list[dt.date]], workers: int = 8) -> tuple[list[FMatch], list[str]]:
    """plan: турнир → нужные даты. Первый запрос по турниру (сегодня) даёт календарь; остальные даты — только игровые.
    Если календаря нет — запрашиваются все даты."""
    today = dt.datetime.now(dt.timezone.utc).date()
    seen: dict[str, FMatch] = {}
    errors: list[str] = []

    def one(comp: str):
        got, errs = [], []
        try:
            js = _scoreboard(comp, today)
        except Exception as e:
            return got, [f'{comp}: {e.__class__.__name__}']
        got += parse(js, comp)
        cal = calendar(js)
        lo, hi = (min(cal), max(cal)) if cal else (None, None)
        for d in sorted(set(plan[comp]) - {today}):
            if cal and lo <= d <= hi and d not in cal:          # внутри сезона — только игровые дни
                continue
            try:
                got += parse(_scoreboard(comp, d), comp)
            except Exception as e:
                errs.append(f'{comp} {d}: {e.__class__.__name__}')
        return got, errs
    with ThreadPoolExecutor(workers) as ex:
        for got, errs in ex.map(one, list(plan)):
            for m in got:
                seen[m.id] = m
            errors += errs
    return sorted(seen.values(), key=lambda m: (m.start, m.id)), errors


def summary(comp: str, event_id: str) -> dict:
    return _get(BASE.format(comp=comp) + f'summary?event={event_id}')


def shots_on_target(js: dict) -> tuple[float, float] | None:
    """T30: удары в створ хозяев и гостей из ESPN summary (boxscore.teams[].statistics: shotsOnTarget)."""
    side = {}
    for c in ((js.get('header') or {}).get('competitions') or [{}])[0].get('competitors', []) or []:
        side[str((c.get('team') or {}).get('id', ''))] = c.get('homeAway', '')
    got = {}
    for t in (js.get('boxscore') or {}).get('teams', []) or []:
        ha = t.get('homeAway') or side.get(str((t.get('team') or {}).get('id', '')), '')
        for s in t.get('statistics', []) or []:
            if s.get('name') == 'shotsOnTarget':
                try:
                    got[ha] = float(s.get('displayValue', s.get('value')))
                except (TypeError, ValueError):
                    pass
    return (got['home'], got['away']) if 'home' in got and 'away' in got else None


def lineups(comp: str, event_id: str, js: dict | None = None) -> list[dict]:
    """Составы матча из ESPN summary: игрок, команда, в старте ли, позиция. Пусто — составов нет."""
    js = js if js is not None else summary(comp, event_id)
    out = []
    for r in js.get('rosters', []) or []:
        team = ((r.get('team') or {}).get('displayName') or '').strip()
        for p in r.get('roster', []) or []:
            ath = p.get('athlete') or {}
            out.append(dict(event=event_id, team=team, home=r.get('homeAway', ''), player_id=str(ath.get('id', '')),
                            player=ath.get('displayName', ''), starter=bool(p.get('starter')),
                            pos=(p.get('position') or {}).get('abbreviation', ''), sub_in=bool(p.get('subbedIn')),
                            sub_out=bool(p.get('subbedOut'))))
    return out


def score90(comp: str, eid: str) -> tuple[int, int] | None:
    """21.11: счёт 90 минут матча с доп. временем / пенальти. Табло (scoreboard) даёт только итог 120 минут; карточка матча
    (summary, header.competitions[0].competitors[].linescores) — по периодам: 1-й тайм, 2-й тайм, доп. время, пенальти.
    Проверено на 2 матчах (engine/research/et_probe.py): Fleetwood — Rochdale 1:2 после ДВ → 1:1 за 90 минут."""
    js = _get(BASE.format(comp=comp) + f'summary?event={eid}')
    c = ((js.get('header') or {}).get('competitions') or [{}])[0]
    sc = {}
    for x in c.get('competitors') or []:
        ls = x.get('linescores') or []
        if len(ls) < 2:
            return None
        try:
            sc[x.get('homeAway')] = int(float(ls[0].get('displayValue', ls[0].get('value')))) + int(float(ls[1].get('displayValue', ls[1].get('value'))))
        except (TypeError, ValueError):
            return None
    return (sc['home'], sc['away']) if 'home' in sc and 'away' in sc else None

