#!/usr/bin/env python3
"""
custer_inmate.py
================
Fetches Custer County (MT) daily inmate roster PDF from the sheriff page and
parses it into JailBookingRecords.
"""
from __future__ import annotations

import html
import io
import logging
import re
import sys
from html.parser import HTMLParser
from typing import Iterable
from urllib.parse import urljoin

import requests
import pdfplumber

sys.path.insert(0, "/root/montanablotter")
from services.ingestion.models import JailBookingRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://custercountymt.gov/emergency-enforcement/sheriff/"
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
    """Scrape the Custer sheriff page for the current daily roster PDF link."""
    logger.info("Fetching Custer sheriff page: %s", base_url)
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
        if any(k in combined for k in ["roster", "daily", "inmate"]):
            full_url = urljoin(base_url, href)
            candidates.append((full_url, text))

    if not candidates:
        logger.warning("No daily roster PDF found on Custer sheriff page")
        return None

    # Prefer the link whose text mentions "daily roster" or "inmate"
    candidates.sort(key=lambda x: ("daily" in x[1].lower(), "roster" in x[1].lower(), "inmate" in x[1].lower()), reverse=True)
    logger.info("Discovered Custer roster PDF: %s", candidates[0][0])
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


def _normalize_date(value: str) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    # Custer uses MM/DD/YY HH:MM
    for fmt in ("%m/%d/%y %H:%M", "%m/%d/%Y %H:%M"):
        try:
            from datetime import datetime
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return None


def _parse_roster_text(pdf_text: str, source_url: str) -> list[JailBookingRecord]:
    """Parse plain text from the Custer County daily roster PDF."""
    records: list[JailBookingRecord] = []
    seen_ids: set[str] = set()

    lines = [line.rstrip() for line in pdf_text.splitlines()]

    # An entry starts with a line matching: LASTNAME, FIRSTNAME <date> <time> ...
    entry_start_re = re.compile(r"^([A-Z][A-Z'\-]+,\s*[A-Z][A-Z'\-\s]+?)(?:\s+\d{2}/\d{2}/\d{2})?\s+\d{2}:\d{2}\s+")

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
    logger.info("Parsed %d Custer records", len(records))
    return records


def _parse_inmate_block(block: list[str], source_url: str, seen_ids: set[str]) -> list[JailBookingRecord]:
    if not block:
        return []

    # Join block into one string for easier regex work
    full_text = " ".join(block)

    # Name is the first two words: LASTNAME, FIRSTNAME (possibly with suffix)
    name_match = re.match(r"^([A-Z][A-Z'\-]+),\s*([A-Z][A-Z'\-]+(?:\s+(?:JR|SR|II|III|IV))?)", full_text)
    if not name_match:
        return []
    last_name = name_match.group(1).title()
    first_name = name_match.group(2).title()
    person_name = f"{last_name}, {first_name}"

    # Booking date and time: MM/DD/YY HH:MM
    dt_match = re.search(r"(\d{2}/\d{2}/\d{2})\s+(\d{2}:\d{2})", full_text)
    booking_at: str | None = None
    if dt_match:
        booking_at = _normalize_date(f"{dt_match.group(1)} {dt_match.group(2)}")

    # Jacket #: numeric token typically 7 digits, near agency/bond
    jacket_match = re.search(r"(\d{7,})\s+", full_text)
    booking_number = jacket_match.group(1) if jacket_match else ""

    # Bond: look for patterns like "Cash/Surety - $50000.00", "No Bond - $0.00"
    bond_match = re.search(r"(Cash/Surety|No Bond)\s*-\s*\$([\d,]+\.\d{2})(?:\s*-)?", full_text)
    bond = ""
    if bond_match:
        bond = f"{bond_match.group(1)} ${bond_match.group(2)}"

    # Charges: everything after the time and before the jacket/agency/bond tail.
    # Heuristic: strip name, date/time, and trailing agency/bond tokens.
    charges_text = full_text[name_match.end():]
    # Remove the date/time
    charges_text = re.sub(r"^\s*\d{2}/\d{2}/\d{2}\s+\d{2}:\d{2}\s*", "", charges_text)
    # Remove trailing jacket/agency/bond: scan from the end for the bond pattern
    # and remove it plus the agency words before it.
    if bond_match:
        charges_text = charges_text[:bond_match.start()].strip()
    if jacket_match and booking_number:
        # Remove jacket number and following agency words up to bond (already removed)
        idx = charges_text.find(booking_number)
        if idx != -1:
            charges_text = charges_text[:idx].strip()

    charges_text = re.sub(r"\s+", " ", charges_text).strip()
    charges_text = charges_text.strip(";-")

    if bond:
        charges_text = f"{charges_text}; Bond: {bond}" if charges_text else f"Bond: {bond}"

    if not charges_text:
        charges_text = "Charge details available on the official Custer County inmate roster."

    source_record_id = f"custer:{person_name.lower().replace(' ', '-')}:{booking_at or booking_number}"
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
            age=None,
            booking_number=booking_number,
            booking_at=booking_at,
            charges_summary=charges_text,
            source_url=source_url,
        )
    ]


def fetch_custer_bookings(source_url: str | None = None) -> list[JailBookingRecord]:
    """Fetch and parse Custer County daily roster PDF."""
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
    recs = fetch_custer_bookings()
    print(f"Fetched {len(recs)} records")
    for r in recs[:5]:
        print(r)
