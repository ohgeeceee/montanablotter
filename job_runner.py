from __future__ import annotations

import argparse
import fcntl
import os
import shlex
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone

import config
from services.alerts.legacy import collect_alert_recipients, send_plaintext_email
from db import connect_db


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _ensure_schema(conn) -> None:
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS scheduled_job_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_name TEXT NOT NULL,
            command_text TEXT NOT NULL,
            status TEXT NOT NULL,
            exit_code INTEGER,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            duration_seconds REAL,
            log_path TEXT,
            output_excerpt TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
        '''
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS scheduled_job_state (
            job_name TEXT PRIMARY KEY,
            last_status TEXT NOT NULL,
            last_exit_code INTEGER,
            last_started_at TEXT,
            last_finished_at TEXT,
            last_duration_seconds REAL,
            last_output_excerpt TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        )
        '''
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_scheduled_job_runs_lookup '
        'ON scheduled_job_runs(job_name, started_at DESC)'
    )


def _output_excerpt(output: str, *, max_lines: int = 40, max_chars: int = 4000) -> str:
    if not output:
        return ''
    lines = output.strip().splitlines()
    excerpt = '\n'.join(lines[-max_lines:])
    if len(excerpt) > max_chars:
        excerpt = excerpt[-max_chars:]
    return excerpt


# Known persistent failure modes that the system records correctly in
# downstream tables (e.g. court_sources.last_error) but should NOT trigger
# an email blast. Maps job_name -> tuple of substring patterns. When every
# error line in the job's output excerpt matches at least one pattern in
# the tuple, the failure email is suppressed.
#
# The dcportal|coljportal 'ERR_CONNECTION_RESET' lines are the WAF IP block
# documented in project memory. The 'Request Rejected' pattern is the WAF
# rejection path handled gracefully by 34b53690 (colj returns 0 events with
# last_error set, but the per-source failure still bumps exit code to 1).
_KNOWN_FAILURE_SIGNATURES = {
    'court_refresh': (
        'ERR_CONNECTION_RESET at https://dcportal.pubcourts.mt.gov/',
        'ERR_CONNECTION_RESET at https://coljportal.pubcourts.mt.gov/',
        'Request Rejected',
    ),
    'source_reviewer': (
        'Your credit balance is too low',
    ),
    'source_onboarder': (
        'Your credit balance is too low',
    ),
}


def _is_known_failure(job_name: str, output: str) -> bool:
    signatures = _KNOWN_FAILURE_SIGNATURES.get(job_name)
    if not signatures:
        return False
    error_lines = [
        line for line in (output or '').splitlines()
        if 'Page.goto' in line
        or 'Traceback' in line
        or 'Unexpected' in line
        or 'error' in line.lower()
    ]
    if not error_lines:
        return False
    return all(any(sig in line for sig in signatures) for line in error_lines)


def _append_log(log_path: str | None, started_at: str, finished_at: str, command_text: str, output: str, status: str, exit_code: int | None) -> None:
    if not log_path:
        return
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, 'a', encoding='utf-8') as handle:
        handle.write(f"\n[{started_at}] job_start status=pending command={command_text}\n")
        if output:
            handle.write(output.rstrip() + '\n')
        handle.write(f"[{finished_at}] job_finish status={status} exit_code={exit_code}\n")


def _load_previous_state(conn, job_name: str) -> dict | None:
    row = conn.execute(
        'SELECT * FROM scheduled_job_state WHERE job_name = ?',
        (job_name,),
    ).fetchone()
    return dict(row) if row else None


def _record_job_state(conn, *, job_name: str, command_text: str, status: str, exit_code: int | None, started_at: str, finished_at: str, duration_seconds: float, output_excerpt: str, log_path: str | None) -> None:
    conn.execute(
        '''
        INSERT INTO scheduled_job_runs (
            job_name,
            command_text,
            status,
            exit_code,
            started_at,
            finished_at,
            duration_seconds,
            log_path,
            output_excerpt
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            job_name,
            command_text,
            status,
            exit_code,
            started_at,
            finished_at,
            duration_seconds,
            log_path,
            output_excerpt,
        ),
    )
    conn.execute(
        '''
        INSERT INTO scheduled_job_state (
            job_name,
            last_status,
            last_exit_code,
            last_started_at,
            last_finished_at,
            last_duration_seconds,
            last_output_excerpt,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(job_name) DO UPDATE SET
            last_status = excluded.last_status,
            last_exit_code = excluded.last_exit_code,
            last_started_at = excluded.last_started_at,
            last_finished_at = excluded.last_finished_at,
            last_duration_seconds = excluded.last_duration_seconds,
            last_output_excerpt = excluded.last_output_excerpt,
            updated_at = datetime('now')
        ''',
        (
            job_name,
            status,
            exit_code,
            started_at,
            finished_at,
            duration_seconds,
            output_excerpt,
        ),
    )


def _fallback_recipients() -> list[str]:
    recipients = []
    configured = getattr(config, 'ADMIN_ALERT_EMAILS', ()) or ()
    for entry in configured:
        email = (entry or '').strip().lower()
        if email and '@' in email and email not in recipients:
            recipients.append(email)
    fallback = (getattr(config, 'SMTP_USER', '') or '').strip().lower()
    if fallback and '@' in fallback and fallback not in recipients:
        recipients.append(fallback)
    return recipients


def _maybe_send_alert(conn, *, previous_state: dict | None, job_name: str, command_text: str, status: str, exit_code: int | None, started_at: str, finished_at: str, duration_seconds: float, output_excerpt: str) -> None:
    previous_status = (previous_state or {}).get('last_status')
    should_send_failure = status != 'ok' and previous_status != status
    should_send_recovery = status == 'ok' and previous_status and previous_status != 'ok'
    if not (should_send_failure or should_send_recovery):
        return

    if should_send_failure and _is_known_failure(job_name, output_excerpt):
        # Persistent infrastructure condition (e.g., WAF IP block on
        # dcportal|coljportal). The downstream source tables already record
        # the failure with last_error, so the operator-visible signal is
        # preserved; we just don't blast email on every state reset.
        return

    try:
        recipients = collect_alert_recipients(conn)
    except Exception:
        recipients = _fallback_recipients()
    if not recipients:
        return

    if should_send_failure:
        subject = f"[Montana Blotter] Scheduled job failed: {job_name}"
        body = (
            f"Job: {job_name}\n"
            f"Status: {status}\n"
            f"Exit code: {exit_code}\n"
            f"Started: {started_at}\n"
            f"Finished: {finished_at}\n"
            f"Duration seconds: {duration_seconds:.2f}\n"
            f"Command: {command_text}\n\n"
            f"Recent output:\n{output_excerpt or '(no output captured)'}\n"
        )
        send_plaintext_email(recipients, subject, body)
        return

    subject = f"[Montana Blotter] Scheduled job recovered: {job_name}"
    body = (
        f"Job: {job_name}\n"
        f"Status: ok\n"
        f"Previous status: {previous_status}\n"
        f"Exit code: {exit_code}\n"
        f"Started: {started_at}\n"
        f"Finished: {finished_at}\n"
        f"Duration seconds: {duration_seconds:.2f}\n"
        f"Command: {command_text}\n\n"
        f"Recent output:\n{output_excerpt or '(no output captured)'}\n"
    )
    send_plaintext_email(recipients, subject, body)


def _run_command(command: list[str], *, workdir: str | None, timeout: int | None, job_name: str | None = None) -> tuple[str, int | None, str]:
    # Propagate JOB_RUN_ID so services/llm_instrument.py can attribute
    # anthropic.messages.create calls back to the cron job that fired them.
    child_env = {**os.environ, "JOB_RUN_ID": job_name} if job_name else None
    try:
        completed = subprocess.run(
            command,
            cwd=workdir or None,
            env=child_env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        output = (completed.stdout or '') + (completed.stderr or '')
        return 'ok' if completed.returncode == 0 else 'failed', completed.returncode, output
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or '') + (exc.stderr or '')
        output = (output or '') + f"\nTimed out after {timeout} seconds."
        return 'timeout', 124, output
    except FileNotFoundError as exc:
        return 'failed', 127, str(exc)
    except Exception:
        return 'failed', 1, traceback.format_exc()


def _record_skipped_run(job_name: str, command: list[str], log_path: str | None, reason: str) -> None:
    """Persist an observable trace when lock contention skips a job.

    Writes a 'skipped' row to scheduled_job_runs (and the job log file when
    configured) so silent skips show up in run history. Best-effort: a DB
    failure is reported on stderr but never changes the skip-path exit code.
    scheduled_job_state is intentionally left untouched — it tracks the last
    real run, and a skip should not mask it or trigger state-change alerts.
    """
    command_text = ' '.join(shlex.quote(part) for part in command)
    now = _iso_now()
    _append_log(log_path, now, now, command_text, reason + '\n', 'skipped', None)
    try:
        conn = connect_db()
        try:
            _ensure_schema(conn)
            conn.execute(
                '''
                INSERT INTO scheduled_job_runs (
                    job_name,
                    command_text,
                    status,
                    exit_code,
                    started_at,
                    finished_at,
                    duration_seconds,
                    log_path,
                    output_excerpt
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    job_name,
                    command_text,
                    'skipped',
                    None,
                    now,
                    now,
                    0.0,
                    log_path,
                    reason,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        sys.stderr.write(f"job_runner warning: failed to record skipped run: {exc}\n")


def _acquire_lock(job_name: str, max_instances: int) -> 'list[object]':
    """Acquire up to max_instances flock-based slots.  Returns held file objects (keep alive).

    Raises BlockingIOError if all slots are already held by running instances.
    Uses max_instances lock files: /tmp/job_runner_<name>_<slot>.lock
    """
    lock_dir = '/tmp'
    held = []
    for slot in range(max_instances):
        path = os.path.join(lock_dir, f'job_runner_{job_name}_{slot}.lock')
        fh = open(path, 'w')
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held.append(fh)
            return held  # got a free slot
        except BlockingIOError:
            fh.close()
    raise BlockingIOError(f"all {max_instances} instance(s) of '{job_name}' already running")


def main() -> int:
    parser = argparse.ArgumentParser(description='Run a scheduled job with DB-backed state and email alerts.')
    parser.add_argument('--name', required=True, help='Stable job name for run history and alerts.')
    parser.add_argument('--log', default='', help='Optional log file to append command output.')
    parser.add_argument('--workdir', default='', help='Optional working directory for the child process.')
    parser.add_argument('--timeout', type=int, default=0, help='Optional timeout in seconds.')
    parser.add_argument('--max-instances', type=int, default=1,
                        help='Maximum concurrent instances of this job (default 1 = skip if already running).')
    parser.add_argument('--shared-lock', default='',
                        help='Optional shared flock name. Jobs with the same shared lock do not overlap.')
    parser.add_argument('--wait-for-lock', type=int, default=0,
                        help='Seconds to wait for the lock to free before skipping. '
                             '0 = skip immediately (default). Useful when a slow sibling '
                             'job (e.g. email_worker) holds the same shared lock for several '
                             'minutes; short-lived ingest jobs queue and run instead of '
                             'dropping their window.')
    parser.add_argument('command', nargs=argparse.REMAINDER, help='Command to execute after --')
    args = parser.parse_args()

    command = list(args.command)
    if command and command[0] == '--':
        command = command[1:]
    if not command:
        parser.error('a child command is required after --')

    # Determine which lock name to use: shared-lock overrides job name
    lock_name = args.shared_lock if args.shared_lock else args.name

    lock_handles: list[object] = []
    if args.max_instances > 0:
        waited = 0
        poll_interval = 5
        while True:
            try:
                lock_handles = _acquire_lock(lock_name, args.max_instances)
                break
            except BlockingIOError as exc:
                if args.wait_for_lock <= 0 or waited >= args.wait_for_lock:
                    reason = f"job_runner: skipping '{args.name}' — {exc}"
                    sys.stderr.write(reason + '\n')
                    _record_skipped_run(args.name, command, (args.log or '').strip() or None, reason)
                    return 0
                time.sleep(poll_interval)
                waited += poll_interval

    command_text = ' '.join(shlex.quote(part) for part in command)
    started_at = _iso_now()
    start_ts = datetime.now(timezone.utc)
    status, exit_code, output = _run_command(
        command,
        workdir=(args.workdir or '').strip() or None,
        timeout=args.timeout or None,
        job_name=args.name,
    )
    finished_at = _iso_now()
    duration_seconds = (datetime.now(timezone.utc) - start_ts).total_seconds()
    excerpt = _output_excerpt(output)
    _append_log(args.log or None, started_at, finished_at, command_text, output, status, exit_code)

    db_error = None
    try:
        conn = connect_db()
        try:
            _ensure_schema(conn)
            previous_state = _load_previous_state(conn, args.name)
            _record_job_state(
                conn,
                job_name=args.name,
                command_text=command_text,
                status=status,
                exit_code=exit_code,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=duration_seconds,
                output_excerpt=excerpt,
                log_path=args.log or None,
            )
            _maybe_send_alert(
                conn,
                previous_state=previous_state,
                job_name=args.name,
                command_text=command_text,
                status=status,
                exit_code=exit_code,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=duration_seconds,
                output_excerpt=excerpt,
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        db_error = str(exc)

    if db_error:
        sys.stderr.write(f"job_runner warning: failed to persist state: {db_error}\n")

    for fh in lock_handles:
        try:
            fh.close()
        except Exception:
            pass

    return exit_code or 0


if __name__ == '__main__':
    raise SystemExit(main())
