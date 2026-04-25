#!/usr/bin/env python3
"""Build a weekly growth/content context block for Hermes workflows."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("/root/montanablotter/blotter.db")


def _query(conn: sqlite3.Connection, sql: str) -> int:
    return int(conn.execute(sql).fetchone()[0])


def main() -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"UTC now: {now}")

    if not DB_PATH.exists():
        print("\nDatabase missing: /root/montanablotter/blotter.db")
        return

    conn = sqlite3.connect(DB_PATH)
    try:
        metrics = {
            "posts_last_7d": _query(conn, "SELECT COUNT(*) FROM posts WHERE datetime(created_at) >= datetime('now', '-7 day')"),
            "posts_last_30d": _query(conn, "SELECT COUNT(*) FROM posts WHERE datetime(created_at) >= datetime('now', '-30 day')"),
            "records_last_7d": _query(conn, "SELECT COUNT(*) FROM records WHERE datetime(created_at) >= datetime('now', '-7 day')"),
            "records_last_30d": _query(conn, "SELECT COUNT(*) FROM records WHERE datetime(created_at) >= datetime('now', '-30 day')"),
            "jail_bookings_last_7d": _query(conn, "SELECT COUNT(*) FROM jail_bookings WHERE datetime(created_at) >= datetime('now', '-7 day')"),
            "jail_bookings_last_30d": _query(conn, "SELECT COUNT(*) FROM jail_bookings WHERE datetime(created_at) >= datetime('now', '-30 day')"),
            "blog_posts_published": _query(conn, "SELECT COUNT(*) FROM blog_posts WHERE published = 1"),
        }

        top_counties = conn.execute(
            """
            SELECT COALESCE(county, 'unknown') AS county, COUNT(*) AS cnt
            FROM posts
            WHERE datetime(created_at) >= datetime('now', '-30 day')
            GROUP BY county
            ORDER BY cnt DESC
            LIMIT 8
            """
        ).fetchall()

        print("\nGrowth metrics:")
        print(json.dumps(metrics, indent=2))
        print("\nTop counties by posts (last 30d):")
        print(json.dumps([{"county": row[0], "count": row[1]} for row in top_counties], indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
