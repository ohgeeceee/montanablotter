"""
Tests for services.ops.cron_introspection — parses crontab.txt and returns the
enabled cron jobs plus their log file paths and expected cadence.

This is the single source of truth for "what does cron actually do?" so the
watchdog can stay in sync with the live crontab.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import services.ops.cron_introspection as cron


SAMPLE_CRONTAB = """\
# Comment line
SHELL=/bin/bash
BASH_ENV=/root/montanablotter/.env

*/3 * * * * /root/montanablotter/scripts/ops/healthcheck_restart.sh
*/15 * * * * /root/montanablotter/venv/bin/python3 /root/montanablotter/email_worker.py --mode queue
0 */6 * * * /root/montanablotter/venv/bin/python3 /root/montanablotter/ingestion/run_all_scrapers.py
30 5 * * * /root/montanablotter/venv/bin/python3 /root/montanablotter/daily_blog_worker.py
"""


@pytest.fixture
def crontab_path(tmp_path, monkeypatch):
    path = tmp_path / "crontab.txt"
    path.write_text(SAMPLE_CRONTAB)
    monkeypatch.setattr(cron, "DEFAULT_CRONTAB_PATH", str(path))
    return path


def test_parse_returns_active_jobs(crontab_path):
    jobs = cron.parse_crontab(str(crontab_path))
    names = {j.name for j in jobs}
    # Healthcheck + email_worker + run_all_scrapers + daily_blog_worker.
    assert "healthcheck_restart" in names
    assert "email_worker" in names
    assert "run_all_scrapers" in names
    assert "daily_blog_worker" in names
    # SHELL/BASH_ENV lines are not jobs.
    assert "SHELL" not in names
    assert "BASH_ENV" not in names


def test_parse_extracts_log_path_from_job_runner(crontab_path):
    """When jobs use job_runner.py, log path is the --log argument."""
    crontab = """\
*/15 * * * * /root/montanablotter/venv/bin/python3 /root/montanablotter/job_runner.py --name email_worker --log /root/montanablotter/logs/mail.log -- /root/montanablotter/venv/bin/python3 /root/montanablotter/email_worker.py
"""
    p = Path(crontab_path).parent / "tab2.txt"
    p.write_text(crontab)
    jobs = cron.parse_crontab(str(p))
    assert len(jobs) == 1
    assert jobs[0].name == "email_worker"
    assert jobs[0].log_path == "/root/montanablotter/logs/mail.log"


def test_parse_extracts_log_path_from_redirect(crontab_path):
    """When jobs use >> redirect, the log path is the file after >>."""
    crontab = """\
*/15 * * * * /root/montanablotter/venv/bin/python3 /root/montanablotter/script.py >> /root/montanablotter/logs/watcher.log 2>&1
"""
    p = Path(crontab_path).parent / "tab3.txt"
    p.write_text(crontab)
    jobs = cron.parse_crontab(str(p))
    assert len(jobs) == 1
    assert jobs[0].log_path == "/root/montanablotter/logs/watcher.log"


def test_parse_ignores_blank_and_comment_lines(crontab_path):
    crontab = """\

# this is a comment
   # indented comment
*/5 * * * * /root/montanablotter/venv/bin/python3 /root/montanablotter/foo.py
"""
    p = Path(crontab_path).parent / "tab4.txt"
    p.write_text(crontab)
    jobs = cron.parse_crontab(str(p))
    assert len(jobs) == 1
    assert jobs[0].name == "foo"


def test_well_known_name_map(crontab_path):
    """The parser should know the well-known script -> log mapping for the
    current crontab so watchdog entries can be auto-derived."""
    mapping = cron.well_known_jobs()
    # Spot check a few known entries.
    assert "email_worker" in mapping
    assert mapping["email_worker"]["log_path"] == "/root/montanablotter/logs/mail.log"
    assert "daily_blog_worker" in mapping
    assert mapping["daily_blog_worker"]["max_age_hours"] == 30


def test_drifts_returns_jobs_in_crontab_not_watched(crontab_path, monkeypatch):
    """drift() reports enabled cron jobs the watchdog does not watch."""
    # Force an empty watchdog.
    monkeypatch.setattr(cron, "get_watchdog_job_names", lambda: set())
    drifts = cron.drift()
    names = {d["name"] for d in drifts}
    assert "run_all_scrapers" in names or "daily_blog_worker" in names
    assert all(d["enabled_in_crontab"] for d in drifts)
