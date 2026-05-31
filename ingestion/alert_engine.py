"""
Record Alert Engine
Matches new records against subscriber alert rules and generates notifications.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db

logger = logging.getLogger("alert_engine")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
    logger.addHandler(handler)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------
@dataclass
class AlertRule:
    id: int
    user_id: int
    rule_type: str  # name, address, keyword, county_feed
    value: str
    channels: list[str]  # email, sms, push


@dataclass
class AlertMatch:
    rule: AlertRule
    record_type: str
    record_id: int
    reason: str
    formatted_message: str


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def ensure_schema(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS alert_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            rule_type TEXT NOT NULL CHECK(rule_type IN ('name','address','keyword','county_feed')),
            value TEXT NOT NULL,
            channels TEXT NOT NULL DEFAULT '["email"]',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_alert_rules_user ON alert_rules(user_id, is_active)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_alert_rules_type ON alert_rules(rule_type, value)"
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS alert_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_id INTEGER NOT NULL,
            record_type TEXT NOT NULL,
            record_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            channel TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            sent_at TEXT,
            error TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_alert_notifications_status ON alert_notifications(status, created_at)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_alert_notifications_rule ON alert_notifications(rule_id)"
    )

    conn.commit()


# ---------------------------------------------------------------------------
# Rule loading
# ---------------------------------------------------------------------------
def load_active_rules(conn: sqlite3.Connection) -> list[AlertRule]:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, user_id, rule_type, value, channels FROM alert_rules WHERE is_active = 1"
    )
    rules = []
    for row in cursor.fetchall():
        rules.append(
            AlertRule(
                id=row["id"],
                user_id=row["user_id"],
                rule_type=row["rule_type"],
                value=row["value"],
                channels=json.loads(row["channels"]),
            )
        )
    return rules


# ---------------------------------------------------------------------------
# Matching logic
# ---------------------------------------------------------------------------
def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower())


def _match_name(record_name: str | None, rule_value: str) -> bool:
    if not record_name:
        return False
    return _normalize_name(rule_value) in _normalize_name(record_name)


def _match_address(record_location: str | None, rule_value: str) -> bool:
    if not record_location:
        return False
    return rule_value.lower() in record_location.lower()


def _match_keyword(record_text: str | None, rule_value: str) -> bool:
    if not record_text:
        return False
    return rule_value.lower() in record_text.lower()


def _match_county(record_county: str | None, rule_value: str) -> bool:
    if not record_county:
        return False
    return rule_value.lower() == record_county.lower()


def check_record_against_rules(
    record: dict[str, Any],
    record_type: str,
    rules: list[AlertRule],
) -> list[AlertMatch]:
    """Check a single record against all alert rules."""
    matches: list[AlertMatch] = []

    # Normalize record fields
    name = record.get("person_name") or record.get("defendant_name") or record.get("employee_name") or record.get("name")
    location = record.get("location") or record.get("address")
    county = record.get("county") or record.get("county_name")
    text = " ".join(str(v) for v in record.values() if v is not None)

    for rule in rules:
        matched = False
        reason = ""

        if rule.rule_type == "name" and _match_name(name, rule.value):
            matched = True
            reason = f"Name match: '{rule.value}' appears in record"
        elif rule.rule_type == "address" and _match_address(location, rule.value):
            matched = True
            reason = f"Address match: '{rule.value}' appears in record"
        elif rule.rule_type == "keyword" and _match_keyword(text, rule.value):
            matched = True
            reason = f"Keyword match: '{rule.value}' found in record"
        elif rule.rule_type == "county_feed" and _match_county(county, rule.value):
            matched = True
            reason = f"County feed: new record in {rule.value}"

        if matched:
            msg = _format_alert(record, record_type, rule, reason)
            matches.append(
                AlertMatch(
                    rule=rule,
                    record_type=record_type,
                    record_id=record.get("id", 0),
                    reason=reason,
                    formatted_message=msg,
                )
            )

    return matches


def _format_alert(record: dict[str, Any], record_type: str, rule: AlertRule, reason: str) -> str:
    """Generate a human-readable alert message."""
    name = record.get("person_name") or record.get("defendant_name") or record.get("employee_name") or record.get("name") or "Unknown"
    county = record.get("county") or record.get("county_name") or "Unknown"
    date = record.get("booking_at") or record.get("violation_date") or record.get("reported_at") or record.get("published_date") or record.get("effective_date") or "recently"
    details = record.get("charges") or record.get("violation") or record.get("incident") or record.get("description") or ""

    type_labels = {
        "jail_booking": "arrest record",
        "fwp_violation": "FWP enforcement action",
        "highway_crash": "highway incident",
        "license_sanction": "professional license sanction",
        "federal_case": "federal court case",
        "public_salary": "public salary record",
        "government_contract": "government contract",
        "government_expenditure": "government expenditure",
    }
    label = type_labels.get(record_type, record_type.replace("_", " "))

    msg = (
        f"Alert: {name} appears in a new {label} in {county} ({date}).\n"
        f"Details: {details[:200]}...\n"
        f"Reason: {reason}\n"
        f"This matches your {rule.rule_type} alert for '{rule.value}'."
    )
    return msg


# ---------------------------------------------------------------------------
# Queue notifications
# ---------------------------------------------------------------------------
def queue_notifications(conn: sqlite3.Connection, matches: list[AlertMatch]) -> int:
    cursor = conn.cursor()
    queued = 0
    for match in matches:
        for channel in match.rule.channels:
            cursor.execute(
                """
                INSERT INTO alert_notifications
                (rule_id, record_type, record_id, message, channel, status)
                VALUES (?, ?, ?, ?, ?, 'pending')
                """,
                (match.rule.id, match.record_type, match.record_id, match.formatted_message, channel),
            )
            queued += 1
    conn.commit()
    return queued


# ---------------------------------------------------------------------------
# Public API — run after any ingestion
# ---------------------------------------------------------------------------
def process_new_record(
    record: dict[str, Any],
    record_type: str,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Evaluate a new record against all alert rules and queue notifications."""
    close_conn = conn is None
    if conn is None:
        conn = db.connect_db()
    ensure_schema(conn)

    rules = load_active_rules(conn)
    if not rules:
        if close_conn:
            conn.close()
        return {"status": "no_rules", "matches": 0}

    matches = check_record_against_rules(record, record_type, rules)
    queued = queue_notifications(conn, matches) if matches else 0

    if close_conn:
        conn.close()
    return {"status": "ok", "matches": len(matches), "notifications_queued": queued}


def run_alert_delivery(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Process pending notifications (email, SMS, push)."""
    close_conn = conn is None
    if conn is None:
        conn = db.connect_db()
    ensure_schema(conn)

    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, rule_id, message, channel FROM alert_notifications WHERE status = 'pending' ORDER BY created_at LIMIT 100"
    )
    pending = cursor.fetchall()

    sent = 0
    failed = 0
    for row in pending:
        notif_id = row["id"]
        channel = row["channel"]

        try:
            if channel == "email":
                _send_email(row, conn)
            elif channel == "sms":
                _send_sms(row, conn)
            elif channel == "push":
                _send_push(row, conn)
            else:
                logger.warning("Unknown channel %s for notification %s", channel, notif_id)
                continue

            cursor.execute(
                "UPDATE alert_notifications SET status = 'sent', sent_at = datetime('now') WHERE id = ?",
                (notif_id,),
            )
            sent += 1
        except Exception as exc:
            logger.error("Failed to send notification %s: %s", notif_id, exc)
            cursor.execute(
                "UPDATE alert_notifications SET status = 'failed', error = ? WHERE id = ?",
                (str(exc), notif_id),
            )
            failed += 1

    conn.commit()
    if close_conn:
        conn.close()
    return {"status": "ok", "sent": sent, "failed": failed}


def _resolve_user_email(rule_id: int, conn: sqlite3.Connection) -> str | None:
    """Look up the email address for the user who owns an alert rule."""
    row = conn.execute(
        "SELECT u.email FROM alert_rules ar JOIN users u ON u.id = ar.user_id WHERE ar.id = ?",
        (rule_id,),
    ).fetchone()
    if not row:
        return None
    email = (row[0] or "").strip()
    return email or None


def _send_email(row: sqlite3.Row, conn: sqlite3.Connection) -> None:
    """Send an alert notification via SMTP using the project's existing mail config."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from services.alerts.legacy import send_plaintext_email

    to_addr = _resolve_user_email(row["rule_id"], conn)
    if not to_addr:
        raise RuntimeError(f"No email address found for rule_id={row['rule_id']}")

    message = row["message"]
    first_line = message.splitlines()[0] if message else "Montana Blotter Alert"
    subject = first_line[:80] if first_line.startswith("Alert:") else f"Alert: {first_line[:74]}"

    ok = send_plaintext_email([to_addr], subject, message)
    if not ok:
        raise RuntimeError(f"SMTP delivery failed for {to_addr} (check SMTP config)")
    logger.info("Email alert sent to %s (rule_id=%s)", to_addr, row["rule_id"])


def _send_sms(row: sqlite3.Row, conn: sqlite3.Connection) -> None:
    raise NotImplementedError("SMS delivery not yet configured (Twilio integration pending)")


def _send_push(row: sqlite3.Row, conn: sqlite3.Connection) -> None:
    raise NotImplementedError("Push delivery not yet configured (web push integration pending)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Alert engine")
    parser.add_argument("--deliver", action="store_true", help="Process pending notifications")
    args = parser.parse_args()

    if args.deliver:
        result = run_alert_delivery()
        logger.info("Delivery result: %s", result)
    else:
        logger.info("Alert engine loaded. Use --deliver to send pending notifications.")
