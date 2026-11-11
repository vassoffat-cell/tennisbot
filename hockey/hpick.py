"""⭐ Хоккей · ставка дня — по правилам тенниса и футбола (dpcore), отдельным сообщением в «Заметки Т».

Хоккейные сутки 12:00 → 12:00 Ташкента (hrun.hday). Публикация — первым прогоном суток (12:07, вместе с отчётом).
Кандидат: показываемая линия публичной лиги, матч суток ещё не начался, P 0,65–0,80 (уверенно, кф ≥ 1,25 — правило проекта).
Порядок: 🔥 (капер ставит то же) → без ⚔️ (капер против) → ступень (🟢/🟡) → нижняя граница P.
До 3 ставок в сутки (⭐1 + ⭐2/⭐3 — другие матчи, только 🟢 или 🔥, без ⚔️).
Замена ⭐1 — только до начала её матча и только по каперам (как в теннисе — «в любое время суток»):
  у текущей ⚔️ и есть кандидат без ⚔️; или кандидат 🔥 при текущей без 🔥 и P ниже не более чем на 3 п.п.
Заменённая ставка остаётся в листе (status «замена») и тоже получает итог → в отчёте видно, помогла ли замена.
Кнопок ✅/🔄 нет: их разбирает Apps Script теннисного бота (dp:yes/dp:no) — общие кнопки сбили бы теннисную ставку.
Лист hk_day_pick таблицы Tennis.
"""
from __future__ import annotations

import pandas as pd

import config as C
import journal as J
import notify
from hockey import hlines as HL

DP_COLS = ['date', 'rank', 'id', 'league', 'start_utc', 'start_local', 'home', 'away', 'slot', 'market', 'pick', 'line',
           'label', 'p', 'fair_k', 'fire', 'clash', 'status', 'reason', 'result', 'settled', 'created']
J.SHEETS.setdefault('hk_day_pick', DP_COLS)
P_MIN, P_MAX = 0.65, 0.80
MAX_PICKS = 3
LMARK = {'W': '✅', 'L': '❌', 'P': '⚪'}


def _blank(x) -> bool:
    return str(x).strip() in ('', 'nan', 'None')


def _num(x):
    try:
        return float(str(x).replace(',', '.'))
    except Exception:
        return float('nan')


def _syn(c: dict, tp: pd.DataFrame) -> tuple[int, int]:
    """(🔥, ⚔️) для линии: капер на тот же матч и рынок — тот же выбор / противоположный."""
    if tp is None or not len(tp):
        return 0, 0
    t = tp[(tp.game_id.astype(str) == str(c['id'])) & (tp.market.astype(str) == str(c['market']))]
    fire = int((t.pick.astype(str) == str(c['pick'])).any())
    clash = int((t.pick.astype(str) != str(c['pick'])).any())
    return fire, clash


def candidates(items: list[tuple[dict, list[dict]]], tp: pd.DataFrame, leagues: tuple, lo: pd.Timestamp, hi: pd.Timestamp,
               now: pd.Timestamp, tier_fn) -> list[dict]:
    out = []
    for row, lns in items:
        st = pd.Timestamp(row['start_utc'])
        if row['league'] not in leagues or not (lo <= st < hi) or st <= now + pd.Timedelta(minutes=10):
            continue
        for x in lns:
            p = float(x['p'])
            if not int(x['shown']) or x['market'] == '1X2' or not (P_MIN <= p <= P_MAX):
                continue
            c = dict(id=row['id'], league=row['league'], start_utc=row['start_utc'], start_local=row['start_local'],
                     home=row['home'], away=row['away'], slot=int(x['slot']), market=x['market'], pick=x['pick'],
                     line=x['line'], label=x['label'], p=round(p, 4), p_low=float(x['p_low']), tier=tier_fn(x, row['league']))
            c['fire'], c['clash'] = _syn(c, tp)
            out.append(c)
    return sorted(out, key=lambda c: (-c['fire'], c['clash'], -c['tier'], -c['p_low'], -c['p']))


def _choose(cands: list[dict]) -> list[dict]:
    if not cands:
        return []
    first = next((c for c in cands if not c['clash']), cands[0])
    picks, games = [first], {first['id']}
    for c in cands:
        if len(picks) >= MAX_PICKS:
            break
        if c['id'] in games or c['clash'] or not (c['tier'] >= 3 or c['fire']):
            continue
        picks.append(c); games.add(c['id'])
    return picks


def _line(c: dict, e) -> str:
    syn = (' 🔥' if c['fire'] else '') + (' ⚔️' if c['clash'] else '')
    k = f"{1 / c['p']:.2f}".replace('.', ',')
    return (f"🕒 {e(c['start_local'])} · {e(c['home'])} — {e(c['away'])}\n"
            f"<b>{e(c['label'])} — {c['p'] * 100:.0f}%</b> · честный кф {k}{syn}")


def _text(day, picks: list[dict], note: str = '') -> str:
    e = notify.esc
    out = [f"⭐ <b>Хоккей · ставка дня {day:%d.%m}</b>",
           _line(picks[0], e)]
    if len(picks) > 1:
        out.append('\n⭐ <b>Ещё уверенные</b>')
        out += [f"⭐{i + 2} " + _line(c, e) for i, c in enumerate(picks[1:])]
    out.append('<i>линия — основное время; ставка в плюс, если кф букмекера выше честного; 🔥/⚔️ — капер за/против</i>')
    if note:
        out.append(f'🔁 {e(note)}')
    return '\n'.join(out)


def _row(day, rank: int, c: dict, status: str = '', reason: str = '') -> dict:
    return dict(date=str(day), rank=rank, id=c['id'], league=c['league'], start_utc=c['start_utc'], start_local=c['start_local'],
                home=c['home'], away=c['away'], slot=c['slot'], market=c['market'], pick=c['pick'], line=c['line'],
                label=c['label'], p=c['p'], fair_k=round(1 / c['p'], 2), fire=c['fire'], clash=c['clash'], status=status,
                reason=reason, result='', settled='', created=J.now_utc())


def process(store: J.Store, rt: dict, items, tp, leagues: tuple, day, lo, hi, now, tier_fn, log: list[str]) -> None:
    st = rt.get('hk_dp') or {}
    cands = candidates(items, tp, leagues, lo, hi, now, tier_fn)
    if st.get('date') != str(day):
        picks = _choose(cands)
        if not picks:
            log.append(f'⭐🏒 {day:%d.%m}: нет уверенных кандидатов ({len(cands)})')
            return
        msgs = notify.send_blocks([_text(day, picks)])
        store.append('hk_day_pick', [_row(day, i + 1, c) for i, c in enumerate(picks)])
        rt['hk_dp'] = dict(date=str(day), msgs=[list(m) for m in msgs], picks=picks, log=[])
        log.append(f"⭐🏒 {day:%d.%m}: {picks[0]['label']} {picks[0]['home']}–{picks[0]['away']} "
                   f"P {picks[0]['p']:.2f}, всего {len(picks)}")
        return
    picks = st.get('picks') or []
    if not picks or pd.Timestamp(picks[0]['start_utc']) <= now:
        return                                                    # матч начался — замок
    cur = picks[0]
    cur['fire'], cur['clash'] = _syn(cur, tp)
    alt = [c for c in cands if (c['id'], c['slot']) != (cur['id'], cur['slot'])]
    new, reason = None, ''
    if cur['clash']:
        new = next((c for c in alt if not c['clash']), None); reason = 'каперы против (⚔️)'
    elif not cur['fire']:
        new = next((c for c in alt if c['fire'] and not c['clash'] and c['p'] >= cur['p'] - 0.03), None); reason = 'каперы за (🔥)'
    if new is None:
        return
    dp = store.read('hk_day_pick')
    ix = [i for i, r in dp.iterrows() if str(r.date) == str(day) and str(r.id) == str(cur['id'])
          and int(_num(r.slot)) == int(cur['slot']) and _blank(r.status)] if len(dp) else []
    if ix:
        dp.loc[ix, ['status', 'reason']] = ['замена', reason]
        store.update('hk_day_pick', dp.loc[ix], ['status', 'reason'])
    picks = [new] + [c for c in picks[1:] if c['id'] != new['id']]
    dp = store.read('hk_day_pick')
    jx = [i for i, r in dp.iterrows() if str(r.date) == str(day) and str(r.id) == str(new['id'])
          and int(_num(r.slot)) == int(new['slot']) and _blank(r.status)] if len(dp) else []
    if jx:                                                        # новая ⭐1 была ⭐2/⭐3 — повышение, без дубля
        dp.loc[jx, ['rank', 'reason']] = [1, reason]
        store.update('hk_day_pick', dp.loc[jx], ['rank', 'reason'])
    else:
        store.append('hk_day_pick', [_row(day, 1, new, reason=reason)])
    notify.delete([tuple(m) for m in st.get('msgs', [])])
    note = f"замена {now + pd.Timedelta(hours=C.TZ_OFFSET_HOURS):%H:%M}: было {cur['label']} {cur['home']}–{cur['away']} — {reason}"
    msgs = notify.send_blocks([_text(day, picks, note)])
    st.update(msgs=[list(m) for m in msgs], picks=picks)
    st.setdefault('log', []).append(note)
    rt['hk_dp'] = st
    log.append(f'⭐🏒 {note} → {new["label"]}')


def settle(store: J.Store, games: pd.DataFrame) -> int:
    dp = store.read('hk_day_pick')
    if not len(dp):
        return 0
    res = {str(r.id): r for r in games[games.end.astype(str).isin(['REG', 'OT', 'SO'])].itertuples(index=False)}
    ix = []
    for i, r in dp[dp.settled.map(_blank)].iterrows():
        x = res.get(str(r.id))
        if x is None:
            continue
        dp.loc[i, ['result', 'settled']] = [HL.settle(str(r.market), str(r.pick), r.line, x.end, x.hg, x.ag, x.hg_reg, x.ag_reg),
                                            pd.Timestamp.now().strftime('%Y-%m-%d')]
        ix.append(i)
    if ix:
        store.update('hk_day_pick', dp.loc[ix], ['result', 'settled'])
    return len(ix)


def rows(dp: pd.DataFrame, d0, d1) -> dict:
    """Итоги за хоккейные сутки d0…d1: ⭐1 (итоговая), ⭐2–3, заменённые."""
    if not len(dp):
        return {}
    x = dp.copy()
    x['d'] = pd.to_datetime(x.date, errors='coerce').dt.date
    if d0 is not None:
        x = x[(x.d >= d0) & (x.d <= d1)]
    x = x[x.result.astype(str).isin(['W', 'L'])].copy()
    if not len(x):
        return {}
    x['won'] = (x.result.astype(str) == 'W').astype(int)
    x['p'] = x.p.map(_num)
    act = x[x.status.map(_blank)]
    r1, r23, rep = act[act['rank'].map(_num) == 1], act[act['rank'].map(_num) > 1], x[x.status.astype(str) == 'замена']
    return {k: (int(v.won.sum()), len(v), float(v.p.mean()) if len(v) else 0.0) for k, v in
            (('main', r1), ('extra', r23), ('replaced', rep))}
