#!/usr/bin/env python3
"""Build a privacy, audit, and content quality context block for Montana Blotter."""

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
        if _table_exists(conn, "posts"):
            context["audit_status_counts"] = _rows(
                conn,
                """
                SELECT COALESCE(audit_status, 'unknown') AS audit_status, COUNT(*) AS count
                FROM posts
                GROUP BY audit_status
                ORDER BY count DESC
                """,
            )
            context["recent_flagged_or_pending_posts"] = _rows(
                conn,
                """
                SELECT id, title, county, audit_status,
                       CASE
                           WHEN COALESCE(pii_flags, '') = '' THEN ''
                           ELSE substr(pii_flags, 1, 500)
                       END AS pii_flags_sample,
                       LENGTH(COALESCE(pii_flags, '')) AS pii_flags_chars,
                       created_at
                FROM posts
                WHERE COALESCE(audit_status, 'pending') NOT IN ('approved', 'clean')
                   OR COALESCE(pii_flags, '') NOT IN ('', '[]', 'null')
                ORDER BY created_at DESC
                LIMIT 20
                """,
            )
            context["thin_recent_summaries"] = _rows(
                conn,
                """
                SELECT id, title, county, LENGTH(COALESCE(summary, '')) AS summary_chars, created_at
                FROM posts
                WHERE datetime(created_at) >= datetime('now', '-7 day')
                  AND LENGTH(COALESCE(summary, '')) < 180
                ORDER BY created_at DESC
                LIMIT 20
                """,
            )
        if _table_exists(conn, "public_comments"):
            context["recent_public_comments"] = _rows(
                conn,
                """
                SELECT status, COUNT(*) AS count
                FROM public_comments
                WHERE datetime(created_at) >= datetime('now', '-7 day')
                GROUP BY status
                ORDER BY count DESC
                """,
            )
        print(json.dumps(context, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
