#!/usr/bin/env python3
"""
lincoln_inmate.py
=================
Fetches Lincoln County (MT) current inmate offense list PDF from the sheriff
detention page and parses it into JailBookingRecords.
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

BASE_URL = "https://lincolncountymt.us/sheriff-home/detention/"
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
    """Scrape the Lincoln detention page for the current jail roster PDF link."""
    logger.info("Fetching Lincoln detention page: %s", base_url)
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
        if any(k in combined for k in ["jail roster", "inmate roster", "inmate", "jail"]):
            full_url = urljoin(base_url, href)
            candidates.append((full_url, text))

    if not candidates:
        logger.warning("No jail roster PDF found on Lincoln detention page")
        return None

    # Prefer the link whose text/filename most clearly says "Inmate Jail Roster"
    candidates.sort(
        key=lambda x: (
            "inmate-jail-roster" in x[0].lower(),
            "jail roster" in x[1].lower(),
            "inmate" in x[1].lower(),
        ),
        reverse=True,
    )
    logger.info("Discovered Lincoln roster PDF: %s", candidates[0][0])
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


def _parse_name(raw: str) -> str:
    """Convert 'BAKER,DAISYR' into 'Baker, Daisy R'."""
    if "," not in raw:
        return raw.title()
    last, first = raw.split(",", 1)
    first = first.strip()
    # Lincoln merges first name + middle initial in all caps with no space, e.g. 'DAISYR'.
    # Split the trailing single initial when the whole token is uppercase and long enough.
    if (
        first.isalpha()
        and first.isupper()
        and len(first) >= 5
        and re.match(r"^[A-Z]{4,}[A-Z]$", first)
    ):
        first = first[:-1] + " " + first[-1]
    return f"{last.strip().title()}, {first.title()}"


def _parse_roster_text(pdf_text: str, source_url: str) -> list[JailBookingRecord]:
    """Parse plain text from the Lincoln County inmate offense list PDF.

    The Lincoln PDF (gerenered ~2026-06) interleaves full charge lines with
    continuation lines that lack an explicit statute token, e.g.::

        20-13-301    Resisting/Obstructing an Officer of the Law      CFJA    Misd   M
        Providing False Information                              CFJA    Misd   F
        45-6-204     Providing False Information                      CFJA    Misd   F
        Doing Business Without License                           CFJA    Misd   M

    The original ``charge_re`` required every line to start with a statute
    token, so the continuation lines were silently dropped and the total
    record count came back as zero.  This version handles bare description
    lines as continuations of the most recent charge in the current block.
    """
    records: list[JailBookingRecord] = []
    seen_ids: set[str] = set()

    lines = [line.strip() for line in pdf_text.splitlines()]

    # Header line for each inmate block
    header_re = re.compile(
        r"Booking#:\s*(\S+)\s+Name:\s*([A-Z,]+)\s+NameNumber:\s*(\S+)"
    )
    # Statute + offense + court + class + optional M/F  (full charge line)
    charge_re = re.compile(
        r"^(\S+)\s+(.+?)\s+([A-Z]{2,4})\s+(\S+)(?:\s+(M|F))?$"
    )
    # Continuation: a bare offense description line (no leading statute).
    continuation_re = re.compile(
        r"^(.+?)\s+([A-Z]{2,4})\s+(\S+)(?:\s+(M|F))?$"
    )

    current: dict | None = None

    def flush_current() -> None:
        nonlocal current
        if current is None:
            return
        person_name = current["person_name"]
        booking_number = current["booking_number"]
        charges = "; ".join(current["charges"]) if current["charges"] else "Charge details available on the official Lincoln County inmate roster."
        source_record_id = f"lincoln:{person_name.lower().replace(' ', '-')}:{booking_number}"
        if source_record_id in seen_ids:
            counter = 1
            while f"{source_record_id}:{counter}" in seen_ids:
                counter += 1
            source_record_id = f"{source_record_id}:{counter}"
        seen_ids.add(source_record_id)
        records.append(
            JailBookingRecord(
                source_record_id=source_record_id,
                person_name=person_name,
                age=None,
                booking_number=booking_number,
                booking_at=None,
                charges_summary=charges,
                source_url=source_url,
            )
        )
        current = None

    for line in lines:
        # Skip page headers/footers and the column-header row (no actual booking number)
        if re.match(r"^(Lincoln County Sheriff|Current Inmate Offense List|Statute\s+Offense|Booking#:\s*Name:\s*NameNumber:|Page \d|rpjlciol)", line, re.I):
            continue

        m = header_re.match(line)
        if m:
            flush_current()
            booking_number = m.group(1).strip()
            person_name = _parse_name(m.group(2))
            current = {
                "booking_number": booking_number,
                "person_name": person_name,
                "charges": [],
            }
            continue

        if current is None:
            continue

        cm = charge_re.match(line)
        if cm:
            statute = cm.group(1).strip()
            offense = cm.group(2).strip()
            if statute and offense:
                current["charges"].append(f"{statute} - {offense}")
            continue

        # Continuation line without explicit statute
        cm2 = continuation_re.match(line)
        if cm2:
            offense = cm2.group(1).strip()
            court = cm2.group(2).strip()
            classification = cm2.group(3).strip()
            if offense and court and classification:
                current["charges"].append(f"{offense} ({court}, {classification})")
            continue

    flush_current()
    logger.info("Parsed %d Lincoln records", len(records))
    return records


def fetch_lincoln_bookings(source_url: str | None = None) -> list[JailBookingRecord]:
    """Fetch and parse Lincoln County inmate offense list PDF."""
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
    recs = fetch_lincoln_bookings()
    print(f"Fetched {len(recs)} records")
    for r in recs[:5]:
        print(r)
