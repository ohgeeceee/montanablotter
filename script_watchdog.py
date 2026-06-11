#!/usr/bin/env python3
"""Wrapper to run the ops watchdog from the repo root so imports resolve."""
from services.ops import watchdog as _watchdog
from services.ops.watchdog import main

# Re-export for legacy callers (e.g. tests/test_news_planner.py) that
# import this wrapper module and read JOBS / STATE_JOBS directly.
JOBS = _watchdog.JOBS
STATE_JOBS = _watchdog.STATE_JOBS
ROOT = _watchdog.ROOT
LOGS = _watchdog.LOGS

if __name__ == "__main__":
    raise SystemExit(main())
