"""
MHP Crash Incident Fetcher
Scrapes Montana Highway Patrol news releases for crash reports.
Falls back to DOJ news releases if MHP pages are unreachable.

Usage:
    python mhp_crash_fetcher.py           # fetch latest
    python mhp_crash_fetcher.py --all     # fetch all paginated history
"""
from __future__ import annotations

import argparse
import hashlib
import html
import logging
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

import config
import db

logger = logging.getLogger("mhp_crash_fetcher")
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format=config.LOG_FORMAT,
)

# ---------------------------------------------------------------------------
# Sources (ordered by preference)
# ---------------------------------------------------------------------------
MHP_NEWS_URL = "https://dojmt.gov/montana-highway-patrol/mhp-news/"
MHP_FATAL_URL = "https://dojmt.gov/montana-highway-patrol/weekly-fatal-report/"
DOJ_NEWS_URL = "https://dojmt.gov/news/"
REQUEST_TIMEOUT = 30

# Highway regex — matches US-2, I-90, MT-200, Highway 93, etc.
HIGHWAY_RE = re.compile(
    r"\b(?:I[-\s]?\d{1,3}|US[-\s]?\d{1,3}|MT[-\s]?\d{1,3}|State\s+Route\s+\d{1,3}|Highway\s+\d{1,3}|Hwy\.?\s+\d{1,3})\b",
    re.IGNORECASE,
)

# Mile marker regex
MILE_MARKER_RE = re.compile(r"mile\s+(?:marker\s+)?(\d+(?:\.\d+)?)", re.IGNORECASE)

# County list for fast matching
MONTANA_COUNTIES_LOWER = {c.lower(): c for c in config.MONTANA_COUNTIES}

# Severity keywords
FATAL_KEYWORDS = ["fatal", "fatality", "killed", "deceased", "died"]
INJURY_KEYWORDS = ["injured", "injury", "hospitalized", "transported"]


def _slugify(text: str) -> str:
    return re.sub(r"[^\w]+", "-", text.lower()).strip("-")


def _extract_highways(text: str) -> list[str]:
    """Return normalized highway designations from text."""
    matches = HIGHWAY_RE.findall(text)
    normalized = []
    for m in matches:
        # Normalize spacing: "US 93" -> "US-93"
        cleaned = re.sub(r"\s+", "-", m.upper().replace(" ", "-"))
        normalized.append(cleaned)
    return list(dict.fromkeys(normalized))  # dedupe preserve order


def _extract_mile_marker(text: str) -> str | None:
    m = MILE_MARKER_RE.search(text)
    return m.group(1) if m else None


def _extract_county(text: str) -> str | None:
    """Return first Montana county found in text, or None."""
    for word in re.split(r"[^\w\s]", text.lower()):
        if word in MONTANA_COUNTIES_LOWER:
            return MONTANA_COUNTIES_LOWER[word]
    # Try multi-word counties (e.g., "Lewis and Clark")
    lowered = text.lower()
    for county in config.MONTANA_COUNTIES:
        if county.lower() in lowered:
            return county
    return None


def _extract_severity(title: str, description: str) -> str:
    combined = f"{title} {description}".lower()
    if any(k in combined for k in FATAL_KEYWORDS):
        return "fatal"
    if any(k in combined for k in INJURY_KEYWORDS):
        return "serious"
    return "minor"


def _extract_injuries_fatalities(text: str) -> tuple[int | None, int | None]:
    """Best-effort extraction of injury/fatality counts."""
    injuries = None
    fatalities = None

    # "one person was injured", "two people injured"
    injury_match = re.search(r"(\d+)\s+(?:person|people).*?injured", text, re.IGNORECASE)
    if injury_match:
        injuries = int(injury_match.group(1))

    fatality_match = re.search(r"(\d+)\s+(?:person|people).*?(?:killed|died|fatal)", text, re.IGNORECASE)
    if fatality_match:
        fatalities = int(fatality_match.group(1))

    return injuries, fatalities


def _parse_occurred_at(text: str) -> str | None:
    """Extract date from text like 'May 10, 2026' or '05/10/2026'."""
    # "May 10, 2026" or "May 10 2026"
    m = re.search(r"([A-Z][a-z]+)\s+(\d{1,2}),?\s+(\d{4})", text)
    if m:
        try:
            dt = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y")
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass
    # "05/10/2026"
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if m:
        try:
            dt = datetime.strptime(f"{m.group(1)}/{m.group(2)}/{m.group(3)}", "%m/%d/%Y")
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def _generate_external_id(url: str, title: str) -> str:
    """Stable ID based on URL path + title hash."""
    path = urlparse(url).path or url
    raw = f"{path}::{title.strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------
def fetch_page(url: str, retries: int = 3) -> str | None:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            logger.warning("Fetch attempt %d/%d failed for %s: %s", attempt, retries, url, exc)
            time.sleep(2 ** attempt)
    return None


def parse_mhp_news_list(html_text: str, base_url: str) -> list[dict[str, Any]]:
    """Parse news release list page and return article stubs."""
    soup = BeautifulSoup(html_text, "html.parser")
    articles = []

    for link in soup.find_all("a", href=True):
        href = link["href"]
        full_url = urljoin(base_url, href)
        # Accept: date-formatted WP paths, /news/ paths, or plain dojmt.gov article slugs
        is_dated = bool(re.search(r"/\d{4}/", full_url))
        is_news_path = "/news/" in full_url or "/mhp-news/" in full_url
        is_dojmt_slug = (
            re.match(r"https://dojmt\.gov/[a-z0-9-]{10,}/$", full_url)
            and "/wp-" not in full_url
            and "/montana-highway-patrol/" not in full_url
        )
        if not (is_dated or is_news_path or is_dojmt_slug):
            continue
        title = link.get_text(strip=True)
        if len(title) < 10:
            continue
        if title.lower() in {"news", "releases", "older posts", "newer posts", "next", "previous"}:
            continue
        articles.append({
            "title": title,
            "url": full_url,
            "source": "mhp_news",
        })
    # Deduplicate by URL
    seen = set()
    deduped = []
    for a in articles:
        if a["url"] not in seen:
            seen.add(a["url"])
            deduped.append(a)
    return deduped


def parse_article(html_text: str, url: str) -> dict[str, Any] | None:
    """Parse a single news release article into structured crash data."""
    soup = BeautifulSoup(html_text, "html.parser")

    # Try to find main content area
    content = soup.find("article") or soup.find("div", class_=re.compile("content|entry|post")) or soup.find("main") or soup.find("body")
    if not content:
        return None

    title_tag = soup.find("h1") or soup.find("h2")
    title = title_tag.get_text(strip=True) if title_tag else ""
    if not title:
        return None

    # Clean text for analysis
    paragraphs = content.find_all("p")
    description = "\n\n".join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 20)
    full_text = f"{title} {description}"

    # Only keep crash-related articles
    crash_keywords = ["crash", "collision", "accident", "rollover", "vehicle", "traffic"]
    if not any(k in full_text.lower() for k in crash_keywords):
        return None

    highways = _extract_highways(full_text)
    mile_marker = _extract_mile_marker(full_text)
    county = _extract_county(full_text)
    severity = _extract_severity(title, description)
    injuries, fatalities = _extract_injuries_fatalities(full_text)
    occurred_at = _parse_occurred_at(full_text)

    # Try to extract nearest city from first paragraph
    nearest_city = None
    if paragraphs:
        first_p = paragraphs[0].get_text(strip=True)
        # "near Billings" or "in Missoula"
        city_match = re.search(r"\b(?:near|in|around)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", first_p)
        if city_match:
            nearest_city = city_match.group(1)

    external_id = _generate_external_id(url, title)

    return {
        "external_id": external_id,
        "source": "mhp_news",
        "title": title,
        "description": description,
        "incident_type": "crash",
        "severity": severity,
        "status": "active",
        "highway": highways[0] if highways else None,
        "mile_marker": mile_marker,
        "nearest_city": nearest_city,
        "county": county,
        "injuries": injuries,
        "fatalities": fatalities,
        "road_status": None,
        "occurred_at": occurred_at,
        "cleared_at": None,
        "source_url": url,
        "raw_html": html_text[:50000],  # cap storage
    }


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def upsert_crash_incident(conn: sqlite3.Connection, record: dict[str, Any]) -> bool:
    """Insert or update a crash incident. Returns True if inserted, False if updated."""
    cursor = conn.cursor()
    cursor.execute("SELECT id, first_seen_at FROM crash_incidents WHERE external_id = ?", (record["external_id"],))
    row = cursor.fetchone()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    if row:
        cursor.execute(
            """
            UPDATE crash_incidents SET
                title = ?,
                description = ?,
                severity = ?,
                status = ?,
                highway = ?,
                mile_marker = ?,
                nearest_city = ?,
                county = ?,
                injuries = ?,
                fatalities = ?,
                road_status = ?,
                occurred_at = ?,
                cleared_at = ?,
                source_url = ?,
                raw_html = ?,
                last_updated_at = ?
            WHERE external_id = ?
            """,
            (
                record["title"],
                record["description"],
                record["severity"],
                record["status"],
                record.get("highway"),
                record.get("mile_marker"),
                record.get("nearest_city"),
                record.get("county"),
                record.get("injuries"),
                record.get("fatalities"),
                record.get("road_status"),
                record.get("occurred_at"),
                record.get("cleared_at"),
                record["source_url"],
                record["raw_html"],
                now,
                record["external_id"],
            ),
        )
        return False
    else:
        cursor.execute(
            """
            INSERT INTO crash_incidents (
                external_id, source, title, description, incident_type, severity, status,
                highway, mile_marker, nearest_city, county, injuries, fatalities,
                road_status, occurred_at, cleared_at, source_url, raw_html,
                first_seen_at, last_updated_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["external_id"],
                record["source"],
                record["title"],
                record["description"],
                record["incident_type"],
                record["severity"],
                record["status"],
                record.get("highway"),
                record.get("mile_marker"),
                record.get("nearest_city"),
                record.get("county"),
                record.get("injuries"),
                record.get("fatalities"),
                record.get("road_status"),
                record.get("occurred_at"),
                record.get("cleared_at"),
                record["source_url"],
                record["raw_html"],
                now,
                now,
                now,
            ),
        )
        return True


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------
def run_fetch(max_pages: int = 1) -> dict[str, int]:
    stats = {"fetched": 0, "new": 0, "updated": 0, "errors": 0}
    conn = db.connect_db()

    try:
        # 1. Fetch list page
        list_html = fetch_page(MHP_NEWS_URL)
        if not list_html:
            logger.error("Failed to fetch MHP news list; trying DOJ news...")
            list_html = fetch_page(DOJ_NEWS_URL)
            if not list_html:
                logger.error("Failed to fetch DOJ news list as well.")
                return stats
            articles = parse_mhp_news_list(list_html, DOJ_NEWS_URL)
        else:
            articles = parse_mhp_news_list(list_html, MHP_NEWS_URL)

        logger.info("Found %d potential articles", len(articles))

        for article in articles:
            article_html = fetch_page(article["url"])
            if not article_html:
                stats["errors"] += 1
                continue

            parsed = parse_article(article_html, article["url"])
            if not parsed:
                continue

            is_new = upsert_crash_incident(conn, parsed)
            stats["fetched"] += 1
            if is_new:
                stats["new"] += 1
                logger.info("NEW crash incident: %s", parsed["title"][:80])
            else:
                stats["updated"] += 1
                logger.info("UPDATED crash incident: %s", parsed["title"][:80])

            # Polite delay
            time.sleep(1.5)

        conn.commit()
    except Exception:
        logger.exception("Unhandled error during fetch")
        conn.rollback()
        stats["errors"] += 1
    finally:
        conn.close()

    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch MHP crash incidents")
    parser.add_argument("--all", action="store_true", help="Fetch all paginated history (not yet implemented)")
    parser.add_argument("--pages", type=int, default=1, help="Number of list pages to scan")
    args = parser.parse_args()

    stats = run_fetch(max_pages=args.pages)
    print(f"Done. Fetched: {stats['fetched']}, New: {stats['new']}, Updated: {stats['updated']}, Errors: {stats['errors']}")
    sys.exit(0 if stats["errors"] == 0 else 1)
