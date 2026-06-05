#!/usr/bin/env python3
"""
Daily dataset metrics refresh job.

Run from cron via job_runner.py so overlapping executions are serialized and
the refresh stays idempotent.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import traceback

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.chdir(PROJECT_ROOT)

from db import get_db
from services.datasets.refresh import DEFAULT_DATASET_SLUGS, file_lock, refresh_all_dataset_metrics


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh cached Montana Blotter dataset metrics.")
    parser.add_argument(
        "--lock",
        default="/root/montanablotter/logs/datasets_refresh.lock",
        help="Path to the lock file used to prevent overlapping refreshes.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    started = time.monotonic()
    conn = None
    try:
        with file_lock(args.lock):
            conn = get_db()
            refresh_all_dataset_metrics(conn, DEFAULT_DATASET_SLUGS)
            conn.commit()
        elapsed = time.monotonic() - started
        print(
            f"datasets_refresh ok slugs={len(DEFAULT_DATASET_SLUGS)} "
            f"elapsed_seconds={elapsed:.2f} lock={args.lock}"
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"datasets_refresh failed: {exc}", file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)
        return 1
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
