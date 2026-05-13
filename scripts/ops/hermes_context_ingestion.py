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


def _compact_watchdog(payload: dict) -> dict:
    """Keep only high-signal watchdog fields to control prompt token size."""
    if not isinstance(payload, dict):
        return {"status": "error", "details": "watchdog payload invalid"}

    checks = payload.get("checks") or []
    failing_checks = []
    for row in checks:
        status = (row or {}).get("status")
        if status and status != "ok":
            failing_checks.append(
                {
                    "name": row.get("name"),
                    "kind": row.get("kind"),
                    "status": status,
                    "details": row.get("details"),
                    "last_seen_at": row.get("last_seen_at"),
                    "age_hours": row.get("age_hours"),
                }
            )

    # Keep only a small sample of near-threshold "ok" jobs for early warning.
    near_stale = []
    for row in checks:
        if (row or {}).get("kind") != "job":
            continue
        if (row or {}).get("status") != "ok":
            continue
        max_age = row.get("max_age_hours")
        age = row.get("age_hours")
        if isinstance(max_age, (int, float)) and isinstance(age, (int, float)) and max_age > 0:
            ratio = age / max_age
            if ratio >= 0.75:
                near_stale.append(
                    {
                        "name": row.get("name"),
                        "cadence": row.get("cadence"),
                        "age_hours": age,
                        "max_age_hours": max_age,
                    }
                )

    near_stale = sorted(near_stale, key=lambda r: r["age_hours"] / r["max_age_hours"], reverse=True)[:5]

    summary = payload.get("summary") or {}
    return {
        "status": payload.get("status"),
        "checked_at": payload.get("checked_at"),
        "summary": {
            "total": summary.get("total"),
            "ok": summary.get("ok"),
            "warn": summary.get("warn"),
            "critical": summary.get("critical"),
            "unknown": summary.get("unknown"),
        },
        "failing_checks": failing_checks[:12],
        "near_stale_checks": near_stale,
    }


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
    print(json.dumps(_compact_watchdog(_watchdog()), indent=2))
    print("\nIngestion DB snapshot:")
    print(json.dumps(_db_snapshot(), indent=2))


if __name__ == "__main__":
    main()
