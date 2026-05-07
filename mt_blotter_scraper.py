"""
mt_blotter_scraper.py
=====================
Unified scraper framework for Montana police blotter data.

Supports:
- HTML table scraping (jail rosters, blotters)
- ArcGIS REST API feeds (Bozeman PD style)
- PDF parsing (Flathead County style)
- RSS/Atom feeds
- Custom adapter registration

Usage:
    python mt_blotter_scraper.py --source missoula_jail --dry-run
    python mt_blotter_scraper.py --source all --retention-days 90
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Add montanablotter to path for config/db access
sys.path.insert(0, "/root/montanablotter")
import config
from dedupe import incident_key_set
try:
    from pipeline_state import (
        ensure_ingestion_job,
        ensure_source_document,
        get_ingestion_job_status,
        increment_ingestion_retry,
        log_scraper_event,
        set_ingestion_job_status,
        sha256_text,
    )
except ImportError:
    # Fallback if pipeline_state not available
    def ensure_ingestion_job(key): return 0
    def get_ingestion_job_status(key): return "idle"
    def set_ingestion_job_status(key, status): pass
    def increment_ingestion_retry(key): pass
    def log_scraper_event(key, event, data): pass
    def sha256_text(text): return hashlib.sha256(text.encode()).hexdigest()
    def ensure_source_document(*args, **kwargs): pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HTTP session with retries
# ---------------------------------------------------------------------------

def _make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=0.75,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({
        "User-Agent": "MontanaBlotter/1.0 (public records aggregation; contact@montanablotter.com)",
    })
    return session

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class ScrapedRecord:
    """Normalized record from any source."""
    source_key: str
    source_type: str
    raw_id: str  # unique identifier from source
    incident_date: str  # ISO date
    incident_time: Optional[str] = None
    incident_type: str = ""
    location: str = ""
    city: str = ""
    county: str = ""
    agency: str = ""
    subject_name: Optional[str] = None
    subject_age: Optional[int] = None
    subject_gender: Optional[str] = None
    charges: list[str] = field(default_factory=list)
    narrative: str = ""
    raw_data: dict = field(default_factory=dict)
    url: str = ""
    scraped_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def fingerprint(self) -> str:
        """Deduplication key — stable across rescrapes."""
        parts = [
            self.source_key,
            self.raw_id,
            self.incident_date,
            self.incident_type,
            self.location,
            self.subject_name or "",
        ]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


@dataclass
class SourceConfig:
    key: str
    display_name: str
    source_type: str  # 'html_table', 'arcgis', 'pdf', 'rss', 'custom'
    county: str
    city: str
    agency: str
    url: str
    enabled: bool = True
    poll_interval_hours: int = 24
    retention_days: int = 90
    # Type-specific configs
    table_selector: Optional[str] = None  # CSS selector for HTML tables
    date_field: Optional[str] = None  # For ArcGIS
    row_parser: Optional[Callable[[Any], ScrapedRecord]] = None  # Custom parser
    arcgis_layer_id: Optional[int] = None
    pdf_url_pattern: Optional[str] = None
    rss_item_selector: Optional[str] = None


# ---------------------------------------------------------------------------
# Source registry — Montana agencies
# ---------------------------------------------------------------------------

SOURCES: dict[str, SourceConfig] = {
    # === JAIL ROSTERS ===
    "missoula_jail": SourceConfig(
        key="missoula_jail",
        display_name="Missoula County Jail Roster",
        source_type="html_table",
        county="Missoula",
        city="Missoula",
        agency="Missoula County Sheriff",
        url="https://webapps.missoulacounty.us/jailroster/Inmates",
        table_selector="table.inmate-table",
        retention_days=90,
    ),
    "gallatin_jail": SourceConfig(
        key="gallatin_jail",
        display_name="Gallatin County Detention Center",
        source_type="html_table",
        county="Gallatin",
        city="Bozeman",
        agency="Gallatin County Sheriff",
        url="https://gallatin-so-mt.zuercherportal.com/#/inmates",
        enabled=False,  # JS-rendered, needs Playwright
        retention_days=90,
    ),
    "yellowstone_jail": SourceConfig(
        key="yellowstone_jail",
        display_name="Yellowstone County Detention Facility",
        source_type="html_table",
        county="Yellowstone",
        city="Billings",
        agency="Yellowstone County Sheriff",
        url="https://www.yellowstonecountymt.gov/sheriff/detention/dcsearch.asp",
        enabled=False,  # ASP form-based, needs POST
        retention_days=90,
    ),
    "flathead_jail": SourceConfig(
        key="flathead_jail",
        display_name="Flathead County Detention Center",
        source_type="html_table",
        county="Flathead",
        city="Kalispell",
        agency="Flathead County Sheriff",
        url="https://apps.flathead.mt.gov/jailroster/",
        retention_days=90,
    ),
    "cascade_jail": SourceConfig(
        key="cascade_jail",
        display_name="Cascade County Detention Center",
        source_type="html_table",
        county="Cascade",
        city="Great Falls",
        agency="Cascade County Sheriff",
        url="https://www.cascadecountymt.gov/departments/sheriff-coroner/detention-center",
        enabled=False,  # No public roster found yet
        retention_days=90,
    ),
    "lewis_clark_jail": SourceConfig(
        key="lewis_clark_jail",
        display_name="Lewis and Clark County Jail",
        source_type="html_table",
        county="Lewis and Clark",
        city="Helena",
        agency="Lewis and Clark County Sheriff",
        url="https://www.lccountymt.gov/sheriff/detention-center",
        enabled=False,  # No public roster found yet
        retention_days=90,
    ),
    # === POLICE BLOTTERS / CALLS FOR SERVICE ===
    "bozeman_calls": SourceConfig(
        key="bozeman_calls",
        display_name="Bozeman PD Calls for Service",
        source_type="arcgis",
        county="Gallatin",
        city="Bozeman",
        agency="Bozeman Police Department",
        url="https://gisweb.bozeman.net/hosted/rest/services/BPD_CFS_Public_30_Days/FeatureServer/0",
        date_field="DATE",
        arcgis_layer_id=0,
        retention_days=30,
    ),
    "bozeman_crime": SourceConfig(
        key="bozeman_crime",
        display_name="Bozeman PD Daily Case Reports",
        source_type="arcgis",
        county="Gallatin",
        city="Bozeman",
        agency="Bozeman Police Department",
        url="https://services3.arcgis.com/f4hk1qcfxRJ0L2BU/arcgis/rest/services/BPD_Daily_Case_Reports_2_view/FeatureServer/1",
        date_field="REPORTED_DATE",
        arcgis_layer_id=1,
        retention_days=90,
    ),
    "missoula_public_report": SourceConfig(
        key="missoula_public_report",
        display_name="Missoula City-County Daily Public Report",
        source_type="html_table",
        county="Missoula",
        city="Missoula",
        agency="Missoula County Sheriff",
        url="https://webapps.missoulacounty.us/dailypublicreport/",
        retention_days=30,
    ),
    "whitefish_blotter": SourceConfig(
        key="whitefish_blotter",
        display_name="Whitefish Police Blotter",
        source_type="html_table",
        county="Flathead",
        city="Whitefish",
        agency="Whitefish Police Department",
        url="https://www.cityofwhitefish.org/police-department/police-blotter",
        enabled=False,  # Needs custom parser
        retention_days=90,
    ),
    "kalispell_blotter": SourceConfig(
        key="kalispell_blotter",
        display_name="Kalispell Police Blotter",
        source_type="html_table",
        county="Flathead",
        city="Kalispell",
        agency="Kalispell Police Department",
        url="https://www.kalispell.com/police-department/police-blotter",
        enabled=False,  # Needs custom parser
        retention_days=90,
    ),
    "great_falls_blotter": SourceConfig(
        key="great_falls_blotter",
        display_name="Great Falls Police Blotter",
        source_type="html_table",
        county="Cascade",
        city="Great Falls",
        agency="Great Falls Police Department",
        url="https://www.greatfallsmt.net/police-department/police-blotter",
        enabled=False,  # Needs custom parser
        retention_days=90,
    ),
    "billings_blotter": SourceConfig(
        key="billings_blotter",
        display_name="Billings Police Blotter",
        source_type="html_table",
        county="Yellowstone",
        city="Billings",
        agency="Billings Police Department",
        url="https://www.billingsmt.gov/police-department/police-blotter",
        enabled=False,  # Needs custom parser
        retention_days=90,
    ),
    "helena_blotter": SourceConfig(
        key="helena_blotter",
        display_name="Helena Police Blotter",
        source_type="html_table",
        county="Lewis and Clark",
        city="Helena",
        agency="Helena Police Department",
        url="https://www.helenamt.gov/police-department/police-blotter",
        enabled=False,  # Needs custom parser
        retention_days=90,
    ),
}


# ---------------------------------------------------------------------------
# Scrapers by source_type
# ---------------------------------------------------------------------------

def _scrape_arcgis(cfg: SourceConfig, session: requests.Session, days_back: int = 7) -> list[ScrapedRecord]:
    """Scrape ArcGIS REST FeatureServer."""
    from urllib.parse import urlencode, urljoin

    since = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    where = f"{cfg.date_field} >= DATE '{since}'"

    params = {
        "where": where,
        "outFields": "*",
        "outSR": "4326",
        "f": "json",
        "resultRecordCount": 2000,
    }

    url = f"{cfg.url}/query"
    resp = session.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    records = []
    for feat in data.get("features", []):
        attrs = feat.get("attributes", {})
        raw_id = str(attrs.get("OBJECTID", attrs.get("GlobalID", "")))
        if not raw_id:
            continue

        # Parse date fields (ArcGIS timestamps are ms since epoch)
        date_val = attrs.get(cfg.date_field)
        if isinstance(date_val, (int, float)):
            dt = datetime.fromtimestamp(date_val / 1000, tz=timezone.utc)
            incident_date = dt.strftime("%Y-%m-%d")
            incident_time = dt.strftime("%H:%M")
        else:
            incident_date = str(date_val)[:10] if date_val else datetime.now().strftime("%Y-%m-%d")
            incident_time = None

        record = ScrapedRecord(
            source_key=cfg.key,
            source_type=cfg.source_type,
            raw_id=raw_id,
            incident_date=incident_date,
            incident_time=incident_time,
            incident_type=attrs.get("OFFENSE_CATEGORY", attrs.get("CRIME_TYPE", attrs.get("CALL_TYPE", ""))),
            location=attrs.get("LOCATION", attrs.get("ADDRESS", "")) or "",
            city=cfg.city,
            county=cfg.county,
            agency=cfg.agency,
            narrative=attrs.get("NARRATIVE", attrs.get("DESCRIPTION", "")) or "",
            raw_data=attrs,
            url=cfg.url,
        )
        records.append(record)

    return records


def _scrape_html_table(cfg: SourceConfig, session: requests.Session, days_back: int = 7) -> list[ScrapedRecord]:
    """Scrape HTML table using BeautifulSoup."""
    from bs4 import BeautifulSoup

    resp = session.get(cfg.url, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    table = soup.select_one(cfg.table_selector or "table")
    if not table:
        logger.warning(f"[{cfg.key}] No table found at {cfg.url}")
        return []

    records = []
    rows = table.find_all("tr")[1:]  # Skip header
    for row in rows:
        cells = row.find_all(["td", "th"])
        if not cells:
            continue

        # Generic extraction — override per-source as needed
        texts = [c.get_text(strip=True) for c in cells]
        record = ScrapedRecord(
            source_key=cfg.key,
            source_type=cfg.source_type,
            raw_id=sha256_text("|".join(texts))[:16],
            incident_date=datetime.now().strftime("%Y-%m-%d"),
            incident_type=texts[0] if texts else "",
            location=texts[1] if len(texts) > 1 else "",
            city=cfg.city,
            county=cfg.county,
            agency=cfg.agency,
            raw_data={"cells": texts},
            url=cfg.url,
        )
        records.append(record)

    return records


def _scrape_pdf(cfg: SourceConfig, session: requests.Session, days_back: int = 7) -> list[ScrapedRecord]:
    """Scrape PDF blotter — placeholder for pdfplumber integration."""
    logger.info(f"[{cfg.key}] PDF scraping not yet implemented")
    return []


def _scrape_rss(cfg: SourceConfig, session: requests.Session, days_back: int = 7) -> list[ScrapedRecord]:
    """Scrape RSS/Atom feed."""
    import xml.etree.ElementTree as ET

    resp = session.get(cfg.url, timeout=30)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    records = []
    # Handle RSS 2.0 and Atom
    items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    for item in items:
        title = item.findtext("title", "")
        link = item.findtext("link", "")
        pub_date = item.findtext("pubDate", "")
        description = item.findtext("description", "")

        record = ScrapedRecord(
            source_key=cfg.key,
            source_type=cfg.source_type,
            raw_id=sha256_text(link or title)[:16],
            incident_date=pub_date[:10] if pub_date else datetime.now().strftime("%Y-%m-%d"),
            incident_type=title,
            narrative=description,
            city=cfg.city,
            county=cfg.county,
            agency=cfg.agency,
            raw_data={"title": title, "link": link, "pubDate": pub_date},
            url=link or cfg.url,
        )
        records.append(record)

    return records


# ---------------------------------------------------------------------------
# Source-specific custom parsers
# ---------------------------------------------------------------------------

def _parse_missoula_public_report(cfg: SourceConfig, session: requests.Session, days_back: int = 7) -> list[ScrapedRecord]:
    """Custom parser for Missoula City-County Daily Public Report."""
    resp = session.get(cfg.url, timeout=30)
    resp.raise_for_status()
    html = resp.text

    RECORD_BLOCK_RE = re.compile(
        r'<tr id="rptrDefault_ctl\d+_header" class="RecordTitle">(?P<header>.*?)</tr>\s*'
        r'<tr id="rptrDefault_ctl\d+_address">(?P<address>.*?)</tr>\s*'
        r'<tr id="rptrDefault_ctl\d+_units">(?P<units>.*?)</tr>',
        re.IGNORECASE | re.DOTALL,
    )
    HEADER_META_RE = re.compile(
        r'<div class="CFSNumber">\s*(?P<incident_type>.*?)\s*'
        r'<span class="agency">\s*-\s*(?P<agency>[^<]+)</span>\s*<br\s*/?>\s*'
        r'<span>\((?P<cfs_number>[^)]+)\)</span>',
        re.IGNORECASE | re.DOTALL,
    )
    DATE_STAMP_RE = re.compile(
        r'<span class="IncidentDateStamp">\s*(?P<date>[^<]+?)\s*<br\s*/?>\s*(?P<time>[^<]+?)\s*</span>',
        re.IGNORECASE | re.DOTALL,
    )

    records = []
    for match in RECORD_BLOCK_RE.finditer(html):
        header = match.group("header")
        address = match.group("address")

        h = HEADER_META_RE.search(header)
        d = DATE_STAMP_RE.search(header)

        incident_type = h.group("incident_type").strip() if h else ""
        agency = h.group("agency").strip() if h else cfg.agency
        cfs_number = h.group("cfs_number").strip() if h else ""
        date_str = d.group("date").strip() if d else ""
        time_str = d.group("time").strip() if d else ""

        # Clean up date
        incident_date = ""
        for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d"):
            try:
                incident_date = datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
                break
            except ValueError:
                continue
        if not incident_date:
            incident_date = datetime.now().strftime("%Y-%m-%d")

        # Clean address HTML
        from bs4 import BeautifulSoup
        addr_soup = BeautifulSoup(address, "html.parser")
        location = addr_soup.get_text(separator=" ", strip=True)

        record = ScrapedRecord(
            source_key=cfg.key,
            source_type=cfg.source_type,
            raw_id=cfs_number or sha256_text(header)[:16],
            incident_date=incident_date,
            incident_time=time_str,
            incident_type=incident_type,
            location=location,
            city=cfg.city,
            county=cfg.county,
            agency=agency,
            narrative=header,
            raw_data={"header": header, "address": address, "cfs": cfs_number},
            url=cfg.url,
        )
        records.append(record)

    return records


def _parse_missoula_jail(cfg: SourceConfig, session: requests.Session, days_back: int = 7) -> list[ScrapedRecord]:
    """Custom parser for Missoula County Jail Roster."""
    resp = session.get(cfg.url, timeout=30)
    resp.raise_for_status()
    html = resp.text

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")

    records = []
    # The roster uses a table with inmate rows
    tables = soup.find_all("table")
    for table in tables:
        rows = table.find_all("tr")[1:]  # Skip header
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 4:
                continue

            texts = [c.get_text(strip=True) for c in cells]
            # Typical columns: Name, Age, Gender, Booking Date, Charges, Status
            name = texts[0] if texts else ""
            age_str = texts[1] if len(texts) > 1 else ""
            gender = texts[2] if len(texts) > 2 else ""
            booking_date = texts[3] if len(texts) > 3 else ""
            charges = texts[4] if len(texts) > 4 else ""

            try:
                age = int(age_str)
            except ValueError:
                age = None

            # Parse booking date
            incident_date = datetime.now().strftime("%Y-%m-%d")
            for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d"):
                try:
                    incident_date = datetime.strptime(booking_date, fmt).strftime("%Y-%m-%d")
                    break
                except ValueError:
                    continue

            record = ScrapedRecord(
                source_key=cfg.key,
                source_type="jail_booking",
                raw_id=sha256_text(name + booking_date)[:16],
                incident_date=incident_date,
                incident_type="Jail Booking",
                location="",
                city=cfg.city,
                county=cfg.county,
                agency=cfg.agency,
                subject_name=name,
                subject_age=age,
                subject_gender=gender,
                charges=[c.strip() for c in charges.split(";") if c.strip()],
                raw_data={"cells": texts},
                url=cfg.url,
            )
            records.append(record)

    return records


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

SCRAPER_DISPATCH: dict[str, Callable] = {
    "arcgis": _scrape_arcgis,
    "html_table": _scrape_html_table,
    "pdf": _scrape_pdf,
    "rss": _scrape_rss,
}

CUSTOM_PARSERS: dict[str, Callable] = {
    "missoula_public_report": _parse_missoula_public_report,
    "missoula_jail": _parse_missoula_jail,
}


def scrape_source(cfg: SourceConfig, session: requests.Session, days_back: int = 7) -> list[ScrapedRecord]:
    """Route to the correct scraper for a source config."""
    if cfg.key in CUSTOM_PARSERS:
        return CUSTOM_PARSERS[cfg.key](cfg, session, days_back)

    scraper = SCRAPER_DISPATCH.get(cfg.source_type)
    if not scraper:
        logger.error(f"[{cfg.key}] No scraper for source_type={cfg.source_type}")
        return []

    return scraper(cfg, session, days_back)


# ---------------------------------------------------------------------------
# Database storage
# ---------------------------------------------------------------------------

def _connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=config.DB_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {config.DB_BUSY_TIMEOUT_MS}")
    return conn


def ensure_scraped_records_schema(conn: sqlite3.Connection) -> None:
    """Create tables for scraped data if they don't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scraped_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fingerprint TEXT UNIQUE NOT NULL,
            source_key TEXT NOT NULL,
            source_type TEXT NOT NULL,
            raw_id TEXT NOT NULL,
            incident_date TEXT NOT NULL,
            incident_time TEXT,
            incident_type TEXT,
            location TEXT,
            city TEXT,
            county TEXT,
            agency TEXT,
            subject_name TEXT,
            subject_age INTEGER,
            subject_gender TEXT,
            charges TEXT,  -- JSON array
            narrative TEXT,
            raw_data TEXT,  -- JSON
            url TEXT,
            scraped_at TEXT DEFAULT (datetime('now')),
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scraped_source ON scraped_records(source_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scraped_date ON scraped_records(incident_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scraped_county ON scraped_records(county)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scraped_fingerprint ON scraped_records(fingerprint)")
    conn.commit()


def store_records(conn: sqlite3.Connection, records: list[ScrapedRecord]) -> dict[str, int]:
    """Store scraped records, skipping duplicates by fingerprint."""
    stats = {"inserted": 0, "skipped": 0, "errors": 0}

    for rec in records:
        fp = rec.fingerprint()
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO scraped_records (
                    fingerprint, source_key, source_type, raw_id, incident_date, incident_time,
                    incident_type, location, city, county, agency, subject_name, subject_age,
                    subject_gender, charges, narrative, raw_data, url, scraped_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fp, rec.source_key, rec.source_type, rec.raw_id, rec.incident_date,
                    rec.incident_time, rec.incident_type, rec.location, rec.city, rec.county,
                    rec.agency, rec.subject_name, rec.subject_age, rec.subject_gender,
                    json.dumps(rec.charges), rec.narrative, json.dumps(rec.raw_data), rec.url,
                    rec.scraped_at,
                ),
            )
            if conn.total_changes > 0:
                stats["inserted"] += 1
            else:
                stats["skipped"] += 1
        except Exception as e:
            logger.error(f"Store error for {fp}: {e}")
            stats["errors"] += 1

    conn.commit()
    return stats


def apply_retention(conn: sqlite3.Connection, source_key: str, retention_days: int) -> int:
    """Delete records older than retention_days for a source."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).strftime("%Y-%m-%d")
    cursor = conn.execute(
        "DELETE FROM scraped_records WHERE source_key = ? AND incident_date < ?",
        (source_key, cutoff),
    )
    conn.commit()
    return cursor.rowcount


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

def run_source(cfg: SourceConfig, session: requests.Session, dry_run: bool = False, days_back: int = 7) -> dict[str, Any]:
    """Scrape a single source and store results."""
    logger.info(f"[{cfg.key}] Starting scrape: {cfg.display_name}")

    job_id = ensure_ingestion_job(cfg.key)
    status = get_ingestion_job_status(cfg.key)
    if status == "running":
        logger.warning(f"[{cfg.key}] Job already running, skipping")
        return {"source": cfg.key, "status": "skipped", "reason": "already_running"}

    set_ingestion_job_status(cfg.key, "running")
    log_scraper_event(cfg.key, "scrape_start", {"url": cfg.url, "days_back": days_back})

    try:
        records = scrape_source(cfg, session, days_back)
        log_scraper_event(cfg.key, "scrape_raw", {"count": len(records)})

        if dry_run:
            logger.info(f"[{cfg.key}] DRY RUN — would store {len(records)} records")
            set_ingestion_job_status(cfg.key, "dry_run")
            return {
                "source": cfg.key,
                "status": "dry_run",
                "records_found": len(records),
                "sample": [r.__dict__ for r in records[:3]],
            }

        conn = _connect_db()
        ensure_scraped_records_schema(conn)
        stats = store_records(conn, records)
        deleted = apply_retention(conn, cfg.key, cfg.retention_days)
        conn.close()

        set_ingestion_job_status(cfg.key, "success")
        log_scraper_event(cfg.key, "scrape_complete", stats)

        return {
            "source": cfg.key,
            "status": "success",
            "records_found": len(records),
            "inserted": stats["inserted"],
            "skipped": stats["skipped"],
            "errors": stats["errors"],
            "retention_deleted": deleted,
        }

    except Exception as e:
        logger.exception(f"[{cfg.key}] Scrape failed: {e}")
        increment_ingestion_retry(cfg.key)
        set_ingestion_job_status(cfg.key, "failed")
        log_scraper_event(cfg.key, "scrape_error", {"error": str(e)})
        return {"source": cfg.key, "status": "failed", "error": str(e)}


def run_all(sources: Optional[list[str]] = None, dry_run: bool = False, days_back: int = 7) -> list[dict[str, Any]]:
    """Run all enabled sources (or specified subset)."""
    session = _make_session()
    results = []

    keys = sources or [k for k, cfg in SOURCES.items() if cfg.enabled]
    for key in keys:
        cfg = SOURCES.get(key)
        if not cfg:
            logger.warning(f"Unknown source: {key}")
            continue
        if not cfg.enabled:
            logger.info(f"[{key}] Skipping disabled source")
            continue

        result = run_source(cfg, session, dry_run, days_back)
        results.append(result)
        time.sleep(1)  # Be polite between sources

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Montana Blotter scraper framework")
    parser.add_argument("--source", default="all", help="Source key to scrape (or 'all')")
    parser.add_argument("--dry-run", action="store_true", help="Print records without storing")
    parser.add_argument("--days-back", type=int, default=7, help="How many days to look back")
    parser.add_argument("--retention-days", type=int, help="Override retention policy")
    parser.add_argument("--list", action="store_true", help="List available sources")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.list:
        print("Available sources:")
        for key, cfg in sorted(SOURCES.items()):
            status = "enabled" if cfg.enabled else "disabled"
            print(f"  {key:20s} [{status:8s}] {cfg.display_name} ({cfg.source_type})")
        return

    if args.source == "all":
        results = run_all(dry_run=args.dry_run, days_back=args.days_back)
    else:
        results = run_all(sources=[args.source], dry_run=args.dry_run, days_back=args.days_back)

    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
