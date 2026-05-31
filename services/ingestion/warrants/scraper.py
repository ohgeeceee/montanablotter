"""
Montana warrant list scraper.

Fetches publicly posted warrant PDFs from county sheriff office websites
and normalizes them into WarrantRecord objects for storage in the warrants table.

Source adapters:
- rosebud: rosebudcountymt.gov/sheriff  (known WordPress PDF pattern)
- All other counties: generic PDF finder (scans sheriff page for warrant PDFs)

Adding a new county: append an entry to SOURCES and restart — the generic
adapter handles any sheriff site that links warrant PDFs from its main page.
"""

from __future__ import annotations

import io
import logging
import re
import sqlite3
from html.parser import HTMLParser
from urllib.parse import urljoin

import pdfplumber
import requests

from services.ingestion.warrants.models import WarrantRecord

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://montanablotter.com)"

# Registry of known warrant-publishing county sheriff sources.
# Keyed by slug (used with --county flag). Any entry without a custom adapter
# uses the generic PDF finder, which scans the URL for warrant PDF links.
SOURCES: dict[str, dict[str, str]] = {
    "rosebud": {
        "county": "Rosebud",
        "url": "https://www.rosebudcountymt.gov/sheriff",
    },
    "cascade": {
        "county": "Cascade",
        "url": "https://www.cascadecountymt.gov/313/Sheriffs-Office",
    },
    "custer": {
        "county": "Custer",
        "url": "https://www.custercountymt.gov/departments/sheriff",
    },
    "dawson": {
        "county": "Dawson",
        "url": "https://www.dawsoncountymt.gov/sheriff",
    },
    "lewis-and-clark": {
        "county": "Lewis and Clark",
        "url": "https://www.lcso.mt.gov",
    },
    "flathead": {
        "county": "Flathead",
        "url": "https://www.flatheadsheriff.org",
    },
    "carbon": {
        "county": "Carbon",
        "url": "https://www.carboncountymt.gov/government/sheriff",
    },
    "powder-river": {
        "county": "Powder River",
        "url": "https://www.powderrivercountymt.gov/departments/sheriff",
    },
    "prairie": {
        "county": "Prairie",
        "url": "https://www.prairiecountymt.gov/sheriff",
    },
    "valley": {
        "county": "Valley",
        "url": "https://www.valleycountymt.gov/county-officials/sheriff/",
    },
}


# ---------------------------------------------------------------------------
# HTML link scanner
# ---------------------------------------------------------------------------

class _AnchorCollector(HTMLParser):
    """Collect all <a href> values and their link text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        href = attr_map.get("href", "").strip()
        if href:
            self.items.append({"href": href, "text": ""})

    def handle_data(self, data: str) -> None:
        if self.items:
            self.items[-1]["text"] += data

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self.items:
            self.items[-1]["text"] = self.items[-1]["text"].strip()


def fetch_warrant_pdfs_from_url(
    session: requests.Session, url: str
) -> list[dict[str, str]]:
    """Scan a sheriff page for links to warrant PDFs.

    A link qualifies if its href contains 'warrant' AND ends with '.pdf',
    or if the visible link text contains 'warrant' and the href ends with '.pdf'.
    Returns list of {"url": absolute_url, "text": link_text}.
    """
    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Could not fetch %s: %s", url, exc)
        return []

    collector = _AnchorCollector()
    collector.feed(resp.text)

    results: list[dict[str, str]] = []
    for item in collector.items:
        href = item["href"]
        text = item["text"]
        if not href.lower().endswith(".pdf"):
            continue
        if "warrant" in href.lower() or "warrant" in text.lower():
            results.append({"url": urljoin(url, href), "text": text})

    logger.info("Found %d warrant PDF link(s) on %s", len(results), url)
    return results


# ---------------------------------------------------------------------------
# PDF parser
# ---------------------------------------------------------------------------

_NAME_RE = re.compile(r"^([A-Z][A-Z' \-]+),\s+([A-Z][A-Z' \-]+)\s+", re.ASCII)
_BOND_RE = re.compile(r"([\d,]+\.\d+)\s*\(([^)]+)\)\s*")
_COURT_RE = re.compile(
    r"(Justice of the Peace|District Court|City Court|Justice Court|DOC|"
    r"[A-Z][a-z]+ Justice|[A-Z][a-z]+ District)\s*$",
    re.IGNORECASE,
)
_WARRANT_HEADER_RE = re.compile(
    r"warrant\s+list|warrants\s+list|active\s+warrants|last,\s*first\s+name", re.IGNORECASE
)


def _parse_warrant_line(
    line: str, county: str, source_url: str
) -> WarrantRecord | None:
    """Parse one text line from a warrant PDF into a WarrantRecord.

    Expected loose format (generalized from Rosebud County PDFs):
        LAST, FIRST  [BOND_AMOUNT (BondType)]  CHARGES...  [COURT]

    Returns None if the line doesn't look like a person entry.
    """
    name_match = _NAME_RE.match(line)
    if not name_match:
        return None

    last = name_match.group(1).strip().title()
    first = name_match.group(2).strip().title()
    person_name = f"{last}, {first}"
    remainder = line[name_match.end():]

    bond_amount = ""
    bond_type = ""
    bond_match = _BOND_RE.match(remainder)
    if bond_match:
        bond_amount = bond_match.group(1)
        bond_type = bond_match.group(2).strip()
        remainder = remainder[bond_match.end():]

    issued_by = ""
    court_match = _COURT_RE.search(remainder)
    if court_match:
        issued_by = court_match.group(1).strip()
        remainder = remainder[: court_match.start()].strip()

    charges_text = remainder.strip() or "Active warrant"

    slug = re.sub(r"[^a-z0-9]+", "-", person_name.lower()).strip("-")
    source_record_id = f"{county.lower().replace(' ', '-')}-warrant:{slug}"

    return WarrantRecord(
        source_record_id=source_record_id,
        county=county,
        person_name=person_name,
        charges_text=charges_text,
        issued_by=issued_by,
        bond_amount=bond_amount,
        bond_type=bond_type,
        source_url=source_url,
    )


def parse_warrant_pdf(
    pdf_bytes: bytes, source_url: str, county: str
) -> list[WarrantRecord]:
    """Extract warrant records from a PDF's text content."""
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception as exc:
        logger.warning("pdfplumber failed on %s: %s", source_url, exc)
        return []

    records: list[WarrantRecord] = []
    seen_ids: set[str] = set()
    in_list = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _WARRANT_HEADER_RE.search(line):
            in_list = True
            continue
        if not in_list:
            continue

        record = _parse_warrant_line(line, county, source_url)
        if not record:
            continue

        # Deduplicate within same PDF (same slug → append counter suffix)
        base_id = record.source_record_id
        if base_id in seen_ids:
            counter = 2
            while f"{base_id}:{counter}" in seen_ids:
                counter += 1
            record = WarrantRecord(
                source_record_id=f"{base_id}:{counter}",
                county=record.county,
                person_name=record.person_name,
                charges_text=record.charges_text,
                issued_by=record.issued_by,
                bond_amount=record.bond_amount,
                bond_type=record.bond_type,
                source_url=record.source_url,
            )
        seen_ids.add(record.source_record_id)
        records.append(record)

    logger.info("Parsed %d warrant record(s) from %s", len(records), source_url)
    return records


# ---------------------------------------------------------------------------
# County-specific adapters
# ---------------------------------------------------------------------------

def _fetch_rosebud(session: requests.Session) -> list[WarrantRecord]:
    """Rosebud County — WordPress sheriff page with linked warrant PDF."""
    url = SOURCES["rosebud"]["url"]
    pdfs = fetch_warrant_pdfs_from_url(session, url)
    records: list[WarrantRecord] = []
    for pdf_info in pdfs:
        try:
            resp = session.get(pdf_info["url"], timeout=60)
            resp.raise_for_status()
            records.extend(parse_warrant_pdf(resp.content, pdf_info["url"], "Rosebud"))
        except requests.RequestException as exc:
            logger.warning("Failed to download Rosebud warrant PDF %s: %s", pdf_info["url"], exc)
    return records


def _fetch_generic_pdf(
    session: requests.Session, county: str, url: str
) -> list[WarrantRecord]:
    """Generic adapter: scan the sheriff page for warrant PDFs and parse them."""
    pdfs = fetch_warrant_pdfs_from_url(session, url)
    if not pdfs:
        logger.info("No warrant PDFs found for %s at %s", county, url)
        return []

    records: list[WarrantRecord] = []
    for pdf_info in pdfs:
        try:
            resp = session.get(pdf_info["url"], timeout=60)
            resp.raise_for_status()
            records.extend(parse_warrant_pdf(resp.content, pdf_info["url"], county))
        except requests.RequestException as exc:
            logger.warning(
                "Failed to download %s warrant PDF %s: %s", county, pdf_info["url"], exc
            )
    return records


# ---------------------------------------------------------------------------
# Public dispatch
# ---------------------------------------------------------------------------

def fetch_warrants_for_county(county_slug: str) -> list[WarrantRecord]:
    """Fetch warrant records for a single county slug.

    Returns an empty list (with a log warning) for unknown slugs.
    """
    source = SOURCES.get(county_slug)
    if not source:
        logger.warning(
            "Unknown county slug %r. Run --list to see available sources.", county_slug
        )
        return []

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    county = source["county"]
    url = source["url"]

    if county_slug == "rosebud":
        return _fetch_rosebud(session)
    return _fetch_generic_pdf(session, county, url)


def upsert_warrants(
    conn: sqlite3.Connection,
    records: list[WarrantRecord],
    run_ts: str,
) -> tuple[int, int]:
    """Write warrant records to DB.

    Uses INSERT OR IGNORE + UPDATE to preserve first_seen_at across re-scrapes.
    Returns (new_count, updated_count).
    """
    cursor = conn.cursor()
    new_count = 0
    updated_count = 0

    for r in records:
        cursor.execute(
            """
            INSERT OR IGNORE INTO warrants (
                source_record_id, county, city, person_name, dob,
                warrant_type, charges_text, issued_by, issue_date,
                bond_amount, bond_type, status, source_url,
                scraped_at, first_seen_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                r.source_record_id, r.county, r.city, r.person_name, r.dob,
                r.warrant_type, r.charges_text, r.issued_by, r.issue_date,
                r.bond_amount, r.bond_type, r.status, r.source_url,
                run_ts, run_ts, run_ts,
            ),
        )
        if cursor.rowcount:
            new_count += 1
        else:
            cursor.execute(
                """
                UPDATE warrants
                   SET charges_text = ?, bond_amount = ?, bond_type = ?,
                       issued_by = ?, status = ?, source_url = ?,
                       scraped_at = ?, updated_at = ?
                 WHERE source_record_id = ?
                """,
                (
                    r.charges_text, r.bond_amount, r.bond_type,
                    r.issued_by, r.status, r.source_url,
                    run_ts, run_ts,
                    r.source_record_id,
                ),
            )
            updated_count += 1

    conn.commit()
    return new_count, updated_count
