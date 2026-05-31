#!/usr/bin/env python3
"""
warrant_ingest.py
=================
CLI for fetching and storing Montana county warrant lists.

Usage:
    python3 warrant_ingest.py --list
    python3 warrant_ingest.py --county rosebud [--dry-run]
    python3 warrant_ingest.py --all [--dry-run]

Cron (daily at 6am):
    0 6 * * * /root/montanablotter/venv/bin/python3 \\
        /root/montanablotter/job_runner.py \\
        --name warrant_ingest \\
        --log /root/montanablotter/logs/warrant_ingest.log \\
        --workdir /root/montanablotter -- \\
        /root/montanablotter/venv/bin/python3 \\
        /root/montanablotter/warrant_ingest.py --all
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

import config
from services.ingestion.warrants.models import ensure_warrant_schema
from services.ingestion.warrants.scraper import (
    SOURCES,
    fetch_warrants_for_county,
    resolve_stale_warrants,
    upsert_warrants,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("warrant_ingest")

DB_PATH = getattr(config, "DB_PATH", "/root/montanablotter/blotter.db")


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _run_county(slug: str, dry_run: bool, run_ts: str) -> tuple[int, int]:
    source = SOURCES.get(slug, {})
    county = source.get("county", slug)
    logger.info("Fetching warrants for %s (%s)...", county, slug)
    records = fetch_warrants_for_county(slug)

    if not records:
        logger.info("No warrant records found for %s.", county)
        return 0, 0

    if dry_run:
        for r in records:
            print(f"  {r.county}: {r.person_name} | {r.charges_text[:80]}")
        return len(records), 0

    conn = _get_conn()
    try:
        ensure_warrant_schema(conn)
        new_count, updated_count = upsert_warrants(conn, records, run_ts)
        active_ids = {r.source_record_id for r in records}
        resolved_count = resolve_stale_warrants(conn, county, active_ids, run_ts)
        logger.info(
            "%s: %d new, %d updated, %d resolved (total fetched: %d)",
            county, new_count, updated_count, resolved_count, len(records),
        )
        return new_count, updated_count
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Montana county warrant lists.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="Print available county sources.")
    group.add_argument("--county", metavar="SLUG", help="Fetch warrants for one county slug.")
    group.add_argument("--all", action="store_true", help="Fetch warrants for all registered counties.")
    parser.add_argument("--dry-run", action="store_true", help="Print records without writing to DB.")
    args = parser.parse_args()

    if args.list:
        print(f"{'Slug':<20} {'County':<20} URL")
        print("-" * 80)
        for slug, meta in sorted(SOURCES.items()):
            print(f"{slug:<20} {meta['county']:<20} {meta['url']}")
        return

    run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    total_new = 0
    total_updated = 0

    if args.county:
        n, u = _run_county(args.county, args.dry_run, run_ts)
        total_new += n
        total_updated += u
    else:
        for slug in sorted(SOURCES):
            try:
                n, u = _run_county(slug, args.dry_run, run_ts)
                total_new += n
                total_updated += u
            except Exception:
                logger.exception("Unhandled error fetching warrants for %s", slug)

    if not args.dry_run:
        logger.info("Done. Total: %d new, %d updated.", total_new, total_updated)


if __name__ == "__main__":
    main()
