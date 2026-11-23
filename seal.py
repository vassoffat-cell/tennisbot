"""🔏 Печать прогнозов суток (решение пользователя 15.11.2026 19:44).

Зачем: пользователь не видит списков прогнозов (решение 11.11 — списки не публикуются) и должен иметь возможность убедиться,
что отчёт не подгоняется задним числом. Поэтому:
  1. Каждый прогон пишет снимок ВСЕХ линий суток в файл репозитория (<data>/seal/<вид>_ГГГГ-ММ-ДД.csv). Строки матча, который уже
     начался, больше НЕ меняются (заморожены последней версией до начала). Файл коммитится облаком — время коммита ставит GitHub,
     история версий файла = чекпоинты.
  2. Утренний первый приговор (⭐1) получает строку «🔎»: сколько матчей и линий рассмотрено за сутки, сколько матчей подошли на
     ставку дня, разбивка по турнирам / лигам и 🔏 код — первые 8 знаков SHA-256 снимка в момент объявления (время ставит Telegram).
Команды не перечисляются. Расход — доли секунды, файл ~10–60 КБ в сутки.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import pandas as pd

COLS = ['match_id', 'start_utc', 'group', 'market', 'pick', 'line', 'p']
SURF = {'hard': 'хард', 'clay': 'грунт', 'grass': 'трава', 'carpet': 'ковёр'}
LEAGUE_RU = {'NHL': 'НХЛ', 'KHL': 'КХЛ'}


def _short_event(ev: str) -> str:
    for w in ('Nitto ', 'FAGE ', 'Bybit ', 'Prudential ', 'BNP Paribas ', 'Rolex ', 'Mutua ', 'Internazionali BNL d\'Italia'):
        ev = ev.replace(w, '')
    return ev.strip()[:24]


NAMES: dict[str, list] = {}       # 21.11: match_id → [игрок 1, игрок 2, начало UTC] — вердикты каперов по теннису (tipsbook._tennis_rel)


def rows(sport: str, items: list) -> list[dict]:
    """Линии прогона → строки печати. Теннис/футбол: items = [(m, row, lines)], хоккей: [(row, lines)]."""
    out = []
    for it in items:
        if sport == 'hockey':
            row, lns = it
            mid, grp = str(row.get('id')), LEAGUE_RU.get(str(row.get('league')), str(row.get('league')))
        else:
            m, row, lns = it
            mid = str(getattr(m, 'id', row.get('espn_id', '')))
            if sport == 'tennis':
                NAMES[mid] = [str(row.get('player1', '')), str(row.get('player2', '')), str(row.get('start_utc', ''))[:16]]
                x0 = lns[0] if lns else {}
                surf = SURF.get(str(x0.get('surface', row.get('surface', ''))).lower(), str(x0.get('surface', '')).lower())
                grp = f"{_short_event(str(x0.get('event', getattr(m, 'event', ''))))}, {surf}".strip(', ')
            else:
                grp = str(row.get('comp_name') or row.get('comp') or '')
        for x in lns:
            line = x.get('line', '')
            out.append(dict(match_id=mid, start_utc=str(row.get('start_utc', ''))[:16], group=grp, market=str(x.get('market', '')),
                            pick=str(x.get('pick', '')), line='' if line in (None, '') or str(line) == 'nan' else line,
                            p=round(float(x.get('p', 0) or 0), 4)))
    return out


def cand_rows(cands: list[dict], id_key: str = 'espn_id') -> list[dict]:
    """16.11: линии-кандидаты ставки дня (в т.ч. линия букмекера, напр. ТБ 18.5 при фикс. 22.5) — тоже в снимок."""
    out = []
    for c in cands or []:
        line = c.get('line', '')
        out.append(dict(match_id=str(c.get(id_key, '')), start_utc=str(c.get('start_utc', ''))[:16], group='', market=str(c.get('market', '')),
                        pick=str(c.get('pick', '')), line='' if line in (None, '') or str(line) == 'nan' else f'{float(line):g}',
                        p=round(float(c.get('p', 0) or 0), 4)))
    return out


def seal(sport: str, items: list, lo, hi, now, folder: Path, day, extra: list[dict] | None = None) -> dict:
    """Снимок суток [lo, hi) (UTC): начавшиеся матчи заморожены, будущие — свежие. Возвращает счётчики и код снимка.
    extra — строки кандидатов ставки дня (cand_rows); группа берётся у линий того же матча."""
    lo, hi, now = pd.Timestamp(lo), pd.Timestamp(hi), pd.Timestamp(now)
    new = pd.DataFrame(rows(sport, items), columns=COLS)
    if extra:
        ex = pd.DataFrame(extra, columns=COLS)
        grp = dict(zip(new.match_id, new.group))
        ex['group'] = ex.match_id.map(grp).fillna('')
        new = pd.concat([new, ex], ignore_index=True)
        new['line'] = new.line.map(lambda v: '' if str(v) in ('', 'nan', 'None') else f'{float(v):g}')
        new = new.drop_duplicates(['match_id', 'market', 'pick', 'line'], keep='first')
    st = pd.to_datetime(new.start_utc, errors='coerce')
    new = new[(st >= lo) & (st < hi) & (st > now)]
    f = Path(folder) / 'seal' / f'{sport}_{day}.csv'
    old = pd.read_csv(f, dtype=str, keep_default_na=False) if f.exists() else pd.DataFrame(columns=COLS)
    if len(old):                                                  # начавшиеся (их нет в прогоне) — заморожены; будущие — свежая версия
        old = old[~old.match_id.isin(set(new.match_id))]
    snap = pd.concat([old.astype(str), new.astype(str)], ignore_index=True).sort_values(['start_utc', 'match_id', 'market', 'pick', 'line'])
    f.parent.mkdir(parents=True, exist_ok=True)
    body = snap.to_csv(index=False)
    f.write_text(body, encoding='utf-8')
    if sport == 'tennis' and NAMES:                              # 21.11: имена игроков матчей снимка (≤ 3 суток)
        nf = Path(folder) / 'seal' / 'tennis_names.json'
        try:
            nm = json.loads(nf.read_text(encoding='utf-8')) if nf.exists() else {}
        except Exception:
            nm = {}
        nm.update(NAMES)
        edge = (now - pd.Timedelta(days=3)).strftime('%Y-%m-%d')
        nf.write_text(json.dumps({k: v for k, v in nm.items() if v[2][:10] >= edge}, ensure_ascii=False), encoding='utf-8')
        pf = Path(folder) / 'seal' / 'tennis_people.json'           # 21.11: игроки ATP/WTA 2025–26 (имя, фамилия) — прогону каперов
        if not pf.exists() or pd.Timestamp(pf.stat().st_mtime, unit='s') < now - pd.Timedelta(days=1):
            ppl = set()
            for t in ('atp', 'wta'):
                for y in (now.year - 1, now.year):
                    f = Path(folder) / 'sackmann' / f'{t}_matches_{y}.csv'
                    if f.exists():
                        x = pd.read_csv(f, usecols=['winner_name', 'loser_name'], dtype=str).stack().dropna()
                        ppl |= {str(v) for v in x if len(str(v).split()) >= 2}
            ppl |= {n for v in nm.values() for n in v[:2] if len(str(n).split()) >= 2}
            if ppl:
                pf.write_text(json.dumps(sorted(ppl), ensure_ascii=False), encoding='utf-8')
    per = snap.drop_duplicates('match_id')
    return dict(n=int(len(per)), lines=int(len(snap)), groups=Counter(per.group), code=hashlib.sha256(body.encode()).hexdigest()[:8])


VERIFY_FROM = '2026-11-23'       # 23.11 (ORG IMP-08): сверка ⭐ ↔ снимок — с этих суток (раньше кандидаты в снимок не попадали)


def verify(dp: pd.DataFrame, folder: Path, sport: str, id_col: str, days: int = 7) -> str:
    """23.11 (ORG IMP-08): каждая ставка дня листа (day_pick / hk_day_pick) за `days` суток есть в снимке своих суток
    (тот же матч, рынок, исход, линия). Снимок пишется ДО выбора ⭐ каждым прогоном (кандидаты — cand_rows), поэтому
    ⭐ вне снимка = запись задним числом или сбой печати. Строка «🔏 ⭐ не в снимке» — ❌ в health."""
    if dp is None or dp.empty or not {'date', id_col, 'market', 'pick'} <= set(dp.columns):
        return f'🔏 ⭐ ↔ снимок ({sport}): журнала нет'
    ln = lambda v: '' if str(v) in ('', 'nan', 'None') else f'{float(v):g}'
    edge = max(VERIFY_FROM, str((pd.Timestamp.now() - pd.Timedelta(days=days)).date()))
    d = dp[(dp.date.astype(str) >= edge) & (dp[id_col].astype(str) != '')]
    miss, n, cache = [], 0, {}
    for r in d.itertuples(index=False):
        day = str(r.date)[:10]
        if day not in cache:
            f = Path(folder) / 'seal' / f'{sport}_{day}.csv'
            s = pd.read_csv(f, dtype=str, keep_default_na=False) if f.exists() else pd.DataFrame(columns=COLS)
            cache[day] = set(zip(s.match_id, s.market, s.pick, s.line.map(ln)))
        n += 1
        k = (str(getattr(r, id_col)), str(r.market), str(r.pick), ln(getattr(r, 'line', '')))
        if k not in cache[day]:
            miss.append(f"{day} {k[0]} {k[1]} {k[2]} {k[3]}".strip())
    if miss:
        return f'🔏 ⭐ не в снимке ({sport}): {len(miss)} из {n} — ' + '; '.join(miss[:5])
    return f'🔏 ⭐ ↔ снимок ({sport}): {n} из {n} ✅'


def _plural(n: int, one: str, few: str, many: str) -> str:
    n100, n10 = n % 100, n % 10
    return one if n10 == 1 and n100 != 11 else few if 2 <= n10 <= 4 and not 12 <= n100 <= 14 else many


def text(info: dict, n_cand: int) -> str:
    """«🔎 Сутки: 3 матча · 21 линия · на ставку дня — 2 · Турин, хард 2 · Индиан-Уэллс, хард 1 · 🔏 7f3a91c2»."""
    n, L = info['n'], info['lines']
    grp = ' · '.join(f'{g} {c}' for g, c in info['groups'].most_common(4) if g)
    more = len(info['groups']) - 4
    return (f"🔎 Сутки: {n} {_plural(n, 'матч', 'матча', 'матчей')} · {L} {_plural(L, 'линия', 'линии', 'линий')}"
            f" · на ставку дня — {n_cand}" + (f"\n{grp}" + (f' · ещё {more}' if more > 0 else '') if grp else '')
            + f" · 🔏 {info['code']}")
