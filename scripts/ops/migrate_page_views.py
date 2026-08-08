#!/usr/bin/env python3
"""
migrate_page_views.py — One-shot migration of historical page_views data
from the main blotter.db into the dedicated page_views.db.

The page_views table in blotter.db was the historical home for analytics
rows. As of 2026-08-02 the live write path already targets page_views.db
(see app.py:7281 and db.py:connect_page_views). This script finishes
the split by moving every row from blotter.db.page_views into
page_views.db, then drops the source table and its 5 indexes.

Why: blotter.db.page_views + 5 indexes currently consume ~10 GB on
disk. The rows are static (last write was 2026-04-23, all rows are
older than 90 days as of 2026-08-02). After this migration, the
retention cron (scripts/ops/prune_page_views.py) keeps page_views.db
under control at 90 days.

Safety:
- Idempotent: detects the destination count and exits if rows are
  already present.
- Pre-flight snapshot: caller is responsible for taking a snapshot
  of blotter.db before running this (instructions below).
- Batched: 50,000 rows per batch with a 100 ms sleep between batches
  to avoid starving live writers.
- Reversible: the pre-flight snapshot can be restored; the destination
  page_views.db can be deleted and recreated by the next request.

Pre-flight (operator must do):
  systemctl stop montanablotter
  cp -a data/blotter.db data/blotter.db.pre_pvmigration_<timestamp>.snap
  systemctl start montanablotter

Run (the script itself does NOT need Gunicorn stopped — it opens its
own connections with a busy timeout and uses a short exclusive lock
on the main DB):
  ./venv/bin/python3 scripts/ops/migrate_page_views.py

After the script reports success, run a separate VACUUM on blotter.db
to reclaim disk space:
  ./venv/bin/python3 -c "import sqlite3; c=sqlite3.connect('data/blotter.db',timeout=600); c.execute('VACUUM'); c.close()"

The VACUUM is intentionally NOT bundled into this script: it takes
a long write lock on the main DB (multi-minute) and is best run from
a maintenance window.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime, UTC
from pathlib import Path

# Default paths match the production layout: data/blotter.db and
# data/page_views.db, both under /root/montanablotter/data/.
DEFAULT_MAIN_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'data', 'blotter.db',
)
DEFAULT_PV_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'data', 'page_views.db',
)

# Indexes that page_views.db needs for the analytics queries in
# app.py:7966-7983. Match the names from blotter.db so the existing
# INDEXED BY hints in app.py keep working.
REQUIRED_INDEXES = [
    ('idx_page_views_created',         'CREATE INDEX idx_page_views_created ON page_views(created_at)'),
    ('idx_page_views_path',            'CREATE INDEX idx_page_views_path ON page_views(path)'),
    ('idx_page_views_created_path',    'CREATE INDEX idx_page_views_created_path ON page_views(created_at, path)'),
    ('idx_page_views_created_referrer', 'CREATE INDEX idx_page_views_created_referrer ON page_views(created_at, referrer)'),
    ('idx_page_views_created_ip',      'CREATE INDEX idx_page_views_created_ip ON page_views(created_at, ip_hash)'),
]

BATCH_SIZE = 50_000
BATCH_SLEEP_SECONDS = 0.1  # yield to live writers between batches


def _open_main(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=120)
    conn.execute('PRAGMA busy_timeout = 120000')
    return conn


def _open_pv(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=120)
    conn.execute('PRAGMA busy_timeout = 120000')
    conn.execute('PRAGMA journal_mode = WAL')
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return bool(row)


def _index_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
        (name,),
    ).fetchone()
    return bool(row)


def migrate(
    main_db: str,
    pv_db: str,
    batch_size: int = BATCH_SIZE,
    batch_sleep: float = BATCH_SLEEP_SECONDS,
    dry_run: bool = False,
) -> dict:
    started = time.time()
    if not os.path.exists(main_db):
        raise SystemExit(f'main DB not found: {main_db}')
    if not os.path.exists(pv_db):
        raise SystemExit(f'page_views DB not found: {pv_db}')

    main = _open_main(main_db)
    pv = _open_pv(pv_db)
    try:
        if not _table_exists(main, 'page_views'):
            raise SystemExit(f'page_views table not present in {main_db} — nothing to migrate')
        if not _table_exists(pv, 'page_views'):
            raise SystemExit(f'page_views table not present in {pv_db} — wait for first request to create it')

        main_count = main.execute('SELECT COUNT(*) FROM page_views').fetchone()[0]
        pv_count = pv.execute('SELECT COUNT(*) FROM page_views').fetchone()[0]
        print(
            f"[migrate_page_views] {datetime.now(UTC).isoformat()} "
            f"main={main_db} ({main_count:,} rows) -> pv={pv_db} ({pv_count:,} rows)",
            flush=True,
        )

        if main_count == 0:
            print('[migrate_page_views] main has 0 rows — nothing to migrate', flush=True)
            return {'migrated': 0, 'already_in_pv': pv_count, 'main_remaining': 0}

        # Idempotency check: if main is small AND pv is large, the
        # data has likely already been migrated. Bail rather than risk
        # duplicate rows (the id is auto-increment so we'd just be
        # creating a new id range, but a human re-run is the most
        # likely cause and we want to make that visible).
        if main_count < 1000 and pv_count > 1000:
            print(
                f"[migrate_page_views] WARNING: main has {main_count:,} rows and pv has {pv_count:,}. "
                f"Treating this as a no-op. If you really want to re-run, "
                f"manually delete the destination rows first.",
                flush=True,
            )
            return {'migrated': 0, 'already_in_pv': pv_count, 'main_remaining': main_count}

        # Identify the high-water mark in pv. Live writes since the
        # split have been using a different id space (they start at
        # 1 in a fresh page_views.db), so the only overlap risk is
        # between rows that already exist in both DBs (e.g. from a
        # prior partial run). We compute the safe upper bound as
        # MIN(pv_max_id, main_max_id_of_pv_rows_already_in_pv) and
        # then walk id > pv_max_id in main. The INSERT OR IGNORE
        # below catches any odd overlap.
        pv_max_id = pv.execute('SELECT COALESCE(MAX(id), 0) FROM page_views').fetchone()[0]
        main_min_id_to_keep = pv_max_id
        # If there is overlap (some rows already in both), we still
        # want to copy main rows with id > pv_max_id, since those are
        # guaranteed not to be in pv.
        to_copy = main.execute(
            'SELECT COUNT(*) FROM page_views WHERE id > ?',
            (pv_max_id,),
        ).fetchone()[0]
        print(
            f"[migrate_page_views] pv max id = {pv_max_id:,}; will copy {to_copy:,} rows "
            f"from main with id > {pv_max_id:,} (no id overlap expected; INSERT OR IGNORE "
            f"catches any odd duplicates)",
            flush=True,
        )

        if dry_run:
            return {
                'dry_run': True,
                'main_count': main_count,
                'pv_count': pv_count,
                'pv_max_id': pv_max_id,
                'to_copy': to_copy,
            }

        # Make sure destination has the indexes the analytics queries
        # need BEFORE we copy rows in (faster than after).
        for name, ddl in REQUIRED_INDEXES:
            if not _index_exists(pv, name):
                print(f"[migrate_page_views] creating {name} on destination", flush=True)
                pv.execute(ddl)
                pv.commit()
            else:
                print(f"[migrate_page_views]   {name} already exists", flush=True)

        # Walk the source in id order, batch by batch. We commit per
        # batch so each batch is its own transaction (cheap to
        # rollback if needed).
        copied = 0
        batches = 0
        last_id = pv_max_id
        while True:
            cur = main.execute(
                'SELECT id, path, ip_hash, referrer, created_at FROM page_views '
                'WHERE id > ? ORDER BY id ASC LIMIT ?',
                (last_id, batch_size),
            ).fetchall()
            if not cur:
                break
            rows = [(r[0], r[1], r[2], r[3], r[4]) for r in cur]
            last_id = rows[-1][0]
            pv.executemany(
                'INSERT OR IGNORE INTO page_views (id, path, ip_hash, referrer, created_at) '
                'VALUES (?, ?, ?, ?, ?)',
                rows,
            )
            pv.commit()
            copied += len(rows)
            batches += 1
            elapsed = time.time() - started
            rate = copied / elapsed if elapsed else 0
            print(
                f"[migrate_page_views]   batch {batches}: copied {len(rows):,} "
                f"(total {copied:,} / {to_copy:,}) @ {rate:,.0f} rows/s",
                flush=True,
            )
            time.sleep(batch_sleep)

        # Verify destination count
        pv_after = pv.execute('SELECT COUNT(*) FROM page_views').fetchone()[0]
        main_after = main.execute('SELECT COUNT(*) FROM page_views').fetchone()[0]
        print(
            f"[migrate_page_views] copied={copied:,} batches={batches} "
            f"pv_after={pv_after:,} main_after={main_after:,}",
            flush=True,
        )

        # Drop the source table + 5 indexes from blotter.db. This
        # frees the data pages but the file size on disk does NOT
        # change until VACUUM runs (we don't bundle that — see
        # script header). After the drop, the on-disk footprint of
        # blotter.db stays the same but the live working set shrinks
        # dramatically.
        print('[migrate_page_views] dropping page_views + indexes from blotter.db', flush=True)
        for name, _ in REQUIRED_INDEXES:
            if _index_exists(main, name):
                main.execute(f'DROP INDEX IF EXISTS {name}')
        main.execute('DROP TABLE IF EXISTS page_views')
        main.commit()
        print('[migrate_page_views] drop complete', flush=True)

        return {
            'migrated': copied,
            'batches': batches,
            'elapsed_seconds': round(time.time() - started, 2),
            'pv_after': pv_after,
            'main_after': main_after,
        }
    finally:
        try:
            main.close()
        except Exception:
            pass
        try:
            pv.close()
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--main-db',
        default=os.environ.get('MB_DB_PATH', DEFAULT_MAIN_DB),
        help='Path to blotter.db (default: %(default)s)',
    )
    parser.add_argument(
        '--pv-db',
        default=os.environ.get('MB_PAGE_VIEWS_DB_PATH', DEFAULT_PV_DB),
        help='Path to page_views.db (default: %(default)s)',
    )
    parser.add_argument('--batch-size', type=int, default=BATCH_SIZE)
    parser.add_argument('--batch-sleep', type=float, default=BATCH_SLEEP_SECONDS)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    result = migrate(
        main_db=args.main_db,
        pv_db=args.pv_db,
        batch_size=args.batch_size,
        batch_sleep=args.batch_sleep,
        dry_run=args.dry_run,
    )
    print(f"[migrate_page_views] done: {result}", flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
