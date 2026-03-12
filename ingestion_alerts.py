#!/usr/bin/env python3
"""
Admin alerts for stale or failing ingestion sources.
"""

from __future__ import annotations

import argparse
import smtplib
import sqlite3
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from typing import Iterable

import config
from ingestion_monitoring import build_ingestion_health_dashboard


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=float(getattr(config, 'DB_TIMEOUT_SECONDS', 30)))
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute(f"PRAGMA busy_timeout = {int(getattr(config, 'DB_BUSY_TIMEOUT_MS', 30000))}")
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS ingestion_source_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            alert_kind TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'open',
            summary TEXT,
            first_detected_at TEXT DEFAULT (datetime('now')),
            last_detected_at TEXT DEFAULT (datetime('now')),
            last_sent_at TEXT,
            resolved_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        '''
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_ingestion_source_alerts_open '
        'ON ingestion_source_alerts(source_type, alert_kind, state)'
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_ingestion_source_alerts_updated '
        'ON ingestion_source_alerts(updated_at)'
    )
    conn.commit()


def _app_setting_int(conn: sqlite3.Connection, key: str, default: int) -> int:
    try:
        row = conn.execute(
            'SELECT value FROM app_settings WHERE key = ?',
            (key,),
        ).fetchone()
    except sqlite3.DatabaseError:
        return default
    if not row or row['value'] in (None, ''):
        return default
    try:
        return int(row['value'])
    except (TypeError, ValueError):
        return default


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _collect_recipients(conn: sqlite3.Connection) -> list[str]:
    recipients: list[str] = []

    configured = getattr(config, 'ADMIN_ALERT_EMAILS', ()) or ()
    if isinstance(configured, str):
        configured = [part.strip() for part in configured.split(',') if part.strip()]
    for entry in configured:
        email = (entry or '').strip().lower()
        if email and '@' in email and email not in recipients:
            recipients.append(email)

    try:
        user_columns = {
            row['name']
            for row in conn.execute("PRAGMA table_info('users')").fetchall()
        }
        if 'email' in user_columns:
            for row in conn.execute(
                """
                SELECT DISTINCT lower(email) AS email
                FROM users
                WHERE membership = 'pro'
                  AND email IS NOT NULL
                  AND trim(email) != ''
                """
            ).fetchall():
                email = (row['email'] or '').strip().lower()
                if email and '@' in email and email not in recipients:
                    recipients.append(email)
    except sqlite3.DatabaseError:
        pass

    fallback = (getattr(config, 'SMTP_USER', '') or '').strip().lower()
    if fallback and '@' in fallback and fallback not in recipients:
        recipients.append(fallback)

    return recipients


def _send_plaintext_email(recipients: Iterable[str], subject: str, body: str) -> bool:
    smtp_user = (getattr(config, 'SMTP_USER', '') or '').strip()
    smtp_password = (getattr(config, 'SMTP_PASSWORD', '') or '').strip()
    smtp_server = (getattr(config, 'SMTP_SERVER', '') or '').strip()
    smtp_port = int(getattr(config, 'SMTP_PORT', 587) or 587)
    target_list = [value.strip().lower() for value in recipients if value and '@' in value]
    if not target_list or not (smtp_user and smtp_password and smtp_server):
        return False

    msg = MIMEText(body, 'plain', 'utf-8')
    msg['Subject'] = subject
    msg['From'] = smtp_user
    msg['To'] = ', '.join(target_list)
    try:
        smtp = smtplib.SMTP(smtp_server, smtp_port, timeout=20)
        smtp.starttls()
        smtp.login(smtp_user, smtp_password)
        smtp.sendmail(smtp_user, target_list, msg.as_string())
        smtp.quit()
        return True
    except Exception:
        return False


def _active_alerts(conn: sqlite3.Connection) -> dict[tuple[str, str], dict]:
    dashboard = build_ingestion_health_dashboard(conn)
    alerts: dict[tuple[str, str], dict] = {}
    for source in dashboard['all_sources']:
        if source['health_label'] not in {'Stale', 'Failing'}:
            continue
        alert_kind = 'stale' if source['health_label'] == 'Stale' else 'failing'
        alerts[(source['source_type'], alert_kind)] = {
            'source_type': source['source_type'],
            'display_name': source['display_name'],
            'alert_kind': alert_kind,
            'health_label': source['health_label'],
            'freshness': source['freshness'],
            'latest_status': source['latest_status'],
            'latest_received_at': source['latest_received_at'],
            'latest_activity_at': source['latest_activity_at'],
            'latest_document_name': source['latest_document_name'],
            'latest_error': source['latest_error'],
            'notes': source['notes'],
            'summary': (
                f"{source['display_name']} is {source['health_label'].lower()} "
                f"(status={source['latest_status'] or 'unknown'}, freshness={source['freshness']})."
            ),
        }
    return alerts


def _open_alert_rows(conn: sqlite3.Connection) -> dict[tuple[str, str], sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT *
        FROM ingestion_source_alerts
        WHERE state = 'open'
        ORDER BY id DESC
        """
    ).fetchall()
    open_rows: dict[tuple[str, str], sqlite3.Row] = {}
    for row in rows:
        key = (row['source_type'], row['alert_kind'])
        if key not in open_rows:
            open_rows[key] = row
    return open_rows


def _format_active_body(alerts: list[dict], heading: str) -> str:
    lines = [
        heading,
        '',
        f"Admin page: {config.BASE_URL.rstrip('/')}/admin/ingestion",
        '',
    ]
    for alert in alerts:
        lines.extend([
            f"- {alert['display_name']} [{alert['alert_kind']}]",
            f"  Source type: {alert['source_type']}",
            f"  Health: {alert['health_label']}",
            f"  Latest status: {alert['latest_status'] or 'unknown'}",
            f"  Freshness: {alert['freshness']}",
            f"  Latest document: {alert['latest_document_name'] or 'unknown'}",
            f"  Latest seen: {alert['latest_received_at'] or 'unknown'}",
            f"  Last activity: {alert['latest_activity_at'] or 'unknown'}",
        ])
        if alert['latest_error']:
            lines.append(f"  Error: {alert['latest_error']}")
        lines.append(f"  Notes: {alert['notes']}")
        lines.append('')
    return '\n'.join(lines).strip() + '\n'


def _format_resolved_body(alerts: list[sqlite3.Row]) -> str:
    lines = [
        'The following ingestion alerts resolved:',
        '',
        f"Admin page: {config.BASE_URL.rstrip('/')}/admin/ingestion",
        '',
    ]
    for alert in alerts:
        lines.extend([
            f"- {alert['source_type']} [{alert['alert_kind']}]",
            f"  Previous summary: {alert['summary'] or 'n/a'}",
            '',
        ])
    return '\n'.join(lines).strip() + '\n'


def run(*, dry_run: bool = False, force_reminder: bool = False) -> int:
    conn = _connect()
    _ensure_tables(conn)

    active = _active_alerts(conn)
    open_rows = _open_alert_rows(conn)
    repeat_hours = max(1, _app_setting_int(conn, 'ingest_alert_repeat_hours', int(getattr(config, 'INGEST_ALERT_REPEAT_HOURS', 24) or 24)))
    repeat_after = timedelta(hours=repeat_hours)
    now = datetime.now(timezone.utc)

    new_alerts: list[dict] = []
    reminder_alerts: list[dict] = []
    resolved_alerts: list[sqlite3.Row] = []

    for key, alert in active.items():
        row = open_rows.get(key)
        if row is None:
            new_alerts.append(alert)
            if not dry_run:
                conn.execute(
                    """
                    INSERT INTO ingestion_source_alerts (
                        source_type, alert_kind, state, summary, first_detected_at, last_detected_at, updated_at
                    ) VALUES (?, ?, 'open', ?, datetime('now'), datetime('now'), datetime('now'))
                    """,
                    (alert['source_type'], alert['alert_kind'], alert['summary']),
                )
            continue

        if not dry_run:
            conn.execute(
                """
                UPDATE ingestion_source_alerts
                SET summary = ?, last_detected_at = datetime('now'), updated_at = datetime('now')
                WHERE id = ?
                """,
                (alert['summary'], row['id']),
            )

        last_sent = _parse_timestamp(row['last_sent_at'])
        if force_reminder or last_sent is None or (now - last_sent) >= repeat_after:
            reminder_alerts.append(alert)

    for key, row in open_rows.items():
        if key in active:
            continue
        resolved_alerts.append(row)
        if not dry_run:
            conn.execute(
                """
                UPDATE ingestion_source_alerts
                SET state = 'resolved', resolved_at = datetime('now'), updated_at = datetime('now')
                WHERE id = ?
                """,
                (row['id'],),
            )

    recipients = _collect_recipients(conn)
    sent_active = False
    sent_resolved = False

    if new_alerts or reminder_alerts:
        body_parts = []
        if new_alerts:
            body_parts.append(_format_active_body(new_alerts, 'New ingestion alerts detected:'))
        if reminder_alerts:
            body_parts.append(_format_active_body(reminder_alerts, 'Ongoing ingestion alerts still active:'))
        subject = f"[Montana Blotter] Ingestion alerts: {len(new_alerts) + len(reminder_alerts)} source(s)"
        if dry_run:
            sent_active = True
        else:
            sent_active = _send_plaintext_email(recipients, subject, '\n'.join(body_parts))
        if sent_active and not dry_run:
            for alert in [*new_alerts, *reminder_alerts]:
                conn.execute(
                    """
                    UPDATE ingestion_source_alerts
                    SET last_sent_at = datetime('now'), updated_at = datetime('now')
                    WHERE source_type = ? AND alert_kind = ? AND state = 'open'
                    """,
                    (alert['source_type'], alert['alert_kind']),
                )

    if resolved_alerts:
        subject = f"[Montana Blotter] Ingestion recovered: {len(resolved_alerts)} source(s)"
        if dry_run:
            sent_resolved = True
        else:
            sent_resolved = _send_plaintext_email(recipients, subject, _format_resolved_body(resolved_alerts))

    conn.commit()
    conn.close()

    print(
        f"recipients={len(recipients)} new={len(new_alerts)} "
        f"reminders={len(reminder_alerts)} resolved={len(resolved_alerts)} "
        f"sent_active={int(sent_active)} sent_resolved={int(sent_resolved)}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='Send admin alerts for stale or failing ingest sources.')
    parser.add_argument('--dry-run', action='store_true', help='Evaluate alerts without sending email or updating state.')
    parser.add_argument('--force-reminder', action='store_true', help='Send reminders for all open alerts regardless of repeat interval.')
    args = parser.parse_args()
    return run(dry_run=args.dry_run, force_reminder=args.force_reminder)


if __name__ == '__main__':
    raise SystemExit(main())
