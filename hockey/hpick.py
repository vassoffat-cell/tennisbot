"""⭐ Хоккей · ставка дня — те же правила, что у тенниса и футбола (dpcore.py, решение пользователя 11.11.2026 23:27).

Хоккейные сутки 12:00 → 12:00 Ташкента (hrun.hday).
  1. Утро суток (первый прогон, 12:07): ⭐1 — лучшая уверенная линия, отдельное закреплённое сообщение («первый приговор»).
  2. В течение суток, до начала матча ставки, по каперам (🔥/⚔️) или сравнению (пересчёт модели):
     • лучшая линия ТОГО ЖЕ матча → правка: сообщение ставки обновляется «мнение изменилось: было → стало (причина)»,
       короткое уведомление; прежняя версия остаётся в листе (status «правка») и тоже получает итог;
     • лучший кандидат ДРУГОГО матча (другой игры, лиги) → новая ставка ⭐2 / ⭐3 — отдельное закреплённое сообщение.
     «Лучше»: у текущей ⚔️, у новой нет; у новой 🔥, у текущей нет, P ниже не более чем на 3 п.п.; ступень выше;
     та же ступень и нижняя граница P выше на ≥ 2 п.п.
  3. После начала матча ставка — замок. Новые сутки — закрепы прошлых ставок сняты, уведомления удалены.
Кандидат: показываемая линия публичной лиги, P 0,65–0,80 (кф ≥ 1,25 — правило проекта), матч суток не начался.
Отчёт: ⭐1 итоговая, ➕ новые линии, ✏️ дни с правкой ⭐1 (чем реже, тем лучше), права итоговая / первая. Кнопок нет.
Лист hk_day_pick таблицы Tennis.
"""
from __future__ import annotations

import pandas as pd

import config as C
import journal as J
import notify
import selflearn as SL
from hockey import hlines as HL

DP_COLS = ['date', 'rank', 'id', 'league', 'start_utc', 'start_local', 'home', 'away', 'slot', 'market', 'pick', 'line',
           'label', 'p', 'fair_k', 'fire', 'clash', 'status', 'reason', 'result', 'settled', 'created']
J.SHEETS.setdefault('hk_day_pick', DP_COLS)
P_MIN, P_MAX = 0.65, 0.80
MAX_PICKS = 3                   # 14.11: запасное значение — лимит динамический (selflearn.max_picks)
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
    if len(t) and 'src' in t.columns:                       # 🧠 ⛔ слабый капер (selflearn) — его 🔥/⚔️ не учитываются
        t = t[t.src.astype(str).map(lambda s: SL.capper('hockey', s) != 'weak')]
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
    return SL.reorder('hockey', sorted(out, key=lambda c: (-c['fire'], c['clash'], -c['tier'], -c['p_low'], -c['p'])))


def better(a: dict, b: dict | None) -> str:
    """Причина, по которой кандидат a лучше текущей ставки b ('' — не лучше). 🧠 Самообучение (selflearn): отключённые
    причины правок, пониженные сегменты, добавочный запас."""
    why = _better(a, b)
    return why if SL.allow('hockey', why, a, b) else ''


def _better(a: dict, b: dict | None) -> str:
    if not b:
        return 'сравнение'
    if b.get('clash') and not a.get('clash'):
        return 'каперы против (⚔️)'
    if a.get('clash'):
        return ''
    if a.get('fire') and not b.get('fire') and a['p'] >= b['p'] - 0.03:
        return 'каперы за (🔥)'
    if a['tier'] > b['tier']:
        return 'сравнение (ступень выше)'
    if a['tier'] == b['tier'] and a['p_low'] >= b['p_low'] + 0.02 + SL.switch_extra('hockey'):
        return f"сравнение (P +{(a['p_low'] - b['p_low']) * 100:.0f} п.п.)"
    return ''


def _line(c: dict, e) -> str:
    syn = (' 🔥' if c['fire'] else '') + (' ⚔️' if c['clash'] else '')
    return (f"🕒 {e(c['start_local'])} · {e(c['home'])} — {e(c['away'])}\n"
            f"<b>{e(c['label'])} — {c['p'] * 100:.0f}%</b> · {notify.fair(c['p'])}{syn}")


def _text(day, c: dict, rank: int, hist: list[str]) -> str:
    e = notify.esc
    out = [f"🏒{rank} <b>Хоккей · ставка дня</b>", _line(c, e)]                  # 11.11: единое начало, без даты
    out += [e(h) for h in hist]                           # 12.11: после «честный кф» — только отметки правок / новой линии
    return '\n'.join(out)


def _row(day, rank: int, c: dict, status: str = '', reason: str = '') -> dict:
    return dict(date=str(day), rank=rank, id=c['id'], league=c['league'], start_utc=c['start_utc'], start_local=c['start_local'],
                home=c['home'], away=c['away'], slot=c['slot'], market=c['market'], pick=c['pick'], line=c['line'],
                label=c['label'], p=c['p'], fair_k=round(1 / c['p'], 2), fire=c['fire'], clash=c['clash'], status=status,
                reason=reason, result='', settled='', created=J.now_utc())


def _publish(day, c: dict, rank: int, hist: list[str]) -> dict:
    x = dict(c, rank=rank, hist=list(hist))
    x['msgs'] = [list(m) for m in notify.send_blocks([_text(day, x, rank, x['hist'])], queue=False)]
    notify.pin([tuple(m) for m in x['msgs']])
    CHANGED[0] = True
    return x


CHANGED = [False]                 # ставки хоккея изменились в этом прогоне → autorun обновит кэш кнопки «⭐ Ставка дня»


def texts(rt: dict) -> list[str]:
    """Тексты текущих ставок дня хоккея (🏒1–🏒3) — для кнопки «⭐ Ставка дня» теннисного бота."""
    return [_text(None, x, int(x['rank']), x.get('hist', [])) for x in (rt.get('hk_dp') or {}).get('picks', [])]


def process(store: J.Store, rt: dict, items, tp, leagues: tuple, day, lo, hi, now, tier_fn, log: list[str]) -> None:
    """Каждая ставка — отдельное закреплённое сообщение; rt['hk_dp'] = {date, picks: [...], notes: [...]}."""
    st = rt.get('hk_dp') or {}
    loc = now + pd.Timedelta(hours=C.TZ_OFFSET_HOURS)
    cands = candidates(items, tp, leagues, lo, hi, now, tier_fn)
    if st.get('date') != str(day):                           # новые сутки: снять закрепы прошлых, убрать уведомления
        old = [tuple(m) for x in st.get('picks', []) for m in x.get('msgs', [])] + [tuple(m) for m in st.get('msgs', [])]
        if old:
            notify.pin(old, on=False)
        notify.delete([tuple(m) for m in st.get('notes', [])])
        st = dict(date=str(day), picks=[], notes=[], hdr2=True)
        rt['hk_dp'] = st
        first = next((c for c in cands if not c['clash']), cands[0] if cands else None)
        if not first:
            log.append(f'⭐🏒 {day:%d.%m}: нет уверенных кандидатов')
            return
        st['picks'] = [_publish(day, first, 1, [])]
        store.append('hk_day_pick', [_row(day, 1, first)])
        log.append(f"⭐🏒 {day:%d.%m}: ⭐1 {first['label']} {first['home']}–{first['away']} P {first['p']:.2f}")
        return
    picks = st.get('picks') or []
    if st.get('msgs'):                                       # переход 11.11: общее сообщение → каждая ставка отдельным закрепом
        notify.delete([tuple(m) for m in st.pop('msgs')])
        picks = [_publish(day, x, i + 1, x.get('hist', [])) for i, x in enumerate(picks)]
        st['picks'] = picks
    if not st.get('hdr2'):                                    # 11.11: единое начало «🏒1 Хоккей · ставка дня» — обновить закрепы
        for x in picks:
            notify.edit([tuple(m) for m in x.get('msgs', [])], _text(day, x, int(x['rank']), x.get('hist', [])), None)
        st['hdr2'] = True
    for x in picks:                                          # 12.11: удалённые пользователем ставки — заново, с закрепом
        if not x.get('msgs') or not notify.alive([tuple(m) for m in x['msgs']]):    # 14.11: и не доставленные вовсе
            x['msgs'] = [list(m) for m in notify.send_blocks([_text(day, x, int(x['rank']), x.get('hist', []))], silent=True, queue=False)]
            notify.pin([tuple(m) for m in x['msgs']])
            CHANGED[0] = True
            log.append(f"⭐🏒 🏒{x['rank']} возвращена (была удалена)")
    if not picks:                                            # утром кандидатов не было — первая появившаяся = ⭐1
        first = next((c for c in cands if not c['clash']), None)
        if first:
            st['picks'] = [_publish(day, first, 1, [f'➕ появилась в {loc:%H:%M}'])]
            store.append('hk_day_pick', [_row(day, 1, first, reason='новая: появилась линия')])
            log.append(f"⭐🏒 ⭐1 появилась: {first['label']}")
        rt['hk_dp'] = st
        return
    dp = store.read('hk_day_pick')
    for x in picks:                                          # правки: лучшая линия того же матча, до начала матча
        if pd.Timestamp(x['start_utc']) <= now:
            continue
        same = [c for c in cands if str(c['id']) == str(x['id'])]
        if not same:
            continue
        mine = next((c for c in same if int(c['slot']) == int(x['slot'])), None)
        if mine:
            x['fire'], x['clash'] = mine['fire'], mine['clash']
        top = same[0]
        why = better(top, mine or x) if int(top['slot']) != int(x['slot']) else ''
        if not why:
            continue
        r = int(x['rank'])
        ix = [i for i, q in dp.iterrows() if str(q.date) == str(day) and str(q.id) == str(x['id'])
              and int(_num(q.slot)) == int(x['slot']) and _blank(q.status)] if len(dp) else []
        if ix:
            dp.loc[ix, ['status', 'reason']] = ['правка', why]
            store.update('hk_day_pick', dp.loc[ix], ['status', 'reason'])
        store.append('hk_day_pick', [_row(day, r, top, reason=f'правка: {why}')])
        h = f"✏️ мнение изменилось в {loc:%H:%M}: было {x['label']} {x['p'] * 100:.0f}% → {top['label']} ({why})"
        msgs, hist = x.get('msgs', []), list(x.get('hist', [])) + [h]
        x.clear(); x.update(dict(top, rank=r, hist=hist, msgs=msgs, edits=int(len([q for q in hist if q.startswith('✏️')]))))
        notify.edit([tuple(m) for m in msgs], _text(day, x, r, hist), None)
        CHANGED[0] = True
        st['notes'] = list(st.get('notes', [])) + [list(m) for m in notify.send_blocks(
            [f"🏒{r} <b>Хоккей · ставка дня: мнение изменилось</b> ✏️ ({notify.esc(why)})\n"
             f"{notify.esc(h)}"])]
        log.append(f'⭐🏒 ⭐{r} правка: {why}')
    nday = sum(1 for row, _ in items if lo <= pd.Timestamp(row['start_utc']) < hi and row['league'] in leagues)
    st['vol'] = max(int(st.get('vol', 0)), nday)
    if len(picks) < SL.max_picks('hockey', MAX_PICKS, st['vol'], loc):                               # новая ставка: другой матч лучше ⭐1 (одна за прогон)
        used = {str(x['id']) for x in picks}
        main = picks[0]
        for c in cands:
            if str(c['id']) in used:
                continue
            why = better(c, main)
            if why:
                r = len(picks) + 1
                picks.append(_publish(day, c, r, [f'➕ новая линия дня в {loc:%H:%M}: {why} (лучше ⭐1 {main["label"]})']))
                store.append('hk_day_pick', [_row(day, r, c, reason=f'новая: {why}')])
                log.append(f"⭐🏒 ⭐{r} новая: {c['label']} ({why})")
                break
    st['picks'] = picks
    rt['hk_dp'] = st


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
    """Итоги за хоккейные сутки d0…d1: ⭐1 итоговая, ➕ новые линии, ✏️ дни с правкой ⭐1, права итоговая / первая."""
    if not len(dp):
        return {}
    x = dp.copy()
    x['d'] = pd.to_datetime(x.date, errors='coerce').dt.date
    if d0 is not None:
        x = x[(x.d >= d0) & (x.d <= d1)]
    if not len(x):
        return {}
    rk = x['rank'].map(_num)
    st = x.status.astype(str)
    days = x[rk == 1].date.astype(str).nunique()
    edays = x[(rk == 1) & (st == 'правка')].date.astype(str).nunique()
    out = {'days': (edays, days)}
    wl = x.result.astype(str).isin(['W', 'L'])
    x['won'] = (x.result.astype(str) == 'W').astype(int)
    x['p'] = x.p.map(_num)
    act = x[wl & x.status.map(_blank)]
    for k, v in (('main', act[act['rank'].map(_num) == 1]), ('extra', act[act['rank'].map(_num) > 1])):
        out[k] = (int(v.won.sum()), len(v), float(v.p.mean()) if len(v) else 0.0)
    good = bad = 0                                           # первая версия против итоговой (где исходы разные)
    fin = {(str(d), int(_num(r))): str(q) for d, r, q, s_ in zip(x.date, x['rank'], x.result, x.status) if _blank(s_)}
    for q in x[(st == 'правка') & wl].itertuples(index=False):
        b = fin.get((str(q.date), int(_num(q.rank))))
        if b in ('W', 'L') and b != str(q.result):
            good += b == 'W'; bad += str(q.result) == 'W'
    out['who'] = (good, bad)
    return out
