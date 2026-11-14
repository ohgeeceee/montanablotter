"""
Daily New Jail Bookings — Facebook Poster

Posts a daily summary of newly ingested jail bookings to the Montana Blotter
Facebook page.

Usage:
    python services/publishing/jail_bookings_facebook.py --dry-run
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import config
import facebook_publisher
from db import connect_db


def _new_bookings(conn, hours: int = 24):
    start = (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(tzinfo=None).strftime('%Y-%m-%d %H:%M:%S')
    return conn.execute(
        '''
        SELECT county_name, person_name, charges_summary
        FROM jail_bookings
        WHERE datetime(COALESCE(first_seen_at, created_at)) > datetime(?)
        ORDER BY county_name, id DESC
        ''',
        (start,),
    ).fetchall()


def build_message(bookings) -> str:
    lines = ["Here are today's newest jail bookings!", ""]
    for b in bookings:
        name = (b['person_name'] or 'Unknown').strip()
        county = (b['county_name'] or 'Unknown').strip()
        lines.append(f"@{name} @{county} County Jail")
    lines.append("")
    lines.append(f"View all: {config.BASE_URL.rstrip('/')}/jail-bookings")
    lines.append("")
    lines.append("#MontanaBlotter #JailBookings #PublicSafety #Montana")
    return "\n".join(lines)


def run(*, dry_run: bool = False, hours: int = 24):
    conn = connect_db()
    try:
        bookings = _new_bookings(conn, hours=hours)
        if not bookings:
            print("No new bookings in the last 24h.")
            return
        message = build_message(bookings)
        if dry_run:
            print(message)
            return
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            '''INSERT INTO facebook_post_queue
               (post_id, content_type, dedupe_key, status, custom_message, enqueue_source,
                created_at, updated_at)
               VALUES (0, 'custom', ?, 'queued', ?, 'jail_bookings_facebook',
                       datetime('now'), datetime('now'))''',
            (f"jailbookings:{datetime.now(timezone.utc).date().isoformat()}", message),
        )
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON")
        qid = conn.execute(
            "SELECT id FROM facebook_post_queue WHERE dedupe_key = ?",
            (f"jailbookings:{datetime.now(timezone.utc).date().isoformat()}",),
        ).fetchone()['id']
        pub = facebook_publisher.publish_queue_item(qid)
        print(f"posted={pub.get('success')} id={pub.get('facebook_post_id') or pub.get('error')}")
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--hours', type=int, default=24)
    args = parser.parse_args()
    run(dry_run=args.dry_run, hours=args.hours)


if __name__ == '__main__':
    main()