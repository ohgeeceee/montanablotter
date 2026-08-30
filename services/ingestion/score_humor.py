"""Score humor on blotter records for the public /funniest feed.

Idempotent: only touches rows where humor_score IS NULL. Re-runnable on a cron
with no special handling. Safe to run alongside the ingest pipeline because it
only writes to the indexed ``humor_score`` column.

Usage:
    python services/ingestion/score_humor.py            # score all unscored rows
    python services/ingestion/score_humor.py --dry-run  # log counts, write nothing
    python services/ingestion/score_humor.py --limit 500
    python services/ingestion/score_humor.py --batch-size 200
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time

sys.path.insert(0, "/root/montanablotter")

import config
from db import connect_db
from services.blotter.humor import is_eligible, score_humor

logger = logging.getLogger(__name__)

DB_TIMEOUT_SECONDS = float(getattr(config, "DB_TIMEOUT_SECONDS", 60))
BATCH_SIZE = int(getattr(config, "HUMOR_BATCH_SIZE", 500))


def fetch_unscored(conn: sqlite3.Connection, limit: int | None, batch_size: int) -> list[sqlite3.Row]:
    """Return up to ``batch_size`` (or ``limit``) unscored, potentially-eligible rows.

    We pull rows with humor_score IS NULL and an incident body long enough to
    matter, then let ``is_eligible`` do the fine-grained deny-list filtering in
    Python so ineligible rows still get a 0 score written (avoiding re-scan).
    """
    sql = (
        "SELECT id, incident, details, incident_type "
        "FROM records "
        "WHERE humor_score IS NULL "
        "  AND length(coalesce(incident, '') || coalesce(details, '')) >= 8 "
        "ORDER BY id ASC "
        "LIMIT ?"
    )
    return conn.execute(
        sql, (batch_size if limit is None else min(batch_size, limit),)
    ).fetchall()


def score_and_write(conn: sqlite3.Connection, rows: list[sqlite3.Row], dry_run: bool) -> tuple[int, int]:
    """Score rows and write humor_score. Returns (scored_count, eligible_count)."""
    scored = 0
    eligible = 0
    updates = []
    for row in rows:
        incident = row["incident"] or ""
        details = row["details"] or ""
        itype = row["incident_type"]
        s = score_humor(incident, details, itype)
        scored += 1
        if s > 0:
            eligible += 1
        # Write the score even when 0 so ineligible/non-funny rows aren't re-scanned.
        updates.append((s, row["id"]))
    if not dry_run and updates:
        conn.executemany("UPDATE records SET humor_score = ? WHERE id = ?", updates)
        conn.commit()
    return scored, eligible


def run(*, dry_run: bool, limit: int | None, batch_size: int) -> dict:
    conn = connect_db(timeout_seconds=DB_TIMEOUT_SECONDS)
    try:
        total_scored = 0
        total_eligible = 0
        processed = 0
        while True:
            rows = fetch_unscored(conn, limit, batch_size)
            if not rows:
                break
            scored, eligible = score_and_write(conn, rows, dry_run)
            total_scored += scored
            total_eligible += eligible
            processed += len(rows)
            if limit is not None and processed >= limit:
                break
            # In dry-run nothing is persisted, so the same rows would be re-fetched
            # forever. One pass is enough to report what *would* be scored.
            if dry_run:
                break
        return {"scored": total_scored, "eligible": total_eligible, "dry_run": dry_run}
    finally:
        conn.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Score humor on blotter records.")
    parser.add_argument("--dry-run", action="store_true", help="Compute scores, write nothing.")
    parser.add_argument("--limit", type=int, default=None, help="Max rows to process this run.")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE, help="Rows per transaction.")
    args = parser.parse_args()

    start = time.monotonic()
    result = run(dry_run=args.dry_run, limit=args.limit, batch_size=args.batch_size)
    elapsed = time.monotonic() - start
    verb = "Would score" if args.dry_run else "Scored"
    logger.info(
        "%s %d rows (%d eligible for /funniest) in %.2fs%s",
        verb, result["scored"], result["eligible"], elapsed,
        " [dry-run]" if args.dry_run else "",
    )


if __name__ == "__main__":
    main()
