"""Generic roster scraper.

Two adapters:
- HTMLTableRosterScraper: scrapes a public-facing HTML table.
- PDFTextRosterScraper: downloads a PDF and extracts a roster from its text
  using a per-county regex (each county's PDF layout differs).

Both produce ``JailBookingRecord`` instances compatible with
``services.ingestion.jail_bookings``.

Why this exists:
The 24 enabled jail_booking_sources include 11 counties with
"no automated county adapter has been added yet" in their notes.
Rather than 11 hand-rolled scrapers, this module provides two generic
patterns; counties that don't fit either pattern (JS-rendered rosters,
CivicPlus 403s, Zuercher portals without a public API) stay flagged
in the DB until a different approach lands.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Callable, Iterable

import requests

from services.ingestion.models import JailBookingRecord

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HTML table parsing
# ---------------------------------------------------------------------------


class _TableParser(HTMLParser):
    """Stripped-down table parser that yields rows of cell text.

    Skips nested tables, normalizes whitespace, preserves link hrefs
    on <a> tags (so detail URLs can be extracted).
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_table = 0
        self._in_row = False
        self._in_cell = False
        self._cell_parts: list[str] = []
        self._cell_hrefs: list[str] = []
        self._row_cells: list[str] = []
        self._row_hrefs: list[str] = []
        self.rows: list[list[str]] = []
        self.hrefs: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "table":
            self._in_table += 1
        elif self._in_table == 1 and tag == "tr":
            self._in_row = True
            self._row_cells = []
            self._row_hrefs = []
        elif self._in_row and tag in {"td", "th"}:
            self._in_cell = True
            self._cell_parts = []
            self._cell_hrefs = []
        elif self._in_cell and tag == "a":
            href = next((v for k, v in attrs if k.lower() == "href"), None)
            if href:
                self._cell_hrefs.append(href)

    def handle_data(self, data: str) -> None:
        if self._in_cell and data:
            self._cell_parts.append(data.strip())

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._in_cell:
            self._in_cell = False
            text = " ".join(self._cell_parts).strip()
            self._row_cells.append(text)
            self._row_hrefs.append(self._cell_hrefs[-1] if self._cell_hrefs else "")
        elif tag == "tr" and self._in_row:
            self._in_row = False
            if self._row_cells:
                self.rows.append(self._row_cells)
                self.hrefs.append(self._row_hrefs)
        elif tag == "table" and self._in_table > 0:
            self._in_table -= 1


def parse_html_tables(page_html: str) -> tuple[list[list[str]], list[list[str]]]:
    """Return (rows, hrefs) for every top-level <table> on the page.

    Each top-level table is a list of rows; each row is a list of cell
    text. The hrefs list mirrors the rows structure and gives the href
    of the first <a> in each cell, or "" if none.
    """
    parser = _TableParser()
    parser.feed(page_html)
    return parser.rows, parser.hrefs


def find_roster_table(
    page_html: str,
    *,
    header_keywords: Iterable[str] = ("inmate", "name", "charge"),
    min_data_rows: int = 1,
) -> tuple[list[str], list[list[str]], list[list[str]]] | None:
    """Find the first <table> whose header row contains a keyword from
    ``header_keywords``. Returns (headers, data_rows, data_hrefs) or None.

    The table must have at least ``min_data_rows`` non-header rows.
    """
    rows, hrefs = parse_html_tables(page_html)
    if not rows:
        return None

    keywords = tuple(k.lower() for k in header_keywords)
    # Heuristic: the parser flattens across top-level tables, so group
    # rows into chunks by detecting header rows that match keywords.
    chunks: list[tuple[list[str], list[list[str]], list[list[str]]]] = []
    current_header: list[str] | None = None
    current_rows: list[list[str]] = []
    current_hrefs: list[list[str]] = []
    for r, h in zip(rows, hrefs):
        joined = " ".join(c.lower() for c in r)
        if any(kw in joined for kw in keywords):
            # Start a new table chunk
            if current_header is not None and current_rows:
                chunks.append((current_header, current_rows, current_hrefs))
            current_header = r
            current_rows = []
            current_hrefs = []
        elif current_header is not None:
            current_rows.append(r)
            current_hrefs.append(h)
    if current_header is not None and current_rows:
        chunks.append((current_header, current_rows, current_hrefs))

    for header, data_rows, data_hrefs in chunks:
        if len(data_rows) < min_data_rows:
            continue
        return header, data_rows, data_hrefs
    return None


def index_by_header(
    headers: list[str],
    *candidates: str,
) -> int | None:
    """Find the column index whose header matches any candidate.

    Matching is case-insensitive and ignores non-alphanumeric chars, so
    "Booking Date", "booking-date", and "BOOKING_DATE" all match
    "booking date".
    """
    norm = [re.sub(r"[^a-z0-9]+", " ", h.lower()).strip() for h in headers]
    for cand in candidates:
        key = re.sub(r"[^a-z0-9]+", " ", cand.lower()).strip()
        for i, h in enumerate(norm):
            if key == h or key in h or h in key:
                return i
    return None


# ---------------------------------------------------------------------------
# Park County (HTML table) — concrete adapter
# ---------------------------------------------------------------------------


def fetch_park_bookings(source_url: str) -> list[JailBookingRecord]:
    """Scrape the Park County inmate roster.

    URL: https://www.parkcounty.org/Government-Departments/Sheriff-s-Office/Inmates-Housed/

    The page is a WordPress-hosted CivicPlus table with 3 columns:
    ``INMATE`` | ``CHARGE/BOND`` | ``HOLDING AGENCY/ ARREST DATE``.
    Inmate name is "Lastname, Firstname". Charges and bond amount live
    in the same cell separated by dollar amounts.
    """
    response = fetch_url(source_url)
    found = find_roster_table(response.text, header_keywords=("inmate",), min_data_rows=1)
    if not found:
        logger.info("Park County roster: no <table> with inmate header found at %s", source_url)
        return []

    headers, data_rows, _hrefs = found
    name_idx = index_by_header(headers, "inmate", "name", "defendant")
    if name_idx is None:
        logger.warning("Park County roster: could not find INMATE column; headers=%r", headers)
        return []

    records: list[JailBookingRecord] = []
    seen: set[str] = set()
    for row in data_rows:
        if name_idx >= len(row):
            continue
        raw_name = row[name_idx].strip()
        if not raw_name or raw_name.lower() in {"name", "inmate"}:
            continue
        # Park uses "Lastname, Firstname"
        if "," not in raw_name:
            continue
        last, first = (p.strip() for p in raw_name.split(",", 1))
        person_name = f"{first} {last}".strip()
        if not person_name:
            continue

        charges = " ".join(c.strip() for c in row if c.strip()) if row else ""
        source_record_id = hashlib.sha1(
            f"park:{person_name.lower()}:{charges[:80].lower()}".encode("utf-8")
        ).hexdigest()[:20]
        if source_record_id in seen:
            continue
        seen.add(source_record_id)

        records.append(
            JailBookingRecord(
                source_record_id=source_record_id,
                person_name=person_name.title(),
                age=None,
                booking_number=source_record_id[:12],
                booking_at=None,
                charges_summary=charges,
                source_url=source_url,
            )
        )

    logger.info("Park County roster: parsed %d record(s) from %s", len(records), source_url)
    return records


# ---------------------------------------------------------------------------
# Beaverhead (PDF) — concrete adapter
# ---------------------------------------------------------------------------


# Each inmate block in the Beaverhead PDF starts with:
#   Book#: 26BK00095 Name: AMOS,NICHOLASC NameID: 21693 Rel.Dt: **/**/**
_BEAVERHEAD_INMATE_HEADER = re.compile(
    r"Book#\s*:\s*(?P<book>\S+)\s+"
    r"Name\s*:\s*(?P<name>[A-Z][A-Z\-' ]*,\s*[A-Z][A-Z\-' ]*?)"
    r"(?=\s+NameID|\s+Book#|\s+Statute#|\s+Inmate|$)",
    re.IGNORECASE,
)


def fetch_beaverhead_bookings(source_url: str) -> list[JailBookingRecord]:
    """Scrape the Beaverhead County inmate roster PDF.

    URL pattern: https://beaverheadcountymt.gov/wp-content/uploads/YYYY/MM/Jail-Roster.pdf

    The PDF is text-extractable (not scanned). Each inmate is a
    "Book#: <id> Name: <LAST,FIRST>" header followed by a list of
    charges / statute rows. Booking date is the "Date" column in the
    statute rows; we capture the earliest as ``booking_at``.
    """
    response = fetch_url(source_url)
    if not response.content[:5].startswith(b"%PDF-"):
        raise ValueError(
            f"Beaverhead roster URL did not return a PDF (got {response.headers.get('Content-Type','')[:40]})"
        )
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError("pdfplumber is required for the Beaverhead PDF adapter") from exc

    # pdfplumber needs a seekable file; response.raw is a stream that
    # doesn't support seek(). Read into a BytesIO and pass that.
    import io
    pdf_bytes = response.content
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        text = "\n".join((page.extract_text() or "") for page in pdf.pages)

    if not text.strip():
        logger.info("Beaverhead roster: PDF contained no extractable text")
        return []

    return _parse_beaverhead_text(text, source_url=source_url)


def _parse_beaverhead_text(text: str, *, source_url: str) -> list[JailBookingRecord]:
    """Parse the extracted text of the Beaverhead PDF.

    Splits the text on the ``Book#:`` headers, then takes the first
    "Date" column value in each inmate's chunk as the booking date.
    """
    # Normalize whitespace
    text = re.sub(r"\s+", " ", text)

    records: list[JailBookingRecord] = []
    seen: set[str] = set()

    # Use the regex to find each inmate block; spans are based on the
    # matches and the gap between them.
    matches = list(_BEAVERHEAD_INMATE_HEADER.finditer(text))
    for i, m in enumerate(matches):
        book = m.group("book").strip()
        raw_name = m.group("name").strip()
        # Normalize the name: "AMOS,NICHOLASC" -> "Nicholas C. Amos"
        person_name = _normalize_beaverhead_name(raw_name)
        if not person_name:
            continue

        chunk_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[m.end():chunk_end]

        # First date in the chunk, in MM/DD/YY format
        date_m = re.search(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b", chunk)
        booking_at = _normalize_beaverhead_date(date_m.group(1)) if date_m else None

        # Charges: statute descriptions between "Statute" and the next
        # statute line. Easier: keep first ~200 chars of the chunk as
        # the charges summary.
        charges_summary = chunk.strip()[:240].rstrip()
        if not charges_summary:
            charges_summary = "Beaverhead County inmate — see official roster for charges."

        source_record_id = hashlib.sha1(
            f"beaverhead:{book}:{person_name.lower()}".encode("utf-8")
        ).hexdigest()[:20]
        if source_record_id in seen:
            continue
        seen.add(source_record_id)

        records.append(
            JailBookingRecord(
                source_record_id=source_record_id,
                person_name=person_name,
                age=None,
                booking_number=book,
                booking_at=booking_at,
                charges_summary=charges_summary,
                source_url=source_url,
            )
        )

    logger.info("Beaverhead roster: parsed %d record(s) from %s", len(records), source_url)
    return records


def _normalize_beaverhead_name(raw: str) -> str:
    """Convert ``AMOS,NICHOLASC`` -> ``Nicholas C. Amos``.

    The PDF is space-stripped so the first name runs into the middle
    initial without a space. We try to detect that pattern: if the
    part after the comma is a single long all-caps word, we look for
    a likely split between first name and middle initial.
    """
    raw = re.sub(r"\s+", " ", raw).strip().rstrip(",")
    if "," not in raw:
        return raw.title()
    last, first = (p.strip() for p in raw.split(",", 1))
    if not first:
        return last.title()
    # Heuristic: "NICHOLASC" -> "NICHOLAS C" (trailing single uppercase letter)
    # Only triggers when the first name is long enough that the trailing
    # letter is plausibly a middle initial (>= 5 chars before it).
    m = re.match(r"^([A-Z][A-Z\-']{3,})([A-Z])$", first)
    if m:
        first = f"{m.group(1)} {m.group(2)}."
    return f"{first.title()} {last.title()}"


def _normalize_beaverhead_date(raw: str) -> str | None:
    """Convert ``03/09/26`` -> ``2026-03-09`` (ISO)."""
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$", raw)
    if not m:
        return None
    mm, dd, yy = m.groups()
    if len(yy) == 2:
        yy = f"20{yy}" if int(yy) < 80 else f"19{yy}"
    return f"{yy}-{int(mm):02d}-{int(dd):02d}"


# ---------------------------------------------------------------------------
# Common fetch helper
# ---------------------------------------------------------------------------


def fetch_url(
    source_url: str,
    *,
    timeout: int = 45,
    headers: dict[str, str] | None = None,
) -> requests.Response:
    """GET a URL with the project's standard headers.

    Raises ``requests.HTTPError`` on non-2xx so the caller can fall back
    or surface a clear error. Retries are the caller's responsibility.
    """
    session_headers = {
        "User-Agent": "Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://montanablotter.com)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if headers:
        session_headers.update(headers)
    response = requests.get(source_url, headers=session_headers, timeout=timeout, allow_redirects=True)
    response.raise_for_status()
    return response


__all__ = [
    "parse_html_tables",
    "find_roster_table",
    "index_by_header",
    "fetch_url",
    "fetch_park_bookings",
    "fetch_beaverhead_bookings",
]

