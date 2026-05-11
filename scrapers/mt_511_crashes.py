"""
MT 511 Crash / Incident Scraper
Checks 511.mt.gov for real-time highway incidents and crash data.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db

logger = logging.getLogger("mt_511_crashes")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
    logger.addHandler(handler)

# 511.mt.gov uses a Waze-like CCP feed or similar; we probe common endpoints.
# Primary: the public incident map API
INCIDENT_API_URL = "https://511.mt.gov/api/v1/incidents"
# Fallback: check if they expose a GeoJSON feed
GEOJSON_URL = "https://511.mt.gov/api/v1/incidents/geojson"

HEADERS = {
    "User-Agent": "MontanaBlotterBot/1.0 (public safety data)",
    "Accept": "application/json,application/geo+json,*/*",
}

MT_COUNTIES = [
    "Yellowstone", "Gallatin", "Missoula", "Flathead", "Cascade",
    "Lewis and Clark", "Ravalli", "Silver Bow", "Lake", "Lincoln",
    "Hill", "Park", "Glacier", "Big Horn", "Rosebud", "Carbon",
    "Stillwater", "Deer Lodge", "Sanders", "Madison", "Powell",
    "Custer", "Beaverhead", "Jefferson", "Valley", "Roosevelt",
    "Dawson", "Toole", "Richland", "McCone", "Phillips", "Fergus",
    "Teton", "Wheatland", "Pondera", "Granite", "Chouteau", "Broadwater",
    "Mineral", "Sheridan", "Sweet Grass", "Fallon", "Powder River",
    "Treasure", "Liberty", "Garfield", "Wibaux", "Petroleum",
    "Golden Valley", "Carter", "Judith Basin", "Daniels", "Musselshell",
    "Prairie", "Meagher", "Blaine",
]

HIGHWAYS = ["I-90", "I-15", "US-2", "US-93", "US-89", "US-12", "US-191", "MT-200"]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class CrashIncident:
    external_id: str
    location: str
    highway: str | None
    mile_marker: str | None
    incident_type: str
    severity: str  # minor, serious, fatal
    injuries: int | None
    fatalities: int | None
    road_status: str | None
    county: str | None
    nearest_city: str | None
    description: str
    reported_at: str | None
    cleared_at: str | None
    source_url: str
    raw_json: str

    def fingerprint(self) -> str:
        payload = f"{self.external_id}|{self.location}|{self.reported_at}"
        return hashlib.sha256(payload.encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def ensure_schema(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS highway_crashes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_id TEXT NOT NULL,
            location TEXT NOT NULL,
            highway TEXT,
            mile_marker TEXT,
            incident_type TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'minor',
            injuries INTEGER,
            fatalities INTEGER,
            road_status TEXT,
            county TEXT,
            nearest_city TEXT,
            description TEXT,
            reported_at TEXT,
            cleared_at TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            source_url TEXT,
            fingerprint TEXT NOT NULL UNIQUE,
            raw_json TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_crashes_county ON highway_crashes(county)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_crashes_highway ON highway_crashes(highway)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_crashes_active ON highway_crashes(is_active, reported_at)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_crashes_severity ON highway_crashes(severity)"
    )
    conn.commit()


# ---------------------------------------------------------------------------
# API fetchers
# ---------------------------------------------------------------------------
def _fetch_json(url: str, timeout: int = 60) -> dict | list | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.error("Failed to fetch %s: %s", url, exc)
        return None


def _extract_highway(text: str) -> str | None:
    text_upper = text.upper()
    for hw in HIGHWAYS:
        if hw.upper() in text_upper:
            return hw
    # Try loose patterns
    m = re.search(r"\b(I-\d{1,3}|US-\d{1,3}|MT-\d{1,3}|SR-\d{1,3})\b", text_upper)
    if m:
        return m.group(1)
    return None


def _extract_county(text: str) -> str | None:
    text_lower = text.lower()
    for county in MT_COUNTIES:
        if county.lower() in text_lower:
            return county
    return None


def _extract_mile_marker(text: str) -> str | None:
    m = re.search(r"(?:Mile\s*Marker?|MM|MP)\s*[:#]?\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:MP|MM)\b", text, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def _classify_severity(description: str, injuries: int | None, fatalities: int | None) -> str:
    desc_lower = description.lower()
    if fatalities and fatalities > 0:
        return "fatal"
    if injuries and injuries > 0:
        return "serious"
    if any(w in desc_lower for w in ("rollover", "head-on", "collision", "injury", "hospital")):
        return "serious"
    if any(w in desc_lower for w in ("fatal", "death", "deceased")):
        return "fatal"
    return "minor"


def _parse_511_incidents(data: dict | list | None) -> list[CrashIncident]:
    """Parse 511 API response into CrashIncident objects."""
    incidents: list[CrashIncident] = []
    if data is None:
        return incidents

    # Handle GeoJSON
    if isinstance(data, dict) and data.get("type") == "FeatureCollection":
        features = data.get("features", [])
    elif isinstance(data, dict) and "incidents" in data:
        features = data["incidents"]
    elif isinstance(data, list):
        features = data
    else:
        logger.warning("Unrecognized 511 response shape: %s", type(data))
        return incidents

    for item in features:
        # Normalize shape
        props = item.get("properties", item)
        geom = item.get("geometry", {})

        external_id = str(props.get("id", props.get("IncidentID", props.get("identifier", hashlib.sha256(str(item).encode()).hexdigest()[:16]))))
        location = props.get("location", props.get("Location", props.get("description", props.get("Description", "Unknown"))))
        if not location or location == "Unknown":
            # Try to build from geometry
            coords = geom.get("coordinates", [])
            if len(coords) >= 2:
                location = f"{coords[1]:.4f}, {coords[0]:.4f}"

        desc = props.get("description", props.get("Description", props.get("headline", "")))
        incident_type = props.get("type", props.get("Type", props.get("incidentType", "crash")))
        reported = props.get("startTime", props.get("created", props.get("reportedAt", None)))
        cleared = props.get("endTime", props.get("clearedAt", None))
        road_status = props.get("roadStatus", props.get("status", None))

        # Injury / fatality extraction
        injuries = None
        fatalities = None
        injury_match = re.search(r"(\d+)\s+injur", desc, re.IGNORECASE)
        if injury_match:
            injuries = int(injury_match.group(1))
        fatal_match = re.search(r"(\d+)\s+fatal", desc, re.IGNORECASE)
        if fatal_match:
            fatalities = int(fatal_match.group(1))

        highway = _extract_highway(location + " " + desc)
        county = _extract_county(location + " " + desc)
        mile_marker = _extract_mile_marker(location + " " + desc)
        severity = _classify_severity(desc, injuries, fatalities)

        incidents.append(
            CrashIncident(
                external_id=external_id,
                location=location,
                highway=highway,
                mile_marker=mile_marker,
                incident_type=incident_type,
                severity=severity,
                injuries=injuries,
                fatalities=fatalities,
                road_status=road_status,
                county=county,
                nearest_city=None,
                description=desc,
                reported_at=reported,
                cleared_at=cleared,
                source_url=INCIDENT_API_URL,
                raw_json=json.dumps(item, default=str),
            )
        )
    return incidents


# ---------------------------------------------------------------------------
# Database insert
# ---------------------------------------------------------------------------
def import_incidents(conn: sqlite3.Connection, incidents: list[CrashIncident]) -> dict[str, int]:
    cursor = conn.cursor()
    inserted = 0
    updated = 0
    for inc in incidents:
        fp = inc.fingerprint()
        cursor.execute("SELECT id FROM highway_crashes WHERE fingerprint = ?", (fp,))
        existing = cursor.fetchone()
        if existing:
            cursor.execute(
                """
                UPDATE highway_crashes
                SET location = ?, highway = ?, mile_marker = ?, incident_type = ?,
                    severity = ?, injuries = ?, fatalities = ?, road_status = ?,
                    county = ?, nearest_city = ?, description = ?, reported_at = ?,
                    cleared_at = ?, is_active = ?, source_url = ?, raw_json = ?,
                    updated_at = datetime('now')
                WHERE fingerprint = ?
                """,
                (inc.location, inc.highway, inc.mile_marker, inc.incident_type,
                 inc.severity, inc.injuries, inc.fatalities, inc.road_status,
                 inc.county, inc.nearest_city, inc.description, inc.reported_at,
                 inc.cleared_at, 1 if not inc.cleared_at else 0, inc.source_url,
                 inc.raw_json, fp),
            )
            updated += 1
        else:
            cursor.execute(
                """
                INSERT INTO highway_crashes
                (external_id, location, highway, mile_marker, incident_type, severity,
                 injuries, fatalities, road_status, county, nearest_city, description,
                 reported_at, cleared_at, is_active, source_url, fingerprint, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (inc.external_id, inc.location, inc.highway, inc.mile_marker,
                 inc.incident_type, inc.severity, inc.injuries, inc.fatalities,
                 inc.road_status, inc.county, inc.nearest_city, inc.description,
                 inc.reported_at, inc.cleared_at,
                 1 if not inc.cleared_at else 0, inc.source_url, fp, inc.raw_json),
            )
            inserted += 1
    conn.commit()
    return {"inserted": inserted, "updated": updated}


def mark_cleared_incidents(conn: sqlite3.Connection, active_ids: set[str]) -> int:
    """Mark incidents as cleared if they no longer appear in the feed."""
    cursor = conn.cursor()
    cursor.execute("SELECT external_id FROM highway_crashes WHERE is_active = 1")
    current_ids = {row["external_id"] for row in cursor.fetchall()}
    to_clear = current_ids - active_ids
    if to_clear:
        placeholders = ",".join("?" * len(to_clear))
        cursor.execute(
            f"""
            UPDATE highway_crashes
            SET is_active = 0, cleared_at = datetime('now'), updated_at = datetime('now')
            WHERE external_id IN ({placeholders})
            """,
            tuple(to_clear),
        )
        conn.commit()
    return len(to_clear)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def run_511_scrape(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    data = _fetch_json(INCIDENT_API_URL)
    if data is None:
        # Try GeoJSON fallback
        data = _fetch_json(GEOJSON_URL)
    if data is None:
        return {"status": "fetch_failure", "message": "Could not reach 511.mt.gov API"}

    incidents = _parse_511_incidents(data)
    if not incidents:
        return {"status": "no_data", "message": "No incidents found in feed"}

    close_conn = conn is None
    if conn is None:
        conn = db.connect_db()
    ensure_schema(conn)
    stats = import_incidents(conn, incidents)
    active_ids = {inc.external_id for inc in incidents}
    cleared = mark_cleared_incidents(conn, active_ids)
    if close_conn:
        conn.close()
    return {"status": "ok", **stats, "cleared": cleared, "total_active": len(active_ids)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scrape MT 511 crash/incident data")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, do not write to DB")
    args = parser.parse_args()

    data = _fetch_json(INCIDENT_API_URL)
    if data is None:
        data = _fetch_json(GEOJSON_URL)
    if data is None:
        logger.error("Could not fetch 511 data")
        sys.exit(1)

    incidents = _parse_511_incidents(data)
    logger.info("Parsed %d incidents", len(incidents))
    for inc in incidents[:5]:
        logger.info(
            "  %s | %s | %s | %s | injuries=%s fatalities=%s",
            inc.external_id, inc.highway, inc.county, inc.severity, inc.injuries, inc.fatalities
        )

    if not args.dry_run and incidents:
        conn = db.connect_db()
        ensure_schema(conn)
        stats = import_incidents(conn, incidents)
        active_ids = {inc.external_id for inc in incidents}
        cleared = mark_cleared_incidents(conn, active_ids)
        logger.info("DB stats: %s, cleared: %d", stats, cleared)
        conn.close()
