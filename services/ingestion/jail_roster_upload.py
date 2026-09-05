"""
jail_roster_upload.py — Upload a jail roster PDF and sync bookings into
the Montana Blotter jail_bookings table.

Parses a jail roster PDF (plain-text or scanned), converts each detected
inmate row into a JailBookingRecord, and upserts them into the jail_bookings
table under a user-selected county source.  Removed/released bookings are
automatically marked as released so the roster stays in sync.

Usage:
    from services.ingestion.jail_roster_upload import upload_jail_roster_pdf
    stats = upload_jail_roster_pdf('/path/to/roster.pdf', county_slug='hill')
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
from dataclasses import replace
from datetime import datetime, timezone

import pdfplumber

from services.ingestion.models import JailBookingRecord
from db import connect_db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PDF parsing helpers
# ---------------------------------------------------------------------------

_NAME_HEADER_TOKENS = (
    "inmate", "name", "booking", "arrestee", "defendant", "suspect",
    "offender", "person", "facility", "incident", "charge", "offense",
    "bond", "arrest", "date", "time", "county", "city", "agency",
)


def _looks_like_name_header(cells):
    joined = " ".join((str(c) for c in cells if c)).lower()
    if not joined:
        return False
    if re.search(r"\d", joined):
        return False
    return sum(1 for tok in _NAME_HEADER_TOKENS if tok in joined) >= 2


def _looks_like_name_line(line):
    """Return True if *line* looks like a person name (LAST, FIRST / FIRST LAST)."""
    line = line.strip()
    if not line or len(line) > 80:
        return False
    if re.match(r"^[A-Z][A-Z'\-]+,\s*[A-Z][A-Z'\-]+", line):
        return True
    if re.match(r"^[A-Z][A-Z'\-]+\s+[A-Z][A-Z'\-]+", line):
        return True
    return False


def _parse_date(value):
    """Try to parse a date string into ``YYYY-MM-DD HH:MM:SS``."""
    value = (value or "").strip()
    if not value:
        return None
    formats = (
        "%m/%d/%Y %H:%M", "%m/%d/%Y %I:%M %p", "%m/%d/%Y %I:%M%p",
        "%m/%d/%Y", "%m/%d/%y %H:%M", "%m-%d-%Y %H:%M",
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
    )
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return None


def _extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extract plain text from a PDF (pdfplumber, no OCR)."""
    pages = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            txt = page.extract_text()
            if txt:
                pages.append(txt)
    return "\n".join(pages)


def _extract_tables_from_pdf(pdf_bytes: bytes):
    """Extract tables from every PDF page via pdfplumber."""
    tables = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            for tbl in page.extract_tables() or []:
                tables.append(tbl)
    return tables


# ---------------------------------------------------------------------------
# Record builders
# ---------------------------------------------------------------------------

def _build_record_from_table_row(cells, source_url=""):
    """Try to turn a table row into a JailBookingRecord."""
    cells = [str(c).strip() if c else "" for c in cells]
    cells = [c for c in cells if c]
    if len(cells) < 2:
        return None
    if _looks_like_name_header(cells):
        return None

    name = ""
    name_idx = -1
    for idx, cell in enumerate(cells):
        if not re.search(r"[A-Za-z]{2,}", cell):
            continue
        if _parse_date(cell):
            continue
        candidate = cell.title()
        if candidate:
            name = candidate
            name_idx = idx
            break
    if not name:
        return None

    booking_at = None
    for cell in cells:
        booking_at = _parse_date(cell)
        if booking_at:
            break

    charges_value = ""
    bond_value = ""
    bond_idx = -1
    for idx, cell in enumerate(cells):
        if idx == name_idx:
            continue
        if re.match(r"(?i)\bbond[:\s]+\$?([\d,]+(?:\.\d{2})?)\s*([a-z]+)?", cell.strip()):
            bond_value = cell
            bond_idx = idx
            break
    if bond_match := re.match(r"(?i)\bbond[:\s]+\$?([\d,]+(?:\.\d{2})?)\s*([a-z]+)?", bond_value):
        bond_value = f"${bond_match.group(1)} {bond_match.group(2) or ''}".strip()
    for idx, cell in enumerate(cells):
        if idx == name_idx or idx == bond_idx:
            continue
        if booking_at and _parse_date(cell):
            continue
        if len(cell) > len(charges_value):
            charges_value = cell

    charges = re.sub(r"\s+", " ", charges_value).strip(" ;,") or "Charge details pending from official roster."
    if bond_value:
        charges = f"{charges}; {bond_value}" if charges else f"Bond {bond_value}"

    source_record_id = f"upload:{name.lower().replace(' ', '-')}"
    return JailBookingRecord(
        source_record_id=source_record_id,
        person_name=name,
        age=None,
        booking_number="",
        booking_at=booking_at,
        charges_summary=charges,
        source_url=source_url,
    )


def _parse_roster_text(text: str, source_url: str = "") -> list[JailBookingRecord]:
    """Parse free-form roster text into JailBookingRecord objects."""
    records: list[JailBookingRecord] = []
    seen_ids: set[str] = set()

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if not _looks_like_name_line(line):
            continue

        # Attempt to split name from rest
        match = re.match(
            r"^([A-Z][A-Z'\-]+,\s*[A-Z][A-Z'\-]+)\s*[,\|\-]\s*(.*)$",
            line,
        )
        if not match:
            match = re.match(
                r"^([A-Z][A-Z'\-]+\s+[A-Z][A-Z'\-]+)\s+(.+)$",
                line,
            )
        if not match:
            continue

        name = match.group(1).strip()
        rest = match.group(2).strip()
        parts = [p.strip() for p in re.split(r"[,]|[ \-][ \t]*", rest) if p.strip()]

        record = _build_record_from_table_row([name] + parts, source_url=source_url)
        if record:
            rid = record.source_record_id
            if rid in seen_ids:
                counter = 1
                while f"{rid}:{counter}" in seen_ids:
                    counter += 1
                record = replace(record, source_record_id=f"{rid}:{counter}")
            seen_ids.add(record.source_record_id)
            records.append(record)

    return records


def _parse_roster_pdf(pdf_bytes: bytes, source_url: str = "") -> list[JailBookingRecord]:
    """Parse a jail roster PDF into JailBookingRecord objects."""
    records: list[JailBookingRecord] = []
    seen_ids: set[str] = set()

    # Strategy 1: table extraction
    tables = _extract_tables_from_pdf(pdf_bytes)
    for table in tables:
        for row in table:
            rec = _build_record_from_table_row(row, source_url=source_url)
            if rec:
                rid = rec.source_record_id
                if rid in seen_ids:
                    counter = 1
                    while f"{rid}:{counter}" in seen_ids:
                        counter += 1
                    rec = replace(rec, source_record_id=f"{rid}:{counter}")
                seen_ids.add(rec.source_record_id)
                records.append(rec)

    # Strategy 2: text extraction if tables yielded nothing
    if not records:
        text = _extract_text_from_pdf(pdf_bytes)
        records = _parse_roster_text(text, source_url=source_url)

    return records


# ---------------------------------------------------------------------------
# DB sync
# ---------------------------------------------------------------------------

def _build_booking_payload(source, record):
    """Mirror jail_bookings._build_booking_payload for uploaded records."""
    payload = {
        "county_slug": source["county_slug"],
        "county_name": source["county_name"],
        "facility_name": source["facility_name"],
        "person_name": record.person_name,
        "booking_number": record.booking_number,
        "booking_at": record.booking_at,
        "charges_summary": record.charges_summary,
        "source_url": record.source_url or source["roster_url"],
        "source_record_id": record.source_record_id,
    }
    hash_id = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    raw_json = json.dumps(
        {
            "source_record_id": record.source_record_id,
            "person_name": record.person_name,
            "age": record.age,
            "booking_number": record.booking_number,
            "booking_at": record.booking_at,
            "charges_summary": record.charges_summary,
            "source_url": record.source_url,
        },
        ensure_ascii=False,
    )
    return hash_id, raw_json


def _sync_uploaded_records(conn, source, records):
    """Upsert uploaded records into jail_bookings, marking stale entries released."""
    now_sql = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    stats = {"fetched_count": len(records), "new_count": 0, "updated_count": 0, "missing_count": 0}

    existing_rows = conn.execute(
        "SELECT id, source_record_id, person_name, booking_at, charges_summary, is_current, hash_id, raw_json "
        "FROM jail_bookings WHERE source_id = ?",
        (source["id"],),
    ).fetchall()
    existing_by_key = {row["source_record_id"]: row for row in existing_rows if row["source_record_id"]}

    for record in records:
        current = existing_by_key.get(record.source_record_id)
        hash_id, raw_json = _build_booking_payload(source, record)

        if current is None:
            stats["new_count"] += 1
            name_slug = re.sub(r"[^a-z0-9-]", "", record.person_name.lower().replace(" ", "-").replace(".", "").replace("'", "").replace(",", ""))
            conn.execute(
                "INSERT INTO jail_bookings (source_id, county_slug, county_name, facility_name, person_name, age, "
                "booking_number, booking_at, charges_summary, source_url, source_record_id, hash_id, raw_json, "
                "name_slug, booking_status, is_current, first_seen_at, last_seen_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'current', 1, datetime('now'), datetime('now'), datetime('now'), datetime('now'))",
                (source["id"], source["county_slug"], source["county_name"], source["facility_name"],
                 record.person_name, record.age, record.booking_number, record.booking_at,
                 record.charges_summary, record.source_url, record.source_record_id,
                 hash_id, raw_json, name_slug),
            )
        else:
            changed = (
                (current["person_name"] or "") != record.person_name
                or (current["booking_at"] or "") != (record.booking_at or "")
                or (current["charges_summary"] or "") != record.charges_summary
                or (current["hash_id"] or "") != hash_id
                or int(current["is_current"] or 0) != 1
            )
            if changed:
                stats["updated_count"] += 1
                name_slug = re.sub(r"[^a-z0-9-]", "", record.person_name.lower().replace(" ", "-").replace(".", "").replace("'", "").replace(",", ""))
                conn.execute(
                    "UPDATE jail_bookings SET person_name=?, age=?, booking_number=?, booking_at=?, charges_summary=?, "
                    "source_url=?, hash_id=?, raw_json=?, name_slug=?, booking_status='current', is_current=1, "
                    "release_at=NULL, last_seen_at=?, updated_at=? WHERE id=?",
                    (record.person_name, record.age, record.booking_number, record.booking_at,
                     record.charges_summary, record.source_url, hash_id, raw_json, name_slug,
                     now_sql, now_sql, current["id"]),
                )
            else:
                conn.execute(
                    "UPDATE jail_bookings SET last_seen_at=? WHERE id=?",
                    (now_sql, current["id"]),
                )

    # Mark any rows no longer in the roster as released
    for row in existing_rows:
        if row["source_record_id"] in {r.source_record_id for r in records}:
            continue
        if int(row["is_current"] or 0) == 0:
            continue
        stats["missing_count"] += 1
        conn.execute(
            "UPDATE jail_bookings SET is_current=0, booking_status='released', release_at=COALESCE(release_at, ?), "
            "last_seen_at=?, updated_at=? WHERE id=?",
            (now_sql, now_sql, now_sql, row["id"]),
        )

    conn.commit()
    return stats


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

def upload_jail_roster_pdf(pdf_path: str, county_slug: str) -> dict:
    """Parse a jail roster PDF and sync bookings into the county's jail_bookings table.

    Args:
        pdf_path: Absolute path to the uploaded PDF file.
        county_slug: Montana county slug (e.g. 'hill', 'cascade').

    Returns:
        dict with fetched_count, new_count, updated_count, missing_count, and errors.
    """
    from db import connect_db

    conn = connect_db()
    try:
        # Resolve source
        source = conn.execute(
            "SELECT * FROM jail_booking_sources WHERE county_slug = ? AND COALESCE(is_enabled, 1) = 1",
            (county_slug,),
        ).fetchone()
        if not source:
            return {
                "fetched_count": 0, "new_count": 0, "updated_count": 0, "missing_count": 0,
                "errors": [f"No enabled jail_booking_sources row for county slug '{county_slug}'."],
            }

        # Read and parse the PDF
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()
        if len(pdf_bytes) < 8 or pdf_bytes[:4] != b"%PDF":
            return {"fetched_count": 0, "new_count": 0, "updated_count": 0, "missing_count": 0,
                    "errors": ["File is not a valid PDF."]}

        records = _parse_roster_pdf(pdf_bytes, source_url=(source["roster_url"] if source["roster_url"] else ""))
        if not records:
            return {
                "fetched_count": 0, "new_count": 0, "updated_count": 0, "missing_count": 0,
                "errors": ["No booking records detected in PDF."],
            }

        stats = _sync_uploaded_records(conn, source, records)
        stats["errors"] = []
        return stats
    finally:
        conn.close()