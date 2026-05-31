#!/usr/bin/env python3
"""
flathead_inmate.py
==================
Fetches the Flathead County Detention Center current inmate roster from
https://apps.flathead.mt.gov/jailroster/ and parses it into booking records.

Integrates into the standard Montana Blotter jail_bookings.py pipeline.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://apps.flathead.mt.gov/jailroster/"
DEFAULT_SOURCE_URL = BASE_URL


@dataclass(frozen=True)
class FlatheadBookingRecord:
    source_record_id: str
    person_name: str
    age: int | None
    booking_number: str
    booking_at: str | None
    charges_summary: str
    source_url: str | None = None


class _RosterTextExtractor(HTMLParser):
    """Convert HTML into a newline-friendly plain-text stream."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"br", "hr"}:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"p", "div", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if data:
            self._parts.append(data)

    def text(self) -> str:
        text = html.unescape("".join(self._parts))
        text = text.replace("\xa0", " ")
        text = re.sub(r"\r", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text


def _text_from_html(fragment: str) -> str:
    parser = _RosterTextExtractor()
    parser.feed(fragment or "")
    text = parser.text()
    return re.sub(r"\s+", " ", text).strip()


def _fetch_html(url: str, *, timeout: int = 45) -> str:
    response = requests.get(
        url,
        timeout=timeout,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://montanablotter.com)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    response.raise_for_status()
    return response.text


def _parse_flathead_roster(page_html: str, source_url: str) -> list[FlatheadBookingRecord]:
    records: list[FlatheadBookingRecord] = []
    entry_matches = re.findall(
        r'<div class="inmate-entry">\s*(.*?)<div class="inmate-entry-footer"></div>\s*</div>',
        page_html,
        re.IGNORECASE | re.DOTALL,
    )

    def extract_stat(entry_html: str, label: str) -> str:
        match = re.search(
            rf'<div class="inmate-stat">\s*<h6>\s*{re.escape(label)}:\s*</h6>\s*<p>(.*?)</p>\s*</div>',
            entry_html,
            re.IGNORECASE | re.DOTALL,
        )
        return _text_from_html(match.group(1)) if match else ""

    for entry_html in entry_matches:
        name_match = re.search(
            r'<div class="inmate-name">\s*<h2[^>]*>(.*?)</h2>',
            entry_html,
            re.IGNORECASE | re.DOTALL,
        )
        if not name_match:
            continue

        raw_name = _text_from_html(name_match.group(1))
        if "," in raw_name:
            last_name, first_name = [part.strip() for part in raw_name.split(",", 1)]
            person_name = f"{last_name.title()}, {first_name.title()}"
        else:
            person_name = raw_name.title()

        age_value = extract_stat(entry_html, "Age")
        age = int(age_value) if age_value.isdigit() else None

        mugshot_match = re.search(
            r"url\('images/inmates/([^']+)'\)",
            entry_html,
            re.IGNORECASE,
        )
        booking_number = ""
        if mugshot_match:
            booking_number = re.sub(r"\.[A-Za-z0-9]+$", "", mugshot_match.group(1)).strip()

        pin_value = extract_stat(entry_html, "PIN")
        if not booking_number:
            booking_number = pin_value

        charge_matches = re.findall(
            r'<p[^>]*class="disposition-description[^"]*"[^>]*>(.*?)</p>',
            entry_html,
            re.IGNORECASE | re.DOTALL,
        )
        charges = [_text_from_html(match) for match in charge_matches if _text_from_html(match)]
        charges_summary = (
            "; ".join(charges[:5])
            if charges
            else "Charge details available on the official Flathead County inmate page."
        )

        source_record_id = booking_number or pin_value or person_name.lower().replace(" ", "-")
        records.append(
            FlatheadBookingRecord(
                source_record_id=source_record_id,
                person_name=person_name,
                age=age,
                booking_number=booking_number or pin_value,
                booking_at=None,
                charges_summary=charges_summary,
                source_url=source_url,
            )
        )

    return records


def fetch_flathead_bookings(source_url: str | None = None) -> list:
    """Fetch current Flathead County inmate roster and return parsed records."""
    url = (source_url or DEFAULT_SOURCE_URL).rstrip("/") + "/?report=inmates&sort=lastname"
    page_html = _fetch_html(url)
    return _parse_flathead_roster(page_html, source_url or DEFAULT_SOURCE_URL)
