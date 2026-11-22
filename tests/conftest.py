"""Pytest/shared test defaults for Montana Blotter."""
import os
import pathlib
import tempfile

# The sign-in wall is on by default in production, but tests need anonymous
# access to most pages. Leave it off unless the runner explicitly enabled it.
os.environ.setdefault('MB_REQUIRE_SIGNIN', 'false')

# Tests must never touch the production database. Default to a disposable
# session DB (explicitly-set paths, e.g. from CI or an individual test's
# monkeypatch, still win because these are setdefault calls).
_TEST_TMP = pathlib.Path(tempfile.mkdtemp(prefix='mb-pytest-'))
os.environ.setdefault('MB_TURSO_ENABLED', 'false')
os.environ.setdefault('MB_DB_PATH', str(_TEST_TMP / 'blotter.db'))
os.environ.setdefault('MB_PAGE_VIEWS_DB_PATH', str(_TEST_TMP / 'page_views.db'))
# Keep uploads inside the disposable test dir — the production default
# (/root/montanablotter/uploads) is not writable on dev checkouts.
os.environ.setdefault('MB_UPLOAD_DIR', str(_TEST_TMP / 'uploads'))

# Build the full schema before any test module imports app or raw sqlite3;
# init_database()/migrate() are idempotent so forked workers can repeat it.
import init_db  # noqa: E402

init_db.init_database()
init_db.migrate()


import pytest  # noqa: E402


@pytest.fixture(autouse=True, scope='class')
def _reset_schema_bootstrap_class():
    # Class-scoped companion: setUpClass builds fresh temp DBs too, and runs
    # before any function-scoped fixture.
    from core.schema_guard import reset_schema_bootstrap

    reset_schema_bootstrap()
    yield


@pytest.fixture(autouse=True)
def _reset_schema_bootstrap_between_tests():
    # ensure_*_schema helpers are memoized per-process by @schema_bootstrap_once
    # so request paths do not repeatedly take SQLite write locks. Tests build a
    # fresh temp DB per case, so the memoized state from conftest import (or a
    # previous test) would leave the new DB without tables. Clear it per test.
    from core.schema_guard import reset_schema_bootstrap

    reset_schema_bootstrap()
    yield
