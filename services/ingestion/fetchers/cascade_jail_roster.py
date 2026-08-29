#!/usr/bin/env python3
"""
cascade_jail_roster.py — Cascade County Jail Roster Fetcher

Fetches the current inmate roster from Cascade County's SharePoint-hosted
roster and parses it into JailBookingRecords for ingestion by Montana Blotter.

The roster is published via the county's public page at:
  https://www.cascadecountymt.gov/314/Inmate-Roster

The page embeds a SharePoint b-download sharing link pointing to:
  https://ccmtgov-my.sharepoint.com/personal/jailroster_cascadecountymt_gov/
  Documents/Attachments/jailroster.pdf

The sharing link is a short-lived guest link. The fetcher follows the redirect
chain, captures the FedAuth/rtFa cookies from the Microsoft auth flow, then
tries multiple download strategies (REST API, direct file URL, sharing link
with ?download=1).

Usage:
    python cascade_jail_roster.py              # Fetch and print records
    python cascade_jail_roster.py --save-pdf   # Also save raw PDF
    python cascade_jail_roster.py --dry-run    # Show link without downloading
"""

from __future__ import annotations

import io
import logging
import re
import sys
from urllib.parse import unquote, urlparse

import pdfplumber
import requests

from services.ingestion.models import JailBookingRecord

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

COUNTY_GOV_PAGE = "https://www.cascadecountymt.gov/314/Inmate-Roster"
SHAREPOINT_TENANT = "ccmtgov-my.sharepoint.com"
SERVER_RELATIVE_PATH = (
    "/personal/jailroster_cascadecountymt_gov/"
    "Documents/Attachments/jailroster.pdf"
)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    return s


# ---------------------------------------------------------------------------
# Sharing link extraction
# ---------------------------------------------------------------------------

_SHARING_LINK_RE = re.compile(
    r"https://ccmtgov-my\.sharepoint\.com/:b:/g/personal/"
    r"jailroster[^'\"\s<>]+"
)


def extract_sharing_link(html: str) -> str | None:
    m = _SHARING_LINK_RE.search(html)
    if m:
        return m.group(0)
    return None


def get_sharing_link(session: requests.Session) -> str | None:
    """Fetch the county page and extract the embedded SharePoint sharing link."""
    try:
        resp = session.get(COUNTY_GOV_PAGE, timeout=30)
        resp.raise_for_status()
        link = extract_sharing_link(resp.text)
        if link:
            logger.info("Found sharing link on county page")
        else:
            logger.warning("No sharing link found on county page")
        return link
    except Exception as e:
        logger.warning("Could not fetch county page: %s", e)
        return None


# ---------------------------------------------------------------------------
# Download pipeline
# ---------------------------------------------------------------------------


def download_roster_pdf(
    session: requests.Session,
    sharing_link: str | None,
) -> bytes:
    """Download the roster PDF using multiple fallback strategies.

    1. Follow the sharing link to capture FedAuth/rtFa cookies
    2. Try REST API with the cookies
    3. Try direct file URL with the cookies
    4. Try sharing link with ?download=1
    """
    if not sharing_link:
        raise RuntimeError(
            "No sharing link available. "
            "The county page may have been updated. "
            "Re-run get_sharing_link() to refresh."
        )

    logger.info("Requesting sharing link: %s", sharing_link[:100])

    resp = session.get(sharing_link, timeout=30, allow_redirects=True)
    logger.info(
        "Sharing link → status=%d, final=%s",
        resp.status_code,
        resp.url[:120],
    )
    logger.info("Cookies: %s", list(session.cookies.keys()))

    if resp.status_code == 404:
        logger.warning("Sharing link returned 404 — the link may have expired.")

    pdf: bytes | None = None

    # Strategy 1: REST API via FedAuth cookie
    pdf = _try_rest_download(session)
    if pdf:
        logger.info("REST API download: %d bytes", len(pdf))
        return pdf

    # Strategy 2: Direct file URL with FedAuth cookie
    pdf = _try_direct_download(session)
    if pdf:
        logger.info("Direct URL download: %d bytes", len(pdf))
        return pdf

    # Strategy 3: Sharing link + ?download=1
    pdf = _try_sharing_download(session, sharing_link)
    if pdf:
        logger.info("Sharing link download param: %d bytes", len(pdf))
        return pdf

    raise RuntimeError(
        "All download strategies failed. "
        "The sharing link appears expired or access-restricted. "
        "Re-extract the sharing link from the county page."
    )


def _try_rest_download(session: requests.Session) -> bytes | None:
    api_url = (
        f"https://{SHAREPOINT_TENANT}/_api/web/"
        f"getfilebyserverrelativeurl('{SERVER_RELATIVE_PATH}')/$value"
    )
    logger.info("REST: %s", api_url[:100])
    resp = session.get(
        api_url, timeout=30,
        headers={"Accept": "application/octet-stream"},
    )
    if resp.status_code == 200 and resp.content[:4] == b"%PDF":
        return resp.content
    logger.warning(
        "REST: status=%d, ct=%s, len=%d, head=%r",
        resp.status_code,
        resp.headers.get("Content-Type", ""),
        len(resp.content),
        resp.content[:80],
    )
    return None


def _try_direct_download(session: requests.Session) -> bytes | None:
    direct_url = f"https://{SHAREPOINT_TENANT}{SERVER_RELATIVE_PATH}"
    logger.info("Direct: %s", direct_url[:100])
    resp = session.get(direct_url, timeout=30, allow_redirects=True)
    if resp.status_code == 200 and resp.content[:4] == b"%PDF":
        return resp.content
    logger.warning(
        "Direct: status=%d, ct=%s, len=%d, head=%r",
        resp.status_code,
        resp.headers.get("Content-Type", ""),
        len(resp.content),
        resp.content[:80],
    )
    return None


def _try_sharing_download(
    session: requests.Session,
    sharing_link: str,
) -> bytes | None:
    dl_url = f"{sharing_link}&download=1" if "?" in sharing_link else f"{sharing_link}?download=1"
    logger.info("Sharing+download: %s", dl_url[:100])
    resp = session.get(dl_url, timeout=30, allow_redirects=True)
    if resp.status_code == 200 and resp.content[:4] == b"%PDF":
        return resp.content
    logger.warning(
        "Sharing+download: status=%d, ct=%s, len=%d",
        resp.status_code,
        resp.headers.get("Content-Type", ""),
        len(resp.content),
    )
    return None


# ---------------------------------------------------------------------------
# PDF parsing
# ---------------------------------------------------------------------------


def parse_roster_pdf(pdf_bytes: bytes) -> list[JailBookingRecord]:
    """Parse the Cascade County roster PDF into JailBookingRecords."""
    records: list[JailBookingRecord] = []
    seen_ids: set[str] = set()

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_idx, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            logger.info("Page %d: %d chars", page_idx, len(text))

            tables = page.extract_tables()
            if tables:
                for table in tables:
                    for row in table:
                        rec = _parse_table_row(row, seen_ids)
                        if rec:
                            records.append(rec)
            else:
                records.extend(_parse_text_page(text, seen_ids))

    logger.info("Parsed %d records from %d pages", len(records), len(pdf.pages))
    return records


def _parse_table_row(
    row: list[str | None],
    seen_ids: set[str],
) -> JailBookingRecord | None:
    cells = [c.strip() if c else "" for c in row]
    cells = [c for c in cells if c]
    if not cells:
        return None

    name = cells[0]
    booking_date: str | None = None
    charges: list[str] = []
    bail: str | None = None
    release_date: str | None = None

    for cell in cells[1:]:
        if re.match(r"^\d{1,2}/\d{1,2}/\d{2,4}$", cell):
            booking_date = _normalize_date(cell)
        elif re.match(r"^\d{4}-\d{2}-\d{2}$", cell):
            booking_date = cell
        elif re.search(r"\$\s?\d", cell) and "Bail" not in cell and "Bond" not in cell:
            bail = cell
        elif re.match(r"^\d{1,2}/\d{1,2}/\d{2,4}$", cell) and booking_date:
            release_date = _normalize_date(cell)
        else:
            charges.append(cell)

    if not _looks_like_name(name):
        return None

    rec = _build_record(
        name=name,
        booking_date=booking_date,
        charges_summary=charges,
        bail=bail,
        release_date=release_date,
        seen_ids=seen_ids,
    )
    return rec


def _parse_text_page(
    text: str,
    seen_ids: set[str],
) -> list[JailBookingRecord]:
    records: list[JailBookingRecord] = []
    current: dict | None = None

    for line in text.splitlines():
        line = line.strip()
        if not line:
            if current:
                records.append(_finalize_current(current, seen_ids))
                current = None
            continue

        if _looks_like_name(line):
            if current:
                records.append(_finalize_current(current, seen_ids))
            current = {
                "name": line,
                "booking_date": None,
                "charges": [],
                "bail": None,
                "release_date": None,
            }
        elif current:
            if re.match(r"^\d{1,2}/\d{1,2}/\d{2,4}$", line):
                current["booking_date"] = _normalize_date(line)
            elif re.match(r"^\d{4}-\d{2}-\d{2}$", line):
                if not current["booking_date"]:
                    current["booking_date"] = line
            elif re.search(r"\$\s?\d", line) and "Bond" not in line and "Bail" not in line:
                if not current["bail"]:
                    current["bail"] = line
            else:
                current["charges"].append(line)

    if current:
        records.append(_finalize_current(current, seen_ids))

    return records


def _finalize_current(current: dict, seen_ids: set[str]) -> JailBookingRecord:
    return _build_record(
        name=current["name"],
        booking_date=current["booking_date"],
        charges_summary=current["charges"],
        bail=current["bail"],
        release_date=current["release_date"],
        seen_ids=seen_ids,
    )


def _build_record(
    name: str,
    booking_date: str | None,
    charges_summary: list[str],
    bail: str | None,
    release_date: str | None,
    seen_ids: set[str],
) -> JailBookingRecord:
    last, first = _split_name(name)
    person_name = f"{last.title()}, {first.title()}"

    charges_str = "; ".join(charges_summary) if charges_summary else ""
    source_record_id = f"cascade:{person_name.lower().replace(' ', '-')}"
    if booking_date:
        source_record_id += f":{booking_date.replace(' ', 'T')}"
    if source_record_id in seen_ids:
        counter = 1
        while f"{source_record_id}:{counter}" in seen_ids:
            counter += 1
        source_record_id = f"{source_record_id}:{counter}"
    seen_ids.add(source_record_id)

    return JailBookingRecord(
        source_record_id=source_record_id,
        person_name=person_name,
        age=None,
        booking_number="",
        booking_at=booking_date or None,
        charges_summary=charges_str or "Charge details available on the official Cascade County roster.",
        source_url=COUNTY_GOV_PAGE,
    )


def _split_name(name: str) -> tuple[str, str]:
    name = re.sub(r"\s+", " ", name).strip()
    if "," in name:
        parts = [p.strip() for p in name.split(",", 1)]
        if len(parts) == 2:
            return parts[0], parts[1]
    parts = name.split(None, 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return name, ""


def _normalize_date(value: str) -> str | None:
    value = value.strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            from datetime import datetime
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    return None


def _looks_like_name(line: str) -> bool:
    if len(line) > 60:
        return False
    if re.match(r"^[A-Z][A-Z'\-]+,\s*[A-Z][A-Z'\-]+", line):
        return True
    if re.match(r"^[A-Z][A-Z'\-]+\s+[A-Z][A-Z'\-]+", line):
        return True
    return False


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def fetch_cascade_jail_roster(save_pdf_path: str | None = None) -> list[JailBookingRecord]:
    """Fetch and parse the Cascade County jail roster."""
    session = _make_session()
    sharing_link = get_sharing_link(session)

    if not sharing_link:
        logger.warning("No sharing link found — using known path directly")
        sharing_link = None

    pdf_bytes = download_roster_pdf(session, sharing_link)
    logger.info("Downloaded PDF: %d bytes", len(pdf_bytes))

    if save_pdf_path:
        with open(save_pdf_path, "wb") as f:
            f.write(pdf_bytes)
        logger.info("Saved PDF to %s", save_pdf_path)

    return parse_roster_pdf(pdf_bytes)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Cascade County Jail Roster Fetcher",
    )
    parser.add_argument(
        "--save-pdf", metavar="PATH",
        help="Save raw PDF to this path",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show the sharing link without downloading",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.dry_run:
        session = _make_session()
        link = get_sharing_link(session)
        print(f"County page: {COUNTY_GOV_PAGE}")
        print(f"Sharing link: {link or 'NOT FOUND — page may have changed'}")
        print(f"Server-relative path: {SERVER_RELATIVE_PATH}")
        return

    try:
        records = fetch_cascade_jail_roster(save_pdf_path=args.save_pdf)
    except Exception as e:
        logger.error("Failed: %s", e)
        sys.exit(1)

    if not records:
        print("No records found in the roster.")
        return

    print(f"\nFound {len(records)} inmate records:\n")
    print("-" * 80)

    for i, rec in enumerate(records, 1):
        print(f"\n[{i}] {rec.person_name}")
        if rec.booking_at:
            print(f"    Booking: {rec.booking_at}")
        if rec.charges_summary:
            print(f"    Charges: {rec.charges_summary}")
        print(f"    Source: {rec.source_url}")

    print("\n" + "-" * 80)
    print(f"Total: {len(records)} records")


if __name__ == "__main__":
    main()
