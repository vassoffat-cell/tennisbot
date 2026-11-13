"""🧠 Самообучение и развитие — ежедневный цикл после каждого суточного отчёта (решение пользователя 14.11.2026:
«никаких отмазок — учимся на своих ошибках и берём положительный и отрицательный опыт каперов; по каждому дневному отчёту —
обучение и развитие; еженедельный аудит — по этим отчётам»). Кодекс — engine/CODEX.md.

Общий модуль тенниса, футбола и хоккея (без проектных импортов). Состояние — JSON вида спорта (коммитится облаком):
  теннис data/lessons.json · футбол football/data/lessons.json · хоккей hockey/data/lessons.json.

ЦИКЛ (learn, раз в сутки, сразу перед суточным отчётом):
  1. Свои ставки дня (все: ⭐1, ⭐2/⭐3, заменённые версии) → сегменты: рынок, ступень, синтез с каперами (🔥/⚔️/без),
     корзина P, новые линии. Проверка «итог против ожидания модели»: W против E = ΣP, z = (W − E) / √Σ P(1 − P).
       z ≤ −1,96 при n ≥ 8 и хуже остальных ставок (zd ≤ −1,64 по остатку W − P) → сегмент ПОНИЖЕН (кандидаты сегмента —
       в конец очереди ставки дня); снятие — при z ≥ −1,0 или zd > −0,5.
       z ≥ +1,96 при n ≥ 12 и лучше остальных (zd ≥ +1,64) → сегмент СИЛЬНЫЙ (положительный опыт: понижение не применяется).
       Все ставки в целом хуже обещанного (n ≥ 15, z ≤ −1,96) → «общая переоценка P» — урок и вопрос калибровки аудиту.
  2. Правки первого приговора по причинам (сравнение / каперы за / каперы против / скриншот): права итоговая против права первая.
       ошибочных ≥ 3 и ≥ 2 × верных + 1 → правки этой причины ОТКЛЮЧЕНЫ на 30 дней (затем повторная проверка);
       общий перевес ошибочных правок → запас правки +1 п.п. за каждые 3 (до +5 п.п.) — «чем реже правки, тем лучше».
  3. ➕ Новые линии дня (⭐2/⭐3) понижены → в сутки не больше 2 ставок.
  4. Каперы (канал × вид спорта; только прогнозы до матча с НЕЗАВИСИМЫМ итогом — табло ESPN / наша база, не отметки канала;
     их кф без ~4% маржи):
       n ≥ 20, z ≥ +1,64 и ROI > 0 → ✔ ДОКАЗАН: его ⚔️ — вето линии, его 🔥 — в силе (снятие при z < 0,5);
       n ≥ 15, z ≤ −1,64 → ⛔ СЛАБЫЙ: его 🔥 и ⚔️ системой не учитываются (снятие при z > −0,5).
Каждое новое и снятое правило — строка истории (дата, +/−, текст) → блок «🧠 Обучение» в отчёте и лист learning (kind «урок»).
Применение: dpcore / hpick (порядок кандидатов, better(), MAX_PICKS), football/tips.relation и hockey/hpick._syn (каперы).
"""
from __future__ import annotations

import datetime as dt
import html
import json
import math
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
PATH = {'tennis': ROOT / 'data' / 'lessons.json', 'football': ROOT / 'football' / 'data' / 'lessons.json',
        'hockey': ROOT / 'hockey' / 'data' / 'lessons.json'}
N_SEG, Z_DEMOTE, Z_RESTORE = 8, -1.96, -1.0
N_STRONG, Z_STRONG = 12, 1.96
Z_DIFF = 1.64                       # сегмент против остальных ставок (остаток W − P)
N_PROVEN, Z_PROVEN, Z_PROVEN_KEEP = 20, 1.64, 0.5
N_WEAK, Z_WEAK, Z_WEAK_KEEP = 15, -1.64, -0.5
MARGIN = 0.96                       # кф капера → вероятность без ~4% маржи
BLOCK_DAYS = 30
MKT = {'ML': 'победа', 'HCP': 'фора', 'AH': 'фора', 'IT1': 'инд. тотал 1', 'IT2': 'инд. тотал 2', 'TOTAL': 'тотал', 'TOT': 'тотал',
       '1X2': 'исход', 'DC': 'двойной шанс', 'BTTS': 'обе забьют', 'EXACT': 'точный счёт'}
REASONS = ('сравнение', 'каперы за', 'каперы против', 'скриншот')
SRC = {'stavki_na_sport_betting1': 'Green Line', 'stavki': 'Прогнозы', 'xs_stavkii': 'Хитрости', 'groznyi_ruslan': 'Грозный',
       'kapper_uk': 'Каппер UK', 'nb-bet': 'nb-bet'}
_CACHE: dict[str, dict] = {}


# ───────────────────────────── состояние ─────────────────────────────
def load(sport: str) -> dict:
    if sport not in _CACHE:
        try:
            _CACHE[sport] = json.loads(PATH[sport].read_text(encoding='utf-8'))
        except Exception:
            _CACHE[sport] = {}
    return _CACHE[sport]


def save(sport: str, st: dict) -> None:
    PATH[sport].parent.mkdir(parents=True, exist_ok=True)
    PATH[sport].write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding='utf-8')
    _CACHE[sport] = st


# ───────────────────────────── применение (ставка дня, каперы) ─────────────────────────────
def seg_keys(c: dict) -> list[str]:
    """Сегменты кандидата / ставки: рынок, ступень, синтез, корзина P (+ новые линии для rank ≥ 2)."""
    out = [f"market:{c.get('market', '')}"]
    t = _num(c.get('tier'))
    if t == t:
        out.append(f'tier:{int(t)}')
    out.append('syn:fire' if _flag(c.get('fire')) else 'syn:clash' if _flag(c.get('clash')) else 'syn:none')
    p = _num(c.get('p'))
    if p == p:
        out.append(f'p:{_band(p)}')
    r = _num(c.get('rank'))
    if r == r and r >= 2:
        out.append('rank:extra')
    return out


def penalty(sport: str, c: dict) -> int:
    """1 — кандидат из пониженного сегмента (сильный сегмент понижение отменяет), 0 — нет."""
    st = load(sport)
    keys = set(seg_keys(c))
    if keys & set(st.get('strong', [])):
        return 0
    return int(bool(keys & set(st.get('demoted', []))))


def reorder(sport: str, cands: list[dict]) -> list[dict]:
    """Пониженные сегменты — в конец очереди (порядок внутри групп сохраняется)."""
    for c in cands:
        c['sl_pen'] = penalty(sport, c)
    return sorted(cands, key=lambda c: c['sl_pen'])


def reason_class(why: str) -> str:
    w = str(why).replace('правка: ', '').replace('новая: ', '').strip()
    if w.startswith('каперы за'):
        return 'каперы за'
    if w.startswith('каперы против'):
        return 'каперы против'
    if w.startswith('скриншот'):
        return 'скриншот'
    if w.startswith('исправление') or w.startswith('исправлена'):
        return 'исправление'
    return 'сравнение'


def allow(sport: str, why: str, a: dict | None = None, b: dict | None = None) -> bool:
    """Можно ли править / добавлять ставку по причине why: причина не отключена; кандидат не из пониженного сегмента,
    если текущая ставка — нет."""
    if not why:
        return False
    rc = reason_class(why)
    if rc != 'исправление' and rc in (load(sport).get('blocked') or {}):
        return False
    if a is not None and b is not None and penalty(sport, a) > penalty(sport, b):
        return False
    return True


def switch_extra(sport: str) -> float:
    return float(load(sport).get('switch_extra', 0.0))


def max_picks(sport: str, default: int) -> int:
    return min(default, int(load(sport).get('max_picks', default)))


def capper(sport: str, src: str) -> str:
    """Статус канала: 'proven' ✔ / 'weak' ⛔ / '' — наблюдение."""
    return str(((load(sport).get('cappers') or {}).get(str(src)) or {}).get('status', ''))


# ───────────────────────────── данные ─────────────────────────────
def _num(x) -> float:
    try:
        return float(str(x).replace(',', '.'))
    except Exception:
        return float('nan')


def _flag(x) -> bool:
    v = _num(x)
    return bool(v == v and v >= 1) or str(x).strip().lower() == 'true'


def _band(p: float) -> str:
    return '0.80+' if p >= 0.80 else '0.70-0.80' if p >= 0.70 else '0.60-0.70' if p >= 0.60 else '<0.60'


def _rank(x) -> int:
    v = _num(x)
    return int(v) if v == v and v >= 1 else 1


def frame_dp(dp: pd.DataFrame, ch: pd.DataFrame | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Теннис / футбол: day_pick (итоговые версии) + dp_changes (заменённые версии с итогом).
    → (P: ставки с итогом W/L — date, rank, market, label, p, tier, fire, clash, result, final),
      (E: правки — date, rank, cls, old, new)."""
    P, E = [], []
    if dp is not None and len(dp):
        for r in dp.itertuples(index=False):
            res = str(getattr(r, 'result', ''))
            if str(getattr(r, 'status', '')) == 'OFF' or res not in ('W', 'L'):
                continue
            P.append(dict(date=str(r.date)[:10], rank=_rank(getattr(r, 'rank', 1)), market=str(r.market), label=str(r.label),
                          p=_num(r.p), tier=_num(getattr(r, 'tier', '')), fire=_flag(getattr(r, 'fire', 0)),
                          clash=_flag(getattr(r, 'clash', 0)), result=res, final=True))
    fin = {(x['date'], x['rank']): x['result'] for x in P}
    if ch is not None and len(ch):
        for r in ch.itertuples(index=False):
            why, res = str(r.reason), str(r.result)
            if not why.startswith('правка') or res not in ('W', 'L'):
                continue
            d, rk = str(r.date)[:10], _rank(getattr(r, 'rank', 1))
            E.append(dict(date=d, rank=rk, cls=reason_class(why), old=res, new=fin.get((d, rk), ''), label=str(r.label)))
    return pd.DataFrame(P), pd.DataFrame(E)


def frame_hk(dp: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Хоккей: hk_day_pick — итоговая версия (status пусто) и заменённые (status «правка», reason — причина)."""
    P, E = [], []
    if dp is None or not len(dp):
        return pd.DataFrame(), pd.DataFrame()
    for r in dp.itertuples(index=False):
        res, sts = str(r.result), str(r.status).strip()
        if res not in ('W', 'L') or sts not in ('', 'nan', 'None'):
            continue
        P.append(dict(date=str(r.date)[:10], rank=_rank(r.rank), market=str(r.market), label=str(r.label), p=_num(r.p),
                      tier=_num(getattr(r, 'tier', '')), fire=_flag(r.fire), clash=_flag(r.clash), result=res, final=True))
    fin = {(x['date'], x['rank']): x['result'] for x in P}
    for r in dp.itertuples(index=False):
        if str(r.status).strip() == 'правка' and str(r.result) in ('W', 'L'):
            d, rk = str(r.date)[:10], _rank(r.rank)
            E.append(dict(date=d, rank=rk, cls=reason_class(r.reason), old=str(r.result), new=fin.get((d, rk), ''), label=str(r.label)))
    return pd.DataFrame(P), pd.DataFrame(E)


def capper_rows(sport: str, today: dt.date) -> pd.DataFrame:
    try:
        import caprep
        return caprep.rows(sport, dt.date(2020, 1, 1), today, 12 if sport == 'hockey' else 8)
    except Exception:
        return pd.DataFrame()


# ───────────────────────────── статистика ─────────────────────────────
def _test(won, p) -> tuple[int, int, float, float]:
    """(W, n, E, z) — итог против ожидания."""
    won, p = list(won), list(p)
    n, w, e = len(won), int(sum(won)), float(sum(p))
    v = float(sum(q * (1 - q) for q in p))
    return w, n, e, ((w - e) / math.sqrt(v) if v > 0 else 0.0)


def _segments(P: pd.DataFrame) -> dict[str, dict]:
    """Сегмент: (W, n, E, z против ожидания модели, zd — против остальных ставок по остатку W − P, n_out)."""
    recs = [(int(r['result'] == 'W'), float(r['p']), set(seg_keys(r))) for r in P.to_dict('records') if r['p'] == r['p']]
    keys = sorted({k for _, _, ks in recs for k in ks})
    res = {}
    for k in keys:
        a = [(w, p) for w, p, ks in recs if k in ks]
        o = [(w, p) for w, p, ks in recs if k not in ks]
        w, n, e, z = _test([x for x, _ in a], [y for _, y in a])
        zd = 0.0
        if len(o) >= 5:
            va = sum(y * (1 - y) for _, y in a) / n ** 2
            vo = sum(y * (1 - y) for _, y in o) / len(o) ** 2
            ra = sum(x - y for x, y in a) / n
            ro = sum(x - y for x, y in o) / len(o)
            zd = (ra - ro) / math.sqrt(va + vo) if va + vo > 0 else 0.0
        res[k] = dict(w=w, n=n, e=round(e, 2), z=round(z, 2), zd=round(zd, 2), n_out=len(o))
    return res


def seg_name(k: str) -> str:
    kind, v = k.split(':', 1)
    if kind == 'market':
        return f'рынок «{MKT.get(v, v)}»'
    if kind == 'tier':
        return f'ступень {v}'
    if kind == 'syn':
        return {'fire': '🔥 вместе с каперами', 'clash': '⚔️ против каперов', 'none': 'без каперов'}[v]
    if kind == 'p':
        return f"P {v.replace('0.', '').replace('-', '–')}%".replace('80+%', '80%+').replace('<60%', '<60%')
    return '➕ новые линии дня'


def _fmt(x: float, d: int = 1) -> str:
    return f'{x:.{d}f}'.replace('.', ',')


# ───────────────────────────── ежедневный цикл ─────────────────────────────
def learn(sport: str, P: pd.DataFrame, E: pd.DataFrame, today: dt.date, max_default: int = 3) -> dict:
    """Пересчитать правила по всем данным; вернуть {'added': [...], 'removed': [...], 'state': st}. Состояние сохраняется."""
    old = load(sport)
    st = dict(old)
    hist = list(old.get('history', []))
    added, removed = [], []
    today_s = today.isoformat()

    def note(sign: str, text: str) -> None:
        (added if sign == '+' else removed).append(text)
        hist.append(dict(date=today_s, sign=sign, text=text))

    # 1. сегменты
    segs = _segments(P) if len(P) else {}
    dem, strong = set(old.get('demoted', [])), set(old.get('strong', []))
    for k, s in segs.items():                                    # хуже ожидания И хуже остальных ставок (не общий сдвиг)
        if k not in dem and s['n'] >= N_SEG and s['z'] <= Z_DEMOTE and s['n_out'] >= 5 and s['zd'] <= -Z_DIFF:
            dem.add(k)
            note('+', f"{seg_name(k)}: {s['w']}/{s['n']} при ожидании {_fmt(s['e'])} (z {_fmt(s['z'])}), хуже остальных ставок"
                      ' → понижен в выборе ставки дня')
        elif k in dem and (s['z'] >= Z_RESTORE or s['zd'] > -0.5):
            dem.discard(k)
            note('−', f"{seg_name(k)}: {s['w']}/{s['n']} (z {_fmt(s['z'])}) — вернулся к норме, понижение снято")
        if k not in strong and s['n'] >= N_STRONG and s['z'] >= Z_STRONG and s['n_out'] >= 5 and s['zd'] >= Z_DIFF:
            strong.add(k)
            note('+', f"{seg_name(k)}: {s['w']}/{s['n']} при ожидании {_fmt(s['e'])} (z +{_fmt(s['z'])}) → сильный, закреплён")
        elif k in strong and (s['z'] < 1.0 or s['zd'] < 0.5):
            strong.discard(k)
            note('−', f"{seg_name(k)}: z {_fmt(s['z'])} — преимущество не подтвердилось, отметка «сильный» снята")
    st['segments'], st['demoted'], st['strong'] = segs, sorted(dem), sorted(strong)
    over = False                                                  # общий сдвиг: ставки дня в целом хуже обещанной P
    if len(P) >= 15:
        w, n, ex, z = _test((P.result == 'W').astype(int), P.p.fillna(0.6))
        over = z <= Z_DEMOTE
        if over != bool(old.get('overconf')):
            note('+' if over else '−', f'ставки дня в целом: {w}/{n} при ожидании {_fmt(ex)} (z {_fmt(z)}) — '
                 + ('модель завышает P ставки дня → вопрос аудиту: калибровка P' if over else 'общий сдвиг ушёл'))
    st['overconf'] = over
    mp = 2 if 'rank:extra' in dem else max_default
    if mp != int(old.get('max_picks', max_default)):
        note('+' if mp < max_default else '−', f'ставок дня в сутки — не больше {mp}' if mp < max_default else f'лимит ставок дня снова {mp}')
    st['max_picks'] = mp

    # 2. правки по причинам
    blocked = {k: v for k, v in (old.get('blocked') or {}).items() if v >= today_s}
    for k in set(old.get('blocked') or {}) - set(blocked):
        note('−', f'правки «{k}» — срок отключения истёк, снова разрешены (повторная проверка)')
    ed = {}
    if len(E):
        x = E[E.new.isin(['W', 'L']) & (E.new != E.old)]
        for cls, g in x.groupby('cls'):
            ed[cls] = dict(right=int((g.new == 'W').sum()), wrong=int((g.old == 'W').sum()))
    for cls, v in ed.items():
        if cls == 'исправление' or cls in blocked:
            continue
        if v['wrong'] >= 3 and v['wrong'] >= 2 * v['right'] + 1 and cls not in (old.get('blocked') or {}):
            blocked[cls] = (today + dt.timedelta(days=BLOCK_DAYS)).isoformat()
            note('+', f"правки «{cls}»: права первая {v['wrong']}, итоговая {v['right']} → отключены на {BLOCK_DAYS} дней")
    st['blocked'], st['edits'] = blocked, ed
    net = sum(v['wrong'] - v['right'] for v in ed.values())
    extra = round(min(0.05, 0.01 * (max(0, net) // 3)), 2)
    if abs(extra - float(old.get('switch_extra', 0.0))) > 1e-9:
        note('+' if extra > float(old.get('switch_extra', 0.0)) else '−',
             f'запас для правки: +{extra * 100:.0f} п.п. к базовому (ошибочных правок больше верных на {max(0, net)})')
    st['switch_extra'] = extra

    # 3. каперы
    cap = dict(old.get('cappers') or {})
    df = capper_rows(sport, today)
    new_cap = {}
    if len(df):
        d = df[df.ind.isin(['W', 'L'])].copy() if 'ind' in df.columns else df.iloc[:0]   # честно: только независимый итог
        d['res'] = d['ind'] if len(d) else d.get('res')
        d['kk'] = d.k.map(_num)
        d = d[d.kk > 1.0]
        for src, g in d.groupby('src'):
            won = (g.res == 'W').astype(int).tolist()
            p = [min(0.99, MARGIN / k) for k in g.kk]
            w, n, e, z = _test(won, p)
            roi = float(sum((k - 1) if r == 'W' else -1.0 for r, k in zip(g.res, g.kk)) / n)
            prev = str((cap.get(src) or {}).get('status', ''))
            stt = prev
            if prev != 'proven' and n >= N_PROVEN and z >= Z_PROVEN and roi > 0:
                stt = 'proven'
            elif prev == 'proven' and z < Z_PROVEN_KEEP:
                stt = ''
            if stt != 'proven':
                if prev != 'weak' and n >= N_WEAK and z <= Z_WEAK:
                    stt = 'weak'
                elif prev == 'weak' and z > Z_WEAK_KEEP:
                    stt = ''
            fire = g[g.rel == '1']; clash = g[g.rel == '-1']
            new_cap[src] = dict(n=n, w=w, e=round(e, 1), z=round(z, 2), roi=round(roi, 3), status=stt,
                                fire=[int((fire.res == 'W').sum()), len(fire)], clash=[int((clash.res == 'L').sum()), len(clash)])
            nm = SRC.get(src, src)
            if stt != prev:
                if stt == 'proven':
                    note('+', f'капер {nm}: {w}/{n}, ROI {roi * 100:+.0f}% (z +{_fmt(z)}) → ✔ доказан: его ⚔️ — вето, 🔥 — в силе')
                elif stt == 'weak':
                    note('+', f'капер {nm}: {w}/{n}, ROI {roi * 100:+.0f}% (z {_fmt(z)}) → ⛔ слабый: его 🔥 и ⚔️ не учитываются')
                else:
                    note('−', f"капер {nm}: статус «{'доказан' if prev == 'proven' else 'слабый'}» снят (z {_fmt(z)})")
    st['cappers'] = new_cap or cap
    st['updated'], st['sport'] = today_s, sport
    st['history'] = hist[-300:]
    save(sport, st)
    return dict(added=added, removed=removed, state=st)


# ───────────────────────────── блоки отчётов ─────────────────────────────
def day_block(sport: str, P: pd.DataFrame, E: pd.DataFrame, day: dt.date, res: dict) -> str:
    """«🧠 Обучение» под отчётом ставок дня: разбор ошибок суток, новые и снятые правила, действующие правила."""
    e = html.escape
    st = res.get('state') or load(sport)
    segs = st.get('segments') or {}
    ds = day.isoformat()
    out = []
    lost = P[(P.date == ds) & (P.result == 'L')] if len(P) else P
    for r in lost.to_dict('records'):
        s = segs.get(f"market:{r['market']}") or {}
        p = r['p']
        if s.get('n', 0) >= 5:
            verdict = (f"рынок «{MKT.get(r['market'], r['market'])}» {s['w']}/{s['n']} при ожидании {_fmt(s['e'])}"
                       + (' — сегмент понижен' if f"market:{r['market']}" in st.get('demoted', [])
                          else ' — ниже ожидания, под наблюдением' if s['z'] <= -1.0 else ' — в норме, ожидаемый риск'))
        else:
            verdict = 'данных по рынку мало — копим'
        out.append(f"❌ {e(r['label'])} (P {p * 100:.0f}%): {e(verdict)}")
    if len(E):
        for r in E[(E.date == ds) & E.new.isin(['W', 'L']) & (E.new != E.old)].to_dict('records'):
            v = (st.get('edits') or {}).get(r['cls'], {})
            out.append(f"✏️ правка «{e(r['cls'])}» {'ошиблась' if r['old'] == 'W' else 'оказалась верной'}"
                       f" — по этой причине: верных {v.get('right', 0)}, ошибочных {v.get('wrong', 0)}")
    if not out:
        won = P[(P.date == ds)] if len(P) else P
        out.append('ошибок за сутки нет' if len(won) else 'рассчитанных ставок за сутки нет')
    out += [f'📘 {e(t)}' for t in res.get('added', []) if not t.startswith('капер ')]
    out += [f'↩️ {e(t)}' for t in res.get('removed', []) if not t.startswith('капер ')]
    act = rules_line(sport)
    if act:
        out.append(f'Действует: {e(act)}')
    return '🧠 <b>Обучение</b>\n' + '\n'.join(out)


def cap_block(sport: str, res: dict) -> str:
    """«🧠 Обучение на каперах» под отчётом каперов: изменения доверия и действующие статусы (пусто — нечего сказать)."""
    e = html.escape
    st = res.get('state') or load(sport)
    ch = [f'📘 {e(t)}' for t in res.get('added', []) if t.startswith('капер ')] + \
         [f'↩️ {e(t)}' for t in res.get('removed', []) if t.startswith('капер ')]
    cap = st.get('cappers') or {}
    good = [SRC.get(s, s) for s, v in cap.items() if v.get('status') == 'proven']
    bad = [SRC.get(s, s) for s, v in cap.items() if v.get('status') == 'weak']
    watch = sum(1 for v in cap.values() if not v.get('status'))
    if not cap and not ch:
        return ''
    line = ' · '.join(x for x in ([f"✔ {', '.join(good)}"] if good else []) + ([f"⛔ {', '.join(bad)}"] if bad else [])
                      + ([f'наблюдение {watch}'] if watch else []))
    fw = sum(v.get('fire', [0, 0])[0] for v in cap.values()); fn = sum(v.get('fire', [0, 0])[1] for v in cap.values())
    cw = sum(v.get('clash', [0, 0])[0] for v in cap.values()); cn = sum(v.get('clash', [0, 0])[1] for v in cap.values())
    syn = (f'\nвсего: 🔥 угадано {fw}/{fn}' if fn else '') + (f' · ⚔️ права система {cw}/{cn}' if cn else '')
    return '🧠 <b>Обучение на каперах</b>\n' + '\n'.join(ch + [f'Доверие: {e(line)}' if line else '']).strip() + syn


def rules_line(sport: str) -> str:
    st = load(sport)
    parts = [f'{seg_name(k)} — понижен' for k in st.get('demoted', [])]
    parts += [f'{seg_name(k)} — сильный' for k in st.get('strong', [])]
    parts += [f'правки «{k}» — откл. до {v[8:10]}.{v[5:7]}' for k, v in (st.get('blocked') or {}).items()]
    if st.get('switch_extra'):
        parts.append(f"запас правки +{st['switch_extra'] * 100:.0f} п.п.")
    if st.get('overconf'):
        parts.append('общая переоценка P — на калибровке')
    if int(st.get('max_picks', 3)) < 3:
        parts.append(f"ставок в сутки ≤ {st['max_picks']}")
    return '; '.join(parts)


def week_block(sport: str, P: pd.DataFrame, E: pd.DataFrame, d0: dt.date, d1: dt.date) -> str:
    """«🧠 Аудит недели» под недельным отчётом ставок дня: калибровка, контроль качества, развитие."""
    e = html.escape
    st = load(sport)
    a, b = d0.isoformat(), d1.isoformat()
    W = P[(P.date >= a) & (P.date <= b)] if len(P) else P
    out = ['🧠 <b>Аудит недели</b>']
    rows = []                                                     # калибровка: корзины P — неделя / всего
    for band in ('0.60-0.70', '0.70-0.80', '0.80+'):
        def cell(df):
            g = df[df.p.map(_band) == band] if len(df) else df
            return f"{int((g.result == 'W').sum())}/{len(g)} ({_fmt(g.p.sum(), 1)})" if len(g) else '—'
        rows.append(f"{band.replace('0.', '').replace('-', '–').replace('80+', '80+'):<7}{cell(W):>12} {cell(P):>13}")
    out.append('Калибровка — угадано (ожидалось):\n<pre>' + e(f"{'P, %':<7}{'неделя':>12} {'всего':>13}\n" + '\n'.join(rows)) + '</pre>')
    q = []
    if len(W):
        w, n, ex, z = _test((W.result == 'W').astype(int), W.p.fillna(0.6))
        q.append(f"ставки недели {w}/{n} при ожидании {_fmt(ex)} (z {'+' if z >= 0 else ''}{_fmt(z)})")
        q.append(f"дней со ставкой {W.date.nunique()} · в среднем {_fmt(len(W) / max(1, W.date.nunique()))} в сутки")
    if len(E):
        we = E[(E.date >= a) & (E.date <= b)]
        if len(we):
            q.append(f"правок {len(we)}: верных {int(((we.new == 'W') & (we.old == 'L')).sum())}, ошибочных {int(((we.new == 'L') & (we.old == 'W')).sum())}")
    if q:
        out.append('Контроль качества: ' + e('; '.join(q)))
    hist = [h for h in st.get('history', []) if a <= h['date'] <= (d1 + dt.timedelta(days=1)).isoformat()]
    out.append('Развитие за неделю:\n' + ('\n'.join(f"{'📘' if h['sign'] == '+' else '↩️'} {e(h['text'])}" for h in hist[-12:])
                                         if hist else 'новых правил нет — данные копятся'))
    act = rules_line(sport)
    out.append(f'Действует: {e(act)}' if act else 'Действующих поправок нет.')
    return '\n'.join(out)


def to_learning(store, sport_ru: str, res: dict) -> int:
    """Новые и снятые правила → лист learning (kind «урок»): история самообучения в кнопке «🧠 Самообучение»."""
    rows = [(f'{sport_ru}: урок', t) for t in res.get('added', [])] + [(f'{sport_ru}: правило снято', t) for t in res.get('removed', [])]
    if not rows:
        return 0
    now = (pd.Timestamp.now(tz='UTC').tz_localize(None) + pd.Timedelta(hours=5)).strftime('%Y-%m-%d %H:%M')
    store.append('learning', [dict(when=now, kind='урок', title=t, text=x, sent='') for t, x in rows])
    return len(rows)


def cycle(sport: str, sport_ru: str, store, dp: pd.DataFrame, ch: pd.DataFrame | None, today: dt.date,
          log: list[str] | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Один вызов из прогона: данные → правила → лист learning. Сбой не ломает отчёт (пустой результат)."""
    try:
        P, E = frame_hk(dp) if sport == 'hockey' else frame_dp(dp, ch)
        res = learn(sport, P, E, today)
        n = to_learning(store, sport_ru, res) if store is not None else 0
        if log is not None:
            log.append(f"🧠 {sport}: правил +{len(res['added'])} −{len(res['removed'])}; в лист learning {n}; действует: {rules_line(sport) or 'нет'}")
        return P, E, res
    except Exception as ex:
        if log is not None:
            log.append(f'🧠 {sport}: сбой самообучения {ex.__class__.__name__}: {ex}')
        return pd.DataFrame(), pd.DataFrame(), {}


def safe(fn, *a) -> str:
    """Блок отчёта без риска уронить отправку."""
    try:
        return fn(*a) or ''
    except Exception as ex:
        return f'🧠 блок самообучения: сбой {ex.__class__.__name__}'
