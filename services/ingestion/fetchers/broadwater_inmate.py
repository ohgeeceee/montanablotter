"""
broadwater_inmate.py
=====================
Fetches Broadwater County jail roster from the sheriff's HTML roster.

Replaces the broken Zuercher portal (broadwater-so-mt.zuercherportal.com)
with the working sheriff-hosted roster at broadwatercountysheriff.org.

Roster URL: https://www.broadwatercountysheriff.org/roster.php
Format:     HTML table with mugshot, name, booking#, charges per row.
"""

from __future__ import annotations

import hashlib
import logging
import re
from html.parser import HTMLParser
from typing import Iterable

import requests

from services.ingestion.models import JailBookingRecord

logger = logging.getLogger(__name__)

ROSTER_URL = "https://www.broadwatercountysheriff.org/roster.php"
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://montanablotter.com)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
})


def fetch_broadwater_bookings(source_url: str) -> list[JailBookingRecord]:
    """Fetch Broadwater County jail roster.

    Accepts either roster.php or a paginated variant like
    roster.php?grp=N.  Follows pagination links automatically.
    """
    url = source_url or ROSTER_URL
    seen_ids: set[str] = set()
    records: list[JailBookingRecord] = []
    pending: list[str] = [url]
    visited: set[str] = set()

    while pending:
        page_url = pending.pop(0)
        if page_url in visited:
            continue
        visited.add(page_url)

        try:
            resp = SESSION.get(page_url, timeout=30)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Broadwater roster fetch failed for %s: %s", page_url, exc)
            continue

        for rec in _parse_roster_page(resp.text, page_url):
            if rec.source_record_id in seen_ids:
                continue
            seen_ids.add(rec.source_record_id)
            records.append(rec)

        for link in _extract_pagination_links(resp.text, page_url):
            if link not in visited and link not in pending:
                pending.append(link)

    logger.info("Broadwater roster: %d record(s) from %s", len(records), url)
    return records


# ------------------------------------------------------------------
# HTML parsing
# ------------------------------------------------------------------


class _RosterParser(HTMLParser):
    """Extract inmate rows from the Broadwater roster table."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_table = 0
        self.in_row = False
        self.in_cell = False
        self.cell_text = ""
        self.cells: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "table":
            self.in_table += 1
        elif self.in_table > 0 and tag == "tr":
            self.in_row = True
            self.cells = []
        elif self.in_row and tag in ("td", "th"):
            self.in_cell = True
            self.cell_text = ""

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.cell_text += data

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in ("td", "th") and self.in_cell:
            self.in_cell = False
            self.cells.append(self.cell_text.strip())
        elif tag == "tr" and self.in_row:
            self.in_row = False
            if self.cells:
                self.rows.append(self.cells)
        elif tag == "table" and self.in_table > 0:
            self.in_table -= 1


def _parse_roster_page(html: str, page_url: str) -> list[JailBookingRecord]:
    parser = _RosterParser()
    parser.feed(html)

    records: list[JailBookingRecord] = []
    for row in parser.rows:
        if len(row) < 2:
            continue
        # The roster page shows columns like:
        # [mugshot] [name] [booking#] [charges] [date] [status]
        # Skip header rows and empty rows
        name = _clean_cell(row[1]) if len(row) > 1 else ""
        if not name or len(name) < 3:
            continue
        if name.lower() in {"name", "inmate name", "inmate", "mugshot"}:
            continue

        booking_number = _clean_cell(row[2]) if len(row) > 2 else ""
        charges = _clean_cell(row[3]) if len(row) > 3 else ""
        booking_date = _clean_cell(row[4]) if len(row) > 4 else ""

        # Normalize "LAST, FIRST" -> "First Last"
        person_name = _normalize_name(name)

        booking_at = None
        if booking_date:
            booking_at = _parse_date(booking_date)

        source_record_id = hashlib.sha1(
            f"broadwater:{booking_number or person_name.lower()}:{page_url}".encode()
        ).hexdigest()[:20]

        records.append(JailBookingRecord(
            source_record_id=source_record_id,
            person_name=person_name,
            age=None,
            booking_number=booking_number,
            booking_at=booking_at,
            charges_summary=charges or "Charge details available on the official Broadwater County roster.",
            source_url=page_url,
        ))

    return records


def _clean_cell(text: str) -> str:
    """Strip HTML entities and collapse whitespace."""
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _normalize_name(raw: str) -> str:
    """Convert 'LASTNAME, FIRSTNAME' -> 'Firstname Lastname'."""
    raw = raw.strip()
    if "," in raw:
        parts = raw.split(",", 1)
        if len(parts) == 2:
            last = parts[0].strip().title()
            first = parts[1].strip().title()
            return f"{first} {last}"
    return raw.title()


def _parse_date(text: str) -> str | None:
    """Try common date formats."""
    text = text.strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            from datetime import datetime
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return None


def _extract_pagination_links(html: str, base_url: str) -> list[str]:
    """Find pagination links like roster.php?grp=N."""
    links: list[str] = []
    for m in re.finditer(r'href=["\']([^"\']*roster\.php\?[^"\']*)["\']', html, re.IGNORECASE):
        url = m.group(1)
        if url.startswith("/"):
            from urllib.parse import urljoin
            url = urljoin(base_url, url)
        if url not in links:
            links.append(url)
    return links


__all__ = ["fetch_broadwater_bookings"]
