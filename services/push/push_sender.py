"""
Web Push notification sender using VAPID.

send_push_notification() sends to a single subscription.
broadcast() fans out to all active subscribers (optionally filtered by county).
"""

import json
import logging
import sqlite3
from typing import Optional

import config

LOGGER = logging.getLogger(__name__)


def _vapid_claims() -> dict:
    return {"sub": f"mailto:{config.VAPID_CLAIMS_EMAIL}"}


def _load_vapid():
    from py_vapid import Vapid02
    return Vapid02.from_file(config.VAPID_PRIVATE_KEY_PATH)


def send_push_notification(
    endpoint: str,
    p256dh: str,
    auth: str,
    title: str,
    body: str,
    url: str = "",
    icon: str = "/static/icons/icon-192.png",
) -> bool:
    """Send a single push notification. Returns True on success."""
    from pywebpush import webpush

    payload = json.dumps({
        "title": title,
        "body": body,
        "url": url,
        "icon": icon,
        "badge": "/static/icons/icon-192.png",
    })

    try:
        vapid = _load_vapid()
        webpush(
            subscription_info={"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}},
            data=payload,
            vapid_private_key=vapid,
            vapid_claims=_vapid_claims(),
        )
        return True
    except Exception as exc:
        LOGGER.warning("Push failed endpoint=%.40s... error=%s", endpoint, exc)
        return False


def broadcast(
    conn: sqlite3.Connection,
    title: str,
    body: str,
    url: str = "",
    county_filter: Optional[str] = None,
    icon: str = "/static/icons/icon-192.png",
) -> dict:
    """
    Send push notifications to all active subscribers.
    If county_filter is given, only sends to subscribers for that county
    plus subscribers with no county preference.

    Returns {"sent": int, "failed": int, "deactivated": int}.
    """
    if county_filter:
        rows = conn.execute(
            "SELECT id, endpoint, p256dh, auth FROM push_subscriptions "
            "WHERE active = 1 AND (county_filter = '' OR county_filter = ?)",
            (county_filter,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, endpoint, p256dh, auth FROM push_subscriptions WHERE active = 1"
        ).fetchall()

    sent = failed = deactivated = 0
    for row in rows:
        ok = send_push_notification(
            endpoint=row["endpoint"],
            p256dh=row["p256dh"],
            auth=row["auth"],
            title=title,
            body=body,
            url=url,
            icon=icon,
        )
        if ok:
            conn.execute(
                "UPDATE push_subscriptions SET last_sent_at = datetime('now') WHERE id = ?",
                (row["id"],),
            )
            sent += 1
        else:
            # 410 Gone means the subscription has expired — deactivate it
            conn.execute(
                "UPDATE push_subscriptions SET active = 0 WHERE id = ?",
                (row["id"],),
            )
            deactivated += 1
            failed += 1

    conn.commit()
    LOGGER.info("Push broadcast: sent=%d failed=%d deactivated=%d", sent, failed, deactivated)
    return {"sent": sent, "failed": failed, "deactivated": deactivated}
