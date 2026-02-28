"""
crimemapping_fetcher.py
=======================
Daily incident puller from CrimeMapping.com for Billings, MT
(and any other agency on the platform).

CrimeMapping.com is operated by CentralSquare Technologies. Their incident
data is publicly displayed on their map portal. This module calls the same
internal JSON endpoints the map UI uses.

IMPORTANT: There is no official documented public API. Verify compliance with
https://www.crimemapping.com/terms before using in production. Consider
requesting a direct data feed from the Billings Police Department instead.

HOW THIS WORKS (reverse-engineered from browser DevTools):
  1. POST /map/MapUpdated     → returns 104 pin records (max per page)
  2. POST /Map/CrimeIncidents_Read → returns full incident list (up to 200/page)

AGENCY IDs (integer, from GetOrganizations):
  Billings Police      : 513
  Great Falls Police   : 587
  Montana State Univ   : 682
  Carbon County Sheriff: 641
  Red Lodge Police     : 643

BOUNDING BOX for Billings, MT (Web Mercator EPSG:3857):
  Center: x=-12078957, y=5745884
  Use a 30km radius box around the center.
"""

import sqlite3
import logging
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BILLINGS_PD_CONFIG = {
    "org_id":      513,                          # integer ID from GetOrganizations
    "agency_name": "Billings Police Department",
    "agency_type": "police",
    "county":      "Yellowstone",
    "city":        "Billings",
    # Bounding box center for Billings, MT in Web Mercator (EPSG:3857)
    # Box is built as center ± radius in fetch_incidents()
    "cx": -12078957.0,
    "cy":   5745884.0,
    "radius_m": 30000,   # 30 km
}

# Crime category IDs → labels (from CrimeMapping map UI)
CRIME_CATEGORY_MAP = {
    1:  "Arson",
    2:  "Assault",
    3:  "Burglary",
    4:  "Disturbing the Peace",
    5:  "Drugs/Alcohol Violation",
    6:  "DUI",
    7:  "Fraud",
    8:  "Homicide",
    9:  "Motor Vehicle Theft",
    10: "Robbery",
    11: "Sex Crime",
    12: "Theft/Larceny",
    13: "Vandalism",
    14: "Vehicle Break-In/Theft",
    15: "Weapons Violation",
}

ALL_CRIME_CATEGORY_IDS = list(CRIME_CATEGORY_MAP.keys())

# Endpoints (confirmed working as of 2026-02)
ENDPOINT_MAP_UPDATED        = "https://www.crimemapping.com/map/MapUpdated"
ENDPOINT_CRIME_INCIDENTS    = "https://www.crimemapping.com/Map/CrimeIncidents_Read"

# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent":       "Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://montanablotter.com)",
    "Accept":           "application/json, text/javascript, */*; q=0.01",
    "Accept-Language":  "en-US,en;q=0.9",
    "Referer":          "https://www.crimemapping.com/map/mt/billings/",
    "X-Requested-With": "XMLHttpRequest",
})


# ---------------------------------------------------------------------------
# Build filter payload
# ---------------------------------------------------------------------------

def _build_filter(org_id: int, cx: float, cy: float, radius_m: float,
                  start_date: datetime, end_date: datetime) -> dict:
    """Construct the filterdata JSON object for CrimeMapping API calls."""
    xmin, ymin = cx - radius_m, cy - radius_m
    xmax, ymax = cx + radius_m, cy + radius_m

    polygon_json = {
        "rings": [[[xmin, ymin], [xmax, ymin], [xmax, ymax],
                   [xmin, ymax], [xmin, ymin]]],
        "spatialReference": {"wkid": 102100},
    }

    return {
        "SelectedCategories": ALL_CRIME_CATEGORY_IDS,
        "SpatialFilter": {
            "FilterType": 2,
            "Filter": json.dumps(polygon_json),
        },
        "TemporalFilter": {
            "PreviousID":      "-1",
            "PreviousNumDays": 0,
            "PreviousName":    "Custom Range",
            "FilterType":      "Explicit",
            "ExplicitStartDate": start_date.strftime("%Y%m%d"),
            "ExplicitEndDate":   end_date.strftime("%Y%m%d"),
        },
        "AgencyFilter": [org_id],
    }


# ---------------------------------------------------------------------------
# Fetch incidents
# ---------------------------------------------------------------------------

def fetch_incidents(
    org_id: int,
    cx: float, cy: float, radius_m: float,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> list[dict]:
    """
    Pull incident records from CrimeMapping.com for a given agency.

    Parameters
    ----------
    org_id     : Integer org ID (from GetOrganizations). Billings PD = 513.
    cx / cy    : Bounding-box center in Web Mercator (EPSG:3857).
    radius_m   : Half-width of square bounding box in metres.
    start_date : Start of window (default: yesterday 00:00 UTC).
    end_date   : End of window   (default: today    00:00 UTC).

    Returns
    -------
    List of normalised raw incident dicts from CrimeIncidents_Read.
    """
    now   = datetime.now(timezone.utc)
    start = start_date or (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    end   = end_date   or now.replace(hour=0, minute=0, second=0, microsecond=0)

    filter_obj = _build_filter(org_id, cx, cy, radius_m, start, end)

    logger.info(
        f"Fetching CrimeMapping: {start.date()} → {end.date()} | org_id={org_id}"
    )

    # Step 1: hit MapUpdated to prime the session (also gives total count)
    try:
        map_resp = _SESSION.post(
            ENDPOINT_MAP_UPDATED,
            data={"filterdata": json.dumps(filter_obj), "alertID": "", "spatfilter": ""},
            timeout=30,
        )
        map_resp.raise_for_status()
        map_data = map_resp.json()
        total = map_data.get("result", {}).get("nr", "?")
        logger.info(f"CrimeMapping MapUpdated: {total} total incidents reported")
    except Exception as e:
        logger.warning(f"MapUpdated preflight failed: {e} — continuing anyway")

    # Step 2: fetch full incident list (paginated, 200/page)
    all_incidents: list[dict] = []
    page = 1
    page_size = 200

    while True:
        try:
            resp = _SESSION.post(
                ENDPOINT_CRIME_INCIDENTS,
                data={
                    "paramFilt":        json.dumps(filter_obj),
                    "unmappableOrgIDs": "[]",
                    "skip":             (page - 1) * page_size,
                    "take":             page_size,
                    "page":             page,
                    "pageSize":         page_size,
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            logger.error(f"CrimeIncidents_Read request failed (page {page}): {e}")
            break
        except ValueError:
            logger.error(f"Non-JSON response from CrimeIncidents_Read (page {page})")
            break

        batch = data.get("Data", [])
        all_incidents.extend(batch)
        logger.info(f"CrimeMapping page {page}: got {len(batch)} incidents")

        if len(batch) < page_size:
            break   # last page
        page += 1

    logger.info(f"CrimeMapping: {len(all_incidents)} total incidents fetched")
    return all_incidents


# ---------------------------------------------------------------------------
# Normalise a CrimeIncidents_Read row → records-table dict
# ---------------------------------------------------------------------------

# Regex to extract crime category ID from image src
_RE_CRIME_ID = re.compile(r"Identify/(\d+)\.svg", re.I)


def normalise_incident(raw: dict, county: str, city: str) -> dict:
    """
    Map CrimeIncidents_Read fields to the montanablotter records schema.

    CrimeIncidents_Read fields:
      Type (HTML img tag), Description, IncidentNum, Location,
      Agency, Date (int: YYYYMMDDHHmmss), MapIt (HTML anchor)
    """
    # --- Crime type ---
    m = _RE_CRIME_ID.search(raw.get("Type", ""))
    crime_id = int(m.group(1)) if m else 0
    incident_type = CRIME_CATEGORY_MAP.get(crime_id, raw.get("Description") or "Unknown")

    # --- Date / time ---
    # Date field is an integer like 20260228025700 → 2026-02-28 02:57:00
    date_raw = str(raw.get("Date", ""))
    parsed_date = ""
    parsed_time = ""
    if len(date_raw) >= 12:
        try:
            dt = datetime.strptime(date_raw[:14], "%Y%m%d%H%M%S")
            parsed_date = dt.strftime("%m/%d/%y")
            parsed_time = dt.strftime("%H:%M")
        except ValueError:
            pass

    # --- Incident / case number ---
    # IncidentNum is often empty in the list view; the MapIt anchor contains
    # the record GUID (e.g. "0_ae054ee4-...") which we use as a stable key.
    incident_num = raw.get("IncidentNum", "").strip()
    mapit = raw.get("MapIt", "")
    m_guid = re.search(r"ReportMapIt\('([^']+)'\)", mapit)
    record_guid = m_guid.group(1) if m_guid else ""

    cfs_number = incident_num or record_guid

    return {
        "cfs_number":    cfs_number,
        "date":          parsed_date,
        "time":          parsed_time,
        "incident_type": incident_type,
        "incident":      incident_type,   # legacy NOT NULL column
        "location":      raw.get("Location") or "",
        "details":       raw.get("Description") or "",
        "county":        county,
        "officer":       "",              # CrimeMapping does not expose officer names
        "_record_guid":  record_guid,
    }


# ---------------------------------------------------------------------------
# Full ingest pipeline
# ---------------------------------------------------------------------------

def ingest_crimemapping(
    config_override: Optional[dict] = None,
    days_back: int = 1,
) -> int:
    """
    Full pipeline: fetch → normalise → write to DB → summarise → audit.

    Parameters
    ----------
    config_override : Pass a dict to override BILLINGS_PD_CONFIG fields.
    days_back       : How many days back to pull (default 1 = yesterday).

    Returns
    -------
    blotter_id of the created blotter record, or 0 on failure.
    """
    cfg = {**BILLINGS_PD_CONFIG, **(config_override or {})}

    now   = datetime.now(timezone.utc)
    end   = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days_back)

    raw_incidents = fetch_incidents(
        org_id=cfg["org_id"],
        cx=cfg["cx"], cy=cfg["cy"], radius_m=cfg["radius_m"],
        start_date=start, end_date=end,
    )

    if not raw_incidents:
        logger.info("No CrimeMapping incidents returned — nothing to ingest.")
        return 0

    # Deduplicate by CFS / record GUID against existing records
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    existing_cfs = {
        r["cfs_number"]
        for r in conn.execute(
            "SELECT cfs_number FROM records WHERE county = ? AND cfs_number != ''",
            (cfg["county"],),
        ).fetchall()
    }

    rows = []
    for raw in raw_incidents:
        rec = normalise_incident(raw, cfg["county"], cfg["city"])
        if rec["cfs_number"] and rec["cfs_number"] in existing_cfs:
            continue    # already in DB
        rows.append(rec)

    if not rows:
        logger.info("All CrimeMapping incidents already exist in DB — skipping.")
        conn.close()
        return 0

    # Insert blotter batch
    cursor = conn.cursor()
    source_label = f"crimemapping-{cfg['city'].lower()}-{end.strftime('%Y%m%d')}"
    cursor.execute(
        "INSERT INTO blotters (filename, county, incident_count, source_type) VALUES (?, ?, ?, ?)",
        (source_label, cfg["county"], len(rows), "crimemapping"),
    )
    blotter_id = cursor.lastrowid

    for rec in rows:
        cursor.execute(
            """
            INSERT INTO records
                (blotter_id, cfs_number, date, time, incident_type,
                 incident, location, details, county, officer)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                blotter_id,
                rec["cfs_number"], rec["date"], rec["time"],
                rec["incident_type"], rec["incident"],
                rec["location"], rec["details"],
                rec["county"], rec["officer"],
            ),
        )

    conn.commit()
    logger.info(f"Inserted {len(rows)} CrimeMapping incidents as blotter #{blotter_id}")

    # Trigger AI summariser
    try:
        import summarizer
        post_count = summarizer.generate_posts(blotter_id)
        logger.info(f"Generated {post_count} posts for CrimeMapping blotter #{blotter_id}")
    except Exception as e:
        logger.warning(f"Summariser failed for blotter #{blotter_id}: {e}")

    # Trigger PII auditor
    try:
        import blotter_auditor
        results = blotter_auditor.audit_blotter_posts(blotter_id)
        flagged = [r for r in results if not r.audit_passed]
        logger.info(f"Auditor: {len(results)} post(s), {len(flagged)} flagged")
    except Exception as e:
        logger.warning(f"Auditor failed for blotter #{blotter_id}: {e}")

    conn.close()
    return blotter_id


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Pull CrimeMapping incidents into Montana Blotter")
    parser.add_argument("--days-back", type=int, default=1, help="Days to look back (default 1)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and print without writing to DB")
    args = parser.parse_args()

    if args.dry_run:
        cfg = BILLINGS_PD_CONFIG
        now = datetime.now(timezone.utc)
        end = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = end - timedelta(days=args.days_back)
        raw = fetch_incidents(
            org_id=cfg["org_id"],
            cx=cfg["cx"], cy=cfg["cy"], radius_m=cfg["radius_m"],
            start_date=start, end_date=end,
        )
        for inc in raw:
            norm = normalise_incident(inc, cfg["county"], cfg["city"])
            print(json.dumps(norm, indent=2, default=str))
        print(f"\n--- {len(raw)} incidents fetched ---")
    else:
        bid = ingest_crimemapping(days_back=args.days_back)
        print(f"Done — blotter_id={bid}")
