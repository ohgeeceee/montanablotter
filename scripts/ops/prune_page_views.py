#!/usr/bin/env python3
"""
prune_page_views.py — Daily retention prune for the page_views table.

Page-view analytics are kept in the dedicated local-only database
(page_views.db) so they don't bloat the main blotter.db. The main
database still has 16.7M historical rows from before the write-path
was split; this script is the long-term retention guard for the
post-split writes that accumulate in page_views.db.

Retention window: 90 days. Anything older is deleted in batches to
keep the per-transaction cost low and avoid long write locks. VACUUM
runs at the end to reclaim the freed pages on disk.

Cron (add to crontab.txt, runs at 03:30 daily — 30 minutes after the
DB backup at 03:00 so the snapshot is taken first):

  30 3 * * * /root/montanablotter/venv/bin/python3 \\
      /root/montanablotter/scripts/ops/prune_page_views.py \\
      >> /root/montanablotter/logs/prune_page_views.log 2>&1

Safe to re-run. Safe to interrupt between batches (the DELETE is
idempotent). Safe to run before the historical migration — anything
older than 90 days in page_views.db is the natural retention target.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime, UTC
from pathlib import Path

# Match the path resolution in db.py so this script works whether the
# operator sets MB_PAGE_VIEWS_DB_PATH or relies on the default next to
# blotter.db.
DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'data',
    'page_views.db',
)
RETENTION_DAYS = 90
BATCH_SIZE = 50_000
BATCH_SLEEP_SECONDS = 0.05  # give other processes a chance to use the DB
VACUUM_THRESHOLD = 1_000_000  # only run VACUUM after at least this many rows deleted


def _open(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=60)
    conn.execute('PRAGMA busy_timeout = 60000')
    conn.execute('PRAGMA journal_mode = WAL')
    return conn


def prune(
    db_path: str,
    retention_days: int = RETENTION_DAYS,
    batch_size: int = BATCH_SIZE,
    vacuum_threshold: int = VACUUM_THRESHOLD,
    dry_run: bool = False,
) -> dict:
    started = time.time()
    if not os.path.exists(db_path):
        return {
            'db_path': db_path,
            'started_at': datetime.now(UTC).isoformat(),
            'skipped': 'file does not exist (no data yet)',
            'rows_deleted': 0,
            'batches': 0,
            'elapsed_seconds': 0.0,
        }

    conn = _open(db_path)
    try:
        total_before = conn.execute('SELECT COUNT(*) FROM page_views').fetchone()[0]
        cutoff = f"-{retention_days} days"
        # Count first so we report an honest before/after pair
        to_delete = conn.execute(
            'SELECT COUNT(*) FROM page_views WHERE created_at < datetime(\'now\', ?)',
            (cutoff,),
        ).fetchone()[0]
        print(
            f"[prune_page_views] {datetime.now(UTC).isoformat()} "
            f"db={db_path} rows={total_before:,} cutoff=now({cutoff}) to_delete={to_delete:,}",
            flush=True,
        )

        if dry_run or to_delete == 0:
            return {
                'db_path': db_path,
                'started_at': started,
                'rows_before': total_before,
                'rows_to_delete': to_delete,
                'rows_deleted': 0,
                'batches': 0,
                'vacuumed': False,
                'dry_run': bool(dry_run),
                'elapsed_seconds': round(time.time() - started, 2),
            }

        deleted = 0
        batches = 0
        last_id = 0
        while True:
            # Delete by id window to keep each statement cheap. We pick
            # a contiguous slice of the oldest rows past the cutoff and
            # bound it by the last id we already deleted so a parallel
            # insert doesn't move rows into the slice mid-loop.
            cur = conn.execute(
                '''
                SELECT id FROM page_views
                WHERE created_at < datetime('now', ?) AND id > ?
                ORDER BY id ASC LIMIT ?
                ''',
                (cutoff, last_id, batch_size),
            ).fetchall()
            if not cur:
                break
            ids = [r[0] for r in cur]
            last_id = ids[-1]
            conn.execute(
                f'DELETE FROM page_views WHERE id IN ({"?, " * (len(ids) - 1)}?)',
                ids,
            )
            conn.commit()
            deleted += len(ids)
            batches += 1
            print(
                f"[prune_page_views]   batch {batches}: deleted {len(ids):,} "
                f"(total {deleted:,} / {to_delete:,})",
                flush=True,
            )
            # Yield to other writers between batches so the live site
            # doesn't see a long write lock.
            time.sleep(BATCH_SLEEP_SECONDS)

        vacuumed = False
        if deleted >= vacuum_threshold:
            print('[prune_page_views] reclaiming disk space with VACUUM...', flush=True)
            conn.execute('VACUUM')
            vacuumed = True
            print('[prune_page_views] VACUUM complete', flush=True)

        total_after = conn.execute('SELECT COUNT(*) FROM page_views').fetchone()[0]
        result = {
            'db_path': db_path,
            'started_at': datetime.fromtimestamp(started, UTC).isoformat(),
            'rows_before': total_before,
            'rows_after': total_after,
            'rows_deleted': deleted,
            'batches': batches,
            'vacuumed': vacuumed,
            'dry_run': False,
            'elapsed_seconds': round(time.time() - started, 2),
        }
        print(
            f"[prune_page_views] done rows_before={total_before:,} "
            f"rows_after={total_after:,} deleted={deleted:,} "
            f"batches={batches} vacuumed={vacuumed} "
            f"elapsed={result['elapsed_seconds']}s",
            flush=True,
        )
        return result
    finally:
        try:
            conn.close()
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--db',
        default=os.environ.get('MB_PAGE_VIEWS_DB_PATH', DEFAULT_DB_PATH),
        help='Path to page_views.db (default: %(default)s)',
    )
    parser.add_argument('--retention-days', type=int, default=RETENTION_DAYS)
    parser.add_argument('--batch-size', type=int, default=BATCH_SIZE)
    parser.add_argument('--vacuum-threshold', type=int, default=VACUUM_THRESHOLD)
    parser.add_argument('--dry-run', action='store_true', help='Count rows, do not delete')
    args = parser.parse_args()

    result = prune(
        db_path=args.db,
        retention_days=args.retention_days,
        batch_size=args.batch_size,
        vacuum_threshold=args.vacuum_threshold,
        dry_run=args.dry_run,
    )
    # Always exit 0 unless a hard error: cron should not alarm on a
    # routine 0-row prune.
    return 0


if __name__ == '__main__':
    sys.exit(main())
