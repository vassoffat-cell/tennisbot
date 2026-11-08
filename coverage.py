"""Полный охват матчей в аналитике (запрос пользователя 08.11.2026): ни один прогноз не теряется.

  • Матч, доигранный ПОСЛЕ суточного отчёта (лайв, перенос, поздний результат ESPN), рассчитывается в следующих запусках
    (каждые 2 ч) и попадает в «7 дн» / «всё время»; в следующем суточном отчёте — отдельной строкой «⏱ Дорасчитано за прошлые дни».
  • Матчи без результата > 36 ч — строка «⏳ Ждут результата»; теннис: запасной расчёт по TennisMyLife (data/tml, обновляется
    ежедневно) для зависших > 48 ч — по фамилиям обоих игроков и дате турнира.
  • Учёт показанного — rt['reported'] (id матчей, уже вошедших в суточные отчёты, последние 60 дней).
"""
from __future__ import annotations

import re
import unicodedata

import pandas as pd

import config as C
import journal as J

norm = lambda s: re.sub(r'[^a-z]', '', unicodedata.normalize('NFKD', str(s).lower()).encode('ascii', 'ignore').decode())


def _last(name: str) -> str:
    return norm(str(name).split()[-1]) if str(name).strip() else ''


def report_rows(M: pd.DataFrame, L: pd.DataFrame, af: pd.DataFrame, daily_day, rt: dict, fmt) -> list[str]:
    """Строки суточного отчёта: дорасчитано за прошлые дни и ждут результата. Обновляет rt['reported']."""
    if 'reported' not in rt:                                        # первый отчёт с учётом: всё прошлое уже «показано»
        rt['reported'] = sorted({str(J.num(r.espn_id)) for r in M.itertuples() if r.day < daily_day}) if len(M) else []
    rep = {str(J.num(x)) for x in rt['reported']}
    out = []
    if len(M):
        late = M[(M.day < daily_day) & ~M.espn_id.map(lambda x: str(J.num(x))).isin(rep)]
        if len(late):
            s = f'⏱ Дорасчитано за прошлые дни: {len(late)} — исход {int(late.hit.sum())}/{len(late)}'
            if len(L):
                ll = L[L.espn_id.map(lambda x: str(J.num(x))).isin({str(J.num(x)) for x in late.espn_id})]
                if len(ll):
                    s += f', линии {int(ll.won.sum())}/{len(ll)}'
            out.append(s + ' (уже в «7 дн» и «всего»)')
    now = pd.Timestamp.now(tz='UTC').tz_localize(None)
    pend = af[af.settled.astype(str).str.strip().isin(['', 'nan'])] if len(af) else af
    if len(pend):
        st = pd.to_datetime(pend.start_utc, errors='coerce')
        old = pend[st < now - pd.Timedelta(hours=36)]
        if len(old):
            out.append(f'⏳ Ждут результата: {len(old)} (начались > 36 ч назад) — бот перепроверяет каждые 2 ч')
    keep = now.date() - pd.Timedelta(days=60).to_pytimedelta()
    rt['reported'] = sorted({str(J.num(r.espn_id)) for r in M.itertuples() if keep <= r.day <= daily_day}) if len(M) else []
    return out


def tml_results(tour: str) -> pd.DataFrame:
    """Результаты TennisMyLife текущего и прошлого сезона (+ текущие турниры): фамилии, дата турнира, счёт."""
    files = {'atp': ('{y}.csv', 'ongoing_tourneys.csv'), 'wta': ('{y}_wta.csv', 'wta_ongoing_tourneys.csv')}[tour]
    y = pd.Timestamp.now().year
    parts = []
    for name in (files[0].format(y=y - 1), files[0].format(y=y), files[1]):
        f = C.DATA / 'tml' / name
        if f.exists():
            parts.append(pd.read_csv(f, usecols=['tourney_date', 'winner_name', 'loser_name', 'score'], low_memory=False))
    if not parts:
        return pd.DataFrame(columns=['tdate', 'w', 'l', 'score'])
    t = pd.concat(parts, ignore_index=True)
    t['tdate'] = pd.to_datetime(t.tourney_date.astype(str).str[:8], format='%Y%m%d', errors='coerce')
    t['w'], t['l'] = t.winner_name.map(_last), t.loser_name.map(_last)
    return t.dropna(subset=['tdate'])


def parse_score(score: str) -> tuple[tuple[tuple[int, int], ...], bool]:
    """«6-4 3-6 7-6(5) RET» → ((6,4),(3,6),(7,6)), снятие=True (счёт с позиции победителя)."""
    s = str(score)
    ret = bool(re.search(r'RET|W/O|DEF|ABD', s, re.I))
    sets = tuple((int(a), int(b)) for a, b in re.findall(r'(\d+)-(\d+)(?:\(\d+\))?', s))
    return sets, ret


def tml_settle(af: pd.DataFrame, tour: str, hours: float = 48) -> dict[int, dict]:
    """Запасной итог для зависших > hours матчей тура: {индекс строки: {status, winner (1|2), score (с позиции игрока 1)}}."""
    now = pd.Timestamp.now(tz='UTC').tz_localize(None)
    pend = af[(af.tour.astype(str).str.lower() == tour) & af.settled.astype(str).str.strip().isin(['', 'nan'])]
    if pend.empty:
        return {}
    st = pd.to_datetime(pend.start_utc, errors='coerce')
    pend = pend[st < now - pd.Timedelta(hours=hours)]
    if pend.empty:
        return {}
    t = tml_results(tour)
    out = {}
    for i, r in pend.iterrows():
        a, b = _last(r.player1), _last(r.player2)
        d = pd.Timestamp(str(r.start_utc))
        cand = t[(t.tdate <= d + pd.Timedelta(days=1)) & (t.tdate >= d - pd.Timedelta(days=16))
                 & (((t.w == a) & (t.l == b)) | ((t.w == b) & (t.l == a)))]
        if len(cand) != 1:
            continue
        x = cand.iloc[0]
        sets, ret = parse_score(x.score)
        if not sets and not ret:
            continue
        p1_won = x.w == a
        sets1 = sets if p1_won else tuple((q, p) for p, q in sets)
        out[i] = dict(status='RET' if ret else 'FIN', winner=1 if p1_won else 2, score=' '.join(f'{p}-{q}' for p, q in sets1))
    return out
