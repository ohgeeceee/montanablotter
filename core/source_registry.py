from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional

import config


DB_TIMEOUT_SECONDS = float(getattr(config, 'DB_TIMEOUT_SECONDS', 30))
DB_BUSY_TIMEOUT_MS = int(getattr(config, 'DB_BUSY_TIMEOUT_MS', 30000))


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=DB_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute(f'PRAGMA journal_mode = WAL')
    conn.execute(f'PRAGMA busy_timeout = {DB_BUSY_TIMEOUT_MS}')
    conn.execute('PRAGMA synchronous = NORMAL')
    return conn


def ensure_source_registry(
    *,
    source_key: str,
    source_type: str,
    display_name: str,
    base_url: Optional[str] = None,
    adapter_name: Optional[str] = None,
    poll_interval_seconds: int = 900,
    is_enabled: bool = True,
    notes: str = '',
) -> int:
    conn = _connect()
    try:
        row = conn.execute(
            'SELECT id FROM source_registry WHERE source_key = ?',
            (source_key,),
        ).fetchone()
        if row:
            conn.execute(
                '''
                UPDATE source_registry
                SET source_type = ?,
                    display_name = ?,
                    base_url = ?,
                    adapter_name = ?,
                    poll_interval_seconds = ?,
                    is_enabled = ?,
                    notes = ?,
                    updated_at = datetime('now')
                WHERE id = ?
                ''',
                (
                    source_type,
                    display_name,
                    base_url,
                    adapter_name,
                    poll_interval_seconds,
                    1 if is_enabled else 0,
                    notes,
                    int(row['id']),
                ),
            )
            conn.commit()
            return int(row['id'])

        cursor = conn.execute(
            '''
            INSERT INTO source_registry (
                source_key, source_type, display_name, base_url, adapter_name,
                poll_interval_seconds, is_enabled, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                source_key,
                source_type,
                display_name,
                base_url,
                adapter_name,
                poll_interval_seconds,
                1 if is_enabled else 0,
                notes,
            ),
        )
        if cursor.lastrowid is None:
            raise RuntimeError('Failed to create source_registry row')
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def get_source_registry(source_key: str) -> Optional[dict[str, Any]]:
    conn = _connect()
    try:
        row = conn.execute(
            'SELECT * FROM source_registry WHERE source_key = ?',
            (source_key,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def attach_source_artifact(
    *,
    source_registry_id: int,
    source_document_id: Optional[int] = None,
    artifact_kind: str = 'raw',
    source_url: Optional[str] = None,
    storage_path: Optional[str] = None,
    content_sha256: Optional[str] = None,
    fetched_at: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> int:
    conn = _connect()
    try:
        if content_sha256:
            row = conn.execute(
                '''
                SELECT id
                FROM source_artifacts
                WHERE source_registry_id = ?
                  AND artifact_kind = ?
                  AND content_sha256 = ?
                ''',
                (source_registry_id, artifact_kind, content_sha256),
            ).fetchone()
            if row:
                conn.execute(
                    '''
                    UPDATE source_artifacts
                    SET source_document_id = COALESCE(?, source_document_id),
                        source_url = COALESCE(?, source_url),
                        storage_path = COALESCE(?, storage_path),
                        fetched_at = COALESCE(?, fetched_at),
                        metadata_json = COALESCE(?, metadata_json),
                        updated_at = datetime('now')
                    WHERE id = ?
                    ''',
                    (
                        source_document_id,
                        source_url,
                        storage_path,
                        fetched_at,
                        json.dumps(metadata or {}),
                        int(row['id']),
                    ),
                )
                conn.commit()
                return int(row['id'])

        cursor = conn.execute(
            '''
            INSERT INTO source_artifacts (
                source_registry_id, source_document_id, artifact_kind,
                source_url, storage_path, content_sha256, fetched_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')), ?)
            ''',
            (
                source_registry_id,
                source_document_id,
                artifact_kind,
                source_url,
                storage_path,
                content_sha256,
                fetched_at,
                json.dumps(metadata or {}),
            ),
        )
        if cursor.lastrowid is None:
            raise RuntimeError('Failed to create source_artifacts row')
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()
