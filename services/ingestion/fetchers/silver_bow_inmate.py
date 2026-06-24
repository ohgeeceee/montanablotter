#!/usr/bin/env python3
"""
silver_bow_inmate.py
====================
Fetches Butte-Silver Bow (MT) inmate offense list PDF from the detention page,
converts the scanned pages to images, OCRs them, and parses the text into
JailBookingRecords.
"""
from __future__ import annotations

import html
import io
import logging
import re
import sys
from datetime import datetime
from html.parser import HTMLParser
from typing import Iterable
from urllib.parse import urljoin

import requests
import urllib3
from pdf2image import convert_from_bytes
import pytesseract

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
sys.path.insert(0, "/root/montanablotter")
from services.ingestion.models import JailBookingRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://www.co.silverbow.mt.us/3274/Detention-Center/"
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


UA = "Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://montanablotter.com)"


def _discover_pdf_url(session: requests.Session, base_url: str = BASE_URL) -> str | None:
    """Scrape the Silver Bow detention page for the current jail roster PDF link."""
    logger.info("Fetching Silver Bow detention page: %s", base_url)
    resp = session.get(base_url, timeout=30, verify=False, headers={"User-Agent": UA})
    resp.raise_for_status()

    parser = _AnchorCollector()
    parser.feed(resp.text)

    candidates: list[tuple[str, str]] = []
    for item in parser.items:
        href = item["href"]
        text = item["text"]
        if not href:
            continue
        combined = f"{text} {href}".lower()
        if not any(k in combined for k in ["jail roster", "inmate roster", "inmate offense", "documentcenter"]):
            continue
        full_url = urljoin(base_url, href)
        candidates.append((full_url, text))

    if not candidates:
        logger.warning("No jail roster PDF found on Silver Bow detention page")
        return None

    candidates.sort(key=lambda x: ("jail roster" in x[1].lower(), "documentcenter" in x[0].lower(), len(x[0])), reverse=True)
    logger.info("Discovered Silver Bow roster PDF: %s", candidates[0][0])
    return candidates[0][0]


def _download_pdf(session: requests.Session, url: str) -> bytes:
    resp = session.get(url, timeout=60, verify=False, headers={"User-Agent": UA})
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "").lower()
    if "text/html" in content_type or b"<!DOCTYPE html" in resp.content[:256].lower():
        raise RuntimeError(f"Expected PDF but received HTML from {url}")
    if len(resp.content) < 512:
        raise RuntimeError(f"PDF from {url} is unusually small ({len(resp.content)} bytes)")
    return resp.content


def _ocr_pdf(pdf_bytes: bytes, dpi: int = 150) -> str:
    """Convert PDF pages to images and OCR them with Tesseract."""
    logger.info("OCRing Silver Bow PDF at %d dpi", dpi)
    images = convert_from_bytes(pdf_bytes, dpi=dpi)
    texts: list[str] = []
    for idx, image in enumerate(images):
        logger.debug("OCR page %d", idx + 1)
        text = pytesseract.image_to_string(image)
        texts.append(text)
    return "\n".join(texts)


def _normalize_date(value: str) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    for fmt in ("%m/%d/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return None


def _parse_roster_text(pdf_text: str, source_url: str) -> list[JailBookingRecord]:
    """Parse OCR text from the Silver Bow inmate offense list PDF."""
    records: list[JailBookingRecord] = []
    seen_ids: set[str] = set()

    lines = [line.strip() for line in pdf_text.splitlines()]

    # Inmate header: Book#: 26-0937 Name: PANSCH, MICHAEL G Name ID: 335 Rel. Dt: **/**/**
    header_re = re.compile(
        r"Book#:\s*(\S+)\s+Name:\s*([A-Z][A-Z'\-]+,\s*[A-Z][A-Z'\-\s]+?)\s+Name\s*ID:\s*(\S+)\s+Rel\.\s*Dt:"
    )

    current: dict | None = None

    def flush_current() -> None:
        nonlocal current
        if current is None:
            return
        person_name = current["person_name"]
        booking_number = current["booking_number"]
        dates = current["dates"]
        charges = current["charges"]
        booking_at = max(dates) if dates else None
        charges_summary = "; ".join(charges) if charges else "Charge details available on the official Silver Bow inmate roster."
        source_record_id = f"silver_bow:{person_name.lower().replace(' ', '-')}:{booking_number}:{booking_at or ''}"
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
                booking_at=booking_at,
                charges_summary=charges_summary,
                source_url=source_url,
            )
        )
        current = None

    # Charge row: statute + offense + optional agency/jud disp + booking date + num days
    charge_re = re.compile(
        r"^(\S+)\s+(.+?)\s+(\d{2}/\d{2}/\d{2})\s+(\d+)$"
    )

    for line in lines:
        if not line:
            continue

        # Skip column header repeats and page footer
        if re.match(r"^(Statute#|Statute\s+Bill|Butte-Silver Bow|Inmate Offense List|rpjlcio)", line, re.I):
            continue

        m = header_re.match(line)
        if m:
            flush_current()
            booking_number = m.group(1).strip()
            person_name = m.group(2).strip().title()
            current = {
                "booking_number": booking_number,
                "person_name": person_name,
                "dates": [],
                "charges": [],
            }
            continue

        if current is None:
            continue

        cm = charge_re.match(line)
        if cm:
            statute = cm.group(1).strip()
            offense = cm.group(2).strip()
            date_str = cm.group(3).strip()
            normalized = _normalize_date(date_str)
            if normalized:
                current["dates"].append(normalized)
            if statute and offense:
                current["charges"].append(f"{statute} - {offense} ({date_str})")

    flush_current()
    logger.info("Parsed %d Silver Bow records", len(records))
    return records


def fetch_silver_bow_bookings(source_url: str | None = None) -> list[JailBookingRecord]:
    """Fetch and parse Butte-Silver Bow inmate offense list PDF via OCR."""
    target = source_url or DEFAULT_SOURCE_URL
    session = requests.Session()
    pdf_url = _discover_pdf_url(session, target)
    if not pdf_url:
        return []
    pdf_bytes = _download_pdf(session, pdf_url)
    pdf_text = _ocr_pdf(pdf_bytes)
    return _parse_roster_text(pdf_text, pdf_url)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    recs = fetch_silver_bow_bookings()
    print(f"Fetched {len(recs)} records")
    for r in recs:
        print(r)
