"""
Sex Offender Registry — Facebook Address-Update Poster

When a registrant's address changes, posts a Facebook update in the form:
    @Name has updated their address to a location near you!
    Registry updated!

Usage:
    python services/alerts/sex_offender_facebook.py --dry-run
"""
from __future__ import annotations

import argparse
import sqlite3
from typing import Any

import config
import facebook_publisher
from db import connect_db


def _recent_address_changes(conn: sqlite3.Connection, days: int = 7) -> list[dict[str, Any]]:
    rows = conn.execute(
        '''
        SELECT soc.id AS change_id, soc.offender_id, soc.created_at,
               so.full_name, so.address_city, so.address_county
        FROM sex_offender_changes soc
        JOIN sex_offenders so ON soc.offender_id = so.id
        WHERE soc.change_type = 'address_change'
          AND so.offender_type = 'SEXUAL'
          AND soc.created_at > datetime('now', ?)
        ORDER BY soc.created_at DESC
        ''',
        (f'-{days} days',),
    ).fetchall()
    return [dict(r) for r in rows]


def _already_posted(conn: sqlite3.Connection, change_id: int) -> bool:
    key = f"sexoffender:{change_id}"
    row = conn.execute(
        "SELECT id FROM facebook_post_queue WHERE dedupe_key = ?", (key,)
    ).fetchone()
    return row is not None


def post_updates(*, dry_run: bool = False, days: int = 7) -> list[dict[str, Any]]:
    conn = connect_db()
    results: list[dict[str, Any]] = []
    try:
        changes = _recent_address_changes(conn, days=days)
        for c in changes:
            change_id = c['change_id']
            name = c['full_name'] or 'A registrant'
            city = (c['address_city'] or '').strip()
            county = (c['address_county'] or '').strip()

            # Build the requested message format
            message = f"@{name} has updated their address to a location near you! Registry updated!"
            if city or county:
                loc = ', '.join(filter(None, [city, county, 'MT']))
                message += f"\n\nLocation: {loc}"
                tags = []
                if city:
                    tags.append('#' + city.replace(' ', '') + 'MT')
                if county:
                    tags.append('#' + county.replace(' ', '') + 'County')
                if tags:
                    message += "\n\n" + ' '.join(tags)
            message += "\n\n#PublicSafety #MontanaBlotter #SexOffenderRegistry"

            if dry_run:
                print(f"[dry-run] change {change_id}: {message.splitlines()[0]}")
                results.append({'change_id': change_id, 'dry_run': True})
                continue

            if _already_posted(conn, change_id):
                print(f"change {change_id}: already queued, skipping")
                continue

            conn.execute('PRAGMA foreign_keys = OFF')
            conn.execute(
                '''
                INSERT INTO facebook_post_queue
                    (post_id, content_type, dedupe_key, status, custom_message, enqueue_source,
                     created_at, updated_at)
                VALUES (0, 'custom', ?, 'queued', ?, 'sex_offender_facebook', datetime('now'), datetime('now'))
                ''',
                (f'sexoffender:{change_id}', message),
            )
            conn.commit()
            conn.execute('PRAGMA foreign_keys = ON')
            queue_id = conn.execute(
                "SELECT id FROM facebook_post_queue WHERE dedupe_key = ?",
                (f'sexoffender:{change_id}',),
            ).fetchone()['id']

            pub = facebook_publisher.publish_queue_item(queue_id)
            print(f"change {change_id} ({name}): posted={pub.get('success')} id={pub.get('facebook_post_id') or pub.get('error')}")
            results.append({'change_id': change_id, 'name': name, 'published': bool(pub.get('success')), 'result': pub})
        conn.commit()
    finally:
        conn.close()
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description='Post sex offender address updates to Facebook')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--days', type=int, default=7)
    args = parser.parse_args()
    post_updates(dry_run=args.dry_run, days=args.days)


if __name__ == '__main__':
    main()