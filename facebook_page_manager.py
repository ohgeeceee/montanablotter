#!/usr/bin/env python3
"""
facebook_page_manager.py — Background worker that manages the Montana Blotter Facebook page.

Features:
  1. Auto-posts new blog posts to the Facebook page (uses existing facebook_publisher queue)
  2. Comments on related crime posts with an edgy, attention-grabbing attitude
     that drives traffic back to montanablotter.com
  3. Tracks engagement metrics
  4. Emits heartbeat to Mission Control

Attitude persona: "The Blotter" — direct, no-BS, Montana-proud, slightly provocative.
Never offensive, never targets victims. Focuses on transparency, public records,
and holding agencies accountable.

Usage:
    python3 facebook_page_manager.py --mode post      # Queue recent blog posts
    python3 facebook_page_manager.py --mode comment    # Find and comment on relevant posts
    python3 facebook_page_manager.py --mode report    # Print engagement summary
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sqlite3
import sys
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

GRAPH_API_VERSION = "v22.0"

# The Blotter persona — edgy but respectful
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
    value = str(raw).strip().lower()
    return value in {"1", "true", "yes", "on"}


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
    """Map text to a crime category for targeted comments."""
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
            action_type TEXT NOT NULL,  -- 'post', 'comment', 'like', 'share'
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
# Mission Control heartbeat
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
# Mode: post — Queue recent blog posts to Facebook
# ---------------------------------------------------------------------------
def _mode_post(conn: sqlite3.Connection, settings: dict[str, Any], limit: int = 5) -> dict[str, Any]:
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
        LIMIT ?
        """,
        (limit,),
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
# Mode: comment — Find relevant Facebook posts and comment
# ---------------------------------------------------------------------------
def _search_relevant_posts(settings: dict[str, Any], query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search Facebook for public posts matching a Montana crime keyword."""
    access_token = settings.get("access_token", "")
    if not access_token:
        return []

    # Search public posts via Graph API
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
                "created_time": item.get("created_time", ""),
            })
        return posts
    except Exception:
        return []


def _post_comment(settings: dict[str, Any], post_id: str, message: str) -> dict[str, Any]:
    """Post a comment on a Facebook post."""
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


def _mode_comment(conn: sqlite3.Connection, settings: dict[str, Any], max_comments: int = 3) -> dict[str, Any]:
    if not settings.get("enabled"):
        return {"ok": True, "commented": 0, "reason": "facebook_disabled"}

    # Search queries — Montana crime keywords
    search_queries = [
        "Montana crime",
        "Montana police",
        "Montana sheriff",
        "Montana DUI",
        "Montana arrest",
        "Montana public safety",
    ]

    commented = 0
    failed = 0
    skipped = 0

    for query in search_queries:
        if commented >= max_comments:
            break

        posts = _search_relevant_posts(settings, query, limit=5)
        for post in posts:
            if commented >= max_comments:
                break

            # Skip if we already commented on this post
            existing = conn.execute(
                "SELECT id FROM facebook_page_actions WHERE target_facebook_id = ? AND action_type = 'comment'",
                (post["facebook_id"],),
            ).fetchone()
            if existing:
                skipped += 1
                continue

            # Skip our own posts
            if settings.get("page_id") in post.get("from_id", ""):
                skipped += 1
                continue

            # Pick category and template
            category = _detect_category(post["message"])
            templates = COMMENT_TEMPLATES.get(category, COMMENT_TEMPLATES["general"])
            comment_text = random.choice(templates)

            # Post the comment
            result = _post_comment(settings, post["facebook_id"], comment_text)

            # Record the action
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
# Mode: report — Print engagement summary
# ---------------------------------------------------------------------------
def _mode_report(conn: sqlite3.Connection) -> dict[str, Any]:
    stats = {}
    for action_type in ["post", "comment", "like", "share"]:
        row = conn.execute(
            "SELECT COUNT(*) as c FROM facebook_page_actions WHERE action_type = ?",
            (action_type,),
        ).fetchone()
        stats[action_type] = row["c"] if row else 0

    recent = conn.execute(
        """
        SELECT action_type, target_facebook_id, message, category, status, created_at
        FROM facebook_page_actions
        ORDER BY created_at DESC
        LIMIT 10
        """
    ).fetchall()

    return {
        "ok": True,
        "stats": stats,
        "recent": [dict(r) for r in recent],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Manage Montana Blotter Facebook page.")
    parser.add_argument("--mode", choices=["post", "comment", "report"], required=True, help="Worker mode")
    parser.add_argument("--limit", type=int, default=5, help="Max items to process")
    parser.add_argument("--max-comments", type=int, default=3, help="Max comments per run")
    args = parser.parse_args()

    _send_heartbeat("working", f"FB Page Manager — mode={args.mode}")

    conn = _connect_db()
    try:
        _ensure_schema(conn)
        settings = _load_fb_settings(conn)

        if args.mode == "post":
            result = _mode_post(conn, settings, limit=args.limit)
        elif args.mode == "comment":
            result = _mode_comment(conn, settings, max_comments=args.max_comments)
        else:
            result = _mode_report(conn)

        _send_heartbeat("done", f"FB Page Manager — {args.mode} complete", json.dumps(result))
        print(json.dumps(result, separators=(",", ":"), sort_keys=True, default=str))
        return 0 if result.get("ok") else 1
    except Exception as exc:
        _send_heartbeat("blocked", f"Error: {exc}")
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
