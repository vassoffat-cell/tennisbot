"""👥 Отчёт по каперам за период — отдельное сообщение для каждого вида спорта (решение пользователя 14.11.2026).

«Отдельным сообщением отчёт по каперам по теннису (если были), если не было — так и пиши: по теннису каперы не прогнозировали».
Источники (без проектных импортов — модуль общий для тенниса, хоккея и футбола):
  • football/data/tips/book.csv — посты и купоны всех каналов, все виды спорта, отметки канала (✅/❌ → self);
  • football/data/tips/tips_all.csv — футбол nb-bet и каналы по табло ESPN (итог по счёту);
  • hockey/data/tips.csv — хоккей: итог по нашей базе и оценка системы (rel: 1 🔥 / −1 ⚔️ / 0 нейтрально).
Каждый прогноз: канал · матч · ставка · кф · итог · согласна ли система. Купоны, показанные после матча, — не прогнозы. Матчи вне нашей базы тоже показываются (P.S. 14.11).
"""
from __future__ import annotations

import datetime as dt
import html
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
BOOK = ROOT / 'football' / 'data' / 'tips' / 'book.csv'
WIDE = ROOT / 'football' / 'data' / 'tips' / 'tips_all.csv'
HK = ROOT / 'hockey' / 'data' / 'tips.csv'
IND = ROOT / 'football' / 'data' / 'tips' / 'book_ind.csv'   # 18.11 К13: независимая сверка строк книги с табло ESPN
SRC = {'stavki_na_sport_betting1': 'Green Line', 'stavki': 'Прогнозы', 'xs_stavkii': 'Хитрости', 'groznyi_ruslan': 'Грозный',
       'kapper_uk': 'Каппер UK', 'nb-bet': 'nb-bet'}
SPORT = {'tennis': ('🎾', 'Теннис', 'по теннису'), 'hockey': ('🏒', 'Хоккей', 'по хоккею'), 'football': ('⚽', 'Футбол', 'по футболу')}
MARK = {'W': '✅', 'L': '❌', 'P': '⚪', 'N': '▫'}   # 18.11: N — итог не найден (прогноз старше суток без итога)
STALE_H = 24
REL = {'1': ' 🔥', '-1': ' ⚔️', '0': ' ·'}


def _read(p: Path) -> pd.DataFrame:
    try:
        d = pd.read_csv(p, dtype=str, keep_default_na=False)
        if 'src' in d.columns:
            d = d[d.src != 'nb-bet']                                 # 14.11: nb-bet отключён пользователем
        return d.reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


def _num(x):
    try:
        return float(str(x).replace(',', '.'))
    except Exception:
        return float('nan')


def rows(sport: str, d0: dt.date, d1: dt.date, day_start: int = 8, tz: int = 5) -> pd.DataFrame:
    """Прогнозы каперов вида sport, опубликованные в сутки системы d0…d1 (сутки начинаются в day_start по Ташкенту)."""
    out = []
    b = _read(BOOK)
    if len(b):
        b = b[(b.sport == sport) & (b.a != '')]
        hk = _read(HK).set_index('uid') if sport == 'hockey' and HK.exists() else pd.DataFrame()
        ind = _read(IND).set_index('uid') if sport == 'football' and IND.exists() else pd.DataFrame()
        for r in b.itertuples(index=False):
            if getattr(r, 'seen', '') == 'post':                 # купон, показанный после матча, — не прогноз
                continue
            if str(r.pick).startswith('❔'):                     # 15.11: не разобранный пост (страховочная сеть) — в статистику не идёт
                continue
            res, rel, ind_, live, eid, lab = r.self if r.self in MARK else '', '', '', '', '', ''
            if len(ind) and r.uid in ind.index:                     # К13: итог по табло важнее отметки самого канала
                x = ind.loc[r.uid]
                if x.result in ('W', 'L', 'P'):
                    res, ind_ = x.result, x.result
                eid, lab = str(x.espn_id), str(x.label)
            if len(hk) and r.uid in hk.index:
                h = hk.loc[r.uid]
                ind_ = h.result if h.result in MARK else ''
                res = res or ind_
                rel = str(h.rel)
                live = '1' if str(h.get('live', '')) == '1' else ''
            out.append(dict(ts=r.post_ts, src=r.src, match=f'{r.a} — {r.b}', pick=str(r.pick)[:40], k=r.k, res=res, rel=rel, ind=ind_, live=live,
                            post=str(getattr(r, 'post_id', '')), wide=0, eid=eid, lab=lab))
    if sport == 'football':
        w = _read(WIDE)
        for r in w.itertuples(index=False) if len(w) else []:
            live = '1' if pd.to_datetime(r.post_ts, errors='coerce') > pd.to_datetime(r.start_utc, errors='coerce') else ''
            out.append(dict(ts=r.post_ts, src=r.src, match=f'{r.home} — {r.away}', pick=str(r.label or r.raw)[:40], k=r.k,
                            res=r.result if r.result in MARK else '', rel='', ind=r.result if r.result in MARK else '', live=live,
                            post=(str(r.uid).split(':')[2] if str(r.uid).startswith('tg:') and str(r.uid).count(':') >= 2 else ''), wide=1,
                            eid=str(r.espn_id), lab=str(r.label)))
    # 14.11 20:38 (решение пользователя): прогноз, опубликованный после начала матча (live = '1'), учитывается в статистике
    # и самообучении, но помечен 🔴
    df = pd.DataFrame(out, columns=['ts', 'src', 'match', 'pick', 'k', 'res', 'rel', 'ind', 'live', 'post', 'wide', 'eid', 'lab'])
    if not len(df):
        return df
    loc = pd.to_datetime(df.ts, errors='coerce') + pd.Timedelta(hours=tz) - pd.Timedelta(hours=day_start)
    day = loc.dt.date
    df = df[(day >= d0) & (day <= d1)].copy()
    df = df.drop_duplicates(['src', 'match', 'pick'])
    # одна ставка из поста канала и из сверки с табло ESPN (то же время и кф) — оставить версию с итогом
    df['has'] = (df.res != '').astype(int)
    kk = df.k.map(_num)
    keyed = df[kk == kk].sort_values('has', ascending=False).drop_duplicates(['src', 'ts', 'k'])
    df = pd.concat([keyed, df[kk != kk]])
    # 18.11 (пользователь: «почему матчи ждут завершения, хотя давно закончились?»): один и тот же пост — строка книги (итог только
    # по отметке канала) и строка сверки с табло ESPN (итог по счёту). Книжные строки без итога этого поста убираются, если сверка
    # дала не меньше строк, чем книга (иначе неизвестно, какая из них «двойник», — остаются).
    drop = []
    for (s, p), g in df[df.post.astype(str) != ''].groupby(['src', 'post']):
        nw = int((g.wide == 1).sum())
        bk = g[(g.wide == 0) & (g.res == '')]
        if nw and len(bk) and len(bk) <= nw:
            drop += list(bk.index)
    df = df.drop(index=drop)
    # К13: строка книги и строка сверки — одна и та же ставка (тот же канал, матч ESPN и запись ставки) → остаётся сверка
    wk = {(s, e, l) for s, e, l, w in zip(df.src, df.eid, df.lab, df.wide) if w == 1 and e}
    dup = pd.Series([(s, e, l) in wk for s, e, l in zip(df.src, df.eid, df.lab)], index=df.index)
    df = df[~((df.wide == 0) & dup)]
    # прогноз старше суток без итога — не «ждёт», а «итог не найден» (рынок без данных: угловые, карточки; матч не опознан)
    old = pd.to_datetime(df.ts, errors='coerce') < pd.Timestamp.now(tz='UTC').tz_localize(None) - pd.Timedelta(hours=STALE_H)
    df.loc[(df.res == '') & old, 'res'] = 'N'
    return df.drop(columns=['has', 'post', 'wide', 'eid', 'lab']).sort_values('ts')        # ind — итог по независимому табло (ESPN / наша база), не отметка канала


def report(sport: str, d0: dt.date, d1: dt.date, title: str, day_start: int = 8) -> str:
    """Сообщение «👥 <вид> · каперы · <период>» (HTML) — одним сообщением (суточный отчёт ② в чате)."""
    return '\n'.join(blocks(sport, d0, d1, title, day_start))


def blocks(sport: str, d0: dt.date, d1: dt.date, title: str, day_start: int = 8) -> list[str]:
    """18.11 (пользователь: «отчёт по каждому каперу отдельно»): [сводка по всем каналам, затем каждый капер — своим сообщением].
    Аналитика (архив меню) шлёт их отдельными сообщениями; report() склеивает для суточного ② в чате."""
    icon, ru, po = SPORT[sport]
    e = html.escape
    df = rows(sport, d0, d1, day_start)
    head = f'👥 <b>{ru} · каперы · {e(title)}</b>'
    if not len(df):
        return [f'{head}\n{po} каперы не прогнозировали.'.replace('\nпо', '\nПо', 1)]
    done = df[df.res.isin(['W', 'L'])]
    w = int((done.res == 'W').sum())
    k = done.k.map(_num)
    pnl = [(kk - 1) if r == 'W' else -1.0 for r, kk in zip(done.res, k) if kk == kk]
    roi = f' · ROI {sum(pnl) / len(pnl) * 100:+.0f}% (по их кф, n {len(pnl)})' if pnl else ''
    fire = df[df.rel == '1']; clash = df[df.rel == '-1']
    syn = ''
    if len(fire) or len(clash):
        fw = int((fire.res == 'W').sum()); cw = int((clash.res == 'L').sum())
        syn = f'\n🔥 система согласна: {fw}/{int(fire.res.isin(["W", "L"]).sum())} угадано · ⚔️ против: права система {cw}/{int(clash.res.isin(["W", "L"]).sum())}'
    # 18.11 (пользователь: «почему только Green Line? где остальные?»): раньше список резался на 60 строках — в месячном отчёте
    # (154 прогноза) Green Line занимала их почти все. Теперь: сводка по ВСЕМ каналам сверху (видна сразу), затем каждый канал —
    # своим раскрывающимся блоком, без обрезки (длинное сообщение режет notify._split_html / archive split_fn с целыми тегами).
    order = df.groupby('src', sort=False).size().sort_values(ascending=False).index
    summ, parts = [], []
    for src in order:
        g = df[df.src == src]
        gd = g[g.res.isin(['W', 'L'])]
        gw, gn = int((gd.res == 'W').sum()), len(gd)
        gp = [(kk - 1) if r == 'W' else -1.0 for r, kk in zip(gd.res, gd.k.map(_num)) if kk == kk]
        name = e(SRC.get(src, src))
        nn = int((g.res == 'N').sum())
        summ.append(f'{name} — ' + (f'{gw}/{gn}' if gn else 'итогов нет') + f' · прогнозов {len(g)}'
                    + (f' · ROI {sum(gp) / len(gp) * 100:+.0f}%' if gp else '') + (f' · ▫ {nn}' if nn else ''))
        rows_ = []
        for r in g.itertuples(index=False):
            kk = _num(r.k)
            rows_.append(f"{MARK.get(r.res, '⏳')} {e(r.match)} · {e(r.pick)}" + (f' @{kk:.2f}'.replace('.', ',') if kk == kk else '')
                         + REL.get(r.rel, '') + (' 🔴' if r.live == '1' else ''))
        parts.append(f'👤 <b>{name}</b> · {e(title)} — ' + (f'{gw}/{gn}' if gn else 'итогов нет') + f' · прогнозов {len(g)}'
                     + (f' · ROI {sum(gp) / len(gp) * 100:+.0f}%' if gp else '') + (f' · ▫ итог не найден {nn}' if nn else '')
                     + '\n<blockquote expandable>' + '\n'.join(rows_) + '</blockquote>')
    return [f'{head} — {len(df)} прогнозов · угадано {w}/{len(done)}{roi}{syn}\n' + '\n'.join(summ)] + parts
