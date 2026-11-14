#!/usr/bin/env python3
"""
beaverhead_inmate.py
=====================
Fetches Beaverhead County (MT) jail roster PDF from
https://beaverheadcountymt.gov/departments/sheriff/jail-roster/
and parses it into JailBookingRecords.

The page is a WordPress attachment wrapper that embeds the current roster PDF
at /wp-content/uploads/YYYY/MM/Jail-Roster.pdf.  We download the PDF and
parse it with pdfplumber.
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
from urllib.parse import urljoin

import pdfplumber
import requests

from services.ingestion.models import JailBookingRecord

logger = logging.getLogger(__name__)

BASE_PAGE = "https://beaverheadcountymt.gov/departments/sheriff/jail-roster/"
PDF_URL_PATTERN = re.compile(
    r"https://beaverheadcountymt\.gov/wp-content/uploads/\d{4}/\d{2}/Jail-Roster\.pdf",
    re.IGNORECASE,
)
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://mtblotter.com)",
    "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
})


def _discover_pdf_url(session: requests.Session, page_url: str = BASE_PAGE) -> str | None:
    """Scrape the WordPress attachment page for the embedded PDF URL."""
    try:
        resp = session.get(page_url, timeout=30)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("Beaverhead roster page fetch failed: %s", exc)
        return None

    match = PDF_URL_PATTERN.search(resp.text)
    if match:
        url = match.group(0)
        logger.info("Beaverhead discovered roster PDF: %s", url)
        return url

    # Fallback: look for any /wp-content/uploads/.../Jail-Roster.pdf link
    fallback = re.search(
        r'href=[\'"]([^\'"]*wp-content/uploads/\d{4}/\d{2}/Jail-Roster\.pdf[^\'"]*)[\'"]',
        resp.text,
        re.IGNORECASE,
    )
    if fallback:
        url = fallback.group(1)
        logger.info("Beaverhead fallback roster PDF: %s", url)
        return url

    logger.warning("No Beaverhead roster PDF found on page")
    return None


def _download_pdf(session: requests.Session, url: str) -> bytes | None:
    try:
        resp = session.get(url, timeout=60, allow_redirects=True)
        resp.raise_for_status()
        if resp.content[:4] != b"%PDF":
            logger.warning("Beaverhead URL did not return a PDF (%d bytes)", len(resp.content))
            return None
        return resp.content
    except Exception as exc:
        logger.warning("Beaverhead PDF download failed: %s", exc)
        return None


def _parse_beaverhead_pdf(pdf_bytes: bytes, source_url: str) -> list[JailBookingRecord]:
    """Parse the Beaverhead County jail roster PDF into booking records.

    The PDF is an "Inmate List" table with columns:
    Booking Type | Name | Booked For | Scheduled Release | Days in Custody | Disposition
    Each row is one inmate.  Name is "LAST, FIRST MIDDLE".
    """
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            text = "\n".join((page.extract_text() or "") for page in pdf.pages)
    except Exception as exc:
        logger.warning("Beaverhead PDF parse failed: %s", exc)
        return []

    if not text.strip():
        logger.info("Beaverhead PDF contained no extractable text")
        return []

    logger.info("Beaverhead PDF text length: %d chars", len(text))

    records: list[JailBookingRecord] = []
    seen_names: set[str] = set()

    row_re = re.compile(
        r'^\w+\s+(.+?,\s*[A-Z][A-Z\s]+?)\s+'
        r'(Beaverhead|Montana|Dillon|Department|Other)\b',
        re.IGNORECASE,
    )

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("Booking Type") or line.startswith("Inmate List"):
            continue
        if "Page " in line and "of" in line:
            continue

        m = row_re.match(line)
        if not m:
            continue

        raw_name = m.group(1).strip()
        if raw_name in seen_names:
            continue
        seen_names.add(raw_name)

        # Normalise name: "SCHMIDT, CARSON LOGAN" -> "Schmidt, Carson Logan"
        if "," in raw_name:
            last_part, first_part = raw_name.split(",", 1)
            person_name = f"{last_part.strip().title()}, {first_part.strip().title()}"
        else:
            person_name = raw_name.title()

        source_record_id = "beaverhead:" + raw_name.lower().replace(" ", "_").replace(",", "")
        records.append(JailBookingRecord(
            source_record_id=source_record_id,
            person_name=person_name,
            age=None,
            booking_number=source_record_id[-12:],
            booking_at=None,
            charges_summary="Charge details available on the official Beaverhead County roster.",
            source_url=source_url,
        ))

    logger.info("Beaverhead roster: parsed %d records", len(records))
    return records


def fetch_beaverhead_bookings(source_url: str | None = None) -> list[JailBookingRecord]:
    """Fetch and parse the Beaverhead County jail roster.

    Discovers the current PDF URL from the WordPress page, downloads it,
    and parses the roster into booking records.
    """
    target_page = source_url or BASE_PAGE
    pdf_url = _discover_pdf_url(SESSION, target_page)
    if not pdf_url:
        return []

    pdf_bytes = _download_pdf(SESSION, pdf_url)
    if not pdf_bytes:
        return []

    return _parse_beaverhead_pdf(pdf_bytes, pdf_url)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    recs = fetch_beaverhead_bookings()
    print(f"Fetched {len(recs)} Beaverhead records")
    for r in recs[:5]:
        print(r)
