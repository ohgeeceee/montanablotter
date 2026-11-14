"""dawson_inmate.py — Dawson County jail roster fetcher.

Dawson County publishes its roster as a date-stamped PDF linked from the
Dawson County Correctional Facility page:

    https://www.dawsonmt.gov/departments/dawson_county_correctional_facility/

The filename is ``DC JAIL ROSTER <MM-DD-YYYY>.pdf`` and both the date and the
``?t=`` cache-buster change on every publish, so the current PDF URL is
re-discovered from the landing page on each run.

PDF layout (extracted text, one block per inmate)::

    DCCF Inmate Roster Printed on November 13, 2026
    Last, First Name Inmate # Current Cell Booking In Custody Hold Reasons Bonds for
    ANDERSON, KELLY IN260246 107 07/30/26 15:07 Yes P&P hold (not 72) for Probation and
    $35000.00
    Parole Glendive; Probation Violation for
    Dawson County Sheriff's Office;
    ...

Each record starts with ``LAST, FIRST IN###### CELL MM/DD/YY HH:MM Yes``; the
bond amount lands on its own ``$`` line and the hold reasons wrap across as
many following lines as needed until the next record starts.
"""
from __future__ import annotations

import io
import logging
import re
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from services.ingestion.models import JailBookingRecord
from services.ingestion.roster_generic import RosterLinkUnavailable, fetch_url

logger = logging.getLogger(__name__)

FACILITY_NAME = "Dawson County Correctional Facility"
LANDING_PAGE = "https://www.dawsonmt.gov/departments/dawson_county_correctional_facility/"

# Any .pdf href on the landing page whose URL mentions the roster.
_PDF_LINK = re.compile(r'href=\s*["\']([^"\']*\.pdf[^"\']*)["\']', re.IGNORECASE)

# "ANDERSON, KELLY IN260246 107 07/30/26 15:07 Yes ..." / "ANDERSON-BATTLES, IN260318 111 ..."
_RECORD_START = re.compile(
    r"^(?P<name>.+?)\s+"
    r"(?P<inmate>IN\d{4,8})\s+"
    r"(?P<cell>\d{2,3})\s+"
    r"(?P<date>\d{1,2}/\d{1,2}/\d{2,4})\s+"
    r"(?P<time>\d{1,2}:\d{2})\s+"
    r"(?P<custody>Yes|No)\b\s*(?P<rest>.*)$"
)

_BOND_LINE = re.compile(r"^\$([\d,]+(?:\.\d{1,2})?)\s*$")
_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def _normalize_pdf_url(raw: str, base: str) -> str:
    """Turn a scraped href into a requestable URL.

    The Dawson filename contains literal spaces ("D C JAIL ROSTER ..."), so
    encode the path while leaving the query string intact.
    """
    raw = raw.strip()
    if raw.startswith("//"):
        raw = "https:" + raw
    if not raw.startswith("http"):
        raw = urljoin(base, raw)
    parts = urlsplit(raw)
    return urlunsplit((parts.scheme, parts.netloc, quote(parts.path, safe="/"), parts.query, parts.fragment))


def _discover_pdf_url(source_url: str) -> str:
    landing = fetch_url(source_url)
    candidates = []
    for match in _PDF_LINK.finditer(landing.text):
        url = _normalize_pdf_url(match.group(1), source_url)
        if "roster" in url.lower():
            candidates.append(url)
    if not candidates:
        raise RosterLinkUnavailable(
            "Dawson County landing page did not expose a roster PDF link; "
            "site layout may have changed."
        )

    # Prefer the most recently dated filename (MM-DD-YYYY) so a stale duplicate
    # link on the page does not win.
    def _date_key(url: str) -> tuple:
        m = re.search(r"(\d{1,2})-(\d{1,2})-(\d{4})", url)
        return (m.group(3), m.group(1), m.group(2)) if m else ("0000", "00", "00")

    candidates.sort(key=_date_key, reverse=True)
    logger.info("Dawson County discovered roster PDF: %s", candidates[0])
    return candidates[0]


def _normalize_name(raw: str) -> str:
    """Normalize the roster's 'LAST, FIRST [SUFFIX]' column.

    Some rows carry no first name at all ("ANDERSON-BATTLES,"), so a trailing
    comma is not a reliable 'first name follows' signal.
    """
    name = " ".join(raw.strip().rstrip(",").split())
    if "," in name:
        last, first = name.split(",", 1)
        first = " ".join(first.split())
        suffix = ""
        if first:
            parts = first.split()
            if parts and parts[-1].lower().rstrip(".") in _NAME_SUFFIXES:
                suffix = " " + parts.pop().upper()
            first = " ".join(parts)
        return f"{last.strip()}, {first}{suffix}".strip(", ").strip()
    parts = name.split()
    if len(parts) > 1 and parts[-1].lower().rstrip(".") in _NAME_SUFFIXES:
        return f"{' '.join(parts[:-1])}, {parts[-1].upper()}"
    return name


def _normalize_datetime(date_str: str, time_str: str) -> str | None:
    try:
        from datetime import datetime

        cleaned = f"{date_str.strip()} {time_str.strip()}"
        for fmt in ("%m/%d/%y %H:%M", "%m/%d/%Y %H:%M"):
            try:
                return datetime.strptime(cleaned, fmt).strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
    except Exception:
        pass
    return None


def _parse_dawson_text(text: str, *, source_url: str) -> list[JailBookingRecord]:
    lines = [ln.strip() for ln in text.splitlines()]
    records: list[JailBookingRecord] = []
    seen: set[str] = set()

    current: tuple[re.Match, list[str]] | None = None

    def flush(match: re.Match, body: list[str]) -> None:
        name = _normalize_name(match.group("name"))
        inmate = match.group("inmate")
        if not name or not inmate:
            return
        key = f"{inmate}|{name}"
        if key in seen:
            return
        seen.add(key)

        bond = ""
        reasons: list[str] = []
        for ln in body:
            bond_match = _BOND_LINE.match(ln)
            if bond_match and not bond:
                bond = bond_match.group(1)
                continue
            reasons.append(ln)
        charges = " ".join(part for part in reasons if part).strip()

        records.append(
            JailBookingRecord(
                source_record_id=f"dawson:{inmate}",
                person_name=name,
                age=None,
                booking_number=inmate,
                booking_at=_normalize_datetime(match.group("date"), match.group("time")),
                charges_summary=charges or f"Inmate listed on the official county roster. Bond ${bond}.",
                source_url=source_url,
            )
        )

    for ln in lines:
        match = _RECORD_START.match(ln)
        if match:
            if current is not None:
                flush(*current)
            current = (match, [match.group("rest")] if match.group("rest") else [])
        elif current is not None and ln:
            current[1].append(ln)

    if current is not None:
        flush(*current)

    logger.info("Dawson County roster: parsed %d record(s) from %s", len(records), source_url)
    return records


def fetch_dawson_bookings(roster_html_or_url: str | None = None) -> list[JailBookingRecord]:
    """Fetch and parse the current Dawson County jail roster."""
    landing = roster_html_or_url or LANDING_PAGE
    pdf_url = _discover_pdf_url(landing)

    resp = fetch_url(pdf_url)
    if not resp.content[:5].startswith(b"%PDF-"):
        raise RosterLinkUnavailable(
            f"Dawson County roster URL did not return a PDF "
            f"(got {resp.headers.get('Content-Type', '')[:40]})"
        )

    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("pdfplumber is required for the Dawson PDF adapter") from exc

    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        text = "\n".join((page.extract_text() or "") for page in pdf.pages)

    if not text.strip():
        raise RosterLinkUnavailable("Dawson County roster PDF contained no extractable text")

    return _parse_dawson_text(text, source_url=pdf_url)


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    for rec in fetch_dawson_bookings():
        print(rec)
