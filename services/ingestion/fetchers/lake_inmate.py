"""
lake_inmate.py
==============
Fetches the Lake County, MT jail roster PDF and parses it into
JailBookingRecords for the Montana Blotter jail-bookings pipeline.

The Lake County roster is a ReportLab-generated PDF with these columns:
    Last, First Middle Name | Jacket # | Age | Race | Sex | Days |
    Booking Date | Arr Agency | Charges / Hold Reasons

The charges column is on the far right (x ≥ ~469pt).  When a charge entry
is long, it wraps to the next visual row — which means the wrapped text can
appear directly below the *next* inmate's booking row in the raw word stream.
To handle this correctly the parser separates words into a left "booking"
stream (x < CHARGE_COL_X) and a right "charge" stream (x ≥ CHARGE_COL_X),
then assigns each charge fragment to the record whose booking header is at
least CHARGE_ASSIGN_MARGIN points above it.
"""

from __future__ import annotations

import io
import logging
import re
import sys
from datetime import datetime

import requests
import pdfplumber

sys.path.insert(0, "/root/montanablotter")
from services.ingestion.models import JailBookingRecord

logger = logging.getLogger(__name__)

ROSTER_URL = "https://www.lakemt.gov/DocumentCenter/View/816/Jail_Roster-?bidId="

# Words with x0 >= this threshold are in the charges column.
CHARGE_COL_X: float = 460.0

# A charge fragment at vertical position Y is assigned to the last booking
# row whose top <= Y - CHARGE_ASSIGN_MARGIN.  This ensures wrapped charge
# lines that appear just below the *next* person's header (typically ~2-6pt
# gap) still belong to the previous record.
CHARGE_ASSIGN_MARGIN: float = 8.0

# Row-merging tolerance for pdfplumber word extraction.
_WORD_Y_TOL: float = 3.0
_WORD_X_TOL: float = 3.0

# Matches the jacket number that terminates a name: e.g. "19-291" or "18-000129".
_JACKET_SPLIT_RE = re.compile(r"(\d{2}-\d+)")

# Date formats present in the PDF: MM/DD/YY (2-digit year).
_DATE_FORMATS = ("%m/%d/%y", "%m/%d/%Y", "%Y-%m-%d")


def _normalize_date(raw: str) -> str | None:
    val = (raw or "").strip()
    if not val:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(val, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return None


def _parse_booking_row(text: str) -> dict[str, str] | None:
    """
    Parse a left-column booking row into its fields.

    Expected shape (whitespace-collapsed):
        LAST, FIRST [MIDDLE] JACKET# AGE RACE SEX DAYS MM/DD/YY AGENCY
    The name may run directly into the jacket# without a space.
    """
    parts = _JACKET_SPLIT_RE.split(text, maxsplit=1)
    if len(parts) < 3:
        return None

    name_raw = parts[0].strip().rstrip(" ,")
    jacket = parts[1].strip()
    remainder = parts[2].strip()

    rest_m = re.match(
        r"^(\d{1,3})\s+(\w+)\s+(Male|Female)\s+(\d+)\s+(\d{2}/\d{2}/\d{2})\s+(\w+)\s*$",
        remainder,
        re.IGNORECASE,
    )
    if not rest_m:
        return None

    age_str, race, sex, days_str, booking_date_raw, agency = rest_m.groups()

    if "," in name_raw:
        last, first = name_raw.split(",", 1)
        person_name = f"{last.strip().title()}, {first.strip().title()}"
    else:
        person_name = name_raw.title()

    return {
        "person_name": person_name,
        "jacket": jacket,
        "age": age_str,
        "race": race,
        "sex": sex,
        "days": days_str,
        "booking_date": booking_date_raw,
        "agency": agency,
    }


# Statute codes start with digits (e.g. "45-5-102", "61-8-1002", "46-6-212").
_STATUTE_START_RE = re.compile(r"^\d{2,}-")


def _join_charge_parts(parts: list[str]) -> str:
    """
    Join charge fragments, collapsing mid-sentence PDF line-wrap continuations.

    Fragments that do NOT start with a statute code (e.g. "Revoked 1st Offense"
    or "Causing Bodily Injury") are continuations of the previous fragment and
    are appended with a space rather than a semicolon separator.
    """
    merged: list[str] = []
    for part in parts[:8]:
        if merged and not _STATUTE_START_RE.match(part):
            merged[-1] = merged[-1].rstrip() + " " + part
        else:
            merged.append(part)
    return "; ".join(merged[:6])


def _parse_page(page) -> tuple[list[tuple[float, dict]], list[tuple[float, str]]]:
    """
    Extract booking rows and charge fragments from a single PDF page.

    Returns (booking_rows, charge_fragments), each a list of (top, data) pairs
    sorted by ascending top (vertical position on this page).

    Processing per page is essential: pdfplumber resets the 'top' coordinate
    to zero at the start of each page, so accumulating rows across pages causes
    top-value collisions that break the charge-assignment logic.
    """
    words = page.extract_words(
        x_tolerance=_WORD_X_TOL,
        y_tolerance=_WORD_Y_TOL,
        keep_blank_chars=False,
    )

    # Cluster words into rows by 'top' position.
    rows: dict[float, list[dict]] = {}
    for w in words:
        top = w["top"]
        matched = next((k for k in rows if abs(k - top) <= _WORD_Y_TOL), None)
        key = matched if matched is not None else top
        rows.setdefault(key, []).append(w)

    booking_rows: list[tuple[float, dict]] = []
    charge_fragments: list[tuple[float, str]] = []

    for row_top in sorted(rows):
        row_words = sorted(rows[row_top], key=lambda w: w["x0"])

        left_text = " ".join(
            w["text"] for w in row_words if w["x0"] < CHARGE_COL_X
        ).strip()
        right_text = " ".join(
            w["text"] for w in row_words if w["x0"] >= CHARGE_COL_X
        ).strip()

        # Skip page-level header/footer rows entirely (both columns).
        # "Roster" anchors the title row whose date portion falls in the right
        # column; "Page \d" catches both left-aligned and right-column footers.
        if re.match(
            r"(Roster$|Last,|Total Records|Page \d)",
            left_text,
            re.IGNORECASE,
        ):
            continue
        if re.match(r"Page \d", right_text, re.IGNORECASE):
            continue

        if left_text:
            parsed = _parse_booking_row(left_text)
            if parsed:
                booking_rows.append((row_top, parsed))

        if right_text:
            charge_fragments.append((row_top, right_text))

    return booking_rows, charge_fragments


def _assign_charges(
    booking_rows: list[tuple[float, dict]],
    charge_fragments: list[tuple[float, str]],
) -> dict[int, list[str]]:
    """
    Assign each charge fragment to the correct booking record.

    Rule: fragment at vertical position Y belongs to the last booking row
    whose top <= Y - CHARGE_ASSIGN_MARGIN.  This margin (8pt ≈ one line
    height) absorbs the common case where PDF charge-text wrapping places
    a continuation line just below the *next* inmate's header row.
    """
    booking_tops = [top for top, _ in booking_rows]
    charges_by_idx: dict[int, list[str]] = {i: [] for i in range(len(booking_rows))}

    for frag_top, frag_text in charge_fragments:
        threshold = frag_top - CHARGE_ASSIGN_MARGIN
        target_idx = None
        for i, btop in enumerate(booking_tops):
            if btop <= threshold:
                target_idx = i
        if target_idx is not None:
            charges_by_idx[target_idx].append(frag_text)

    return charges_by_idx


def _parse_pdf_bytes(pdf_bytes: bytes, source_url: str) -> list[JailBookingRecord]:
    records: list[JailBookingRecord] = []
    seen_ids: set[str] = set()

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            booking_rows, charge_fragments = _parse_page(page)
            charges_by_idx = _assign_charges(booking_rows, charge_fragments)

            for i, (_, fields) in enumerate(booking_rows):
                charge_parts = charges_by_idx.get(i, [])
                charges_summary = (
                    _join_charge_parts(charge_parts)
                    if charge_parts
                    else "Charge details available on the official Lake County jail roster."
                )

                jacket = fields["jacket"]
                source_record_id = f"lake:{jacket}"
                if source_record_id in seen_ids:
                    counter = 1
                    while f"{source_record_id}:{counter}" in seen_ids:
                        counter += 1
                    source_record_id = f"{source_record_id}:{counter}"
                seen_ids.add(source_record_id)

                age_val = fields["age"]
                records.append(
                    JailBookingRecord(
                        source_record_id=source_record_id,
                        person_name=fields["person_name"],
                        age=int(age_val) if age_val.isdigit() else None,
                        booking_number=jacket,
                        booking_at=_normalize_date(fields["booking_date"]),
                        charges_summary=charges_summary,
                        source_url=source_url,
                    )
                )

    logger.info("Parsed %d Lake County booking records from PDF", len(records))
    return records


def fetch_lake_bookings(source_url: str | None = None) -> list[JailBookingRecord]:
    """Download and parse the Lake County jail roster PDF."""
    url = source_url or ROSTER_URL
    response = requests.get(
        url,
        timeout=60,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://montanablotter.com)",
            "Accept": "application/pdf,*/*",
        },
    )
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "").lower()
    if "text/html" in content_type or response.content[:5] == b"<html":
        from services.ingestion.jail_bookings import SourceTemporarilyUnavailable
        raise SourceTemporarilyUnavailable(
            f"Lake County roster URL returned HTML instead of PDF — page may have moved: {url}"
        )
    if len(response.content) < 512:
        from services.ingestion.jail_bookings import SourceTemporarilyUnavailable
        raise SourceTemporarilyUnavailable(
            f"Lake County roster PDF is unexpectedly small ({len(response.content)} bytes)."
        )

    return _parse_pdf_bytes(response.content, url)
