"""Helena Municipal Court active warrant list adapter.

The page at helenamt.gov/Departments/Municipal-Court/Arrest-Warrants-List
renders an alphabetically-organized <li> list of names inside id="main-content".
There are no charges or bond amounts — only names in LAST, FIRST format.
Single-letter alphabet navigation items are filtered out.
"""

from __future__ import annotations

import logging
import re

import requests

from services.ingestion.warrants.html_table import (
    normalize_person_name,
    slugify,
)
from services.ingestion.warrants.models import WarrantRecord

logger = logging.getLogger(__name__)

_SOURCE_URL = "https://www.helenamt.gov/Departments/Municipal-Court/Arrest-Warrants-List"
_COUNTY = "Lewis and Clark"
_CITY = "Helena"


def fetch_helena_warrants(session: requests.Session) -> list[WarrantRecord]:
    """Fetch warrant names from the Helena Municipal Court warrant list page."""
    try:
        resp = session.get(_SOURCE_URL, timeout=60)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Failed to fetch Helena warrant list: %s", exc)
        return []

    return _parse_helena_page(resp.text)


def _parse_helena_page(html: str) -> list[WarrantRecord]:
    # Isolate the main content section to avoid nav/sidebar noise
    idx = html.find('id="main-content"')
    if idx == -1:
        idx = 0
    # Clip at the footer to exclude social links and footer navigation
    footer_idx = html.find('class="footer', idx)
    if footer_idx == -1:
        footer_idx = html.find('<!--normalTemplateEnd-->', idx)
    section = html[idx:footer_idx] if footer_idx > idx else html[idx:]

    # Extract <li> content
    raw_items = re.findall(r"<li[^>]*>(.*?)</li>", section, re.S | re.I)

    records: list[WarrantRecord] = []
    seen: set[str] = set()

    # Common non-name words that appear in footer/nav list items
    _NON_NAME_WORDS = re.compile(
        r"\b(follow|watch|subscribe|youtube|twitter|instagram|facebook|linkedin|"
        r"quick|links|search|menu|home|contact|faq|skip|select|open|close|"
        r"accessibility|translate|english|language|court|municipal|department|"
        r"government|privacy|terms|sitemap|copyright|rights|reserved|news|"
        r"calendar|jobs|pay|online|services|residents|business|visitors|"
        r"welcome|about|staff|directory|forms|permits|license)\b",
        re.I,
    )

    for item in raw_items:
        # Strip inner HTML tags
        name = re.sub(r"<[^>]+>", "", item).strip()
        # Skip alphabet navigation entries (single letter or empty)
        if len(name) <= 2:
            continue
        # Skip items with non-name keywords
        if _NON_NAME_WORDS.search(name):
            continue
        # Must contain at least one letter sequence that looks like a name token
        if not re.search(r"[A-Za-z]{2,}", name):
            continue
        # Require it matches a person-name pattern: "LAST, FIRST" or "First Last"
        # A name has at least two word-tokens and no digits-only tokens
        tokens = name.split()
        if len(tokens) < 2:
            continue
        # Reject if any token is purely numeric (likely a case/ID number)
        if any(t.isdigit() for t in tokens):
            continue

        person_name = normalize_person_name(name)
        if len(person_name) < 4:
            continue

        slug = slugify(person_name)
        source_record_id = f"helena-muni-warrant:{slug}"
        if source_record_id in seen:
            counter = 2
            while f"{source_record_id}:{counter}" in seen:
                counter += 1
            source_record_id = f"{source_record_id}:{counter}"
        seen.add(source_record_id)

        records.append(
            WarrantRecord(
                source_record_id=source_record_id,
                county=_COUNTY,
                city=_CITY,
                person_name=person_name,
                charges_text="Active warrant",
                issued_by="Helena Municipal Court",
                source_url=_SOURCE_URL,
            )
        )

    logger.info("Parsed %d warrant record(s) from Helena Municipal Court", len(records))
    return records
