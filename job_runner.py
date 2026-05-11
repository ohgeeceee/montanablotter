from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
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


def _run_command(command: list[str], *, workdir: str | None, timeout: int | None) -> tuple[str, int | None, str]:
    try:
        completed = subprocess.run(
            command,
            cwd=workdir or None,
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


def main() -> int:
    parser = argparse.ArgumentParser(description='Run a scheduled job with DB-backed state and email alerts.')
    parser.add_argument('--name', required=True, help='Stable job name for run history and alerts.')
    parser.add_argument('--log', default='', help='Optional log file to append command output.')
    parser.add_argument('--workdir', default='', help='Optional working directory for the child process.')
    parser.add_argument('--timeout', type=int, default=0, help='Optional timeout in seconds.')
    parser.add_argument('command', nargs=argparse.REMAINDER, help='Command to execute after --')
    args = parser.parse_args()

    command = list(args.command)
    if command and command[0] == '--':
        command = command[1:]
    if not command:
        parser.error('a child command is required after --')

    command_text = ' '.join(shlex.quote(part) for part in command)
    started_at = _iso_now()
    start_ts = datetime.now(timezone.utc)
    status, exit_code, output = _run_command(
        command,
        workdir=(args.workdir or '').strip() or None,
        timeout=args.timeout or None,
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

    return exit_code or 0


if __name__ == '__main__':
    raise SystemExit(main())
