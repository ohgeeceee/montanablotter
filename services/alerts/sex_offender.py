"""
Violent / Sexual Offender Proximity Alert Engine

Checks new registrations against alert subscriptions and sends emails.

Usage:
    python services/alerts/sex_offender.py --dry-run
"""
from __future__ import annotations

import argparse
import math
import os
import smtplib
import sqlite3
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import config
from db import connect_db

BASE_URL = getattr(config, 'BASE_URL', 'https://montanablotter.com')


def _send_alert_email(to_addr: str, subject: str, html_body: str) -> bool:
    smtp_user = getattr(config, 'SMTP_USER', config.EMAIL_USER)
    smtp_password = getattr(config, 'SMTP_PASSWORD', config.EMAIL_PASSWORD)
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = f"Montana Blotter <{smtp_user}>"
    msg['To'] = to_addr
    msg.attach(MIMEText(html_body, 'html'))
    try:
        with smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, to_addr, msg.as_string())
        return True
    except Exception as exc:
        print(f"Email failed to {to_addr}: {exc}")
        return False


def _build_alert_html(matches: list[dict], zip_code: str | None, radius: float, unsubscribe_url: str) -> str:
    location_label = f"zip code {zip_code}" if zip_code else "your location"
    rows = ""
    for m in matches:
        c = m['change']
        name_parts = (c.get('full_name') or '').split()
        display_name = f"{name_parts[0]} {name_parts[-1][0]}." if len(name_parts) >= 2 else c.get('full_name', 'Unknown')
        change_label = 'New registration' if c.get('change_type') == 'new_registration' else 'Address change'
        city = c.get('address_city') or ''
        county = c.get('address_county') or ''
        location_str = ', '.join(filter(None, [city, county, 'MT']))
        dist = m['distance_miles']
        tier = c.get('offender_type') or ''
        rows += f"""
        <tr>
          <td style="padding:8px 12px;border-bottom:1px solid #e2e8f0">{display_name}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #e2e8f0">{change_label}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #e2e8f0">{location_str}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #e2e8f0">{dist} mi</td>
          <td style="padding:8px 12px;border-bottom:1px solid #e2e8f0">{tier}</td>
        </tr>"""

    return f"""
    <html><body style="font-family:sans-serif;color:#1e293b;max-width:600px;margin:0 auto">
      <h2 style="font-size:18px;margin-bottom:4px">Violent/Sexual Offender Proximity Alert</h2>
      <p style="color:#64748b;font-size:14px;margin-top:0">
        {len(matches)} registrant(s) within {int(radius)} mile(s) of {location_label}
      </p>
      <table style="width:100%;border-collapse:collapse;font-size:14px">
        <thead>
          <tr style="background:#f1f5f9">
            <th style="padding:8px 12px;text-align:left">Name</th>
            <th style="padding:8px 12px;text-align:left">Event</th>
            <th style="padding:8px 12px;text-align:left">Location</th>
            <th style="padding:8px 12px;text-align:left">Distance</th>
            <th style="padding:8px 12px;text-align:left">Type</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
      <p style="font-size:13px;margin-top:20px;color:#64748b">
        Source: <a href="https://svor.doj.mt.gov">MT DOJ Violent/Sexual Offender Registry</a>.<br>
        Montana Blotter does not guarantee completeness or accuracy of registry data.<br>
        For emergencies, contact local law enforcement.
      </p>
      <p style="font-size:12px;color:#94a3b8;margin-top:16px">
        <a href="{unsubscribe_url}" style="color:#94a3b8">Unsubscribe from these alerts</a>
      </p>
    </body></html>"""


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
        SELECT soc.*, so.full_name, so.address_street, so.address_city, so.address_county, so.lat, so.lon, so.offender_type
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
                zip_code = sub['zip_code'] if 'zip_code' in sub.keys() else None
                token = sub['unsubscribe_token'] if 'unsubscribe_token' in sub.keys() else ''
                unsub_url = f"{BASE_URL}/sex-offender-alerts/unsubscribe?token={token}" if token else BASE_URL
                html = _build_alert_html(matched, zip_code, sub['radius_miles'], unsub_url)
                subject = f"Sex Offender Proximity Alert — {len(matched)} update(s) near {zip_code or 'your area'}"
                sent = _send_alert_email(sub['email'], subject, html)
                if sent:
                    conn.execute(
                        'UPDATE sex_offender_alert_subscriptions SET last_sent_at = datetime("now") WHERE id = ?',
                        (sub['id'],),
                    )
            else:
                print(f"[dry-run] Would notify {sub['email']} about {len(matched)} changes within {sub['radius_miles']} miles")

    if not dry_run:
        conn.commit()

    return notifications


def main():
    parser = argparse.ArgumentParser(description='Check and send violent/sexual offender proximity alerts')
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
