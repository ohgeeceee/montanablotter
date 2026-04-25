#!/usr/bin/env python3
"""
Verify scheduled Montana Blotter jobs have produced recent log activity.
"""

from __future__ import annotations

import argparse
import json
import http.client
import socket
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config


ROOT = Path("/root/montanablotter")
SYSTEMD_SERVICE = "montanablotter.service"
AGENT_EVENTS_SERVICE = "montanablotter-agent-events.service"
WEB_SOCKET_PATH = Path("/tmp/montanablotter.sock")
WEB_REQUEST_PATH = "/"
WEB_HOST = "127.0.0.1"
WEB_PORT = 5000


@dataclass(frozen=True)
class MonitoredJob:
    name: str
    log_path: Path
    max_age_hours: float
    cadence: str


@dataclass(frozen=True)
class MonitoredStateJob:
    name: str
    max_age_hours: float
    cadence: str


JOBS: tuple[MonitoredJob, ...] = (
    MonitoredJob("cleanup", ROOT / "cleanup.log", 30, "daily"),
    MonitoredJob("email_worker", ROOT / "mail.log", 2, "every 15 minutes"),
    MonitoredJob("facebook_worker", ROOT / "facebook_worker.log", 2, "every 15 minutes"),
    MonitoredJob("morning_briefing", ROOT / "briefing.log", 30, "daily"),
    MonitoredJob("daily_blog_worker", ROOT / "daily_blog.log", 30, "daily"),
    MonitoredJob("weekly_county_digest", ROOT / "weekly_county_digest.log", 8 * 24, "weekly"),
    MonitoredJob("weekly_safety_report", ROOT / "weekly_safety_report.log", 8 * 24, "weekly"),
    MonitoredJob("charge_explainer_worker", ROOT / "charge_explainer.log", 8 * 24, "weekly"),
    MonitoredJob("weekly_snapshot", ROOT / "weekly_snapshot.log", 8 * 24, "weekly"),
    MonitoredJob("script_watchdog", ROOT / "cron_errors.log", 30, "daily"),
    MonitoredJob("pattern_conversion_report", ROOT / "cron.log", 30, "daily"),
    MonitoredJob("backup_db", ROOT / "backup.log", 26, "daily"),
    MonitoredJob("court_refresh", ROOT / "court_refresh.log", 5, "every 3 hours"),
    MonitoredJob("court_source_alerts", ROOT / "court_source_alerts.log", 2, "every 30 minutes"),
    MonitoredJob("agendas_ingest", ROOT / "agendas_ingest.log", 8, "every 6 hours"),
    MonitoredJob("meeting_source_alerts", ROOT / "meeting_source_alerts.log", 2, "every 30 minutes"),
    MonitoredJob("crimemapping_fetcher", ROOT / "crimemapping.log", 14, "twice daily"),
    MonitoredJob("missoula_public_report_fetcher", ROOT / "missoula_fetcher.log", 2, "hourly"),
    MonitoredJob("whitefish_blotter_fetcher", ROOT / "whitefish_fetcher.log", 8, "every 6 hours"),
    MonitoredJob("jail_booking", ROOT / "jail_booking_ingest.log", 4, "every 2 hours"),
    MonitoredJob("missing_person_watch", ROOT / "missing_person_watch.log", 2, "hourly"),
    MonitoredJob("bozeman_police_calls", ROOT / "bozeman_calls.log", 2, "hourly"),
    MonitoredJob("bozeman_police_crime", ROOT / "bozeman_crime.log", 8, "every 6 hours"),
    MonitoredJob("ingestion_alerts", ROOT / "ingestion_alerts.log", 2, "every 30 minutes"),
    MonitoredJob("news_planner", ROOT / "news_planner.log", 5, "every 3 hours"),
    MonitoredJob("news_writer_agent", ROOT / "news_writer.log", 2, "hourly"),
    MonitoredJob("news_editor_agent", ROOT / "news_editor.log", 2, "hourly"),
)

STATE_JOBS: tuple[MonitoredStateJob, ...] = (
    MonitoredStateJob("jail_booking_ingest_yellowstone", 4, "every 2 hours"),
    MonitoredStateJob("jail_booking_ingest_missoula", 4, "every 2 hours"),
    MonitoredStateJob("jail_booking_ingest_flathead", 4, "every 2 hours"),
    MonitoredStateJob("jail_booking_ingest_jefferson", 4, "every 2 hours"),
    MonitoredStateJob("jail_booking_ingest_sanders", 4, "every 2 hours"),
)

SUCCESS_STATUSES = {"ok", "success"}


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


def _parse_state_time(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        try:
            parsed = datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _check_state_job(job: MonitoredStateJob, now: datetime) -> dict[str, object]:
    db_path = getattr(config, "DB_PATH", str(ROOT / "blotter.db"))
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM scheduled_job_state WHERE job_name = ?",
            (job.name,),
        ).fetchone()
        conn.close()
    except sqlite3.Error as exc:
        return {
            "name": job.name,
            "kind": "job",
            "source": "scheduled_job_state",
            "cadence": job.cadence,
            "status": "error",
            "details": str(exc),
            "max_age_hours": job.max_age_hours,
            "last_seen_at": None,
            "age_hours": None,
        }

    if not row:
        return {
            "name": job.name,
            "kind": "job",
            "source": "scheduled_job_state",
            "cadence": job.cadence,
            "status": "missing",
            "details": "no scheduled_job_state row",
            "max_age_hours": job.max_age_hours,
            "last_seen_at": None,
            "age_hours": None,
        }

    finished_at = _parse_state_time(row["last_finished_at"])
    age_hours = None
    status = "ok"
    if finished_at is None:
        status = "missing"
    else:
        age_hours = round((now - finished_at).total_seconds() / 3600, 2)
        if now - finished_at > timedelta(hours=job.max_age_hours):
            status = "stale"
    last_status = (row["last_status"] or "").strip().lower()
    if last_status not in SUCCESS_STATUSES:
        status = "error"

    return {
        "name": job.name,
        "kind": "job",
        "source": "scheduled_job_state",
        "cadence": job.cadence,
        "status": status,
        "last_status": row["last_status"],
        "exit_code": row["last_exit_code"],
        "max_age_hours": job.max_age_hours,
        "last_seen_at": _isoformat(finished_at),
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


def _check_agent_events_service() -> dict[str, object]:
    try:
        result = subprocess.run(
            [
                "systemctl",
                "show",
                AGENT_EVENTS_SERVICE,
                "--property=ActiveState,SubState,ExecMainPID,ExecMainStatus",
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        return {
            "name": AGENT_EVENTS_SERVICE,
            "kind": "service",
            "status": "missing",
            "details": "systemctl is not installed",
        }
    except subprocess.TimeoutExpired:
        return {
            "name": AGENT_EVENTS_SERVICE,
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
        "name": AGENT_EVENTS_SERVICE,
        "kind": "service",
        "status": status,
        "active_state": active_state,
        "sub_state": sub_state,
        "pid": pid,
        "exit_status": exit_status,
        "details": details,
    }


def _check_web_service() -> dict[str, object]:
    started = datetime.now(timezone.utc)
    try:
        connection = http.client.HTTPConnection(WEB_HOST, WEB_PORT, timeout=5)
        connection.request("HEAD", WEB_REQUEST_PATH, headers={"Host": "montanablotter.com"})
        response = connection.getresponse()
        status_code = response.status
        status_line = f"HTTP/{response.version / 10:.1f} {status_code} {response.reason}"
        response.read()
        connection.close()
    except (OSError, http.client.HTTPException) as exc:
        return {
            "name": "web",
            "kind": "service",
            "status": "error",
            "target": f"http://{WEB_HOST}:{WEB_PORT}{WEB_REQUEST_PATH}",
            "details": str(exc),
        }

    elapsed_ms = round((datetime.now(timezone.utc) - started).total_seconds() * 1000, 2)
    status = "ok" if status_code is not None and 200 <= status_code < 400 else "error"

    return {
        "name": "web",
        "kind": "service",
        "status": status,
        "target": f"http://{WEB_HOST}:{WEB_PORT}{WEB_REQUEST_PATH}",
        "status_line": status_line,
        "status_code": status_code,
        "response_ms": elapsed_ms,
        "details": status_line,
    }


def run_watchdog() -> tuple[int, dict[str, object]]:
    now = datetime.now(timezone.utc)
    job_checks = [
        *[_check_job(job, now) for job in JOBS],
        *[_check_state_job(job, now) for job in STATE_JOBS],
    ]
    service_checks = [_check_systemd_service(), _check_agent_events_service(), _check_web_service()]
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
            source = item.get("source", "log")
            location = item.get("log_path") or source
            print(
                f"- {item['name']}: status={item['status']} cadence={item['cadence']} "
                f"last_seen={item['last_seen_at'] or 'missing'} age={age_label} "
                f"source={location}"
            )
            continue

        print(f"- {item['name']}: status={item['status']} {item['details']}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
