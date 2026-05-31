#!/usr/bin/env python3
"""
facebook_stats_poster.py — Queue statistical summaries and Innocence Project content for Facebook.

Modes:
    preview          — Show what would be posted without queuing
    queue-stats      — Generate and queue a stats post from recent blotter data
    queue-innocence  — Queue the next Innocence Project awareness message
    report           — Show queued custom items

Usage:
    ./venv/bin/python3 facebook_stats_poster.py --mode preview
    ./venv/bin/python3 facebook_stats_poster.py --mode queue-stats --limit 1
    ./venv/bin/python3 facebook_stats_poster.py --mode queue-innocence --limit 1
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

from facebook_publisher import queue_post, run_facebook_queue

DB_PATH = os.getenv("DB_PATH", "/root/montanablotter/blotter.db")
DB_TIMEOUT = float(os.getenv("DB_TIMEOUT_SECONDS", "30"))
BUSY_TIMEOUT_MS = int(os.getenv("DB_BUSY_TIMEOUT_MS", "30000"))

INNOCENCE_PROJECT_MESSAGES = [
    {
        "message": (
            "Montana's Innocence Project works to exonerate the wrongfully convicted and reform the system that put them there. "
            "Public records — like the blotters we publish every day — are part of the transparency that prevents injustice.\n\n"
            "Learn more about the Montana Innocence Project and how you can support their work.\n\n"
            "#Montana #InnocenceProject #CriminalJusticeReform #PublicRecords"
        ),
        "link_url": "https://www.montanainnocenceproject.org/",
    },
    {
        "message": (
            "Did you know Montana has no law guaranteeing the preservation of biological evidence after conviction? "
            "The Montana Innocence Project is fighting to change that — because DNA doesn't lie, but systems can fail.\n\n"
            "Support evidence preservation and accountability in Montana.\n\n"
            "#Montana #InnocenceProject #DNA #JusticeReform"
        ),
        "link_url": "https://www.montanainnocenceproject.org/",
    },
    {
        "message": (
            "Wrongful convictions don't just harm the innocent — they let the real perpetrators go free and erode trust in law enforcement. "
            "Montana Blotter believes transparency is the first step toward accountability.\n\n"
            "The Montana Innocence Project needs your support.\n\n"
            "#Montana #InnocenceProject #Accountability #PublicSafety"
        ),
        "link_url": "https://www.montanainnocenceproject.org/",
    },
    {
        "message": (
            "Montana's county jails and blotters are public records. So should be the evidence that convicts or exonerates. "
            "The Montana Innocence Project advocates for open discovery, evidence preservation, and post-conviction DNA access.\n\n"
            "Read the blotter. Support the cause.\n\n"
            "#Montana #InnocenceProject #OpenRecords #Transparency"
        ),
        "link_url": "https://www.montanainnocenceproject.org/",
    },
]


def _connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    return conn


def _generate_weekly_stats(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Generate a stats summary for the trailing 7 days."""
    since = (datetime.now(UTC) - timedelta(days=7)).isoformat()

    # Total incidents
    total_row = conn.execute(
        "SELECT COUNT(*) as c FROM posts WHERE created_at >= ?",
        (since,),
    ).fetchone()
    total = total_row["c"] if total_row else 0
    if total == 0:
        return None

    # Top counties
    county_rows = conn.execute(
        """
        SELECT county, COUNT(*) as c
        FROM posts
        WHERE created_at >= ? AND county IS NOT NULL AND county != ''
        GROUP BY county
        ORDER BY c DESC
        LIMIT 3
        """,
        (since,),
    ).fetchall()
    top_counties = [f"{r['county']} ({r['c']})" for r in county_rows]

    # Top incident types
    type_rows = conn.execute(
        """
        SELECT incident_type, COUNT(*) as c
        FROM posts
        WHERE created_at >= ? AND incident_type IS NOT NULL AND incident_type != ''
        GROUP BY incident_type
        ORDER BY c DESC
        LIMIT 3
        """,
        (since,),
    ).fetchall()
    top_types = [f"{r['incident_type']} ({r['c']})" for r in type_rows]

    # DUI count (search title/summary for DUI)
    dui_row = conn.execute(
        """
        SELECT COUNT(*) as c
        FROM posts
        WHERE created_at >= ? AND (lower(title) LIKE '%dui%' OR lower(summary) LIKE '%dui%')
        """,
        (since,),
    ).fetchone()
    dui_count = dui_row["c"] if dui_row else 0

    message = (
        f"📊 This Week in Montana Blotters\n\n"
        f"{total:,} incidents logged across the state.\n\n"
        f"Top counties:\n"
        + ("\n".join(f"• {c}" for c in top_counties) or "• N/A") + "\n\n"
        + f"Most common incident types:\n"
        + ("\n".join(f"• {c}" for c in top_types) or "• N/A") + "\n\n"
        + f"{dui_count} DUI-related incidents.\n\n"
        + "Data pulled from public police blotters statewide.\n"
        + "Read more at montanablotter.com\n\n"
        + "#Montana #PublicSafety #CrimeStats #Transparency"
    )

    if len(message) > 5000:
        message = message[:4997].rstrip() + "..."

    return {
        "message": message,
        "link_url": "https://montanablotter.com",
    }


def _generate_daily_stats(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Generate a stats summary for the trailing 24 hours."""
    since = (datetime.now(UTC) - timedelta(hours=24)).isoformat()

    total_row = conn.execute(
        "SELECT COUNT(*) as c FROM posts WHERE created_at >= ?",
        (since,),
    ).fetchone()
    total = total_row["c"] if total_row else 0
    if total == 0:
        return None

    county_rows = conn.execute(
        """
        SELECT county, COUNT(*) as c
        FROM posts
        WHERE created_at >= ? AND county IS NOT NULL AND county != ''
        GROUP BY county
        ORDER BY c DESC
        LIMIT 3
        """,
        (since,),
    ).fetchall()
    top_counties = [f"{r['county']} ({r['c']})" for r in county_rows]

    message = (
        f"📋 Daily Snapshot — Montana Blotters\n\n"
        f"{total:,} incidents in the last 24 hours.\n\n"
        f"Most active counties:\n"
        + ("\n".join(f"• {c}" for c in top_counties) or "• N/A") + "\n\n"
        + "See the full picture at montanablotter.com\n\n"
        + "#Montana #DailyBlotter #PublicSafety"
    )

    if len(message) > 5000:
        message = message[:4997].rstrip() + "..."

    return {"message": message, "link_url": "https://montanablotter.com"}


def _mode_preview(conn: sqlite3.Connection) -> dict[str, Any]:
    weekly = _generate_weekly_stats(conn)
    daily = _generate_daily_stats(conn)
    innocence = INNOCENCE_PROJECT_MESSAGES[0]

    return {
        "ok": True,
        "weekly_stats_preview": weekly["message"] if weekly else "No data for weekly stats.",
        "daily_stats_preview": daily["message"] if daily else "No data for daily stats.",
        "innocence_preview": innocence["message"],
    }


def _mode_queue_stats(conn: sqlite3.Connection, limit: int = 1, period: str = "weekly") -> dict[str, Any]:
    queued = 0
    skipped = 0
    for _ in range(limit):
        if period == "daily":
            payload = _generate_daily_stats(conn)
        else:
            payload = _generate_weekly_stats(conn)
        if not payload:
            skipped += 1
            continue
        result = queue_post(
            content_type="custom",
            enqueue_source="fb_stats_poster",
            custom_message=payload["message"],
            link_url=payload["link_url"],
            conn=conn,
        )
        if result.get("ok") and (result.get("created") or result.get("requeued")):
            queued += 1
        else:
            skipped += 1
    conn.commit()
    return {"ok": True, "queued": queued, "skipped": skipped}


def _mode_queue_innocence(conn: sqlite3.Connection, limit: int = 1) -> dict[str, Any]:
    # Rotate through messages based on what's been posted recently
    recent = conn.execute(
        """
        SELECT custom_message FROM facebook_post_queue
        WHERE content_type = 'custom' AND enqueue_source = 'fb_innocence_poster'
        ORDER BY created_at DESC
        LIMIT 10
        """
    ).fetchall()
    recent_messages = {r["custom_message"] for r in recent}

    queued = 0
    skipped = 0
    for _ in range(limit):
        # Pick a message not recently used, or random if all used
        available = [m for m in INNOCENCE_PROJECT_MESSAGES if m["message"] not in recent_messages]
        if not available:
            available = INNOCENCE_PROJECT_MESSAGES
        choice = random.choice(available)

        result = queue_post(
            content_type="custom",
            enqueue_source="fb_innocence_poster",
            custom_message=choice["message"],
            link_url=choice["link_url"],
            conn=conn,
        )
        if result.get("ok") and (result.get("created") or result.get("requeued")):
            queued += 1
            recent_messages.add(choice["message"])
        else:
            skipped += 1
    conn.commit()
    return {"ok": True, "queued": queued, "skipped": skipped}


def _mode_report(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT content_type, status, COUNT(*) as c
        FROM facebook_post_queue
        GROUP BY content_type, status
        """
    ).fetchall()
    stats = {}
    for r in rows:
        stats.setdefault(r["content_type"], {})[r["status"]] = r["c"]

    recent = conn.execute(
        """
        SELECT content_type, custom_message, link_url, status, created_at
        FROM facebook_post_queue
        WHERE content_type = 'custom'
        ORDER BY created_at DESC
        LIMIT 5
        """
    ).fetchall()

    return {
        "ok": True,
        "stats": stats,
        "recent_custom": [{"message": r["custom_message"][:200], "status": r["status"], "created_at": r["created_at"]} for r in recent],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Queue Montana Blotter stats and Innocence Project posts for Facebook.")
    parser.add_argument("--mode", choices=["preview", "queue-stats", "queue-innocence", "report"], required=True)
    parser.add_argument("--limit", type=int, default=1, help="Max items to queue")
    parser.add_argument("--period", choices=["daily", "weekly"], default="weekly", help="Stats period")
    args = parser.parse_args()

    conn = _connect_db()
    try:
        if args.mode == "preview":
            result = _mode_preview(conn)
        elif args.mode == "queue-stats":
            result = _mode_queue_stats(conn, limit=args.limit, period=args.period)
        elif args.mode == "queue-innocence":
            result = _mode_queue_innocence(conn, limit=args.limit)
        else:
            result = _mode_report(conn)

        print(json.dumps(result, indent=2, default=str))
        return 0 if result.get("ok") else 1
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
