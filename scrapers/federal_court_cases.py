"""
MT Federal Court Cases Scraper
Sources: DOJ press releases (justice.gov) and MT Federal District Court (mtd.uscourts.gov)
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
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db

logger = logging.getLogger("federal_court_cases")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
    logger.addHandler(handler)

HEADERS = {
    "User-Agent": "MontanaBlotterBot/1.0 (public records aggregation)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

DOJ_NEWS_URL = "https://www.justice.gov/usao-mt"
MT_FEDERAL_COURT_URL = "https://www.mtd.uscourts.gov/news"

MT_COUNTIES = [
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
    "Prairie", "Meagher", "Blaine",
]

FEDERAL_CASE_TYPES = ["drug", "firearms", "fraud", "human_trafficking", "cyber", "immigration", "white_collar", "other"]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class FederalCase:
    defendant_name: str
    charges: str
    outcome: str | None  # indicted, convicted, sentenced
    sentence_length: str | None
    county: str | None
    case_number: str | None
    case_type: str | None
    source_url: str
    source_name: str
    published_date: str | None
    summary: str
    raw_text: str

    def fingerprint(self) -> str:
        payload = f"{self.defendant_name}|{self.case_number}|{self.source_url}"
        return hashlib.sha256(payload.encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def ensure_schema(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS federal_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            defendant_name TEXT NOT NULL,
            charges TEXT NOT NULL,
            outcome TEXT,
            sentence_length TEXT,
            county TEXT,
            case_number TEXT,
            case_type TEXT,
            source_url TEXT NOT NULL,
            source_name TEXT NOT NULL,
            published_date TEXT,
            summary TEXT,
            fingerprint TEXT NOT NULL UNIQUE,
            raw_text TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_federal_county ON federal_cases(county)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_federal_type ON federal_cases(case_type)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_federal_outcome ON federal_cases(outcome)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_federal_date ON federal_cases(published_date)"
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


def _extract_date(text: str) -> str | None:
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


def _extract_county(text: str) -> str | None:
    text_lower = text.lower()
    for county in MT_COUNTIES:
        if county.lower() in text_lower:
            return county
    return None


def _extract_case_number(text: str) -> str | None:
    patterns = [
        r"Case\s*#?\s*([A-Z0-9\-]+)",
        r"Case\s+Number\s*:?\s*([A-Z0-9\-]+)",
        r"([A-Z]{2,3}-\d{4}-\d+\b)",
        r"CR-\d{2,4}-\d+",
        r"No\.?\s*([A-Z0-9\-]+)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def _extract_defendant_name(text: str) -> str | None:
    """Try to extract a person's name from text. Skip job postings and court admin."""
    text_lower = text.lower()
    # Skip administrative / job postings
    skip_phrases = [
        "applications sought", "employment opportunity", "request for quotation",
        "clerk of court", "magistrate judge", "career law clerk", "probation officer",
        "court order", "announcement", "retirement", "investiture", "civics contest",
        "virtual power act", "courthouse closure", "district court district",
        "united states district court", "direct all inquiries",
    ]
    if any(phrase in text_lower for phrase in skip_phrases):
        return None
    patterns = [
        r"([A-Z][a-z]+ [A-Z][a-z]+(?: [A-Z][a-z]+)?)\s+(?:of\s+[A-Z][a-z]+)",
        r"([A-Z][a-z]+ [A-Z][a-z]+)\s*,\s*\d{1,3}\s*,?\s*of",
        r"defendant\s+([A-Z][a-z]+ [A-Z][a-z]+)",
        r"([A-Z][a-z]+ [A-Z][a-z]+)\s+(?:was sentenced|was charged|pleaded|was convicted|was indicted)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip()
            # Reject if candidate looks like an organization or title
            reject = ["district", "court", "clerk", "judge", "united", "states", "montana"]
            if candidate.lower() in reject:
                continue
            return candidate
    return None


def _classify_case_type(text: str) -> str:
    text_lower = text.lower()
    if any(w in text_lower for w in ("drug", "cocaine", "methamphetamine", "heroin", "fentanyl", "trafficking", "narcotic", "controlled substance")):
        return "drug"
    if any(w in text_lower for w in ("firearm", "gun", "weapon", "ammunition", "felon in possession")):
        return "firearms"
    if any(w in text_lower for w in ("fraud", "wire fraud", "bank fraud", "securities", "embezzlement", "money laundering")):
        return "fraud"
    if any(w in text_lower for w in ("human trafficking", "sex trafficking", "trafficking of persons")):
        return "human_trafficking"
    if any(w in text_lower for w in ("cyber", "computer", "hacking", "internet", "dark web", "cryptocurrency", "bitcoin")):
        return "cyber"
    if any(w in text_lower for w in ("immigration", "illegal reentry", "deportation", "visa")):
        return "immigration"
    if any(w in text_lower for w in ("tax", "tax evasion", "bribery", "corruption", "public official")):
        return "white_collar"
    return "other"


def _extract_outcome(text: str) -> str | None:
    text_lower = text.lower()
    if "sentenced" in text_lower or "sentence" in text_lower:
        return "sentenced"
    if "convicted" in text_lower or "pleaded guilty" in text_lower or "found guilty" in text_lower:
        return "convicted"
    if "indicted" in text_lower or "charged" in text_lower:
        return "indicted"
    return None


def _extract_sentence(text: str) -> str | None:
    m = re.search(r"(\d+\s*(?:years?|months?|life))\s*(?:in\s*prison)?", text, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


# ---------------------------------------------------------------------------
# DOJ parser
# ---------------------------------------------------------------------------
def parse_doj_news_list(soup: BeautifulSoup) -> list[dict[str, str]]:
    articles = []
    for item in soup.find_all("a", href=True):
        href = item["href"]
        text = item.get_text(strip=True)
        if not text or len(text) < 10:
            continue
        if "/usao-mt/pr/" in href:
            full_url = urljoin("https://www.justice.gov", href)
            articles.append({"title": text, "url": full_url})
    seen = set()
    unique = []
    for a in articles:
        if a["url"] not in seen:
            seen.add(a["url"])
            unique.append(a)
    return unique


def parse_doj_article(soup: BeautifulSoup, url: str) -> list[FederalCase]:
    cases = []
    # DOJ pages are heavily bot-protected; use the URL slug as a fallback source of data
    slug = url.rstrip("/").split("/")[-1]
    text = slug.replace("-", " ")

    name = _extract_defendant_name(text)
    if not name:
        # Try to get name from the listing page title if available
        title_tag = soup.find("title")
        if title_tag and title_tag.get_text(strip=True) and title_tag.get_text(strip=True) != "&nbsp;":
            name = _extract_defendant_name(title_tag.get_text(strip=True))
        if not name:
            return cases

    case_num = _extract_case_number(text)
    county = _extract_county(text)
    case_type = _classify_case_type(text)
    outcome = _extract_outcome(text)
    sentence = _extract_sentence(text)
    date = _extract_date(text)

    cases.append(
        FederalCase(
            defendant_name=name,
            charges=text[:500],
            outcome=outcome,
            sentence_length=sentence,
            county=county,
            case_number=case_num,
            case_type=case_type,
            source_url=url,
            source_name="DOJ Press Release",
            published_date=date,
            summary=text[:300],
            raw_text=text,
        )
    )
    return cases


# ---------------------------------------------------------------------------
# MT Federal Court parser
# ---------------------------------------------------------------------------
def parse_mtd_news_list(soup: BeautifulSoup) -> list[dict[str, str]]:
    articles = []
    for item in soup.find_all("a", href=True):
        href = item["href"]
        text = item.get_text(strip=True)
        if not text or len(text) < 10:
            continue
        if "/news/" in href:
            full_url = urljoin("https://www.mtd.uscourts.gov", href)
            articles.append({"title": text, "url": full_url})
    seen = set()
    unique = []
    for a in articles:
        if a["url"] not in seen:
            seen.add(a["url"])
            unique.append(a)
    return unique


def parse_mtd_article(soup: BeautifulSoup, url: str) -> list[FederalCase]:
    cases = []
    content = soup.find("article") or soup.find("div", class_=re.compile("content|main|body")) or soup.find("main")
    if not content:
        content = soup.find("body")
    if not content:
        return cases

    text = content.get_text(separator="\n", strip=True)
    paragraphs = [p.strip() for p in text.split("\n") if len(p.strip()) > 60]

    for para in paragraphs:
        name = _extract_defendant_name(para)
        if not name:
            continue
        case_num = _extract_case_number(para)
        county = _extract_county(para)
        case_type = _classify_case_type(para)
        outcome = _extract_outcome(para)
        sentence = _extract_sentence(para)
        date = _extract_date(para)

        cases.append(
            FederalCase(
                defendant_name=name,
                charges=para[:500],
                outcome=outcome,
                sentence_length=sentence,
                county=county,
                case_number=case_num,
                case_type=case_type,
                source_url=url,
                source_name="MT Federal District Court",
                published_date=date,
                summary=para[:300],
                raw_text=para,
            )
        )
    return cases


# ---------------------------------------------------------------------------
# Database insert
# ---------------------------------------------------------------------------
def import_cases(conn: sqlite3.Connection, cases: list[FederalCase]) -> dict[str, int]:
    cursor = conn.cursor()
    inserted = 0
    updated = 0
    for c in cases:
        fp = c.fingerprint()
        cursor.execute("SELECT id FROM federal_cases WHERE fingerprint = ?", (fp,))
        existing = cursor.fetchone()
        if existing:
            cursor.execute(
                """
                UPDATE federal_cases
                SET defendant_name = ?, charges = ?, outcome = ?, sentence_length = ?,
                    county = ?, case_number = ?, case_type = ?, source_url = ?,
                    source_name = ?, published_date = ?, summary = ?, raw_text = ?,
                    updated_at = datetime('now')
                WHERE fingerprint = ?
                """,
                (c.defendant_name, c.charges, c.outcome, c.sentence_length,
                 c.county, c.case_number, c.case_type, c.source_url,
                 c.source_name, c.published_date, c.summary, c.raw_text, fp),
            )
            updated += 1
        else:
            cursor.execute(
                """
                INSERT INTO federal_cases
                (defendant_name, charges, outcome, sentence_length, county, case_number,
                 case_type, source_url, source_name, published_date, summary, fingerprint, raw_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (c.defendant_name, c.charges, c.outcome, c.sentence_length,
                 c.county, c.case_number, c.case_type, c.source_url,
                 c.source_name, c.published_date, c.summary, fp, c.raw_text),
            )
            inserted += 1
    conn.commit()
    return {"inserted": inserted, "updated": updated}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def run_federal_scrape(
    max_doj_articles: int = 30,
    max_mtd_articles: int = 30,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    all_cases: list[FederalCase] = []

    # DOJ
    doj_soup = _fetch_html(DOJ_NEWS_URL)
    if doj_soup:
        doj_articles = parse_doj_news_list(doj_soup)[:max_doj_articles]
        logger.info("DOJ: %d articles found", len(doj_articles))
        for article in doj_articles:
            article_soup = _fetch_html(article["url"])
            if article_soup:
                cases = parse_doj_article(article_soup, article["url"])
                all_cases.extend(cases)

    # MT Federal Court
    mtd_soup = _fetch_html(MT_FEDERAL_COURT_URL)
    if mtd_soup:
        mtd_articles = parse_mtd_news_list(mtd_soup)[:max_mtd_articles]
        logger.info("MT Federal Court: %d articles found", len(mtd_articles))
        for article in mtd_articles:
            article_soup = _fetch_html(article["url"])
            if article_soup:
                cases = parse_mtd_article(article_soup, article["url"])
                all_cases.extend(cases)

    if not all_cases:
        return {"status": "no_data", "message": "No federal cases extracted"}

    close_conn = conn is None
    if conn is None:
        conn = db.connect_db()
    ensure_schema(conn)
    stats = import_cases(conn, all_cases)
    if close_conn:
        conn.close()
    return {"status": "ok", **stats, "total_extracted": len(all_cases)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scrape MT federal court cases")
    parser.add_argument("--max-doj", type=int, default=30)
    parser.add_argument("--max-mtd", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    result = run_federal_scrape(max_doj_articles=args.max_doj, max_mtd_articles=args.max_mtd)
    logger.info("Result: %s", result)
