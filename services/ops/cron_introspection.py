"""
Parse crontab.txt and return the active jobs.

The motivation: cron entries and watchdog entries drift. This module gives us
one canonical place to ask "what does cron actually do?" and from that derive
watchdog entries and detect drift.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

# Repo root is the parent of this file's parent.
DEFAULT_CRONTAB_PATH = "/root/montanablotter/crontab.txt"
REPO_ROOT = "/root/montanablotter"

# Regex for a cron schedule field: 5 space-separated fields.
SCHEDULE_RE = re.compile(
    r"^\s*([\*\d\/\,\-\+]+)(\s+[\*\d\/\,\-\+]+){4}\s+(.+?)\s*$"
)
# job_runner.py wraps include a `--log <path>` flag we can extract.
JOB_RUNNER_LOG_RE = re.compile(r"--log\s+(\S+)")
# A simple shell `>> path` or `> path` redirect for non-job-runner jobs.
REDIRECT_RE = re.compile(r">>?\s*(\S+?)(?:\s+2>&1)?\s*$")
# Schedule fields.
SCHEDULE_HEAD_RE = re.compile(r"^(\S+\s+\S+\s+\S+\s+\S+\s+\S+)\s+(.+?)\s*$")


@dataclass(frozen=True)
class CronJob:
    name: str
    schedule: str
    command: str
    log_path: str | None = None
    # Cadence label, best-effort.
    cadence: str = "unknown"


def _script_name_from_path(path: str) -> str:
    """Pull a stable, human-friendly name out of a command path."""
    p = Path(path)
    base = p.name
    for ext in (".py", ".sh"):
        if base.endswith(ext):
            return base[: -len(ext)]
    return base


def _extract_log_path(command: str) -> str | None:
    if "--log" in command:
        m = JOB_RUNNER_LOG_RE.search(command)
        if m:
            return m.group(1)
    # Look for `>> /path/to/log`.
    if ">>" in command:
        m = re.search(r">>\s*(\S+)", command)
        if m:
            return m.group(1)
    if ">" in command and ">>" not in command:
        m = re.search(r"(?<!>)>\s*(\S+)", command)
        if m:
            return m.group(1)
    return None


def _extract_name(command: str) -> str:
    """Decide on a job name based on command contents.

    Order:
      1. If --name <X> is in the command (job_runner wrapper), use X.
      2. Otherwise the basename of the last script path on the command line.
    """
    m = re.search(r"--name\s+([\w\-]+)", command)
    if m:
        return m.group(1)
    # Pull out paths to *.py or executable scripts and pick the last one
    # (the actual script, not job_runner.py wrapping it).
    paths = re.findall(r"(/[\w\-\./]+\.(?:py|sh))", command)
    if not paths:
        return "unknown"
    # Filter out job_runner.py; we want the wrapped script.
    candidates = [p for p in paths if not p.endswith("job_runner.py")]
    target = candidates[-1] if candidates else paths[-1]
    return _script_name_from_path(target)


def _classify_cadence(schedule: str) -> str:
    """Best-effort cadence label for display."""
    parts = schedule.split()
    if len(parts) != 5:
        return "unknown"
    minute, hour, dom, month, dow = parts
    if minute.startswith("*/") and hour == "*":
        return f"every {minute[2:]} minutes"
    if minute == "0" and hour.startswith("*/"):
        return f"every {hour[2:]} hours"
    if minute.isdigit() and hour.isdigit():
        return f"daily at {hour.zfill(2)}:{minute.zfill(2)}"
    if dom == "*" and month == "*" and dow == "*":
        return f"daily at minute {minute}"
    return "scheduled"


def parse_crontab(path: str | None = None) -> list[CronJob]:
    """Parse a crontab file and return the enabled jobs."""
    out: list[CronJob] = []
    resolved = path if path is not None else DEFAULT_CRONTAB_PATH
    text = Path(resolved).read_text() if Path(resolved).exists() else ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Skip env-var assignments like SHELL=/bin/bash
        if re.match(r"^[A-Z_][A-Z0-9_]*=", line):
            continue
        m = SCHEDULE_HEAD_RE.match(line)
        if not m:
            continue
        schedule, command = m.group(1), m.group(2)
        name = _extract_name(command)
        log = _extract_log_path(command)
        cadence = _classify_cadence(schedule)
        out.append(
            CronJob(
                name=name,
                schedule=schedule,
                command=command,
                log_path=log,
                cadence=cadence,
            )
        )
    return out


# Well-known job -> log path + watchdog threshold. Updated by hand as jobs
# are added/removed. Used to seed the watchdog and to detect drift.
def well_known_jobs() -> dict[str, dict]:
    return {
        "email_worker": {
            "log_path": f"{REPO_ROOT}/logs/mail.log",
            "max_age_hours": 2,
            "cadence": "every 15 minutes",
        },
        "email_image_blotter": {
            "log_path": f"{REPO_ROOT}/logs/email_image_blotter.log",
            "max_age_hours": 2,
            "cadence": "every 15 minutes",
        },
        "disposition_watcher": {
            "log_path": f"{REPO_ROOT}/logs/disposition_watcher.log",
            "max_age_hours": 2,
            "cadence": "every 15 minutes",
        },
        "check_ads_health": {
            "log_path": f"{REPO_ROOT}/logs/ad_health.log",
            "max_age_hours": 4,
            "cadence": "every 15 minutes",
        },
        "meeting_source_alerts": {
            "log_path": f"{REPO_ROOT}/logs/meeting_source_alerts.log",
            "max_age_hours": 2,
            "cadence": "every 30 minutes",
        },
        "agendas_ingest": {
            "log_path": f"{REPO_ROOT}/logs/agendas_ingest.log",
            "max_age_hours": 8,
            "cadence": "every 6 hours",
        },
        "datasets_refresh": {
            "log_path": f"{REPO_ROOT}/logs/datasets_refresh.log",
            "max_age_hours": 24,
            "cadence": "daily",
        },
        "run_all_scrapers": {
            "log_path": f"{REPO_ROOT}/logs/run_all_scrapers.log",
            "max_age_hours": 8,
            "cadence": "every 6 hours",
        },
        "daily_blog_worker": {
            "log_path": f"{REPO_ROOT}/logs/daily_blog.log",
            "max_age_hours": 30,
            "cadence": "daily",
        },
        "backup_db": {
            "log_path": f"{REPO_ROOT}/logs/backup.log",
            "max_age_hours": 26,
            "cadence": "daily",
        },
        "court_refresh": {
            "log_path": f"{REPO_ROOT}/logs/court_refresh.log",
            "max_age_hours": 8,
            "cadence": "every 6 hours",
        },
        "news_planner": {
            "log_path": f"{REPO_ROOT}/logs/news_planner.log",
            "max_age_hours": 5,
            "cadence": "every 3 hours",
        },
        "news_writer_agent": {
            "log_path": f"{REPO_ROOT}/logs/news_writer.log",
            "max_age_hours": 5,
            "cadence": "every 3 hours",
        },
        "news_editor_agent": {
            "log_path": f"{REPO_ROOT}/logs/news_editor.log",
            "max_age_hours": 5,
            "cadence": "every 3 hours",
        },
    }


def get_watchdog_job_names() -> set[str]:
    """Return the set of names currently in services.ops.watchdog.JOBS."""
    try:
        from services.ops import watchdog as w
    except Exception:
        return set()
    return {job.name for job in w.JOBS}


def drift() -> list[dict]:
    """Return crontab jobs that the watchdog does not know about."""
    crontab = parse_crontab()
    watched = get_watchdog_job_names()
    known = set(well_known_jobs().keys())
    rows: list[dict] = []
    for job in crontab:
        rows.append(
            {
                "name": job.name,
                "schedule": job.schedule,
                "cadence": job.cadence,
                "log_path": job.log_path,
                "enabled_in_crontab": True,
                "watched_by_watchdog": job.name in watched,
                "in_well_known_map": job.name in known,
            }
        )
    return rows
