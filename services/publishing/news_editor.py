from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import UTC, datetime

import config

from services.agents.events_bus import publish_agent_event
from redis.exceptions import RedisError

DB_PATH = config.DB_PATH
LOG_PATH = "/root/montanablotter/news_editor.log"
DAILY_PUBLISH_CAP = 5

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
SENSITIVE_PATTERNS = [
    re.compile(r"\bvictim\b", re.IGNORECASE),
    re.compile(r"\bminor\b", re.IGNORECASE),
    re.compile(r"\bjuvenile\b", re.IGNORECASE),
    re.compile(r"\b\d{1,2}-year-old\b", re.IGNORECASE),
]

POLICY_PATTERNS = [
    re.compile(r"historical perspective", re.IGNORECASE),
    re.compile(r"^\s*//", re.MULTILINE),
]


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _emit_event(payload: dict) -> None:
    try:
        publish_agent_event(payload)
    except (RedisError, OSError, PermissionError) as exc:
        logger.warning("news editor event emission skipped: %s", exc)


def _record_review(
    conn: sqlite3.Connection,
    *,
    blog_post_id: int,
    story_candidate_id: int,
    decision: str,
    reason: str,
) -> dict[str, str]:
    conn.execute(
        """
        INSERT INTO blog_draft_reviews (blog_post_id, story_candidate_id, decision, reason, evidence_json, reviewer_agent)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            blog_post_id,
            story_candidate_id,
            decision,
            reason,
            json.dumps({"reason": reason}, sort_keys=True),
            "news-editor",
        ),
    )
    conn.commit()
    _emit_event(
        {
            "agent_id": "news-editor",
            "agent_name": "News Editor",
            "message": f"{decision.title()} draft {blog_post_id}: {reason}",
            "stage": "review",
            "status": "done" if decision == "approved" else "blocked",
            "created_at": datetime.now(UTC).isoformat(),
            "meta": {"candidate_id": story_candidate_id, "blog_post_id": blog_post_id, "decision": decision},
        }
    )
    return {"decision": decision, "reason": reason}


def review_and_maybe_publish(conn: sqlite3.Connection, *, blog_post_id: int, story_candidate_id: int) -> dict[str, str]:
    source = conn.execute(
        "SELECT id FROM blog_post_sources WHERE blog_post_id = ?",
        (blog_post_id,),
    ).fetchone()
    if not source:
        return _record_review(
            conn,
            blog_post_id=blog_post_id,
            story_candidate_id=story_candidate_id,
            decision="rejected",
            reason="missing source traceability",
        )

    published_today = conn.execute(
        """
        SELECT COUNT(*)
        FROM blog_posts
        WHERE published = 1
          AND datetime(created_at) >= datetime('now', 'start of day')
        """
    ).fetchone()[0]
    if int(published_today) >= DAILY_PUBLISH_CAP:
        return _record_review(
            conn,
            blog_post_id=blog_post_id,
            story_candidate_id=story_candidate_id,
            decision="rejected",
            reason="daily cap reached",
        )

    post = conn.execute(
        "SELECT title FROM blog_posts WHERE id = ?",
        (blog_post_id,),
    ).fetchone()
    duplicate = conn.execute(
        """
        SELECT id
        FROM blog_posts
        WHERE published = 1
          AND lower(title) = lower(?)
          AND id != ?
          AND datetime(created_at) >= datetime('now', '-3 days')
        """,
        (post["title"], blog_post_id),
    ).fetchone()
    if duplicate:
        return _record_review(
            conn,
            blog_post_id=blog_post_id,
            story_candidate_id=story_candidate_id,
            decision="rejected",
            reason="duplicate recent article",
        )

    post_body = conn.execute(
        "SELECT body FROM blog_posts WHERE id = ?",
        (blog_post_id,),
    ).fetchone()
    body_text = str(post_body["body"] or "")
    if any(pattern.search(body_text) for pattern in SENSITIVE_PATTERNS):
        return _record_review(
            conn,
            blog_post_id=blog_post_id,
            story_candidate_id=story_candidate_id,
            decision="rejected",
            reason="sensitive content requires manual review",
        )

    if any(pattern.search(body_text) for pattern in POLICY_PATTERNS):
        return _record_review(
            conn,
            blog_post_id=blog_post_id,
            story_candidate_id=story_candidate_id,
            decision="rejected",
            reason="policy violation requires manual review",
        )

    conn.execute(
        "UPDATE blog_posts SET published = 1, updated_at = datetime('now') WHERE id = ?",
        (blog_post_id,),
    )
    conn.execute(
        "UPDATE story_candidates SET status = 'published', updated_at = datetime('now') WHERE id = ?",
        (story_candidate_id,),
    )
    conn.commit()
    return _record_review(
        conn,
        blog_post_id=blog_post_id,
        story_candidate_id=story_candidate_id,
        decision="approved",
        reason="passed editor checks",
    )


def run() -> int:
    logger.info("news editor run started")
    conn = connect_db()
    row = conn.execute(
        """
        SELECT bp.id AS blog_post_id, sc.id AS story_candidate_id
        FROM story_candidates sc
        JOIN blog_post_sources bps
          ON bps.source_url = sc.source_url
         AND bps.source_type = sc.source_type
        JOIN blog_posts bp
          ON bp.id = bps.blog_post_id
        WHERE sc.status = 'drafted'
          AND bp.published = 0
        ORDER BY sc.score DESC, sc.id ASC
        LIMIT 1
        """
    ).fetchone()
    if row:
        review_and_maybe_publish(
            conn,
            blog_post_id=int(row["blog_post_id"]),
            story_candidate_id=int(row["story_candidate_id"]),
        )
    conn.close()
    logger.info("news editor run finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
