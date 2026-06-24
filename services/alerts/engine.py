"""
Premium alert engine: evaluates new records against user alert profiles
and enqueues notifications with deliverability tracking.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
from datetime import datetime, timedelta
from typing import List, Optional

import requests

DB_PATH = os.getenv("MB_DB_PATH", "/root/montanablotter/blotter.db").strip() or "/root/montanablotter/blotter.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


SEVERITY_ORDER = {"all": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def _severity_rank(severity: Optional[str]) -> int:
    return SEVERITY_ORDER.get((severity or "").lower().strip(), 0)


def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 3958.8  # Earth radius in miles
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlng / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _match_profile(record: sqlite3.Row, profile: sqlite3.Row) -> bool:
    # Incident type filter
    alert_types = json.loads(profile["alert_types"] or '["all"]')
    if "all" not in alert_types:
        incident_type = (record["incident_type"] or "").strip().lower()
        if incident_type not in [a.lower() for a in alert_types]:
            return False

    # Severity threshold
    if _severity_rank(record.get("severity")) < _severity_rank(profile["severity_threshold"]):
        return False

    # Geographic filters
    counties = json.loads(profile["counties"]) if profile["counties"] else []
    cities = json.loads(profile["cities"]) if profile["cities"] else []
    neighborhoods = json.loads(profile["neighborhoods"]) if profile["neighborhoods"] else []
    radius = profile["radius_miles"]
    center_lat = profile["center_lat"]
    center_lng = profile["center_lng"]

    if counties:
        rec_county = (record["county"] or "").strip().lower()
        if rec_county not in [c.lower() for c in counties]:
            return False

    if cities:
        rec_city = (record.get("city") or "").strip().lower()
        if rec_city not in [c.lower() for c in cities]:
            return False

    if neighborhoods:
        rec_neighborhood = (record.get("neighborhood") or "").strip().lower()
        if rec_neighborhood not in [n.lower() for n in neighborhoods]:
            return False

    if radius and center_lat is not None and center_lng is not None:
        rec_lat = record.get("lat")
        rec_lng = record.get("lng")
        if rec_lat is None or rec_lng is None:
            # Try geocode lookup
            conn = _connect()
            geo = conn.execute(
                "SELECT lat, lng FROM incident_geocodes WHERE record_id=?",
                (record["id"],),
            ).fetchone()
            conn.close()
            if geo:
                rec_lat, rec_lng = geo["lat"], geo["lng"]
            else:
                return False
        if _haversine(center_lat, center_lng, rec_lat, rec_lng) > radius:
            return False

    return True


def _enqueue_notification(
    conn: sqlite3.Connection,
    profile_id: Optional[int],
    user_id: int,
    record_id: int,
    channel: str,
    recipient: str,
    subject: str,
    body_html: str,
    body_text: str,
) -> int:
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO notification_queue (
            profile_id, user_id, record_id, channel, recipient,
            subject, body_html, body_text, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', datetime('now'))
        """,
        (profile_id, user_id, record_id, channel, recipient, subject, body_html, body_text),
    )
    conn.commit()
    return cursor.lastrowid


def _render_alert_email(record: sqlite3.Row, profile: sqlite3.Row) -> tuple:
    subject = f"New incident in {record['county']}: {record['incident_type'] or 'Alert'}"
    body_text = f"""
New incident matching your alert profile "{profile['name']}".

Date: {record['date']} {record.get('time') or ''}
Location: {record.get('location') or 'Unknown'}
County: {record['county']}
Type: {record['incident_type'] or 'N/A'}
Details: {record.get('details') or 'N/A'}

View on MontanaBlotter.com
""".strip()
    body_html = f"""
<p>New incident matching your alert profile <strong>{profile['name']}</strong>.</p>
<ul>
<li><strong>Date:</strong> {record['date']} {record.get('time') or ''}</li>
<li><strong>Location:</strong> {record.get('location') or 'Unknown'}</li>
<li><strong>County:</strong> {record['county']}</li>
<li><strong>Type:</strong> {record['incident_type'] or 'N/A'}</li>
<li><strong>Details:</strong> {record.get('details') or 'N/A'}</li>
</ul>
<p><a href="https://montanablotter.com/record/{record['id']}">View on MontanaBlotter.com</a></p>
""".strip()
    return subject, body_html, body_text


def _send_webhook(url: str, payload: dict) -> bool:
    try:
        resp = requests.post(url, json=payload, timeout=15, headers={"Content-Type": "application/json"})
        return resp.status_code in (200, 201, 202, 204)
    except Exception:
        return False


def _send_slack(webhook_url: str, message: str) -> bool:
    return _send_webhook(webhook_url, {"text": message})


def _send_teams(webhook_url: str, message: str) -> bool:
    return _send_webhook(webhook_url, {"text": message})


def process_new_record(record_id: int) -> dict:
    """Evaluate a new record against all active alert profiles and enqueue notifications."""
    conn = _connect()
    record = conn.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
    if not record:
        conn.close()
        return {"status": "error", "reason": "record_not_found"}

    profiles = conn.execute(
        "SELECT * FROM user_alert_profiles WHERE is_active=1"
    ).fetchall()

    enqueued = 0
    for profile in profiles:
        if not _match_profile(record, profile):
            continue
        user = conn.execute(
            "SELECT email FROM public_users WHERE id=? AND is_active=1", (profile["user_id"],)
        ).fetchone()
        if not user:
            continue

        subject, body_html, body_text = _render_alert_email(record, profile)
        channel = profile["delivery_channel"] or "email"
        recipient = user["email"]

        notif_id = _enqueue_notification(
            conn, profile["id"], profile["user_id"], record_id, channel, recipient,
            subject, body_html, body_text,
        )

        if channel == "webhook" and profile["webhook_url"]:
            payload = {
                "alert_profile": profile["name"],
                "record_id": record_id,
                "date": record["date"],
                "county": record["county"],
                "incident_type": record["incident_type"],
                "location": record.get("location"),
                "details": record.get("details"),
            }
            ok = _send_webhook(profile["webhook_url"], payload)
            conn.execute(
                "UPDATE notification_queue SET status=?, sent_at=datetime('now') WHERE id=?",
                ("delivered" if ok else "failed", notif_id),
            )
            conn.commit()

        if channel == "slack" and profile["slack_channel"]:
            msg = f"*{subject}*\n{body_text[:800]}"
            ok = _send_slack(profile["slack_channel"], msg)
            conn.execute(
                "UPDATE notification_queue SET status=?, sent_at=datetime('now') WHERE id=?",
                ("delivered" if ok else "failed", notif_id),
            )
            conn.commit()

        if channel == "teams" and profile["teams_webhook"]:
            msg = f"**{subject}**\n\n{body_text[:800]}"
            ok = _send_teams(profile["teams_webhook"], msg)
            conn.execute(
                "UPDATE notification_queue SET status=?, sent_at=datetime('now') WHERE id=?",
                ("delivered" if ok else "failed", notif_id),
            )
            conn.commit()

        enqueued += 1

    conn.close()
    return {"status": "ok", "enqueued": enqueued}


def get_recent_alerts_for_user(user_id: int, limit: int = 5) -> List[dict]:
    """Return recent alert notifications for a public user, joined to record context."""
    conn = _connect()
    rows = conn.execute(
        """
        SELECT nq.id, nq.subject, nq.created_at, nq.record_id,
               r.county, r.incident_type
        FROM notification_queue nq
        LEFT JOIN records r ON nq.record_id = r.id
        WHERE nq.user_id = ?
        ORDER BY nq.created_at DESC
        LIMIT ?
        """,
        (user_id, limit),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def retry_failed_notifications(max_retries: int = 3) -> dict:
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM notification_queue WHERE status='failed' AND retry_count<?",
        (max_retries,),
    ).fetchall()
    retried = 0
    for row in rows:
        if row["channel"] == "email":
            # Placeholder: email worker picks up pending items
            conn.execute(
                "UPDATE notification_queue SET status='pending', retry_count=retry_count+1 WHERE id=?",
                (row["id"],),
            )
            retried += 1
    conn.commit()
    conn.close()
    return {"status": "ok", "retried": retried}


def sweep_stale_pending(older_than_hours: int = 24) -> dict:
    conn = _connect()
    cutoff = (datetime.utcnow() - timedelta(hours=older_than_hours)).isoformat()
    conn.execute(
        "UPDATE notification_queue SET status='expired' WHERE status='pending' AND created_at<?",
        (cutoff,),
    )
    conn.commit()
    conn.close()
    return {"status": "ok"}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "retry":
        print(retry_failed_notifications())
    else:
        print("Usage: python alert_engine.py retry")
