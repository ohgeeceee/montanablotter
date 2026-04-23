#!/usr/bin/env python3
"""
Hourly watcher for the official Montana missing-person database.

This script syncs the latest Montana DOJ records into the local database and
emails admin recipients when new or reactivated people appear in the state feed.
"""

from __future__ import annotations

import argparse
import sqlite3

import config
import requests
from alerting import collect_alert_recipients, send_plaintext_email
from missing_persons import sync_official_missing_persons


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=float(getattr(config, 'DB_TIMEOUT_SECONDS', 30) or 30))
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute(f"PRAGMA busy_timeout = {int(getattr(config, 'DB_BUSY_TIMEOUT_MS', 30000) or 30000)}")
    return conn


def _build_notification_body(result: dict[str, object]) -> str:
    new_records = list(result.get('new_records') or [])
    lines = [
        'New official missing-person records appeared in the Montana DOJ database.',
        '',
        f"Official last updated: {result.get('official_last_updated') or 'unknown'}",
        f"Official last checked: {result.get('official_last_checked') or 'unknown'}",
        f"Current official active total: {result.get('active_total') or 0}",
        '',
        f"Public page: {config.BASE_URL.rstrip('/')}/missing-persons",
        f"Admin page: {config.BASE_URL.rstrip('/')}/admin/operations/missing-persons",
        '',
        'New / reactivated records:',
        '',
    ]
    for person in new_records:
        lines.append(f"- {person.get('full_name') or 'Unknown'}")
        if person.get('last_seen_at_label'):
            lines.append(f"  Last seen: {person['last_seen_at_label']}")
        if person.get('investigating_agency'):
            lines.append(f"  Agency: {person['investigating_agency']}")
        if person.get('public_href'):
            lines.append(f"  Local record: {config.BASE_URL.rstrip('/')}{person['public_href']}")
        if person.get('source_url'):
            lines.append(f"  Official source: {person['source_url']}")
        lines.append('')
    return '\n'.join(lines).strip() + '\n'


def run(*, dry_run: bool = False, force_email: bool = False) -> int:
    conn = _connect()
    try:
        try:
            result = sync_official_missing_persons(conn, actor='hourly_missing_person_watch')
        except requests.exceptions.RequestException as exc:
            print('missing_person_watch:', f"source_unavailable={exc}", "emailed=no")
            return 0
        conn.commit()

        new_records = list(result.get('new_records') or [])
        should_email = bool(new_records or force_email)
        recipients = collect_alert_recipients(conn)

        if should_email and not dry_run:
            subject = (
                f"[Montana Blotter] Missing-person watch: "
                f"{len(new_records)} new / reactivated record(s)"
            )
            send_plaintext_email(recipients, subject, _build_notification_body(result))

        print(
            'missing_person_watch:',
            f"active_total={result.get('active_total', 0)}",
            f"created={result.get('created', 0)}",
            f"reactivated={result.get('reactivated', 0)}",
            f"resolved={result.get('resolved', 0)}",
            f"emailed={'yes' if should_email and not dry_run else 'no'}",
        )
        return 0
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description='Sync official Montana missing-person records and email admins about new cases.')
    parser.add_argument('--dry-run', action='store_true', help='Sync and report without sending email.')
    parser.add_argument('--force-email', action='store_true', help='Send an admin email even when there are no new records.')
    args = parser.parse_args()
    return run(dry_run=args.dry_run, force_email=args.force_email)


if __name__ == '__main__':
    raise SystemExit(main())
