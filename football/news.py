"""Сбор новостей ESPN по турнирам (раз в сутки) — для будущей проверки факторов «смена тренера» (F4) и «травмы».
В прогноз пока НЕ входит: копим с 08.11.2026, проверка через 2–3 месяца (как составы). Данные: data/news.csv.gz."""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

import config as C
import fespn

URL = 'https://site.api.espn.com/apis/site/v2/sports/soccer/{comp}/news?limit=50'
COACH = re.compile(r'\b(sack\w*|fired|dismiss\w*|appoint\w*|new (manager|coach|head coach)|interim|caretaker|'
                   r'parts? ways|steps? down|resign\w*)\b', re.I)
INJURY = re.compile(r'\b(injur\w*|hamstring|knee|ankle|groin|calf|thigh|muscle|surgery|ruled out|sidelined|out for|'
                    r'fitness|illness|concussion|ACL|fracture\w*|strain\w*|suspend\w*|ban(ned)?)\b', re.I)


def _one(comp: str) -> list[dict]:
    try:
        js = fespn._get(URL.format(comp=comp))
    except Exception:
        return []
    out = []
    for a in js.get('articles', []) or []:
        t = f"{a.get('headline', '')} {a.get('description', '')}"
        teams = [c.get('description', '') for c in a.get('categories', []) or [] if c.get('type') == 'team']
        out.append(dict(id=str(a.get('id', '')), comp=comp, published=str(a.get('published', ''))[:16],
                        headline=a.get('headline', ''), description=a.get('description', ''), teams='; '.join(teams),
                        coach=int(bool(COACH.search(t))), injury=int(bool(INJURY.search(t)))))
    return out


def collect() -> str:
    rows = []
    with ThreadPoolExecutor(8) as ex:
        for r in ex.map(_one, list(C.COMPETITIONS)):
            rows += r
    if not rows:
        return 'новости: нет ответа'
    new = pd.DataFrame(rows)
    new = new[new.id != '']
    old = pd.read_csv(C.NEWS, dtype={'id': str}) if C.NEWS.exists() else pd.DataFrame(columns=new.columns)
    add = new[~new.id.isin(set(old.id.astype(str)))].drop_duplicates('id')
    if len(add):
        pd.concat([old, add], ignore_index=True).to_csv(C.NEWS, index=False, compression='gzip')
    return f'новости: +{len(add)} (смена тренера {int(add.coach.sum()) if len(add) else 0}, травмы {int(add.injury.sum()) if len(add) else 0})'
