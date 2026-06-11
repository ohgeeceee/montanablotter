"""
The watchdog JOBS tuple must stay in sync with crontab.txt. These tests read
both and assert the overlap is sane. When you add a cron job, you add it to
services/ops/cron_introspection.well_known_jobs() too.
"""
from __future__ import annotations

import pytest

import services.ops.cron_introspection as cron
import services.ops.watchdog as watchdog


def test_well_known_jobs_all_have_log_paths():
    for name, info in cron.well_known_jobs().items():
        assert info.get("log_path"), f"{name} missing log_path"
        assert info.get("max_age_hours"), f"{name} missing max_age_hours"


def test_well_known_jobs_match_crontab():
    """The set of well-known jobs should be a superset of what's in crontab.

    This is the structural assertion that catches drift: if you add a cron
    job, you must add it here. If you remove a cron job, you must remove it
    from here too.

    Exception: `healthcheck_restart` is silent-on-success (only logs on
    failure) so its log mtime is not a useful freshness signal and we
    intentionally do not watch it. The liveness signal comes from the
    systemd service check on montanablotter.service.
    """
    crontab_names = {j.name for j in cron.parse_crontab()}
    known_names = set(cron.well_known_jobs().keys())
    # Silent-success jobs we deliberately do not watch.
    skipped = {"healthcheck_restart"}

    missing = crontab_names - known_names - skipped
    assert not missing, f"cron jobs not in well_known_jobs map: {sorted(missing)}"


def test_watchdog_includes_all_live_cron_jobs():
    """Each cron job with a well_known entry must be in watchdog.JOBS.

    This is the contract that prevents the original "watchdog screams about
    retired jobs" failure mode from coming back.
    """
    crontab_names = {j.name for j in cron.parse_crontab()}
    known = cron.well_known_jobs()
    watchdog_names = {job.name for job in watchdog.JOBS}

    live_jobs = {n for n in crontab_names if n in known}
    unwatched = live_jobs - watchdog_names
    assert not unwatched, (
        f"watchdog does not monitor these live cron jobs: {sorted(unwatched)}"
    )
