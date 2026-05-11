"""
crimemapping_fetcher.py
=======================
Daily incident puller from CrimeMapping.com for all Montana agencies
on the platform.

CrimeMapping.com is operated by CentralSquare Technologies. Their incident
data is publicly displayed on their map portal. This module calls the same
internal JSON endpoints the map UI uses.

IMPORTANT: There is no official documented public API. Verify compliance with
https://www.crimemapping.com/terms before using in production.

HOW THIS WORKS (reverse-engineered from browser DevTools):
  1. POST /map/MapUpdated          → primes session, returns total count
  2. POST /Map/CrimeIncidents_Read → returns full paginated incident list

CLI USAGE:
  python crimemapping_fetcher.py --all-montana          # ingest all 8 MT agencies
  python crimemapping_fetcher.py --org-id 513           # one agency by ID
  python crimemapping_fetcher.py --all-montana --dry-run
  python crimemapping_fetcher.py --days-back 7          # backfill last week
"""

import sqlite3
import logging
import json
import re
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
import config
from core.dedupe import incident_key_set, incident_keys

logger = logging.getLogger(__name__)
DB_TIMEOUT_SECONDS = float(getattr(config, "DB_TIMEOUT_SECONDS", 30))
DB_BUSY_TIMEOUT_MS = int(getattr(config, "DB_BUSY_TIMEOUT_MS", 30000))
SCOUT_MAX_ATTEMPTS = max(2, int(getattr(config, "SCOUT_HTTP_MAX_ATTEMPTS", 5)))
SCOUT_BASE_DELAY_SECONDS = max(0.25, float(getattr(config, "SCOUT_HTTP_BASE_DELAY_SECONDS", 0.75)))
SCOUT_MAX_DELAY_SECONDS = max(1.0, float(getattr(config, "SCOUT_HTTP_MAX_DELAY_SECONDS", 12.0)))
SCOUT_JITTER_SECONDS = max(0.0, float(getattr(config, "SCOUT_HTTP_JITTER_SECONDS", 0.8)))
SCOUT_INTER_PAGE_DELAY_MIN = max(0.0, float(getattr(config, "SCOUT_INTER_PAGE_DELAY_MIN_SECONDS", 0.15)))
SCOUT_INTER_PAGE_DELAY_MAX = max(SCOUT_INTER_PAGE_DELAY_MIN, float(getattr(config, "SCOUT_INTER_PAGE_DELAY_MAX_SECONDS", 1.0)))

ROTATING_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:137.0) Gecko/20100101 Firefox/137.0",
]


def _load_existing_incident_keys(
    conn: sqlite3.Connection,
    county: str,
    rows: list[dict],
) -> set[str]:
    raw_dates = sorted({(row.get("date") or "").strip() for row in rows if row.get("date")})
    cfs_numbers = sorted({(row.get("cfs_number") or "").strip() for row in rows if row.get("cfs_number")})

    clauses = []
    params: list[str] = []
    if raw_dates:
        placeholders = ",".join("?" for _ in raw_dates)
        clauses.append(f"(county = ? AND date IN ({placeholders}))")
        params.extend([county, *raw_dates])
    if cfs_numbers:
        placeholders = ",".join("?" for _ in cfs_numbers)
        clauses.append(f"(cfs_number IN ({placeholders}))")
        params.extend(cfs_numbers)
    if not clauses:
        return set()

    existing_rows = conn.execute(
        f"""
        SELECT cfs_number, date, time,
               COALESCE(incident_type, incident, '') AS incident_type,
               COALESCE(location, '') AS location,
               COALESCE(details, '') AS details,
               county
        FROM records
        WHERE {' OR '.join(clauses)}
        """,
        params,
    ).fetchall()
    return incident_key_set(existing_rows)

# ---------------------------------------------------------------------------
# All Montana agencies on CrimeMapping (as of 2026-02)
# org_id from /map/GetOrganizations; cx/cy from the same response
# radius_m: 30 km box works for city PDs; 60 km for county sheriffs
# ---------------------------------------------------------------------------

MONTANA_AGENCIES = [
    {
        "org_id":      513,
        "agency_name": "Billings Police Department",
        "county":      "Yellowstone",
        "city":        "Billings",
        "cx": -12078957.0,  "cy": 5745884.0,  "radius_m": 30000,
    },
    {
        "org_id":      587,
        "agency_name": "Great Falls Police Department",
        "county":      "Cascade",
        "city":        "Great Falls",
        "cx": -12390407.0,  "cy": 6024222.0,  "radius_m": 30000,
    },
    {
        "org_id":      122,
        "agency_name": "Flathead County Sheriff",
        "county":      "Flathead",
        "city":        "Kalispell",
        "cx": -12724965.0,  "cy": 6138410.0,  "radius_m": 60000,
    },
    {
        "org_id":      682,
        "agency_name": "Montana State University Police",
        "county":      "Gallatin",
        "city":        "Bozeman",
        "cx": -12362616.0,  "cy": 5727149.0,  "radius_m": 15000,
    },
    {
        "org_id":      641,
        "agency_name": "Carbon County Sheriff",
        "county":      "Carbon",
        "city":        "Red Lodge",
        "cx": -12161328.0,  "cy": 5652275.0,  "radius_m": 60000,
    },
    {
        "org_id":      643,
        "agency_name": "Red Lodge Police Department",
        "county":      "Carbon",
        "city":        "Red Lodge",
        "cx": -12161327.0,  "cy": 5651152.0,  "radius_m": 15000,
    },
    {
        "org_id":      642,
        "agency_name": "Bridger Police Department",
        "county":      "Carbon",
        "city":        "Bridger",
        "cx": -12124267.0,  "cy": 5668027.0,  "radius_m": 15000,
    },
    {
        "org_id":      620,
        "agency_name": "Chouteau County Sheriff",
        "county":      "Chouteau",
        "city":        "Fort Benton",
        "cx": -12318506.0,  "cy": 6078687.0,  "radius_m": 60000,
    },
]

# Convenience lookup by org_id
AGENCIES_BY_ID = {a["org_id"]: a for a in MONTANA_AGENCIES}

# Keep backward compat
BILLINGS_PD_CONFIG = AGENCIES_BY_ID[513]

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
    "User-Agent":       random.choice(ROTATING_USER_AGENTS),
    "Accept":           "application/json, text/javascript, */*; q=0.01",
    "Accept-Language":  "en-US,en;q=0.9",
    "Referer":          "https://www.crimemapping.com/map/mt/billings/",
    "X-Requested-With": "XMLHttpRequest",
})


def _rotate_session_headers(session: requests.Session) -> None:
    session.headers.update({
        "User-Agent": random.choice(ROTATING_USER_AGENTS),
        "Referer": f"https://www.crimemapping.com/map/mt/{random.choice(['billings', 'great-falls', 'kalispell', 'bozeman'])}/",
    })


def _request_with_retry(
    session: requests.Session,
    *,
    method: str,
    url: str,
    data: dict,
    timeout: int = 30,
) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(1, SCOUT_MAX_ATTEMPTS + 1):
        _rotate_session_headers(session)
        try:
            response = session.request(method, url, data=data, timeout=timeout)
            status = int(response.status_code or 0)
            if status in {403, 429} or 500 <= status <= 599:
                last_exc = RuntimeError(f"HTTP {status} for {url}")
                if attempt >= SCOUT_MAX_ATTEMPTS:
                    response.raise_for_status()
                backoff = min(SCOUT_MAX_DELAY_SECONDS, SCOUT_BASE_DELAY_SECONDS * (2 ** (attempt - 1)))
                sleep_for = backoff + random.uniform(0, SCOUT_JITTER_SECONDS)
                logger.warning(
                    "Scout request throttled/blocked (%s) on attempt %s/%s; retrying in %.2fs",
                    status,
                    attempt,
                    SCOUT_MAX_ATTEMPTS,
                    sleep_for,
                )
                time.sleep(sleep_for)
                continue
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_exc = exc
            if attempt >= SCOUT_MAX_ATTEMPTS:
                raise
            backoff = min(SCOUT_MAX_DELAY_SECONDS, SCOUT_BASE_DELAY_SECONDS * (2 ** (attempt - 1)))
            sleep_for = backoff + random.uniform(0, SCOUT_JITTER_SECONDS)
            logger.warning(
                "Scout request failed (%s) attempt %s/%s; retrying in %.2fs",
                exc.__class__.__name__,
                attempt,
                SCOUT_MAX_ATTEMPTS,
                sleep_for,
            )
            time.sleep(sleep_for)
    if last_exc:
        raise RuntimeError(str(last_exc))
    raise RuntimeError("request failed with unknown error")


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
        map_resp = _request_with_retry(
            _SESSION,
            method="POST",
            url=ENDPOINT_MAP_UPDATED,
            data={"filterdata": json.dumps(filter_obj), "alertID": "", "spatfilter": ""},
            timeout=30,
        )
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
            resp = _request_with_retry(
                _SESSION,
                method="POST",
                url=ENDPOINT_CRIME_INCIDENTS,
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
            data = resp.json()
        except ValueError:
            logger.error(f"Non-JSON response from CrimeIncidents_Read (page {page})")
            raise RuntimeError(f"Non-JSON response from CrimeIncidents_Read (page {page})")
        except Exception as e:
            logger.error(f"CrimeIncidents_Read request failed (page {page}): {e}")
            raise RuntimeError(f"CrimeIncidents_Read request failed (page {page}): {e}") from e

        batch = data.get("Data", [])
        all_incidents.extend(batch)
        logger.info(f"CrimeMapping page {page}: got {len(batch)} incidents")
        time.sleep(random.uniform(SCOUT_INTER_PAGE_DELAY_MIN, SCOUT_INTER_PAGE_DELAY_MAX))

        if len(batch) < page_size:
            break   # last page
        page += 1

    logger.info(f"CrimeMapping: {len(all_incidents)} total incidents fetched")
    return all_incidents


def smoke_check_agency(
    agency: dict,
    *,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> dict:
    """
    Probe a single CrimeMapping agency without writing to the database.

    Unlike `fetch_incidents`, this is strict about network failures so a smoke
    check can distinguish "0 incidents today" from "endpoint unavailable".
    """
    now = datetime.now(timezone.utc)
    start = start_date or (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    end = end_date or now.replace(hour=0, minute=0, second=0, microsecond=0)
    filter_obj = _build_filter(
        agency["org_id"],
        agency["cx"],
        agency["cy"],
        agency["radius_m"],
        start,
        end,
    )

    map_resp = _request_with_retry(
        _SESSION,
        method="POST",
        url=ENDPOINT_MAP_UPDATED,
        data={"filterdata": json.dumps(filter_obj), "alertID": "", "spatfilter": ""},
        timeout=30,
    )
    map_data = map_resp.json()
    map_total = map_data.get("result", {}).get("nr", 0)

    resp = _request_with_retry(
        _SESSION,
        method="POST",
        url=ENDPOINT_CRIME_INCIDENTS,
        data={
            "paramFilt": json.dumps(filter_obj),
            "unmappableOrgIDs": "[]",
            "skip": 0,
            "take": 5,
            "page": 1,
            "pageSize": 5,
        },
        timeout=30,
    )
    data = resp.json()
    if "Data" not in data:
        raise RuntimeError(f"CrimeMapping probe returned unexpected payload for org_id={agency['org_id']}")

    return {
        "agency_name": agency["agency_name"],
        "org_id": agency["org_id"],
        "window_start": start.date().isoformat(),
        "window_end": end.date().isoformat(),
        "map_total": int(map_total or 0),
        "sample_count": len(data.get("Data") or []),
    }


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
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> int:
    """
    Full pipeline: fetch → normalise → write to DB → summarise → audit.

    Parameters
    ----------
    config_override : Pass a dict to override BILLINGS_PD_CONFIG fields.
    days_back       : How many days back to pull (default 1 = yesterday).
                      Ignored when start_date/end_date are provided.
    start_date      : Explicit window start (UTC midnight).
    end_date        : Explicit window end   (UTC midnight).

    Returns
    -------
    blotter_id of the created blotter record, or 0 on failure.
    """
    cfg = {**BILLINGS_PD_CONFIG, **(config_override or {})}

    now = datetime.now(timezone.utc)
    if start_date and end_date:
        start, end = start_date, end_date
    else:
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

    # Deduplicate by record GUID and a cross-source incident signature
    conn = sqlite3.connect(config.DB_PATH, timeout=DB_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {DB_BUSY_TIMEOUT_MS}")
    existing_keys = _load_existing_incident_keys(conn, cfg["county"], [
        normalise_incident(raw, cfg["county"], cfg["city"]) for raw in raw_incidents
    ])

    rows = []
    for raw in raw_incidents:
        rec = normalise_incident(raw, cfg["county"], cfg["city"])
        rec_keys = incident_keys(rec, county=cfg["county"])
        if rec_keys and rec_keys & existing_keys:
            continue
        rows.append(rec)
        existing_keys.update(rec_keys)

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
    parser = argparse.ArgumentParser(
        description="Pull CrimeMapping incidents into Montana Blotter"
    )
    parser.add_argument(
        "--all-montana", action="store_true",
        help="Fetch all Montana agencies (default when no --org-id given)",
    )
    parser.add_argument(
        "--org-id", type=int, default=None,
        help="Fetch a single agency by CrimeMapping org ID",
    )
    parser.add_argument("--days-back", type=int, default=1, help="Days to look back (default 1)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and print without writing to DB")
    parser.add_argument("--list", action="store_true", help="List all configured Montana agencies")
    args = parser.parse_args()

    if args.list:
        print(f"{'ID':>6}  {'County':<16}  {'City':<14}  Agency")
        print("-" * 66)
        for a in MONTANA_AGENCIES:
            print(f"{a['org_id']:>6}  {a['county']:<16}  {a['city']:<14}  {a['agency_name']}")
        raise SystemExit(0)

    # Decide which agencies to run
    if args.org_id:
        if args.org_id not in AGENCIES_BY_ID:
            parser.error(f"org_id {args.org_id} not found. Use --list to see available agencies.")
        agencies = [AGENCIES_BY_ID[args.org_id]]
    else:
        agencies = MONTANA_AGENCIES   # --all-montana is the default

    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Build list of (start, end) day windows — one blotter per day
    day_windows = [
        (today - timedelta(days=d+1), today - timedelta(days=d))
        for d in range(args.days_back)
    ]  # most-recent day first

    if args.dry_run:
        for cfg in agencies:
            for day_start, day_end in day_windows:
                label = day_start.strftime("%Y-%m-%d")
                print(f"\n{'='*60}")
                print(f"DRY RUN: {cfg['agency_name']} | {label}")
                print(f"{'='*60}")
                raw = fetch_incidents(
                    org_id=cfg["org_id"],
                    cx=cfg["cx"], cy=cfg["cy"], radius_m=cfg["radius_m"],
                    start_date=day_start, end_date=day_end,
                )
                for inc in raw:
                    norm = normalise_incident(inc, cfg["county"], cfg["city"])
                    print(json.dumps(norm, indent=2, default=str))
                print(f"--- {len(raw)} incidents ---")
    else:
        total_blotters = 0
        for cfg in agencies:
            logger.info(f"--- Ingesting {cfg['agency_name']} ---")
            for day_start, day_end in day_windows:
                label = day_start.strftime("%Y-%m-%d")
                try:
                    bid = ingest_crimemapping(
                        config_override=cfg,
                        start_date=day_start,
                        end_date=day_end,
                    )
                    if bid:
                        total_blotters += 1
                        print(f"  {cfg['agency_name']} {label}: blotter_id={bid}")
                    else:
                        print(f"  {cfg['agency_name']} {label}: no new incidents")
                except Exception as exc:
                    logger.error(
                        "Scout failed for agency=%s window=%s (%s); continuing batch",
                        cfg["agency_name"],
                        label,
                        exc,
                    )
                    print(f"  {cfg['agency_name']} {label}: failed ({exc})")
        print(f"\nDone — {total_blotters} blotter(s) created")
