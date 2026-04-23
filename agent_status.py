import os
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from typing import Any

import psutil
from redis import Redis
from rq import Queue

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.getenv("MB_DB_PATH", os.path.join(BASE_DIR, "blotter.db"))

REDIS_HOST = os.getenv("MB_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("MB_REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("MB_REDIS_DB", "0"))
DEAD_LETTER_ALERT_THRESHOLD = int(os.getenv("MB_DEAD_LETTER_ALERT_THRESHOLD", "5"))
DEAD_LETTER_ALERT_WINDOW_HOURS = int(os.getenv("MB_DEAD_LETTER_ALERT_WINDOW_HOURS", "24"))


def utcnow() -> datetime:
    return datetime.now(UTC)


def redis_conn() -> Redis:
    return Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=False)


def queue_snapshot() -> list[dict[str, Any]]:
    conn = redis_conn()
    snapshots: list[dict[str, Any]] = []

    for name in ["ingestion", "parsing", "publishing"]:
        q = Queue(name, connection=conn)
        try:
            failed_count = len(q.failed_job_registry)
        except Exception:
            failed_count = None

        row: dict[str, Any] = {
            "name": name,
            "count": q.count,
            "failed_count": failed_count,
        }
        snapshots.append(row)

    return snapshots


def systemd_is_active(service_name: str) -> bool:
    result = subprocess.run(
        ["systemctl", "is-active", service_name],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "active"


def service_status_snapshot() -> list[dict[str, Any]]:
    services = [
        {"label": "Web App", "service": "montanablotter.service"},
        {"label": "Nginx", "service": "nginx.service"},
        # redis.service is often an alias on Ubuntu; check redis-server too.
        {"label": "Redis", "service": "redis.service", "fallback_service": "redis-server.service"},
        {"label": "RQ Ingestion", "service": "montanablotter-rq-ingestion.service"},
        {"label": "RQ Parsing", "service": "montanablotter-rq-parsing.service"},
        {"label": "RQ Publishing", "service": "montanablotter-rq-publishing.service"},
    ]

    rows: list[dict[str, Any]] = []
    for item in services:
        primary = item["service"]
        fallback = item.get("fallback_service")
        active = systemd_is_active(primary)
        resolved_service = primary
        if not active and fallback:
            active = systemd_is_active(str(fallback))
            if active:
                resolved_service = str(fallback)
        rows.append(
            {
                "label": item["label"],
                "service": primary,
                "resolved_service": resolved_service,
                "active": active,
            }
        )
    return rows


def rq_worker_process_snapshot() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    now = utcnow()
    for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
        try:
            cmdline = proc.info.get("cmdline") or []
            if not cmdline:
                continue
            cmd_text = " ".join(str(part) for part in cmdline).lower()
            if "rq" not in cmd_text or "worker" not in cmd_text:
                continue
            started_at = datetime.fromtimestamp(float(proc.info.get("create_time") or 0), tz=UTC)
            uptime_seconds = max(0, int((now - started_at).total_seconds()))
            rows.append(
                {
                    "pid": int(proc.info["pid"]),
                    "name": proc.info.get("name") or "",
                    "cmdline": " ".join(str(part) for part in cmdline)[:300],
                    "uptime_seconds": uptime_seconds,
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, ValueError):
            continue
    return rows


def job_failures_snapshot(limit: int = 10) -> dict[str, Any]:
    if not os.path.exists(DB_PATH):
        return {
            "db_exists": False,
            "failed_last_24h": 0,
            "recent": [],
            "dead_letters_last_window": 0,
            "dead_letter_recent": [],
        }

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        cutoff = (utcnow() - timedelta(hours=24)).isoformat()
        failed_last_24h = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM jobs
            WHERE status = 'failed'
              AND COALESCE(finished_at, started_at, created_at, '') >= ?
            """,
            (cutoff,),
        ).fetchone()["c"]

        recent_rows = conn.execute(
            """
            SELECT job_id, queue_name, task_name, source_key, error_text, finished_at, started_at
            FROM jobs
            WHERE status = 'failed'
            ORDER BY COALESCE(finished_at, started_at, created_at) DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        dead_letters_last_window = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM dead_letters
            WHERE failed_at >= ?
            """,
            ((utcnow() - timedelta(hours=max(1, DEAD_LETTER_ALERT_WINDOW_HOURS))).isoformat(),),
        ).fetchone()["c"]
        dead_letter_recent_rows = conn.execute(
            """
            SELECT job_id, queue_name, task_name, source_key, error_text, failed_at
            FROM dead_letters
            ORDER BY failed_at DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    except sqlite3.OperationalError:
        return {
            "db_exists": True,
            "failed_last_24h": 0,
            "recent": [],
            "dead_letters_last_window": 0,
            "dead_letter_recent": [],
        }
    finally:
        conn.close()

    recent = []
    for row in recent_rows:
        recent.append(
            {
                "job_id": row["job_id"],
                "queue_name": row["queue_name"],
                "task_name": row["task_name"],
                "source_key": row["source_key"],
                "finished_at": row["finished_at"] or row["started_at"],
                "error_text": (row["error_text"] or "")[:300],
            }
        )

    dead_letter_recent = []
    for row in dead_letter_recent_rows:
        dead_letter_recent.append(
            {
                "job_id": row["job_id"],
                "queue_name": row["queue_name"],
                "task_name": row["task_name"],
                "source_key": row["source_key"],
                "failed_at": row["failed_at"],
                "error_text": (row["error_text"] or "")[:300],
            }
        )

    return {
        "db_exists": True,
        "failed_last_24h": int(failed_last_24h),
        "recent": recent,
        "dead_letters_last_window": int(dead_letters_last_window),
        "dead_letter_recent": dead_letter_recent,
    }


def system_snapshot() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "captured_at": utcnow().isoformat(),
        "queues": [],
        "services": [],
        "rq_workers": [],
        "jobs": {"db_exists": False, "failed_last_24h": 0, "recent": []},
        "alerts": [],
    }

    try:
        payload["queues"] = queue_snapshot()
    except Exception as exc:
        payload["alerts"].append(
            {
                "type": "queue_snapshot_error",
                "severity": "critical",
                "title": "Queue snapshot failed",
                "message": f"Could not read RQ queue metrics: {exc}",
            }
        )

    payload["services"] = service_status_snapshot()
    payload["rq_workers"] = rq_worker_process_snapshot()
    payload["jobs"] = job_failures_snapshot(limit=10)

    for svc in payload["services"]:
        if not svc.get("active"):
            payload["alerts"].append(
                {
                    "type": "service_down",
                    "severity": "critical",
                    "title": f"Service down: {svc.get('label')}",
                    "message": f"{svc.get('resolved_service') or svc.get('service')} is not active.",
                }
            )

    for queue in payload["queues"]:
        failed_count = queue.get("failed_count")
        if isinstance(failed_count, int) and failed_count > 0:
            payload["alerts"].append(
                {
                    "type": "queue_failed_jobs",
                    "severity": "warning",
                    "title": f"Failed jobs in {queue.get('name')}",
                    "message": f"{failed_count} jobs in failed registry.",
                }
            )

    failed_24h = int(payload["jobs"].get("failed_last_24h") or 0)
    if failed_24h > 0:
        payload["alerts"].append(
            {
                "type": "jobs_failed_recently",
                "severity": "warning",
                "title": "Recent task failures",
                "message": f"{failed_24h} failed jobs in the last 24 hours.",
            }
        )

    dead_letters_window = int(payload["jobs"].get("dead_letters_last_window") or 0)
    if dead_letters_window >= DEAD_LETTER_ALERT_THRESHOLD:
        severity = "critical" if dead_letters_window >= (DEAD_LETTER_ALERT_THRESHOLD * 2) else "warning"
        payload["alerts"].append(
            {
                "type": "dead_letters_threshold",
                "severity": severity,
                "title": "Dead-letter threshold reached",
                "message": (
                    f"{dead_letters_window} dead-letter events in last "
                    f"{DEAD_LETTER_ALERT_WINDOW_HOURS}h (threshold {DEAD_LETTER_ALERT_THRESHOLD})."
                ),
            }
        )

    return payload
