#!/usr/bin/env python3
"""
lewis_clark_inmate.py
=====================
Fetches Lewis and Clark County (MT) jail roster PDF from the sheriff detention
page and parses it into JailBookingRecords.
"""
from __future__ import annotations

import html
import io
import logging
import re
import sys
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urljoin

import requests
import pdfplumber

sys.path.insert(0, "/root/montanablotter")
from services.ingestion.models import JailBookingRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://www.lccountymt.gov/Sheriff/Detention-Center"
DEFAULT_SOURCE_URL = BASE_URL


class _AnchorCollector(HTMLParser):
    """Collect all <a> tags with their href and text content."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attr_map = {name.lower(): (value or "") for name, value in attrs}
        href = attr_map.get("href", "")
        if not href:
            return
        self._current = {"href": href.strip()}
        self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._current is None:
            return
        self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._current is None:
            return
        text = " ".join(part.strip() for part in self._text_parts if part and part.strip())
        item = {
            "href": self._current.get("href", ""),
            "text": html.unescape(text).strip(),
        }
        self.items.append(item)
        self._current = None
        self._text_parts = []


def _discover_pdf_url(session: requests.Session, base_url: str = BASE_URL) -> str | None:
    """Scrape the L&C detention page for the current jail roster PDF link."""
    logger.info("Fetching Lewis & Clark detention page: %s", base_url)
    resp = session.get(base_url, timeout=30)
    resp.raise_for_status()

    parser = _AnchorCollector()
    parser.feed(resp.text)

    candidates: list[tuple[str, str]] = []
    for item in parser.items:
        href = item["href"]
        text = item["text"]
        if not href.lower().endswith(".pdf"):
            continue
        combined = f"{text} {href}".lower()
        if any(k in combined for k in ["jail roster", "inmate roster", "current inmate"]):
            full_url = urljoin(base_url, href)
            candidates.append((full_url, text))

    if not candidates:
        logger.warning("No jail roster PDF found on L&C detention page")
        return None

    # Prefer the shortest / most generic filename (jail_roster.pdf over dated docs)
    candidates.sort(key=lambda x: ("jail_roster" not in x[0].lower(), len(x[0])))
    logger.info("Discovered L&C roster PDF: %s", candidates[0][0])
    return candidates[0][0]


def _download_pdf(session: requests.Session, url: str) -> bytes:
    resp = session.get(url, timeout=60)
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "").lower()
    if "text/html" in content_type or b"<!DOCTYPE html" in resp.content[:256].lower():
        raise RuntimeError(f"Expected PDF but received HTML from {url}")
    if len(resp.content) < 512:
        raise RuntimeError(f"PDF from {url} is unusually small ({len(resp.content)} bytes)")
    return resp.content


def _normalize_datetime(value: str) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    for fmt in ("%m/%d/%y %H:%M", "%m/%d/%Y %H:%M"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return None


def _parse_roster_text(pdf_text: str, source_url: str) -> list[JailBookingRecord]:
    """Parse plain text from the Lewis & Clark jail roster PDF."""
    records: list[JailBookingRecord] = []
    seen_ids: set[str] = set()

    lines = [line.rstrip() for line in pdf_text.splitlines()]

    # An entry starts with: LASTNAME, [FIRSTNAME] <age> <sex> <MM/DD/YY HH:MM> <booking#>
    # Some rows are missing the first name in the PDF, so make it optional.
    entry_start_re = re.compile(
        r"^([A-Z][A-Z'\-]+,\s*(?:[A-Z][A-Z'\-\s]*?)?)\s+(\d{1,3})\s+(Male|Female)\s+(\d{2}/\d{2}/\d{2}\s+\d{2}:\d{2})\s+(\S+)"
    )

    current_block: list[str] = []

    def flush_block(block: list[str]) -> None:
        if not block:
            return
        records.extend(_parse_inmate_block(block, source_url, seen_ids))

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if entry_start_re.match(line):
            flush_block(current_block)
            current_block = [line]
        elif current_block:
            current_block.append(line)

    flush_block(current_block)
    logger.info("Parsed %d Lewis & Clark records", len(records))
    return records


def _parse_inmate_block(block: list[str], source_url: str, seen_ids: set[str]) -> list[JailBookingRecord]:
    if not block:
        return []

    full_text = " ".join(block)
    match = re.match(
        r"^([A-Z][A-Z'\-]+),\s*(?:([A-Z][A-Z'\-\s]*?))?\s+(\d{1,3})\s+(Male|Female)\s+(\d{2}/\d{2}/\d{2}\s+\d{2}:\d{2})\s+(\S+)\s+(.*)$",
        full_text,
        re.DOTALL,
    )
    if not match:
        return []

    last_name = match.group(1).title()
    first_name = (match.group(2) or "").strip().title()
    person_name = f"{last_name}, {first_name}" if first_name else last_name
    age = int(match.group(3)) if match.group(3).isdigit() else None
    booking_at = _normalize_datetime(match.group(5))
    booking_number = match.group(6).strip()
    charges_text = match.group(7).strip()

    charges_text = re.sub(r"\s+", " ", charges_text).strip()
    if not charges_text:
        charges_text = "Charge details available on the official Lewis & Clark County inmate roster."

    source_record_id = f"lewis_clark:{person_name.lower().replace(' ', '-')}:{booking_number}:{booking_at or ''}"
    if source_record_id in seen_ids:
        counter = 1
        while f"{source_record_id}:{counter}" in seen_ids:
            counter += 1
        source_record_id = f"{source_record_id}:{counter}"
    seen_ids.add(source_record_id)

    return [
        JailBookingRecord(
            source_record_id=source_record_id,
            person_name=person_name,
            age=age,
            booking_number=booking_number,
            booking_at=booking_at,
            charges_summary=charges_text,
            source_url=source_url,
        )
    ]


def fetch_lewis_clark_bookings(source_url: str | None = None) -> list[JailBookingRecord]:
    """Fetch and parse Lewis & Clark County jail roster PDF."""
    target = source_url or DEFAULT_SOURCE_URL
    session = requests.Session()
    pdf_url = _discover_pdf_url(session, target)
    if not pdf_url:
        return []
    pdf_bytes = _download_pdf(session, pdf_url)
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    return _parse_roster_text(text, pdf_url)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    recs = fetch_lewis_clark_bookings()
    print(f"Fetched {len(recs)} records")
    for r in recs[:5]:
        print(r)
