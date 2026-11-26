"""Дешёвая проверка здоровья за последние N часов (по умолчанию 26) — вместо перечитывания чата и всех логов.

Запуск (чат разработки, аудит, автопилот): python3 engine/health.py [часы]
Читает только GitHub API: итоги прогонов по workflow, плановые запуски, которые не случились, и ключевые строки
ТОЛЬКО тех прогонов, где должны быть контрольные события (engine/CONTROL.md, раздел «Контрольные точки»).
Вывод — таблица «точка | ожидание | факт | ✅/❌». Логи целиком не скачиваются.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

REPO = 'vassoffat-cell/tennisbot'
TZ = timedelta(hours=5)                     # Ташкент


def gh(path: str) -> dict:
    return json.loads(subprocess.run(['gh', 'api', path], capture_output=True, text=True, check=True).stdout)


_RUNS: dict[tuple[str, datetime], list[dict]] = {}


def runs(wf: str, since: datetime) -> list[dict]:
    """Все прогоны workflow с момента since (16.11 аудит: постранично — за 168 ч их > 100, раньше счёт обрезался на 100)."""
    key = (wf, since)
    if key not in _RUNS:
        out: list[dict] = []
        for page in range(1, 16):                           # 23.11: месяц прогонов (minutes_check) — до 1 500
            js = gh(f'repos/{REPO}/actions/workflows/{wf}/runs?per_page=100&page={page}&created=>={since:%Y-%m-%dT%H:%M:%SZ}')
            got = js.get('workflow_runs', [])
            out += got
            if len(got) < 100:
                break
        _RUNS[key] = out
    return _RUNS[key]


def log(run_id: int) -> str:
    return subprocess.run(['gh', 'run', 'view', str(run_id), '--repo', REPO, '--log'], capture_output=True, text=True).stdout


def grep(text: str, pat: str) -> list[str]:
    return [re.sub(r'^.*?Z ', '', x)[:160] for x in text.splitlines() if re.search(pat, x)]


def _dur(r: dict) -> float:
    """Длительность прогона, с (шлюз Ц7: пропущенный прогон ≈ 5–20 с)."""
    try:
        a = datetime.fromisoformat((r.get('run_started_at') or r['created_at']).replace('Z', '+00:00'))
        return (datetime.fromisoformat(r['updated_at'].replace('Z', '+00:00')) - a).total_seconds()
    except Exception:
        return 0.0


def minutes_check() -> list[tuple[str, str, str, str]]:
    """23.11 (ORG IMP-01): минуты GitHub Actions за месяц и прогноз до конца месяца.
    Оценка по прогонам: каждый прогон — длительность, округлённая вверх до минуты (так считает GitHub); Billing API из облака
    недоступен, поэтому это [РАСЧЁТ], не [ФАКТ]. Лимит 2 000 — при перерасходе пользователь докупает (23.11 13:44): ⚠️, не ❌."""
    import math
    now = datetime.now(timezone.utc)
    m0 = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    wfs = [w['path'].split('/')[-1] for w in gh(f'repos/{REPO}/actions/workflows?per_page=100').get('workflows', [])]
    used, last3 = 0, 0
    for wf in wfs:
        for r in runs(wf, m0):
            mins = math.ceil(max(_dur(r), 1) / 60)
            used += mins
            if datetime.fromisoformat(r['created_at'].replace('Z', '+00:00')) >= now - timedelta(days=3):
                last3 += mins
    nxt = (m0 + timedelta(days=32)).replace(day=1)
    left_days = (nxt - now).total_seconds() / 86400
    fc = used + last3 / 3 * left_days
    return [('минуты GitHub: месяц / прогноз (расчёт по прогонам)', '≤ 2 000',
             f'{used} / ≈ {fc:.0f} (темп {last3 / 3:.0f}/сутки)', '✅' if fc <= 2000 else '⚠️')]


def oddspapi_check() -> list[tuple[str, str, str, str]]:
    """25.11 (Ц6б): месячный учёт OddsPapi — всего / теннис / КХЛ; теннис в приоритете, КХЛ — только из остатка."""
    import json
    from pathlib import Path
    f = Path(__file__).resolve().parent.parent / 'data' / 'odds' / 'oddspapi_usage.json'
    try:
        m = json.loads(f.read_text(encoding='utf-8')).get(datetime.now(timezone.utc).strftime('%Y-%m'), {})
    except Exception:
        return [('OddsPapi: запросов за месяц', '≤ 250', 'нет учёта', '⚠️')]
    tot = sum(int(v) for v in m.values())
    parts = ' · '.join(f'{k} {v}' for k, v in sorted(m.items()))
    return [('OddsPapi: запросов за месяц (Ц6б)', '≤ 245', f'{tot} ({parts})', '✅' if tot <= 245 else '❌')]


def seal_today() -> list[tuple[str, str, str, str]]:
    """🔏 С16: снимок линий суток есть у тенниса и хоккея (футбол — если у него были матчи; полная сверка — seal_check.py)."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    day = (datetime.now(timezone.utc) + TZ - timedelta(hours=12)).date()      # к 12:00 сутки всех видов уже начались
    # 16.11 аудит: снимок хоккейных суток пишет первый прогон ПОСЛЕ 12:00 (плановый 13:07, GitHub сдвигает до ~14:30)
    dh = (datetime.now(timezone.utc) + TZ - timedelta(hours=14, minutes=30)).date()
    got = {s: sorted((root / d / 'seal').glob(f'{s}_*.csv'))[-1:] for s, d in
           (('tennis', 'data'), ('hockey', 'hockey/data'), ('football', 'football/data'))}
    last = {s: (f[0].stem.split('_')[1] if f else '—') for s, f in got.items()}
    ok = last['tennis'] != '—' and last['tennis'] >= str(day) and last['hockey'] != '—' and last['hockey'] >= str(dh)
    return [('печать С16: последний снимок суток', f'≥ {day:%d.%m}', ', '.join(f'{s} {v}' for s, v in last.items()), '✅' if ok else '❌')]


K16_UTC = '2026-11-21 05:13'   # 23.11 аудит: К16 (21.11 10:13 Ташкент) — открытый купон 1WIN больше не «после матча»; купоны до К16,
                                # переразмеченные задним числом «post → pre» (7 строк 29.10–20.11), — история выговора 21.11, а не новый пропуск
K9_UTC = '2026-11-15 14:06'    # К9 «страховочная сеть» (15.11 19:06 Ташкент): с этого момента книга v10 и сообщение «❔»


def cappers(since: datetime) -> list[tuple[str, str, str, str]]:
    """👥 Нерушимое правило (CONTROL Г2/Г2а/К6): каналы читаются каждый час, КАЖДЫЙ прогноз — отдельным сообщением.
    ① перерыв между чтениями каналов ≤ 75 мин (с 14.11 каналы читает ТОЛЬКО cappers.yml — Ц7; футбол не считается);
    ② прогнозы, прочитанные за окно, но не присланные (журнал книги против runtime.tips_alerted; нужен свежий git pull)."""
    out = []
    ts = sorted(datetime.fromisoformat((r.get('run_started_at') or r['created_at']).replace('Z', '+00:00'))
                for wf in ('cappers.yml',) for r in runs(wf, since - timedelta(hours=2)) if r['conclusion'] == 'success')
    ts = [t for t in ts if t >= since - timedelta(hours=2)] + [datetime.now(timezone.utc)]
    gaps = [(b - a, a) for a, b in zip(ts, ts[1:]) if b >= since
            and not 2 <= (a + TZ).hour <= 14]                    # 19.11 Г0б: перерыв, начатый в 02–14 Ташкента (тихое окно 03–15), не ошибка
    g, at = max(gaps) if gaps else (timedelta(0), since)
    out.append(('каперы: перерыв между чтениями каналов', '≤ 75 мин', f'макс {int(g.total_seconds() // 60)} мин (с {(at + TZ):%d.%m %H:%M})',
                '✅' if g <= timedelta(minutes=75) else '❌'))
    try:
        import csv
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent / 'football' / 'data'
        sent = set(json.loads((root / 'runtime.json').read_text(encoding='utf-8')).get('tips_alerted', []))
        edge = (since.replace(tzinfo=None)).strftime('%Y-%m-%d %H:%M')
        posts: dict[tuple[str, str], list[dict]] = {}
        with open(root / 'tips' / 'book.csv', encoding='utf-8') as f:
            for r in csv.DictReader(f):
                if r['post_ts'] >= edge:
                    posts.setdefault((r['src'], r['post_id']), []).append(r)
        miss, hist = [], []
        for (src, pid), rs in posts.items():                  # 16.11: доставка — по ПОСТУ (текст + купон одного поста = одно сообщение)
            if any(r['uid'] in sent for r in rs):
                continue
            slips = [r for r in rs if r['slip']]
            want = [r for r in slips if r['seen'] == 'pre' and not r['self']]       # купон до матча — прогноз
            if not slips:                                                           # купонов нет — текст поста (кроме рекламы)
                want += [r for r in rs if not re.search(r'реклам|промокод|подписат', r['pick'], re.I)]
            if want:                                         # 16.11 аудит: посты ДО К9 (15.11 19:06), найденные пересборкой книги
                t0 = min(r['post_ts'] for r in rs)
                (hist if t0 < K9_UTC or (slips and t0 < K16_UTC) else miss).append(f'{src}:{pid}')   # задним числом, — история
        out.append(('каперы: прочитаны, но не присланы', '0', f"{len(miss)}" + (f" ({', '.join(miss[:4])})" if miss else '')
                    + (f'; до К9/К16 (история, пересборка): {len(hist)}' if hist else ''), '✅' if not miss else '❌'))
    except Exception as e:
        out.append(('каперы: прочитаны, но не присланы', '0', f'нет данных ({e.__class__.__name__})', '❌'))
    return out


def _special(r: dict) -> bool:
    """16.11 аудит: разовая проверка (research) и репетиция (rehearse) — не полный прогон: без отправок и 🔒 (название прогона)."""
    return any(k in (r.get('display_title') or '') for k in ('rehearse', 'research'))



KEY = [('daily.yml', (11, 15), r'суточный отчёт отправлен|суточный отчёт: тихий режим', 'отчёт тенниса'),   # 25.11 Р8
       ('daily.yml', (11, 30), r'ставка дня: объявлена|ставка дня: тихий режим', '🎾1 / межсезонье'),
       ('football.yml', (11, 15), r'суточный отчёт отправлен', 'отчёт футбола'),
       ('football.yml', (11, 30), r'ставка дня: объявлена|ставка дня: тихий режим', '⚽1'),
       ('daily.yml', (12, 15), r'🏒 суточный отчёт \d\d\.\d\d отправлен', 'отчёт хоккея'),
       ('daily.yml', (12, 30), r'⭐🏒 .*⭐1|⭐🏒 \d', '🏒1')]
LATE_MIN = 30                                   # A35 (аудит 16.11): опоздание > 30 мин от начала ключевого часа = ❌


def punctual(cache: dict) -> list[tuple[str, str, str, str]]:
    """A35: во сколько на самом деле пришли ключевые сообщения последних суток — прогон с событием, минут от начала часа."""
    now = datetime.now(timezone.utc)
    out = []
    for wf, (hh, mm), pat, name, *frm in KEY:
        loc = now + TZ
        slot = loc.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if slot > loc:
            slot -= timedelta(days=1)
        s_utc = slot - TZ
        f_utc = s_utc - timedelta(minutes=(hh * 60 + mm) - (frm[0][0] * 60 + frm[0][1])) if frm else s_utc
        rs = sorted([r for r in runs(wf, f_utc) if r['conclusion'] == 'success' and _dur(r) > 25],
                    key=lambda r: r.get('run_started_at') or r['created_at'])
        hit = None
        for r in rs:
            t0 = datetime.fromisoformat((r.get('run_started_at') or r['created_at']).replace('Z', '+00:00'))
            if t0 > s_utc + timedelta(hours=6):
                break
            cache.setdefault(r['id'], log(r['id']))
            if grep(cache[r['id']], pat):
                hit = t0
                break
        if hit is None:
            late = int((now - s_utc).total_seconds() // 60)
            out.append((f'вовремя: {name} ({slot:%d.%m %H:%M})', f'≤ {LATE_MIN} мин',
                        'нет' + (f' (прошло {late} мин)' if late < 360 else ''), '❌' if late > LATE_MIN else '⏳'))
            continue
        late = max(0, int((hit - s_utc).total_seconds() // 60))
        out.append((f'вовремя: {name} ({slot:%d.%m %H:%M})', f'≤ {LATE_MIN} мин', f'{(hit + TZ):%H:%M} (+{late} мин)',
                    '✅' if late <= LATE_MIN else '❌'))
    return out

def plan_check(since: datetime) -> list[tuple[str, str, str, str]]:
    """ТЗ-007 шаг 2: каждое окно плана (data/plan.json, football/data/plan.json) закрыто прогоном бота в [at − 15 мин, hi + 15 мин]."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    now = datetime.now(timezone.utc)
    out = []
    for wf, fn, name in (('daily.yml', 'data/plan.json', 'теннис/хоккей'), ('football.yml', 'football/data/plan.json', 'футбол')):
        try:
            gs = json.loads((root / fn).read_text(encoding='utf-8')).get('groups', [])
        except Exception:
            gs = []
        ts = [datetime.fromisoformat((r.get('run_started_at') or r['created_at']).replace('Z', '+00:00'))
              for r in runs(wf, since - timedelta(hours=1)) if not _special(r)]
        due, miss = 0, []
        for g in gs:
            at = datetime.strptime(g['at'], '%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc)
            hi = datetime.strptime(g['hi'], '%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc)
            if not (since <= at and hi < now - timedelta(minutes=15)):
                continue
            due += 1
            if not any(at - timedelta(minutes=15) <= t <= hi + timedelta(minutes=15) for t in ts):
                miss.append(f'{(at + TZ):%H:%M}')
        out.append((f'план по матчам ({name}): окно → прогон', 'все', f'окон {due}, без прогона {len(miss)}'
                    + (f" ({', '.join(miss[:6])})" if miss else ''), '⏳' if not due else ('✅' if not miss else '❌')))
    return out


def capper_check() -> list[tuple[str, str, str, str]]:
    """К13 (18.11): сверка книги каперов с табло — совпадение с собственными отметками каналов (должно быть 100%) и доля
    прогнозов без итога за 7 дней (рост — новая формулировка канала, которую разбор не знает)."""
    import pandas as pd
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / 'football' / 'data' / 'tips'
    try:
        b = pd.read_csv(root / 'book.csv', dtype=str, keep_default_na=False).set_index('uid')
        ind = pd.read_csv(root / 'book_ind.csv', dtype=str, keep_default_na=False)
    except Exception as e:
        return [('каперы: сверка книги с табло (К13)', 'файлы', f'нет: {e.__class__.__name__}', '❌')]
    ind = ind[ind.uid.isin(b.index)]
    ind['self'] = ind.uid.map(b['self'])
    ind['ts'] = pd.to_datetime(ind.uid.map(b['post_ts']), errors='coerce')
    both = ind[ind.result.isin(['W', 'L']) & ind.self.isin(['W', 'L'])]
    bad = int((both.result != both.self).sum())
    rows = [('каперы: сверка с табло = отметки каналов (К13)', '100%', f'{len(both) - bad}/{len(both)}', '✅' if not bad else '❌')]
    ind['sport'] = ind.uid.map(b['sport'])                       # 24.11: табло ESPN — только футбол (хоккей и теннис — свои базы)
    wk = ind[(ind.sport == 'football') & (ind.ts > pd.Timestamp.now() - pd.Timedelta(days=7)) & (ind.ts < pd.Timestamp.now() - pd.Timedelta(hours=26))]
    if len(wk):
        nf = int(wk.why.isin(['матч не найден', 'не разобрано']).sum())
        rows.append(('каперы: без итога за 7 дн. (не найден / не разобрано)', '≤ 20%', f'{nf}/{len(wk)} ({nf / len(wk) * 100:.0f}%)',
                     '✅' if nf <= 0.2 * len(wk) else '❌'))
    return rows


def shadow_check(since: datetime, cache: dict | None = None) -> list[tuple[str, str, str, str]]:
    """С21 (19.11, «взгляд со стороны»): теневой портфель растёт — за окно добавлены стороны с ценой, если у вида были матчи
    (снимок суток С16 непустой). Дважды находили «тихую» пустоту: портфель получал только кандидатов ⭐ (1 строка за 2 суток)."""
    import pandas as pd
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    edge = since.astimezone(timezone.utc).replace(tzinfo=None).strftime('%Y-%m-%d %H:%M')
    rows = []
    for sport, folder in (('tennis', 'data'), ('football', 'football/data'), ('hockey', 'hockey/data')):   # 21.11: + хоккей
        fs = sorted((root / folder / 'shadow').glob(f'{sport}_*.csv'))[-2:]
        try:
            d = pd.concat([pd.read_csv(f, dtype=str, keep_default_na=False) for f in fs]) if fs else pd.DataFrame(columns=['first_at', 'market'])
        except Exception as e:
            rows.append((f'теневой портфель ({sport})', 'растёт', f'нет данных ({e.__class__.__name__})', '❌')); continue
        new = d[d.first_at >= edge]
        seal = 0
        for f in sorted((root / folder / 'seal').glob(f'{sport}_*.csv'))[-2:]:
            if f.stem.split('_')[1] >= edge[:10] or f.stat().st_mtime >= since.timestamp():
                try:                                       # 21.11: хоккей — цена только у НХЛ (КХЛ без цены, С26а)
                    seal += sum(1 for ln in list(open(f, encoding='utf-8'))[1:] if sport != 'hockey' or ln.startswith('nhl:'))
                except Exception:
                    pass
        if sport == 'hockey' and not len(new) and seal and cache is not None:   # 21.11: цены НХЛ берутся за 3 ч до матчей —
            priced = 0                                                       # пусто без цен — не сбой
            for r in [r for r in runs('daily.yml', since) if r['conclusion'] == 'success' and _dur(r) > 25 and not _special(r)]:
                cache.setdefault(r['id'], log(r['id']))
                priced = max([priced] + [int(x) for x in re.findall(r'линий с ценой 1xBet (\d+)', cache[r['id']])])
            if priced == 0:
                rows.append((f'теневой портфель ({sport}): новых сторон с ценой', '> 0', 'цен НХЛ за окно не было (прогоны за 3 ч до матчей ещё впереди)', '✅'))
                continue
        mk = new.market.value_counts().to_dict() if len(new) else {}
        if len(new):
            rows.append((f'теневой портфель ({sport}): новых сторон с ценой', '> 0', f'{len(new)} ({", ".join(f"{k} {v}" for k, v in mk.items())})', '✅'))
        else:
            rows.append((f'теневой портфель ({sport}): новых сторон с ценой', '> 0', 'матчей не было' if not seal else f'0 при снимке {seal} линий',
                         '✅' if not seal else '❌'))
    return rows


def rules_check() -> list[tuple[str, str, str, str]]:
    """21.11 (проверка «без лазеек»): ⭐ текущих суток по правилам — 🎯 не больше 3 (С23а), каждая ⭐ — свой матч, начала ⭐ не ближе 2 ч
    (С14; снятые не считаются), у каждой ⭐ есть цена (С19, С17), EV не выше 20% (правило Sonego). Источник — состояние ботов в репозитории."""
    from pathlib import Path
    import pandas as pd
    root = Path(__file__).resolve().parent.parent
    out = []
    for name, fn, key in (('теннис', 'data/runtime.json', 'day_pick'), ('футбол', 'football/data/runtime.json', 'day_pick'),
                          ('хоккей', 'data/runtime.json', 'hk_dp')):
        try:
            st = json.loads((root / fn).read_text(encoding='utf-8')).get(key) or {}
        except Exception as e:
            out.append((f'⭐ правила суток ({name})', 'по правилам', f'нет данных ({e.__class__.__name__})', '❌')); continue
        picks = list(st.get('picks') or []) if key == 'hk_dp' else ([st] if st.get('espn_id') else []) + list(st.get('extras') or [])
        live = [x for x in picks if not x.get('off')]
        if not live:
            out.append((f'⭐ правила суток ({name})', 'по правилам', f"{st.get('date', '—')}: ставок нет", '✅')); continue
        bad = []
        n_t = sum(1 for x in live if str(x.get('tier', '')).split('.')[0] != '0')
        if n_t > 3:
            bad.append(f'🎯 {n_t} > 3')
        ids = [str(x.get('espn_id') or x.get('id') or x.get('game_id') or x.get('match')) for x in live]   # хоккей: номер матча — поле id
        if len(set(ids)) < len(ids):
            bad.append('два ⭐ на один матч')
        for x in live:
            k = x.get('k') or (x.get('best') or {}).get('k')
            if not k:
                bad.append(f"нет цены: {x.get('label')}")
            ev = x.get('ev') if x.get('ev') not in (None, '') else (x.get('best') or {}).get('ev')
            if ev not in (None, '') and float(ev) > 0.20:
                bad.append(f"EV {float(ev) * 100:.0f}% > 20%: {x.get('label')}")
        out.append((f'⭐ правила суток ({name}): ≤ 3 🎯, свой матч, цена', 'по правилам',
                    f"{st.get('date', '—')}: ⭐ {len(live)} (🎯 {n_t})" + (' · ' + '; '.join(bad) if bad else ''), '❌' if bad else '✅'))
    return out


def halves_check() -> list[tuple[str, str, str, str]]:
    """К17 (21.11): ставки каперов на таймы получают итог — у сыгранных > 30 ч назад нет «висящих» без итога."""
    from pathlib import Path
    import pandas as pd
    root = Path(__file__).resolve().parent.parent
    try:
        d = pd.read_csv(root / 'football/data/tips/tips_all.csv', dtype=str, keep_default_na=False)
    except Exception as e:
        return [('каперы: ставки на таймы с итогом (К17)', 'все', f'нет данных ({e.__class__.__name__})', '❌')]
    h = d[d.legs.str.contains('@|WBH|HTFT|GBH', regex=True)]
    old = h[pd.to_datetime(h.start_utc, errors='coerce') < pd.Timestamp.now(tz='UTC').tz_localize(None) - pd.Timedelta(hours=30)]
    hang = old[old.settled == '']
    return [('каперы: ставки на таймы с итогом (К17)', 'все', f'на таймы {len(h)}, сыграны {len(old)}, без итога {len(hang)}',
             '✅' if not len(hang) else '❌')]


def ours_check(since: datetime) -> list[tuple[str, str, str, str]]:
    """20.11 (пользователь: «все матчи вне системы» — журнал «каперы × наши матчи» был пуст 5 суток незаметно):
    каждый прогноз капера на НАШ матч (матрица модели есть) до начала — в журнале tips.csv и с вердиктом 🔥/⚔️/·,
    кроме рынков вне модели (таймы, фолы, угловые). Пустой журнал при прогнозах на наши матчи = ❌ сразу."""
    import sys
    import pandas as pd
    from pathlib import Path
    fb = Path(__file__).resolve().parent.parent / 'football'
    if str(fb) not in sys.path:
        sys.path.insert(0, str(fb))
    try:
        import tipsbook as B
        import tips as T
        fx = B._dists()
        b = B.load()
        t = T.load()
    except Exception as e:
        return [('каперы × наши матчи: вердикт и журнал (К15)', 'файлы', f'нет: {e.__class__.__name__}', '❌')]
    if not fx:
        return [('каперы × наши матчи: матрицы модели (dists.json)', 'есть', 'нет файла', '❌')]
    edge = since.astimezone(timezone.utc).replace(tzinfo=None)
    d = b[((b.slip == '') | (b.seen == 'pre')) & (b.a != '') & (pd.to_datetime(b.post_ts, errors='coerce') >= edge)]
    same = {(u.split(':')[1], u.split(':')[2], u.split(':')[-1]) for u in t.uid if u.startswith('tg:') and u.count(':') >= 3}
    n = out = miss = 0
    lost = []
    for r in d.itertuples(index=False):
        hit = B.ours(r, fx)
        if hit is None:
            continue
        eid, e, legs = hit
        if pd.to_datetime(r.post_ts, errors='coerce') >= pd.Timestamp(e['start']):
            continue
        n += 1
        if not legs:
            out += 1
        elif (r.src, str(r.post_id), eid) not in same:
            miss += 1
            lost.append(f"{e['home']} {str(r.pick)[:20]}")
    return [('каперы × наши матчи: в журнале и с вердиктом (К15)', 'все',
             f'наших {n}: в журнале {n - out - miss}, рынок вне модели {out}, потеряно {miss}' + (f' ({"; ".join(lost[:3])})' if lost else ''),
             '✅' if not miss else '❌')]


def wide_check() -> list[tuple[str, str, str, str]]:
    """21.11 (CODEX 2а): «все матчи» (tips_all.csv — сверка каждого прогноза с табло ESPN) растёт, если в книге есть футбольные
    прогнозы каналов: журнал стоял с 13–15.11 незаметно (посты не передавались в прогон каперов)."""
    import pandas as pd
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / 'football' / 'data' / 'tips'
    try:
        b = pd.read_csv(root / 'book.csv', dtype=str, keep_default_na=False)
        w = pd.read_csv(root / 'tips_all.csv', dtype=str, keep_default_na=False)
    except Exception as e:
        return [('каперы: «все матчи» растёт', 'файлы', f'нет: {e.__class__.__name__}', '❌')]
    edge = (pd.Timestamp.now(tz='UTC').tz_localize(None) - pd.Timedelta(hours=72)).strftime('%Y-%m-%d %H:%M')
    nb = int(((b.sport == 'football') & ~b.pick.str.startswith('❔') & (b.post_ts >= edge)).sum())
    nw = int(((w.src != 'nb-bet') & (w.post_ts >= edge)).sum())
    return [('каперы: «все матчи» (tips_all) за 72 ч растёт', '≥ 15% книги', f'книга {nb} → все матчи {nw}',
             '✅' if nb < 5 or nw >= 0.15 * nb else '❌')]


def alerts_check() -> list[tuple[str, str, str, str]]:
    """23.11 аудит (AUDIT 0ж④, CODEX 2а): журнал присланных сообщений каперов с вердиктом (football/data/tips/alerts.csv, с 23.11).
    Каждый присланный прогноз — строка с причиной; «⬜ матч не найден» и «ставка не разобрана» — доля, разбор на аудите."""
    import pandas as pd
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / 'football' / 'data' / 'tips'
    if not (root / 'alerts.csv').exists():
        return [('каперы: журнал вердиктов (alerts.csv)', 'растёт', 'файла ещё нет (с 23.11)', '⏳')]
    a = pd.read_csv(root / 'alerts.csv', dtype=str, keep_default_na=False)
    edge = (pd.Timestamp.now(tz='UTC').tz_localize(None) - pd.Timedelta(hours=72)).strftime('%Y-%m-%d %H:%M')
    a = a[a.sent >= edge]
    if a.empty:
        return [('каперы: журнал вердиктов за 72 ч', 'растёт', 'строк 0', '⏳')]
    v = a.verdict.str.extract(r'^(\S+\s\S+)')[0].fillna('—').value_counts()
    empty = int((a.verdict.str.strip() == '').sum())
    return [('каперы: журнал вердиктов за 72 ч (у каждого — причина)', 'пусто 0', f"строк {len(a)}, без вердикта {empty}; "
             + ', '.join(f'{k} {n}' for k, n in v.head(6).items()), '✅' if not empty else '❌')]


def main(hours: int = 26) -> None:
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    rows = []
    for wf, per_day in (('daily.yml', 9), ('football.yml', 7), ('cappers.yml', 12)):   # 26.11: каперы 12 в сутки (15:22…02:22, Г0б), было 21 — ложный ❌
        rs = runs(wf, since)
        bad = [r for r in rs if r['conclusion'] not in ('success', None) and not _special(r)]   # 23.11 аудит: разовая проверка (research) — не сбой системы
        exp = int(per_day * hours / 24)
        need = int(exp * 0.7)                                  # 26.11: порог = показанному числу (15 при «≥ 15» было ❌)
        rows.append((f'{wf}: прогонов', f'≥ {need}', f'{len(rs)} (сбоев {len(bad)})', '✅' if not bad and len(rs) >= need else '❌'))
        for r in bad[:5]:
            rows.append(('  сбой', '', f"{(datetime.fromisoformat(r['created_at'].replace('Z', '+00:00')) + TZ):%d.%m %H:%M} {r['event']}", '❌'))
    # контрольные события: ищем в прогонах нужного часа (по местному времени начала)
    checks = [('daily.yml', (11, 14), r'ставка дня: объявлена|ставка дня: тихий режим', 'теннис: 🎾1 объявлена в 11:30 (или тихий режим)'),
              ('daily.yml', (11, 14), r'суточный отчёт отправлен|суточный отчёт: тихий режим', 'теннис: суточный отчёт (или тихий режим)'),
              ('football.yml', (11, 14), r'ставка дня: объявлена', 'футбол: ⚽1 объявлена в 11:30'),
              ('football.yml', (11, 14), r'суточный отчёт отправлен|отчёт отправлен', 'футбол: суточный отчёт'),
              ('daily.yml', (12, 15), r'🏒 суточный отчёт \d\d\.\d\d отправлен', 'хоккей: отчёт за сутки 12:00→12:00'),
              ('daily.yml', (12, 15), r'⭐🏒 .*⭐1|⭐🏒 \d', 'хоккей: 🏒1 объявлена'),
              ('daily.yml', (11, 14), r'🧠 tennis: правил', 'теннис: цикл самообучения'),
              ('daily.yml', (11, 14), r'📈 CLV tennis', 'теннис: цена закрытия (CLV)'),
              ('football.yml', (11, 14), r'📈 CLV football', 'футбол: цена закрытия (CLV)'),
              ('football.yml', (11, 14), r'🧠 football: правил', 'футбол: цикл самообучения'),
              ('daily.yml', (12, 15), r'🧠 hockey: правил', 'хоккей: цикл самообучения'),
              ('football.yml', (21, 24), r'🎬 итог', 'футбол: репетиция завтрашних суток (21:13, Р5)'),
              ('daily.yml', (0, 24), r'НЕ ДОСТАВЛЕНО|потеряно', 'теннис/хоккей: потерянные сообщения (последний прогон)'),
              ('football.yml', (0, 24), r'НЕ ДОСТАВЛЕНО|потеряно', 'футбол: потерянные сообщения (последний прогон)'),
              ('daily.yml', (0, 24), r'🔒 заглушка: [1-9]', 'теннис/хоккей: 🔒 контрольная точка (В3а, последний полный прогон)'),
              ('football.yml', (0, 24), r'🔒 заглушка: [1-9]', 'футбол: 🔒 контрольная точка (В3а, последний полный прогон)'),
              ('cappers.yml', (0, 24), r'🔒 контрольная точка: 2', 'каперы: 🔒 контрольная точка в оба бота (В3а, последний прогон)'),
              ('football.yml', (0, 24), r'👥 матрицы для вердиктов каперов: \d+', 'футбол: матрицы модели для вердиктов каперов (К15)'),
              ('cappers.yml', (0, 24), r'👥 каперы → наши матчи: на наши', 'каперы → наши матчи: журнал (К15, последний прогон)'),
              ('daily.yml', (0, 24), r"parse entities|can't parse", 'теннис/хоккей: сбои разметки скрытого текста (В3б)'),
              ('football.yml', (0, 24), r"parse entities|can't parse", 'футбол: сбои разметки скрытого текста (В3б)'),
              ('daily.yml', (11, 15), r'инциденты — сбой', 'теннис/хоккей: сбои журнала инцидентов ⭐ (Ц12)'),
              ('football.yml', (11, 15), r'инциденты — сбой', 'футбол: сбои журнала инцидентов ⭐ (Ц12)'),
              ('cappers.yml', (0, 24), r'вердикт без причины', 'каперы: сбои вердикта — сообщение без причины оценки (К16)'),
              ('daily.yml', (0, 24), r'⭐ не в снимке', 'теннис/хоккей: сбои сверки ⭐ ↔ снимок суток (ORG IMP-08)'),
              ('football.yml', (0, 24), r'⭐ не в снимке', 'футбол: сбои сверки ⭐ ↔ снимок суток (ORG IMP-08)'),
              ('daily.yml', (0, 24), r'Traceback|сбой', 'теннис/хоккей: сбои внутри прогонов (последний прогон)'),
              ('football.yml', (0, 24), r'Traceback|сбой', 'футбол: сбои внутри прогонов (последний прогон)')]
    cache: dict[int, str] = {}
    for wf, (h0, h1), pat, name in checks:
        rs = [r for r in runs(wf, since) if r['conclusion'] == 'success']
        if (h0, h1) != (0, 24):
            rs = [r for r in rs if h0 <= (datetime.fromisoformat(r['created_at'].replace('Z', '+00:00')) + TZ).hour < h1]
        else:                                                     # последний ПОЛНЫЙ прогон (шлюз Ц7 даёт пустые ≈ 5 с)
            full = [r for r in rs if wf == 'cappers.yml' or (_dur(r) > 25 and not _special(r))]
            rs = full[:1]
        hit = []
        for r in rs:
            cache.setdefault(r['id'], log(r['id']))
            hit += grep(cache[r['id']], pat)
        bad = 'сбо' in name or 'потерян' in name
        ok = (not hit) if bad else bool(hit)
        rows.append((name, 'нет' if bad else 'есть', (hit[0] if hit else '—') + (f' (+{len(hit) - 1})' if len(hit) > 1 else ''), '✅' if ok else '❌'))
    rows += cappers(since)
    rows += punctual(cache)
    rows += seal_today()
    rows += plan_check(since)
    rows += capper_check()
    rows += shadow_check(since, cache)
    rows += rules_check()
    rows += halves_check()
    rows += ours_check(since)
    rows += wide_check()
    rows += alerts_check()
    rows += minutes_check()
    rows += oddspapi_check()
    w = max(len(r[0]) for r in rows)
    print(f'Здоровье за {hours} ч (до {(now + TZ):%d.%m %H:%M} Ташкент)')
    for a, b, c, d in rows:
        print(f'{d} {a:<{w}} | {b:<8} | {c}')


if __name__ == '__main__':
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 26)
