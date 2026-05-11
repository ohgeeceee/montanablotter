#!/usr/bin/env python3
"""
duplicate_blog_checker.py — Background worker that scans blog_posts for duplicates.

Checks for:
  1. Exact title matches (case-insensitive)
  2. Similar titles via normalized word overlap (>70% shared words)
  3. Exact slug matches
  4. Similar body content via first-200-char overlap (>80%)

Findings are written to blog_duplicate_findings for admin review.
Emits heartbeat to Mission Control so it shows up in the 2D office.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
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

SIMILARITY_THRESHOLD = 0.85  # word overlap for titles
BODY_OVERLAP_THRESHOLD = 0.92  # char overlap for body snippets


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    return conn


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
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
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union else 0.0


def _overlap_ratio(a: str, b: str) -> float:
    """Word-level overlap for body snippets using Jaccard on word sets."""
    a = (a or "").strip()
    b = (b or "").strip()
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return _jaccard(_word_set(a), _word_set(b))


def _body_snippet(body: str, length: int = 600) -> str:
    """Return body stripped of common template boilerplate."""
    text = (body or "").strip()
    # Remove common template headers that cause false positives
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
    # Skip HTML tags
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
        for b in posts[i + 1 :]:
            a_id = int(a["id"])
            b_id = int(b["id"])
            pair = (min(a_id, b_id), max(a_id, b_id))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)

            # 1. Exact title match
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
                continue  # don't double-report

            # 2. Similar title (word overlap)
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

            # 3. Exact slug match
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

            # 4. Similar body snippet
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
    current_pairs = set()
    for f in findings:
        pair = (min(f["post_a_id"], f["post_b_id"]), max(f["post_a_id"], f["post_b_id"]), f["match_type"])
        current_pairs.add(pair)
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
    # Prune stale findings: rows not in current run
    conn.execute(
        """
        DELETE FROM blog_duplicate_findings
        WHERE resolution = 'open'
          AND (post_a_id, post_b_id, match_type) NOT IN (
              SELECT post_a_id, post_b_id, match_type FROM blog_duplicate_findings
              WHERE created_at >= datetime('now', '-1 minute')
          )
        """
    )
    conn.commit()
    return {"inserted": inserted, "skipped": skipped}


# ---------------------------------------------------------------------------
# Mission Control heartbeat
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
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Scan blog_posts for duplicates.")
    parser.add_argument("--auto-resolve", action="store_true", help="Auto-mark old findings as resolved when no longer duplicate.")
    args = parser.parse_args()

    _send_heartbeat("working", "Scanning blog posts for duplicates...")

    conn = _connect_db()
    try:
        _ensure_schema(conn)
        findings = _find_duplicates(conn)
        stats = _store_findings(conn, findings)

        # Auto-resolve stale findings if requested
        if args.auto_resolve:
            open_findings = conn.execute(
                "SELECT id, post_a_id, post_b_id, match_type FROM blog_duplicate_findings WHERE resolution = 'open'"
            ).fetchall()
            resolved = 0
            for row in open_findings:
                # Re-check if these posts still trigger the same match
                a = conn.execute(
                    "SELECT title, slug, body FROM blog_posts WHERE id = ?", (row["post_a_id"],)
                ).fetchone()
                b = conn.execute(
                    "SELECT title, slug, body FROM blog_posts WHERE id = ?", (row["post_b_id"],)
                ).fetchone()
                if not a or not b:
                    # One post was deleted — resolve
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
            stats["auto_resolved"] = resolved

        result = {
            "ok": True,
            "findings": len(findings),
            "inserted_or_updated": stats["inserted"],
            "skipped": stats["skipped"],
        }
        if args.auto_resolve:
            result["auto_resolved"] = stats.get("auto_resolved", 0)

        _send_heartbeat("done", f"Found {len(findings)} duplicates", json.dumps(result))
        print(json.dumps(result, separators=(",", ":"), sort_keys=True))
        return 0
    except Exception as exc:
        _send_heartbeat("blocked", f"Error: {exc}")
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
