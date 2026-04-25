#!/usr/bin/env python3
"""Build a daily editorial briefing context block for Montana Blotter."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("/root/montanablotter/blotter.db")


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table,),
    ).fetchone() is not None


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    cur = conn.execute(sql, params)
    cols = [col[0] for col in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def main() -> None:
    print(f"UTC now: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    if not DB_PATH.exists():
        print("Database missing: /root/montanablotter/blotter.db")
        return

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        context: dict[str, object] = {}
        if _table_exists(conn, "posts"):
            context["new_posts_by_county_24h"] = _rows(
                conn,
                """
                SELECT COALESCE(county, 'unknown') AS county, COUNT(*) AS count
                FROM posts
                WHERE datetime(created_at) >= datetime('now', '-1 day')
                GROUP BY county
                ORDER BY count DESC
                LIMIT 10
                """,
            )
            context["recent_public_posts"] = _rows(
                conn,
                """
                SELECT title, COALESCE(county, 'unknown') AS county, incident_type, created_at
                FROM posts
                ORDER BY created_at DESC
                LIMIT 12
                """,
            )
        if _table_exists(conn, "jail_bookings"):
            context["jail_booking_counties_24h"] = _rows(
                conn,
                """
                SELECT county_name, COUNT(*) AS count
                FROM jail_bookings
                WHERE datetime(created_at) >= datetime('now', '-1 day')
                GROUP BY county_name
                ORDER BY count DESC
                LIMIT 10
                """,
            )
        if _table_exists(conn, "public_meetings"):
            context["upcoming_public_meetings_7d"] = _rows(
                conn,
                """
                SELECT title, body_name, location_name, meeting_date, meeting_time, meeting_page_url
                FROM public_meetings
                WHERE date(meeting_date) BETWEEN date('now') AND date('now', '+7 day')
                ORDER BY meeting_date ASC
                LIMIT 10
                """,
            )
        if _table_exists(conn, "missing_persons"):
            context["active_missing_persons"] = _rows(
                conn,
                """
                SELECT full_name, city, county, last_seen_at, source_name
                FROM missing_persons
                WHERE status = 'missing'
                ORDER BY updated_at DESC
                LIMIT 8
                """,
            )
        if _table_exists(conn, "blog_posts"):
            context["blog_inventory"] = _rows(
                conn,
                """
                SELECT published, COUNT(*) AS count
                FROM blog_posts
                GROUP BY published
                ORDER BY published DESC
                """,
            )
        print(json.dumps(context, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
