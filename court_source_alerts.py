#!/usr/bin/env python3
"""
Admin alerts for stale or failing court tracker sources.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import UTC, datetime, timedelta

import config
from court_tracker import court_admin_context, ensure_court_tracker_schema
from ingestion_alerts import _app_setting_int
from alerting import collect_alert_recipients, send_plaintext_email as _send_plaintext_email
from init_db import _configure_sqlite


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=float(getattr(config, 'DB_TIMEOUT_SECONDS', 30)))
    conn.row_factory = sqlite3.Row
    _configure_sqlite(conn)
    ensure_court_tracker_schema(conn)
    return conn


def _active_alerts(conn: sqlite3.Connection) -> dict[tuple[str, str], dict]:
    context = court_admin_context(conn)
    alerts: dict[tuple[str, str], dict] = {}
    for source in context['sources']:
        if source['health_state'] not in {'stale', 'error'}:
            continue
        alert_kind = 'error' if source['health_state'] == 'error' else 'stale'
        alerts[(source['slug'], alert_kind)] = {
            'source_slug': source['slug'],
            'display_name': source['name'],
            'alert_kind': alert_kind,
            'provider_type': source['provider_type'],
            'source_url': source['source_url'],
            'last_scraped_at': source['last_scraped_at'],
            'last_success_at': source['last_success_at'],
            'last_error': source['last_error'],
            'summary': (
                f"{source['name']} is {alert_kind} "
                f"(scraped={source['last_scraped_at'] or 'never'}, success={source['last_success_at'] or 'never'})."
            ),
        }
    return alerts


def _open_alert_rows(conn: sqlite3.Connection) -> dict[tuple[str, str], sqlite3.Row]:
    rows = conn.execute(
        '''
        SELECT *
        FROM court_source_alerts
        WHERE state = 'open'
        ORDER BY id DESC
        '''
    ).fetchall()
    open_rows: dict[tuple[str, str], sqlite3.Row] = {}
    for row in rows:
        key = (row['source_slug'], row['alert_kind'])
        if key not in open_rows:
            open_rows[key] = row
    return open_rows


def _format_active_body(alerts: list[dict], heading: str) -> str:
    lines = [
        heading,
        '',
        f"Admin page: {config.BASE_URL.rstrip('/')}/admin/operations/courts",
        '',
    ]
    for alert in alerts:
        lines.extend([
            f"- {alert['display_name']} [{alert['alert_kind']}]",
            f"  Source slug: {alert['source_slug']}",
            f"  Provider: {alert['provider_type']}",
            f"  Last scrape: {alert['last_scraped_at'] or 'never'}",
            f"  Last success: {alert['last_success_at'] or 'never'}",
            f"  Source URL: {alert['source_url']}",
        ])
        if alert['last_error']:
            lines.append(f"  Error: {alert['last_error']}")
        lines.append('')
    return '\n'.join(lines).strip() + '\n'


def _format_resolved_body(alerts: list[sqlite3.Row]) -> str:
    lines = [
        'The following court source alerts resolved:',
        '',
        f"Admin page: {config.BASE_URL.rstrip('/')}/admin/operations/courts",
        '',
    ]
    for alert in alerts:
        lines.extend([
            f"- {alert['source_slug']} [{alert['alert_kind']}]",
            f"  Previous summary: {alert['summary'] or 'n/a'}",
            '',
        ])
    return '\n'.join(lines).strip() + '\n'


def run(*, dry_run: bool = False, force_reminder: bool = False) -> int:
    conn = _connect()
    repeat_hours = max(1, _app_setting_int(conn, 'court_alert_repeat_hours', 24))
    repeat_after = timedelta(hours=repeat_hours)
    now = datetime.now(UTC)
    active = _active_alerts(conn)
    open_rows = _open_alert_rows(conn)

    new_alerts: list[dict] = []
    reminder_alerts: list[dict] = []
    resolved_alerts: list[sqlite3.Row] = []

    for key, alert in active.items():
        row = open_rows.get(key)
        if row is None:
            new_alerts.append(alert)
            if not dry_run:
                conn.execute(
                    '''
                    INSERT INTO court_source_alerts (
                        source_slug, alert_kind, state, summary, first_detected_at, last_detected_at, updated_at
                    ) VALUES (?, ?, 'open', ?, datetime('now'), datetime('now'), datetime('now'))
                    ''',
                    (alert['source_slug'], alert['alert_kind'], alert['summary']),
                )
            continue

        if not dry_run:
            conn.execute(
                '''
                UPDATE court_source_alerts
                SET summary = ?,
                    last_detected_at = datetime('now'),
                    updated_at = datetime('now')
                WHERE id = ?
                ''',
                (alert['summary'], row['id']),
            )

        should_remind = force_reminder
        if not should_remind:
            last_sent_raw = row['last_sent_at']
            if not last_sent_raw:
                should_remind = True
            else:
                last_sent = datetime.fromisoformat(last_sent_raw.replace('Z', '+00:00'))
                if last_sent.tzinfo is None:
                    last_sent = last_sent.replace(tzinfo=UTC)
                should_remind = now - last_sent >= repeat_after
        if should_remind:
            reminder_alerts.append(alert)

    for key, row in open_rows.items():
        if key in active:
            continue
        resolved_alerts.append(row)
        if not dry_run:
            conn.execute(
                '''
                UPDATE court_source_alerts
                SET state = 'resolved',
                    resolved_at = datetime('now'),
                    updated_at = datetime('now')
                WHERE id = ?
                ''',
                (row['id'],),
            )

    recipients = collect_alert_recipients(conn)
    sent_active = False
    sent_resolved = False

    if new_alerts or reminder_alerts:
        subject = f"[Montana Blotter] Court source alerts: {len(new_alerts) + len(reminder_alerts)} source(s)"
        body_parts = []
        if new_alerts:
            body_parts.append(_format_active_body(new_alerts, 'New court source alerts detected:'))
        if reminder_alerts:
            body_parts.append(_format_active_body(reminder_alerts, 'Ongoing court source alerts still active:'))
        sent_active = True if dry_run else _send_plaintext_email(recipients, subject, '\n'.join(body_parts))
        if sent_active and not dry_run:
            for alert in [*new_alerts, *reminder_alerts]:
                conn.execute(
                    '''
                    UPDATE court_source_alerts
                    SET last_sent_at = datetime('now'), updated_at = datetime('now')
                    WHERE source_slug = ? AND alert_kind = ? AND state = 'open'
                    ''',
                    (alert['source_slug'], alert['alert_kind']),
                )

    if resolved_alerts:
        sent_resolved = True if dry_run else _send_plaintext_email(
            recipients,
            f"[Montana Blotter] Court source recovered: {len(resolved_alerts)} source(s)",
            _format_resolved_body(resolved_alerts),
        )
        if sent_resolved and not dry_run:
            conn.execute(
                '''
                UPDATE court_source_alerts
                SET last_sent_at = datetime('now'), updated_at = datetime('now')
                WHERE state = 'resolved' AND resolved_at IS NOT NULL AND coalesce(last_sent_at, '') = ''
                '''
            )

    if dry_run:
        conn.rollback()
    else:
        conn.commit()
    conn.close()

    print(
        f"recipients={len(recipients)} new={len(new_alerts)} reminders={len(reminder_alerts)} "
        f"resolved={len(resolved_alerts)} sent_active={int(sent_active)} sent_resolved={int(sent_resolved)}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='Send admin alerts for stale or failing court sources.')
    parser.add_argument('--dry-run', action='store_true', help='Evaluate alerts without sending email or updating state.')
    parser.add_argument('--force-reminder', action='store_true', help='Send reminders for all open alerts regardless of repeat interval.')
    args = parser.parse_args()
    return run(dry_run=args.dry_run, force_reminder=args.force_reminder)


if __name__ == '__main__':
    raise SystemExit(main())
