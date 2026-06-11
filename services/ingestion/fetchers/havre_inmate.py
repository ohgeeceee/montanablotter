#!/usr/bin/env python3
"""
havre_inmate.py
===============

Parse the daily jail roster DOCX emailed by the Hill County Sheriff's
Office (HCSO) and upsert the rows into the Montana Blotter `jail_bookings`
table.

HCSO sends the daily roster as a Microsoft Word document (.docx). The file
is a ZIP of XML; the body in ``word/document.xml`` is a sequence of
``<w:p>`` paragraphs and ``<w:tbl>`` tables. Each inmate is typically
either a single paragraph (free-form text) or a row in a table — this
parser handles both shapes.

Note: HCSO re-uses the same filename for every daily roster (their DOCX
template is named ``JAILROSTER - 12-24-25.docx``), so callers MUST pass
``roster_date`` (the email's Date header) to scope source_record_ids to
the actual day. Without it, every re-ingest silently no-ops on dedup.

Two public entrypoints:

  fetch_havre_bookings(docx_path) -> list[JailBookingRecord]
      Pure parser: read the .docx, return records. No DB I/O.

  ingest_havre_roster(docx_path) -> SyncStats
      Full pipeline: parse + upsert + record run + mark source checked.
      This is what the email worker calls; it is also the CLI target.

The first time this runs against a real HCSO email, the row-extraction
heuristics in ``_row_to_record`` may need tuning — that's the extension
point. Everything else (conn, sync, dedup, run recording) reuses the
established jail_bookings orchestrator and is stable.
"""

from __future__ import annotations

import argparse
import logging
import re
import sqlite3
import sys
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Iterable

sys.path.insert(0, "/root/montanablotter")

from services.ingestion.jail_bookings import (  # noqa: E402
    SyncStats,
    _connect_db,
    _ensure_tracked_sources,
    _mark_source_checked,
    _record_run,
    _sync_records,
)
from services.ingestion.models import JailBookingRecord  # noqa: E402

logger = logging.getLogger(__name__)

# HCSO does not publish a public online roster; data only arrives via the
# daily email DOCX (sender is moxley@hillso.org or reichl@hillso.org).
# The matching jail_booking_sources row (seeded in
# services.ingestion.jail_bookings._ensure_tracked_sources) records the
# facility's public-facing info for SEO/disclosure; the per-booking
# source_url points at the uploaded DOCX itself.
HCSO_FACILITY_NAME = "Hill County Detention Center"
HCSO_COUNTY_SLUG = "hill"
HCSO_COUNTY_NAME = "Hill"
HCSO_PHONE = "406-265-5481"
# Kept as aliases for the slug (the only one this module actually uses)
# so the import in ingest_havre_roster stays readable.
HPD_COUNTY_SLUG = HCSO_COUNTY_SLUG  # legacy alias

# WordprocessingML XML namespace. Every tag in word/document.xml uses this.
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"  # Clark notation prefix for ElementTree tag matching

_DOCX_PARSE_ERROR = (
    "Could not read {path} as a .docx — file is missing word/document.xml "
    "or is not a valid Office Open XML document."
)


# ---------------------------------------------------------------------------
# Date normalization
# ---------------------------------------------------------------------------

_DATE_FORMATS = (
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y %I:%M %p",
    "%m/%d/%Y %I:%M%p",
    "%m/%d/%Y",
    "%m/%d/%y %H:%M",
    "%m-%d-%Y %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
)


def _normalize_booking_datetime(value: str | None) -> str | None:
    """Coerce a wide range of human date formats into the canonical
    ``YYYY-MM-DD HH:MM:SS`` string the rest of the pipeline expects.
    Returns ``None`` if the value is empty or unparseable.
    """
    raw = (value or "").strip()
    if not raw:
        return None
    raw = re.sub(r"\s+([A-Z]{2,4}|UTC|GMT)$", "", raw)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Person-name normalization
# ---------------------------------------------------------------------------

_NAME_GARBAGE = re.compile(r"(?i)\b(arrestee|inmate|defendant|#|no\.?)\b")


def _normalize_person_name(value: str | None) -> str:
    """Title-case a roster name, collapsing whitespace and stripping common
    docx table headers (e.g. "Inmate Name"). Returns an empty string if the
    value doesn't look like a name.
    """
    if not value:
        return ""
    text = _NAME_GARBAGE.sub(" ", str(value))
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    if "," in text:
        last, first = [part.strip() for part in text.split(",", 1)]
        return f"{last.title()}, {first.title()}"
    return text.title()


def _person_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


# ---------------------------------------------------------------------------
# Charge normalization
# ---------------------------------------------------------------------------

_BOND_PREFIX = re.compile(r"(?i)\bbond[:\s]+\$?([\d,]+(?:\.\d{2})?)\s*([a-z]+)?")
_NO_BOND = re.compile(r"(?i)\bno\s+bond\b")


def _normalize_charges(value: str | None, bond: str | None = None) -> str:
    """Clean up a charges cell, optionally appending a bond line. Empty
    cells fall through to a placeholder so the row still gets inserted.
    """
    charges = (value or "").replace("\n", "; ")
    charges = re.sub(r"\s+", " ", charges).strip(" ;,")
    bond_text = ""
    if bond:
        bond = bond.strip()
        if bond and not _NO_BOND.search(bond):
            bond_text = f"Bond {bond}"
    if charges and bond_text:
        return f"{charges}; {bond_text}"
    if bond_text:
        return bond_text
    return charges or "Charge details pending from official HCSO roster."


def _coerce_age(value) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    digits = re.findall(r"\d+", text)
    if not digits:
        return None
    try:
        age = int(digits[0])
        return age if 0 < age < 130 else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Row extraction
# ---------------------------------------------------------------------------

_HEADER_TOKENS = (
    "inmate",
    "booking",
    "name",
    "age",
    "dob",
    "charge",
    "offense",
    "bond",
    "arrest",
    "date",
    "time",
    "hpd",
    "havre",
)


def _looks_like_header(cells: Iterable[str | None]) -> bool:
    joined = " ".join((str(c) for c in cells if c)).lower()
    if not joined:
        return False
    # A real column header has no numeric/date content in any cell —
    # charge text like "Bond: $5,000" or "1st offense" would otherwise
    # trip the token-count heuristic below.
    if re.search(r"\d", joined):
        return False
    return sum(1 for tok in _HEADER_TOKENS if tok in joined) >= 2


def _row_to_record(
    cells_in: list[str | None],
    *,
    source_path: Path,
    row_idx: int,
    roster_date: str | None = None,
) -> JailBookingRecord | None:
    """Convert one parsed row (list of cell strings) into a JailBookingRecord.

    Works on both table rows and free-form paragraph text that's been
    split on commas/tabs. The heuristics are intentionally conservative:
    skip rows we can't make sense of rather than guess wrong.
    """
    cells = [str(c).strip() if c is not None else "" for c in cells_in]
    cells = [c for c in cells if c != ""]
    if len(cells) < 2:
        return None
    if _looks_like_header(cells):
        return None

    # Person name: prefer the first cell that contains a letter and not
    # just a date.
    name = ""
    name_idx = -1
    for idx, cell in enumerate(cells):
        if not re.search(r"[A-Za-z]{2,}", cell):
            continue
        if _normalize_booking_datetime(cell):
            continue
        candidate = _normalize_person_name(cell)
        if candidate:
            name = candidate
            name_idx = idx
            break
    if not name:
        return None

    booking_at: str | None = None
    for cell in cells:
        booking_at = _normalize_booking_datetime(cell)
        if booking_at:
            break

    age: int | None = None
    for cell in cells:
        candidate = _coerce_age(cell)
        if candidate and candidate > 5:
            age = candidate
            break

    charges_value = ""
    bond_value = ""
    bond_idx = -1
    # Pass 1: identify the bond cell. Bond cells are recognized by the
    # "Bond: $X" prefix (case-insensitive) — this is a more reliable
    # signal than relative cell length, which breaks when a separate
    # bond column is wider than the charges column.
    for idx, cell in enumerate(cells):
        if idx == name_idx:
            continue
        if _BOND_PREFIX.match(cell.strip()):
            bond_value = cell
            bond_idx = idx
            break
    if bond_match := _BOND_PREFIX.search(bond_value):
        bond_value = f"${bond_match.group(1)} {bond_match.group(2) or ''}".strip()
    # Pass 2: charges is the longest remaining cell (skipping name, date,
    # age, and the bond cell we already identified).
    for idx, cell in enumerate(cells):
        if idx == name_idx or idx == bond_idx:
            continue
        if booking_at and _normalize_booking_datetime(cell):
            continue
        if age is not None and str(age) in cell:
            continue
        if len(cell) > len(charges_value):
            charges_value = cell
    charges = _normalize_charges(charges_value, bond=bond_value)

    # HCSO always sends the daily DOCX with the same filename
    # ("JAILROSTER - 12-24-25.docx" — they re-use a template) but the
    # *content* changes every day. If we keyed source_record_id off the
    # filename stem, every roster would share the same prefix and dedup
    # would silently no-op on re-ingest. Pass the email's Date header as
    # ``roster_date`` (YYYY-MM-DD) so each day gets a distinct prefix.
    if roster_date:
        source_record_id = (
            f"havre-docx:{roster_date}:r{row_idx}:{_person_slug(name)}"
        )
    else:
        source_record_id = (
            f"havre-docx:{source_path.stem}:r{row_idx}:{_person_slug(name)}"
        )
    # source_url is intentionally None for havre rows: the daily DOCX is a
    # full unredacted roster (names, ages, charges, bond amounts, warrant
    # details) and we don't expose it for anonymous download from /uploads/.
    # The DOCX is still archived in uploads/ for operator reference; the
    # jail-bookings page shows the rendered data (which is the point of the
    # site) but no longer surfaces a "Open Official Source" button. This
    # matches the pattern used for counties that link to their own published
    # roster — we just don't have an external link to point at.
    return JailBookingRecord(
        source_record_id=source_record_id,
        person_name=name,
        age=age,
        booking_number="",
        booking_at=booking_at,
        charges_summary=charges,
        source_url=None,
    )


# ---------------------------------------------------------------------------
# Paragraph parsing (free-form roster lines)
# ---------------------------------------------------------------------------

# Match "LASTNAME, FIRSTNAME" at the start of a paragraph. The name
# is allowed to span 1-3 words on each side of the comma; the name field
# is followed by enough whitespace + a delimiter to disambiguate from
# other comma-separated fields like "DOE, JOHN, 35, ..."
_PARA_NAME_PATTERN = re.compile(
    r"""
    ^\s*
    (?P<last>[A-Z][A-Za-z'\-\.]+(?:\s+[A-Z][A-Za-z'\-\.]+){0,2})
    ,\s*
    (?P<first>[A-Z][A-Za-z'\-\.]+(?:\s+[A-Z][A-Za-z'\-\.]+){0,2})
    \s*[\-,\t|]\s*
    (?P<rest>.+)$
    """,
    re.VERBOSE,
)


def _parse_paragraph_record(
    text: str, *, source_path: Path, row_idx: int, roster_date: str | None = None
) -> JailBookingRecord | None:
    """Parse a free-form paragraph that contains one inmate's info.

    Used for docx rosters that put one inmate per paragraph with
    delimiter-separated fields, e.g.::

        DOE, JOHN, 35, 1/15/2026 14:30, DUI - 1st offense
        SMITH, JANE - 28 - 3/20/2026 09:15 - Disorderly Conduct

    Strategy: find the leading "LASTNAME, FIRSTNAME" pattern, then pass
    the name plus the rest of the line to ``_row_to_record`` for the
    field-by-field interpretation.
    """
    match = _PARA_NAME_PATTERN.match(text)
    if not match:
        return None
    name = f"{match.group('last').strip()}, {match.group('first').strip()}"
    rest = match.group("rest").strip()
    # Split the rest on commas, tabs, pipes, or " - " so the row
    # parser sees discrete cells.
    parts = [p.strip() for p in re.split(r"[,]|\s+\-\s+|\s+\|\s+|\t", rest) if p.strip()]
    return _row_to_record(
        [name] + parts, source_path=source_path, row_idx=row_idx, roster_date=roster_date
    )


# ---------------------------------------------------------------------------
# docx extraction
# ---------------------------------------------------------------------------


def _read_docx_xml(docx_path: Path) -> ET.Element:
    """Open a .docx as a ZIP and return the parsed ``w:body`` element.

    Raises a clear error if the file isn't a valid Office Open XML doc
    (missing word/document.xml, not a ZIP, etc.).
    """
    try:
        with zipfile.ZipFile(docx_path) as z:
            with z.open("word/document.xml") as f:
                tree = ET.parse(f)
    except KeyError as exc:
        raise ValueError(_DOCX_PARSE_ERROR.format(path=docx_path)) from exc
    except (zipfile.BadZipFile, ET.ParseError) as exc:
        raise ValueError(_DOCX_PARSE_ERROR.format(path=docx_path)) from exc

    body = tree.getroot().find(f"{W}body")
    if body is None:
        raise ValueError(_DOCX_PARSE_ERROR.format(path=docx_path))
    return body


def _paragraph_text(p_elem: ET.Element) -> str:
    """Concatenate all ``<w:t>`` text nodes inside a paragraph element."""
    return "".join(t.text or "" for t in p_elem.iter(f"{W}t"))


def _row_cells(tr_elem: ET.Element) -> list[str]:
    """Return one stripped string per ``<w:tc>`` cell in a table row."""
    cells = []
    for tc in tr_elem.findall(f"{W}tc"):
        cells.append(_paragraph_text(tc).strip())
    return cells


def _extract_docx_records(docx_path: Path) -> list[tuple[int, list[str]]]:
    """Walk the docx body and return a list of ``(row_idx, cells)`` tuples.

    Each paragraph becomes a single-cell entry (the paragraph text); each
    table row becomes a multi-cell entry (one per ``<w:tc>``). Paragraphs
    that are empty after stripping are skipped. The row_idx is sequential
    across both paragraphs and table rows so source_record_id is stable
    across re-ingestions.
    """
    body = _read_docx_xml(docx_path)
    records: list[tuple[int, list[str]]] = []
    row_idx = 0
    for child in body:
        if child.tag == f"{W}p":
            text = _paragraph_text(child).strip()
            if not text:
                continue
            records.append((row_idx, [text]))
            row_idx += 1
        elif child.tag == f"{W}tbl":
            for tr in child.findall(f"{W}tr"):
                cells = _row_cells(tr)
                if any(c for c in cells):
                    records.append((row_idx, cells))
                    row_idx += 1
    return records


# ---------------------------------------------------------------------------
# Public entrypoints
# ---------------------------------------------------------------------------


def fetch_havre_bookings(
    source: str | Path,
    *,
    roster_date: str | None = None,
) -> list[JailBookingRecord]:
    """Parse a Hill County Sheriff's Office (HCSO) jail roster .docx file.

    Args:
        source: Local path to the .docx (the email worker writes it to
            ``uploads/`` before calling this).
        roster_date: Optional ISO date (``YYYY-MM-DD``) for the roster —
            typically the email's ``Date:`` header. When provided, every
            record's ``source_record_id`` is scoped to this date so
            re-ingesting the same DOCX filename on a new day creates
            fresh rows instead of silently dedup'ing against the previous
            day's rows. When ``None``, falls back to the file stem (the
            legacy behaviour, kept for CLI / test usage).

    Returns:
        A list of ``JailBookingRecord`` objects. Order matches the order
        of paragraphs and table rows in the .docx.
    """
    path = Path(source)
    if not path.is_file():
        raise FileNotFoundError(f"Havre roster file not found: {path}")
    if path.suffix.lower() != ".docx":
        raise ValueError(
            f"fetch_havre_bookings expects a .docx file, got: {path.suffix}"
        )

    candidates = _extract_docx_records(path)
    records: list[JailBookingRecord] = []
    for row_idx, raw_cells in candidates:
        if len(raw_cells) == 1:
            # Free-form paragraph: try the dedicated paragraph parser
            # (which knows how to handle "LASTNAME, FIRSTNAME" followed
            # by comma/dash/pipe-separated fields).
            rec = _parse_paragraph_record(
                raw_cells[0], source_path=path, row_idx=row_idx, roster_date=roster_date
            )
            if rec:
                records.append(rec)
                continue
        # Table row (multi-cell): pass directly to the cell-based parser.
        rec = _row_to_record(raw_cells, source_path=path, row_idx=row_idx, roster_date=roster_date)
        if rec:
            records.append(rec)
    logger.info("Parsed %d records from HCSO roster docx: %s", len(records), path)
    return records


def ingest_havre_roster(
    docx_path: str | Path, *, dry_run: bool = False, roster_date: str | None = None
) -> SyncStats:
    """End-to-end ingestion: parse the .docx, upsert rows, record the run.

    The function is safe to call from the email worker, the CLI, or tests.
    It opens its own DB connection so callers don't need to manage
    transactions or row factories.

    Args:
        docx_path: Local path to the .docx.
        dry_run: If True, parse + diff only; no DB writes.
        roster_date: Optional ISO date (``YYYY-MM-DD``) forwarded to
            :func:`fetch_havre_bookings` so each roster day's
            ``source_record_id`` is unique. See that function for details.
    """
    path = Path(docx_path)
    if not path.is_file():
        raise FileNotFoundError(f"Havre roster file not found: {path}")

    records = fetch_havre_bookings(path, roster_date=roster_date)
    if not records:
        logger.warning("Havre docx parsed to 0 records: %s", path)
        return SyncStats()

    conn: sqlite3.Connection = _connect_db()
    try:
        _ensure_tracked_sources(conn)
        source = conn.execute(
            '''
            SELECT * FROM jail_booking_sources WHERE county_slug = ?
            ''',
            (HPD_COUNTY_SLUG,),
        ).fetchone()
        if source is None:
            raise RuntimeError(
                f"jail_booking_sources row missing for county_slug={HPD_COUNTY_SLUG} "
                f"after _ensure_tracked_sources; cannot ingest {path}"
            )
        stats = _sync_records(conn, source, records, dry_run=dry_run)
        run_status = "success" if not dry_run else "dry_run"
        _record_run(
            conn,
            source_id=source["id"],
            run_type="email_docx",
            status=run_status,
            fetched_count=stats.fetched_count,
            new_count=stats.new_count,
            updated_count=stats.updated_count,
            missing_count=stats.missing_count,
            notes=f"Parsed {path.name}",
        )
        _mark_source_checked(
            conn,
            source["id"],
            success=not dry_run,
            notes=f"Email DOCX ingest: {path.name}",
        )
        conn.commit()
        return stats
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli() -> int:
    parser = argparse.ArgumentParser(
        description="Parse and ingest a Havre PD daily jail roster DOCX.",
    )
    parser.add_argument(
        "docx_path",
        help="Path to the local DOCX file (the email worker saves it to uploads/ before calling).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and print records without writing to the database.",
    )
    parser.add_argument(
        "--print-records",
        action="store_true",
        help="Print the parsed records as a summary (implies dry-run).",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging."
    )
    parser.add_argument(
        "--roster-date",
        default=None,
        help=(
            "ISO date (YYYY-MM-DD) of the roster. When set, every record's "
            "source_record_id is scoped to this date. Defaults to None, "
            "which falls back to the DOCX filename stem (legacy behaviour)."
        ),
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.print_records:
        records = fetch_havre_bookings(args.docx_path, roster_date=args.roster_date)
        for rec in records:
            print(
                f"{rec.booking_at or '----------'} | "
                f"{rec.person_name:<28} | "
                f"age={rec.age or '-':>3} | "
                f"{rec.charges_summary[:60]}"
            )
        print(f"\nTotal records: {len(records)}")
        return 0

    stats = ingest_havre_roster(
        args.docx_path, dry_run=args.dry_run, roster_date=args.roster_date
    )
    print(
        f"havre: fetched={stats.fetched_count} new={stats.new_count} "
        f"updated={stats.updated_count} missing={stats.missing_count}"
        + (" (dry-run)" if args.dry_run else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
