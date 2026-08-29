#!/usr/bin/env python3
"""
cascade_roster.py — Cascade County Jail Roster Fetcher

Fetches the current inmate roster PDF from Cascade County's SharePoint-hosted
roster and parses it into structured records.

The roster is published at:
  https://www.cascadecountymt.gov/314/Inmate-Roster

which links to a SharePoint guest-access sharing URL for the PDF:
  https://ccmtgov-my.sharepoint.com/:b:/g/personal/jailroster_cascadecountymt_gov/EbIMNOlpS-pNj2V6jtlc11YBzi2NN7EmcmLK4hRFY4pTGw?e=NvKMHS

Usage:
    python cascade_roster.py              # Fetch and print records
    python cascade_roster.py --save-pdf  # Also save the raw PDF
    python cascade_roster.py --dry-run   # Show links without downloading
"""

from __future__ import annotations

import io
import logging
import re
import sys
from urllib.parse import unquote, urlparse

import pdfplumber
import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

CASCADE_GOV_ROSTER_PAGE = "https://www.cascadecountymt.gov/314/Inmate-Roster"
SHAREPOINT_TENANT = "ccmtgov-my.sharepoint.com"
KNOWN_FILE_PATH = (
    "/personal/jailroster_cascadecountymt_gov/"
    "Documents/Attachments/jailroster.pdf"
)

DEFAULT_SHARING_LINK = (
    "https://ccmtgov-my.sharepoint.com/:b:/g/personal/"
    "jailroster_cascadecountymt_gov/EbIMNOlpS-pNj2V6jtlc11YBzi2NN7EmcmLK4hRFY4pTGw?e=NvKMHS"
)


# ---------------------------------------------------------------------------
# HTTP session
# ---------------------------------------------------------------------------

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
})


# ---------------------------------------------------------------------------
# Sharing link discovery
# ---------------------------------------------------------------------------

def get_sharing_link() -> str:
    """Extract the current SharePoint sharing link from the county page."""
    try:
        resp = SESSION.get(CASCADE_GOV_ROSTER_PAGE, timeout=30)
        resp.raise_for_status()
        match = re.search(
            r'https://ccmtgov-my\.sharepoint\.com/:b:/g/personal/jailroster[^"\'\\s<>]+',
            resp.text,
        )
        if match:
            link = match.group(0)
            logger.info("Found sharing link on county page")
            return link
        logger.warning("No sharing link found on county page")
    except Exception as e:
        logger.warning("Could not fetch county page: %s", e)
    return DEFAULT_SHARING_LINK


# ---------------------------------------------------------------------------
# Download pipeline
# ---------------------------------------------------------------------------

def download_roster_pdf(sharing_link: str) -> bytes:
    """
    Download the roster PDF.

    Flow:
    1. GET the sharing link → follows 302 to OneDrive page, sets FedAuth cookie
    2. Try REST API download using the FedAuth cookie
    3. If REST fails, try direct file URL with cookie
    4. If direct fails, try sharing link with ?download=1
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": SESSION.headers["User-Agent"],
        "Accept": SESSION.headers["Accept"],
    })

    logger.info("Requesting SharePoint sharing link...")
    resp = session.get(sharing_link, timeout=30, allow_redirects=True)
    logger.info(
        "Sharing link → status=%d, final_url=%s",
        resp.status_code,
        resp.url[:120]
    )
    logger.info("Cookies: %s", list(session.cookies.keys()))

    if resp.status_code == 404:
        logger.warning(
            "Sharing link returned 404 — link has expired."
        )
        # Still try with the cookie we got
    elif resp.status_code not in (200, 302):
        raise RuntimeError(f"Sharing link returned {resp.status_code}")

    # Extract file path from redirect chain
    file_path = _extract_file_path(resp)
    if not file_path:
        logger.info("Using known file path")
        file_path = KNOWN_FILE_PATH

    logger.info("File path: %s", file_path)

    # Try REST API with FedAuth cookie
    pdf = _try_rest_api(session, file_path)
    if pdf:
        logger.info("Downloaded via REST API: %d bytes", len(pdf))
        return pdf

    # Try direct download
    pdf = _try_direct(session, file_path)
    if pdf:
        logger.info("Downloaded via direct URL: %d bytes", len(pdf))
        return pdf

    # Try sharing link with download param
    pdf = _try_sharing_download(session, sharing_link)
    if pdf:
        logger.info("Downloaded via sharing link download param: %d bytes", len(pdf))
        return pdf

    raise RuntimeError(
        "All download approaches failed. "
        "The sharing link may have expired."
    )


def _extract_file_path(resp: requests.Response) -> str | None:
    """Extract server-relative file path from the redirect chain."""
    parsed = urlparse(resp.url)
    m = re.search(r"id=([^&]+)", parsed.query)
    if m:
        decoded = unquote(m.group(1))
        if decoded.startswith("/"):
            logger.info("File path from final URL: %s", decoded)
            return decoded

    for hist in resp.history:
        hp = urlparse(hist.url)
        m = re.search(r"id=([^&]+)", hp.query)
        if m:
            decoded = unquote(m.group(1))
            if decoded.startswith("/"):
                logger.info("File path from redirect history: %s", decoded)
                return decoded

    # Check if final URL is a direct file URL
    if "/personal/jailroster" in resp.url and ".pdf" in resp.url.lower():
        logger.info("Final URL is direct file URL")
        return urlparse(resp.url).path

    return None


def _try_rest_api(session: requests.Session, file_path: str) -> bytes | None:
    """Download via SharePoint REST API using FedAuth cookie."""
    api_url = (
        f"https://{SHAREPOINT_TENANT}/_api/web/"
        f"getfilebyserverrelativeurl('{file_path}')/$value"
    )
    logger.info("Trying REST API: %s...", api_url[:90])

    resp = session.get(api_url, timeout=30, headers={"Accept": "application/octet-stream"})

    if resp.status_code == 200 and resp.content[:4] == b"%PDF":
        return resp.content

    logger.warning(
        "REST API: status=%d, ct=%s, len=%d",
        resp.status_code,
        resp.headers.get('Content-Type', ''),
        len(resp.content),
    )
    return None


def _try_direct(session: requests.Session, file_path: str) -> bytes | None:
    """Download via direct file URL using FedAuth cookie."""
    direct_url = f"https://{SHAREPOINT_TENANT}{file_path}"
    logger.info("Trying direct URL: %s...", direct_url[:90])

    resp = session.get(direct_url, timeout=30, allow_redirects=True)

    if resp.status_code == 200 and resp.content[:4] == b"%PDF":
        return resp.content

    logger.warning(
        "Direct URL: status=%d, ct=%s",
        resp.status_code,
        resp.headers.get('Content-Type', ''),
    )
    return None


def _try_sharing_download(session: requests.Session, sharing_link: str) -> bytes | None:
    """Try ?download=1 on the sharing link."""
    if '?' in sharing_link:
        dl_url = sharing_link + "&download=1"
    else:
        dl_url = sharing_link + "?download=1"

    logger.info("Trying sharing link with download param...")

    resp = session.get(dl_url, timeout=30, allow_redirects=True)

    if resp.status_code == 200 and resp.content[:4] == b"%PDF":
        return resp.content

    logger.warning(
        "Sharing download param: status=%d, ct=%s",
        resp.status_code,
        resp.headers.get('Content-Type', ''),
    )
    return None


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

def parse_roster_pdf(pdf_bytes: bytes) -> list[dict]:
    """Parse the Cascade County inmate roster PDF into structured records."""
    records: list[dict] = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_idx, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            logger.info("Page %d: %d chars", page_idx, len(text))

            tables = page.extract_tables()
            if tables:
                for table in tables:
                    for row in table:
                        rec = _parse_row(row)
                        if rec:
                            records.append(rec)
            else:
                records.extend(_parse_text_lines(text))

    logger.info("Parsed %d records", len(records))
    return records


def _parse_row(row: list[str | None]) -> dict | None:
    cells = [c.strip() if c else "" for c in row]
    cells = [c for c in cells if c]
    if len(cells) < 2:
        return None

    name = cells[0]
    booking_date = ""
    charges: list[str] = []
    bail = ""
    status = ""

    for cell in cells[1:]:
        if re.match(r"\d{1,2}/\d{1,2}/\d{2,4}", cell):
            booking_date = cell
        elif re.search(r"\$\s?\d{1,3}(,\d{3})*(\.\d{2})?", cell):
            bail = cell
        elif any(kw in cell.lower() for kw in ["released", "custody", "held", "detention"]):
            status = cell
        else:
            charges.append(cell)

    return {
        "name": name,
        "booking_date": booking_date,
        "charges": charges,
        "bail": bail,
        "status": status,
    }


def _parse_text_lines(text: str) -> list[dict]:
    """Parse roster text line by line as fallback."""
    records: list[dict] = []
    lines = text.split("\n")
    current: dict | None = None

    for line in lines:
        line = line.strip()
        if not line:
            if current:
                records.append(current)
                current = None
            continue

        if _looks_like_name(line):
            if current:
                records.append(current)
            current = {
                "name": line,
                "booking_date": "",
                "charges": [],
                "bail": "",
                "status": "",
            }
        elif current:
            if re.match(r"\d{1,2}/\d{1,2}/\d{2,4}", line):
                current["booking_date"] = line
            elif re.search(r"\$\s?\d+", line):
                current["bail"] = line
            else:
                current["charges"].append(line)

    if current:
        records.append(current)
    return records


def _looks_like_name(line: str) -> bool:
    if len(line) > 60:
        return False
    if re.match(r"^[A-Z][A-Z'\-]+,\s*[A-Z][A-Z'\-]+", line):
        return True
    if re.match(r"^[A-Z][A-Z'\-]+\s+[A-Z][A-Z'\-]+", line):
        return True
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def fetch_cascade_roster(save_pdf_path: str | None = None) -> list[dict]:
    """Fetch and parse the Cascade County jail roster."""
    sharing_link = get_sharing_link()
    logger.info("Sharing link: %s", sharing_link[:100])

    pdf_bytes = download_roster_pdf(sharing_link)
    logger.info("Downloaded PDF: %d bytes", len(pdf_bytes))

    if save_pdf_path:
        with open(save_pdf_path, "wb") as f:
            f.write(pdf_bytes)
        logger.info("Saved PDF to %s", save_pdf_path)

    records = parse_roster_pdf(pdf_bytes)
    return records


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Cascade County Jail Roster Fetcher"
    )
    parser.add_argument(
        "--save-pdf", metavar="PATH",
        help="Save the raw PDF to this path",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show the sharing link and file path without downloading",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.dry_run:
        sharing_link = get_sharing_link()
        print(f"County page: {CASCADE_GOV_ROSTER_PAGE}")
        print(f"Sharing link: {sharing_link}")
        print(f"Known file path: {KNOWN_FILE_PATH}")
        return

    try:
        records = fetch_cascade_roster(save_pdf_path=args.save_pdf)

        if not records:
            print("No records found in the roster.")
            return

        print(f"\nFound {len(records)} inmate records:\n")
        print("-" * 80)

        for i, rec in enumerate(records, 1):
            print(f"\n[{i}] {rec.get('name', 'Unknown')}")
            if rec.get('booking_date'):
                print(f"    Booking: {rec['booking_date']}")
            if rec.get('charges'):
                print(f"    Charges: {', '.join(rec['charges'])}")
            if rec.get('bail'):
                print(f"    Bail: {rec['bail']}")
            if rec.get('status'):
                print(f"    Status: {rec['status']}")

        print("\n" + "-" * 80)
        print(f"Total: {len(records)} records")

    except Exception as e:
        logger.error("Failed: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()