#!/usr/bin/env python3
"""Build a concise ops-health context block for Hermes cron workflows."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/root/montanablotter")
WATCHDOG_CMD = [str(ROOT / "venv/bin/python3"), str(ROOT / "script_watchdog.py"), "--json"]


def _load_watchdog() -> dict:
    try:
        proc = subprocess.run(WATCHDOG_CMD, check=False, capture_output=True, text=True)
    except Exception as exc:  # pragma: no cover
        return {"status": "error", "details": f"watchdog exec failed: {exc}"}
    if proc.returncode not in (0, 1):
        return {
            "status": "error",
            "details": f"watchdog exit={proc.returncode}",
            "stderr": (proc.stderr or "").strip(),
        }
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {
            "status": "error",
            "details": "watchdog produced invalid JSON",
            "stdout": proc.stdout[-500:],
        }


def _tail(path: Path, lines: int = 12) -> str:
    if not path.exists():
        return f"{path}: missing"
    try:
        data = path.read_text(errors="replace").splitlines()
    except Exception as exc:  # pragma: no cover
        return f"{path}: read error: {exc}"
    if not data:
        return f"{path}: empty"
    return "\n".join(data[-lines:])


def main() -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    watchdog = _load_watchdog()
    failing_services = watchdog.get("failing_services") or []
    failing_jobs = watchdog.get("failing_jobs") or []

    print(f"UTC now: {now}")
    print("")
    print("Watchdog summary:")
    print(json.dumps(
        {
            "status": watchdog.get("status"),
            "checked_at": watchdog.get("checked_at"),
            "job_count": watchdog.get("job_count"),
            "service_count": watchdog.get("service_count"),
            "failing_count": watchdog.get("failing_count"),
            "failing_services": failing_services,
            "failing_jobs": failing_jobs,
        },
        indent=2,
    ))

    print("\nRecent cron_errors.log tail:")
    print(_tail(ROOT / "logs" / "cron_errors.log"))
    print("\nRecent ingestion_alerts.log tail:")
    print(_tail(ROOT / "logs" / "ingestion_alerts.log"))


if __name__ == "__main__":
    main()
