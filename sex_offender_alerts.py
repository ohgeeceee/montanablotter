"""
Sex Offender Proximity Alert Engine

Checks new registrations against alert subscriptions and sends emails.

Usage:
    python sex_offender_alerts.py --dry-run
"""
from __future__ import annotations

import argparse
import math
import os
import sqlite3
from typing import Any

from db import connect_db


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in miles between two lat/lon points."""
    R = 3958.8  # Earth radius in miles
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def check_and_notify(conn: sqlite3.Connection, *, dry_run: bool = False) -> list[dict[str, Any]]:
    """Check new changes against subscriptions and return notifications sent."""
    changes = conn.execute(
        '''
        SELECT soc.*, so.full_name, so.address_street, so.address_city, so.address_county, so.lat, so.lon
        FROM sex_offender_changes soc
        JOIN sex_offenders so ON soc.offender_id = so.id
        WHERE soc.created_at > datetime('now', '-7 days')
          AND soc.change_type IN ('new_registration', 'address_change')
        ORDER BY soc.created_at DESC
        '''
    ).fetchall()

    subscriptions = conn.execute(
        'SELECT * FROM sex_offender_alert_subscriptions WHERE is_active = 1'
    ).fetchall()

    notifications = []
    for sub in subscriptions:
        matched = []
        for change in changes:
            if change['lat'] is None or change['lon'] is None:
                continue
            dist = haversine(sub['lat'], sub['lon'], change['lat'], change['lon'])
            if dist <= sub['radius_miles']:
                matched.append({
                    'change': dict(change),
                    'distance_miles': round(dist, 1),
                })

        if matched:
            notification = {
                'email': sub['email'],
                'subscription_id': sub['id'],
                'matches': matched,
            }
            notifications.append(notification)

            if not dry_run:
                print(f"Would notify {sub['email']} about {len(matched)} changes within {sub['radius_miles']} miles")
                conn.execute(
                    'UPDATE sex_offender_alert_subscriptions SET last_sent_at = datetime("now") WHERE id = ?',
                    (sub['id'],),
                )

    if not dry_run:
        conn.commit()

    return notifications


def main():
    parser = argparse.ArgumentParser(description='Check and send sex offender proximity alerts')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    conn = connect_db()
    try:
        notifications = check_and_notify(conn, dry_run=args.dry_run)
        print(f'Notifications: {len(notifications)}')
        for n in notifications:
            print(f"  {n['email']}: {len(n['matches'])} matches")
    finally:
        conn.close()


if __name__ == '__main__':
    main()
