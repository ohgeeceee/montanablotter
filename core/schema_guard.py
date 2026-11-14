"""Per-process memoization for schema bootstrap functions.

Several ``ensure_*_schema(conn)`` helpers run DDL (CREATE TABLE / CREATE INDEX /
ALTER TABLE) and then commit. When they are called from request paths -- the
homepage sponsor block, /subscribe, /newsletter, the lawyer/bail ad pages --
every request took a SQLite write lock. Under cron write load that produced
``sqlite3.OperationalError: database is locked`` (HTTP 500) and, worse, a
gunicorn worker wedged forever inside the DDL while holding the WAL write lock,
which blocked every writer on the site.

Decorate those helpers with ``@schema_bootstrap_once`` so each process runs the
bootstrap a single time. Deploys restart the process, so newly added columns
from a fresh deploy are still applied.
"""

from __future__ import annotations

import functools
import threading

_DONE: dict[str, bool] = {}
_LOCKS: dict[str, threading.Lock] = {}
_META_LOCK = threading.Lock()


def schema_bootstrap_once(func):
    """Run ``func`` at most once per process.

    Safe for ``func`` signatures of the form ``func(conn, ...)``. The first
    caller performs the bootstrap under a per-function lock; concurrent callers
    wait and then skip. If the bootstrap raises, the flag stays unset so a later
    request retries instead of silently running with a half-built schema.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        key = "%s.%s" % (func.__module__, func.__qualname__)
        with _META_LOCK:
            lock = _LOCKS.setdefault(key, threading.Lock())
        with lock:
            if _DONE.get(key):
                return
            func(*args, **kwargs)
            _DONE[key] = True

    wrapper._schema_bootstrap_key = "%s.%s" % (func.__module__, func.__qualname__)
    return wrapper


def reset_schema_bootstrap(key: str | None = None) -> None:
    """Clear memoized bootstrap state (tests, or an explicit re-migration)."""
    with _META_LOCK:
        if key is None:
            _DONE.clear()
        else:
            _DONE.pop(key, None)
