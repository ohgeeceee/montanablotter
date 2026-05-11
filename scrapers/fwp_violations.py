"""
MT Fish, Wildlife & Parks (FWP) Violations Scraper
Extracts enforcement actions — poaching citations, license revocations,
illegal harvest — from FWP news releases and enforcement pages.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db

logger = logging.getLogger("fwp_violations")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
    logger.addHandler(handler)

BASE_URL = "https://fwp.mt.gov"
ENFORCEMENT_NEWS_URL = "https://fwp.mt.gov/news/allnews"
# Keywords that indicate enforcement/violation content
ENFORCEMENT_KEYWORDS = [
    "poach", "violation", "cited", "citation", "enforcement", "warden",
    "guilty", "sentenced", "fine", "court", "charge", "investigation",
    "license", "suspend", "revoke", "privilege", "detention", "crimes",
    "illegal", "unlawful", "harvest", "trespass", "lacy",
]

HEADERS = {
    "User-Agent": "MontanaBlotterBot/1.0 (public records aggregation)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

DB_PATH = os.getenv("MB_DB_PATH", "/root/montanablotter/blotter.db")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class FWPViolation:
    name: str
    county: str | None
    violation: str
    violation_date: str | None
    fine: float | None
    license_status: str | None
    case_number: str | None
    source_url: str
    summary: str | None
    violation_type: str | None  # poaching, license, habitat, transport, commercial
    raw_text: str

    def fingerprint(self) -> str:
        payload = f"{self.name}|{self.county}|{self.violation}|{self.violation_date}|{self.case_number}"
        return hashlib.sha256(payload.encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def ensure_schema(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS fwp_violations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_name TEXT NOT NULL,
            county TEXT,
            violation TEXT NOT NULL,
            violation_date TEXT,
            fine REAL,
            license_status TEXT,
            case_number TEXT,
            violation_type TEXT,
            summary TEXT,
            source_url TEXT NOT NULL,
            fingerprint TEXT NOT NULL UNIQUE,
            raw_text TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_fwp_county ON fwp_violations(county)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_fwp_date ON fwp_violations(violation_date)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_fwp_type ON fwp_violations(violation_type)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_fwp_name ON fwp_violations(person_name)"
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------
def _fetch_html(url: str, timeout: int = 60) -> BeautifulSoup | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:
        logger.error("Failed to fetch %s: %s", url, exc)
        return None


def _extract_money(text: str) -> float | None:
    """Find dollar amounts like $1,234.56 or $500."""
    matches = re.findall(r"\$[\d,]+(?:\.\d{2})?", text)
    if not matches:
        return None
    # Return the largest amount (usually the fine, not a permit fee)
    values = []
    for m in matches:
        try:
            values.append(float(m.replace("$", "").replace(",", "")))
        except ValueError:
            continue
    return max(values) if values else None


def _extract_date(text: str) -> str | None:
    """Try to find a date in common formats."""
    patterns = [
        r"(\d{1,2}/\d{1,2}/\d{2,4})",
        r"(\d{1,2}-\d{1,2}-\d{2,4})",
        r"([A-Z][a-z]+ \d{1,2},? \d{4})",
        r"(\d{4}-\d{2}-\d{2})",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m.group(1)
    return None


def _extract_case_number(text: str) -> str | None:
    """Look for case numbers like CR-2024-1234 or Case #12345."""
    patterns = [
        r"Case\s*#?\s*([A-Z0-9\-]+)",
        r"Case\s+Number\s*:?\s*([A-Z0-9\-]+)",
        r"([A-Z]{2,3}-\d{4}-\d+\b)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def _extract_county(text: str) -> str | None:
    """Look for Montana county names."""
    # Top 20 counties by population + common references
    counties = [
        "Yellowstone", "Gallatin", "Missoula", "Flathead", "Cascade",
        "Lewis and Clark", "Ravalli", "Silver Bow", "Lake", "Lincoln",
        "Hill", "Park", "Glacier", "Big Horn", "Rosebud", "Carbon",
        "Stillwater", "Deer Lodge", "Sanders", "Madison", "Powell",
        "Custer", "Beaverhead", "Jefferson", "Valley", "Roosevelt",
        "Dawson", "Toole", "Richland", "McCone", "Phillips", "Fergus",
        "Teton", "Wheatland", "Pondera", "Granite", "Chouteau", "Broadwater",
        "Mineral", "Sheridan", "Sweet Grass", "Fallon", "Powder River",
        "Treasure", "Liberty", "Garfield", "Wibaux", "Petroleum",
        "Golden Valley", "Carter", "Judith Basin", "Daniels", "Musselshell",
        "Prairie", "Meagher", "Blaine", "Colstrip",
    ]
    text_lower = text.lower()
    for county in counties:
        if county.lower() in text_lower:
            return county
    return None


def _classify_violation(text: str) -> str:
    """Classify violation into broad categories."""
    text_lower = text.lower()
    if any(w in text_lower for w in ("poach", "illegal harvest", "unlawful take", "kill", "shoot")):
        return "poaching"
    if any(w in text_lower for w in ("license", "permit", "tag", "suspension", "revoke")):
        return "license"
    if any(w in text_lower for w in ("habitat", "wetland", "stream", "damage")):
        return "habitat"
    if any(w in text_lower for w in ("transport", "lacy act", "import", "export")):
        return "transport"
    if any(w in text_lower for w in ("commercial", "guide", "outfitter", "business")):
        return "commercial"
    return "other"


def _extract_name(text: str) -> str | None:
    """Try to extract a person's name from the first sentence."""
    # Look for patterns like "John Smith, 45, of Billings..." or "John Smith was cited..."
    patterns = [
        r"([A-Z][a-z]+ [A-Z][a-z]+),?\s+\d{1,3},?\s+of",
        r"([A-Z][a-z]+ [A-Z][a-z]+)\s+was",
        r"([A-Z][a-z]+ [A-Z][a-z]+)\s+pleaded",
        r"([A-Z][a-z]+ [A-Z][a-z]+)\s+of\s+[A-Z][a-z]+",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m.group(1)
    return None


# ---------------------------------------------------------------------------
# Page parsers
# ---------------------------------------------------------------------------
def parse_enforcement_news_list(soup: BeautifulSoup, base_url: str) -> list[dict[str, str]]:
    """Extract article links from the news listing page, filtering for enforcement content."""
    articles = []
    for link in soup.find_all("a", href=True):
        href = link["href"]
        text = link.get_text(strip=True)
        if not text or len(text) < 10:
            continue
        # Only include links to actual news articles
        if "/homepage/news/" not in href:
            continue
        full_url = urljoin(base_url, href)
        # Check if title contains enforcement keywords
        text_lower = text.lower()
        if any(kw in text_lower for kw in ENFORCEMENT_KEYWORDS):
            articles.append({"title": text, "url": full_url})
    # Deduplicate by URL
    seen = set()
    unique = []
    for a in articles:
        if a["url"] not in seen:
            seen.add(a["url"])
            unique.append(a)
    return unique


def parse_enforcement_article(soup: BeautifulSoup, url: str) -> list[FWPViolation]:
    """Parse a single enforcement article into violation records."""
    violations = []
    # Try to find the main content area
    content = soup.find("article") or soup.find("div", class_=re.compile("content|main|body")) or soup.find("main")
    if not content:
        content = soup.find("body")
    if not content:
        return violations

    text = content.get_text(separator="\n", strip=True)
    paragraphs = [p.strip() for p in text.split("\n") if len(p.strip()) > 40]

    for para in paragraphs:
        name = _extract_name(para)
        if not name:
            continue
        county = _extract_county(para)
        vtype = _classify_violation(para)
        vdate = _extract_date(para)
        fine = _extract_money(para)
        case_num = _extract_case_number(para)
        license_status = None
        if "revoke" in para.lower() or "suspension" in para.lower():
            license_status = "revoked" if "revoke" in para.lower() else "suspended"

        violations.append(
            FWPViolation(
                name=name,
                county=county,
                violation=para[:500],
                violation_date=vdate,
                fine=fine,
                license_status=license_status,
                case_number=case_num,
                source_url=url,
                summary=para[:300],
                violation_type=vtype,
                raw_text=para,
            )
        )
    return violations


def fetch_and_parse_articles(max_articles: int = 50) -> list[FWPViolation]:
    """Fetch the news list across multiple pages, then parse enforcement articles."""
    all_violations: list[FWPViolation] = []
    seen_urls: set[str] = set()

    # Scan first 10 pages of news
    for page in range(1, 11):
        page_url = f"{ENFORCEMENT_NEWS_URL}?r822_r1_r2:pageSize=100&r822_r1_r2:page={page}"
        soup = _fetch_html(page_url)
        if not soup:
            logger.error("Could not load news list page %d", page)
            continue

        articles = parse_enforcement_news_list(soup, BASE_URL)
        logger.info("Page %d: found %d enforcement articles", page, len(articles))

        for article in articles:
            if article["url"] in seen_urls:
                continue
            seen_urls.add(article["url"])
            if len(seen_urls) >= max_articles:
                break

            article_soup = _fetch_html(article["url"])
            if not article_soup:
                continue
            violations = parse_enforcement_article(article_soup, article["url"])
            if violations:
                logger.info("Extracted %d violations from %s", len(violations), article["url"])
                all_violations.extend(violations)

        if len(seen_urls) >= max_articles:
            break

    return all_violations


# ---------------------------------------------------------------------------
# Database insert
# ---------------------------------------------------------------------------
def import_violations(conn: sqlite3.Connection, violations: list[FWPViolation]) -> dict[str, int]:
    cursor = conn.cursor()
    inserted = 0
    updated = 0
    for v in violations:
        fp = v.fingerprint()
        cursor.execute("SELECT id FROM fwp_violations WHERE fingerprint = ?", (fp,))
        existing = cursor.fetchone()
        if existing:
            cursor.execute(
                """
                UPDATE fwp_violations
                SET person_name = ?, county = ?, violation = ?, violation_date = ?,
                    fine = ?, license_status = ?, case_number = ?, violation_type = ?,
                    summary = ?, source_url = ?, raw_text = ?, updated_at = datetime('now')
                WHERE fingerprint = ?
                """,
                (v.name, v.county, v.violation, v.violation_date, v.fine,
                 v.license_status, v.case_number, v.violation_type, v.summary,
                 v.source_url, v.raw_text, fp),
            )
            updated += 1
        else:
            cursor.execute(
                """
                INSERT INTO fwp_violations
                (person_name, county, violation, violation_date, fine, license_status,
                 case_number, violation_type, summary, source_url, fingerprint, raw_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (v.name, v.county, v.violation, v.violation_date, v.fine,
                 v.license_status, v.case_number, v.violation_type, v.summary,
                 v.source_url, fp, v.raw_text),
            )
            inserted += 1
    conn.commit()
    return {"inserted": inserted, "updated": updated}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def run_fwp_scrape(max_articles: int = 50, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    violations = fetch_and_parse_articles(max_articles)
    if not violations:
        return {"status": "no_data", "message": "No violations extracted"}
    close_conn = conn is None
    if conn is None:
        conn = db.connect_db()
    ensure_schema(conn)
    stats = import_violations(conn, violations)
    if close_conn:
        conn.close()
    return {"status": "ok", **stats, "total_extracted": len(violations)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scrape MT FWP enforcement violations")
    parser.add_argument("--max-articles", type=int, default=50, help="Max articles to parse")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, do not write to DB")
    args = parser.parse_args()

    violations = fetch_and_parse_articles(args.max_articles)
    logger.info("Extracted %d total violations", len(violations))
    for v in violations[:5]:
        logger.info("  %s | %s | %s | $%s", v.name, v.violation_type, v.county, v.fine)

    if not args.dry_run and violations:
        conn = db.connect_db()
        ensure_schema(conn)
        stats = import_violations(conn, violations)
        logger.info("DB stats: %s", stats)
        conn.close()
