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
import pick as PK
import selflearn as SL
from hockey import hlines as HL

DP_COLS = ['date', 'rank', 'id', 'league', 'start_utc', 'start_local', 'home', 'away', 'slot', 'market', 'pick', 'line',
           'label', 'p', 'fair_k', 'fire', 'clash', 'status', 'reason', 'result', 'settled', 'created',
           'k', 'p_close', 'clv',          # 14.11: кф DraftKings на момент ставки, цена закрытия (DK без маржи перед матчем), CLV
           'ev']                           # 17.11 лестница: EV по кф 1xBet (линии с ценой)
J.SHEETS.setdefault('hk_day_pick', DP_COLS)
P_MIN, P_MAX = 0.65, 0.80
MAX_PICKS = 3                   # 14.11: запасное значение — лимит динамический (selflearn.max_picks)
TARGET_MAX = 3                  # 21.11 (решение пользователя, С23а): ⭐ с ценой — до 3 в сутки, лучшие по EV
WITHDRAW = True                 # 21.11 С2а (решение пользователя): ⭐, переставшая проходить порог, — правка или «снята — рынок против»


def HV_P_MIN() -> float:
    from hockey import hvalue as HV
    return HV.P_MIN
LMARK = {'W': '✅', 'L': '❌', 'P': '⚪'}


def _blank(x) -> bool:
    return str(x).strip() in ('', 'nan', 'None')


def _num(x):
    try:
        return float(str(x).replace(',', '.'))
    except Exception:
        return float('nan')


def _syn(c: dict, tp: pd.DataFrame) -> tuple[int, int, int, int]:
    """(🔥, ⚔️ доказанных ✔ — двигают выбор ⭐; 🔥, ⚔️ всех не-⛔ — значок в тексте) для линии:
    капер на тот же матч и рынок — тот же выбор / противоположный (19.11, решение пользователя п.1)."""
    if tp is None or not len(tp):
        return 0, 0, 0, 0
    t = tp[(tp.game_id.astype(str) == str(c['id'])) & (tp.market.astype(str) == str(c['market']))]
    if len(t) and 'src' in t.columns:                       # 🧠 ⛔ слабый капер (selflearn) — его 🔥/⚔️ не учитываются
        t = t[t.src.astype(str).map(lambda s: SL.capper('hockey', s) != 'weak')]
    fire = int((t.pick.astype(str) == str(c['pick'])).any())
    clash = int((t.pick.astype(str) != str(c['pick'])).any())
    # 21.11 Г3б: выбор ⭐ двигают ✔ доказанные И близкие к ✔ (n ≥ 50, z ≥ 1,0, ROI > 0) каналы
    tt = t[t.src.astype(str).map(lambda s: SL.capper_soft('hockey', s))] if len(t) and 'src' in t.columns else t.iloc[0:0]
    return int((tt.pick.astype(str) == str(c['pick'])).any()), int((tt.pick.astype(str) != str(c['pick'])).any()), fire, clash


def _proven_clash(c: dict, tp: pd.DataFrame) -> bool:
    """21.11 Г3б / С2а: против линии стоит ✔ доказанный капер (тот же матч и рынок, другой выбор) — повод снять ⭐."""
    if tp is None or not len(tp) or 'src' not in tp.columns:
        return False
    t = tp[(tp.game_id.astype(str) == str(c['id'])) & (tp.market.astype(str) == str(c['market']))]
    t = t[t.src.astype(str).map(lambda s: SL.capper('hockey', s) == 'proven')]
    return bool((t.pick.astype(str) != str(c['pick'])).any())


def stale(games: pd.DataFrame | None, row: dict) -> bool:
    """Аудит 16.11 (0б): КХЛ — ставка дня только при свежих силах обеих команд (≥ 5 матчей за 60 дней, как hcappers.fresh).
    История без утечки (engine/research/hk_stale.py, пропуск сезона как сейчас): линии P ≥ 0,75 при < 5 свежих —
    угадано 65,1% при обещанных 79,7% (n = 149, z = −4,43), лог-лосс хуже постоянного; при ≥ 5 — 77,9% при 79,6% (z = −1,36)."""
    if games is None or row.get('league') != 'KHL' or not getattr(C, 'KHL_DP_FRESH', True):
        return False
    from hockey import hcappers as HC
    ok, _, _ = HC.fresh(games, 'KHL', row['home'], row['away'], pd.Timestamp(row['start_utc']))
    return not ok


HK_LADDER = True                  # 17.11 С23 для хоккея: сначала линии с ценой 1xBet (hvalue, по EV), затем линии модели без цены
TIER_PRICED = 4                   # выше ступеней модели (1–3)
HK_NEED_PRICE = True              # 19.11 (решение пользователя, «взгляд со стороны» п.4): ⭐ хоккея только с ценой 1xBet; нет цены —
                                  # «ставки нет» (линия модели без цены: перевес неизвестен, кф обычно < 1,35)
NO_PRICE_TEXT = '🏒 Хоккей · ставка дня\nСегодня ставки нет: у 1xBet нет цены на линии с перевесом.'


SHADOW_ROWS: list[dict] = []      # 21.11: стороны с ценой этого прогона (все, без фильтра P/кф/EV) → теневой портфель
SHADOW_COLS = ['key', 'date', 'id', 'league', 'start_utc', 'match', 'market', 'pick', 'line', 'label', 'p', 'k', 'ev', 'book',
               'first_at', 'k_first', 'p_first', 'last_at', 'p_last', 'result', 'settled']


def shadow(now: pd.Timestamp, log: list[str]) -> int:
    """21.11 (решение пользователя «да»: у хоккея не было теневого портфеля, С21 — только теннис и футбол): каждая сторона НХЛ
    с ценой конторы основного времени (1xBet / Marathon, С25а) — бумажная ставка в hockey/data/shadow/hockey_ГГГГ-ММ.csv.
    p — справедливая P рынка (инверсия Pinnacle, A21): первая (p_first, k_first) и последняя до начала (p_last = закрытие → CLV).
    Итог — shadow_settle (то же правило, что у ⭐: HL.settle, основное время)."""
    rows = [c for c in SHADOW_ROWS if pd.Timestamp(c['start_utc']) > now]
    SHADOW_ROWS.clear()
    if not rows:
        return 0
    f = HD_DATA / 'shadow' / f'hockey_{now:%Y-%m}.csv'
    old = pd.read_csv(f, dtype=str, keep_default_na=False) if f.exists() else pd.DataFrame(columns=SHADOW_COLS)
    idx = {k: i for i, k in enumerate(old.key)} if len(old) else {}
    at, add = f'{now:%Y-%m-%d %H:%M}', []
    for c in rows:
        line = '' if c['line'] in (None, '') else f"{float(c['line']):g}"
        kk = f"{c['id']}|{c['market']}|{c['pick']}|{line}"
        rec = dict(key=kk, date=str((pd.Timestamp(c['start_utc']) + pd.Timedelta(hours=C.TZ_OFFSET_HOURS - 12)).date()),
                   id=str(c['id']), league=str(c['id']).split(':')[0].upper(), start_utc=str(c['start_utc'])[:16], match=c['match'],
                   market=c['market'], pick=c['pick'], line=line, label=c['label'], p=round(float(c['p']), 4), k=round(float(c['k']), 3),
                   ev=round(float(c['ev']), 4), book=c['book'], last_at=at, p_last=round(float(c['p']), 4))
        if kk in idx:
            for col in ('p', 'k', 'ev', 'book', 'last_at', 'p_last'):
                old.at[idx[kk], col] = str(rec[col])
        else:
            rec.update(first_at=at, k_first=rec['k'], p_first=rec['p'], result='', settled='')
            idx[kk] = -1
            add.append(rec)
    out = pd.concat([old, pd.DataFrame(add, columns=SHADOW_COLS)], ignore_index=True).reindex(columns=SHADOW_COLS)
    f.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(f, index=False)
    log.append(f'🗂 теневой портфель 🏒: сторон с ценой {len(rows)}, новых {len(add)}')
    return len(rows)


def shadow_settle(games: pd.DataFrame, log: list[str]) -> int:
    """Итог бумажных ставок хоккея (текущий и прошлый месяц) — тем же правилом и счётом, что ⭐ (HL.settle, основное время)."""
    res = {str(r.id): r for r in games[games.end.astype(str).isin(['REG', 'OT', 'SO'])].itertuples(index=False)}
    n = 0
    for f in sorted((HD_DATA / 'shadow').glob('hockey_*.csv'))[-2:]:
        d = pd.read_csv(f, dtype=str, keep_default_na=False)
        ix = []
        for i, r in d[d.settled == ''].iterrows():
            x = res.get(str(r.id))
            if x is None:
                continue
            d.loc[i, ['result', 'settled']] = [HL.settle(str(r.market), str(r.pick), r.line if r.line != '' else None, x.end, x.hg, x.ag,
                                                         x.hg_reg, x.ag_reg), pd.Timestamp.now().strftime('%Y-%m-%d')]
            ix.append(i)
        if ix:
            d.to_csv(f, index=False)
            n += len(ix)
    if n:
        log.append(f'🗂 теневой портфель 🏒: рассчитано {n}')
    return n


def candidates(items: list[tuple[dict, list[dict]]], tp: pd.DataFrame, leagues: tuple, lo: pd.Timestamp, hi: pd.Timestamp,
               now: pd.Timestamp, tier_fn, games: pd.DataFrame | None = None, skipped: list | None = None,
               evs: list | None = None, M=None) -> list[dict]:
    out, priced = [], []
    for row, lns in items:
        st = pd.Timestamp(row['start_utc'])
        if row['league'] not in leagues or not (lo <= st < hi) or st <= now + pd.Timedelta(minutes=10):
            continue
        if stale(games, row):                                 # 16.11: КХЛ на устаревших силах — в «тени» ставки дня
            if skipped is not None:
                skipped.append(row['id'])
            continue
        if HK_LADDER and evs and M is not None and row['league'] == 'NHL':
            try:
                from hockey import hvalue as HV
                for v in HV.candidates(M, row, evs, keep_all=True):    # 21.11: каждая сторона с ценой — в теневой портфель
                    SHADOW_ROWS.append(dict(id=row['id'], start_utc=row['start_utc'], match=f"{row['home']} — {row['away']}",
                                            market=v['market'], pick=v['pick'], line=v['line'], label=v['label'], p=v['p'],
                                            k=v['k'], ev=v['ev'], book=v.get('book', '')))
                for v in HV.candidates(M, row, evs):
                    c = dict(id=row['id'], league=row['league'], start_utc=row['start_utc'], start_local=row['start_local'],
                             home=row['home'], away=row['away'], slot=v['slot'], market=v['market'], pick=v['pick'], line=v['line'],
                             label=v['label'], p=round(v['p'], 4), p_low=round(v['p'], 4), tier=TIER_PRICED,
                             k=round(v['k'], 2), ev=round(v['ev'], 4), book=v.get('book', 'onexbet'))
                    c['fire'], c['clash'], c['fire_i'], c['clash_i'] = _syn(c, tp)
                    priced.append(c)
            except Exception as ex:                           # цена — улучшение, а не условие работы ставки дня
                if skipped is not None:
                    skipped.append(f'цены: {ex.__class__.__name__}')
        for x in lns:
            p = float(x['p'])
            if not int(x['shown']) or x['market'] == '1X2' or not (P_MIN <= p <= P_MAX):
                continue
            c = dict(id=row['id'], league=row['league'], start_utc=row['start_utc'], start_local=row['start_local'],
                     home=row['home'], away=row['away'], slot=int(x['slot']), market=x['market'], pick=x['pick'],
                     line=x['line'], label=x['label'], p=round(p, 4), p_low=float(x['p_low']), tier=tier_fn(x, row['league']))
            c['fire'], c['clash'], c['fire_i'], c['clash_i'] = _syn(c, tp)
            out.append(c)
    model = SL.reorder('hockey', sorted(out, key=lambda c: (-c['fire'], c['clash'], -c['tier'], -c['p_low'], -c['p'])))
    priced = SL.reorder('hockey', sorted(priced, key=lambda c: (-c['fire'], c['clash'], -c['ev'])))   # 21.11: + понижения самообучения
    return priced + model   # лестница: цена 1xBet → модель


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
        return 'сравнение (есть цена 1xBet)' if int(a['tier']) == TIER_PRICED else 'сравнение (ступень выше)'
    if a.get('ev') is not None and b.get('ev') not in (None, ''):             # 17.11 лестница: обе с ценой — по EV
        d = float(a['ev']) - float(b['ev'])
        return f'сравнение (EV +{d * 100:.0f} п.п.)' if d >= 0.02 + SL.switch_extra('hockey') else ''
    if a['tier'] == b['tier'] and a['p_low'] >= b['p_low'] + 0.02 + SL.switch_extra('hockey'):
        return f"сравнение (P +{(a['p_low'] - b['p_low']) * 100:.0f} п.п.)"
    return ''


def _line(c: dict, e) -> str:
    syn = (' 🔥' if c.get('fire_i', c['fire']) else '') + (' ⚔️' if c.get('clash_i', c['clash']) else '')
    return (f"🕒 {e(c['start_local'])} · {e(c['home'])} — {e(c['away'])}\n"
            f"<b>{e(c['label'])}{'' if c.get('market') == 'ML' else ' (осн. время)'} — {c['p'] * 100:.0f}%</b> · {notify.fair(c['p'])}{syn}")


def _ev_line(c: dict) -> str:
    """17.11 лестница: кф 1xBet и EV (линии с ценой) или «цены 1xBet нет»; ставка хоккея — ¼ ед. (💰 в хоккее не доказана)."""
    if not HK_LADDER:
        return ''
    if c.get('k') not in (None, '') and c.get('ev') not in (None, ''):
        s = f"💵 {PK.book_ru(c)} {float(c['k']):.2f} · EV {float(c['ev']) * 100:+.1f}%".replace('.', ',').replace('-', '−')
    else:
        s = '💵 цены 1xBet нет'
    return s + ' · ставка ¼ ед.'


def _text(day, c: dict, rank: int, hist: list[str]) -> str:
    if c.get('off_text'):                                    # 21.11 С2а: снятая ⭐ — текст «снята» не перезаписывается
        return c['off_text']
    e = notify.esc
    out = [f"🏒{rank} <b>Хоккей · ставка дня</b> · 🎯 уверенная"] + ([e(c['scan'])] if c.get('scan') else []) + [_line(c, e)]  # 15.11: 🔎 печать суток
    if _ev_line(c):
        out.append(_ev_line(c))
    out += [e(h) for h in hist]                           # 12.11: после «честный кф» — только отметки правок / новой линии
    return '\n'.join(out)


def _row(day, rank: int, c: dict, status: str = '', reason: str = '') -> dict:
    return dict(date=str(day), rank=rank, id=c['id'], league=c['league'], start_utc=c['start_utc'], start_local=c['start_local'],
                home=c['home'], away=c['away'], slot=c['slot'], market=c['market'], pick=c['pick'], line=c['line'],
                label=c['label'], p=c['p'], fair_k=round(1 / c['p'], 2), fire=c['fire'], clash=c['clash'], status=status,
                reason=reason, result='', settled='', created=J.now_utc(), k=c.get('k', ''), ev=c.get('ev', ''))


def _publish(day, c: dict, rank: int, hist: list[str]) -> dict:
    x = dict(c, rank=rank, hist=list(hist))
    x['msgs'] = [list(m) for m in notify.send_blocks([_text(day, x, rank, x['hist'])], queue=False)]
    notify.pin([tuple(m) for m in x['msgs']])
    CHANGED[0] = True
    return x


SEGMENT_H = 2.0                   # 14.11 (решение пользователя): одна ставка дня на игровой отрезок — начала ставок не ближе 2 ч


def busy(start, starts: list) -> bool:
    """Игровой отрезок занят: уже есть ставка дня, чей матч начинается ближе SEGMENT_H часов (риск одной волны событий)."""
    s = pd.Timestamp(start)
    return any(abs((s - pd.Timestamp(x)).total_seconds()) < SEGMENT_H * 3600 for x in starts if x not in (None, ''))


HD_DATA = __import__('pathlib').Path(__file__).resolve().parent / 'data'   # hockey/data (печать — hockey/data/seal)
CHANGED = [False]                 # ставки хоккея изменились в этом прогоне → autorun обновит кэш кнопки «⭐ Ставка дня»


def texts(rt: dict) -> list[str]:
    """Тексты текущих ставок дня хоккея (🏒1–🏒3) — для кнопки «⭐ Ставка дня» теннисного бота."""
    return [_text(None, x, int(x['rank']), x.get('hist', [])) for x in (rt.get('hk_dp') or {}).get('picks', [])]


def process(store: J.Store, rt: dict, items, tp, leagues: tuple, day, lo, hi, now, tier_fn, log: list[str],
            games: pd.DataFrame | None = None, evs: list | None = None, M=None) -> None:
    """Каждая ставка — отдельное закреплённое сообщение; rt['hk_dp'] = {date, picks: [...], notes: [...]}."""
    st = rt.get('hk_dp') or {}
    loc = now + pd.Timedelta(hours=C.TZ_OFFSET_HOURS)
    skipped: list = []
    try:
        cands = candidates(items, tp, leagues, lo, hi, now, tier_fn, games, skipped, evs=evs, M=M)
        np_ = sum(int(c['tier']) == TIER_PRICED for c in cands)
        if HK_LADDER:
            log.append(f'⭐🏒 лестница: линий с ценой 1xBet {np_} (матчей {len({str(c["id"]) for c in cands if int(c["tier"]) == TIER_PRICED})})')
        for s in {str(s) for s in skipped if str(s).startswith('цены')}:
            log.append(f'⭐🏒 {s} — ставка дня по линиям модели')
        skipped = [s for s in skipped if not str(s).startswith('цены')]
    except Exception as ex:                                  # сторож свежести не должен ломать ставку дня
        log.append(f'⭐🏒 свежесть КХЛ: сбой {ex.__class__.__name__}: {ex}')
        cands = candidates(items, tp, leagues, lo, hi, now, tier_fn)
    if skipped:
        log.append(f'⭐🏒 КХЛ мало свежих матчей (< 5 за 60 дн.) — вне ставки дня: {len(set(skipped))} матч.')
    all_cands = cands
    if HK_LADDER and HK_NEED_PRICE:
        cands = [c for c in cands if int(c['tier']) == TIER_PRICED]
    scan = ''
    try:                                                     # 🔏 15.11: печать прогнозов суток (снимок в репозитории каждый прогон)
        import seal as SEAL
        info = SEAL.seal('hockey', [it for it in items if it[0].get('league') in leagues], lo, hi, now, HD_DATA, str(day),
                         SEAL.cand_rows(all_cands, 'id'))
        scan = SEAL.text(info, len({str(c['id']) for c in all_cands}))
    except Exception as ex:
        log.append(f'⭐🏒 печать: сбой {ex.__class__.__name__}: {ex}')
    if st.get('date') != str(day):                           # новые сутки: снять закрепы прошлых, убрать уведомления
        old = [tuple(m) for x in st.get('picks', []) for m in x.get('msgs', [])] + [tuple(m) for m in st.get('msgs', [])]
        if old:
            notify.pin(old, on=False)
        notify.delete([tuple(m) for m in st.get('notes', [])])
        st = dict(date=str(day), picks=[], notes=[], hdr2=True, hdr3=True, hdr4=True)
        rt['hk_dp'] = st
        first = next((c for c in cands if not c['clash']), cands[0] if cands else None)
        if not first:
            log.append(f'⭐🏒 {day:%d.%m}: нет уверенных кандидатов' + (' с ценой 1xBet' if HK_NEED_PRICE else ''))
            if HK_NEED_PRICE and all_cands:                 # 19.11 п.4: одно тихое сообщение «ставки нет» (без закрепа)
                st['notes'] = [list(m) for m in notify.send_blocks([NO_PRICE_TEXT], silent=True, queue=False)]
                CHANGED[0] = True
            return
        st['picks'] = [_publish(day, dict(first, scan=scan), 1, [])]
        store.append('hk_day_pick', [_row(day, 1, first)])
        log.append(f"⭐🏒 {day:%d.%m}: ⭐1 {first['label']} {first['home']}–{first['away']} P {first['p']:.2f}")
        return
    picks = st.get('picks') or []
    if st.get('msgs'):                                       # переход 11.11: общее сообщение → каждая ставка отдельным закрепом
        notify.delete([tuple(m) for m in st.pop('msgs')])
        picks = [_publish(day, x, i + 1, x.get('hist', [])) for i, x in enumerate(picks)]
        st['picks'] = picks
    if not st.get('hdr2') or not st.get('hdr3') or not st.get('hdr4'):              # 11.11 единое начало; 17.11 строка 💵 / ставка ¼
        for x in picks:
            notify.edit([tuple(m) for m in x.get('msgs', [])], _text(day, x, int(x['rank']), x.get('hist', [])), None)
        st['hdr2'] = st['hdr3'] = st['hdr4'] = True
    if __import__('os').getenv('RESEND_PICKS'):              # ТЗ-006 «🔄 Ставки на сутки»: заново все, старые — удалить
        for x in picks:
            if x.get('msgs'):
                notify.delete([tuple(m) for m in x['msgs']])
            x['msgs'] = [list(m) for m in notify.send_blocks([_text(day, x, int(x['rank']), x.get('hist', []))], silent=True, queue=False)]
            if not x.get('off'):                             # С2а: снятая ⭐ не закрепляется
                notify.pin([tuple(m) for m in x['msgs']])
        CHANGED[0] = True
        log.append(f'⭐🏒 ставки на сутки: прислано заново {len(picks)}')
    for x in picks:                                          # 12.11: удалённые пользователем ставки — заново, с закрепом
        if not x.get('msgs') or not notify.alive([tuple(m) for m in x['msgs']]):    # 14.11: и не доставленные вовсе
            x['msgs'] = [list(m) for m in notify.send_blocks([_text(day, x, int(x['rank']), x.get('hist', []))], silent=True, queue=False)]
            if not x.get('off'):                             # С2а: снятая ⭐ не закрепляется
                notify.pin([tuple(m) for m in x['msgs']])
            CHANGED[0] = True
            log.append(f"⭐🏒 🏒{x['rank']} возвращена (была удалена)")
    if not picks:                                            # утром кандидатов не было — первая появившаяся = ⭐1
        first = next((c for c in cands if not c['clash']), None)
        if first:
            if st.get('notes'):                              # 19.11 п.4: «ставки нет» снимается, когда появилась цена
                notify.delete([tuple(m) for m in st['notes']]); st['notes'] = []
            st['picks'] = [_publish(day, dict(first, scan=scan), 1, [f'➕ появилась в {loc:%H:%M}'])]
            store.append('hk_day_pick', [_row(day, 1, first, reason='новая: появилась линия')])
            log.append(f"⭐🏒 ⭐1 появилась: {first['label']}")
        rt['hk_dp'] = st
        return
    dp = store.read('hk_day_pick')
    fresh = {(str(c['id']), c['market'], c['pick'], '' if c['line'] in (None, '') else f"{float(c['line']):g}"): c for c in SHADOW_ROWS}
    for x in picks:                                          # правки: лучшая линия того же матча, до начала матча
        if x.get('off') or pd.Timestamp(x['start_utc']) <= now:
            continue
        same = [c for c in cands if str(c['id']) == str(x['id'])]
        f0 = fresh.get((str(x['id']), x['market'], x['pick'], '' if x.get('line') in (None, '') else f"{float(x['line']):g}"))
        vet = WITHDRAW and _proven_clash(x, tp)              # 21.11 Г3б: ✔ капер против объявленной ⭐
        fails = (WITHDRAW and f0 is not None and not any(int(c['slot']) == int(x['slot']) for c in same)
                 and (f0['p'] < HV_P_MIN() or f0['k'] < PK.MIN_K or f0['ev'] < PK.EV_FLOOR)) or vet
        if vet:
            same = [c for c in same if int(c['slot']) != int(x['slot']) and not _proven_clash(c, tp)]
            f0 = f0 or dict(p=float(x['p']), k=float(x.get('k') or 0), ev=float(x.get('ev') or 0))
        if fails:                                            # 21.11 С2а: ⭐ больше не проходит порог при свежей цене
            was = ('капер ✔ против; ' if vet else '') + (f"P {float(x['p']) * 100:.0f}% → {f0['p'] * 100:.0f}%, кф {f0['k']:.2f}".replace('.', ',')
                   + (f", EV {float(x['ev']) * 100:+.1f}% → {f0['ev'] * 100:+.1f}%".replace('.', ',') if x.get('ev') not in (None, '') else ''))
            r = int(x['rank'])
            if not same:
                ix = [i for i, q in dp.iterrows() if str(q.date) == str(day) and str(q.id) == str(x['id'])
                      and int(_num(q.slot)) == int(x['slot']) and _blank(q.status)] if len(dp) else []
                if ix:
                    dp.loc[ix, ['status', 'reason']] = ['снята', f'рынок против ({was})']
                    store.update('hk_day_pick', dp.loc[ix], ['status', 'reason'])
                t = (f"🏒{r} <b>Хоккей · ставка дня снята</b> ❌\n{notify.esc(x['home'])} — {notify.esc(x['away'])}\n"
                     f"<s>{notify.esc(x['label'])}</s> — рынок против: {notify.esc(was)} (в {loc:%H:%M})")
                x.update(off=True, off_text=t)
                if x.get('msgs'):
                    notify.edit([tuple(m) for m in x['msgs']], t, None)
                    notify.pin([tuple(m) for m in x['msgs']], on=False)
                st['notes'] = list(st.get('notes', [])) + [list(m) for m in notify.send_blocks([t])]
                CHANGED[0] = True
                log.append(f'⭐🏒 ⭐{r} снята: {was}')
                continue
            top, why = same[0], f'рынок против: {was}'
            mine = None
        else:
            if not same:
                continue
            mine = next((c for c in same if int(c['slot']) == int(x['slot'])), None)
            if mine:
                x['fire'], x['clash'] = mine['fire'], mine['clash']
                x['fire_i'], x['clash_i'] = mine.get('fire_i', mine['fire']), mine.get('clash_i', mine['clash'])
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
        sc = x.get('scan', '')
        x.clear(); x.update(dict(top, rank=r, hist=hist, msgs=msgs, edits=int(len([q for q in hist if q.startswith('✏️')])),
                                 **({'scan': sc} if sc else {})))
        notify.edit([tuple(m) for m in msgs], _text(day, x, r, hist), None)
        CHANGED[0] = True
        st['notes'] = list(st.get('notes', [])) + [list(m) for m in notify.send_blocks(
            [f"🏒{r} <b>Хоккей · ставка дня: мнение изменилось</b> ✏️ ({notify.esc(why)})\n"
             f"{notify.esc(h)}"])]
        log.append(f'⭐🏒 ⭐{r} правка: {why}')
    nday = sum(1 for row, _ in items if lo <= pd.Timestamp(row['start_utc']) < hi and row['league'] in leagues)
    st['vol'] = max(int(st.get('vol', 0)), nday)
    if not HK_LADDER and len(picks) < SL.max_picks('hockey', MAX_PICKS, st['vol'], loc):   # 17.11 лестница: новые 🎯 днём не добавляются (💰 в хоккее нет)
        used = {str(x['id']) for x in picks}
        main = picks[0]
        for c in cands:
            if str(c['id']) in used or busy(c['start_utc'], [x['start_utc'] for x in picks]):
                continue
            why = better(c, main)
            if why:
                r = len(picks) + 1
                picks.append(_publish(day, c, r, [f'➕ новая линия дня в {loc:%H:%M}: {why} (лучше ⭐1 {main["label"]})']))
                store.append('hk_day_pick', [_row(day, r, c, reason=f'новая: {why}')])
                log.append(f"⭐🏒 ⭐{r} новая: {c['label']} ({why})")
                break
    if HK_LADDER and picks:                                  # 21.11 С23а (решение пользователя): 🏒 до 3 ⭐ в сутки — лучшие по EV, с ценой
        used = {str(x['id']) for x in picks}
        # 21.11 (проверка синтеза): тот же порядок, что у 🏒1 — самообучение → 🔥 / ⚔️ каперов → EV (раньше — только EV)
        pool = [c for c in cands if int(c['tier']) == TIER_PRICED and c.get('ev') is not None
                and not SL.penalty('hockey', dict(c, rank=2))]      # О2: пониженный сегмент — не в новую ⭐
        for c in pool:
            if sum(1 for x in picks if not x.get('off')) >= TARGET_MAX:
                break
            if str(c['id']) in used or pd.Timestamp(c['start_utc']) <= now or busy(c['start_utc'], [x['start_utc'] for x in picks if not x.get('off')]):
                continue
            r = len(picks) + 1
            why = f"EV {float(c['ev']) * 100:+.1f}% · {sum(1 for x in picks if not x.get('off')) + 1}-я из {TARGET_MAX}".replace('.', ',')
            picks.append(_publish(day, c, r, [f'➕ новая линия дня в {loc:%H:%M}: {why}']))
            store.append('hk_day_pick', [_row(day, r, c, reason=f'новая: {why}')])
            log.append(f"⭐🏒 ⭐{r} новая: {c['label']} ({why})")
            used.add(str(c['id']))
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
    off_ = x[(st == 'снята') & wl]                           # 21.11 С2а: снятые ⭐ — снятие право, если ставка проиграла
    out['off'] = (int((off_.result.astype(str) == 'L').sum()), len(off_), int((st == 'снята').sum()))
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
