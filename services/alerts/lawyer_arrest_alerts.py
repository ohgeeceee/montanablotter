"""
Lawyer arrest alerts — real-time push to paying lawyer_ad_orders advertisers
when a new blotter post matches their county + practice area, plus a
defendant-name claim flow that surfaces a "case outcome tracked" badge once
services.disposition confirms a disposition for the claimed case.

Reuses the existing lawyer_ad_orders roster (blueprints/lawyer_ads.py) instead
of a parallel subscription table — an advertiser's counties_served and
practice_areas already gate this.

Two cron-driven entry points (see scripts/ops/lawyer_arrest_alerts_watcher.py):
- dispatch_pending_alerts(conn): find posts not yet processed, email matching
  advertisers, mark posts.lawyer_alert_dispatched_at.
- check_claimed_outcomes(conn): for claimed deliveries with a linked
  court_case, detect a newly-scraped disposition and send the outcome email.

Matching, claiming, and posts.lawyer_alert_dispatched_at are all idempotent —
re-running any function here is safe.
"""

from __future__ import annotations

import logging
import secrets
import smtplib
import sqlite3
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from typing import Optional

import config
from blueprints.lawyer_ads import _county_matches, _smtp_settings
from services.disposition.lookup import lookup_disposition

log = logging.getLogger(__name__)

DISPATCH_BATCH_SIZE = 50

# records.charge_category values ('violent','domestic','drug','dui','weapons',
# 'property','traffic', or NULL) mapped to the lawyer_ads _PRACTICE_AREAS
# vocabulary. Anything not in this map (including NULL) still matches
# 'Criminal Defense' advertisers — most arrests are criminal defense leads.
_DUI_TRAFFIC_CATEGORIES = {'dui', 'traffic'}


def _practice_areas_for_charge(charge_category: Optional[str]) -> list[str]:
    category = (charge_category or '').strip().lower()
    if category in _DUI_TRAFFIC_CATEGORIES:
        return ['DUI / Traffic', 'Criminal Defense']
    return ['Criminal Defense']


def _order_matches_practice(practice_areas_raw: str, wanted: list[str]) -> bool:
    raw = (practice_areas_raw or '').lower()
    return any(area.lower() in raw for area in wanted)


def find_matching_orders(conn: sqlite3.Connection, county: str, charge_category: Optional[str]) -> list[sqlite3.Row]:
    """Active, paid lawyer advertisers whose county + practice area match this post."""
    wanted_areas = _practice_areas_for_charge(charge_category)
    rows = conn.execute(
        '''
        SELECT o.id, o.firm_name, o.email, o.counties_served, o.practice_areas
        FROM lawyer_ad_orders o
        JOIN lawyer_ad_listings l ON l.order_id = o.id
        WHERE o.status = 'active' AND l.is_active = 1
        '''
    ).fetchall()
    matched = []
    for row in rows:
        if not _county_matches(row['counties_served'], county):
            continue
        if not _order_matches_practice(row['practice_areas'], wanted_areas):
            continue
        matched.append(row)
    return matched


def _send_arrest_alert_email(order: sqlite3.Row, post: sqlite3.Row, claim_token: str) -> tuple[bool, str]:
    user, password, server, port = _smtp_settings()
    destination = (order['email'] or '').strip().lower()
    if not (user and password and server and destination and '@' in destination):
        return False, 'smtp_not_configured'

    firm_name = escape(str(order['firm_name'] or 'your firm'))
    title = escape(str(post['title'] or 'New arrest'))
    county = escape(str(post['county'] or ''))
    summary = escape(str(post['summary'] or ''))
    base_url = (getattr(config, 'BASE_URL', '') or 'https://montanablotter.com').rstrip('/')
    post_url = f'{base_url}/post/{post["id"]}'
    claim_url = f'{base_url}/lawyers/claim/{claim_token}'

    msg = MIMEMultipart('alternative')
    msg['Subject'] = f'New {county} arrest matching your practice area'
    msg['From'] = f'Montana Blotter <{user}>'
    msg['To'] = destination
    msg['Reply-To'] = 'advertising@montanablotter.com'
    plain = (
        f'A new arrest was just published in {post["county"]} matching your listing.\n\n'
        f'{post["title"]}\n{post["summary"]}\n\n'
        f'View: {post_url}\n'
        f'If this is your client, claim the case to get notified when the court outcome posts:\n{claim_url}\n'
    )
    html = f'''<!doctype html>
<html><body style="font-family:Arial,sans-serif;color:#1e1b18;line-height:1.5;">
  <h2 style="font-family:Georgia,serif;">New {county} arrest — {firm_name}</h2>
  <p><strong>{title}</strong></p>
  <p>{summary}</p>
  <p><a href="{escape(post_url)}">View full post</a></p>
  <p><a href="{escape(claim_url)}" style="display:inline-block;padding:10px 16px;background:#1e1b18;color:#fff;text-decoration:none;border-radius:6px;">Claim this case</a></p>
  <p style="font-size:12px;color:#666;">Claiming lets us notify you the moment a court disposition posts, and adds a verified "Represented by" badge to the public post.</p>
</body></html>'''
    msg.attach(MIMEText(plain, 'plain'))
    msg.attach(MIMEText(html, 'html'))

    try:
        with smtplib.SMTP(server, port, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.sendmail(user, [destination], msg.as_string())
        return True, ''
    except Exception as exc:  # noqa: BLE001
        log.warning('Arrest alert email failed order_id=%s destination=%s: %s', order['id'], destination, exc)
        return False, str(exc)[:300]


def dispatch_pending_alerts(conn: sqlite3.Connection, batch_size: int = DISPATCH_BATCH_SIZE) -> dict:
    """Find posts not yet processed for arrest alerts, email matching advertisers."""
    stats = {'posts_checked': 0, 'alerts_sent': 0, 'alerts_failed': 0}
    posts = conn.execute(
        '''
        SELECT p.id, p.title, p.summary, p.county, r.charge_category
        FROM posts p
        LEFT JOIN records r ON r.id = p.record_id
        WHERE p.lawyer_alert_dispatched_at IS NULL
          AND p.audit_status != 'pending'
          AND p.county IS NOT NULL AND p.county != ''
        ORDER BY p.id ASC
        LIMIT ?
        ''',
        (batch_size,),
    ).fetchall()

    for post in posts:
        stats['posts_checked'] += 1
        matching_orders = find_matching_orders(conn, post['county'], post['charge_category'])
        for order in matching_orders:
            claim_token = secrets.token_urlsafe(24)
            sent, error = _send_arrest_alert_email(order, post, claim_token)
            conn.execute(
                '''
                INSERT INTO lawyer_arrest_alert_deliveries
                    (order_id, post_id, county, charge_category, status, error, sent_at, claim_token)
                VALUES (?, ?, ?, ?, ?, ?, CASE WHEN ? = 1 THEN datetime('now') ELSE NULL END, ?)
                ON CONFLICT(order_id, post_id) DO NOTHING
                ''',
                (
                    order['id'], post['id'], post['county'], post['charge_category'],
                    'sent' if sent else 'failed', error or None, 1 if sent else 0, claim_token,
                ),
            )
            stats['alerts_sent' if sent else 'alerts_failed'] += 1
        conn.execute(
            "UPDATE posts SET lawyer_alert_dispatched_at = datetime('now') WHERE id = ?",
            (post['id'],),
        )
    conn.commit()
    return stats


def claim_case(conn: sqlite3.Connection, claim_token: str, defendant_name: str) -> dict:
    """Attorney supplies the defendant name at claim time; resolve to a court_case
    via the existing disposition lookup rather than reverse-engineering redacted PII."""
    delivery = conn.execute(
        '''
        SELECT d.*, p.county
        FROM lawyer_arrest_alert_deliveries d
        JOIN posts p ON p.id = d.post_id
        WHERE d.claim_token = ?
        ''',
        (claim_token,),
    ).fetchone()
    if not delivery:
        return {'ok': False, 'error': 'not_found'}

    court_case_id = None
    defendant_name = (defendant_name or '').strip()
    if defendant_name:
        result = lookup_disposition(conn, name=defendant_name, county=delivery['county'], limit=1)
        matches = result.get('matches') or []
        if matches:
            cases = matches[0].get('court_cases') or []
            if cases:
                court_case_id = cases[0].get('id')

    conn.execute(
        '''
        UPDATE lawyer_arrest_alert_deliveries
        SET claimed_at = datetime('now'), court_case_id = ?
        WHERE claim_token = ?
        ''',
        (court_case_id, claim_token),
    )
    conn.commit()
    return {'ok': True, 'court_case_id': court_case_id}


def _send_outcome_email(order_email: str, firm_name: str, post_url: str, disposition: str) -> tuple[bool, str]:
    user, password, server, port = _smtp_settings()
    destination = (order_email or '').strip().lower()
    if not (user and password and server and destination and '@' in destination):
        return False, 'smtp_not_configured'
    msg = MIMEMultipart('alternative')
    msg['Subject'] = 'Court outcome posted for your claimed case'
    msg['From'] = f'Montana Blotter <{user}>'
    msg['To'] = destination
    plain = f'A disposition has been recorded: {disposition}\n\nCase: {post_url}\n'
    msg.attach(MIMEText(plain, 'plain'))
    try:
        with smtplib.SMTP(server, port, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.sendmail(user, [destination], msg.as_string())
        return True, ''
    except Exception as exc:  # noqa: BLE001
        log.warning('Outcome alert email failed destination=%s: %s', destination, exc)
        return False, str(exc)[:300]


def check_claimed_outcomes(conn: sqlite3.Connection) -> dict:
    """For claimed deliveries with a linked court_case, notify once a disposition lands."""
    stats = {'checked': 0, 'notified': 0}
    rows = conn.execute(
        '''
        SELECT d.id AS delivery_id, d.post_id, o.email, o.firm_name,
               cc.disposition, cc.outcome_scraped_at
        FROM lawyer_arrest_alert_deliveries d
        JOIN lawyer_ad_orders o ON o.id = d.order_id
        JOIN court_cases cc ON cc.id = d.court_case_id
        WHERE d.claimed_at IS NOT NULL
          AND d.outcome_notified_at IS NULL
          AND cc.outcome_scraped_at IS NOT NULL
          AND cc.disposition IS NOT NULL AND cc.disposition != ''
        '''
    ).fetchall()
    base_url = (getattr(config, 'BASE_URL', '') or 'https://montanablotter.com').rstrip('/')
    for row in rows:
        stats['checked'] += 1
        post_url = f'{base_url}/post/{row["post_id"]}'
        sent, _error = _send_outcome_email(row['email'], row['firm_name'], post_url, row['disposition'])
        if sent:
            conn.execute(
                "UPDATE lawyer_arrest_alert_deliveries SET outcome_notified_at = datetime('now') WHERE id = ?",
                (row['delivery_id'],),
            )
            stats['notified'] += 1
    conn.commit()
    return stats
