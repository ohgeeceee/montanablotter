from __future__ import annotations

import sqlite3

import config


def configure_connection(
    conn: sqlite3.Connection,
    *,
    row_factory=sqlite3.Row,
    enable_wal: bool = False,
    busy_timeout_ms: int | None = None,
) -> sqlite3.Connection:
    conn.execute('PRAGMA foreign_keys = ON')
    timeout_ms = int(
        getattr(config, 'DB_BUSY_TIMEOUT_MS', 30000)
        if busy_timeout_ms is None
        else busy_timeout_ms
    )
    conn.execute(f'PRAGMA busy_timeout = {max(0, timeout_ms)}')
    if enable_wal:
        conn.execute('PRAGMA journal_mode = WAL')
        conn.execute('PRAGMA synchronous = NORMAL')
    if row_factory is not None:
        conn.row_factory = row_factory
    return conn


def connect_db(
    *,
    row_factory=sqlite3.Row,
    enable_wal: bool = False,
    timeout_seconds: float | None = None,
    busy_timeout_ms: int | None = None,
) -> sqlite3.Connection:
    if timeout_seconds is None:
        timeout_seconds = float(getattr(config, 'DB_TIMEOUT_SECONDS', 30))
    conn = sqlite3.connect(config.DB_PATH, timeout=float(timeout_seconds))
    return configure_connection(
        conn,
        row_factory=row_factory,
        enable_wal=enable_wal,
        busy_timeout_ms=busy_timeout_ms,
    )


def get_db() -> sqlite3.Connection:
    return connect_db()
