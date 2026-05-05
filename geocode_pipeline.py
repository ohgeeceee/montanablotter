"""
Geocoding pipeline and crime mapping utilities for MontanaBlotter.
Converts textual location descriptions to lat/lng and populates incident_geocodes.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Optional

import requests

DB_PATH = os.getenv("MB_DB_PATH", "/root/montanablotter/blotter.db").strip() or "/root/montanablotter/blotter.db"
GEOCODE_SLEEP = float(os.getenv("MB_GEOCODE_SLEEP", "0.25"))


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


# ---------------------------------------------------------------------------
# Geocoding backends
# ---------------------------------------------------------------------------

def _nominatim_geocode(query: str) -> Optional[dict]:
    """Free Nominatim (OpenStreetMap) geocoding with polite throttling."""
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": query + ", Montana, USA", "format": "json", "limit": 1}
    headers = {"User-Agent": "MontanaBlotter/1.0 (support@montanablotter.com)"}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code == 429:
            time.sleep(2)
            resp = requests.get(url, params=params, headers=headers, timeout=15)
        data = resp.json()
        if data and len(data) > 0:
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
    query = raw_location or ""
    if city:
        query = f"{query}, {city}"
    if county:
        query = f"{query}, {county} County"

    # Try premium backends first if keys are configured
    mapbox_token = os.getenv("MAPBOX_ACCESS_TOKEN", "").strip()
    google_key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()

    if mapbox_token:
        result = _mapbox_geocode(query, mapbox_token)
        if result:
            return result
    if google_key:
        result = _google_geocode(query, google_key)
        if result:
            return result

    return _nominatim_geocode(query)


# ---------------------------------------------------------------------------
# Pipeline: backfill un-geocoded records
# ---------------------------------------------------------------------------

def backfill_geocodes(batch_size: int = 200) -> dict:
    conn = _connect()
    rows = conn.execute(
        """
        SELECT r.id, r.location, r.county, r.incident_type
        FROM records r
        LEFT JOIN incident_geocodes g ON r.id=g.record_id
        WHERE g.record_id IS NULL AND r.location IS NOT NULL AND trim(r.location) != ''
        ORDER BY r.id DESC
        LIMIT ?
        """,
        (batch_size,),
    ).fetchall()

    processed = 0
    failed = 0
    for row in rows:
        result = geocode_location(row["location"], county=row["county"])
        if result:
            conn.execute(
                """
                INSERT INTO incident_geocodes (record_id, raw_location, lat, lng, geocode_confidence, county, city, geocoded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    row["id"],
                    row["location"],
                    result["lat"],
                    result["lng"],
                    result["confidence"],
                    row["county"],
                    None,
                ),
            )
            processed += 1
        else:
            failed += 1
        time.sleep(GEOCODE_SLEEP)

    conn.commit()
    conn.close()
    return {"processed": processed, "failed": failed}


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
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "backfill"
    if cmd == "backfill":
        print(backfill_geocodes())
    elif cmd == "scorecards":
        print(compute_safety_scorecards())
    else:
        print("Usage: python geocode_pipeline.py [backfill|scorecards]")
