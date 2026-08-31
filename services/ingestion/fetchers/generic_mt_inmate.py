"""Generic Montana county jail-roster fetcher.

Many small Montana county sheriff sites publish an inmate list as either an
HTML table (or name/charge list) or a downloadable PDF.  This module provides a
tolerant parser that extracts ``LAST, FIRST`` style inmate rows from either
format and returns :class:`JailBookingRecord` objects.

It is intentionally conservative: it only emits a record when it finds a name
that looks like ``LAST, FIRST`` (or ``First Last``) plus at least one of
booking date / charge text.  Counties whose page is only a department landing
page (no roster data) will simply return an empty list, which the orchestrator
records as a successful-but-empty run rather than inventing data.
"""
from __future__ import annotations

import io
import logging
import re
from typing import Iterable
from urllib.parse import urljoin

import requests

from services.ingestion.models import JailBookingRecord

logger = logging.getLogger(__name__)

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
_TIMEOUT = 60

# A "LAST, FIRST ..." or "Last First" name fragment.
_NAME_LAST_FIRST = re.compile(r"\b([A-Z][A-Za-z'.-]+(?:\s[A-Z][A-Za-z'.-]+)*),\s+([A-Z][A-Za-z'.-]+(?:\s[A-Z][A-Za-z'.-]+)*)\b")
_NAME_FIRST_LAST = re.compile(r"\b([A-Z][A-Za-z'.-]+)\s+([A-Z][A-Za-z'.-]+(?:\s[A-Z][A-Za-z'.-]+)*)\b")
_DATE = re.compile(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b")
_BOOKING_LABEL = re.compile(r"booking", re.IGNORECASE)
_CHARGE_LABEL = re.compile(r"charge|offense|held for", re.IGNORECASE)


def _get(url: str) -> requests.Response:
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp


def _normalize_datetime(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip()
    fmts = ("%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d", "%m/%d/%y", "%m-%d-%y")
    for fmt in fmts:
        try:
            from datetime import datetime

            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return None


def _visible_text(html: str) -> str:
    """Strip tags/scripts/styles and return readable text."""
    html = re.sub(r"(?is)<(script|style|head|noscript).*?</\1>", " ", html)
    html = re.sub(r"(?is)<!--.*?-->", " ", html)
    html = re.sub(r"(?is)<[^>]+>", " ", html)
    html = re.sub(r"&nbsp;", " ", html)
    html = re.sub(r"&amp;", "&", html)
    html = re.sub(r"\s+", " ", html)
    return html


def _extract_from_text(text: str, source_url: str, county_slug: str) -> list[JailBookingRecord]:
    """Extract inmate rows from roster text.

    Strict by design: a line only becomes an inmate when it carries a clear
    ``LAST, FIRST`` name AND is adjacent to a booking date or charge text.
    Plain ``City, ST`` addresses / navigation strings on sheriff landing pages
    are rejected so we never invent inmates from menu text.
    """
    # Words that indicate the match is site chrome, not a person.
    _NAV = re.compile(
        r"\b(home|about|contact|services|employment|opportunities|departments|airport|"
        r"county|montana|sheriff|records|request|forms|faq|tip|hotline|board|"
        r"vision|dental|housing|veterans|respite|assisted|low income|crime|"
        r"exploited|children|index|follow|path|value|width|initial|layout|"
        r"document|window|img|smiley|post|enter|version|sec|letters|numbers|"
        r"ofobj|internal|linkurl|target|prop|signed|dated|praecipe|name)\b",
        re.IGNORECASE,
    )
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    records: list[JailBookingRecord] = []
    seen: set[str] = set()

    for idx, line in enumerate(lines):
        # Require the "LAST, FIRST" comma form — landing-page "City, ST" pairs
        # without a following charge/booking context are dropped below.
        m = _NAME_LAST_FIRST.search(line)
        if not m:
            continue
        last, first = m.group(1).strip().title(), m.group(2).strip().title()
        # Drop a trailing " Age" / " Age NN" token some PDFs append to the name.
        first = re.sub(r"\s+Age(?:\s+\d+)?$", "", first, flags=re.IGNORECASE).strip()
        if len(last) < 2 or len(first) < 2:
            continue
        # Reject if either token is a known navigation word.
        if _NAV.search(last) or _NAV.search(first):
            continue

        context = line + " " + " ".join(lines[idx + 1 : idx + 4])
        date_m = _DATE.search(context)
        charge_m = re.search(
            r"(?:charge[s]?|offense|held for|booking)\s*:?\s*(.+?)(?:\s+bond\s|$)",
            context,
            re.IGNORECASE,
        )
        # Need at least a booking date OR a charge label to accept the row.
        if not date_m and not charge_m:
            continue

        person_name = f"{last}, {first}"
        key = f"{county_slug}:{person_name.lower().replace(' ', '-')}"
        if key in seen:
            continue

        booking_at = _normalize_datetime(date_m.group(1)) if date_m else None
        charges = charge_m.group(1).strip().rstrip(";, ")[:300] if charge_m else ""
        # Trim a charge string that bled into the next inmate's row.
        charges = re.split(r"\s{2,}[A-Z][A-Za-z.'-]+,\s", charges)[0].strip() if charges else ""
        charges_summary = charges or "Inmate listed on the official county roster."

        seen.add(key)
        records.append(
            JailBookingRecord(
                source_record_id=key,
                person_name=person_name,
                age=None,
                booking_number="",
                booking_at=booking_at,
                charges_summary=charges_summary,
                source_url=source_url,
            )
        )
    return records


def _extract_pdf(bytes_content: bytes, source_url: str, county_slug: str) -> list[JailBookingRecord]:
    try:
        import pdfplumber
    except Exception as exc:  # pragma: no cover
        logger.warning("pdfplumber unavailable: %s", exc)
        return []
    with pdfplumber.open(io.BytesIO(bytes_content)) as pdf:
        text = "\n".join(page.extract_text(x_tolerance=2, y_tolerance=3) or "" for page in pdf.pages)
    return _extract_from_text(text, source_url, county_slug)


def fetch_generic_mt_bookings(source_url: str, *, county_slug: str = "") -> list[JailBookingRecord]:
    """Fetch a county roster URL (HTML or PDF) and parse inmate rows.

    Args:
        source_url: The roster/sheriff page URL.
        county_slug: Slug used to namespace ``source_record_id`` values.
    """
    resp = _get(source_url)
    content = resp.content
    if content[:4] == b"%PDF" or "pdf" in (resp.headers.get("Content-Type") or "").lower():
        return _extract_pdf(content, source_url, county_slug)
    return _extract_from_text(_visible_text(resp.text), source_url, county_slug)
