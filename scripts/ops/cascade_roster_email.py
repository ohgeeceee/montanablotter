#!/usr/bin/env python3
"""Download the Cascade County jail roster PDF from SharePoint and email it
to records@montanablotter.com.

Runs as a standalone daily job independent of the main jail-bookings ingest
pipeline.  The SharePoint link is scraped from the public Cascade County
inmate-roster page, so no extra authentication is required.

Usage:
    python3 scripts/ops/cascade_roster_email.py [--dry-run]
"""

import argparse
import logging
import os
import re
import smtplib
import sys
import tempfile
from datetime import date
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

# Project root must be on the path so we can import from services/.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(_PROJECT_ROOT))

from config import _load_env_file_defaults
_load_env_file_defaults(str(_PROJECT_ROOT / ".env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("cascade_roster_email")

# ---------------------------------------------------------------------------
# Fetch configuration
# ---------------------------------------------------------------------------
ROSTER_PAGE_URL = "https://www.cascadecountymt.gov/314/Inmate-Roster"
RECIPIENT = "records@montanablotter.com"
SENDER = os.environ.get("MB_SMTP_USER", "montanablotter@gmail.com")
SMTP_SERVER = os.environ.get("MB_SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("MB_SMTP_PORT", "587"))
SMTP_PASSWORD = os.environ.get("MB_SMTP_PASSWORD", "")
SUBJECT = "Cascade County Jail Roster - Daily Update"

# Headers that mimic a real browser.  Cascade County's server blocks plain
# requests and some CDNs reject the default urllib/requests user-agent.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Timeout for the HTTP round-trip (seconds).
_HTTP_TIMEOUT = 60

# Regex used by the main cascade_jail_roster fetcher to find the
# SharePoint document library link.
_SHARING_LINK_RE = re.compile(
    r"https://ccmtgov-my\.sharepoint\.com/:b:/g/personal/"
    r"jailroster[^'\"\s<>]+"
)

# ---------------------------------------------------------------------------
# SharePoint download helpers (mirrors cascade_jail_roster.py)
# ---------------------------------------------------------------------------
SHAREPOINT_TENANT = "ccmtgov-my.sharepoint.com"
SERVER_RELATIVE_PATH = (
    "/personal/jailroster_cascadecountymt_gov/"
    "Documents/Attachments/jailroster.pdf"
)

# Extra headers the REST API requires beyond the base _HEADERS.
_REST_EXTRA_HEADERS = {
    "Accept": "application/octet-stream",
    "X-HTTP-Method-Override": "GET",
}


def _make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(_HEADERS)
    return session


def _fetch_roster_page() -> str:
    """GET the Cascade inmate-roster page and return the HTML."""
    logger.info("Fetching Cascade roster page: %s", ROSTER_PAGE_URL)
    resp = requests.get(ROSTER_PAGE_URL, headers=_HEADERS, timeout=_HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def _extract_sharing_link(html: str) -> str | None:
    """Find the SharePoint sharing link using the same regex as the
    main cascade_jail_roster fetcher.

    Returns the sharing link as-is (e.g.
    https://ccmtgov-my.sharepoint.com/:b:/g/personal/jailroster_xxx/...)
    which the fetcher code itself uses to construct the final PDF URL.
    """
    m = _SHARING_LINK_RE.search(html)
    return m.group(0) if m else None


def _extract_sharepoint_pdf_url(html: str) -> str | None:
    """Find the SharePoint PDF link on the roster page.

    Cascade embeds a link whose href contains ``.pdf`` and typically points at
    the SharePoint document library.  We search for href values that end in
    ``.pdf`` (case-insensitive) and return the first match.
    """
    for match in re.finditer(r'href\s*=\s*["\']([^"\']+\.pdf[^"\']*)["\']', html, re.IGNORECASE):
        url = match.group(1).strip()
        if url:
            return url if url.startswith("http") else f"https://www.cascadecountymt.gov/{url}"
    for match in re.finditer(r'href\s*=\s*([^\s>]+?\.pdf[^\s>]*)', html, re.IGNORECASE):
        url = match.group(1).strip().rstrip("'\"")
        if url:
            return url if url.startswith("http") else f"https://www.cascadecountymt.gov/{url}"
    return None


def _try_download_via_onedrive(session: requests.Session, sharing_link: str) -> bytes | None:
    """Strategy 3 (new): Follow sharing-link redirect chain to the
    onedrive.aspx handler that actually serves the PDF.

    SharePoint redirects the sharing link to onedrive.aspx with an
    embedded file ID; that handler returns the binary PDF when the
    session carries the FedAuth cookie captured during the initial
    redirect.  The old ``?download=1`` trick only returns the
    HTML shell, so this is the path that works today.
    """
    # Re-fetch the sharing link WITHOUT following redirects so we
    # capture the 302 Location pointing at onedrive.aspx
    logger.info("Following sharing-link redirect chain")
    resp = session.get(sharing_link, timeout=30, allow_redirects=False)
    if resp.status_code != 302:
        logger.warning("Expected 302, got %d", resp.status_code)
        return None

    location = resp.headers.get("Location", "")
    if not location.startswith("https://") or "onedrive.aspx" not in location:
        logger.warning("Unexpected redirect: %s", location[:150])
        return None

    logger.info("onedrive redirect: %s", location[:150])
    # Follow the full redirect chain now (the FedAuth cookie is set)
    resp = session.get(location, timeout=30, allow_redirects=True)
    logger.info(
        "onedrive final: status=%d, ct=%s, len=%d",
        resp.status_code,
        resp.headers.get("Content-Type", ""),
        len(resp.content),
    )
    if resp.status_code == 200 and resp.content[:4] == b"%PDF":
        return resp.content
    logger.warning(
        "onedrive: not a PDF (head=%r)", resp.content[:40]
    )
    return None


def _try_sharing_download(session: requests.Session, sharing_link: str) -> bytes | None:
    """Strategy 4: sharing link + ?download=1."""
    dl_url = (
        f"{sharing_link}&download=1"
        if "?" in sharing_link
        else f"{sharing_link}?download=1"
    )
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


def _download_pdf(sharing_link: str) -> bytes:
    """Download the roster PDF using multiple fallback strategies.

    Mirrors cascade_jail_roster.py:download_roster_pdf().
    """
    session = _make_session()

    # Step 1: visit the sharing link to pick up FedAuth/rtFa cookies.
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

    # Strategy 1: Follow sharing-link redirect to onedrive.aspx.
    # SharePoint redirects the sharing link to onedrive.aspx with an
    # embedded file ID; that handler returns the binary PDF when the
    # session carries the FedAuth cookie captured during the initial
    # redirect.
    data = _try_download_via_onedrive(session, sharing_link)
    if data:
        logger.info("Onedrive download: %d bytes", len(data))
        return data

    # Strategy 2: sharing link + ?download=1
    data = _try_sharing_download(session, sharing_link)
    if data:
        logger.info("Sharing link download param: %d bytes", len(data))
        return data

    raise RuntimeError(
        "All download strategies failed. "
        "The sharing link appears expired or access-restricted. "
        "Re-extract the sharing link from the county page."
    )


def _email_pdf(pdf_bytes: bytes, filename: str) -> None:
    """Send the PDF as an attachment via SMTP."""
    if not SMTP_PASSWORD:
        raise RuntimeError(
            "MB_SMTP_PASSWORD is not set.  Export it from .env before running."
        )

    msg = MIMEMultipart()
    msg["From"] = SENDER
    msg["To"] = RECIPIENT
    msg["Subject"] = SUBJECT

    body = MIMEText(
        "Good morning,\n\n"
        "Attached is the daily Cascade County (Montana) jail roster PDF "
        "scraped from the SharePoint link on the county's inmate-roster page.\n\n"
        "— Montana Blotter",
        "plain",
    )
    msg.attach(body)

    part = MIMEApplication(pdf_bytes, Name=filename)
    part["Content-Disposition"] = f'attachment; filename="{filename}"'
    msg.attach(part)

    logger.info("Connecting to %s:%s to send email…", SMTP_SERVER, SMTP_PORT)
    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(SENDER, SMTP_PASSWORD)
        server.sendmail(SENDER, [RECIPIENT], msg.as_string())

    logger.info("Email sent to %s with attachment %s (%d bytes)", RECIPIENT, filename, len(pdf_bytes))


def run(dry_run: bool = False) -> None:
    """Execute the full fetch → email pipeline."""
    html = _fetch_roster_page()
    sharing_link = _extract_sharing_link(html)
    pdf_url = sharing_link or _extract_sharepoint_pdf_url(html)
    if not pdf_url:
        logger.error("No SharePoint link found on %s.  Aborting.", ROSTER_PAGE_URL)
        raise SystemExit(1)

    logger.info("Found SharePoint link: %s", pdf_url)
    pdf_bytes = _download_pdf(pdf_url)
    logger.info("Downloaded %d bytes", len(pdf_bytes))

    if dry_run:
        logger.info("DRY RUN — would email %d bytes to %s", len(pdf_bytes), RECIPIENT)
        return

    filename = f"cascade_jail_roster_{date.today().isoformat()}.pdf"
    _email_pdf(pdf_bytes, filename)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Fetch Cascade County jail roster and email it."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Download PDF but do not send email."
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run)
