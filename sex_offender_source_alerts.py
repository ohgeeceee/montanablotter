#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Violent / sexual offender registry freshness/coverage alerts")
    parser.add_argument("--db", default="blotter.db")
    parser.add_argument("--max-stale-hours", type=int, default=30)
    parser.add_argument("--min-active", type=int, default=500)
    parser.add_argument("--min-counties", type=int, default=40)
    return parser.parse_args()


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ALERT: database not found at {db_path}")
        return 2

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT id, snapshot_date, total_count, notes FROM sex_offender_snapshots ORDER BY id DESC LIMIT 1")
    latest = cur.fetchone()
    if not latest:
        print("ALERT: no violent/sexual offender snapshots found")
        return 1

    issues: list[str] = []
    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(hours=args.max_stale_hours)
    snap_ts = _parse_ts(latest["snapshot_date"])
    if not snap_ts:
        issues.append(f"latest snapshot has invalid timestamp: {latest['snapshot_date']}")
    elif snap_ts < stale_cutoff:
        issues.append(
            f"latest snapshot is stale: {latest['snapshot_date']} (> {args.max_stale_hours}h old)"
        )

    cur.execute("SELECT COUNT(*) FROM sex_offenders WHERE status = 'active'")
    active_count = int(cur.fetchone()[0])
    if active_count < args.min_active:
        issues.append(f"active offender count too low: {active_count} < {args.min_active}")

    cur.execute(
        """
        SELECT COUNT(DISTINCT address_county)
        FROM sex_offenders
        WHERE status = 'active' AND COALESCE(TRIM(address_county), '') <> ''
        """
    )
    county_count = int(cur.fetchone()[0])
    if county_count < args.min_counties:
        issues.append(f"active county coverage too low: {county_count} < {args.min_counties}")

    print(
        f"snapshot_id={latest['id']} snapshot_date={latest['snapshot_date']} "
        f"active={active_count} counties={county_count}"
    )
    if latest["notes"]:
        print(f"notes={latest['notes']}")

    if issues:
        for issue in issues:
            print(f"ALERT: {issue}")
        return 1
    print("Violent / sexual offender source health check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
