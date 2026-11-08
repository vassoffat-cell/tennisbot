"""Сбор новостей ESPN WTA/ATP (раз в сутки) — для будущей проверки фактора «травмы и снятия» (T22).
В прогноз пока НЕ входит: копим с 08.11.2026, проверка через 2–3 месяца. Данные: data/news.csv.gz."""
from __future__ import annotations

import re

import pandas as pd

import config as C
import espn

URL = 'https://site.api.espn.com/apis/site/v2/sports/tennis/{tour}/news?limit=50'
INJURY = re.compile(r'\b(injur\w*|withdr\w*|retire[sd]?|retirement|pull(s|ed)? out|illness|ill\b|surgery|hamstring|knee|'
                    r'ankle|wrist|shoulder|back (pain|injury)|abdominal|blister|cramp\w*|ruled out|sidelined|walkover|'
                    r'medical timeout|strain\w*|tear)\b', re.I)


def collect() -> str:
    rows = []
    for tour in ('wta', 'atp'):
        try:
            js = espn._get(URL.format(tour=tour))
        except Exception:
            continue
        for a in js.get('articles', []) or []:
            t = f"{a.get('headline', '')} {a.get('description', '')}"
            ath = [c.get('description', '') for c in a.get('categories', []) or [] if c.get('type') == 'athlete']
            rows.append(dict(id=str(a.get('id', '')), tour=tour.upper(), published=str(a.get('published', ''))[:16],
                             headline=a.get('headline', ''), description=a.get('description', ''), players='; '.join(ath),
                             injury=int(bool(INJURY.search(t)))))
    if not rows:
        return 'новости: нет ответа'
    new = pd.DataFrame(rows)
    new = new[new.id != '']
    old = pd.read_csv(C.NEWS, dtype={'id': str}) if C.NEWS.exists() else pd.DataFrame(columns=new.columns)
    add = new[~new.id.isin(set(old.id.astype(str)))].drop_duplicates('id')
    if len(add):
        pd.concat([old, add], ignore_index=True).to_csv(C.NEWS, index=False, compression='gzip')
    return f'новости: +{len(add)} (травмы/снятия {int(add.injury.sum()) if len(add) else 0})'
