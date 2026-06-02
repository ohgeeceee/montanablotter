"""Sheridan County Sheriff active warrant list adapter.

Sheridan County embeds a publicly published Google Spreadsheet on their
sheriff page at sheridancountymt.gov/sheriff. The spreadsheet is published
as CSV and requires no authentication.

CSV columns: Date, Name, DOB, Age, Offense Desc
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime

import requests

from services.ingestion.warrants.html_table import normalize_person_name, slugify
from services.ingestion.warrants.models import WarrantRecord

logger = logging.getLogger(__name__)

_SHEET_ID = "2PACX-1vQlw38SAjL51_MMHG93iqUYkAK4-jZj0lsdgEkN_uqUn65i_7XC2n_FFji6m_G6uw"
_CSV_URL = f"https://docs.google.com/spreadsheets/d/e/{_SHEET_ID}/pub?output=csv"
_SOURCE_URL = "https://www.sheridancountymt.gov/sheriff"
_COUNTY = "Sheridan"


def fetch_sheridan_warrants(session: requests.Session) -> list[WarrantRecord]:
    """Fetch warrant records from Sheridan County's published Google Spreadsheet."""
    try:
        resp = session.get(_CSV_URL, timeout=45)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Failed to fetch Sheridan County warrant sheet: %s", exc)
        return []

    return _parse_sheridan_csv(resp.text)


def _normalise_date(raw: str) -> str:
    """Convert MM/DD/YYYY to YYYY-MM-DD, return raw string on failure."""
    raw = raw.strip()
    try:
        return datetime.strptime(raw, "%m/%d/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return raw


def _parse_sheridan_csv(csv_text: str) -> list[WarrantRecord]:
    records: list[WarrantRecord] = []
    seen: set[str] = set()

    reader = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        raw_name = (row.get("Name") or "").strip()
        if not raw_name:
            continue

        person_name = normalize_person_name(raw_name)
        if len(person_name) < 3:
            continue

        slug = slugify(person_name)
        source_record_id = f"sheridan-warrant:{slug}"
        if source_record_id in seen:
            counter = 2
            while f"{source_record_id}:{counter}" in seen:
                counter += 1
            source_record_id = f"{source_record_id}:{counter}"
        seen.add(source_record_id)

        issue_date = _normalise_date(row.get("Date") or "")
        dob = _normalise_date(row.get("DOB") or "")
        charges = (row.get("Offense Desc") or "Active warrant").strip()

        records.append(
            WarrantRecord(
                source_record_id=source_record_id,
                county=_COUNTY,
                person_name=person_name,
                dob=dob,
                charges_text=charges,
                issue_date=issue_date,
                issued_by="Sheridan County Sheriff",
                source_url=_SOURCE_URL,
            )
        )

    logger.info("Parsed %d warrant record(s) from Sheridan County", len(records))
    return records
