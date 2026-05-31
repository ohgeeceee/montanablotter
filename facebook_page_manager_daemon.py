#!/usr/bin/env python3
"""
facebook_page_manager_daemon.py — 24/7 persistent worker that manages the Montana Blotter Facebook page.

Features:
  1. Auto-queues new blog posts every 10 minutes
  2. Comments on relevant crime posts every 30 minutes (3x daily cap)
  3. Tracks engagement metrics
  4. Sends heartbeat to Mission Control every 10 seconds
  5. Graceful shutdown on SIGTERM/SIGINT

Attitude persona: "The Blotter" — direct, no-BS, Montana-proud, slightly provocative.
Never offensive, never targets victims. Focuses on transparency, public records,
and holding agencies accountable.
"""

from __future__ import annotations

import json
import os
import random
import re
import signal
import sqlite3
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DB_PATH = os.getenv("DB_PATH", "/root/montanablotter/blotter.db")
DB_TIMEOUT = float(os.getenv("DB_TIMEOUT_SECONDS", "30"))
BUSY_TIMEOUT_MS = int(os.getenv("DB_BUSY_TIMEOUT_MS", "30000"))
HEARTBEAT_URL = os.getenv("MISSION_CONTROL_HEARTBEAT_URL", "http://127.0.0.1:5000/admin/api/mission-control/heartbeat")

POST_INTERVAL_SECONDS = int(os.getenv("FB_MANAGER_POST_INTERVAL", "600"))  # 10 min
COMMENT_INTERVAL_SECONDS = int(os.getenv("FB_MANAGER_COMMENT_INTERVAL", "1800"))  # 30 min
HEARTBEAT_INTERVAL_SECONDS = 10
MAX_COMMENTS_PER_DAY = 6

GRAPH_API_VERSION = "v22.0"

COMMENT_TEMPLATES = {
    "general": [
        "We covered this on montanablotter.com — public records shouldn't be this hard to find.",
        "This is exactly why we built Montana Blotter. Transparency matters.",
        "The full report is up at montanablotter.com. We read the blotters so you don't have to.",
        "Public safety data belongs to the public. We make it readable at montanablotter.com.",
        "Another one for the archives. See the full breakdown at montanablotter.com.",
    ],
    "drug": [
        "Same story, different county. We track every drug arrest across Montana at montanablotter.com.",
        "The blotters tell the real story. montanablotter.com has the full picture.",
        "Law enforcement works hard. We work hard to make their reports public. montanablotter.com",
    ],
    "dui": [
        "Another DUI. Another record we pulled from the blotter. montanablotter.com",
        "Montana roads deserve better. We track every impaired driving arrest at montanablotter.com.",
    ],
    "theft": [
        "Small towns, big crime. The blotters don't lie. montanablotter.com",
        "Property crime stats are buried in PDFs. We dig them out. montanablotter.com",
    ],
    "assault": [
        "Violence doesn't stay in the dark when the blotter goes public. montanablotter.com",
        "The records are public. The stories matter. montanablotter.com",
    ],
    "warrant": [
        "Warrants served, records logged, public informed. montanablotter.com",
        "Another warrant off the backlog. We track them all at montanablotter.com.",
    ],
    "missing": [
        "Every missing person deserves attention. We amplify the blotter alerts at montanablotter.com.",
        "The blotter mentioned it first. We make sure it's seen. montanablotter.com",
    ],
    "fire": [
        "Fire season in Montana. We track every dispatch call at montanablotter.com.",
        "When the pager goes off, the blotter gets the call. We get the blotter. montanablotter.com",
    ],
}

_shutdown_event = threading.Event()


def _signal_handler(signum, frame) -> None:
    _shutdown_event.set()


signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    return conn


def _get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row and row["value"] is not None else default


def _to_bool(raw: Any, default: bool = False) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _load_fb_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    page_id = _get_setting(conn, "facebook_page_id", "").strip()
    access_token = _get_setting(conn, "facebook_page_access_token", "").strip()
    enabled = _to_bool(_get_setting(conn, "facebook_enabled", "0"))
    return {
        "page_id": page_id,
        "access_token": access_token,
        "enabled": enabled,
    }


def _detect_category(text: str) -> str:
    text = (text or "").lower()
    if any(w in text for w in ["dui", "dwi", "impaired", "intoxicated", "under the influence"]):
        return "dui"
    if any(w in text for w in ["meth", "heroin", "fentanyl", "cocaine", "possession", "narcotic", "drug"]):
        return "drug"
    if any(w in text for w in ["theft", "burglary", "stolen", "shoplifting", "larceny", "property"]):
        return "theft"
    if any(w in text for w in ["assault", "battery", "domestic violence", "dv", "aggravated"]):
        return "assault"
    if any(w in text for w in ["warrant", "bench warrant", "fugitive", "extradition"]):
        return "warrant"
    if any(w in text for w in ["missing", "runaway", "endangered", "amber alert", "silver alert"]):
        return "missing"
    if any(w in text for w in ["fire", "wildfire", "structure fire", "dispatch", "ems", "medical"]):
        return "fire"
    return "general"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS facebook_page_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_type TEXT NOT NULL,
            target_facebook_id TEXT,
            target_url TEXT,
            blog_post_id INTEGER,
            blotter_post_id INTEGER,
            message TEXT,
            category TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            facebook_response_json TEXT,
            error_text TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_fb_actions_type_status ON facebook_page_actions(action_type, status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_fb_actions_created ON facebook_page_actions(created_at)"
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------
def _send_heartbeat(state: str, task: str, detail: str = "") -> None:
    try:
        requests.post(
            HEARTBEAT_URL,
            headers={"X-Internal-Mission-Control": "local", "Content-Type": "application/json"},
            json={
                "agent_id": "facebook_page_manager",
                "display_name": "FB Page Manager",
                "runtime": "hermes",
                "state": state,
                "current_task": task,
                "detail_text": detail,
                "source_kind": "worker",
            },
            timeout=5,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Post mode — queue new blog posts
# ---------------------------------------------------------------------------
def _run_post_mode(conn: sqlite3.Connection, settings: dict[str, Any]) -> dict[str, Any]:
    from facebook_publisher import queue_post

    if not settings.get("enabled"):
        return {"ok": True, "queued": 0, "reason": "facebook_disabled"}

    rows = conn.execute(
        """
        SELECT bp.id
        FROM blog_posts bp
        LEFT JOIN facebook_post_queue fbq ON fbq.blog_post_id = bp.id AND fbq.content_type = 'blog'
        WHERE bp.published = 1
          AND fbq.id IS NULL
        ORDER BY bp.created_at DESC
        LIMIT 3
        """,
    ).fetchall()

    queued = 0
    skipped = 0
    for row in rows:
        result = queue_post(
            blog_post_id=int(row["id"]),
            content_type="blog",
            enqueue_source="fb_page_manager_auto",
            conn=conn,
        )
        if result.get("ok") and (result.get("created") or result.get("requeued")):
            queued += 1
        else:
            skipped += 1

    conn.commit()
    return {"ok": True, "queued": queued, "skipped": skipped}


# ---------------------------------------------------------------------------
# Comment mode — find and comment on relevant posts
# ---------------------------------------------------------------------------
def _search_relevant_posts(settings: dict[str, Any], query: str, limit: int = 5) -> list[dict[str, Any]]:
    access_token = settings.get("access_token", "")
    if not access_token:
        return []

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/search"
    params = {
        "q": query,
        "type": "post",
        "fields": "id,message,created_time,from{name,id},permalink_url",
        "limit": limit,
        "access_token": access_token,
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()
        if not resp.ok or "error" in data:
            return []
        posts = []
        for item in data.get("data", []):
            posts.append({
                "facebook_id": item.get("id"),
                "message": item.get("message", ""),
                "permalink": item.get("permalink_url", ""),
                "from_name": item.get("from", {}).get("name", ""),
                "from_id": item.get("from", {}).get("id", ""),
                "created_time": item.get("created_time", ""),
            })
        return posts
    except Exception:
        return []


def _post_comment(settings: dict[str, Any], post_id: str, message: str) -> dict[str, Any]:
    access_token = settings.get("access_token", "")
    if not access_token:
        return {"ok": False, "error": "no_access_token"}

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{post_id}/comments"
    payload = {
        "message": message,
        "access_token": access_token,
    }
    try:
        resp = requests.post(url, data=payload, timeout=15)
        data = resp.json()
        if resp.ok and "id" in data:
            return {"ok": True, "comment_id": data["id"]}
        return {"ok": False, "error": data.get("error", {}).get("message", "unknown")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _count_today_comments(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) as c FROM facebook_page_actions
        WHERE action_type = 'comment'
          AND status = 'posted'
          AND created_at >= date('now')
        """
    ).fetchone()
    return row["c"] if row else 0


def _run_comment_mode(conn: sqlite3.Connection, settings: dict[str, Any], max_comments: int = 2) -> dict[str, Any]:
    if not settings.get("enabled"):
        return {"ok": True, "commented": 0, "reason": "facebook_disabled"}

    today_count = _count_today_comments(conn)
    if today_count >= MAX_COMMENTS_PER_DAY:
        return {"ok": True, "commented": 0, "reason": f"daily_cap_reached:{today_count}/{MAX_COMMENTS_PER_DAY}"}

    search_queries = [
        "Montana crime",
        "Montana police",
        "Montana sheriff",
        "Montana DUI",
        "Montana arrest",
    ]

    commented = 0
    failed = 0
    skipped = 0

    for query in search_queries:
        if commented >= max_comments or today_count + commented >= MAX_COMMENTS_PER_DAY:
            break

        posts = _search_relevant_posts(settings, query, limit=5)
        for post in posts:
            if commented >= max_comments or today_count + commented >= MAX_COMMENTS_PER_DAY:
                break

            existing = conn.execute(
                "SELECT id FROM facebook_page_actions WHERE target_facebook_id = ? AND action_type = 'comment'",
                (post["facebook_id"],),
            ).fetchone()
            if existing:
                skipped += 1
                continue

            if settings.get("page_id") in post.get("from_id", ""):
                skipped += 1
                continue

            category = _detect_category(post["message"])
            templates = COMMENT_TEMPLATES.get(category, COMMENT_TEMPLATES["general"])
            comment_text = random.choice(templates)

            result = _post_comment(settings, post["facebook_id"], comment_text)

            status = "posted" if result.get("ok") else "failed"
            conn.execute(
                """
                INSERT INTO facebook_page_actions
                    (action_type, target_facebook_id, target_url, message, category, status, facebook_response_json, error_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "comment",
                    post["facebook_id"],
                    post["permalink"],
                    comment_text,
                    category,
                    status,
                    json.dumps(result) if result.get("ok") else None,
                    result.get("error") if not result.get("ok") else None,
                ),
            )
            conn.commit()

            if result.get("ok"):
                commented += 1
            else:
                failed += 1

    return {"ok": True, "commented": commented, "failed": failed, "skipped": skipped}


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main() -> int:
    print(f"[{_now()}] FB Page Manager daemon starting...")
    _send_heartbeat("ready", "Daemon starting up...")

    conn = _connect_db()
    try:
        _ensure_schema(conn)
    except Exception as exc:
        _send_heartbeat("blocked", f"Schema error: {exc}")
        print(f"[{_now()}] Schema error: {exc}", file=sys.stderr)
        return 1

    settings = _load_fb_settings(conn)
    _send_heartbeat("ready", f"Facebook enabled={settings.get('enabled')}. Waiting for work...")

    last_post_time = 0
    last_comment_time = 0
    last_heartbeat_time = 0
    last_post_result: dict[str, Any] = {"queued": 0}
    last_comment_result: dict[str, Any] = {"commented": 0}

    while not _shutdown_event.is_set():
        now = time.time()

        # Heartbeat every 10 seconds
        if now - last_heartbeat_time >= HEARTBEAT_INTERVAL_SECONDS:
            task = f"Watching. Last post: {last_post_result.get('queued', 0)} queued. Last comment: {last_comment_result.get('commented', 0)} posted."
            _send_heartbeat("waiting", task)
            last_heartbeat_time = now

        # Post mode every POST_INTERVAL_SECONDS
        if now - last_post_time >= POST_INTERVAL_SECONDS:
            _send_heartbeat("working", "Queueing new blog posts to Facebook...")
            try:
                last_post_result = _run_post_mode(conn, settings)
                print(f"[{_now()}] Post mode: {last_post_result}")
                _send_heartbeat("waiting", f"Post mode done. Queued {last_post_result.get('queued', 0)}.")
            except Exception as exc:
                print(f"[{_now()}] Post mode error: {exc}", file=sys.stderr)
                _send_heartbeat("blocked", f"Post error: {exc}")
            last_post_time = now

        # Comment mode every COMMENT_INTERVAL_SECONDS
        if now - last_comment_time >= COMMENT_INTERVAL_SECONDS:
            _send_heartbeat("tool_run", "Searching for relevant Facebook posts to comment on...")
            try:
                last_comment_result = _run_comment_mode(conn, settings, max_comments=2)
                print(f"[{_now()}] Comment mode: {last_comment_result}")
                _send_heartbeat("waiting", f"Comment mode done. Posted {last_comment_result.get('commented', 0)}.")
            except Exception as exc:
                print(f"[{_now()}] Comment mode error: {exc}", file=sys.stderr)
                _send_heartbeat("blocked", f"Comment error: {exc}")
            last_comment_time = now

        # Refresh settings periodically (every 5 min)
        if int(now) % 300 == 0:
            try:
                settings = _load_fb_settings(conn)
            except Exception:
                pass

        _shutdown_event.wait(1.0)

    print(f"[{_now()}] Shutdown signal received. Cleaning up...")
    _send_heartbeat("done", "Daemon shutting down gracefully.")
    conn.close()
    print(f"[{_now()}] FB Page Manager daemon stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
