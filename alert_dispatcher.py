"""
alert_dispatcher.py — Immediate county alert emails after new blotter ingest.

Called from processor.py after records are inserted:
    from alert_dispatcher import dispatch_alerts
    dispatch_alerts(blotter_id, county)

Also handles Name Watch matching across all active watches.

Respects a 6-hour cooldown per (email, county) pair to prevent spam
when multiple blotters arrive for the same county on the same day.
"""

from __future__ import annotations

import json
import logging
import secrets
import smtplib
import sqlite3
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import config
from blotter_analytics import CATEGORY_LABELS

DB_PATH = config.DB_PATH
BASE_URL = getattr(config, 'BASE_URL', 'https://montanablotter.com')
ALERT_COOLDOWN_HOURS = 6
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Email sending (mirrors morning_briefing.send_email)
# ---------------------------------------------------------------------------

def _send_email(to_addr: str, subject: str, html_body: str) -> bool:
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
    except Exception as e:
        logger.warning("Alert email failed to %s: %s", to_addr, e)
        return False


# ---------------------------------------------------------------------------
# Alert email templates
# ---------------------------------------------------------------------------

def _county_alert_html(
    county: str,
    record_count: int,
    category_counts: dict,
    unsubscribe_url: str,
    blotter_url: str,
) -> str:
    cat_rows = ""
    for cat, label in CATEGORY_LABELS.items():
        n = category_counts.get(cat, 0)
        if n > 0:
            cat_rows += f"<tr><td style='padding:6px 0;color:#555;'>{label}</td><td style='padding:6px 0;font-weight:bold;text-align:right;'>{n}</td></tr>"

    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:Georgia,serif;max-width:560px;margin:0 auto;padding:24px;color:#1E1B18;background:#fff;">
  <div style="border-bottom:3px solid #1E1B18;padding-bottom:12px;margin-bottom:20px;">
    <p style="font-family:'Courier New',monospace;font-size:11px;font-weight:bold;text-transform:uppercase;letter-spacing:0.15em;color:#888;margin:0;">Montana Blotter · County Alert</p>
    <h1 style="font-size:22px;font-weight:bold;margin:8px 0 0;">{county} County — New Blotter Posted</h1>
  </div>
  <p style="font-size:15px;line-height:1.6;">
    <strong>{record_count} new incident{'' if record_count == 1 else 's'}</strong> were logged in
    <strong>{county} County</strong> and are now indexed on Montana Blotter.
  </p>
  {'<table style="width:100%;border-collapse:collapse;margin:16px 0;">' + cat_rows + '</table>' if cat_rows else ''}
  <div style="margin:24px 0;">
    <a href="{blotter_url}" style="display:inline-block;background:#1E1B18;color:#fff;padding:12px 24px;text-decoration:none;font-weight:bold;font-size:14px;border-radius:6px;">
      View Full Report →
    </a>
  </div>
  <hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
  <p style="font-size:11px;color:#aaa;font-family:'Courier New',monospace;">
    You're receiving this because you subscribed to {county} County alerts on MontanaBlotter.com.<br>
    <a href="{unsubscribe_url}" style="color:#aaa;">Unsubscribe</a>
  </p>
</body>
</html>
""".strip()


def _name_watch_html(
    watch_name: str,
    county: str,
    incident_type: str,
    record_url: str,
    unsubscribe_url: str,
) -> str:
    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:Georgia,serif;max-width:560px;margin:0 auto;padding:24px;color:#1E1B18;background:#fff;">
  <div style="border-bottom:3px solid #D4892A;padding-bottom:12px;margin-bottom:20px;">
    <p style="font-family:'Courier New',monospace;font-size:11px;font-weight:bold;text-transform:uppercase;letter-spacing:0.15em;color:#D4892A;margin:0;">Montana Blotter · Name Watch Alert</p>
    <h1 style="font-size:22px;font-weight:bold;margin:8px 0 0;">Name Watch Match: {county} County</h1>
  </div>
  <p style="font-size:15px;line-height:1.6;">
    A new blotter record in <strong>{county} County</strong> may match your Name Watch
    for <strong>"{watch_name}"</strong>.
  </p>
  <p style="font-size:14px;color:#555;">Incident type: <strong>{incident_type}</strong></p>
  <div style="margin:24px 0;">
    <a href="{record_url}" style="display:inline-block;background:#D4892A;color:#fff;padding:12px 24px;text-decoration:none;font-weight:bold;font-size:14px;border-radius:6px;">
      View Record →
    </a>
  </div>
  <p style="font-size:12px;color:#888;line-height:1.5;">
    This alert is based on a text match in public blotter data. Montana Blotter does not
    verify this is the same person as your watch target.
  </p>
  <hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
  <p style="font-size:11px;color:#aaa;font-family:'Courier New',monospace;">
    <a href="{unsubscribe_url}" style="color:#aaa;">Cancel this Name Watch</a>
  </p>
</body>
</html>
""".strip()


# ---------------------------------------------------------------------------
# Core dispatch logic
# ---------------------------------------------------------------------------

def _cooldown_ok(conn: sqlite3.Connection, email: str, county: str) -> bool:
    """Return True if enough time has passed since the last alert to this email+county."""
    row = conn.execute(
        "SELECT last_alerted_at FROM alert_subscriptions WHERE email=? AND county LIKE ? AND active=1",
        (email, f"%{county}%"),
    ).fetchone()
    if not row or not row[0]:
        return True
    try:
        last = datetime.fromisoformat(row[0]).replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - last > timedelta(hours=ALERT_COOLDOWN_HOURS)
    except Exception:
        return True


def dispatch_alerts(blotter_id: int, county: str, db_path: str = DB_PATH) -> int:
    """
    Send county alerts and name watch alerts for a newly ingested blotter.
    Returns total number of emails sent.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Fetch new records for this blotter
    records = conn.execute(
        "SELECT id, incident_type, details, charge_category FROM records WHERE blotter_id=?",
        (blotter_id,),
    ).fetchall()

    if not records:
        conn.close()
        return 0

    # Summarize by charge category
    category_counts: dict[str, int] = {}
    for r in records:
        cat = r['charge_category'] or 'other'
        category_counts[cat] = category_counts.get(cat, 0) + 1

    sent = 0

    # --- County alert subscriptions ---
    subscribers = conn.execute(
        """
        SELECT id, email, alert_types, token
        FROM alert_subscriptions
        WHERE county LIKE ? AND active=1
        """,
        (f"%{county}%",),
    ).fetchall()

    for sub in subscribers:
        if not _cooldown_ok(conn, sub['email'], county):
            continue

        # Check if any record matches their alert_types filter
        try:
            alert_types = json.loads(sub['alert_types'] or '["all"]')
        except Exception:
            alert_types = ['all']

        if 'all' not in alert_types:
            matching = any(
                (r['charge_category'] or 'other') in alert_types for r in records
            )
            if not matching:
                continue

        unsubscribe_url = f"{BASE_URL}/alerts/unsubscribe?token={sub['token']}"
        blotter_url = f"{BASE_URL}/?county={county.replace(' ', '+')}"

        # Filter category counts to only what they subscribed to
        display_counts = category_counts if 'all' in alert_types else {
            k: v for k, v in category_counts.items() if k in alert_types
        }

        html = _county_alert_html(
            county=county,
            record_count=len(records),
            category_counts=display_counts,
            unsubscribe_url=unsubscribe_url,
            blotter_url=blotter_url,
        )
        subject = f"[{county} County] {len(records)} new incident{'s' if len(records) != 1 else ''} — Montana Blotter"

        if _send_email(sub['email'], subject, html):
            conn.execute(
                "UPDATE alert_subscriptions SET last_alerted_at=datetime('now') WHERE id=?",
                (sub['id'],),
            )
            sent += 1

    # --- Name watches ---
    # Build a text corpus from all records to match against
    record_texts = [
        (r['id'], f"{r['incident_type'] or ''} {r['details'] or ''}".lower())
        for r in records
    ]

    watches = conn.execute(
        """
        SELECT id, email, watch_name, token
        FROM name_watches
        WHERE active=1 AND (county IS NULL OR county='' OR county LIKE ?)
        """,
        (f"%{county}%",),
    ).fetchall()

    for watch in watches:
        name_lower = (watch['watch_name'] or '').lower().strip()
        if not name_lower:
            continue

        matched_record_id = None
        matched_incident = None
        for rec_id, text in record_texts:
            if name_lower in text:
                matched_record_id = rec_id
                # Get the incident type for display
                for r in records:
                    if r['id'] == rec_id:
                        matched_incident = r['incident_type'] or 'Incident'
                        break
                break

        if matched_record_id is None:
            continue

        # Cooldown for name watches (check last_alerted_at)
        try:
            last_alerted = watch['last_alerted_at'] or ''
            if last_alerted:
                last = datetime.fromisoformat(last_alerted).replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) - last < timedelta(hours=ALERT_COOLDOWN_HOURS):
                    continue
        except Exception:
            pass

        unsubscribe_url = f"{BASE_URL}/alerts/name-watch/cancel?token={watch['token']}"
        record_url = f"{BASE_URL}/record/{matched_record_id}"

        html = _name_watch_html(
            watch_name=watch['watch_name'],
            county=county,
            incident_type=matched_incident or 'Incident',
            record_url=record_url,
            unsubscribe_url=unsubscribe_url,
        )
        subject = f"Name Watch Alert: Match in {county} County — Montana Blotter"

        if _send_email(watch['email'], subject, html):
            conn.execute(
                "UPDATE name_watches SET last_alerted_at=datetime('now') WHERE id=?",
                (watch['id'],),
            )
            sent += 1

    conn.commit()
    conn.close()

    if sent:
        logger.info("dispatch_alerts: blotter_id=%d county=%s sent=%d", blotter_id, county, sent)

    return sent


# ---------------------------------------------------------------------------
# Subscription helpers (used by app.py routes)
# ---------------------------------------------------------------------------

def subscribe_county_alert(
    email: str,
    county: str,
    alert_types: list[str],
    source: str = 'web',
    db_path: str = DB_PATH,
) -> str:
    """
    Create or update a county alert subscription.
    Returns the unsubscribe token.
    """
    token = secrets.token_urlsafe(32)
    alert_types_json = json.dumps(alert_types or ['all'])
    conn = sqlite3.connect(db_path)

    existing = conn.execute(
        "SELECT id, token FROM alert_subscriptions WHERE email=? AND county=?",
        (email, county),
    ).fetchone()

    if existing:
        conn.execute(
            """
            UPDATE alert_subscriptions
            SET alert_types=?, active=1, source=?
            WHERE id=?
            """,
            (alert_types_json, source, existing[0]),
        )
        token = existing[1]
    else:
        conn.execute(
            """
            INSERT INTO alert_subscriptions (email, county, alert_types, token, source)
            VALUES (?, ?, ?, ?, ?)
            """,
            (email, county, alert_types_json, token, source),
        )

    conn.commit()
    conn.close()
    return token


def subscribe_name_watch(
    email: str,
    watch_name: str,
    county: Optional[str],
    db_path: str = DB_PATH,
) -> str:
    """Create a name watch. Returns the cancel token."""
    token = secrets.token_urlsafe(32)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO name_watches (email, watch_name, county, token) VALUES (?, ?, ?, ?)",
        (email, watch_name.strip(), county or None, token),
    )
    conn.commit()
    conn.close()
    return token


def cancel_alert_subscription(token: str, db_path: str = DB_PATH) -> Optional[str]:
    """Deactivate a county alert subscription by token. Returns email or None."""
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT email FROM alert_subscriptions WHERE token=?", (token,)
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE alert_subscriptions SET active=0 WHERE token=?", (token,)
        )
        conn.commit()
    conn.close()
    return row[0] if row else None


def cancel_name_watch(token: str, db_path: str = DB_PATH) -> Optional[str]:
    """Deactivate a name watch by token. Returns email or None."""
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT email FROM name_watches WHERE token=?", (token,)
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE name_watches SET active=0 WHERE token=?", (token,)
        )
        conn.commit()
    conn.close()
    return row[0] if row else None
