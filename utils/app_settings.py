from __future__ import annotations

import sqlite3

from db import get_db


def _app_setting_raw(key: str, default=None, conn=None):
    own_conn = conn is None
    if own_conn:
        conn = get_db()
    try:
        row = conn.execute(
            'SELECT value FROM app_settings WHERE key = ?',
            (key,),
        ).fetchone()
        if not row or row['value'] is None or row['value'] == '':
            return default
        return row['value']
    except sqlite3.Error:
        return default
    finally:
        if own_conn:
            conn.close()


def _app_setting_bool(key: str, default: bool = False, conn=None) -> bool:
    raw = _app_setting_raw(key, default=None, conn=conn)
    if raw is None:
        return bool(default)
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}


def _app_setting_int(key: str, default: int, minimum: int | None = None, maximum: int | None = None, conn=None) -> int:
    raw = _app_setting_raw(key, default=None, conn=conn)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = int(default)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _app_setting_text(key: str, default: str = '', max_length: int | None = None, conn=None) -> str:
    raw = _app_setting_raw(key, default=None, conn=conn)
    value = str(default if raw is None else raw).strip()
    if max_length is not None:
        value = value[:max_length]
    return value


def _save_app_setting(conn, key: str, value) -> None:
    if isinstance(value, bool):
        stored_value = '1' if value else '0'
    else:
        stored_value = str(value).strip()
    conn.execute(
        '''
        INSERT INTO app_settings (key, value, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        ''',
        (key, stored_value),
    )
