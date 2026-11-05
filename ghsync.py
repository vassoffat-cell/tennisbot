"""Отправка файлов tennis-data с компьютера в ваш репозиторий GitHub, чтобы облако переобучалось на свежих данных.

Нужны в .env: GITHUB_TOKEN (fine-grained token с правом Contents: Read and write только на репозиторий tennisbot)
и GITHUB_REPO (например vassoffat-cell/tennisbot). Работает через REST API GitHub, git не нужен.
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from pathlib import Path

import config as C

API = 'https://api.github.com/repos/{repo}/contents/{path}'


def enabled() -> bool:
    return bool(C.GITHUB_TOKEN and C.GITHUB_REPO)


def _req(method: str, url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        'Authorization': f'Bearer {C.GITHUB_TOKEN}', 'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'tennisbot'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode() or '{}')


def push(local: Path, repo_path: str, message: str) -> str:
    """Создать или заменить файл в репозитории. Одинаковый файл повторно не отправляется."""
    url = API.format(repo=C.GITHUB_REPO, path=repo_path)
    content = local.read_bytes()
    sha = None
    try:
        cur = _req('GET', url + '?ref=main')
        sha = cur.get('sha')
        if cur.get('content') and base64.b64decode(cur['content']) == content:
            return f'{repo_path}: в GitHub уже актуален'
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    body = {'message': message, 'content': base64.b64encode(content).decode(), 'branch': 'main'}
    if sha:
        body['sha'] = sha
    _req('PUT', url, body)
    return f'{repo_path}: отправлен в GitHub'


def push_tennis_data() -> list[str]:
    if not enabled():
        return ['GitHub не настроен (GITHUB_TOKEN и GITHUB_REPO в .env) — файл обновлён только на компьютере']
    out = []
    for y in (C.LAST_SEASON, C.LAST_SEASON - 1):
        for f in sorted(C.TENNIS_DATA.glob(f'{y}_*.xlsx')):
            try:
                out.append(push(f, f'data/tennis-data/{f.name}', f'tennis-data {f.name} с компьютера'))
            except Exception as e:
                out.append(f'{f.name}: не отправлен в GitHub ({e})')
    return out
