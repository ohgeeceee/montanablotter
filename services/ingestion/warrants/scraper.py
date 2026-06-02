"""
Montana warrant list scraper.

Fetches publicly posted warrant lists from county sheriff offices, Zuercher
portals, municipal courts, and PDF bulletins — normalizing them into
WarrantRecord objects for storage in the warrants table.

Source registry: services/ingestion/warrants/source_registry.py (56 counties
+ major city PD / municipal court sources).

Adapters:
- flathead: HTML A–Z list
- lewis-clark: OpenCities Justice Court `<li>` list
- rosebud + generic: sheriff-page warrant PDF finder
- zuercher: Zuercher portal #/warrants API (jail-hold supplement on failure)
- billings / great-falls / municipal-table: HTML table municipal lists
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

from services.ingestion.http_client import make_ingest_session, public_dns_fallback
from services.ingestion.warrants.html_table import parse_warrant_table_page
from services.ingestion.warrants.models import WarrantRecord
from services.ingestion.warrants.source_registry import SOURCES

try:
    from services.ingestion.warrants.billings import fetch_billings_warrants
except ImportError:  # pragma: no cover - optional during partial deploys
    fetch_billings_warrants = None  # type: ignore[assignment,misc]

try:
    from services.ingestion.warrants.flathead import fetch_flathead_warrants
except ImportError:  # pragma: no cover - optional during partial deploys
    fetch_flathead_warrants = None  # type: ignore[assignment,misc]

try:
    from services.ingestion.warrants.great_falls import fetch_great_falls_warrants
except ImportError:  # pragma: no cover - optional during partial deploys
    fetch_great_falls_warrants = None  # type: ignore[assignment,misc]

try:
    from services.ingestion.warrants.lewis_clark import fetch_lewis_clark_warrants
except ImportError:  # pragma: no cover - optional during partial deploys
    fetch_lewis_clark_warrants = None  # type: ignore[assignment,misc]

try:
    from services.ingestion.warrants.zuercher import fetch_zuercher_warrants
except ImportError:  # pragma: no cover - optional during partial deploys
    fetch_zuercher_warrants = None  # type: ignore[assignment,misc]

try:
    from services.ingestion.warrants.helena import fetch_helena_warrants
except ImportError:  # pragma: no cover - optional during partial deploys
    fetch_helena_warrants = None  # type: ignore[assignment,misc]

try:
    from services.ingestion.warrants.sheridan import fetch_sheridan_warrants
except ImportError:  # pragma: no cover - optional during partial deploys
    fetch_sheridan_warrants = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://montanablotter.com)"

_MUNICIPAL_INFO_MARKERS = (
    "please contact the court",
    "please call the municipal court",
    "montana public access portal",
    "court public access portal",
    "jccrimclerks@yellowstonecountymt.gov",
)


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


def _fetch_municipal_table(
    session: requests.Session, source: dict[str, str], slug: str
) -> list[WarrantRecord]:
    """Generic municipal court HTML table adapter."""
    url = source["url"]
    try:
        resp = session.get(url, timeout=60)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Failed to fetch municipal warrant page %s: %s", url, exc)
        return []

    return parse_warrant_table_page(
        resp.text,
        county=source["county"],
        city=source.get("city", ""),
        source_url=url,
        source_prefix=f"{slug}-warrant",
        issued_by=source.get("issued_by", ""),
        info_only_markers=_MUNICIPAL_INFO_MARKERS,
    )


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

    session = make_ingest_session(user_agent=USER_AGENT)

    county = source["county"]
    url = source["url"]

    adapter = source.get("adapter", "generic")

    with public_dns_fallback():
        if adapter == "flathead" and fetch_flathead_warrants is not None:
            return fetch_flathead_warrants(session)
        if adapter == "billings" and fetch_billings_warrants is not None:
            return fetch_billings_warrants(session)
        if adapter == "great-falls" and fetch_great_falls_warrants is not None:
            return fetch_great_falls_warrants(session)
        if adapter == "lewis-clark" and fetch_lewis_clark_warrants is not None:
            return fetch_lewis_clark_warrants(session)
        if adapter == "zuercher" and fetch_zuercher_warrants is not None:
            return fetch_zuercher_warrants(session, url, county=county)
        if adapter == "helena" and fetch_helena_warrants is not None:
            return fetch_helena_warrants(session)
        if adapter == "sheridan" and fetch_sheridan_warrants is not None:
            return fetch_sheridan_warrants(session)
        if adapter == "municipal-table":
            return _fetch_municipal_table(session, source, county_slug)
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
                bond_amount, bond_type, status, source_url, mugshot_url,
                scraped_at, first_seen_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                r.source_record_id, r.county, r.city, r.person_name, r.dob,
                r.warrant_type, r.charges_text, r.issued_by, r.issue_date,
                r.bond_amount, r.bond_type, r.status, r.source_url,
                getattr(r, "mugshot_url", "") or "",
                run_ts, run_ts, run_ts,
            ),
        )
        if cursor.rowcount:
            new_count += 1
        else:
            mugshot_url = getattr(r, "mugshot_url", "") or ""
            if mugshot_url:
                cursor.execute(
                    """
                    UPDATE warrants
                       SET charges_text = ?, bond_amount = ?, bond_type = ?,
                           issued_by = ?, status = ?, source_url = ?,
                           city = CASE WHEN ? != '' THEN ? ELSE city END,
                           mugshot_url = ?,
                           scraped_at = ?, updated_at = ?,
                           resolved_at = CASE WHEN ? = 'active' THEN '' ELSE resolved_at END
                     WHERE source_record_id = ?
                    """,
                    (
                        r.charges_text, r.bond_amount, r.bond_type,
                        r.issued_by, r.status or 'active', r.source_url,
                        r.city, r.city,
                        mugshot_url,
                        run_ts, run_ts,
                        r.status or 'active',
                        r.source_record_id,
                    ),
                )
            else:
                cursor.execute(
                    """
                    UPDATE warrants
                       SET charges_text = ?, bond_amount = ?, bond_type = ?,
                           issued_by = ?, status = ?, source_url = ?,
                           city = CASE WHEN ? != '' THEN ? ELSE city END,
                           scraped_at = ?, updated_at = ?,
                           resolved_at = CASE WHEN ? = 'active' THEN '' ELSE resolved_at END
                     WHERE source_record_id = ?
                    """,
                    (
                        r.charges_text, r.bond_amount, r.bond_type,
                        r.issued_by, r.status or 'active', r.source_url,
                        r.city, r.city,
                        run_ts, run_ts,
                        r.status or 'active',
                        r.source_record_id,
                    ),
                )
            updated_count += 1

    conn.commit()
    return new_count, updated_count


def resolve_stale_warrants(
    conn: sqlite3.Connection,
    county: str,
    active_source_ids: set[str],
    run_ts: str,
) -> int:
    """Mark county warrants no longer on the sheriff list as resolved."""
    cursor = conn.cursor()
    rows = cursor.execute(
        """
        SELECT source_record_id
        FROM warrants
        WHERE county = ? AND status = 'active'
        """,
        (county,),
    ).fetchall()
    resolved = 0
    for row in rows:
        source_record_id = str(row['source_record_id'])
        if source_record_id in active_source_ids:
            continue
        cursor.execute(
            """
            UPDATE warrants
               SET status = 'resolved',
                   resolved_at = CASE
                       WHEN COALESCE(resolved_at, '') = '' THEN ?
                       ELSE resolved_at
                   END,
                   updated_at = ?
             WHERE source_record_id = ?
            """,
            (run_ts, run_ts, source_record_id),
        )
        resolved += 1
    conn.commit()
    return resolved
