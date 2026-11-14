"""🏒 Каперы по хоккею — учимся у них (решение пользователя 11.11.2026: «параллельно учись у каперов по КХЛ, они чаще ставят»).

Источник — архив каналов, который уже собирает футбольное облако (football/data/tips/book.csv, вид «хоккей»):
текст постов и купоны 1WIN (Green Line, OCR) с кф и статусом купона.
  1. Русские названия → матч нашей базы (КХЛ / НХЛ, hockey/data/games.csv.gz), сторона капера ↔ хозяева/гости.
  2. Рынок: ТБ/ТМ, ИТБ/ИТМ (команда по названию), Ф1/Ф2, П1/П2 (основное время), 1X/X2/12, «победа …» (с ОТ).
     «Команда, ТБ(2.5)» — в хоккее тотал матча ≤ 3,5 не бывает → это ИТ названной команды.
  3. Итог: статус купона (правда букмекера) или наш счёт основного времени; совпадение двух — проверка правил рынков.
  4. P модели для того же исхода (модель лиги на дату матча — без утечки) → 🔥 согласие (P ≥ 0,55) / ⚔️ спор (P ≤ 0,45).
  5. Чему учимся: точность и ROI каперов по лиге и рынку против рынка (1/кф); кто прав в спорах; линии букмекера КХЛ
     (кф каперов) — внешний ориентир, пока своих кф КХЛ нет. Вклад в P — только после n ≥ 30 и проверки (AUTOPILOT, A9).
Хранилище — hockey/data/tips.csv.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from hockey import hlines as HL
from hockey import hmodel as HM

ROOT = Path(__file__).resolve().parent
BOOK = ROOT.parent / 'football' / 'data' / 'tips' / 'book.csv'
OUT = ROOT / 'data' / 'tips.csv'
COLS = ['uid', 'src', 'post_ts', 'league', 'game_id', 'start', 'home', 'away', 'market', 'pick', 'line', 'label', 'k',
        'self', 'ours', 'result', 'p_model', 'p_imp', 'rel', 'raw', 'live']
SRC_RU = {'stavki_na_sport_betting1': 'Green Line', 'stavki': 'Ставки', 'xs_stavkii': 'XS', 'groznyi_ruslan': 'Грозный',
          'kapper_uk': 'Kapper UK'}

# русское название (регэксп по нижнему регистру) → английское в базе (регэксп). Порядок важен: «динамо минск» раньше «динамо».
KHL = [(r'динамо\s*мн|минск', r'minsk'), (r'динамо', r'(dinamo|dynamo) mos'), (r'авангард', r'avangard'),
       (r'автомобилист', r'avtomobilist|yekaterinburg|ekaterinburg'), (r'адмирал', r'admiral|vladivostok'), (r'ак\s*барс', r'ak bars|bars kazan|kazan'), (r'\bамур', r'amur|khabarovsk'),
       (r'барыс', r'barys'), (r'\bлада', r'lada|togliatti'), (r'локомотив', r'lokomotiv|yaroslavl'), (r'металлург', r'metallurg|magnitogorsk'),
       (r'нефтехимик', r'neftekhimik|nizh?nekamsk|niznekamsk'), (r'салават', r'salavat'), (r'северсталь', r'severstal|cherepovets'), (r'\bцска', r'cska'),
       (r'\bска\b', r'^ska|\bska\b'), (r'сибирь', r'sibir|novosibirsk'), (r'спартак', r'spartak'), (r'торпедо|\bнн\b', r'torpedo|nizhny novgorod'),
       (r'трактор', r'tra[ck]tor'), (r'шанха|куньлун|дракон|дрэгонс', r'shanghai|kunlun'), (r'витязь', r'vityaz'), (r'\bсочи', r'sochi')]
NHL = [(r'анахайм|дакс', 'ducks'), (r'бостон|брюинз', 'bruins'), (r'баффало|сейбрз', 'sabres'), (r'калгари|флэймз|флеймз', 'flames'),
       (r'каролин|харрикейн', 'hurricanes'), (r'чикаго|блэкхокс', 'blackhawks'), (r'колорадо|эвеланш', 'avalanche'),
       (r'коламбус|блю\s*джекетс', 'blue jackets'), (r'даллас|старз', 'stars'), (r'детройт|ред\s*уингз', 'red wings'),
       (r'эдмонтон|ойлерз', 'oilers'), (r'флорида|пантерз', 'panthers'), (r'лос-?\s*анджелес|кингз', 'kings'),
       (r'миннесота|уайлд', 'wild'), (r'монреаль|канадиенс', 'canadiens'), (r'нэшвилл|предаторз', 'predators'),
       (r'нью-?\s*джерси|девилз', 'devils'), (r'айлендерс', 'islanders'), (r'рейнджерс', 'rangers'), (r'оттава|сенаторз', 'senators'),
       (r'филадельфи|флайерз', 'flyers'), (r'питтсбург|пингвинз', 'penguins'), (r'сан-?\s*хосе|шаркс', 'sharks'),
       (r'сиэтл|кракен', 'kraken'), (r'сент-?\s*луис|блюз', 'blues'), (r'тампа|лайтнинг', 'lightning'),
       (r'торонто|мейпл', 'maple leafs'), (r'ванкувер|кэнакс', 'canucks'), (r'вегас|голден', 'golden knights'),
       (r'вашингтон|кэпиталз', 'capitals'), (r'виннипег|джетс', 'jets'), (r'\bюта|мамонт|аризона', 'utah')]
REL = {1: '🔥', -1: '⚔️', 0: '·'}


def _keys(ru: str) -> list[tuple[str, str]]:
    low = str(ru).lower().replace('ё', 'е')
    return [(lg, en) for lg, tab in (('KHL', KHL), ('NHL', NHL)) for r, en in tab if re.search(r, low)][:1]


def _hit(en: str, name: str) -> bool:
    return bool(re.search(en, str(name).lower()))


def match(a: str, b: str, ts: pd.Timestamp, games: pd.DataFrame) -> tuple[pd.Series, bool] | None:
    """Матч базы для пары капера; второй элемент — True, если капер назвал хозяев первыми."""
    ka, kb = _keys(a), _keys(b)
    if not ka or not kb or ka[0][0] != kb[0][0]:
        return None
    lg = ka[0][0]
    st = pd.to_datetime(games.start)
    g = games[(games.league == lg) & (st >= ts - pd.Timedelta(hours=6)) & (st <= ts + pd.Timedelta(hours=60))]
    for r in g.sort_values('start').itertuples(index=False):
        if _hit(ka[0][1], r.home) and _hit(kb[0][1], r.away):
            return pd.Series(r._asdict()), True
        if _hit(ka[0][1], r.away) and _hit(kb[0][1], r.home):
            return pd.Series(r._asdict()), False
    return None


NUM = r'(\d+(?:\.\d+)?)'


def _named(s: str, k: list) -> bool:
    if not k:
        return False
    tab = KHL if k[0][0] == 'KHL' else NHL
    return any(re.search(r, s) for r, en in tab if en == k[0][1])


def parse(pick: str, a: str, b: str) -> tuple[str, str, float | str] | None:
    """(рынок, выбор, линия) в терминах «команда a / команда b»: IT1/IT2, AH H/A, 1X2 H/A — сторона a = '1'."""
    s = re.sub(r'(\d),(\d)', r'\1.\2', str(pick).lower().replace('ё', 'е').replace('х', 'x')).replace('−', '-').replace('–', '-')
    ka, kb = _keys(a), _keys(b)
    side = None
    named = [n for n, k in (('1', ka), ('2', kb)) if _named(s, k)]
    if len(named) == 1:
        side = named[0]
    m = re.search(r'\bи\.?\s*т\.?\s*([12])?\s*([бм])\w*\s*([12])?\s*\(?\s*' + NUM, s)
    if m:
        d = m.group(1) or m.group(3) or side
        return (f'IT{d}', 'O' if m.group(2) == 'б' else 'U', float(m.group(4))) if d else None
    m = re.search(r'инд\w*\.?\s*тотал\w*.*?(больше|меньше)\s*\(?\s*' + NUM, s)
    if m:
        return (f'IT{side}', 'O' if m.group(1)[0] == 'б' else 'U', float(m.group(2))) if side else None
    m = re.search(r'\bт\s*([бм])\s*\(?\s*' + NUM, s) or re.search(r'тотал\w*\s*(больше|меньше)\s*\(?\s*' + NUM, s)
    if m:
        L = float(m.group(2))
        bm = 'O' if m.group(1)[0] == 'б' else 'U'
        if L <= 3.5:                                         # «Локомотив, ТБ(2.5)» — индивидуальный тотал
            return (f'IT{side}', bm, L) if side else None
        return 'TOT', bm, L
    m = re.search(r'\bф\s*([12])?\s*\(\s*([+-]?\s*' + NUM[1:-1] + r')\s*\)', s)
    if m and (m.group(1) or side):
        return 'AH', 'H' if (m.group(1) or side) == '1' else 'A', float(m.group(2).replace(' ', ''))
    m = re.search(r'\bп\s*([12])\b', s)
    if m:
        return '1X2', 'H' if m.group(1) == '1' else 'A', ''
    m = re.search(r'(?<![\d.])(1x|x2|2x|12)(?![\d.])', s)
    if m:
        return 'DC', {'2x': 'X2'}.get(m.group(1), m.group(1).upper()), ''
    if side and re.search(r'побед|выигра', s):
        return 'ML', 'H' if side == '1' else 'A', ''
    return None


def _orient(leg, home_first: bool):
    """Сторона капера (a = '1') → хозяева/гости матча базы."""
    mk, pk, L = leg
    if home_first:
        return mk, pk, L
    if mk in ('IT1', 'IT2'):
        return ('IT2' if mk == 'IT1' else 'IT1'), pk, L
    if mk in ('AH', '1X2', 'ML'):
        return mk, {'H': 'A', 'A': 'H'}.get(pk, pk), L
    if mk == 'DC':
        return mk, {'1X': 'X2', 'X2': '1X'}.get(pk, pk), L
    return mk, pk, L


def label(mk: str, pk: str, L) -> str:
    if mk == 'TOT':
        return f'Т{"Б" if pk == "O" else "М"}{L:g}'
    if mk in ('IT1', 'IT2'):
        return f'ИТ{mk[-1]} {"Б" if pk == "O" else "М"}{L:g}'
    if mk == 'AH':
        return f'Ф{1 if pk == "H" else 2}({L:+g})'
    if mk == '1X2':
        return {'H': 'П1 осн.', 'A': 'П2 осн.', 'D': 'X осн.'}[pk]
    if mk == 'ML':
        return 'П1 ОТ' if pk == 'H' else 'П2 ОТ'
    return pk


def p_model(m: HM.Model, home: str, away: str, mk: str, pk: str, L) -> float | None:
    """P выигрыша ставки (возврат исключён: P(W) / (1 − P(возврат)))."""
    lam = m.lambdas(home, away) if m else None
    if not lam:
        return None
    P = m.matrix(*lam)
    i, j = np.indices(P.shape)
    if mk == 'ML':
        ph, pd_ = float(P[i > j].sum()), float(np.trace(P))
        mh = ph + pd_ * m.ot_home(*lam)
        return mh if pk == 'H' else 1 - mh
    if mk == '1X2':
        return float(P[{'H': i > j, 'D': i == j, 'A': i < j}[pk]].sum())
    if mk == 'DC':
        return float(P[{'1X': i >= j, 'X2': i <= j, '12': i != j}[pk]].sum())
    v = {'TOT': i + j, 'IT1': i, 'IT2': j}.get(mk)
    if v is not None:
        w, push = float(P[(v > L) if pk == 'O' else (v < L)].sum()), float(P[v == L].sum())
    else:                                                        # AH
        d = (i - j + L) if pk == 'H' else (j - i + L)
        w, push = float(P[d > 0].sum()), float(P[d == 0].sum())
    return w / max(1 - push, 1e-9)


def _num(x):
    try:
        return float(str(x).replace(',', '.'))
    except Exception:
        return float('nan')


def run(games: pd.DataFrame, log: list[str], models: dict | None = None) -> pd.DataFrame:
    """Пересборка таблицы ставок каперов по хоккею (малый объём — каждый прогон целиком)."""
    if not BOOK.exists():
        return pd.DataFrame(columns=COLS)
    b = pd.read_csv(BOOK, dtype=str, keep_default_na=False)
    b = b[(b.sport == 'hockey') & (b.a != '') & (b.b != '')]
    models = models if models is not None else {}
    fin = games.set_index('id')
    out, nomatch, noparse = [], 0, 0
    for r in b.itertuples(index=False):
        ts = pd.to_datetime(r.post_ts, errors='coerce')
        if pd.isna(ts):
            continue
        mt = match(r.a, r.b, ts, games)
        if mt is None:
            nomatch += 1; continue
        g, hf = mt
        leg = parse(r.pick, r.a, r.b)
        if leg is None:
            noparse += 1; continue
        mk, pk, L = _orient(leg, hf)
        live = pd.to_datetime(g.start) < ts - pd.Timedelta(minutes=5)   # 14.11 20:38: после начала — учитывается с пометкой 🔴
        if pd.to_datetime(g.start) < ts - pd.Timedelta(hours=3):       # пост после конца матча — не прогноз
            continue
        day = pd.Timestamp(pd.to_datetime(g.start).date())
        key = (g.league, day)
        if key not in models:
            models[key] = HM.fit(games, g.league, day)
        p = p_model(models[key], g.home, g.away, mk, pk, L)
        ours = ''
        if str(g.end) in ('REG', 'OT', 'SO'):
            x = fin.loc[g.id]
            ours = HL.settle(mk, pk, L, x.end, x.hg, x.ag, x.hg_reg, x.ag_reg)
        selfm = r.self if r.self in ('W', 'L', 'P') else ''
        k = _num(r.k)
        rel = '' if p is None or live else (1 if p >= 0.55 else -1 if p <= 0.45 else 0)   # live: система не оценивает
        out.append(dict(uid=r.uid, src=r.src, post_ts=r.post_ts, league=g.league, game_id=g.id, start=g.start, home=g.home,
                        away=g.away, market=mk, pick=pk, line=L, label=label(mk, pk, L), k=k if k == k else '', self=selfm,
                        ours=ours, result=selfm or ours, p_model='' if p is None else round(p, 4),
                        p_imp=round(1 / k, 4) if k == k and k > 1 else '', rel=rel, raw=str(r.pick)[:80], live='1' if live else ''))
    df = pd.DataFrame(out, columns=COLS)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False)
    log.append(f'🏒 каперы: ставок {len(df)} (КХЛ {int((df.league == "KHL").sum())}, НХЛ {int((df.league == "NHL").sum())}), '
               f'не найден матч {nomatch}, рынок не разобран {noparse}')
    return df


def stats(df: pd.DataFrame, league: str | None = None) -> dict:
    """Итоги каперов: n, W, L, P, ROI (по кф), средняя P рынка (1/кф) и модели, споры с моделью."""
    d = df[df.result.isin(['W', 'L', 'P'])] if len(df) else df
    if league:
        d = d[d.league == league]
    if not len(d):
        return dict(n=0)
    k = d.k.map(_num)
    pnl = np.where(d.result == 'W', k - 1, np.where(d.result == 'L', -1.0, 0.0))
    ok = ~np.isnan(pnl)
    wl = d[d.result.isin(['W', 'L'])]
    both = d[(d.self != '') & (d.ours != '')]
    rel = {v: wl[wl.rel.astype(str) == str(v)] for v in (1, -1)}
    return dict(n=len(d), w=int((d.result == 'W').sum()), l=int((d.result == 'L').sum()),
                roi=float(pnl[ok].mean()) if ok.any() else float('nan'), nk=int(ok.sum()),
                p_imp=float(wl.p_imp.map(_num).mean()), p_mod=float(wl.p_model.map(_num).mean()),
                fire=(int((rel[1].result == 'W').sum()), len(rel[1])), clash=(int((rel[-1].result == 'W').sum()), len(rel[-1])),
                agree=(int((both.self == both.ours).sum()), len(both)))


def for_game(df: pd.DataFrame, gid: str) -> list[str]:
    """Строки капера к матчу прогноза: «🔥 Green Line: ТБ4,5 @1,64 · модель 58%»."""
    if not len(df):
        return []
    out = []
    for r in df[df.game_id.astype(str) == str(gid)].itertuples(index=False):
        p = _num(r.p_model)
        k = _num(r.k)
        out.append(f"{REL.get(r.rel if r.rel != '' else 0, '·')} {SRC_RU.get(r.src, r.src)}: {r.label}"
                   + (f" @{k:.2f}".replace('.', ',') if k == k else '') + (f" · модель {p * 100:.0f}%" if p == p else ''))
    return out
