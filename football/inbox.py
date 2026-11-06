"""Входящие файлы football-data: бот (Google Apps Script) кладёт присланные в Telegram файлы в папку Google Диска
«footbot_inbox», открытую сервисному аккаунту. Облако при каждом запуске забирает новые файлы из папки
и помечает их префиксом processed_ (повторно не берутся)."""
from __future__ import annotations

from pathlib import Path

import config as C

FOLDER = C.INBOX_FOLDER
API = 'https://www.googleapis.com/drive/v3/files'


def _session():
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_file(C.GOOGLE_CREDENTIALS, scopes=['https://www.googleapis.com/auth/drive'])
    return AuthorizedSession(creds)


def fetch() -> tuple[list[tuple[str, str, bytes]], str]:
    """Новые файлы: [(id, имя, байты)] и примечание (пусто, если всё в порядке)."""
    if not Path(C.GOOGLE_CREDENTIALS).exists():
        return [], ''
    s = _session()
    q = f"name='{FOLDER}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    r = s.get(API, params={'q': q, 'fields': 'files(id,name)', 'supportsAllDrives': 'true',
                           'includeItemsFromAllDrives': 'true'})
    r.raise_for_status()
    folders = r.json().get('files', [])
    if not folders:
        return [], f'папка {FOLDER} не найдена или не открыта сервисному аккаунту'
    out = []
    for f in folders:
        q = f"'{f['id']}' in parents and trashed=false and not name contains 'processed_'"
        r = s.get(API, params={'q': q, 'fields': 'files(id,name)', 'supportsAllDrives': 'true',
                               'includeItemsFromAllDrives': 'true'})
        r.raise_for_status()
        for x in r.json().get('files', []):
            if not x['name'].lower().endswith('.xlsx'):
                continue
            d = s.get(f"{API}/{x['id']}", params={'alt': 'media', 'supportsAllDrives': 'true'})
            d.raise_for_status()
            out.append((x['id'], x['name'], d.content))
    return out, ''


def mark_done(file_id: str, name: str) -> None:
    s = _session()
    s.patch(f'{API}/{file_id}', params={'supportsAllDrives': 'true'}, json={'name': f'processed_{name}'}).raise_for_status()
