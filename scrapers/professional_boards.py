"""
MT Professional Licensing Board Sanctions Scraper
Maps ~12 boards, fetches discipline pages, extracts actions.
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

logger = logging.getLogger("professional_boards")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
    logger.addHandler(handler)

HEADERS = {
    "User-Agent": "MontanaBlotterBot/1.0 (public records aggregation)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Board registry — URL + type hint
BOARDS = [
    {"name": "Medical Examiners", "url": "https://boards.bsd.dli.mt.gov/med", "type": "medical"},
    {"name": "Dental", "url": "https://boards.bsd.dli.mt.gov/den", "type": "dental"},
    {"name": "Nursing", "url": "https://boards.bsd.dli.mt.gov/nur", "type": "nursing"},
    {"name": "Pharmacy", "url": "https://boards.bsd.dli.mt.gov/pha", "type": "pharmacy"},
    {"name": "Real Estate", "url": "https://boards.bsd.dli.mt.gov/rre", "type": "real_estate"},
    {"name": "Accountancy", "url": "https://boards.bsd.dli.mt.gov/acc", "type": "accountancy"},
    {"name": "Barber/Cosmetology", "url": "https://boards.bsd.dli.mt.gov/bar", "type": "barber"},
    {"name": "Contractors", "url": "https://boards.bsd.dli.mt.gov/con", "type": "contractors"},
    {"name": "Veterinary", "url": "https://boards.bsd.dli.mt.gov/vet", "type": "veterinary"},
    {"name": "Optometry", "url": "https://boards.bsd.dli.mt.gov/opt", "type": "optometry"},
    {"name": "Chiropractic", "url": "https://boards.bsd.dli.mt.gov/chi", "type": "chiropractic"},
    {"name": "State Bar", "url": "https://montanabar.org/disciplinary-actions", "type": "legal"},
]

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


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class LicenseSanction:
    name: str
    license_number: str | None
    board: str
    board_type: str
    violation_type: str | None
    action_taken: str | None
    effective_date: str | None
    county: str | None
    source_url: str
    raw_text: str

    def fingerprint(self) -> str:
        payload = f"{self.name}|{self.license_number}|{self.board}|{self.effective_date}|{self.action_taken}"
        return hashlib.sha256(payload.encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def ensure_schema(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS license_sanctions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_name TEXT NOT NULL,
            license_number TEXT,
            board TEXT NOT NULL,
            board_type TEXT NOT NULL,
            violation_type TEXT,
            action_taken TEXT,
            effective_date TEXT,
            county TEXT,
            source_url TEXT NOT NULL,
            fingerprint TEXT NOT NULL UNIQUE,
            raw_text TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_sanctions_name ON license_sanctions(person_name)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_sanctions_board ON license_sanctions(board)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_sanctions_type ON license_sanctions(board_type)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_sanctions_county ON license_sanctions(county)"
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


def _extract_license_number(text: str) -> str | None:
    m = re.search(r"License\s*#?\s*[:]?\s*([A-Z0-9\-]+)", text, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"\b(LIC\s*#?\s*[A-Z0-9\-]+)\b", text, re.IGNORECASE)
    if m:
        return m.group(1).replace("LIC", "").replace("#", "").strip()
    return None


def _extract_action(text: str) -> str | None:
    actions = [
        "revoked", "suspended", "reprimanded", "censured", "fined",
        "probation", "surrendered", "denied", "voluntary surrender",
        "restricted", "limited", "expelled", "disbarred",
    ]
    text_lower = text.lower()
    for action in actions:
        if action in text_lower:
            return action
    return None


def _extract_name(text: str) -> str | None:
    # Try common name patterns
    patterns = [
        r"([A-Z][a-z]+ [A-Z][a-z]+(?: [A-Z][a-z]+)?),?\s+(?:MD|DO|RN|PA|DDS|JD|CPA|LSW|LCSW)",
        r"([A-Z][a-z]+ [A-Z][a-z]+(?: [A-Z])?)\s+(?:of\s+[A-Z][a-z]+)",
        r"([A-Z][a-z]+ [A-Z][a-z]+)\s+-\s+(?:License|Action)",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m.group(1).strip()
    return None


# ---------------------------------------------------------------------------
# Board-specific parsers
# ---------------------------------------------------------------------------
def parse_generic_table(soup: BeautifulSoup, board: dict[str, str]) -> list[LicenseSanction]:
    """Parse a typical HTML table of disciplinary actions."""
    sanctions: list[LicenseSanction] = []
    tables = soup.find_all("table")
    for table in tables:
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        headers = [th.get_text(strip=True).lower() for th in rows[0].find_all(["th", "td"])]
        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            cell_texts = [c.get_text(strip=True) for c in cells]
            raw_text = " | ".join(cell_texts)

            name = _extract_name(raw_text)
            if not name:
                # Fallback: first cell often contains the name
                name = cell_texts[0] if cell_texts[0] else None
            if not name:
                continue

            license_num = _extract_license_number(raw_text)
            action = _extract_action(raw_text)
            date = _extract_date(raw_text)
            county = _extract_county(raw_text)

            sanctions.append(
                LicenseSanction(
                    name=name,
                    license_number=license_num,
                    board=board["name"],
                    board_type=board["type"],
                    violation_type=None,
                    action_taken=action,
                    effective_date=date,
                    county=county,
                    source_url=board["url"],
                    raw_text=raw_text,
                )
            )
    return sanctions


def parse_list_items(soup: BeautifulSoup, board: dict[str, str]) -> list[LicenseSanction]:
    """Parse ul/li or div-based lists of disciplinary actions."""
    sanctions: list[LicenseSanction] = []
    items = soup.find_all("li") or soup.find_all("div", class_=re.compile("item|entry|record"))
    for item in items:
        text = item.get_text(strip=True)
        if len(text) < 30:
            continue
        name = _extract_name(text)
        if not name:
            continue
        license_num = _extract_license_number(text)
        action = _extract_action(text)
        date = _extract_date(text)
        county = _extract_county(text)
        sanctions.append(
            LicenseSanction(
                name=name,
                license_number=license_num,
                board=board["name"],
                board_type=board["type"],
                violation_type=None,
                action_taken=action,
                effective_date=date,
                county=county,
                source_url=board["url"],
                raw_text=text,
            )
        )
    return sanctions


def parse_board_page(board: dict[str, str]) -> list[LicenseSanction]:
    """Attempt to parse a board's discipline page."""
    soup = _fetch_html(board["url"])
    if not soup:
        return []

    # Try table first, then list items
    sanctions = parse_generic_table(soup, board)
    if not sanctions:
        sanctions = parse_list_items(soup, board)
    return sanctions


# ---------------------------------------------------------------------------
# Database insert
# ---------------------------------------------------------------------------
def _name_slug(name: str) -> str:
    import re as _re
    text = name.lower().strip()
    text = _re.sub(r'[^\w\s-]', '', text)
    text = _re.sub(r'[\s_-]+', '-', text)
    return text[:80]


def import_sanctions(conn: sqlite3.Connection, sanctions: list[LicenseSanction]) -> dict[str, int]:
    cursor = conn.cursor()
    inserted = 0
    updated = 0
    for s in sanctions:
        fp = s.fingerprint()
        name_slug = _name_slug(s.name)
        cursor.execute("SELECT id FROM license_sanctions WHERE fingerprint = ?", (fp,))
        existing = cursor.fetchone()
        if existing:
            cursor.execute(
                """
                UPDATE license_sanctions
                SET person_name = ?, name = ?, name_slug = ?, license_number = ?, board = ?, board_type = ?,
                    violation_type = ?, action_taken = ?, effective_date = ?, county = ?,
                    source_url = ?, raw_text = ?, updated_at = datetime('now')
                WHERE fingerprint = ?
                """,
                (s.name, s.name, name_slug, s.license_number, s.board, s.board_type, s.violation_type,
                 s.action_taken, s.effective_date, s.county, s.source_url, s.raw_text, fp),
            )
            updated += 1
        else:
            cursor.execute(
                """
                INSERT INTO license_sanctions
                (person_name, name, name_slug, license_number, board, board_type, violation_type,
                 action_taken, effective_date, county, source_url, fingerprint, raw_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (s.name, s.name, name_slug, s.license_number, s.board, s.board_type, s.violation_type,
                 s.action_taken, s.effective_date, s.county, s.source_url, fp, s.raw_text),
            )
            inserted += 1
    conn.commit()
    return {"inserted": inserted, "updated": updated}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def run_professional_boards_scrape(
    board_filter: list[str] | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Scrape all (or filtered) professional boards."""
    boards = BOARDS
    if board_filter:
        boards = [b for b in boards if b["type"] in board_filter or b["name"] in board_filter]

    all_sanctions: list[LicenseSanction] = []
    per_board_stats: dict[str, int] = {}

    for board in boards:
        sanctions = parse_board_page(board)
        per_board_stats[board["name"]] = len(sanctions)
        all_sanctions.extend(sanctions)
        logger.info("Board '%s': %d sanctions extracted", board["name"], len(sanctions))

    if not all_sanctions:
        return {"status": "no_data", "message": "No sanctions extracted from any board", "per_board": per_board_stats}

    close_conn = conn is None
    if conn is None:
        conn = db.connect_db()
    ensure_schema(conn)
    stats = import_sanctions(conn, all_sanctions)
    if close_conn:
        conn.close()
    return {"status": "ok", **stats, "total_extracted": len(all_sanctions), "per_board": per_board_stats}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scrape MT professional licensing board sanctions")
    parser.add_argument("--board", action="append", help="Filter by board type or name (can repeat)")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, do not write to DB")
    args = parser.parse_args()

    result = run_professional_boards_scrape(board_filter=args.board or None)
    logger.info("Result: %s", result)
