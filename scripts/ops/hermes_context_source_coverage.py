#!/usr/bin/env python3
"""Build a source coverage and freshness context block for Montana Blotter."""

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
        if _table_exists(conn, "source_documents"):
            context["source_documents_by_type_7d"] = _rows(
                conn,
                """
                SELECT source_type, COUNT(*) AS count, MAX(created_at) AS newest_seen
                FROM source_documents
                WHERE datetime(created_at) >= datetime('now', '-7 day')
                GROUP BY source_type
                ORDER BY count DESC
                """,
            )
        if _table_exists(conn, "jail_booking_sources"):
            context["jail_booking_sources"] = _rows(
                conn,
                """
                SELECT county_name, facility_name, source_type, coverage_tier, is_enabled,
                       last_checked_at, last_success_at, latest_error, notes
                FROM jail_booking_sources
                ORDER BY county_name ASC
                LIMIT 40
                """,
            )
        if _table_exists(conn, "ingestion_source_alerts"):
            context["open_ingestion_alerts"] = _rows(
                conn,
                """
                SELECT source_type, alert_kind, summary, first_detected_at, last_detected_at
                FROM ingestion_source_alerts
                WHERE state = 'open'
                ORDER BY last_detected_at DESC
                LIMIT 20
                """,
            )
        if _table_exists(conn, "court_source_alerts"):
            context["open_court_source_alerts"] = _rows(
                conn,
                """
                SELECT source_slug, alert_kind, summary, first_detected_at, last_detected_at
                FROM court_source_alerts
                WHERE state = 'open'
                ORDER BY last_detected_at DESC
                LIMIT 20
                """,
            )
        if _table_exists(conn, "meeting_source_alerts"):
            context["open_meeting_source_alerts"] = _rows(
                conn,
                """
                SELECT source_slug, alert_kind, summary, first_detected_at, last_detected_at
                FROM meeting_source_alerts
                WHERE state = 'open'
                ORDER BY last_detected_at DESC
                LIMIT 20
                """,
            )
        print(json.dumps(context, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
