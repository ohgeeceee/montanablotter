"""Parse OpenCities-style warrant lists rendered as HTML ``<li>`` items."""

from __future__ import annotations

import logging
import re

from services.ingestion.warrants.html_table import normalize_person_name, slugify
from services.ingestion.warrants.models import WarrantRecord

logger = logging.getLogger(__name__)

_LI_RE = re.compile(r"<li>([^<]+)</li>", re.IGNORECASE)
_NAV_NOISE = {
    "home",
    "contact us",
    "search",
    "menu",
    "skip to content",
}


def looks_like_warrant_name(value: str) -> bool:
    value = value.strip()
    if not value or len(value) > 80:
        return False
    if value.lower() in _NAV_NOISE:
        return False
    if "," not in value:
        return False
    last, first = value.split(",", 1)
    last = last.strip()
    first = first.strip()
    if not last or not first:
        return False
    if not re.match(r"^[A-Za-z][A-Za-z' \-.]+$", last):
        return False
    if not re.match(r"^[A-Za-z][A-Za-z' \-.]+$", first):
        return False
    return True


def parse_li_warrant_list(
    page_html: str,
    *,
    county: str,
    source_url: str,
    source_prefix: str,
    city: str = "",
    issued_by: str = "",
) -> list[WarrantRecord]:
    """Extract warrant records from ``<li>Last, First</li>`` list markup."""
    records: list[WarrantRecord] = []
    seen: set[str] = set()

    for raw in _LI_RE.findall(page_html or ""):
        name = raw.strip()
        if not looks_like_warrant_name(name):
            continue

        person_name = normalize_person_name(name)
        slug = slugify(person_name)
        source_record_id = f"{source_prefix}:{slug}"
        if source_record_id in seen:
            counter = 2
            while f"{source_record_id}:{counter}" in seen:
                counter += 1
            source_record_id = f"{source_record_id}:{counter}"
        seen.add(source_record_id)

        records.append(
            WarrantRecord(
                source_record_id=source_record_id,
                county=county,
                city=city,
                person_name=person_name,
                charges_text="Active warrant",
                issued_by=issued_by,
                source_url=source_url,
            )
        )

    logger.info("Parsed %d warrant record(s) from li list at %s", len(records), source_url)
    return records
