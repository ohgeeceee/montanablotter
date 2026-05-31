#!/usr/bin/env python3
"""Build a revenue and advertiser opportunity context block for Montana Blotter."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("/root/montanablotter/data/blotter.db")


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table,),
    ).fetchone() is not None


def _rows(conn: sqlite3.Connection, sql: str) -> list[dict]:
    cur = conn.execute(sql)
    cols = [col[0] for col in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def main() -> None:
    print(f"UTC now: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    if not DB_PATH.exists():
        print(f"Database missing: {DB_PATH}")
        return

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        context: dict[str, object] = {}
        for table in ["bail_ad_inquiries", "bail_ad_orders", "bail_consumer_leads", "bail_agency_outreach"]:
            if _table_exists(conn, table):
                context[f"{table}_last_30d"] = _rows(
                    conn,
                    f"""
                    SELECT COUNT(*) AS count, MAX(created_at) AS newest_seen
                    FROM {table}
                    WHERE datetime(created_at) >= datetime('now', '-30 day')
                    """,
                )
        if _table_exists(conn, "jail_bookings"):
            context["top_booking_counties_30d"] = _rows(
                conn,
                """
                SELECT county_name, COUNT(*) AS booking_count
                FROM jail_bookings
                WHERE datetime(created_at) >= datetime('now', '-30 day')
                GROUP BY county_name
                ORDER BY booking_count DESC
                LIMIT 12
                """,
            )
        if _table_exists(conn, "posts"):
            context["top_post_counties_30d"] = _rows(
                conn,
                """
                SELECT COALESCE(county, 'unknown') AS county, COUNT(*) AS post_count
                FROM posts
                WHERE datetime(created_at) >= datetime('now', '-30 day')
                GROUP BY county
                ORDER BY post_count DESC
                LIMIT 12
                """,
            )
        if _table_exists(conn, "search_console_rows"):
            context["high_intent_search_queries"] = _rows(
                conn,
                """
                SELECT query, clicks, impressions, ctr, position
                FROM search_console_rows
                WHERE LOWER(query) LIKE '%bail%'
                   OR LOWER(query) LIKE '%jail roster%'
                   OR LOWER(query) LIKE '%warrant%'
                ORDER BY clicks DESC, impressions DESC
                LIMIT 20
                """,
            )
        print(json.dumps(context, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
