"""
jail_booking_ingest.py
======================

Fetch and sync current jail roster entries into the Montana Blotter
jail-bookings tables.

This first pass automates Missoula County, where the current inmate roster is
publicly accessible as server-rendered HTML. Other large counties are kept in
the source registry and reported as unsupported or temporarily unavailable
until county-specific adapters are added.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import io
import json
import logging
import os
import re
import sqlite3
import string
import sys
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Iterable
from urllib.parse import parse_qs, urljoin, urlparse

import requests
import urllib3

import pdfplumber

sys.path.insert(0, "/root/montanablotter")
import config
from db import timed_db_transaction
from services.alerts.bail_bonds import dispatch_felony_booking_alerts, dispatch_telegram_booking_alerts
from services.ingestion.models import JailBookingRecord
from services.ingestion.fetchers.flathead_inmate import (
    fetch_flathead_bookings,
    _parse_flathead_roster,
)
from services.ingestion.fetchers.rosebud_inmate import fetch_rosebud_bookings
from services.ingestion.fetchers.custer_inmate import fetch_custer_bookings
from services.ingestion.fetchers.lewis_clark_inmate import fetch_lewis_clark_bookings
from services.ingestion.fetchers.lincoln_inmate import fetch_lincoln_bookings
from services.ingestion.fetchers.silver_bow_inmate import fetch_silver_bow_bookings
from services.ingestion.fetchers.yellowstone_inmate import fetch_bookings as _fetch_yellowstone_bookings_raw
from services.ingestion.fetchers.public_roster_inmate import (
    fetch_big_horn_bookings,
    fetch_fallon_bookings,
    fetch_fergus_bookings,
    fetch_glacier_bookings,
    fetch_roosevelt_bookings,
)

logger = logging.getLogger(__name__)

DB_TIMEOUT_SECONDS = float(getattr(config, "DB_TIMEOUT_SECONDS", 60))
DB_BUSY_TIMEOUT_MS = int(getattr(config, "DB_BUSY_TIMEOUT_MS", 60000))
DB_LOCK_RETRY_ATTEMPTS = int(getattr(config, "DB_LOCK_RETRY_ATTEMPTS", 5))
DB_LOCK_RETRY_SLEEP_SECONDS = float(getattr(config, "DB_LOCK_RETRY_SLEEP_SECONDS", 3.0))
PUBLISHER_PAYLOAD_PATH = str(getattr(config, "NEXTJS_JAIL_BOOKING_PAYLOAD_PATH", "") or "").strip()

SUPPORTED_ADAPTERS = {"beaverhead", "big-horn", "broadwater", "cascade", "carbon", "custer", "dawson", "fallon", "fergus", "flathead", "gallatin", "glacier", "granite", "jefferson", "lake", "lewis-and-clark", "lincoln", "madison", "meagher", "mineral", "missoula", "park", "phillips", "pondera", "powell", "ravalli", "roosevelt", "rosebud", "sanders", "silver-bow", "stillwater", "valley", "wheatland", "yellowstone"}
SKIPPED_SOURCES = {
    "broadwater": "Site times out from ingest machine (TCP connect to 34.94.199.155:443 fails; both HTTP and HTTPS). Parser ready — re-enable when network path recovers.",
}

ZUERCHER_COUNTIES = frozenset({
    "jefferson", "ravalli", "madison", "carbon",
    "stillwater", "meagher", "wheatland", "roosevelt",
    "gallatin",
})
TRACKED_SOURCES = {
    "yellowstone": {
        "county_name": "Yellowstone",
        "facility_name": "Yellowstone County Detention Facility",
        "roster_url": "https://www.yellowstonecountymt.gov/sheriff/detention/dcsearch.asp",
        "phone": "406-256-2929",
        "coverage_tier": "major",
        "is_featured": 1,
    },
    "missoula": {
        "county_name": "Missoula",
        "facility_name": "Missoula County Detention Facility",
        "roster_url": "https://webapps.missoulacounty.us/jailroster/Inmates",
        "phone": "406-258-4780",
        "coverage_tier": "major",
        "is_featured": 1,
    },
    "gallatin": {
        "county_name": "Gallatin",
        "facility_name": "Gallatin County Detention Center",
        "roster_url": "https://gallatin-so-mt.zuercherportal.com/#/inmates",
        "phone": "406-582-2100",
        "coverage_tier": "major",
        "is_featured": 1,
    },
    "hill": {
        "county_name": "Hill",
        "facility_name": "Hill County Detention Center",
        "roster_url": "https://hillso.org",
        "phone": "406-265-5481",
        "coverage_tier": "major",
        "is_featured": 1,
    },
    "flathead": {
        "county_name": "Flathead",
        "facility_name": "Flathead County Detention Center",
        "roster_url": "https://apps.flathead.mt.gov/jailroster/",
        "phone": "406-758-5610",
        "coverage_tier": "major",
        "is_featured": 1,
    },
    "lake": {
        "county_name": "Lake",
        "facility_name": "Lake County Detention Center",
        "roster_url": "https://www.lakemt.gov/DocumentCenter/View/816/Jail_Roster-?bidId=",
        "phone": "406-883-7301",
        "coverage_tier": "major",
        "is_featured": 1,
    },
    "cascade": {
        "county_name": "Cascade",
        "facility_name": "Cascade County Detention Center",
        "roster_url": "https://www.cascadecountymt.gov/314/Inmate-Roster",
        "phone": "406-454-6840",
        "coverage_tier": "major",
        "is_featured": 1,
    },
    "jefferson": {
        "county_name": "Jefferson",
        "facility_name": "Jefferson County Detention Center",
        "roster_url": "https://jefferson-so-mt.zuercherportal.com/#/inmates",
        "phone": None,
        "coverage_tier": "standard",
        "is_featured": 0,
    },
    "sanders": {
        "county_name": "Sanders",
        "facility_name": "Sanders County Jail",
        "roster_url": "https://www.sanderscountymt.gov",
        "phone": None,
        "coverage_tier": "standard",
        "is_featured": 0,
    },
    "ravalli": {
        "county_name": "Ravalli",
        "facility_name": "Ravalli County Detention Center",
        "roster_url": "https://ravalli-so-mt.zuercherportal.com/#/inmates",
        "phone": None,
        "coverage_tier": "standard",
        "is_featured": 0,
    },
    "rosebud": {
        "county_name": "Rosebud",
        "facility_name": "Rosebud County Detention Center",
        "roster_url": "https://www.rosebudcountymt.gov/sheriff",
        "phone": None,
        "coverage_tier": "standard",
        "is_featured": 0,
    },
    "madison": {
        "county_name": "Madison",
        "facility_name": "Madison County Detention Center",
        "roster_url": "https://madison-so-mt.zuercherportal.com/#/inmates",
        "phone": None,
        "coverage_tier": "standard",
        "is_featured": 0,
    },
    "carbon": {
        "county_name": "Carbon",
        "facility_name": "Carbon County Detention Center",
        "roster_url": "https://carbon-so-mt.zuercherportal.com/#/inmates",
        "phone": None,
        "coverage_tier": "standard",
        "is_featured": 0,
    },
    "stillwater": {
        "county_name": "Stillwater",
        "facility_name": "Stillwater County Detention Center",
        "roster_url": "https://stillwater-so-mt.zuercherportal.com/#/inmates",
        "phone": None,
        "coverage_tier": "standard",
        "is_featured": 0,
    },
    "meagher": {
        "county_name": "Meagher",
        "facility_name": "Meagher County Detention Center",
        "roster_url": "https://meagher-so-mt.zuercherportal.com/#/inmates",
        "phone": None,
        "coverage_tier": "standard",
        "is_featured": 0,
    },
    "wheatland": {
        "county_name": "Wheatland",
        "facility_name": "Wheatland County Detention Center",
        "roster_url": "https://wheatland-so-mt.zuercherportal.com/#/inmates",
        "phone": None,
        "coverage_tier": "standard",
        "is_featured": 0,
    },
    "valley": {
        "county_name": "Valley",
        "facility_name": "Valley County Detention Center",
        "roster_url": "https://www.valleycountymt.gov/1288/Jail-Roster",
        "phone": None,
        "coverage_tier": "standard",
        "is_featured": 0,
    },
    "broadwater": {
        "county_name": "Broadwater",
        "facility_name": "Broadwater County Detention Center",
        "roster_url": "https://www.broadwatercountysheriff.org/roster.php",
        "phone": "406-266-3441",
        "coverage_tier": "standard",
        "is_featured": 0,
    },
    "custer": {
        "county_name": "Custer",
        "facility_name": "Custer County Detention Facility",
        "roster_url": "https://custercountymt.gov/emergency-enforcement/sheriff/",
        "phone": "406-874-3320",
        "coverage_tier": "standard",
        "is_featured": 0,
    },
    "lewis_clark": {
        "county_name": "Lewis and Clark",
        "facility_name": "Lewis and Clark County Detention Center",
        "roster_url": "https://www.lccountymt.gov/Sheriff/Detention-Center",
        "phone": "406-447-8235",
        "coverage_tier": "major",
        "is_featured": 1,
    },
    "lincoln": {
        "county_name": "Lincoln",
        "facility_name": "Lincoln County Detention Center",
        "roster_url": "https://lincolncountymt.us/sheriff-home/detention/",
        "phone": "406-283-2447",
        "coverage_tier": "standard",
        "is_featured": 0,
    },
    "beaverhead": {
        "county_name": "Beaverhead",
        "facility_name": "Beaverhead County Detention Center",
        "roster_url": "https://beaverheadcountymt.gov/wp-content/uploads/2026/03/Jail-Roster.pdf",
        "phone": None,
        "coverage_tier": "standard",
        "is_featured": 0,
    },
    "big-horn": {
        "county_name": "Big Horn",
        "facility_name": "Big Horn County Detention Center",
        "roster_url": "https://www.bighorncountymt.gov/239/Detention",
        "phone": None,
        "coverage_tier": "standard",
        "is_featured": 0,
    },
    "fallon": {
        "county_name": "Fallon",
        "facility_name": "Fallon County Detention Center",
        "roster_url": "https://falloncountymt.gov/sheriff",
        "phone": None,
        "coverage_tier": "standard",
        "is_featured": 0,
    },
    "dawson": {
        "county_name": "Dawson",
        "facility_name": "Dawson County Detention Center",
        "roster_url": "https://www.co.dawson.mt.us",
        "phone": None,
        "coverage_tier": "standard",
        "is_featured": 0,
    },
    "granite": {
        "county_name": "Granite",
        "facility_name": "Granite County Detention Center",
        "roster_url": "https://www.co.granite.mt.us",
        "phone": None,
        "coverage_tier": "standard",
        "is_featured": 0,
    },
    "mineral": {
        "county_name": "Mineral",
        "facility_name": "Mineral County Detention Center",
        "roster_url": "https://www.co.mineral.mt.us",
        "phone": None,
        "coverage_tier": "standard",
        "is_featured": 0,
    },
    "phillips": {
        "county_name": "Phillips",
        "facility_name": "Phillips County Detention Center",
        "roster_url": "https://www.phillipscosheriff.com",
        "phone": None,
        "coverage_tier": "standard",
        "is_featured": 0,
    },
    "pondera": {
        "county_name": "Pondera",
        "facility_name": "Pondera County Detention Center",
        "roster_url": "https://www.co.pondera.mt.us",
        "phone": None,
        "coverage_tier": "standard",
        "is_featured": 0,
    },
    "powell": {
        "county_name": "Powell",
        "facility_name": "Powell County Detention Center",
        "roster_url": "https://www.co.powell.mt.us",
        "phone": None,
        "coverage_tier": "standard",
        "is_featured": 0,
    },
    "fergus": {
        "county_name": "Fergus",
        "facility_name": "Fergus County Detention Center",
        "roster_url": "https://fergusmt.gov/detention-center-roster",
        "phone": None,
        "coverage_tier": "standard",
        "is_featured": 0,
    },
    "glacier": {
        "county_name": "Glacier",
        "facility_name": "Glacier County Detention Center",
        "roster_url": "https://glaciercountymt.gov/category/jail-roster/",
        "phone": None,
        "coverage_tier": "standard",
        "is_featured": 0,
    },
    "lewis-and-clark": {
        "county_name": "Lewis and Clark",
        "facility_name": "Lewis and Clark County Detention Center",
        "roster_url": "https://www.lccountymt.gov/Sheriff/Detention-Center",
        "phone": "406-447-8235",
        "coverage_tier": "major",
        "is_featured": 1,
    },
    "silver-bow": {
        "county_name": "Silver Bow",
        "facility_name": "Butte-Silver Bow Detention Center",
        "roster_url": "https://co.silverbow.mt.us/3274/Detention-Center",
        "phone": "406-497-1120",
        "coverage_tier": "major",
        "is_featured": 1,
    },
    "roosevelt": {
        "county_name": "Roosevelt",
        "facility_name": "Roosevelt County Detention Center",
        "roster_url": "https://www.rooseveltcountymt.gov/sheriff-coroner/",
        "phone": None,
        "coverage_tier": "standard",
        "is_featured": 0,
    },
}


@dataclass
class SyncStats:
    fetched_count: int = 0
    new_count: int = 0
    updated_count: int = 0
    missing_count: int = 0
    alert_candidates: list[dict[str, object]] = field(default_factory=list)


class SourceTemporarilyUnavailable(RuntimeError):
    """Raised when an external roster endpoint is temporarily unreachable."""


def _normalize_hash_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.strip().lower().split())
    return str(value).strip().lower()



def _compute_booking_hash(payload: dict[str, object]) -> str:
    fields = {
        "county_slug": _normalize_hash_value(payload.get("county_slug")),
        "county_name": _normalize_hash_value(payload.get("county_name")),
        "facility_name": _normalize_hash_value(payload.get("facility_name")),
        "person_name": _normalize_hash_value(payload.get("person_name")),
        "booking_number": _normalize_hash_value(payload.get("booking_number")),
        "booking_at": _normalize_hash_value(payload.get("booking_at")),
        "charges_summary": _normalize_hash_value(payload.get("charges_summary")),
        "source_url": _normalize_hash_value(payload.get("source_url")),
        "source_record_id": _normalize_hash_value(payload.get("source_record_id")),
    }
    blob = json.dumps(fields, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()



def _build_booking_payload(source: sqlite3.Row, record: JailBookingRecord) -> tuple[str, str]:
    payload = {
        "county_slug": source["county_slug"],
        "county_name": source["county_name"],
        "facility_name": source["facility_name"],
        "person_name": record.person_name,
        "booking_number": record.booking_number,
        "booking_at": record.booking_at,
        "charges_summary": record.charges_summary,
        "source_url": record.source_url or source["roster_url"],
        "source_record_id": record.source_record_id,
    }
    hash_id = _compute_booking_hash(payload)
    raw_json = json.dumps(
        {
            "source_record_id": record.source_record_id,
            "person_name": record.person_name,
            "age": record.age,
            "booking_number": record.booking_number,
            "booking_at": record.booking_at,
            "charges_summary": record.charges_summary,
            "source_url": record.source_url,
        },
        ensure_ascii=False,
    )
    return hash_id, raw_json


def _name_slug_for(person_name: str) -> str:
    """Normalize a person name into a URL-friendly slug.

    Mirrors the backfill migration in init_db.py so that newly ingested
    bookings are immediately groupable with existing rows.
    """
    if not person_name:
        return ''
    return re.sub(r"[^a-z0-9-]", "", (
        person_name
        .lower()
        .replace(" ", "-")
        .replace(".", "")
        .replace("'", "")
        .replace(",", "")
    ))


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


def _connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=DB_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute(f"PRAGMA busy_timeout = {DB_BUSY_TIMEOUT_MS}")
    return conn


def _is_lock_error(exc: Exception) -> bool:
    return "database is locked" in str(exc).lower()


def _ensure_tracked_sources(
    conn: sqlite3.Connection, *, county_slug: str | None = None
) -> None:
    """Seed/update jail_booking_sources rows from TRACKED_SOURCES.

    By default all tracked sources are refreshed (used by the scheduled CLI
    runner). Callers that only need one source -- e.g. the Havre email DOCX
    pipeline -- should pass ``county_slug`` to touch only that row. This
    dramatically narrows the write lock window when other processes are
    contending for the database.
    """
    sources = (
        {county_slug: TRACKED_SOURCES[county_slug]}.items()
        if county_slug and county_slug in TRACKED_SOURCES
        else TRACKED_SOURCES.items()
    )
    for slug, meta in sources:
        conn.execute(
            '''
            INSERT OR IGNORE INTO jail_booking_sources (
                county_slug,
                county_name,
                facility_name,
                roster_url,
                phone,
                source_type,
                coverage_tier,
                is_enabled,
                is_featured,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, 'official_roster', ?, 1, ?, datetime('now'), datetime('now'))
            ''',
            (
                slug,
                meta["county_name"],
                meta["facility_name"],
                meta["roster_url"],
                meta["phone"],
                meta["coverage_tier"],
                meta["is_featured"],
            ),
        )
        # Keep existing rows in sync with the canonical metadata in TRACKED_SOURCES.
        # This repairs stale roster URLs when a source moves to a new endpoint,
        # but preserves the existing is_enabled flag so intentional disables are
        # not overwritten.
        conn.execute(
            '''
            UPDATE jail_booking_sources
            SET county_name = ?,
                facility_name = ?,
                roster_url = ?,
                phone = ?,
                coverage_tier = ?,
                is_featured = ?,
                updated_at = datetime('now')
            WHERE county_slug = ?
            ''',
            (
                meta["county_name"],
                meta["facility_name"],
                meta["roster_url"],
                meta["phone"],
                meta["coverage_tier"],
                meta["is_featured"],
                slug,
            ),
        )
    conn.commit()


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


def _text_from_html(fragment: str) -> str:
    parser = _RosterTextExtractor()
    parser.feed(fragment or "")
    text = parser.text()
    return re.sub(r"\s+", " ", text).strip()


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


def _extract_text_lines(page_html: str) -> list[str]:
    parser = _RosterTextExtractor()
    parser.feed(page_html)
    plain_text = parser.text()
    lines = [re.sub(r"\s+", " ", line).strip() for line in plain_text.splitlines()]
    return [line for line in lines if line]


def _extract_hidden_form_fields(page_html: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in re.finditer(r"<input\b[^>]*>", page_html, re.IGNORECASE):
        tag = match.group(0)
        if not re.search(r'type="hidden"', tag, re.IGNORECASE):
            continue
        name_match = re.search(r'name="([^"]+)"', tag, re.IGNORECASE)
        if not name_match:
            continue
        value_match = re.search(r'value="([^"]*)"', tag, re.IGNORECASE)
        fields[html.unescape(name_match.group(1))] = html.unescape(value_match.group(1)) if value_match else ""
    return fields


def _is_name_line(line: str) -> bool:
    if "," not in line:
        return False
    if line.upper() != line:
        return False
    return bool(re.match(r"^[A-Z' .-]+,\s*[A-Z' .-]+$", line))


def _extract_broadwater_page_urls(page_html: str, source_url: str) -> list[str]:
    page_urls = {source_url}
    for href in re.findall(r'href="([^"]*roster\.php[^"]*)"', page_html, re.IGNORECASE):
        absolute_url = urljoin(source_url, html.unescape(href))
        parsed = urlparse(absolute_url)
        if not parsed.path.endswith("/roster.php"):
            continue
        if "released" in parsed.query.lower():
            continue
        page_urls.add(absolute_url)

    def sort_key(page_url: str) -> tuple[int, str]:
        query = parse_qs(urlparse(page_url).query)
        grp = query.get("grp", ["0"])[0]
        return (int(grp) if grp.isdigit() else 0, page_url)

    return sorted(page_urls, key=sort_key)


def _parse_broadwater_roster(page_html: str, page_url: str) -> list[JailBookingRecord]:
    lines = _extract_text_lines(page_html)
    records: list[JailBookingRecord] = []
    idx = 0

    while idx < len(lines):
        line = lines[idx]
        if not _is_name_line(line):
            idx += 1
            continue
        if idx + 5 >= len(lines):
            break
        if lines[idx + 1] != "Booking #:" or lines[idx + 3] != "Booking Date:" or lines[idx + 5] != "Charges:":
            idx += 1
            continue

        booking_number = lines[idx + 2]
        booking_at = _normalize_datetime(lines[idx + 4])
        scan_idx = idx + 6
        charge_lines: list[str] = []
        while scan_idx < len(lines) and lines[scan_idx] != "Bond:":
            candidate = lines[scan_idx]
            if candidate not in {"View Profile >>>", "* * *"}:
                charge_lines.append(candidate)
            scan_idx += 1
        if scan_idx >= len(lines):
            idx += 1
            continue

        bond_value = lines[scan_idx + 1] if scan_idx + 1 < len(lines) else ""
        charges_summary = (
            "; ".join(charge_lines[:4])
            if charge_lines
            else "Charge details available on the official Broadwater County inmate page."
        )
        if bond_value:
            bond_text = f"Bond {bond_value}" if bond_value != "Bond amount unavailable" else bond_value
            charges_summary = f"{charges_summary}; {bond_text}" if charges_summary else bond_text

        records.append(
            JailBookingRecord(
                source_record_id=f"broadwater:{booking_number}",
                person_name=line.title(),
                age=None,
                booking_number=booking_number,
                booking_at=booking_at,
                charges_summary=charges_summary,
                source_url=page_url,
            )
        )
        idx = scan_idx + 2

    return records


def fetch_broadwater_bookings(source_url: str) -> list[JailBookingRecord]:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://montanablotter.com)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": source_url,
        }
    )

    pending_urls = [source_url]
    seen_pages: set[str] = set()
    seen_records: set[str] = set()
    records: list[JailBookingRecord] = []

    while pending_urls:
        page_url = pending_urls.pop(0)
        if page_url in seen_pages:
            continue

        response = session.get(page_url, timeout=20)
        response.raise_for_status()
        page_html = response.text
        seen_pages.add(page_url)

        for linked_page_url in _extract_broadwater_page_urls(page_html, source_url):
            if linked_page_url not in seen_pages and linked_page_url not in pending_urls:
                pending_urls.append(linked_page_url)

        for record in _parse_broadwater_roster(page_html, page_url):
            if record.source_record_id in seen_records:
                continue
            seen_records.add(record.source_record_id)
            records.append(record)

    return records


def _summarize_zuercher_hold_reasons(raw_value: str, *, county_name: str = "") -> str:
    raw = (raw_value or "").strip()
    fallback = (
        f"Charge details available on the official {county_name} inmate portal."
        if county_name
        else "Charge details available on the official inmate portal."
    )
    if not raw:
        return fallback
    parts = [
        _text_from_html(fragment)
        for fragment in re.split(r"<br\s*/?>", raw, flags=re.IGNORECASE)
        if _text_from_html(fragment)
    ]
    if not parts:
        return fallback
    return "; ".join(parts[:4])


def _summarize_jefferson_hold_reasons(raw_value: str) -> str:
    return _summarize_zuercher_hold_reasons(raw_value, county_name="Jefferson County")


def fetch_zuercher_bookings(source_url: str, *, county_name: str = "") -> list[JailBookingRecord]:
    api_base = source_url.rstrip("/")
    if api_base.endswith("#/inmates"):
        api_base = api_base[:-9].rstrip("/")

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://montanablotter.com)",
            "Accept": "application/json,text/plain,*/*",
            "Referer": f"{api_base}/#/inmates",
        }
    )

    records: list[JailBookingRecord] = []
    offset = 0
    page_size = 50
    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00.000Z")

    while True:
        response = session.post(
            f"{api_base}/api/portal/inmates/load",
            json={
                "name": "",
                "race": "all",
                "sex": "all",
                "cell_block": "all",
                "held_for_agency": "any",
                "in_custody": today,
                "paging": {"start": offset, "count": page_size},
                "sorting": {"sort_by_column_tag": "name", "sort_descending": False},
            },
            timeout=45,
        )
        if response.status_code == 404:
            raise SourceTemporarilyUnavailable(
                f"Zuercher API endpoint not found for {api_base} (portal may not expose public API)."
            )
        response.raise_for_status()
        content_type = str(response.headers.get("Content-Type", "") or "")
        response_bytes = getattr(response, "content", b"") or b""
        if isinstance(response_bytes, str):
            response_bytes = response_bytes.encode("utf-8", errors="ignore")
        elif not isinstance(response_bytes, (bytes, bytearray)):
            response_bytes = b""
        if "text/html" in content_type or (response_bytes[:100].strip().lower().startswith(b"<")):
            raise SourceTemporarilyUnavailable(
                f"Zuercher portal at {api_base} returned HTML instead of JSON — likely in maintenance mode."
            )
        payload = response.json() or {}
        rows = payload.get("records") or []

        for row in rows:
            charges_summary = _summarize_zuercher_hold_reasons(row.get("hold_reasons", ""), county_name=county_name)
            arrest_date = _normalize_datetime(f"{row.get('arrest_date', '')} 00:00")
            identity_parts = [
                (row.get("name") or "").strip().upper(),
                (row.get("held_for_agency") or "").strip().upper(),
                (row.get("sex") or "").strip().upper(),
                (row.get("arrest_date") or "").strip(),
                charges_summary,
            ]
            source_record_id = hashlib.sha1("|".join(identity_parts).encode("utf-8")).hexdigest()[:20]
            booking_number = source_record_id[:12]

            records.append(
                JailBookingRecord(
                    source_record_id=source_record_id,
                    person_name=(row.get("name") or "").title(),
                    age=None,
                    booking_number=booking_number,
                    booking_at=arrest_date,
                    charges_summary=charges_summary,
                    source_url=f"{api_base}/#/inmates",
                )
            )

        offset += len(rows)
        total = int(payload.get("total_record_count") or 0)
        if not rows or offset >= total:
            break

    return records


def fetch_jefferson_bookings(source_url: str) -> list[JailBookingRecord]:
    return fetch_zuercher_bookings(source_url, county_name="Jefferson County")


def fetch_ravalli_bookings(source_url: str) -> list[JailBookingRecord]:
    return fetch_zuercher_bookings(source_url, county_name="Ravalli County")


# Optional Node.js bridge for Zuercher portals.
#
# The Zuercher SPA calls two REST endpoints (GET .../api/portal/inmates/init and
# POST .../api/portal/inmates/load). The Python fetcher above handles that directly.
# A drop-in Node implementation lives at scripts/zuercher_fetcher.js (no browser
# automation; native fetch + AbortController). This bridge lets an operator route
# Zuercher fetches through that Node script instead of the Python path, by setting
# MB_USE_NODE_ZUERCHER=1 in the environment.
#
# Reversible: if the env var is unset/0, this returns None and the caller keeps
# using the existing fetch_zuercher_bookings() path. No existing behavior changes.
_ZUERCHER_NODE_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "scripts",
    "zuercher_fetcher.js",
)


def fetch_zuercher_bookings_via_node(
    source_url: str, *, county_name: str = ""
) -> list[JailBookingRecord] | None:
    """Fetch Zuercher roster via the Node script. Returns None if disabled/unavailable.

    Returns None (not []) so callers can fall back to the Python fetcher. A non-None
    return is a parsed list of JailBookingRecord in the same shape as the Python path.
    """
    if not os.environ.get("MB_USE_NODE_ZUERCHER", "").strip() in ("1", "true", "True"):
        return None
    if not os.path.exists(_ZUERCHER_NODE_SCRIPT):
        logger.warning("Node Zuercher bridge enabled but script missing: %s", _ZUERCHER_NODE_SCRIPT)
        return None

    api_base = source_url.rstrip("/")
    if api_base.endswith("#/inmates"):
        api_base = api_base[:-9].rstrip("/")

    import subprocess

    try:
        proc = subprocess.run(
            [
                "node",
                _ZUERCHER_NODE_SCRIPT,
                "--county",
                county_name or "Unknown",
                "--url",
                api_base,
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception as exc:  # noqa: BLE001 - bridge must never crash the ingest loop
        logger.warning("Node Zuercher bridge failed to invoke node: %s", exc)
        return None

    if proc.returncode == 2:
        # Node script reported InmateLoadUnavailable (e.g. HTTP 500 on /load).
        # Surface as SourceTemporarilyUnavailable so the release cascade is not
        # triggered on a broken endpoint.
        raise SourceTemporarilyUnavailable(
            f"Node Zuercher bridge: inmates/load unavailable for {api_base}: "
            f"{proc.stderr.strip()[:200]}"
        )
    if proc.returncode != 0:
        logger.warning("Node Zuercher bridge exited %s: %s", proc.returncode, proc.stderr.strip()[:200])
        return None

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        logger.warning("Node Zuercher bridge returned invalid JSON: %s", exc)
        return None

    records: list[JailBookingRecord] = []
    for r in payload.get("records", []):
        identity_parts = [
            (r.get("inmate_name") or "").strip().upper(),
            (r.get("agency") or "").strip().upper(),
            (r.get("booking_date") or "").strip(),
            "; ".join(r.get("charges", [])),
        ]
        source_record_id = hashlib.sha1("|".join(identity_parts).encode("utf-8")).hexdigest()[:20]
        records.append(
            JailBookingRecord(
                source_record_id=source_record_id,
                person_name=(r.get("inmate_name") or "").title(),
                age=None,
                booking_number=source_record_id[:12],
                booking_at=_normalize_datetime(f"{(r.get('booking_date') or '')} 00:00"),
                charges_summary="; ".join(r.get("charges", [])) or (
                    f"Charge details available on the official {county_name} inmate portal."
                    if county_name
                    else "Charge details available on the official inmate portal."
                ),
                source_url=f"{api_base}/#/inmates",
            )
        )
    return records


def _parse_sanders_search_results(page_html: str, base_url: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for match in re.findall(
        r'<tr bgcolor="[^"]*"[^>]*>(.*?)</tr>',
        page_html,
        re.IGNORECASE | re.DOTALL,
    ):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", match, re.IGNORECASE | re.DOTALL)
        if len(cells) < 6:
            continue
        detail_match = re.search(r'href="(viewbkg\.php\?bkg=\d+)"', cells[5], re.IGNORECASE)
        rows.append(
            {
                "jacket_number": _text_from_html(cells[0]).strip(),
                "person_name": _text_from_html(cells[1]).strip().title(),
                "date_of_birth": _text_from_html(cells[2]).strip(),
                "race": _text_from_html(cells[3]).strip(),
                "gender": _text_from_html(cells[4]).strip(),
                "detail_url": f"{base_url}/{detail_match.group(1)}" if detail_match else "",
            }
        )
    return rows


def _parse_sanders_detail(page_html: str) -> dict[str, str | None]:
    def extract_value(label: str) -> str:
        match = re.search(
            rf'<div class="srt_label">\s*{re.escape(label)}\s*</div>\s*<div class="rtn_field">\s*&nbsp;(.*?)</div>',
            page_html,
            re.IGNORECASE | re.DOTALL,
        )
        return _text_from_html(match.group(1)).strip() if match else ""

    charge_rows: list[str] = []
    for row_html in re.findall(r"<tr bgcolor=\"[^\"]*\"[^>]*>(.*?)</tr>", page_html, re.IGNORECASE | re.DOTALL):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.IGNORECASE | re.DOTALL)
        if len(cells) < 4:
            continue
        charge = _text_from_html(cells[1]).strip()
        bond = _text_from_html(cells[3]).strip()
        if charge:
            charge_rows.append(f"{charge} | Bond {bond or 'Unknown'}")

    return {
        "booking_number": extract_value("Jacket #:"),
        "person_name": extract_value("Inmate's Name:").title(),
        "date_of_birth": extract_value("Date of Birth:"),
        "booking_date": extract_value("Booking Date:"),
        "arresting_agency": extract_value("Arresting Agency:"),
        "release_date": extract_value("Release Date:"),
        "charges_summary": "; ".join(charge_rows[:4]) if charge_rows else "Charge details available on the official Sanders County inmate page.",
    }


def fetch_sanders_bookings(source_url: str) -> list[JailBookingRecord]:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    base_url = source_url.rstrip("/")
    if not base_url.endswith("/jms_public"):
        base_url = f"{base_url}/jms_public"

    session = requests.Session()
    session.verify = False  # Sanders County's current TLS certificate is expired.
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://montanablotter.com)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": f"{base_url}/index.php",
        }
    )

    results: list[JailBookingRecord] = []
    seen: set[str] = set()
    try:
        for letter in string.ascii_uppercase:
            response = session.post(
                f"{base_url}/functions/search.php",
                data={
                    "nx": "",
                    "last": letter,
                    "first": "",
                    "jkt": "",
                    "middle": "",
                    "dob_yr": "",
                },
                timeout=45,
            )
            response.raise_for_status()
            for row in _parse_sanders_search_results(response.text, base_url):
                detail_url = row["detail_url"]
                source_record_id = detail_url or row["jacket_number"]
                if not source_record_id or source_record_id in seen:
                    continue
                seen.add(source_record_id)

                detail_html = ""
                charges_summary = "Charge details available on the official Sanders County inmate page."
                booking_at = None
                if detail_url:
                    detail_response = session.get(detail_url, timeout=45)
                    detail_response.raise_for_status()
                    detail_html = detail_response.text
                if detail_html:
                    detail = _parse_sanders_detail(detail_html)
                    charges_summary = str(detail["charges_summary"] or charges_summary)
                    booking_at = _normalize_datetime(str(detail["booking_date"] or ""))
                else:
                    detail = {}

                results.append(
                    JailBookingRecord(
                        source_record_id=source_record_id,
                        person_name=str(detail.get("person_name") or row["person_name"]),
                        age=None,
                        booking_number=str(detail.get("booking_number") or row["jacket_number"]),
                        booking_at=booking_at,
                        charges_summary=charges_summary,
                        source_url=detail_url or f"{base_url}/index.php",
                    )
                )
    except requests.exceptions.RequestException as exc:
        raise SourceTemporarilyUnavailable(f"Sanders roster temporarily unavailable: {exc}") from exc

    return results


def fetch_yellowstone_bookings(source_url: str) -> list[JailBookingRecord]:
    raw_records = _fetch_yellowstone_bookings_raw(source_url)
    return [
        JailBookingRecord(
            source_record_id=r.source_record_id,
            person_name=r.person_name,
            age=None,
            booking_number=r.booking_number,
            booking_at=r.booking_at,
            charges_summary=r.charges_summary,
            source_url=r.source_url,
        )
        for r in raw_records
    ]


def _extract_cascade_pdf_url(page_html: str) -> str | None:
    """Find the SharePoint PDF link embedded in the Cascade County roster page.

    Cascade has used two link shapes:
      1. Direct personal/tenant path ending in .pdf (legacy).
      2. Anonymous ``:b:/g/personal/...`` sharing links (current), where
         ``jailroster`` appears in the username portion of the URL.
    """
    # Current anonymous sharing link, e.g.
    # https://ccmtgov-my.sharepoint.com/:b:/g/personal/jailroster_cascadecountymt_gov/...
    match = re.search(
        r'href="(https://ccmtgov-my\.sharepoint\.com/:[bu]:/g/personal/jailroster[^"]+)"',
        page_html,
        re.IGNORECASE,
    )
    if match:
        return match.group(1)
    # Legacy direct .pdf link with "jailroster" in the path.
    match = re.search(
        r'href="(https://ccmtgov-my\.sharepoint\.com/[^"]*jailroster[^"]*\.pdf[^"]*)"',
        page_html,
        re.IGNORECASE,
    )
    if match:
        return match.group(1)
    # Broader fallback for any sharepoint PDF link on the page.
    match = re.search(
        r'href="(https://ccmtgov-my\.sharepoint\.com/[^"]*\.pdf[^"]*)"',
        page_html,
        re.IGNORECASE,
    )
    if match:
        return match.group(1)
    return None


def _fetch_pdf_text(pdf_url: str) -> str:
    """Download a PDF and return extracted plain text using pdfplumber."""
    response = requests.get(
        pdf_url,
        timeout=60,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://montanablotter.com)",
            "Accept": "application/pdf,*/*",
        },
    )
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "").lower()
    if "text/html" in content_type or b"<!DOCTYPE html" in response.content[:256].lower():
        raise SourceTemporarilyUnavailable(
            "Cascade County PDF is behind authentication (SharePoint sign-in required)."
        )
    with pdfplumber.open(io.BytesIO(response.content)) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]
    return "\n".join(pages)


def _parse_cascade_pdf_text(pdf_text: str, source_url: str) -> list[JailBookingRecord]:
    """Parse plain text extracted from a Cascade County jail roster PDF.

    The PDF format is not publicly documented, so this parser uses heuristics
    matched against common Montana jail roster layouts.
    """
    records: list[JailBookingRecord] = []
    lines = [line.strip() for line in pdf_text.splitlines() if line.strip()]
    for idx, line in enumerate(lines):
        # Skip header/footer/metadata lines
        if re.match(r"^(Jail Roster|Inmate Roster|Cascade County|Printed|Page \d|Date:)", line, re.IGNORECASE):
            continue
        # Name lines: LASTNAME, FIRSTNAME or LASTNAME, FIRSTNAME MIDDLE
        name_match = re.match(
            r"^([A-Z][A-Z' -]+),\s+([A-Z][A-Z' -]+(?:\s+[A-Z][A-Z' -]+)*)\s*(\d{1,3})?\s*",
            line,
        )
        if not name_match:
            continue
        last = name_match.group(1).strip()
        first = name_match.group(2).strip()
        age_str = name_match.group(3)
        person_name = f"{last.title()}, {first.title()}"
        age = int(age_str) if age_str and age_str.isdigit() else None

        # Look for booking date and charges in current line and next few lines
        booking_at: str | None = None
        charges_parts: list[str] = []
        context = line + " " + " ".join(lines[idx + 1 : idx + 4])

        date_match = re.search(
            r"Booking\s*:?\s*(\d{1,2}/\d{1,2}/\d{4}|\d{1,2}-\d{1,2}-\d{4}|\d{4}-\d{2}-\d{2})",
            context,
            re.IGNORECASE,
        )
        if date_match:
            booking_at = _normalize_datetime(date_match.group(1))

        charges_match = re.search(
            r"Charges?\s*:?\s*(.+?)(?:\s+Bond\s|Bond\s|$)",
            context,
            re.IGNORECASE | re.DOTALL,
        )
        if charges_match:
            raw_charges = charges_match.group(1).strip().rstrip(";, ")
            if raw_charges:
                charges_parts.append(raw_charges[:300])

        charges_summary = (
            "; ".join(charges_parts)
            if charges_parts
            else "Charge details available on the official Cascade County inmate roster."
        )
        source_record_id = f"cascade:{person_name.lower().replace(' ', '-')}:{booking_at or idx}"
        records.append(
            JailBookingRecord(
                source_record_id=source_record_id,
                person_name=person_name,
                age=age,
                booking_number="",
                booking_at=booking_at,
                charges_summary=charges_summary,
                source_url=source_url,
            )
        )
    return records


def fetch_cascade_bookings(source_url: str) -> list[JailBookingRecord]:
    page_html = _fetch_html(source_url)
    pdf_url = _extract_cascade_pdf_url(page_html)
    if not pdf_url:
        logger.warning("Cascade roster: no PDF link found on %s", source_url)
        return []
    pdf_text = _fetch_pdf_text(pdf_url)
    return _parse_cascade_pdf_text(pdf_text, pdf_url)


def _record_run(
    conn: sqlite3.Connection,
    *,
    source_id: int,
    run_type: str,
    status: str,
    fetched_count: int = 0,
    new_count: int = 0,
    updated_count: int = 0,
    missing_count: int = 0,
    notes: str = "",
) -> None:
    conn.execute(
        '''
        INSERT INTO jail_booking_runs (
            source_id,
            run_type,
            status,
            fetched_count,
            new_count,
            updated_count,
            missing_count,
            started_at,
            completed_at,
            notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'), ?)
        ''',
        (
            source_id,
            run_type,
            status,
            fetched_count,
            new_count,
            updated_count,
            missing_count,
            notes[:1000],
        ),
    )


def _mark_source_checked(
    conn: sqlite3.Connection,
    source_id: int,
    *,
    success: bool,
    notes: str = "",
    latest_error: str = "",
) -> None:
    if success:
        conn.execute(
            '''
            UPDATE jail_booking_sources
            SET last_checked_at = datetime('now'),
                last_success_at = datetime('now'),
                latest_error = '',
                notes = CASE WHEN ? != '' THEN ? ELSE notes END
            WHERE id = ?
            ''',
            (notes[:1000], notes[:1000], source_id),
        )
    else:
        conn.execute(
            '''
            UPDATE jail_booking_sources
            SET last_checked_at = datetime('now'),
                latest_error = CASE WHEN ? != '' THEN ? ELSE latest_error END,
                notes = CASE WHEN ? != '' THEN ? ELSE notes END
            WHERE id = ?
            ''',
            (latest_error[:2000], latest_error[:2000], notes[:1000], notes[:1000], source_id),
        )


def _sync_records(
    conn: sqlite3.Connection,
    source: sqlite3.Row,
    records: Iterable[JailBookingRecord],
    *,
    dry_run: bool = False,
) -> SyncStats:
    now_sql = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    stats = SyncStats()
    payload = list(records)
    stats.fetched_count = len(payload)

    existing_rows = conn.execute(
        '''
        SELECT id, source_record_id, person_name, booking_at, charges_summary, is_current, hash_id, raw_json
        FROM jail_bookings
        WHERE source_id = ?
        ''',
        (source["id"],),
    ).fetchall()
    existing_by_key = {row["source_record_id"]: row for row in existing_rows if row["source_record_id"]}
    current_existing_count = sum(1 for row in existing_rows if int(row["is_current"] or 0) == 1)
    if not payload and current_existing_count:
        raise RuntimeError(
            f"Parsed zero jail booking rows for {source['county_name']} with "
            f"{current_existing_count} current booking(s); refusing to mark releases."
        )
    seen_ids: set[str] = set()

    for record in payload:
        seen_ids.add(record.source_record_id)
        current = existing_by_key.get(record.source_record_id)
        hash_id, raw_json = _build_booking_payload(source, record)
        if current is None:
            stats.new_count += 1
            if dry_run:
                continue
            name_slug = _name_slug_for(record.person_name)
            cursor = conn.execute(
                '''
                INSERT INTO jail_bookings (
                    source_id,
                    county_slug,
                    county_name,
                    facility_name,
                    person_name,
                    age,
                    booking_number,
                    booking_at,
                    charges_summary,
                    source_url,
                    source_record_id,
                    hash_id,
                    raw_json,
                    name_slug,
                    booking_status,
                    is_current,
                    first_seen_at,
                    last_seen_at,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'current', 1, datetime('now'), datetime('now'), datetime('now'), datetime('now'))
                ''',
                (
                    source["id"],
                    source["county_slug"],
                    source["county_name"],
                    source["facility_name"],
                    record.person_name,
                    record.age,
                    record.booking_number,
                    record.booking_at,
                    record.charges_summary,
                    record.source_url or source["roster_url"],
                    record.source_record_id,
                    hash_id,
                    raw_json,
                    name_slug,
                ),
            )
            stats.alert_candidates.append(
                {
                    'booking_id': cursor.lastrowid,
                    'county_slug': source['county_slug'],
                    'county_name': source['county_name'],
                    'person_name': record.person_name,
                    'booking_at': record.booking_at,
                    'charges_summary': record.charges_summary,
                    'source_url': record.source_url or source['roster_url'],
                    'source_record_id': record.source_record_id,
                }
            )
            continue

        changed = any(
            [
                (current["person_name"] or "") != record.person_name,
                (current["booking_at"] or "") != (record.booking_at or ""),
                (current["charges_summary"] or "") != record.charges_summary,
                (current["hash_id"] or "") != hash_id,
                int(current["is_current"] or 0) != 1,
            ]
        )
        if changed:
            stats.updated_count += 1
        if dry_run:
            continue
        name_slug = _name_slug_for(record.person_name)
        conn.execute(
            '''
            UPDATE jail_bookings
            SET person_name = ?,
                age = ?,
                booking_number = ?,
                booking_at = ?,
                charges_summary = ?,
                source_url = ?,
                hash_id = ?,
                raw_json = ?,
                name_slug = ?,
                booking_status = 'current',
                is_current = 1,
                release_at = NULL,
                last_seen_at = ?,
                updated_at = ?
            WHERE id = ?
            ''',
            (
                record.person_name,
                record.age,
                record.booking_number,
                record.booking_at,
                record.charges_summary,
                record.source_url or source["roster_url"],
                hash_id,
                raw_json,
                name_slug,
                now_sql,
                now_sql,
                current["id"],
            ),
        )
        if not changed:
            continue
        stats.alert_candidates.append(
            {
                'booking_id': current['id'],
                'county_slug': source['county_slug'],
                'county_name': source['county_name'],
                'person_name': record.person_name,
                'booking_at': record.booking_at,
                'charges_summary': record.charges_summary,
                'source_url': record.source_url or source['roster_url'],
                'source_record_id': record.source_record_id,
            }
        )

    for row in existing_rows:
        if row["source_record_id"] in seen_ids or int(row["is_current"] or 0) == 0:
            continue
        stats.missing_count += 1
        if dry_run:
            continue
        conn.execute(
            '''
            UPDATE jail_bookings
            SET booking_status = 'released',
                is_current = 0,
                release_at = COALESCE(release_at, ?),
                last_seen_at = ?,
                updated_at = ?
            WHERE id = ?
            ''',
            (now_sql, now_sql, now_sql, row["id"]),
        )

    return stats


def _fetch_records_for_source(source: sqlite3.Row, roster_url: str) -> list[JailBookingRecord]:
    """Fetch records for a single source. Network/OCR work happens here, outside DB transactions."""
    county_slug = source["county_slug"]
    if county_slug == "big-horn":
        return fetch_big_horn_bookings(roster_url)
    if county_slug == "fallon":
        return fetch_fallon_bookings(roster_url)
    if county_slug == "fergus":
        return fetch_fergus_bookings(roster_url)
    if county_slug == "glacier":
        return fetch_glacier_bookings(roster_url)
    if county_slug == "roosevelt":
        return fetch_roosevelt_bookings(roster_url)
    if county_slug == "lewis-and-clark":
        return fetch_lewis_clark_bookings(roster_url)
    if county_slug == "silver-bow":
        return fetch_silver_bow_bookings(roster_url)
    if county_slug == "valley":
        from services.ingestion.fetchers.valley_inmate import fetch_valley_bookings
        return fetch_valley_bookings(roster_url)
    if county_slug == "broadwater":
        from services.ingestion.fetchers.broadwater_inmate import fetch_broadwater_bookings
        return fetch_broadwater_bookings(roster_url)
    if county_slug == "flathead":
        return fetch_flathead_bookings(roster_url)
    if county_slug in ZUERCHER_COUNTIES:
        node_records = fetch_zuercher_bookings_via_node(roster_url, county_name=source["county_name"])
        if node_records is not None:
            return node_records
        return fetch_zuercher_bookings(roster_url, county_name=source["county_name"])
    if county_slug == "missoula":
        from services.ingestion.fetchers.missoula_inmate import fetch_missoula_bookings
        return fetch_missoula_bookings(roster_url)
    if county_slug == "sanders":
        return fetch_sanders_bookings(roster_url)
    if county_slug == "yellowstone":
        return fetch_yellowstone_bookings(roster_url)
    if county_slug == "cascade":
        return fetch_cascade_bookings(roster_url)
    if county_slug == "custer":
        return fetch_custer_bookings(roster_url)
    if county_slug == "lake":
        from services.ingestion.fetchers.lake_inmate import fetch_lake_bookings
        return fetch_lake_bookings(roster_url)
    if county_slug == "lewis_clark":
        return fetch_lewis_clark_bookings(roster_url)
    if county_slug == "lincoln":
        return fetch_lincoln_bookings(roster_url)
    if county_slug == "silver_bow":
        return fetch_silver_bow_bookings(roster_url)
    if county_slug == "rosebud":
        return fetch_rosebud_bookings(roster_url)
    if county_slug == "wheatland":
        from services.ingestion.fetchers.wheatland_inmate import fetch_wheatland_bookings
        return fetch_wheatland_bookings(roster_url)
    if county_slug == "park":
        from services.ingestion.roster_generic import fetch_park_bookings
        return fetch_park_bookings(roster_url)
    if county_slug == "beaverhead":
        from services.ingestion.roster_generic import fetch_beaverhead_bookings
        return fetch_beaverhead_bookings(roster_url)
    if county_slug == "dawson":
        from services.ingestion.fetchers.dawson_inmate import fetch_dawson_bookings
        return fetch_dawson_bookings(roster_url)
    if county_slug == "granite":
        from services.ingestion.fetchers.granite_inmate import fetch_granite_bookings
        return fetch_granite_bookings(roster_url)
    if county_slug == "mineral":
        from services.ingestion.fetchers.mineral_inmate import fetch_mineral_bookings
        return fetch_mineral_bookings(roster_url)
    if county_slug == "phillips":
        from services.ingestion.fetchers.phillips_inmate import fetch_phillips_bookings
        return fetch_phillips_bookings(roster_url)
    if county_slug == "pondera":
        from services.ingestion.fetchers.pondera_inmate import fetch_pondera_bookings
        return fetch_pondera_bookings(roster_url)
    if county_slug == "powell":
        from services.ingestion.fetchers.powell_inmate import fetch_powell_bookings
        return fetch_powell_bookings(roster_url)
    raise RuntimeError(f"No adapter for county slug: {county_slug}")


def _run_source(
    conn: sqlite3.Connection,
    source: sqlite3.Row,
    *,
    dry_run: bool = False,
    records: list[JailBookingRecord] | None = None,
) -> tuple[SyncStats, str]:
    county_slug = source["county_slug"]
    roster_url = (source["roster_url"] or "").strip()
    if county_slug == "hill":
        note = "Hill County is ingested from the Havre email DOCX pipeline, not the scheduled roster fetcher."
        _record_run(conn, source_id=source["id"], run_type="scheduled", status="skipped", notes=note)
        return SyncStats(), "skipped"
    if county_slug in SKIPPED_SOURCES:
        note = SKIPPED_SOURCES[county_slug]
        _record_run(conn, source_id=source["id"], run_type="scheduled", status="skipped", notes=note)
        _mark_source_checked(conn, source["id"], success=False, notes=note, latest_error=note)
        return SyncStats(), "skipped"
    if county_slug not in SUPPORTED_ADAPTERS:
        note = "No automated county adapter has been added yet."
        _record_run(conn, source_id=source["id"], run_type="scheduled", status="skipped", notes=note)
        _mark_source_checked(conn, source["id"], success=False, notes=note, latest_error=note)
        return SyncStats(), "skipped"

    try:
        if records is None:
            records = _fetch_records_for_source(source, roster_url)
    except SourceTemporarilyUnavailable as exc:
        note = str(exc)
        _record_run(conn, source_id=source["id"], run_type="scheduled", status="skipped", notes=note)
        _mark_source_checked(conn, source["id"], success=False, notes=note, latest_error=note)
        return SyncStats(), "skipped"

    stats = _sync_records(conn, source, records, dry_run=dry_run)
    alert_summary = {'matched': 0, 'sent': 0, 'failed': 0, 'skipped': 0}
    telegram_summary = {'matched': 0, 'sent': 0, 'failed': 0, 'skipped': 0}
    if not dry_run and getattr(config, 'BAIL_BONDS_ALERTS_ENABLED', True) and stats.alert_candidates:
        alert_summary = dispatch_felony_booking_alerts(conn, stats.alert_candidates)
        telegram_summary = dispatch_telegram_booking_alerts(conn, stats.alert_candidates)
    note = f"Fetched {stats.fetched_count} records from {source['county_name']}."
    if stats.alert_candidates:
        note += (
            f" Bondsman SMS matched={alert_summary['matched']} sent={alert_summary['sent']} "
            f"failed={alert_summary['failed']} skipped={alert_summary['skipped']}."
            f" Telegram matched={telegram_summary['matched']} sent={telegram_summary['sent']} "
            f"failed={telegram_summary['failed']} skipped={telegram_summary['skipped']}."
        )
    _record_run(
        conn,
        source_id=source["id"],
        run_type="scheduled" if not dry_run else "dry_run",
        status="success",
        fetched_count=stats.fetched_count,
        new_count=stats.new_count,
        updated_count=stats.updated_count,
        missing_count=stats.missing_count,
        notes=note,
    )
    _mark_source_checked(conn, source["id"], success=True, notes=note)
    return stats, "success"


def _classify_scraper_failure(exc: Exception) -> tuple[str, str]:
    message = str(exc).strip() or exc.__class__.__name__
    lowered = message.lower()
    if isinstance(exc, requests.exceptions.Timeout) or "timed out" in lowered or "timeout" in lowered:
        return "network_timeout", message
    if isinstance(exc, requests.exceptions.RequestException):
        return "network_error", message
    if any(token in lowered for token in ("not found", "unexpected", "selector", "viewstate", "prompt", "table")):
        return "dom_change", message
    return "scraper_error", message


def _build_publisher_payload(
    conn: sqlite3.Connection,
    *,
    successful_source_ids: list[int],
    failed_counties: dict[str, str],
) -> dict[str, object]:
    now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    if not successful_source_ids:
        return {
            "generated_at": now_iso,
            "status": "empty",
            "successful_counties": [],
            "failed_counties": failed_counties,
            "record_count": 0,
            "records": [],
        }

    placeholders = ",".join("?" for _ in successful_source_ids)
    rows = conn.execute(
        f"""
        SELECT
            jb.id,
            jb.county_slug,
            jb.county_name,
            jb.person_name,
            jb.age,
            jb.booking_number,
            jb.booking_at,
            jb.charges_summary,
            jb.source_url,
            jb.source_record_id,
            jb.first_seen_at,
            jb.last_seen_at
        FROM jail_bookings jb
        WHERE jb.source_id IN ({placeholders})
          AND COALESCE(jb.is_current, 1) = 1
        ORDER BY datetime(COALESCE(jb.booking_at, jb.first_seen_at, jb.created_at)) DESC, jb.id DESC
        """,
        successful_source_ids,
    ).fetchall()
    records = [dict(row) for row in rows]
    success_rows = conn.execute(
        f"SELECT county_slug, county_name FROM jail_booking_sources WHERE id IN ({placeholders}) ORDER BY county_name ASC",
        successful_source_ids,
    ).fetchall()
    successful_counties = [dict(row) for row in success_rows]
    return {
        "generated_at": now_iso,
        "status": "partial" if failed_counties else "success",
        "successful_counties": successful_counties,
        "failed_counties": failed_counties,
        "record_count": len(records),
        "records": records,
    }


def _publish_payload_to_disk(payload: dict[str, object]) -> None:
    if not PUBLISHER_PAYLOAD_PATH:
        return
    target_dir = os.path.dirname(PUBLISHER_PAYLOAD_PATH) or "."
    os.makedirs(target_dir, exist_ok=True)
    tmp_path = f"{PUBLISHER_PAYLOAD_PATH}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
    os.replace(tmp_path, PUBLISHER_PAYLOAD_PATH)


def ingest_jail_bookings(*, county_slug: str = "", dry_run: bool = False) -> dict[str, SyncStats]:
    attempt = 1
    max_attempts = max(1, DB_LOCK_RETRY_ATTEMPTS)

    def _load_sources(conn: sqlite3.Connection) -> list[sqlite3.Row]:
        with timed_db_transaction("ingest_jail_bookings_setup"):
            _ensure_tracked_sources(conn)
            if county_slug:
                rows = conn.execute(
                    '''
                    SELECT *
                    FROM jail_booking_sources
                    WHERE county_slug = ? AND COALESCE(is_enabled, 1) = 1
                    ORDER BY county_name ASC
                    ''',
                    (county_slug,),
                ).fetchall()
            else:
                rows = conn.execute(
                    '''
                    SELECT *
                    FROM jail_booking_sources
                    WHERE COALESCE(is_enabled, 1) = 1
                    ORDER BY COALESCE(is_featured, 0) DESC, county_name ASC
                    '''
                ).fetchall()
            conn.commit()
            return rows

    def _prefetch_records(sources: list[sqlite3.Row]) -> dict[str, tuple[list[JailBookingRecord] | None, tuple[str, str] | None]]:
        """Fetch roster records outside the DB transaction so slow OCR/network ops don't hold DB locks."""
        prefetched: dict[str, tuple[list[JailBookingRecord] | None, tuple[str, str] | None]] = {}
        for source in sources:
            county_slug = source["county_slug"]
            roster_url = (source["roster_url"] or "").strip()
            if county_slug == "hill" or county_slug in SKIPPED_SOURCES or county_slug not in SUPPORTED_ADAPTERS:
                prefetched[county_slug] = (None, None)
                continue
            logger.info("Prefetching jail roster source: %s", source["county_name"])
            try:
                records = _fetch_records_for_source(source, roster_url)
                prefetched[county_slug] = (records, None)
            except SourceTemporarilyUnavailable as exc:
                prefetched[county_slug] = (None, ("temporary_unavailable", str(exc)))
            except Exception as exc:
                failure_type, exact_error = _classify_scraper_failure(exc)
                logger.exception("Jail booking prefetch failed for %s", county_slug)
                prefetched[county_slug] = (None, (failure_type, exact_error))
        return prefetched

    while True:
        conn = _connect_db()
        try:
            sources = _load_sources(conn)
            prefetched = _prefetch_records(sources)

            with timed_db_transaction("ingest_jail_bookings"):
                results: dict[str, SyncStats] = {}
                successful_source_ids: list[int] = []
                failed_counties: dict[str, str] = {}
                for source in sources:
                    county_slug = source["county_slug"]
                    logger.info("Processing jail roster source: %s", source["county_name"])
                    records, error = prefetched.get(county_slug, (None, None))
                    if error:
                        failure_type, exact_error = error
                        failed_counties[county_slug] = f"{failure_type}: {exact_error}"
                        results[county_slug] = SyncStats()
                        _record_run(
                            conn,
                            source_id=source["id"],
                            run_type="scheduled" if not dry_run else "dry_run",
                            status="failed" if failure_type != "temporary_unavailable" else "skipped",
                            notes=f"{failure_type}: {exact_error}",
                        )
                        _mark_source_checked(
                            conn,
                            source["id"],
                            success=False,
                            notes=f"{failure_type}: {exact_error}",
                            latest_error=exact_error,
                        )
                        conn.commit()
                        continue

                    try:
                        stats, run_status = _run_source(conn, source, dry_run=dry_run, records=records)
                        results[county_slug] = stats
                        if run_status == "success":
                            successful_source_ids.append(int(source["id"]))
                        conn.commit()
                    except Exception as exc:
                        failure_type, exact_error = _classify_scraper_failure(exc)
                        logger.exception("Jail booking ingest failed for %s", county_slug)
                        failed_counties[county_slug] = f"{failure_type}: {exact_error}"
                        results[county_slug] = SyncStats()
                        _record_run(
                            conn,
                            source_id=source["id"],
                            run_type="scheduled" if not dry_run else "dry_run",
                            status="failed",
                            notes=f"{failure_type}: {exact_error}",
                        )
                        _mark_source_checked(
                            conn,
                            source["id"],
                            success=False,
                            notes=f"{failure_type}: {exact_error}",
                            latest_error=exact_error,
                        )
                        conn.commit()
                        continue

                payload = _build_publisher_payload(
                    conn,
                    successful_source_ids=successful_source_ids,
                    failed_counties=failed_counties,
                )
                try:
                    _publish_payload_to_disk(payload)
                except Exception as exc:
                    logger.warning("Failed to write publisher payload: %s", exc)
                return results
        except sqlite3.OperationalError as exc:
            if not _is_lock_error(exc) or attempt >= max_attempts:
                raise
            logger.warning(
                "SQLite was locked during jail ingest (attempt %s/%s); retrying in %.1fs",
                attempt,
                max_attempts,
                DB_LOCK_RETRY_SLEEP_SECONDS,
            )
            time.sleep(max(0.0, DB_LOCK_RETRY_SLEEP_SECONDS))
            attempt += 1
        finally:
            conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync jail roster bookings into Montana Blotter.")
    parser.add_argument("--county", default="", help="Optional county slug, for example missoula")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and parse without writing booking rows")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    results = ingest_jail_bookings(county_slug=(args.county or "").strip().lower(), dry_run=args.dry_run)

    for county_slug, stats in results.items():
        print(
            f"{county_slug}: fetched={stats.fetched_count} new={stats.new_count} "
            f"updated={stats.updated_count} missing={stats.missing_count}"
        )


if __name__ == "__main__":
    main()
