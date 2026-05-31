#!/usr/bin/env python3
"""
rosebud_inmate.py
=================
Fetches Rosebud County incarceration and warrants PDFs from their
WordPress-hosted sheriff page and parses them into JailBookingRecords.

Integrates into the standard Montana Blotter jail_bookings.py pipeline.
"""

from __future__ import annotations

import html
import io
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Iterable
from urllib.parse import urljoin

import requests

import pdfplumber

sys.path.insert(0, "/root/montanablotter")
from services.ingestion.models import JailBookingRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://www.rosebudcountymt.gov/sheriff"
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


def _discover_pdfs(session: requests.Session, base_url: str = BASE_URL) -> list[dict[str, str]]:
    """Scrape the Rosebud sheriff page for PDF links."""
    logger.info("Fetching Rosebud sheriff page: %s", base_url)
    resp = session.get(base_url, timeout=30)
    resp.raise_for_status()

    parser = _AnchorCollector()
    parser.feed(resp.text)

    pdfs: list[dict[str, str]] = []
    for item in parser.items:
        href = item["href"]
        text = item["text"]
        if not href.lower().endswith(".pdf"):
            continue
        full_url = urljoin(base_url, href)
        pdfs.append({
            "url": full_url,
            "text": text,
            "filename": os.path.basename(href),
        })

    logger.info("Discovered %d PDFs on Rosebud page", len(pdfs))
    return pdfs


def _is_incarceration_pdf(pdf: dict[str, str]) -> bool:
    combined = f"{pdf['text']} {pdf['filename']}".lower()
    return any(k in combined for k in ["incarceration", "inmate roster", "inmate roster"])


def _is_warrant_pdf(pdf: dict[str, str]) -> bool:
    combined = f"{pdf['text']} {pdf['filename']}".lower()
    return "warrant" in combined


def _download_pdf(session: requests.Session, url: str) -> bytes:
    resp = session.get(url, timeout=60)
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "").lower()
    if "text/html" in content_type or b"<!DOCTYPE html" in resp.content[:256].lower():
        raise RuntimeError(f"Expected PDF but received HTML from {url}")
    if len(resp.content) < 512:
        raise RuntimeError(f"PDF from {url} is unusually small ({len(resp.content)} bytes)")
    return resp.content


def _parse_incarceration_table(pdf_bytes: bytes, source_url: str) -> list[JailBookingRecord]:
    """Parse an Incarceration List PDF using pdfplumber table extraction."""
    records: list[JailBookingRecord] = []
    seen_ids: set[str] = set()

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            if not tables:
                continue
            for table in tables:
                for row in table:
                    if not row or len(row) < 6:
                        continue
                    # Skip header rows and section headers
                    if row[0] and str(row[0]).strip().upper() in {
                        "INCARCERATION", "COUNTY HOLDS", "DOC HOLDS", "FEDERAL HOLDS"
                    }:
                        continue
                    if not row[0] or not row[3]:
                        continue

                    date_str = str(row[0]).strip()
                    # Must look like a date
                    if not re.match(r"\d{1,2}/\d{1,2}/\d{4}", date_str):
                        continue

                    person_name = str(row[3]).strip().replace("\n", " ")
                    person_name = re.sub(r"\s+", " ", person_name)
                    if "," in person_name:
                        parts = person_name.split(",", 1)
                        person_name = f"{parts[0].strip().title()}, {parts[1].strip().title()}"
                    else:
                        person_name = person_name.title()

                    age_str = str(row[2] or "").strip()
                    age = int(age_str) if age_str.isdigit() else None

                    charges = str(row[4] or "").strip().replace("\n", "; ")
                    charges = re.sub(r"\s+", " ", charges)

                    bond = str(row[5] or "").strip().replace("\n", " ")
                    bond = re.sub(r"\s+", " ", bond)
                    if bond and bond.upper() != "NO BOND":
                        charges = f"{charges}; Bond {bond}" if charges else f"Bond {bond}"

                    booking_at = _normalize_incarceration_date(date_str)
                    source_record_id = f"rosebud:{person_name.lower().replace(' ', '-')}:{booking_at or date_str}"
                    if source_record_id in seen_ids:
                        # Deduplicate by appending a counter
                        counter = 1
                        while f"{source_record_id}:{counter}" in seen_ids:
                            counter += 1
                        source_record_id = f"{source_record_id}:{counter}"
                    seen_ids.add(source_record_id)

                    records.append(
                        JailBookingRecord(
                            source_record_id=source_record_id,
                            person_name=person_name,
                            age=age,
                            booking_number="",
                            booking_at=booking_at,
                            charges_summary=charges or "Charge details available on the official Rosebud County inmate roster.",
                            source_url=source_url,
                        )
                    )

    logger.info("Parsed %d incarceration records from Rosebud PDF", len(records))
    return records


def _normalize_incarceration_date(value: str) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return None


def _parse_warrants_text(pdf_bytes: bytes, source_url: str) -> list[JailBookingRecord]:
    """Parse a Warrants List PDF using text extraction and heuristics."""
    records: list[JailBookingRecord] = []
    seen_ids: set[str] = set()

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    # Skip header lines
    in_list = False
    for line in lines:
        if line.startswith("Rosebud County Sheriff's Office"):
            continue
        if line.startswith("WARRANT LIST"):
            in_list = True
            continue
        if line.startswith("Last, First Name"):
            in_list = True
            continue
        if not in_list:
            continue
        # Try to parse a warrant line
        record = _parse_warrant_line(line)
        if not record:
            continue
        if record.source_record_id in seen_ids:
            counter = 1
            while f"{record.source_record_id}:{counter}" in seen_ids:
                counter += 1
            record = JailBookingRecord(
                source_record_id=f"{record.source_record_id}:{counter}",
                person_name=record.person_name,
                age=record.age,
                booking_number=record.booking_number,
                booking_at=record.booking_at,
                charges_summary=record.charges_summary,
                source_url=record.source_url,
            )
        seen_ids.add(record.source_record_id)
        records.append(record)

    logger.info("Parsed %d warrant records from Rosebud PDF", len(records))
    return records


def _parse_warrant_line(line: str) -> JailBookingRecord | None:
    """Parse a single line from the warrants PDF.

    Expected format roughly:
        LAST, FIRST  BOND_AMOUNT (BondType)  CHARGES...  Issued By COURT_NAME
    """
    # Name must start with LAST, FIRST
    name_match = re.match(r"^([A-Z][A-Z' -]+),\s+([A-Z][A-Z' -]+)\s+", line)
    if not name_match:
        return None

    last = name_match.group(1).strip()
    first = name_match.group(2).strip()
    person_name = f"{last.title()}, {first.title()}"

    remainder = line[name_match.end():]

    # Bond: look for amount in parentheses type
    bond_match = re.match(r"([\d,]+\.\d+)\s*\(([^)]+)\)\s*", remainder)
    bond_str = ""
    bond_type = ""
    if bond_match:
        bond_str = bond_match.group(1)
        bond_type = bond_match.group(2).strip()
        remainder = remainder[bond_match.end():]

    # Court names commonly at the end
    court_match = re.search(
        r"(Lee Busch Justice of the Peace|Penny McPherson-City|Elaine Egeland-City|Forsyth City|Colstrip City|Justice|District|DOC|City)\s*$",
        remainder,
        re.IGNORECASE,
    )
    court = ""
    if court_match:
        court = court_match.group(1).strip()
        remainder = remainder[: court_match.start()].strip()

    charges = remainder.strip()
    if not charges:
        charges = "Active warrant"

    if bond_str:
        charges = f"WARRANT: {charges}; Bond ${bond_str} ({bond_type})" if bond_type else f"WARRANT: {charges}; Bond ${bond_str}"
    else:
        charges = f"WARRANT: {charges}"
    if court:
        charges = f"{charges}; Issued By {court}"

    source_record_id = f"rosebud-warrant:{person_name.lower().replace(' ', '-')}"
    return JailBookingRecord(
        source_record_id=source_record_id,
        person_name=person_name,
        age=None,
        booking_number="",
        booking_at=None,
        charges_summary=charges,
        source_url=None,
    )


def fetch_rosebud_bookings(source_url: str | None = None) -> list[JailBookingRecord]:
    """Fetch Rosebud County incarceration and warrants PDFs and return parsed records."""
    url = source_url or DEFAULT_SOURCE_URL
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://montanablotter.com)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )

    pdfs = _discover_pdfs(session, url)
    incarceration_pdf = next((p for p in pdfs if _is_incarceration_pdf(p)), None)
    warrant_pdf = next((p for p in pdfs if _is_warrant_pdf(p)), None)

    records: list[JailBookingRecord] = []

    if incarceration_pdf:
        logger.info("Downloading Rosebud incarceration PDF: %s", incarceration_pdf["url"])
        pdf_bytes = _download_pdf(session, incarceration_pdf["url"])
        records.extend(_parse_incarceration_table(pdf_bytes, incarceration_pdf["url"]))
    else:
        logger.warning("No Rosebud incarceration PDF found on %s", url)

    if warrant_pdf:
        logger.info("Downloading Rosebud warrants PDF: %s", warrant_pdf["url"])
        try:
            pdf_bytes = _download_pdf(session, warrant_pdf["url"])
            records.extend(_parse_warrants_text(pdf_bytes, warrant_pdf["url"]))
        except Exception:
            logger.exception("Failed to parse Rosebud warrants PDF; continuing with incarceration records only")
    else:
        logger.info("No Rosebud warrants PDF found on %s", url)

    return records
