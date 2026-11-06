"""Ситуационные факторы матча чемпионата (проверены на 41 480 матчах 2020–2026 против модели и Pinnacle):
  rest        — дней с прошлого матча (чемпионат + еврокубки, как в проверке);
  eu_prev     — еврокубок за ≤4 дня до матча;
  eu_next_ko  — плей-офф еврокубка через ≤4 дня (ротация);
  dead        — команде математически нечего играть (чемпионство, еврозона, вылет решены), высшие дивизионы.
Поправки (log λ команды, совместная пуассоновская регрессия, research/oos_gain.py):
  режим «модель» — FACTORS_MODEL; режим «рынок» — только eu_prev (рынок недооценивает, −4,5%)."""
from __future__ import annotations

import numpy as np
import pandas as pd

import config as C


class Schedule:
    def __init__(self, league: pd.DataFrame, uefa: pd.DataFrame) -> None:
        """league: date, home, away (все матчи чемпионатов); uefa: date, team, ko."""
        a = pd.concat([league[['date', 'home']].rename(columns={'home': 'team'}).assign(kind='L', ko=False),
                       league[['date', 'away']].rename(columns={'away': 'team'}).assign(kind='L', ko=False),
                       uefa[['date', 'team', 'ko']].assign(kind='U')], ignore_index=True)
        a = a[a.team.notna()]
        a['day'] = pd.to_datetime(a.date).dt.normalize()
        a = a.drop_duplicates(['day', 'team', 'kind']).sort_values(['team', 'day'])
        self.t = {t: (g.day.values.astype('datetime64[D]').astype(np.int64), g.kind.values, g.ko.values.astype(bool))
                  for t, g in a.groupby('team')}

    def features(self, team: str, day: pd.Timestamp) -> dict:
        x = np.datetime64(pd.Timestamp(day).normalize().date(), 'D').astype(np.int64)
        if team not in self.t:
            return dict(rest=np.nan, eu_prev=0, eu_next_ko=0)
        d, kind, ko = self.t[team]
        k = np.searchsorted(d, x)                       # матчи строго раньше этого дня — d[:k]
        rest = float(x - d[k - 1]) if k > 0 else np.nan
        j = np.searchsorted(d, x, side='right')         # строго позже
        eu_prev = any(kind[m] == 'U' and 0 < x - d[m] <= 4 for m in range(max(0, k - 3), k))
        eu_ko = any(kind[m] == 'U' and ko[m] and 0 < d[m] - x <= 4 for m in range(j, min(len(d), j + 3)))
        return dict(rest=min(rest, 30.0) if rest == rest else np.nan, eu_prev=int(eu_prev), eu_next_ko=int(eu_ko))


def dead_flags(season: pd.DataFrame, div: str, day: pd.Timestamp) -> dict[str, int]:
    """Команды высшего дивизиона, которым нечего играть, по таблице до дня матча (season — матчи сезона этого дивизиона)."""
    if div not in C.TOPDIV:
        return {}
    E, R = C.TOPDIV[div]
    s = season[season.date < pd.Timestamp(day).normalize()]
    teams = sorted(set(season.home) | set(season.away))
    n = len(teams)
    if n < 10 or s.empty:
        return {}
    ti = {t: k for k, t in enumerate(teams)}
    pts, pl = np.zeros(n), np.zeros(n)
    for r in s.itertuples(index=False):
        h, a = ti[r.home], ti[r.away]
        pl[h] += 1; pl[a] += 1
        if r.hg > r.ag:
            pts[h] += 3
        elif r.hg < r.ag:
            pts[a] += 3
        else:
            pts[h] += 1; pts[a] += 1
    rem = np.maximum(2 * (n - 1) - pl, 0)
    out = {}
    for t, i in ti.items():
        others = np.arange(n) != i
        mx_o, mx_i = pts[others] + 3 * rem[others], pts[i] + 3 * rem[i]
        dead = True
        for b in (1, E, n - R):
            can_up = np.sum(pts[others] > mx_i) < b          # ещё может оказаться в первых b
            can_down = np.sum(mx_o >= pts[i]) >= b           # ещё могут вытолкнуть за b
            if can_up and can_down:
                dead = False
                break
        out[t] = int(dead)
    return out


def adjust(lh: float, la: float, fh: dict, fa: dict, mode: str) -> tuple[float, float]:
    """Поправка λ на факторы. mode: 'model' — все проверенные поправки; 'market' — только еврокубок ≤4 дня назад."""
    if mode == 'market':
        b = C.EU_PREV_MARKET
        return lh * np.exp(b * fh.get('eu_prev', 0)), la * np.exp(b * fa.get('eu_prev', 0))
    B = C.FACTORS_MODEL
    sh = int(fh.get('rest', 99) <= 3) if fh.get('rest') == fh.get('rest') else 0
    sa = int(fa.get('rest', 99) <= 3) if fa.get('rest') == fa.get('rest') else 0

    def eta(own, opp, s_own, s_opp):
        return (B['const'] + B['own_eu_prev'] * own.get('eu_prev', 0) + B['opp_eu_prev'] * opp.get('eu_prev', 0)
                + B['own_eu_next_ko'] * own.get('eu_next_ko', 0) + B['opp_eu_next_ko'] * opp.get('eu_next_ko', 0)
                + B['own_dead'] * own.get('dead', 0) + B['opp_dead'] * opp.get('dead', 0)
                + B['own_short'] * s_own * (1 - s_opp) + B['opp_short'] * s_opp * (1 - s_own) + B['both_short'] * s_own * s_opp)
    return lh * float(np.exp(eta(fh, fa, sh, sa))), la * float(np.exp(eta(fa, fh, sa, sh)))


def describe(f: dict) -> str:
    """Короткая пометка факторов команды для журнала: «ЕК−3д», «ПО ЕК+4д», «нечего играть», «отдых 2д»."""
    out = []
    if f.get('eu_prev'):
        out.append('после ЕК')
    if f.get('eu_next_ko'):
        out.append('плей-офф ЕК впереди')
    if f.get('dead'):
        out.append('нечего играть')
    if f.get('rest') == f.get('rest') and f.get('rest', 99) <= 3:
        out.append(f"отдых {int(f['rest'])}д")
    return ', '.join(out)
