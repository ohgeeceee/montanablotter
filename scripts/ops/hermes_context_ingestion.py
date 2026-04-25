#!/usr/bin/env python3
"""Build a focused ingestion triage context block for Hermes workflows."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/root/montanablotter")
DB_PATH = ROOT / "blotter.db"
WATCHDOG_CMD = [str(ROOT / "venv/bin/python3"), str(ROOT / "script_watchdog.py"), "--json"]


def _watchdog() -> dict:
    proc = subprocess.run(WATCHDOG_CMD, check=False, capture_output=True, text=True)
    if proc.returncode not in (0, 1):
        return {"status": "error", "details": f"watchdog exit={proc.returncode}"}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"status": "error", "details": "watchdog JSON parse failure"}


def _db_snapshot() -> dict:
    if not DB_PATH.exists():
        return {"error": "db missing"}

    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        snapshot = {}
        snapshot["jail_bookings_last_24h"] = cur.execute(
            "SELECT COUNT(*) FROM jail_bookings WHERE datetime(created_at) >= datetime('now', '-1 day')"
        ).fetchone()[0]
        snapshot["posts_last_24h"] = cur.execute(
            "SELECT COUNT(*) FROM posts WHERE datetime(created_at) >= datetime('now', '-1 day')"
        ).fetchone()[0]
        snapshot["records_last_24h"] = cur.execute(
            "SELECT COUNT(*) FROM records WHERE datetime(created_at) >= datetime('now', '-1 day')"
        ).fetchone()[0]

        recent_runs = cur.execute(
            """
            SELECT job_name, status, started_at, finished_at
            FROM scheduled_job_runs
            ORDER BY id DESC
            LIMIT 12
            """
        ).fetchall()
        snapshot["recent_scheduled_job_runs"] = [
            {
                "job_name": row[0],
                "status": row[1],
                "started_at": row[2],
                "finished_at": row[3],
            }
            for row in recent_runs
        ]
        return snapshot
    finally:
        conn.close()


def main() -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"UTC now: {now}")
    print("\nWatchdog:")
    print(json.dumps(_watchdog(), indent=2))
    print("\nIngestion DB snapshot:")
    print(json.dumps(_db_snapshot(), indent=2))


if __name__ == "__main__":
    main()
