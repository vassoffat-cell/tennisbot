"""Состояние между запусками облака: id отправленного списка в Telegram, даты отчётов и переобучения.
Хранится в data/runtime.json и сохраняется в репозиторий вместе с данными."""
from __future__ import annotations

import json

import config as C

PATH = C.DATA / 'runtime.json'
DEFAULT = {'list_messages': [], 'list_match_ids': [], 'last_daily': '', 'last_retrain': '',
           'last_files': '2026-11-05', 'last_reminder': ''}   # файлы tennis-data последний раз пришли 05.11.2026


def load() -> dict:
    try:
        d = json.loads(PATH.read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        d = {}
    return {**DEFAULT, **d}


def save(d: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding='utf-8')
