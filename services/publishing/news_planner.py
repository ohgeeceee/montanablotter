from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from xml.etree import ElementTree as ET

import config
import requests

from services.agents.events_bus import publish_agent_event
from redis.exceptions import RedisError
from core.news_source_registry import is_allowed_source_url

DB_PATH = config.DB_PATH
LOG_PATH = "/root/montanablotter/news_planner.log"
EXTERNAL_SOURCE_PATH = "/root/montanablotter/configs/newsroom_sources.json"

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


def _emit_event(payload: dict[str, Any]) -> None:
    try:
        publish_agent_event(payload)
    except (RedisError, OSError, PermissionError) as exc:
        logger.warning("news planner event emission skipped: %s", exc)


def _score_candidate(facts: dict[str, Any]) -> float:
    score = 0.0
    if facts.get("county"):
        score += 3
    if facts.get("agency_name"):
        score += 2
    if facts.get("incident_date") or facts.get("occurred_at"):
        score += 2
    if facts.get("summary"):
        score += 2
    if facts.get("internal_post_id"):
        score += 3
    return score


def build_dedupe_key(packet: dict[str, Any]) -> str:
    normalized = {
        "headline_hint": str(packet.get("headline_hint") or "").strip().lower(),
        "location_label": str(packet.get("location_label") or "").strip().lower(),
        "occurred_at": str(packet.get("occurred_at") or "")[:19],
        "source_url": str(packet.get("source_url") or "").strip().lower(),
    }
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_candidate_packet(*, source_type: str, source_url: str, headline_hint: str, facts: dict[str, Any]) -> dict[str, Any]:
    if not is_allowed_source_url(source_url):
        raise ValueError(f"Source URL is not allow-listed: {source_url}")

    packet = {
        "source_type": source_type,
        "source_url": source_url,
        "headline_hint": headline_hint.strip(),
        "facts_json": json.dumps(facts, sort_keys=True),
        "location_label": str(facts.get("county") or facts.get("location") or "").strip(),
        "occurred_at": str(facts.get("occurred_at") or facts.get("incident_date") or datetime.now(UTC).isoformat()),
        "agency_name": str(facts.get("agency_name") or "").strip(),
        "status": "new",
        "score": float(facts.get("score") or _score_candidate(facts)),
    }
    packet["dedupe_key"] = build_dedupe_key(packet)
    return packet


def persist_candidate(conn: sqlite3.Connection, packet: dict[str, Any]) -> int:
    existing = conn.execute(
        "SELECT id FROM story_candidates WHERE dedupe_key = ?",
        (packet["dedupe_key"],),
    ).fetchone()
    if existing:
        return int(existing["id"])

    cursor = conn.execute(
        """
        INSERT INTO story_candidates (
            source_type,
            source_url,
            headline_hint,
            facts_json,
            location_label,
            occurred_at,
            agency_name,
            dedupe_key,
            status,
            score
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            packet["source_type"],
            packet["source_url"],
            packet["headline_hint"],
            packet["facts_json"],
            packet["location_label"],
            packet["occurred_at"],
            packet["agency_name"],
            packet["dedupe_key"],
            packet["status"],
            packet["score"],
        ),
    )
    conn.commit()
    candidate_id = int(cursor.lastrowid)
    _emit_event(
        {
            "agent_id": "news-planner",
            "agent_name": "News Planner",
            "message": f"Queued story candidate {candidate_id}",
            "stage": "plan",
            "status": "working",
            "created_at": datetime.now(UTC).isoformat(),
            "meta": {"candidate_id": candidate_id},
        }
    )
    return candidate_id


def fetch_internal_story_packets(conn: sqlite3.Connection, *, limit: int = 5) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, title, summary, county, city, agency_name, incident_date
        FROM posts
        WHERE COALESCE(audit_status, 'pending') = 'clean'
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    packets: list[dict[str, Any]] = []
    for row in rows:
        facts = {
            "summary": row["summary"] or "",
            "county": row["county"] or "",
            "city": row["city"] or "",
            "agency_name": row["agency_name"] or "",
            "incident_date": row["incident_date"] or "",
            "internal_post_id": int(row["id"]),
            "score": 10,
        }
        packets.append(
            normalize_candidate_packet(
                source_type="montanablotter",
                source_url=f"https://montanablotter.com/posts/{int(row['id'])}",
                headline_hint=row["title"] or "Montana Blotter report",
                facts=facts,
            )
        )
    return packets


def fetch_external_story_packets() -> list[dict[str, Any]]:
    if not os.path.exists(EXTERNAL_SOURCE_PATH):
        return []

    with open(EXTERNAL_SOURCE_PATH, "r", encoding="utf-8") as handle:
        rows = json.load(handle)

    packets: list[dict[str, Any]] = []
    for row in rows:
        source_url = str(row.get("source_url") or "").strip()
        if not source_url or not is_allowed_source_url(source_url):
            continue
        facts = dict(row.get("facts") or {})
        if "score" not in facts:
            facts["score"] = 8
        source_type = str(row.get("source_type") or "montana_news")
        source_format = str(row.get("format") or "").strip().lower()
        if source_format == "rss":
            packets.extend(_fetch_rss_packets(source_type=source_type, source_url=source_url, base_facts=facts))
            continue
        if source_format == "html":
            packets.extend(_fetch_html_packets(source_type=source_type, source_url=source_url, base_facts=facts, row=row))
            continue
        if str(row.get("headline_hint") or "").strip():
            packets.append(
                normalize_candidate_packet(
                    source_type=source_type,
                    source_url=source_url,
                    headline_hint=str(row.get("headline_hint") or "").strip(),
                    facts=facts,
                )
            )
    return packets


def _fetch_rss_packets(*, source_type: str, source_url: str, base_facts: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    try:
        response = requests.get(source_url, timeout=20)
        response.raise_for_status()
        root = ET.fromstring(response.text)
    except (requests.RequestException, ET.ParseError) as exc:
        logger.warning("Skipping RSS newsroom source %s: %s", source_url, exc)
        return []

    packets: list[dict[str, Any]] = []
    items = root.findall(".//item")
    for item in items[:limit]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        description = (item.findtext("description") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        if not title or not link or not is_allowed_source_url(link):
            continue
        occurred_at = ""
        if pub_date:
            try:
                occurred_at = parsedate_to_datetime(pub_date).astimezone(UTC).isoformat()
            except (TypeError, ValueError, IndexError, OverflowError):
                occurred_at = ""
        facts = dict(base_facts)
        if description and "summary" not in facts:
            facts["summary"] = description
        if occurred_at and "occurred_at" not in facts:
            facts["occurred_at"] = occurred_at
        packets.append(
            normalize_candidate_packet(
                source_type=source_type,
                source_url=link,
                headline_hint=title,
                facts=facts,
            )
        )
    return packets


def _fetch_html_packets(*, source_type: str, source_url: str, base_facts: dict[str, Any], row: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    try:
        from bs4 import BeautifulSoup
    except ModuleNotFoundError:
        logger.warning("Skipping HTML newsroom source %s because bs4 is not installed", source_url)
        return []

    try:
        response = requests.get(source_url, timeout=20)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Skipping HTML newsroom source %s: %s", source_url, exc)
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    item_selector = str(row.get("item_selector") or "").strip()
    title_selector = str(row.get("title_selector") or "").strip()
    link_selector = str(row.get("link_selector") or "").strip()
    summary_selector = str(row.get("summary_selector") or "").strip()
    if not item_selector or not title_selector:
        return []

    packets: list[dict[str, Any]] = []
    items = soup.select(item_selector)
    for item in items[:limit]:
        title_node = item.select_one(title_selector)
        if not title_node:
            continue
        title = title_node.get_text(" ", strip=True)
        link_node = item.select_one(link_selector) if link_selector else title_node
        link = (link_node.get("href") or "").strip() if link_node else ""
        if link.startswith("/"):
            from urllib.parse import urljoin
            link = urljoin(source_url, link)
        if not title or not link or not is_allowed_source_url(link):
            continue
        facts = dict(base_facts)
        if summary_selector:
            summary_node = item.select_one(summary_selector)
            if summary_node and "summary" not in facts:
                facts["summary"] = summary_node.get_text(" ", strip=True)
        packets.append(
            normalize_candidate_packet(
                source_type=source_type,
                source_url=link,
                headline_hint=title,
                facts=facts,
            )
        )
    return packets


def plan_candidates(conn: sqlite3.Connection, *, limit: int = 5) -> list[int]:
    packets = [*fetch_internal_story_packets(conn, limit=limit), *fetch_external_story_packets()]
    candidate_ids: list[int] = []
    for packet in packets:
        candidate_ids.append(persist_candidate(conn, packet))
    return candidate_ids


def run() -> int:
    logger.info("news planner run started")
    conn = connect_db()
    candidate_ids = plan_candidates(conn)
    conn.close()
    logger.info("news planner run finished with %s candidates", len(candidate_ids))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
