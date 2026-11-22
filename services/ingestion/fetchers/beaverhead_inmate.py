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

    Current layout (``InmateOffenseList,withStatute`` report): each inmate
    starts with a header line and is followed by that inmate's offence rows::

        Book#: 26BK00031 Name: COX,CHRISTOPHERM NameID: 15983 Rel.Dt: **/**/**
        Statute# Statute BillAgncy JudDisp Reference# Booking Num
        45-5-102 DeliberateHomicide BCSO DC-1-2025- 10/16/25 392
        0004336-IN

    ``Rel.Dt`` is redacted (``**/**/**``) on the public roster, and the PDF is
    space-stripped so names arrive as ``LAST,FIRST<MI>`` — both are normalised
    by the shared helpers in ``roster_generic``.
    """
    from services.ingestion.roster_generic import (
        _normalize_beaverhead_date,
        _normalize_beaverhead_name,
    )

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

    header_re = re.compile(
        r"^Book#:\s*(?P<book>\S+)\s+Name:\s*(?P<name>.+?)\s+"
        r"NameID:\s*(?P<nameid>\d+)\s+Rel\.Dt:\s*(?P<rel>\S+)\s*$"
    )
    skip_prefixes = (
        "Statute#", "Date Days", "InmateOffenseList", "Page ", "Book#:",
    )
    # Report footer stamp, e.g. "rpjlciol.x4 11/12/26".
    footer_re = re.compile(
        r"^\S+\.(?:x4|pdf|docx?|txt|xlsx?)\s+\d{1,2}/\d{1,2}/\d{2,4}$", re.IGNORECASE
    )

    records: list[JailBookingRecord] = []
    seen: set[str] = set()
    current: tuple[re.Match, list[str]] | None = None

    def flush(match: re.Match, body: list[str]) -> None:
        book = match.group("book")
        raw_name = match.group("name")
        if not book or not raw_name or book in seen:
            return
        seen.add(book)
        person_name = _normalize_beaverhead_name(raw_name)
        charges = " ".join(part for part in body if part).strip()
        records.append(JailBookingRecord(
            source_record_id=f"beaverhead:{book}",
            person_name=person_name,
            age=None,
            booking_number=book,
            booking_at=None,
            charges_summary=charges or "Charge details available on the official Beaverhead County roster.",
            source_url=source_url,
        ))

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = header_re.match(line)
        if match:
            if current is not None:
                flush(*current)
            current = (match, [])
            continue
        if current is None:
            continue
        if line.startswith(skip_prefixes):
            continue
        if footer_re.match(line):
            continue
        current[1].append(line)

    if current is not None:
        flush(*current)

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
