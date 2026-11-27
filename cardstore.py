"""🖼 Картинки ВСЕХ отчётов меню «Аналитика / История» (решение пользователя 27.11 14:35 «давай», В9).

Отчёт, ушедший в чат картинкой, меню показывает по file_id (archive.img). Остальные (месяц, год, прошлые дни, История) — по картинке
из ветки репозитория: cards-fb (футбольный бот) и cards-tn (теннис + хоккей). В ветке — только текущие версии: каждая публикация —
один коммит без истории (force push), поэтому репозиторий не растёт. index.json: {ключ архива: число картинок}.

publish(items, df) — вызывается из archive.put: перерисовать изменённые ключи + дорисовать до BACKFILL недостающих (старые дни).
Только в GitHub Actions (локально — ничего). Любой сбой — строка в лог, отчёты и меню работают по-прежнему (текстом).
Apps Script читает картинку: GET api.github.com/repos/<repo>/contents/<файл>?ref=<ветка> (GH_TOKEN, Accept: raw) → sendPhoto.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BRANCH = {'fb': 'cards-fb', 'tn': 'cards-tn', 'hk': 'cards-tn'}
BACKFILL = 30                                  # недостающих ключей за прогон (≈ 0,4 с каждый)


def fname(key: str, i: int) -> str:
    return re.sub(r'[^0-9A-Za-z\-]', '_', key) + f'_{i}.png'


def _git(args: list[str], env: dict, inp: bytes | None = None) -> bytes:
    return subprocess.run(['git', *args], cwd=ROOT, env=env, input=inp, capture_output=True, check=True, timeout=120).stdout


def publish(items: list[tuple[str, list[str]]], df=None, branches: list[str] | None = None) -> str:
    """items — [(ключ, блоки)] изменённых отчётов; df — лист archive (для дорисовки недостающих). Возвращает строку для лога."""
    if not os.getenv('GITHUB_ACTIONS'):
        return ''
    try:
        import cards
    except Exception as ex:
        return f'🖼 картинки меню: нет cards ({ex.__class__.__name__})'
    out = []
    for br in sorted({BRANCH.get(k.split('|')[0], 'cards-tn') for k, _ in items} | set(branches or [])):
        try:
            out.append(_publish_branch(br, [(k, b) for k, b in items if BRANCH.get(k.split('|')[0], 'cards-tn') == br], df, cards))
        except Exception as ex:
            out.append(f'🖼 картинки меню {br}: сбой {ex.__class__.__name__}: {str(ex)[:120]}')
    return '; '.join(out)


def _publish_branch(br: str, items: list, df, cards) -> str:
    tmp = tempfile.mkdtemp()
    env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / 'index'), GIT_AUTHOR_NAME='cards-bot', GIT_COMMITTER_NAME='cards-bot',
               GIT_AUTHOR_EMAIL='cards-bot@users.noreply.github.com', GIT_COMMITTER_EMAIL='cards-bot@users.noreply.github.com')
    idx: dict[str, int] = {}
    try:                                                       # текущая ветка → во временный индекс
        _git(['fetch', '--depth', '1', 'origin', f'refs/heads/{br}'], env)
        _git(['read-tree', 'FETCH_HEAD'], env)
        idx = json.loads(_git(['show', 'FETCH_HEAD:index.json'], env).decode() or '{}')
    except subprocess.CalledProcessError:
        _git(['read-tree', '--empty'], env)                    # ветки ещё нет
    sp = {'cards-fb': ('fb',), 'cards-tn': ('tn', 'hk')}[br]
    todo = list(items)
    if df is not None and len(df):                             # дорисовать старые ключи без картинок (не больше BACKFILL за прогон)
        have = {k for k, _ in todo} | set(idx)
        miss = []
        for k, t in zip(df.key.astype(str), df.text.astype(str)):
            if k.split('|')[0] in sp and k not in have:
                try:
                    miss.append((k, json.loads(t)))
                except Exception:
                    pass
        todo += miss[-BACKFILL:]                               # сначала свежие (лист растёт вниз)
    n_ok = 0
    for key, blocks in todo:
        try:
            pngs = cards.render([b for b in blocks if b])[0]
        except Exception:
            pngs = []
        for i in range(1, int(idx.get(key, 0)) + 1):            # старые картинки ключа — убрать
            subprocess.run(['git', 'update-index', '--force-remove', fname(key, i)], cwd=ROOT, env=env, capture_output=True)
        if not pngs:
            idx.pop(key, None)
            continue
        for i, png in enumerate(pngs, 1):
            blob = _git(['hash-object', '-w', '--stdin'], env, png).decode().strip()
            _git(['update-index', '--add', '--cacheinfo', f'100644,{blob},{fname(key, i)}'], env)
        idx[key] = len(pngs)
        n_ok += 1
    blob = _git(['hash-object', '-w', '--stdin'], env, json.dumps(idx, ensure_ascii=False, sort_keys=True).encode()).decode().strip()
    _git(['update-index', '--add', '--cacheinfo', f'100644,{blob},index.json'], env)
    tree = _git(['write-tree'], env).decode().strip()
    commit = _git(['commit-tree', tree, '-m', f'картинки меню: {n_ok} отчётов'], env).decode().strip()
    _git(['push', '--force', 'origin', f'{commit}:refs/heads/{br}'], env)
    return f'🖼 картинки меню {br}: обновлено {n_ok}, всего {len(idx)}'
