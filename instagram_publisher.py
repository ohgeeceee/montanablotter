"""
Instagram Graph API publisher for Montana Blotter.

Posting requires a Business/Creator Instagram account linked to the Facebook Page.
The Facebook Page Access Token is reused — no separate Instagram token needed.

Two-step flow:
  1. POST /{ig_user_id}/media  → creation_id
  2. POST /{ig_user_id}/media_publish?creation_id={id}  → published media_id
"""

import logging
import os
import sqlite3
from typing import Any, Dict, Optional

import requests

import config

LOGGER = logging.getLogger(__name__)

DB_PATH = config.DB_PATH
GRAPH_API_VERSION = getattr(config, "FACEBOOK_GRAPH_API_VERSION", "v22.0")


def _connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return (row["value"] if row and row["value"] is not None else default)


def _to_bool(raw: Any, default: bool = False) -> bool:
    if raw is None:
        return default
    value = str(raw).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off", ""}:
        return False
    return default


def load_instagram_settings(conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    owns_conn = conn is None
    if owns_conn:
        conn = _connect_db()
    try:
        ig_user_id = (
            _get_setting(conn, "instagram_user_id")
            or (os.getenv("MB_INSTAGRAM_BUSINESS_ACCOUNT_ID") or getattr(config, "INSTAGRAM_BUSINESS_ACCOUNT_ID", "")).strip()
        )
        access_token = (
            _get_setting(conn, "instagram_access_token")
            or (os.getenv("MB_FACEBOOK_PAGE_ACCESS_TOKEN") or getattr(config, "FACEBOOK_PAGE_ACCESS_TOKEN", "")).strip()
        )
        enabled = _to_bool(_get_setting(conn, "instagram_enabled", "0"))
        return {"ig_user_id": ig_user_id, "access_token": access_token, "enabled": enabled}
    finally:
        if owns_conn and conn is not None:
            conn.close()


def post_to_instagram(
    caption: str,
    image_url: str,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """
    Publish one image post to Instagram.

    Returns {"success": bool, "media_id": str|None, "permalink": str|None, "error": str|None}.
    """
    owns_conn = conn is None
    if owns_conn:
        conn = _connect_db()

    try:
        settings = load_instagram_settings(conn)
        ig_user_id = settings["ig_user_id"]
        access_token = settings["access_token"]

        if not ig_user_id or not access_token:
            return {
                "success": False, "media_id": None, "permalink": None,
                "error": "Missing INSTAGRAM_BUSINESS_ACCOUNT_ID or access token",
            }

        base = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

        # Step 1: create media container
        create_resp = requests.post(
            f"{base}/{ig_user_id}/media",
            params={"image_url": image_url, "caption": caption, "access_token": access_token},
            timeout=30,
        )
        create_data = create_resp.json()
        if "error" in create_data:
            msg = create_data["error"].get("message", str(create_data["error"]))
            LOGGER.error("Instagram media container error: %s", msg)
            return {"success": False, "media_id": None, "permalink": None, "error": msg}

        creation_id = create_data.get("id")
        if not creation_id:
            return {
                "success": False, "media_id": None, "permalink": None,
                "error": f"No creation_id returned: {create_data}",
            }

        # Step 2: publish
        publish_resp = requests.post(
            f"{base}/{ig_user_id}/media_publish",
            params={"creation_id": creation_id, "access_token": access_token},
            timeout=30,
        )
        publish_data = publish_resp.json()
        if "error" in publish_data:
            msg = publish_data["error"].get("message", str(publish_data["error"]))
            LOGGER.error("Instagram publish error: %s", msg)
            return {"success": False, "media_id": None, "permalink": None, "error": msg}

        media_id = publish_data.get("id")
        LOGGER.info("Instagram post published: media_id=%s", media_id)
        return {"success": True, "media_id": media_id, "permalink": None, "error": None}

    except Exception as exc:
        LOGGER.exception("Instagram post_to_instagram failed")
        return {"success": False, "media_id": None, "permalink": None, "error": str(exc)}
    finally:
        if owns_conn and conn is not None:
            conn.close()
