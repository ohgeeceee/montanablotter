"""
lake_inmate.py
==============
Fetches the Lake County, MT jail roster PDF and parses it into
JailBookingRecords for the Montana Blotter jail-bookings pipeline.

The Lake County roster is a ReportLab-generated PDF with these columns:
    Last, First Middle Name | Jacket # | Age | Race | Sex | Days |
    Booking Date | Arr Agency | Charges / Hold Reasons

The charges column is on the far right (x ≥ ~469pt).  ReportLab vertically
centers each inmate's multi-line charge block against their booking row, so
the *last* line of a block is baseline-aligned with the header row and any
earlier lines sit *above* it — potentially above the previous inmate's
header.  Assigning fragments to "the last header above them" therefore
shifts wrapped first-lines onto the previous inmate (this mis-attribution
was reported for Lake bookings on 2026-09-21).

Correct rule: the parser separates words into a left "booking" stream
(x < CHARGE_COL_X) and a right "charge" stream (x ≥ CHARGE_COL_X), then
assigns each charge fragment to the *first* booking header whose bottom
edge reaches down to the fragment's vertical position (baseline-anchored).
Fragments below the last header on a page (page-tail wrapped lines) are
assigned to that last header.
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

# Charge blocks are vertically centered on their header row, so a block's
# last line shares the header's baseline and earlier lines sit above it.
# A fragment at vertical position Y belongs to the first booking row whose
# bottom edge reaches Y - _BASELINE_TOL (small tolerance for glyph ascent
# differences between the two columns).
_BASELINE_TOL: float = 2.5

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

        left_words = [w for w in row_words if w["x0"] < CHARGE_COL_X]
        right_words = [w for w in row_words if w["x0"] >= CHARGE_COL_X]
        left_text = " ".join(w["text"] for w in left_words).strip()
        right_text = " ".join(w["text"] for w in right_words).strip()

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
                left_bottom = max(
                    (w["bottom"] for w in left_words), default=row_top
                )
                booking_rows.append((row_top, left_bottom, parsed))

        if right_text:
            charge_fragments.append((row_top, right_text))

    return booking_rows, charge_fragments


def _assign_charges(
    booking_rows: list[tuple[float, float, dict]],
    charge_fragments: list[tuple[float, str]],
    carry_in: list[str] | None = None,
) -> tuple[dict[int, list[str]], list[str]]:
    """
    Assign each charge fragment to the correct booking record.

    Rule: fragment at vertical position Y belongs to the *first* booking row
    whose bottom edge reaches down to it (bottom >= Y - _BASELINE_TOL).
    Fragments below the last header's bottom edge on the page are page-tail
    lines: ReportLab split the last inmate's charge block across the page
    break, so they are returned as ``carry_out`` for the next page's first
    header (or dropped on the final page).  ``carry_in`` holds the previous
    page's tail fragments and is attached to this page's first header.

    Rationale: ReportLab vertically centers each inmate's multi-line charge
    block against their booking row, so the block's *last* line shares the
    header's baseline and earlier lines sit *above* it.  The previous rule
    ("last header above the fragment") shifted first-lines of wrapped blocks
    onto the *previous* inmate, mis-attributing charges (reported 2026-09-21).
    """
    charges_by_idx: dict[int, list[str]] = {i: [] for i in range(len(booking_rows))}
    carry_out: list[str] = []

    if carry_in and booking_rows:
        charges_by_idx[0].extend(carry_in)

    # Target of the most recent statute-initial fragment: 'row' -> index,
    # 'carry' -> page-tail list.  Wrapped continuation lines follow their
    # parent line's target even if they drift across the anchor boundary
    # (e.g. "in Subsection 45-9-102(1) or (2)" wrapping under the previous
    # inmate's "45-9-102[Fel] - Criminal Possession" line).
    prev: tuple[str, int] | None = ("row", 0) if (carry_in and booking_rows) else None

    for frag_top, frag_text in charge_fragments:
        statute_like = bool(_STATUTE_START_RE.match(frag_text))
        if not statute_like and prev is not None:
            if prev[0] == "row":
                charges_by_idx[prev[1]].append(frag_text)
            else:
                carry_out.append(frag_text)
            continue
        target_idx = None
        for i, (_btop, bbottom, _fields) in enumerate(booking_rows):
            if bbottom >= frag_top - _BASELINE_TOL:
                target_idx = i
                break
        if target_idx is None:
            # Page-tail fragment.  If it continues a wrapped charge line
            # (does not start with a statute code) it belongs to the last
            # header on this page; if it starts a NEW charge block it belongs
            # to the first header on the next page (ReportLab split the
            # block across the page break).
            if booking_rows and not statute_like:
                charges_by_idx[len(booking_rows) - 1].append(frag_text)
                prev = ("row", len(booking_rows) - 1)
            else:
                carry_out.append(frag_text)
                prev = ("carry", -1)
        else:
            charges_by_idx[target_idx].append(frag_text)
            prev = ("row", target_idx)

    return charges_by_idx, carry_out


def _parse_pdf_bytes(pdf_bytes: bytes, source_url: str) -> list[JailBookingRecord]:
    records: list[JailBookingRecord] = []
    seen_ids: set[str] = set()
    carry: list[str] = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            booking_rows, charge_fragments = _parse_page(page)
            charges_by_idx, carry = _assign_charges(booking_rows, charge_fragments, carry_in=carry)

            for i, (_, _bbottom, fields) in enumerate(booking_rows):
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
