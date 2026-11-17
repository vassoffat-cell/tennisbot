"""К5 / К14 (18.11.2026): проверка кандидатов — теннисные каналы каперов с открытым архивом (t.me/s/…).

Только чтение веб-ленты, без записи и отправок. Для каждого канала:
  посты за ~90 суток (до 12 страниц), доля теннисных, доля с кф, доля с отметкой итога, доля основного тура
  (ATP/WTA/Большой шлем против челленджеров/ITF), образцы постов.
Запуск: daily.yml, вход research = "engine/research/tennis_cap_probe.py [канал …]".
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'football'))
import tips as T  # noqa: E402

CANDS = ['imperatortennis', 'tennisrangeatp', 'BetonTenn1s', 'tennisnyykapper', 'xmtennis']
TEN = re.compile(r'🎾|теннис|\batp\b|\bwta\b|\bitf\b|челленджер|challenger|\bгейм|\bсет|тай-?брейк|большой шлем', re.I)
MAIN = re.compile(r'\batp\b(?!\s*challenger)|\bwta\b(?!\s*125)|итогов|finals|турин|ролан|уимблдон|us open|australian|мастерс|masters|\b(250|500|1000)\b', re.I)
LOW = re.compile(r'челленджер|challenger|\bitf\b|\bw\d{2,3}\b|\bm\d{2}\b|\buts\b|wta 125', re.I)
KF = re.compile(r'(?:кф|коэф\w*|@|odds)\s*[:=]?\s*\d+[.,]\d+|\b[1-9][.,]\d{2}\b')
MARK = re.compile(r'✅|❌|↩️|зашл|прош[её]л|мимо|возврат|\+\d+[.,]?\d*\s*(?:ед|u)\b', re.I)
PAIR = re.compile(r'[А-ЯЁA-Z][\w\.\-]+(?:\s[А-ЯЁA-Z][\w\.\-]+)?\s*[-–—]\s*[А-ЯЁA-Z][\w\.\-]+')


def probe(ch: str, pages: int = 12, days: int = 90) -> dict:
    edge = pd.Timestamp.now(tz='UTC').tz_localize(None) - pd.Timedelta(days=days)
    posts, before = [], None
    for _ in range(pages):
        try:
            got = T.tg_posts(ch, before)
        except Exception as e:                      # закрытый канал / нет веб-ленты
            return dict(ch=ch, err=str(e)[:80])
        if not got:
            break
        posts += got
        before = min(p['id'] for p in got)
        if min(p['ts'] for p in got) < edge:
            break
        time.sleep(1.0)
    posts = [p for p in posts if p['ts'] >= edge]
    if not posts:
        return dict(ch=ch, err='веб-лента пуста или закрыта')
    tx = [p['text'] for p in posts]
    ten = [t for t in tx if TEN.search(t) or PAIR.search(t)]
    return dict(ch=ch, n=len(posts), d0=str(min(p['ts'] for p in posts))[:10], d1=str(max(p['ts'] for p in posts))[:10],
                ten=len(ten), kf=sum(bool(KF.search(t)) for t in ten), mark=sum(bool(MARK.search(t)) for t in tx),
                main=sum(bool(MAIN.search(t)) for t in ten), low=sum(bool(LOW.search(t)) for t in ten),
                photo=sum(bool(p.get('photos')) and not p['text'].strip() for p in posts),
                per_day=round(len(ten) / max(1, (max(p['ts'] for p in posts) - min(p['ts'] for p in posts)).days), 2),
                sample=[re.sub(r'\s+', ' ', t)[:160] for t in ten[:3] + ten[-3:]])


def main() -> None:
    chs = sys.argv[1:] or CANDS
    for ch in chs:
        r = probe(ch)
        if 'err' in r:
            print(f'❌ {ch}: {r["err"]}')
            continue
        print(f"\n=== {ch}: постов {r['n']} ({r['d0']}…{r['d1']}), теннис {r['ten']} ({r['per_day']}/сут), с кф {r['kf']}, "
              f"отметки итога {r['mark']}, осн. тур {r['main']}, челл/ITF {r['low']}, фото без текста {r['photo']}")
        for s in r['sample']:
            print('   ·', s)


if __name__ == '__main__':
    main()
