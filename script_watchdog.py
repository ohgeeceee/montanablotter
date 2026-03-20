#!/usr/bin/env python3
"""
Verify scheduled Montana Blotter jobs have produced recent log activity.
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path("/root/montanablotter")
SYSTEMD_SERVICE = "montanablotter.service"
WEB_SOCKET_PATH = Path("/tmp/montanablotter.sock")
WEB_REQUEST_PATH = "/"


@dataclass(frozen=True)
class MonitoredJob:
    name: str
    log_path: Path
    max_age_hours: float
    cadence: str


JOBS: tuple[MonitoredJob, ...] = (
    MonitoredJob("cleanup", ROOT / "cleanup.log", 30, "daily"),
    MonitoredJob("email_worker", ROOT / "mail.log", 2, "every 15 minutes"),
    MonitoredJob("facebook_worker", ROOT / "facebook_worker.log", 2, "every 15 minutes"),
    MonitoredJob("morning_briefing", ROOT / "briefing.log", 30, "daily"),
    MonitoredJob("daily_blog_worker", ROOT / "daily_blog.log", 30, "daily"),
    MonitoredJob("weekly_county_digest", ROOT / "weekly_county_digest.log", 8 * 24, "weekly"),
    MonitoredJob("pattern_conversion_report", ROOT / "cron.log", 30, "daily"),
    MonitoredJob("backup_db", ROOT / "backup.log", 26, "daily"),
    MonitoredJob("court_refresh", ROOT / "court_refresh.log", 5, "every 3 hours"),
    MonitoredJob("court_source_alerts", ROOT / "court_source_alerts.log", 2, "every 30 minutes"),
    MonitoredJob("agendas_ingest", ROOT / "agendas_ingest.log", 8, "every 6 hours"),
    MonitoredJob("meeting_source_alerts", ROOT / "meeting_source_alerts.log", 2, "every 30 minutes"),
    MonitoredJob("crimemapping_fetcher", ROOT / "crimemapping.log", 14, "twice daily"),
    MonitoredJob("missoula_public_report_fetcher", ROOT / "missoula_fetcher.log", 2, "hourly"),
    MonitoredJob("whitefish_blotter_fetcher", ROOT / "whitefish_fetcher.log", 8, "every 6 hours"),
    MonitoredJob("jail_booking_ingest", ROOT / "jail_booking_ingest.log", 4, "every 2 hours"),
    MonitoredJob("bozeman_police_calls", ROOT / "bozeman_calls.log", 2, "hourly"),
    MonitoredJob("bozeman_police_crime", ROOT / "bozeman_crime.log", 8, "every 6 hours"),
    MonitoredJob("ingestion_alerts", ROOT / "ingestion_alerts.log", 2, "every 30 minutes"),
)


def _isoformat(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _check_job(job: MonitoredJob, now: datetime) -> dict[str, object]:
    exists = job.log_path.exists()
    modified_at = None
    age_hours = None
    status = "ok"

    if exists:
        modified_at = datetime.fromtimestamp(job.log_path.stat().st_mtime, tz=timezone.utc)
        age_hours = round((now - modified_at).total_seconds() / 3600, 2)
        if now - modified_at > timedelta(hours=job.max_age_hours):
            status = "stale"
    else:
        status = "missing"

    return {
        "name": job.name,
        "kind": "job",
        "cadence": job.cadence,
        "log_path": str(job.log_path),
        "status": status,
        "max_age_hours": job.max_age_hours,
        "last_seen_at": _isoformat(modified_at),
        "age_hours": age_hours,
    }


def _check_systemd_service() -> dict[str, object]:
    try:
        result = subprocess.run(
            [
                "systemctl",
                "show",
                SYSTEMD_SERVICE,
                "--property=ActiveState,SubState,ExecMainPID,ExecMainStatus",
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        return {
            "name": SYSTEMD_SERVICE,
            "kind": "service",
            "status": "missing",
            "details": "systemctl is not installed",
        }
    except subprocess.TimeoutExpired:
        return {
            "name": SYSTEMD_SERVICE,
            "kind": "service",
            "status": "error",
            "details": "systemctl timed out",
        }

    properties: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        properties[key] = value

    active_state = properties.get("ActiveState")
    sub_state = properties.get("SubState")
    pid = properties.get("ExecMainPID")
    exit_status = properties.get("ExecMainStatus")

    status = "ok" if result.returncode == 0 and active_state == "active" and sub_state == "running" else "error"
    details = f"active={active_state or 'unknown'} sub={sub_state or 'unknown'} pid={pid or 'unknown'} exit={exit_status or 'unknown'}"

    return {
        "name": SYSTEMD_SERVICE,
        "kind": "service",
        "status": status,
        "active_state": active_state,
        "sub_state": sub_state,
        "pid": pid,
        "exit_status": exit_status,
        "details": details,
    }


def _check_web_service() -> dict[str, object]:
    if not WEB_SOCKET_PATH.exists():
        return {
            "name": "web",
            "kind": "service",
            "status": "missing",
            "socket_path": str(WEB_SOCKET_PATH),
            "details": "unix socket not found",
        }

    started = datetime.now(timezone.utc)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(5)
            client.connect(str(WEB_SOCKET_PATH))
            request = (
                f"HEAD {WEB_REQUEST_PATH} HTTP/1.1\r\n"
                "Host: localhost\r\n"
                "Connection: close\r\n"
                "\r\n"
            )
            client.sendall(request.encode("ascii"))

            response = b""
            while b"\r\n" not in response and len(response) < 4096:
                chunk = client.recv(4096)
                if not chunk:
                    break
                response += chunk
    except OSError as exc:
        return {
            "name": "web",
            "kind": "service",
            "status": "error",
            "socket_path": str(WEB_SOCKET_PATH),
            "details": str(exc),
        }

    status_line = response.split(b"\r\n", 1)[0].decode("iso-8859-1", errors="replace")
    parts = status_line.split(" ", 2)
    status_code = None
    if len(parts) >= 2 and parts[1].isdigit():
        status_code = int(parts[1])

    elapsed_ms = round((datetime.now(timezone.utc) - started).total_seconds() * 1000, 2)
    status = "ok" if status_code is not None and 200 <= status_code < 400 else "error"

    return {
        "name": "web",
        "kind": "service",
        "status": status,
        "socket_path": str(WEB_SOCKET_PATH),
        "status_line": status_line,
        "status_code": status_code,
        "response_ms": elapsed_ms,
        "details": status_line,
    }


def run_watchdog() -> tuple[int, dict[str, object]]:
    now = datetime.now(timezone.utc)
    job_checks = [_check_job(job, now) for job in JOBS]
    service_checks = [_check_systemd_service(), _check_web_service()]
    checks = [*service_checks, *job_checks]
    failing = [item for item in checks if item["status"] != "ok"]
    payload = {
        "checked_at": _isoformat(now),
        "job_count": len(job_checks),
        "service_count": len(service_checks),
        "failing_count": len(failing),
        "failing_jobs": [item["name"] for item in job_checks if item["status"] != "ok"],
        "failing_services": [item["name"] for item in service_checks if item["status"] != "ok"],
        "status": "ok" if not failing else "error",
        "service_checks": service_checks,
        "job_checks": job_checks,
        "checks": checks,
    }
    return (1 if failing else 0), payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Check that scheduled Montana Blotter jobs are still running.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of plaintext.")
    args = parser.parse_args()

    exit_code, payload = run_watchdog()
    if args.json:
        print(json.dumps(payload, indent=2))
        return exit_code

    checked_at = payload["checked_at"]
    print(
        f"[script_watchdog] checked_at={checked_at} services={payload['service_count']} "
        f"jobs={payload['job_count']} failing={payload['failing_count']}"
    )
    for item in payload["checks"]:
        if item["kind"] == "job":
            age = item["age_hours"]
            age_label = "n/a" if age is None else f"{age:.2f}h"
            print(
                f"- {item['name']}: status={item['status']} cadence={item['cadence']} "
                f"last_seen={item['last_seen_at'] or 'missing'} age={age_label} "
                f"log={item['log_path']}"
            )
            continue

        print(f"- {item['name']}: status={item['status']} {item['details']}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
