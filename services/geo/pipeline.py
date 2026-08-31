"""
Geocoding pipeline and crime mapping utilities for MontanaBlotter.
Converts textual location descriptions to lat/lng and populates incident_geocodes.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Optional

import random
import requests

DB_PATH = os.getenv("MB_DB_PATH", "/root/montanablotter/blotter.db").strip() or "/root/montanablotter/blotter.db"
GEOCODE_SLEEP = float(os.getenv("MB_GEOCODE_SLEEP", "0.25"))

# Primary city for each Montana county — used as geocoding context when
# the raw location string lacks an explicit city name.
_COUNTY_PRIMARY_CITY: dict[str, str] = {
    "Beaverhead": "Dillon", "Big Horn": "Hardin", "Blaine": "Chinook",
    "Broadwater": "Townsend", "Carbon": "Red Lodge", "Carter": "Ekalaka",
    "Cascade": "Great Falls", "Chouteau": "Fort Benton", "Custer": "Miles City",
    "Daniels": "Scobey", "Dawson": "Glendive", "Deer Lodge": "Anaconda",
    "Fallon": "Baker", "Fergus": "Lewistown", "Flathead": "Kalispell",
    "Gallatin": "Bozeman", "Garfield": "Jordan", "Glacier": "Cut Bank",
    "Golden Valley": "Ryegate", "Granite": "Philipsburg", "Hill": "Havre",
    "Jefferson": "Boulder", "Judith Basin": "Stanford", "Lake": "Polson",
    "Lewis and Clark": "Helena", "Liberty": "Chester", "Lincoln": "Libby",
    "Madison": "Virginia City", "McCone": "Circle",
    "Meagher": "White Sulphur Springs", "Mineral": "Superior",
    "Missoula": "Missoula", "Musselshell": "Roundup", "Park": "Livingston",
    "Petroleum": "Winnett", "Phillips": "Malta", "Pondera": "Conrad",
    "Powder River": "Broadus", "Powell": "Deer Lodge", "Prairie": "Terry",
    "Ravalli": "Hamilton", "Richland": "Sidney", "Roosevelt": "Wolf Point",
    "Rosebud": "Forsyth", "Sanders": "Thompson Falls",
    "Sheridan": "Plentywood", "Silver Bow": "Butte", "Stillwater": "Columbus",
    "Sweet Grass": "Big Timber", "Teton": "Choteau", "Toole": "Shelby",
    "Treasure": "Hysham", "Valley": "Glasgow", "Wheatland": "Harlowton",
    "Wibaux": "Wibaux", "Yellowstone": "Billings",
}

# Obfuscated block numbers: "1Xx", "22XX", "3x" at the start of a string.
_OBFUSCATED_NUM_RE = re.compile(r'^\d+[Xx]+\s+', re.IGNORECASE)
# Block notation: "2300 Blk Avenue C" or "900 Block Of 18th St"
_BLK_NOTATION_RE = re.compile(r'^(\d+)\s+Bl(?:k|ock\s+Of?)\s+', re.IGNORECASE)
# Mile-marker prefix common in MT highway calls: "1Xx Mm I90"
_MM_RE = re.compile(r'\bMm\s+', re.IGNORECASE)


def _normalize_location(raw: str) -> str:
    """Strip police blotter obfuscation to produce a geocodable street string.

    Handles two common Montana blotter patterns:
    - Block notation: "2300 Blk Avenue C" → "2300 Avenue C"
    - Obfuscated numbers: "1Xx W Broadway" → "W Broadway"
    - Mile markers: "1Xx Mm I90" → "I90"
    """
    s = raw.strip()
    s = _BLK_NOTATION_RE.sub(r'\1 ', s)   # keep block number, drop "Blk"
    s = _OBFUSCATED_NUM_RE.sub('', s)      # drop obfuscated house numbers
    s = _MM_RE.sub('', s)                  # drop "Mm" mile-marker prefix
    return s.strip()


# Spread (degrees) applied to place-name-only geocodes (no street number) so the
# map does not collapse thousands of city-centroid points onto one coordinate.
# Deterministic per record_id so re-geocoding never moves a point.
_CITY_SCATTER_DEG = 0.03


def _is_street_level(raw_location: Optional[str]) -> bool:
    """True when the location carries enough precision to keep exact coords.

    A street/building number or mile marker means the geocoder resolved a real
    point; a bare city name ("Bozeman, MT") does not and should be scattered.
    """
    if not raw_location:
        return False
    return any(ch.isdigit() for ch in _normalize_location(raw_location))


def finalize_geocode_coords(record_id: int, raw_location: Optional[str], lat: float, lng: float):
    """Return (lat, lng) to persist for a geocoded record.

    Street-level locations keep their exact coordinates. Place-name-only
    locations are scattered deterministically around the geocoded centroid so
    the Crime Map / heatmap render a city-wide spread instead of one blob.
    """
    if _is_street_level(raw_location):
        return lat, lng
    rng = random.Random(record_id)
    return (
        lat + rng.uniform(-_CITY_SCATTER_DEG, _CITY_SCATTER_DEG),
        lng + rng.uniform(-_CITY_SCATTER_DEG, _CITY_SCATTER_DEG),
    )


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


# ---------------------------------------------------------------------------
# Geocoding backends
# ---------------------------------------------------------------------------

def _nominatim_geocode(street: str, city: str = "", state: str = "Montana") -> Optional[dict]:
    """Free Nominatim (OpenStreetMap) geocoding with polite throttling.

    Uses structured params (street/city/state) rather than free-text concatenation
    so Nominatim's indexed fields produce better match rates for short street strings.
    """
    url = "https://nominatim.openstreetmap.org/search"
    params: dict = {"format": "json", "limit": 1, "countrycodes": "us"}
    headers = {"User-Agent": "MontanaBlotter/1.0 (support@montanablotter.com)"}

    if city:
        params.update({"street": street, "city": city, "state": state})
    else:
        params["q"] = f"{street}, {state}, USA"

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code == 429:
            time.sleep(2)
            resp = requests.get(url, params=params, headers=headers, timeout=15)
        data = resp.json()
        if data:
            first = data[0]
            return {
                "lat": float(first["lat"]),
                "lng": float(first["lon"]),
                "confidence": "medium" if first.get("importance", 0) > 0.5 else "low",
                "display_name": first.get("display_name", ""),
            }
    except Exception:
        pass
    return None


def _mapbox_geocode(query: str, token: str) -> Optional[dict]:
    """Mapbox geocoding (premium accuracy)."""
    url = "https://api.mapbox.com/geocoding/v5/mapbox.places/" + requests.utils.quote(query + ", Montana")
    params = {"access_token": token, "limit": 1, "country": "us"}
    try:
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()
        features = data.get("features", [])
        if features:
            f = features[0]
            center = f.get("center", [None, None])
            return {
                "lat": center[1],
                "lng": center[0],
                "confidence": "high",
                "display_name": f.get("place_name", ""),
            }
    except Exception:
        pass
    return None


def _google_geocode(query: str, key: str) -> Optional[dict]:
    """Google Maps geocoding (premium accuracy)."""
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": query + ", Montana, USA", "key": key}
    try:
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()
        if data.get("status") == "OK" and data.get("results"):
            r = data["results"][0]
            loc = r["geometry"]["location"]
            return {
                "lat": loc["lat"],
                "lng": loc["lng"],
                "confidence": "high" if r.get("partial_match") is False else "medium",
                "display_name": r.get("formatted_address", ""),
            }
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Public geocode helper
# ---------------------------------------------------------------------------

def geocode_location(raw_location: str, county: Optional[str] = None, city: Optional[str] = None) -> Optional[dict]:
    """Geocode a blotter location string, normalizing police obfuscation first.

    Resolution order: Mapbox → Google → Nominatim (structured) → Nominatim (free-text).
    """
    street = _normalize_location(raw_location or "")
    if not street:
        return None

    # Resolve city: explicit override → county lookup → empty
    resolved_city = city or _COUNTY_PRIMARY_CITY.get((county or "").strip().title(), "")

    # Build the full query string for premium backends
    query_parts = [street]
    if resolved_city:
        query_parts.append(resolved_city)
    query_parts.append("Montana")
    full_query = ", ".join(query_parts)

    mapbox_token = os.getenv("MAPBOX_ACCESS_TOKEN", "").strip()
    google_key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()

    if mapbox_token:
        result = _mapbox_geocode(full_query, mapbox_token)
        if result:
            return result
    if google_key:
        result = _google_geocode(full_query, google_key)
        if result:
            return result

    # Structured Nominatim call (best for street-level with known city)
    result = _nominatim_geocode(street, city=resolved_city)
    if result:
        return result

    # Fallback: free-text with just state (intersections, landmarks, highways)
    if resolved_city:
        return _nominatim_geocode(full_query)
    return None


# ---------------------------------------------------------------------------
# Pipeline: backfill un-geocoded records
# ---------------------------------------------------------------------------

def _ensure_geocode_failures_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS geocode_failures (
            record_id INTEGER PRIMARY KEY,
            raw_location TEXT,
            county TEXT,
            failed_at TEXT DEFAULT (datetime('now')),
            attempt_count INTEGER DEFAULT 1
        )
    """)


def backfill_geocodes(batch_size: int = 200, county: Optional[str] = None) -> dict:
    """Geocode un-geocoded records, skipping previously-failed attempts.

    Args:
        batch_size: Max records to process per run.
        county: If given, restrict backfill to this county.
    """
    conn = _connect()
    _ensure_geocode_failures_table(conn)

    where = [
        "g.record_id IS NULL",
        "f.record_id IS NULL",
        "r.location IS NOT NULL",
        "trim(r.location) != ''",
    ]
    params: list = []
    if county:
        where.append("r.county = ?")
        params.append(county)
    params.append(batch_size)

    rows = conn.execute(
        f"""
        SELECT r.id, r.location, r.county, r.incident_type
        FROM records r
        LEFT JOIN incident_geocodes g ON r.id = g.record_id
        LEFT JOIN geocode_failures f ON r.id = f.record_id
        WHERE {' AND '.join(where)}
        ORDER BY r.id DESC
        LIMIT ?
        """,
        params,
    ).fetchall()

    processed = 0
    failed = 0
    for row in rows:
        result = geocode_location(row["location"], county=row["county"])
        if result:
            flat, flng = finalize_geocode_coords(row["id"], row["location"], result["lat"], result["lng"])
            conn.execute(
                """
                INSERT OR IGNORE INTO incident_geocodes
                  (record_id, raw_location, lat, lng, geocode_confidence, county, city, geocoded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (row["id"], row["location"], flat, flng,
                 result["confidence"], row["county"], None),
            )
            processed += 1
        else:
            conn.execute(
                """
                INSERT INTO geocode_failures (record_id, raw_location, county)
                VALUES (?, ?, ?)
                ON CONFLICT(record_id) DO UPDATE SET
                  attempt_count = attempt_count + 1,
                  failed_at = datetime('now')
                """,
                (row["id"], row["location"], row["county"]),
            )
            failed += 1
        time.sleep(GEOCODE_SLEEP)

    conn.commit()
    conn.close()
    return {"processed": processed, "failed": failed, "batch_size": batch_size}


# ---------------------------------------------------------------------------
# Scorecard computation
# ---------------------------------------------------------------------------

def compute_safety_scorecards(period_days: int = 30) -> dict:
    conn = _connect()
    period_end = datetime.utcnow().isoformat()[:10]
    period_start = (datetime.utcnow() - __import__("datetime").timedelta(days=period_days)).isoformat()[:10]

    # County-level scorecards
    rows = conn.execute(
        """
        SELECT county,
               COUNT(*) as total_incidents,
               COUNT(DISTINCT incident_type) as diversity,
               AVG(CASE WHEN incident_type IN ('Assault','Burglary','Robbery','Homicide','Sexual Assault') THEN 5
                        WHEN incident_type IN ('DUI','Drug','Theft','Fraud') THEN 3
                        ELSE 1 END) as avg_severity
        FROM records
        WHERE date >= ? AND date <= ? AND county IS NOT NULL
        GROUP BY county
        """,
        (period_start, period_end),
    ).fetchall()

    created = 0
    for row in rows:
        total = row["total_incidents"]
        diversity = row["diversity"]
        avg_severity = row["avg_severity"] or 1.0
        # Simple composite: lower is better; invert so 100 = safest
        raw_score = max(0, 100 - (total * 0.5) - (diversity * 2) - (avg_severity * 10))
        score = round(min(100, max(0, raw_score)), 2)

        metrics = {
            "total_incidents": total,
            "incident_diversity": diversity,
            "avg_severity": round(avg_severity, 2),
        }

        # Trends: compare with previous period
        prev_start = (datetime.utcnow() - timedelta(days=period_days * 2)).isoformat()[:10]
        prev = conn.execute(
            "SELECT COUNT(*) as c FROM records WHERE county=? AND date >= ? AND date < ?",
            (row["county"], prev_start, period_start),
        ).fetchone()
        prev_total = prev["c"] if prev else 0
        trend_pct = round(((total - prev_total) / max(1, prev_total)) * 100, 1)
        trends = {"previous_period_total": prev_total, "change_percent": trend_pct}

        # Factors
        factors = {
            "contributing": ["High call volume" if total > 100 else "Moderate volume"],
            "improving": ["Declining trend"] if trend_pct < -5 else [],
            "deteriorating": ["Rising trend"] if trend_pct > 10 else [],
        }

        conn.execute(
            """
            INSERT INTO safety_scorecards (
                area_type, area_slug, area_name, county, population, score,
                percentile_state, percentile_national, methodology_version,
                metrics_json, trends_json, factors_json, period_start, period_end
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(area_type, area_slug, period_start, period_end, methodology_version)
            DO UPDATE SET score=excluded.score, metrics_json=excluded.metrics_json,
                          trends_json=excluded.trends_json, factors_json=excluded.factors_json,
                          computed_at=datetime('now')
            """,
            (
                "county",
                (row["county"] or "").lower().replace(" ", "-"),
                row["county"],
                row["county"],
                None,
                score,
                None,
                None,
                "v1",
                json.dumps(metrics),
                json.dumps(trends),
                json.dumps(factors),
                period_start,
                period_end,
            ),
        )
        created += 1

    conn.commit()
    conn.close()
    return {"created_or_updated": created, "period": f"{period_start} to {period_end}"}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="MontanaBlotter geocoding pipeline")
    sub = parser.add_subparsers(dest="cmd")

    bf = sub.add_parser("backfill", help="Geocode un-geocoded records")
    bf.add_argument("--batch", type=int, default=200, help="Records per run (default 200)")
    bf.add_argument("--county", type=str, default=None, help="Restrict to one county")

    sub.add_parser("scorecards", help="Recompute safety scorecards")

    args = parser.parse_args()
    if args.cmd == "backfill":
        print(backfill_geocodes(batch_size=args.batch, county=args.county))
    elif args.cmd == "scorecards":
        print(compute_safety_scorecards())
    else:
        parser.print_help()
