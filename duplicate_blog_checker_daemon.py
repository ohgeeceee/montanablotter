#!/usr/bin/env python3
"""
duplicate_blog_checker_daemon.py — 24/7 persistent worker that continuously monitors
blog_posts for duplicates. Runs as a systemd service.

- Polls the database every 5 minutes
- Sends heartbeat to Mission Control every 10 seconds
- Sleeps efficiently between polls
- Graceful shutdown on SIGTERM/SIGINT
"""

from __future__ import annotations

import json
import os
import re
import signal
import sqlite3
import sys
import threading
import time
from datetime import UTC, datetime
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DB_PATH = os.getenv("DB_PATH", "/root/montanablotter/blotter.db")
DB_TIMEOUT = float(os.getenv("DB_TIMEOUT_SECONDS", "30"))
BUSY_TIMEOUT_MS = int(os.getenv("DB_BUSY_TIMEOUT_MS", "30000"))
HEARTBEAT_URL = os.getenv("MISSION_CONTROL_HEARTBEAT_URL", "http://127.0.0.1:5000/admin/api/mission-control/heartbeat")

POLL_INTERVAL_SECONDS = int(os.getenv("DUP_CHECKER_POLL_INTERVAL", "300"))  # 5 min
HEARTBEAT_INTERVAL_SECONDS = 10

SIMILARITY_THRESHOLD = 0.85
BODY_OVERLAP_THRESHOLD = 0.92

# Graceful shutdown
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


def _normalize(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _word_set(text: str) -> set[str]:
    return set(_normalize(text).split())


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _overlap_ratio(a: str, b: str) -> float:
    a = (a or "").strip()
    b = (b or "").strip()
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return _jaccard(_word_set(a), _word_set(b))


def _body_snippet(body: str, length: int = 600) -> str:
    text = (body or "").strip()
    boilerplate_patterns = [
        r"(?i)^\s*Daily Activity Report\s*[-–]\s*",
        r"(?i)^\s*Weekly Safety Report\s*[-–]\s*",
        r"(?i)^\s*Montana Blotter\s*",
        r"(?i)^\s*This report covers\s+",
        r"(?i)^\s*Incident Summary\s*",
        r"(?i)^\s*---\s*",
        r"(?i)^\s*#+\s*",
    ]
    for pat in boilerplate_patterns:
        text = re.sub(pat, "", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:length]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS blog_duplicate_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_a_id INTEGER NOT NULL,
            post_b_id INTEGER NOT NULL,
            match_type TEXT NOT NULL,
            match_score REAL,
            post_a_title TEXT,
            post_b_title TEXT,
            post_a_slug TEXT,
            post_b_slug TEXT,
            resolution TEXT DEFAULT 'open',
            resolved_at TEXT,
            resolved_by TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(post_a_id, post_b_id, match_type)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_dup_findings_open ON blog_duplicate_findings(resolution, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_dup_findings_post ON blog_duplicate_findings(post_a_id, post_b_id)"
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------
def _find_duplicates(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, title, slug, body, excerpt, created_at
        FROM blog_posts
        ORDER BY created_at DESC
        """
    ).fetchall()

    posts = [dict(r) for r in rows]
    findings: list[dict[str, Any]] = []
    seen_pairs: set[tuple[int, int]] = set()

    for i, a in enumerate(posts):
        for b in posts[i + 1:]:
            a_id = int(a["id"])
            b_id = int(b["id"])
            pair = (min(a_id, b_id), max(a_id, b_id))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)

            if (a["title"] or "").strip().lower() == (b["title"] or "").strip().lower():
                findings.append(
                    {
                        "post_a_id": a_id,
                        "post_b_id": b_id,
                        "match_type": "exact_title",
                        "match_score": 1.0,
                        "post_a_title": a["title"],
                        "post_b_title": b["title"],
                        "post_a_slug": a["slug"],
                        "post_b_slug": b["slug"],
                    }
                )
                continue

            title_sim = _jaccard(_word_set(a["title"]), _word_set(b["title"]))
            if title_sim >= SIMILARITY_THRESHOLD:
                findings.append(
                    {
                        "post_a_id": a_id,
                        "post_b_id": b_id,
                        "match_type": "similar_title",
                        "match_score": round(title_sim, 3),
                        "post_a_title": a["title"],
                        "post_b_title": b["title"],
                        "post_a_slug": a["slug"],
                        "post_b_slug": b["slug"],
                    }
                )
                continue

            if (a["slug"] or "").strip() == (b["slug"] or "").strip():
                findings.append(
                    {
                        "post_a_id": a_id,
                        "post_b_id": b_id,
                        "match_type": "exact_slug",
                        "match_score": 1.0,
                        "post_a_title": a["title"],
                        "post_b_title": b["title"],
                        "post_a_slug": a["slug"],
                        "post_b_slug": b["slug"],
                    }
                )
                continue

            body_sim = _overlap_ratio(_body_snippet(a["body"]), _body_snippet(b["body"]))
            if body_sim >= BODY_OVERLAP_THRESHOLD:
                findings.append(
                    {
                        "post_a_id": a_id,
                        "post_b_id": b_id,
                        "match_type": "similar_body",
                        "match_score": round(body_sim, 3),
                        "post_a_title": a["title"],
                        "post_b_title": b["title"],
                        "post_a_slug": a["slug"],
                        "post_b_slug": b["slug"],
                    }
                )

    return findings


def _store_findings(conn: sqlite3.Connection, findings: list[dict[str, Any]]) -> dict[str, int]:
    inserted = 0
    skipped = 0
    for f in findings:
        try:
            conn.execute(
                """
                INSERT INTO blog_duplicate_findings
                    (post_a_id, post_b_id, match_type, match_score,
                     post_a_title, post_b_title, post_a_slug, post_b_slug)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(post_a_id, post_b_id, match_type) DO UPDATE SET
                    match_score = excluded.match_score,
                    post_a_title = excluded.post_a_title,
                    post_b_title = excluded.post_b_title,
                    post_a_slug = excluded.post_a_slug,
                    post_b_slug = excluded.post_b_slug,
                    created_at = datetime('now')
                """,
                (
                    f["post_a_id"],
                    f["post_b_id"],
                    f["match_type"],
                    f["match_score"],
                    f["post_a_title"],
                    f["post_b_title"],
                    f["post_a_slug"],
                    f["post_b_slug"],
                ),
            )
            inserted += 1
        except Exception:
            skipped += 1
    conn.commit()
    return {"inserted": inserted, "skipped": skipped}


def _auto_resolve_stale(conn: sqlite3.Connection) -> int:
    open_findings = conn.execute(
        "SELECT id, post_a_id, post_b_id, match_type FROM blog_duplicate_findings WHERE resolution = 'open'"
    ).fetchall()
    resolved = 0
    for row in open_findings:
        a = conn.execute(
            "SELECT title, slug, body FROM blog_posts WHERE id = ?", (row["post_a_id"],)
        ).fetchone()
        b = conn.execute(
            "SELECT title, slug, body FROM blog_posts WHERE id = ?", (row["post_b_id"],)
        ).fetchone()
        if not a or not b:
            conn.execute(
                "UPDATE blog_duplicate_findings SET resolution = 'resolved_post_deleted', resolved_at = datetime('now') WHERE id = ?",
                (row["id"],),
            )
            resolved += 1
            continue

        still_dup = False
        if row["match_type"] == "exact_title":
            still_dup = (a["title"] or "").strip().lower() == (b["title"] or "").strip().lower()
        elif row["match_type"] == "similar_title":
            still_dup = _jaccard(_word_set(a["title"]), _word_set(b["title"])) >= SIMILARITY_THRESHOLD
        elif row["match_type"] == "exact_slug":
            still_dup = (a["slug"] or "").strip() == (b["slug"] or "").strip()
        elif row["match_type"] == "similar_body":
            still_dup = _overlap_ratio(_body_snippet(a["body"]), _body_snippet(b["body"])) >= BODY_OVERLAP_THRESHOLD

        if not still_dup:
            conn.execute(
                "UPDATE blog_duplicate_findings SET resolution = 'resolved_no_longer_duplicate', resolved_at = datetime('now') WHERE id = ?",
                (row["id"],),
            )
            resolved += 1
    conn.commit()
    return resolved


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------
def _send_heartbeat(state: str, task: str, detail: str = "") -> None:
    try:
        requests.post(
            HEARTBEAT_URL,
            headers={"X-Internal-Mission-Control": "local", "Content-Type": "application/json"},
            json={
                "agent_id": "duplicate_blog_checker",
                "display_name": "Blog Dup-Checker",
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
# Main loop
# ---------------------------------------------------------------------------
def _run_check(conn: sqlite3.Connection) -> dict[str, Any]:
    findings = _find_duplicates(conn)
    stats = _store_findings(conn, findings)
    resolved = _auto_resolve_stale(conn)
    return {
        "findings": len(findings),
        "inserted_or_updated": stats["inserted"],
        "skipped": stats["skipped"],
        "auto_resolved": resolved,
    }


def main() -> int:
    print(f"[{_now()}] Blog Dup-Checker daemon starting...")
    _send_heartbeat("ready", "Daemon starting up, connecting to database...")

    conn = _connect_db()
    try:
        _ensure_schema(conn)
    except Exception as exc:
        _send_heartbeat("blocked", f"Schema error: {exc}")
        print(f"[{_now()}] Schema error: {exc}", file=sys.stderr)
        return 1

    # Initial check
    _send_heartbeat("working", "Running initial duplicate scan...")
    result = _run_check(conn)
    print(f"[{_now()}] Initial check: {result}")
    _send_heartbeat("waiting", f"Initial scan complete. {result['findings']} findings. Watching for changes...")

    last_check_time = time.time()
    last_heartbeat_time = time.time()

    while not _shutdown_event.is_set():
        now = time.time()

        # Heartbeat every 10 seconds
        if now - last_heartbeat_time >= HEARTBEAT_INTERVAL_SECONDS:
            _send_heartbeat("waiting", f"Watching blog_posts. Last check found {result['findings']} duplicates.")
            last_heartbeat_time = now

        # Full check every POLL_INTERVAL_SECONDS
        if now - last_check_time >= POLL_INTERVAL_SECONDS:
            _send_heartbeat("working", "Scanning blog posts for duplicates...")
            try:
                result = _run_check(conn)
                print(f"[{_now()}] Check result: {result}")
                _send_heartbeat("waiting", f"Scan complete. {result['findings']} findings. Watching...")
            except Exception as exc:
                print(f"[{_now()}] Check error: {exc}", file=sys.stderr)
                _send_heartbeat("blocked", f"Scan error: {exc}")
            last_check_time = now

        # Sleep in small chunks so we respond quickly to shutdown
        _shutdown_event.wait(1.0)

    # Shutdown
    print(f"[{_now()}] Shutdown signal received. Cleaning up...")
    _send_heartbeat("done", "Daemon shutting down gracefully.")
    conn.close()
    print(f"[{_now()}] Blog Dup-Checker daemon stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
