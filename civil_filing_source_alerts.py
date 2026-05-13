#!/usr/bin/env python3
"""Alert if civil filing sources are stale or if recent records are missing."""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Civil filing source health checks")
    parser.add_argument("--db", default="blotter.db", help="SQLite database path")
    parser.add_argument(
        "--max-stale-hours",
        type=int,
        default=36,
        help="Max allowed age for source last_success_at before alerting",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=3,
        help="How many recent days to require filing activity",
    )
    parser.add_argument(
        "--min-recent-records",
        type=int,
        default=1,
        help="Minimum filings required in lookback window",
    )
    parser.add_argument(
        "--bootstrap-grace-days",
        type=int,
        default=14,
        help="Days to allow newly added sources before alerting on first success",
    )
    return parser.parse_args()


def parse_ts(value: str | None) -> datetime | None:
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

    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(hours=args.max_stale_hours)
    recent_cutoff = (now.date() - timedelta(days=args.lookback_days)).isoformat()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            id, source_key, county, is_enabled, last_success_at,
            COALESCE(last_error, '') AS last_error,
            created_at
        FROM civil_filing_sources
        WHERE adapter_type = 'icourtcase'
        ORDER BY source_key
        """
    )
    sources = cur.fetchall()

    issues: list[str] = []
    checks: list[str] = []

    if not sources:
        issues.append("no civil filing sources configured for adapter_type=icourtcase")

    grace_cutoff = now - timedelta(days=args.bootstrap_grace_days)
    for source in sources:
        if int(source["is_enabled"] or 0) != 1:
            checks.append(f"SKIP {source['source_key']}: disabled")
            continue

        last_success = parse_ts(source["last_success_at"])
        if last_success is None:
            created_at = parse_ts(source["created_at"])
            if created_at and created_at >= grace_cutoff:
                checks.append(
                    f"BOOTSTRAP {source['source_key']}: waiting for first success "
                    f"(created_at={source['created_at']})"
                )
            else:
                issues.append(f"{source['source_key']} has never succeeded")
        elif last_success < stale_cutoff:
            issues.append(
                f"{source['source_key']} stale last_success_at={source['last_success_at']} "
                f"(>{args.max_stale_hours}h old)"
            )
        else:
            checks.append(f"OK {source['source_key']}: last_success_at={source['last_success_at']}")

        if source["last_error"].strip():
            issues.append(f"{source['source_key']} last_error={source['last_error']}")

        if last_success is not None:
            cur.execute(
                """
                SELECT COUNT(*)
                FROM civil_filings
                WHERE source_id = ?
                  AND filing_date >= ?
                """,
                (source["id"], recent_cutoff),
            )
            recent_count = int(cur.fetchone()[0])
            if recent_count < args.min_recent_records:
                issues.append(
                    f"{source['source_key']} recent filings={recent_count} "
                    f"(required>={args.min_recent_records} since {recent_cutoff})"
                )
            else:
                checks.append(
                    f"OK {source['source_key']}: recent filings={recent_count} since {recent_cutoff}"
                )

    for line in checks:
        print(line)
    if issues:
        for line in issues:
            print(f"ALERT: {line}")
        return 1

    print("Civil filing source health check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
