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
LOG_PATH = "/root/montanablotter/news_writer.log"

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _emit_event(payload: dict) -> None:
    try:
        publish_agent_event(payload)
    except (RedisError, OSError, PermissionError) as exc:
        logger.warning("news writer event emission skipped: %s", exc)


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:80].strip("-")


def _county_label(value: str) -> str:
    county = (value or "").strip()
    if not county:
        return "Montana"
    return county if county.lower().endswith("county") else f"{county} County"


def _clean_summary(text: str) -> str:
    cleaned_lines: list[str] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            if cleaned_lines and cleaned_lines[-1]:
                cleaned_lines.append("")
            continue
        lowered = line.lower()
        if line.startswith("//"):
            continue
        if "historical perspective" in lowered:
            continue
        cleaned_lines.append(line)

    while cleaned_lines and cleaned_lines[-1] == "":
        cleaned_lines.pop()
    return "\n".join(cleaned_lines).strip()


def _build_excerpt(title: str, facts: dict, summary: str) -> str:
    """Generate a short excerpt (under 200 chars) from facts.

    The summary field often contains a full incident list. We try to extract
    a genuine narrative sentence rather than a template header or list item.
    """
    agency_name = (facts.get("agency_name") or "Authorities").strip()
    county_label = _county_label(str(facts.get("county") or ""))
    incident_date = (facts.get("incident_date") or facts.get("occurred_at") or "").strip()
    city = (facts.get("city") or "").strip()

    # Skip patterns that indicate a list/header/template, not a narrative sentence
    SKIP_PATTERNS = [
        r"(?i)^\s*the\s+\w+\s+responded\s+to\s+the\s+following\s+incidents",
        r"(?i)^\s*incident\s+(list|summary|report)",
        r"(?i)^\s*daily\s+activity\s+report",
        r"(?i)^\s*---+",
        r"(?i)^\s*\d{1,2}:\d{2}\s*[-–]",
        r"(?i)^\s*\d{1,2}:\d{2}:\d{2}\s*[-–]",
        r"(?i)^\s*authorities\s+(in\s+\w+\s+county\s+)?on\s+\d{2}/\d{2}/\d{2}\s+is\s+referenced",
        r"(?i)^\s*this\s+report\s+is\s+based\s+on\s+source\s+material\s+reviewed",
        r"(?i)^\s*the\s+item\s+is\s+being\s+tracked\s+as\s+a\s+factual\s+local-news\s+brief",
        r"(?i)^\s*montana\s+blotter'?s\s+(autonomous\s+newsroom\s+prepared|newsroom\s+pipeline\s+prepared)",
    ]

    def _is_skip(text: str) -> bool:
        return any(re.search(pat, text) for pat in SKIP_PATTERNS)

    # Try to find first real sentence from summary
    if summary:
        sentences = re.split(r"(?<=[.!?])\s+", summary)
        for sent in sentences:
            sent = sent.strip()
            if len(sent) >= 30 and not _is_skip(sent):
                if len(sent) <= 160:
                    return sent
                return sent[:157].rsplit(" ", 1)[0] + "..."

    # Synthetic excerpt as fallback
    parts = [agency_name]
    if city:
        parts.append(f"in {city}")
    elif county_label != "Montana":
        parts.append(f"in {county_label}")
    if incident_date:
        parts.append(f"on {incident_date[:10]}")
    parts.append("— public safety activity from source records.")
    excerpt = " ".join(parts)
    if len(excerpt) > 200:
        excerpt = excerpt[:197].rsplit(" ", 1)[0] + "..."
    return excerpt


def _build_body(title: str, facts: dict) -> str:
    agency_name = (facts.get("agency_name") or "Authorities").strip()
    city = (facts.get("city") or "").strip()
    county_label = _county_label(str(facts.get("county") or ""))
    incident_date = (facts.get("incident_date") or facts.get("occurred_at") or "").strip()
    summary = _clean_summary(str(facts.get("summary") or ""))

    lede_parts = [agency_name]
    if city:
        lede_parts.append(f"in {city}")
    elif county_label != "Montana":
        lede_parts.append(f"in {county_label}")
    if incident_date:
        lede_parts.append(f"on {incident_date[:10]}")
    lede = " ".join(lede_parts).strip()
    if lede:
        lede = f"{lede} is referenced in source material tied to this report."
    else:
        lede = "This report is based on source material reviewed by Montana Blotter."

    context = (
        f"The item is being tracked as a factual local-news brief for {county_label}. "
        f"Montana Blotter's newsroom pipeline prepared this draft from source-linked public material."
    )

    source_note = (
        "This post is limited to information present in the linked source packet and is intended to avoid speculation, "
        "editorial opinion, or conclusions beyond the public record."
    )

    paragraphs = [title, lede]
    if summary:
        paragraphs.append(summary)
    paragraphs.append(context)
    paragraphs.append(source_note)
    return "\n\n".join(part for part in paragraphs if part)


def create_draft_from_candidate(conn: sqlite3.Connection, candidate_id: int) -> int:
    candidate = conn.execute(
        """
        SELECT id, source_type, source_url, headline_hint, facts_json
        FROM story_candidates
        WHERE id = ?
        """,
        (candidate_id,),
    ).fetchone()
    if not candidate:
        raise ValueError(f"Unknown candidate id: {candidate_id}")

    facts = json.loads(candidate["facts_json"] or "{}")
    title = str(candidate["headline_hint"] or "").strip()
    summary = _clean_summary(str(facts.get("summary") or ""))
    # Build a short excerpt — first 1-2 sentences or a synthetic lede, capped at 200 chars
    excerpt = _build_excerpt(title, facts, summary)
    body = _build_body(title, facts)
    slug = _slugify(f"{title}-{candidate_id}")

    cursor = conn.execute(
        """
        INSERT INTO blog_posts (title, slug, body, excerpt, author, published)
        VALUES (?, ?, ?, ?, ?, 0)
        """,
        (title, slug, body, excerpt, "Montana Blotter News Writer"),
    )
    post_id = int(cursor.lastrowid)
    conn.execute(
        """
        INSERT INTO blog_post_sources (blog_post_id, source_url, source_type, notes)
        VALUES (?, ?, ?, ?)
        """,
        (post_id, candidate["source_url"], candidate["source_type"], "writer-agent source packet"),
    )
    conn.execute(
        "UPDATE story_candidates SET status = 'drafted', updated_at = datetime('now') WHERE id = ?",
        (candidate_id,),
    )
    conn.commit()

    _emit_event(
        {
            "agent_id": "news-writer",
            "agent_name": "News Writer",
            "message": f"Created draft {post_id} from candidate {candidate_id}",
            "stage": "draft",
            "status": "working",
            "created_at": datetime.now(UTC).isoformat(),
            "meta": {"candidate_id": candidate_id, "blog_post_id": post_id},
        }
    )
    return post_id


def run() -> int:
    logger.info("news writer run started")
    conn = connect_db()
    row = conn.execute(
        "SELECT id FROM story_candidates WHERE status = 'new' ORDER BY score DESC, id ASC LIMIT 1"
    ).fetchone()
    if row:
        create_draft_from_candidate(conn, int(row["id"]))
    conn.close()
    logger.info("news writer run finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
