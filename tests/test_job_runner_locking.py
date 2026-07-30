"""Regression tests for job_runner shared-lock waiting semantics."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
JOB_RUNNER = REPO_ROOT / "job_runner.py"

_HOLDER = """
import fcntl
import sys
import time

path = sys.argv[1]
hold_seconds = float(sys.argv[2])
handle = open(path, "w")
fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
print("ready", flush=True)
time.sleep(hold_seconds)
"""


def _start_holder(lock_path: Path, hold_seconds: float) -> subprocess.Popen[str]:
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(lock_path), str(hold_seconds)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "ready"
    return holder


def _run_waiter(
    lock_name: str,
    wait_seconds: int,
    db_path: Path,
) -> tuple[subprocess.CompletedProcess[str], float]:
    started = time.monotonic()
    env = dict(os.environ)
    env["MB_DB_PATH"] = str(db_path)
    completed = subprocess.run(
        [
            sys.executable,
            str(JOB_RUNNER),
            "--name",
            "test_lock_waiter",
            "--shared-lock",
            lock_name,
            "--wait-for-lock",
            str(wait_seconds),
            "--max-instances",
            "1",
            "--",
            "/bin/true",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=max(wait_seconds + 10, 15),
        check=False,
    )
    return completed, time.monotonic() - started


def test_wait_for_shared_lock_then_runs(tmp_path):
    lock_name = "pytest_wait_for_shared_lock"
    lock_path = Path("/tmp") / f"job_runner_{lock_name}_0.lock"
    lock_path.unlink(missing_ok=True)
    holder = _start_holder(lock_path, 1.0)
    try:
        completed, elapsed = _run_waiter(
            lock_name,
            8,
            tmp_path / "runs.db",
        )
    finally:
        holder.wait(timeout=5)
        lock_path.unlink(missing_ok=True)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "skipping" not in completed.stderr
    assert elapsed >= 0.8


def test_shared_lock_timeout_records_skip(tmp_path):
    lock_name = "pytest_skip_shared_lock"
    lock_path = Path("/tmp") / f"job_runner_{lock_name}_0.lock"
    lock_path.unlink(missing_ok=True)
    holder = _start_holder(lock_path, 8.0)
    try:
        completed, elapsed = _run_waiter(
            lock_name,
            1,
            tmp_path / "runs.db",
        )
    finally:
        holder.terminate()
        holder.wait(timeout=5)
        lock_path.unlink(missing_ok=True)

    assert completed.returncode == 0
    assert "skipping 'test_lock_waiter'" in completed.stderr
    assert elapsed >= 1.0
    assert elapsed < 7.0
