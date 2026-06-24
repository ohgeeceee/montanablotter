"""
Expo push notification sender for the mobile app.

register_mobile_push_token() stores Expo push tokens.
send_expo_push_notification() sends to a single token.
broadcast_expo_push() fans out to active tokens.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Optional

import requests

LOGGER = logging.getLogger(__name__)
EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


def register_mobile_push_token(
    conn: sqlite3.Connection,
    *,
    expo_push_token: str,
    public_user_id: Optional[int] = None,
    platform: str = "",
    device_id: str = "",
    county_filter: str = "",
    alert_types: Optional[list[str]] = None,
) -> int:
    """
    Register or reactivate an Expo push token.
    Returns the row id.
    """
    expo_push_token = (expo_push_token or "").strip()
    if not expo_push_token:
        raise ValueError("expo_push_token is required")

    alert_types_json = json.dumps(alert_types) if alert_types else "[\"all\"]"

    existing = conn.execute(
        "SELECT id FROM mobile_push_tokens WHERE expo_push_token = ?",
        (expo_push_token,),
    ).fetchone()

    if existing:
        conn.execute(
            """
            UPDATE mobile_push_tokens
            SET public_user_id = ?, platform = ?, device_id = ?, county_filter = ?,
                alert_types = ?, is_active = 1, updated_at = datetime('now')
            WHERE id = ?
            """,
            (
                public_user_id,
                platform,
                device_id,
                county_filter,
                alert_types_json,
                existing["id"],
            ),
        )
        token_id = existing["id"]
    else:
        cursor = conn.execute(
            """
            INSERT INTO mobile_push_tokens (
                public_user_id, expo_push_token, platform, device_id, county_filter,
                alert_types, is_active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1, datetime('now'), datetime('now'))
            """,
            (
                public_user_id,
                expo_push_token,
                platform,
                device_id,
                county_filter,
                alert_types_json,
            ),
        )
        token_id = cursor.lastrowid

    conn.commit()
    return token_id


def deactivate_mobile_push_token(conn: sqlite3.Connection, expo_push_token: str) -> None:
    """Mark an Expo push token as inactive (e.g., on logout or unregister)."""
    conn.execute(
        "UPDATE mobile_push_tokens SET is_active = 0, updated_at = datetime('now') WHERE expo_push_token = ?",
        (expo_push_token,),
    )
    conn.commit()


def _build_expo_payload(
    token: str,
    title: str,
    body: str,
    data: Optional[dict] = None,
    sound: str = "default",
    badge: int = 1,
) -> dict:
    payload = {
        "to": token,
        "title": title,
        "body": body,
        "sound": sound,
        "badge": badge,
    }
    if data:
        payload["data"] = data
    return payload


def send_expo_push_notification(
    expo_push_token: str,
    title: str,
    body: str,
    data: Optional[dict] = None,
) -> dict:
    """
    Send a single Expo push notification.
    Returns the Expo API response dict.
    """
    payload = _build_expo_payload(expo_push_token, title, body, data)
    try:
        response = requests.post(
            EXPO_PUSH_URL,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip, deflate",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        LOGGER.warning("Expo push failed token=%.20s... error=%s", expo_push_token, exc)
        return {"error": str(exc)}


def broadcast_expo_push(
    conn: sqlite3.Connection,
    title: str,
    body: str,
    data: Optional[dict] = None,
    county_filter: Optional[str] = None,
) -> dict:
    """
    Send Expo push notifications to all active mobile tokens.
    If county_filter is given, also sends to subscribers with no county preference.
    """
    if county_filter:
        rows = conn.execute(
            """
            SELECT id, expo_push_token FROM mobile_push_tokens
            WHERE is_active = 1 AND (county_filter = '' OR county_filter = ?)
            """,
            (county_filter,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, expo_push_token FROM mobile_push_tokens WHERE is_active = 1"
        ).fetchall()

    sent = failed = deactivated = 0
    for row in rows:
        result = send_expo_push_notification(row["expo_push_token"], title, body, data)
        if result.get("data", {}).get("status") == "ok":
            conn.execute(
                "UPDATE mobile_push_tokens SET last_sent_at = datetime('now') WHERE id = ?",
                (row["id"],),
            )
            sent += 1
        else:
            error = (result.get("data", {}) or {}).get("message") or result.get("error")
            # Expo returns DeviceNotRegistered for invalid tokens.
            if error and "not registered" in str(error).lower():
                conn.execute(
                    "UPDATE mobile_push_tokens SET is_active = 0 WHERE id = ?",
                    (row["id"],),
                )
                deactivated += 1
            failed += 1

    conn.commit()
    LOGGER.info("Expo push broadcast: sent=%d failed=%d deactivated=%d", sent, failed, deactivated)
    return {"sent": sent, "failed": failed, "deactivated": deactivated}
