"""Send paid subscribers a daily email of newly ingested jail bookings."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from html import escape
import sqlite3

import config
from init_db import ensure_jail_roster_digest_schema
from services.monetization.name_suppression import redact_person_name, redact_text
from services.monetization.paywall import normalize_plan
from services.publishing.morning_briefing import send_email


BASE_URL = config.BASE_URL.rstrip('/')
PAID_STATUSES = {'active', 'trialing', 'past_due'}
MAX_BOOKINGS_PER_EMAIL = 250


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0).strftime('%Y-%m-%d %H:%M:%S')


def _eligible_subscriptions(conn: sqlite3.Connection):
    rows = conn.execute(
        '''
        SELECT s.public_user_id, s.counties, s.last_checked_at,
               u.email, u.subscriber_plan, u.subscription_status, u.is_subscribed
        FROM jail_roster_digest_subscriptions s
        JOIN public_users u ON u.id = s.public_user_id
        WHERE s.enabled = 1 AND COALESCE(u.is_active, 1) = 1
        ORDER BY s.public_user_id
        '''
    ).fetchall()
    eligible = []
    for row in rows:
        plan = normalize_plan(row['subscriber_plan'])
        status = (row['subscription_status'] or '').strip().lower()
        if bool(row['is_subscribed']) and plan in {'plus', 'pro'} and status in PAID_STATUSES:
            eligible.append((row, plan))
    return eligible


def _selected_counties(raw_value: str) -> list[str]:
    return [item.strip() for item in (raw_value or '').split(',') if item.strip()]


def _new_bookings(conn, start_at: str, end_at: str, counties: list[str]):
    sql = '''
        SELECT id, county_slug, county_name, facility_name, person_name, age,
               booking_at, charges_summary, arresting_agency, first_seen_at
        FROM jail_bookings
        WHERE datetime(COALESCE(first_seen_at, created_at)) > datetime(?)
          AND datetime(COALESCE(first_seen_at, created_at)) <= datetime(?)
    '''
    params: list[object] = [start_at, end_at]
    if counties:
        placeholders = ','.join('?' for _ in counties)
        sql += f' AND county_name IN ({placeholders})'
        params.extend(counties)
    sql += ' ORDER BY county_name, datetime(COALESCE(booking_at, first_seen_at)) DESC, id DESC LIMIT ?'
    params.append(MAX_BOOKINGS_PER_EMAIL + 1)
    return conn.execute(sql, params).fetchall()


def build_digest_html(bookings, *, statewide: bool, truncated: bool = False) -> str:
    grouped = defaultdict(list)
    for row in bookings[:MAX_BOOKINGS_PER_EMAIL]:
        grouped[(row['county_name'] or 'Unknown').strip()].append(row)

    scope_label = 'all available Montana counties' if statewide else ', '.join(grouped) or 'selected counties'
    parts = [
        '<div style="max-width:720px;margin:0 auto;font-family:Arial,sans-serif;color:#1e293b;">',
        '<div style="border-top:5px solid #b91c1c;padding:24px 4px 12px;">',
        '<p style="margin:0;color:#b91c1c;font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.12em;">Montana Blotter</p>',
        '<h1 style="margin:8px 0 6px;font-size:28px;">Daily Jail Roster</h1>',
        f'<p style="margin:0;color:#64748b;">{len(bookings[:MAX_BOOKINGS_PER_EMAIL])} new booking record(s) from {escape(scope_label)}.</p>',
        '</div>',
    ]

    for county, county_bookings in grouped.items():
        county_slug = county_bookings[0]['county_slug'] or ''
        parts.append(
            f'<h2 style="margin:24px 0 8px;padding-bottom:7px;border-bottom:1px solid #cbd5e1;font-size:20px;">'
            f'{escape(county)} County <span style="color:#64748b;font-size:13px;">({len(county_bookings)})</span></h2>'
        )
        for row in county_bookings:
            person_name = redact_person_name(row['person_name'] or 'Name unavailable', county)
            charges = redact_text(row['charges_summary'] or 'Charge details unavailable', county)
            booking_time = row['booking_at'] or row['first_seen_at'] or ''
            facility = row['facility_name'] or f'{county} County jail'
            parts.append(
                '<div style="padding:11px 0;border-bottom:1px solid #e2e8f0;">'
                f'<a href="{BASE_URL}/booking/{int(row["id"])}" style="font-weight:800;color:#0f172a;text-decoration:none;">{escape(person_name)}</a>'
                f'<div style="margin-top:3px;color:#475569;font-size:13px;">{escape(facility)} · {escape(booking_time)}</div>'
                f'<div style="margin-top:4px;color:#64748b;font-size:13px;">{escape(charges)}</div>'
                '</div>'
            )
        parts.append(
            f'<p style="margin:10px 0 0;"><a href="{BASE_URL}/jail-bookings/{escape(county_slug)}" '
            'style="color:#b91c1c;font-size:13px;font-weight:700;">View county jail roster →</a></p>'
        )

    if truncated:
        parts.append(
            f'<p style="margin-top:22px;padding:12px;background:#fff7ed;color:#9a3412;">This email is limited to '
            f'{MAX_BOOKINGS_PER_EMAIL} records. <a href="{BASE_URL}/jail-bookings" style="color:#9a3412;font-weight:700;">View the complete jail roster</a>.</p>'
        )

    parts.extend([
        '<p style="margin-top:24px;color:#64748b;font-size:12px;line-height:1.5;">Booking records are allegations and do not establish guilt. Data availability varies by county and source schedule.</p>',
        f'<p style="color:#94a3b8;font-size:12px;"><a href="{BASE_URL}/account#jail-roster-digest" style="color:#64748b;">Manage jail-roster emails</a> · <a href="{BASE_URL}/pricing" style="color:#64748b;">Manage subscription</a></p>',
        '</div>',
    ])
    return ''.join(parts)


def run_digest(*, now: datetime | None = None, dry_run: bool = False) -> dict[str, int]:
    run_end_dt = now or datetime.now(timezone.utc)
    if run_end_dt.tzinfo is None:
        run_end_dt = run_end_dt.replace(tzinfo=timezone.utc)
    run_end = _timestamp(run_end_dt)
    default_start = _timestamp(run_end_dt - timedelta(hours=24))
    totals = {'eligible': 0, 'sent': 0, 'skipped': 0, 'failed': 0, 'bookings': 0}

    conn = get_db()
    ensure_jail_roster_digest_schema(conn)
    subscriptions = _eligible_subscriptions(conn)
    totals['eligible'] = len(subscriptions)

    for subscription, plan in subscriptions:
        counties = _selected_counties(subscription['counties'])
        if plan == 'plus' and (not counties or len(counties) > 5):
            totals['failed'] += 1
            continue
        start_at = subscription['last_checked_at'] or default_start
        bookings = _new_bookings(conn, start_at, run_end, counties)
        totals['bookings'] += min(len(bookings), MAX_BOOKINGS_PER_EMAIL)

        if not bookings:
            totals['skipped'] += 1
            if not dry_run:
                conn.execute(
                    '''UPDATE jail_roster_digest_subscriptions
                       SET last_checked_at = ?, updated_at = datetime('now')
                       WHERE public_user_id = ?''',
                    (run_end, subscription['public_user_id']),
                )
                conn.commit()
            continue

        if dry_run:
            totals['sent'] += 1
            continue

        subject = f"Montana jail roster: {min(len(bookings), MAX_BOOKINGS_PER_EMAIL)} new booking{'s' if len(bookings) != 1 else ''}"
        html = build_digest_html(
            bookings,
            statewide=(plan == 'pro' and not counties),
            truncated=len(bookings) > MAX_BOOKINGS_PER_EMAIL,
        )
        try:
            send_email(subscription['email'], subject, html)
        except Exception as exc:
            totals['failed'] += 1
            print(f'Jail roster digest failed for user {subscription["public_user_id"]}: {exc}')
            continue

        conn.execute(
            '''UPDATE jail_roster_digest_subscriptions
               SET last_checked_at = ?, last_sent_at = ?, updated_at = datetime('now')
               WHERE public_user_id = ?''',
            (run_end, run_end, subscription['public_user_id']),
        )
        conn.commit()
        totals['sent'] += 1

    conn.close()
    print(
        'Jail roster digest: '
        f"{totals['eligible']} eligible, {totals['sent']} sent, "
        f"{totals['skipped']} skipped, {totals['failed']} failed, "
        f"{totals['bookings']} bookings"
    )
    return totals


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='Count deliveries without sending or advancing checkpoints.')
    args = parser.parse_args()
    result = run_digest(dry_run=args.dry_run)
    return 1 if result['failed'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
