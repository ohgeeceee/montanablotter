from __future__ import annotations

import sqlite3

import config


def configure_connection(
    conn: sqlite3.Connection,
    *,
    row_factory=sqlite3.Row,
    enable_wal: bool = False,
) -> sqlite3.Connection:
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute(f"PRAGMA busy_timeout = {int(getattr(config, 'DB_BUSY_TIMEOUT_MS', 30000))}")
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
) -> sqlite3.Connection:
    timeout_seconds = float(getattr(config, 'DB_TIMEOUT_SECONDS', 30))
    conn = sqlite3.connect(config.DB_PATH, timeout=timeout_seconds)
    return configure_connection(conn, row_factory=row_factory, enable_wal=enable_wal)


def get_db() -> sqlite3.Connection:
    return connect_db()
