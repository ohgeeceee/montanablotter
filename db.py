from __future__ import annotations

import os
import sqlite3

import config

# ---------------------------------------------------------------------------
# Turso / libsql support
# ---------------------------------------------------------------------------
# When TURSO_DATABASE_URL and TURSO_AUTH_TOKEN are set the main database
# connection uses libsql with an embedded replica (local file for fast reads,
# Turso cloud for durability & remote access).
#
# The large ``page_views`` table is kept in a **separate** local-only SQLite
# file (``page_views.db`` next to the main database) so it doesn't count
# against the Turso storage quota.
# ---------------------------------------------------------------------------

def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, '').strip().lower()
    if not raw:
        return default
    return raw in {'1', 'true', 'yes', 'on'}


_TURSO_ENABLED = _env_bool('MB_TURSO_ENABLED', default=True)
_TURSO_URL = os.getenv('TURSO_DATABASE_URL', '').strip()
_TURSO_TOKEN = os.getenv('TURSO_AUTH_TOKEN', '').strip()
_USE_TURSO = _TURSO_ENABLED and bool(_TURSO_URL and _TURSO_TOKEN)

if _USE_TURSO:
    try:
        import libsql_experimental as _libsql  # type: ignore[import-untyped]
    except ImportError:
        _USE_TURSO = False

# Path for the embedded replica (local cache of the Turso cloud DB).
_REPLICA_PATH = os.getenv(
    'TURSO_REPLICA_PATH',
    os.path.join(os.path.dirname(config.DB_PATH), 'blotter_replica.db'),
)

# Separate local-only database for the heavyweight page_views table.
_PAGE_VIEWS_DB_PATH = os.getenv(
    'MB_PAGE_VIEWS_DB_PATH',
    os.path.join(os.path.dirname(config.DB_PATH), 'page_views.db'),
)


def _is_turso_forbidden_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        '403' in message
        or 'forbidden' in message
        or 'reads are blocked' in message
    )


# ---------------------------------------------------------------------------
# Compatibility wrapper
# ---------------------------------------------------------------------------
# libsql_experimental connections don't support ``row_factory``.  We wrap
# the connection so that every query transparently returns ``sqlite3.Row``‑
# style objects (supporting both ``row['col']`` and ``row[0]`` access).

class _DictRow:
    """Minimal sqlite3.Row work-alike: supports column-name & index access."""

    __slots__ = ('_keys', '_values')

    def __init__(self, keys: list[str], values: tuple):
        object.__setattr__(self, '_keys', keys)
        object.__setattr__(self, '_values', values)

    # --- dict-like access ---------------------------------------------------
    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        try:
            idx = self._keys.index(key)
        except ValueError:
            raise KeyError(key)
        return self._values[idx]

    def __contains__(self, key):
        return key in self._keys

    def get(self, key, default=None):
        try:
            return self[key]
        except (KeyError, IndexError):
            return default

    def keys(self):
        return list(self._keys)

    def values(self):
        return list(self._values)

    def items(self):
        return list(zip(self._keys, self._values))

    def __iter__(self):
        return iter(self._keys)

    def __len__(self):
        return len(self._values)

    def __repr__(self):
        return f'<DictRow {dict(zip(self._keys, self._values))}>'


class _WrappedCursor:
    """Thin cursor wrapper that converts tuples to ``_DictRow`` objects."""

    def __init__(self, real_cursor):
        self._cur = real_cursor

    @property
    def description(self):
        return self._cur.description

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    @property
    def rowcount(self):
        return self._cur.rowcount

    def _wrap(self, row):
        if row is None:
            return None
        desc = self._cur.description
        if desc:
            keys = [col[0] for col in desc]
            return _DictRow(keys, tuple(row))
        return row

    def fetchone(self):
        return self._wrap(self._cur.fetchone())

    def fetchall(self):
        rows = self._cur.fetchall()
        desc = self._cur.description
        if not desc:
            return rows
        keys = [col[0] for col in desc]
        return [_DictRow(keys, tuple(r)) for r in rows]

    def fetchmany(self, size=None):
        rows = self._cur.fetchmany(size) if size else self._cur.fetchmany()
        desc = self._cur.description
        if not desc:
            return rows
        keys = [col[0] for col in desc]
        return [_DictRow(keys, tuple(r)) for r in rows]

    def __iter__(self):
        return self

    def __next__(self):
        row = self._cur.fetchone()
        if row is None:
            raise StopIteration
        return self._wrap(row)

    def close(self):
        if hasattr(self._cur, 'close'):
            self._cur.close()


class _LibsqlConnectionWrapper:
    """Wraps a libsql connection to emulate ``row_factory = sqlite3.Row``."""

    def __init__(self, conn):
        self._conn = conn

    # --- Proxy common attributes -------------------------------------------
    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()

    def sync(self):
        return self._conn.sync()

    @property
    def in_transaction(self):
        return self._conn.in_transaction

    # --- Execute wrappers ---------------------------------------------------
    def execute(self, sql, parameters=()):
        # libsql requires tuple parameters, not lists.
        if isinstance(parameters, list):
            parameters = tuple(parameters)
        cur = self._conn.execute(sql, parameters)
        return _WrappedCursor(cur)

    def executemany(self, sql, seq_of_params):
        cur = self._conn.executemany(sql, [tuple(p) if isinstance(p, list) else p for p in seq_of_params])
        return _WrappedCursor(cur)

    def executescript(self, sql):
        return self._conn.executescript(sql)

    def cursor(self):
        return _WrappedCursor(self._conn.cursor())

    # Pass through row_factory setter (no-op for compatibility)
    @property
    def row_factory(self):
        return None

    @row_factory.setter
    def row_factory(self, value):
        pass  # handled by our wrapper


# ---------------------------------------------------------------------------
# Connection factories
# ---------------------------------------------------------------------------

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
    """Return a connection to the main application database.

    When Turso is configured this uses an embedded replica (local file backed
    by the Turso cloud database).  Otherwise it falls back to a plain local
    SQLite connection — identical to the original behaviour.
    """
    global _USE_TURSO

    if _USE_TURSO:
        try:
            # Use a per-PID replica path to avoid WAL contention between
            # gunicorn/uwsgi workers sharing the same filesystem.
            pid = os.getpid()
            replica = f'{_REPLICA_PATH}.{pid}'
            raw_conn = _libsql.connect(
                database=replica,
                sync_url=_TURSO_URL,
                auth_token=_TURSO_TOKEN,
            )
            # Perform an initial sync so the local replica is up-to-date.
            raw_conn.sync()
            # Return a wrapper that provides sqlite3.Row-compatible dict access.
            return _LibsqlConnectionWrapper(raw_conn)
        except Exception as exc:
            if _is_turso_forbidden_error(exc):
                # The configured Turso database/token does not allow replica
                # export reads. Disable Turso for the rest of the process so
                # every request does not retry the same forbidden sync.
                _USE_TURSO = False
            # If Turso fails (network, WAL contention, etc.) fall back to the
            # local SQLite file so the site stays up.
            pass

    # Fallback: plain local SQLite (original behaviour).
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


# ---------------------------------------------------------------------------
# page_views helpers — always local SQLite
# ---------------------------------------------------------------------------

def connect_page_views(
    *,
    row_factory=sqlite3.Row,
    enable_wal: bool = False,
) -> sqlite3.Connection:
    """Return a connection to the local page_views database."""
    conn = sqlite3.connect(_PAGE_VIEWS_DB_PATH, timeout=30)
    conn.execute('PRAGMA busy_timeout = 30000')
    if row_factory is not None:
        conn.row_factory = row_factory

    # Auto-create the table if it doesn't exist yet (first run only).
    conn.execute('''
        CREATE TABLE IF NOT EXISTS page_views (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL,
            ip_hash TEXT,
            referrer TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    conn.commit()
    return conn
