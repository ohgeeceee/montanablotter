#!/usr/bin/env python3
"""
yellowstone_inmate.py
=====================
Fetches Yellowstone County jail roster and inmate charge details from their
sheriff website.  Integrates into the standard Montana Blotter ingestion
pipeline.
"""

from __future__ import annotations

import html
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable
from urllib.parse import urljoin

import requests

logger = logging.getLogger(__name__)

SHERIFF_URL = "https://www.yellowstonecountymt.gov/sheriff/"
DEFAULT_ROSTER_URL = "https://www.yellowstonecountymt.gov/Sheriff/Detention/dcsearch.asp"
YELLOWSTONE_CHARGE_LOOKBACK_DAYS = 7

_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}


@dataclass
class YellowstoneRecord:
    source_record_id: str
    person_name: str
    booking_number: str
    booking_at: str | None
    charges_summary: str
    source_url: str | None = None


def _parse_value(raw: str) -> int:
    token = raw.strip()
    if token.isdigit():
        return int(token)
    lowered = token.lower()
    if lowered not in _WORDS:
        raise RuntimeError(f"Unsupported Yellowstone prompt token: {token}")
    return _WORDS[lowered]


def _solve_prompt(page_html: str) -> str:
    match = re.search(r'<label for="Answer"[^>]*>([^<]+)</label>', page_html)
    if not match:
        raise RuntimeError("Yellowstone verification prompt not found")
    label = _text_from_html(match.group(1))
    expr = label.split("=")[0].strip()
    parts = expr.split()
    if len(parts) != 3:
        raise RuntimeError(f"Unexpected Yellowstone prompt: {label}")
    left = _parse_value(parts[0])
    op = parts[1]
    right = _parse_value(parts[2])
    if op == "+":
        return str(left + right)
    if op == "-":
        return str(left - right)
    raise RuntimeError(f"Unsupported Yellowstone operator: {op}")


def _text_from_html(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _normalize_datetime(value: str) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    raw = re.sub(r"\b(am|pm)\b", lambda match: match.group(1).upper(), raw, flags=re.IGNORECASE)
    candidates = (
        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%y %I:%M:%S %p",
        "%m/%d/%y %I:%M %p",
        "%m-%d-%Y - %I:%M:%S %p",
        "%m-%d-%Y - %I:%M %p",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%m/%d/%Y",
        "%Y-%m-%d",
    )
    for fmt in candidates:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return None


def _should_fetch_yellowstone_charge_detail(booking_at: str | None) -> bool:
    if not booking_at:
        return True
    try:
        booked_at = datetime.strptime(booking_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return True
    cutoff = datetime.now().replace(microsecond=0) - timedelta(days=YELLOWSTONE_CHARGE_LOOKBACK_DAYS)
    return booked_at >= cutoff


def _parse_full_roster(page_html: str) -> list[dict[str, str | None]]:
    """Parse the full-roster table (no detail links, 8 columns)."""
    table_match = re.search(
        r'<table class="table table-striped _table-sm caption-top data-table">(.*?)</table>',
        page_html,
        re.IGNORECASE | re.DOTALL,
    )
    if not table_match:
        return []
    rows: list[dict[str, str | None]] = []
    for row_html in re.findall(r"<tr>(.*?)</tr>", table_match.group(1), re.IGNORECASE | re.DOTALL):
        cell_matches = re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.IGNORECASE | re.DOTALL)
        if len(cell_matches) != 8:
            continue
        rows.append(
            {
                "last_name": _text_from_html(cell_matches[0]),
                "first_name": _text_from_html(cell_matches[1]),
                "middle_name": _text_from_html(cell_matches[2]),
                "jacket_number": _text_from_html(cell_matches[3]),
                "housing_unit": _text_from_html(cell_matches[4]),
                "total_bond": _text_from_html(cell_matches[5]),
                "booking_date": _text_from_html(cell_matches[6]),
                "date_of_birth": _text_from_html(cell_matches[7]),
            }
        )
    return rows


def _parse_search_result(page_html: str, base_url: str) -> list[dict[str, str | None]]:
    """Parse a name-search result table (has detail links, 7 columns)."""
    table_match = re.search(
        r'<table class="table table-striped table-sm caption-top data-table">(.*?)</table>',
        page_html,
        re.IGNORECASE | re.DOTALL,
    )
    if not table_match:
        return []
    rows: list[dict[str, str | None]] = []
    for row_html in re.findall(r"<tr>(.*?)</tr>", table_match.group(1), re.IGNORECASE | re.DOTALL):
        cell_matches = re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.IGNORECASE | re.DOTALL)
        if len(cell_matches) != 7:
            continue
        detail_match = re.search(
            r'href="([^"]*inmatedet\.asp\?[^"]+)"',
            cell_matches[0],
            re.IGNORECASE,
        )
        rows.append(
            {
                "person_name": _text_from_html(cell_matches[0]),
                "jacket_number": _text_from_html(cell_matches[1]),
                "housing_unit": _text_from_html(cell_matches[2]),
                "detail_url": urljoin(base_url + "/", detail_match.group(1))
                if detail_match
                else None,
                "total_bond": _text_from_html(cell_matches[4]),
                "booking_date": _text_from_html(cell_matches[5]),
                "date_of_birth": _text_from_html(cell_matches[6]),
            }
        )
    return rows


def _parse_charges(page_html: str) -> str:
    table_match = re.search(
        r'<table class="table table-striped text-center data-table">(.*?)</table>',
        page_html,
        re.IGNORECASE | re.DOTALL,
    )
    if not table_match:
        return "Charge details available on the official Yellowstone County inmate page."
    summaries: list[str] = []
    for row_html in re.findall(r"<tr>(.*?)</tr>", table_match.group(1), re.IGNORECASE | re.DOTALL):
        cell_matches = re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.IGNORECASE | re.DOTALL)
        if len(cell_matches) != 5:
            continue
        charge_type = _text_from_html(cell_matches[2])
        charge = _text_from_html(cell_matches[3])
        bond_amount = _text_from_html(cell_matches[4])
        parts = [charge]
        if charge_type:
            parts.append(charge_type)
        if bond_amount:
            parts.append(f"Bond {bond_amount}")
        summaries.append(" | ".join([part for part in parts if part]))
    if not summaries:
        return "Charge details available on the official Yellowstone County inmate page."
    return "; ".join(summaries[:3])


def discover_roster_url(sheriff_url: str = SHERIFF_URL) -> str | None:
    """Discover the detention roster URL from the sheriff homepage."""
    resp = requests.get(
        sheriff_url,
        timeout=30,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://montanablotter.com)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    resp.raise_for_status()
    for match in re.findall(
        r'href="([^"]*(?:detention|jail|roster|inmate)[^"]*)"',
        resp.text,
        re.IGNORECASE,
    ):
        href = match.strip()
        if "dcsearch.asp" in href.lower():
            return urljoin(sheriff_url, href)
    return None


def fetch_bookings(
    source_url: str | None = None,
    *,
    fetch_charges: bool = True,
    max_charge_lookups: int = 0,
    request_delay_seconds: float = 0.15,
) -> list[YellowstoneRecord]:
    """
    Fetch Yellowstone County jail bookings.

    Args:
        source_url: The roster URL.  If *None*, the URL is discovered from the
            sheriff homepage.
        fetch_charges: Whether to look up individual charge details.
        max_charge_lookups: Maximum number of inmates to fetch charges for
            (``0`` means no limit).
        request_delay_seconds: Sleep time between charge-lookup requests to
            avoid hammering the server.
    """
    roster_url = source_url or discover_roster_url()
    if not roster_url:
        raise RuntimeError("Could not discover Yellowstone roster URL")

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://montanablotter.com)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": roster_url,
        }
    )

    page_html = session.get(roster_url, timeout=45).text
    answer = _solve_prompt(page_html)

    response = session.post(
        roster_url,
        data={
            "ViewFullRoster": "True",
            "Answer": answer,
            "action": "Search",
        },
        timeout=45,
    )
    response.raise_for_status()
    roster_rows = _parse_full_roster(response.text)

    if not roster_rows:
        logger.warning("No Yellowstone roster rows found")
        return []

    records: list[YellowstoneRecord] = []
    base_path = roster_url.rsplit("/", 1)[0]

    for idx, row in enumerate(roster_rows):
        last_name = (row.get("last_name") or "").strip()
        first_name = (row.get("first_name") or "").strip()
        middle_name = (row.get("middle_name") or "").strip()
        person_name = ", ".join(
            [
                last_name.title(),
                " ".join(
                    part.title()
                    for part in [first_name, middle_name]
                    if part
                ),
            ]
        )
        booking_number = (row.get("jacket_number") or "").strip()
        booking_at = _normalize_datetime(f"{row.get('booking_date') or ''} 12:00 AM")
        detail_url: str | None = None
        charge_summary = (
            "Charge details available on the official Yellowstone County inmate page."
        )

        if fetch_charges and (max_charge_lookups == 0 or idx < max_charge_lookups):
            if _should_fetch_yellowstone_charge_detail(booking_at):
                try:
                    search_name = f"{last_name}, {first_name}"
                    if middle_name:
                        search_name += f" {middle_name}"
                    search_resp = session.post(
                        roster_url,
                        data={
                            "ViewFullRoster": "",
                            "InmateName": search_name,
                            "BookingDate": "",
                            "action": "Search",
                        },
                        timeout=45,
                    )
                    search_resp.raise_for_status()
                    search_results = _parse_search_result(search_resp.text, base_path)
                    if search_results:
                        detail_url = search_results[0].get("detail_url")
                        if detail_url:
                            detail_resp = session.get(detail_url, timeout=45)
                            detail_resp.raise_for_status()
                            charge_summary = _parse_charges(detail_resp.text)
                            booknum_match = re.search(r"Booknum=([^&]+)", detail_url)
                            if booknum_match:
                                booking_number = booknum_match.group(1)
                except Exception as exc:
                    logger.warning(
                        "Charge lookup failed for %s: %s", person_name, exc
                    )

        records.append(
            YellowstoneRecord(
                source_record_id=(
                    detail_url
                    if detail_url
                    else f"yellowstone:{booking_number or person_name.lower().replace(' ', '-')}"
                ),
                person_name=person_name.strip(),
                booking_number=booking_number,
                booking_at=booking_at,
                charges_summary=charge_summary,
                source_url=detail_url or roster_url,
            )
        )

        if fetch_charges and (max_charge_lookups == 0 or idx < max_charge_lookups):
            time.sleep(request_delay_seconds)

    return records
