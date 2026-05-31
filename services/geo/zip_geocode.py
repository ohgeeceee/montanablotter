"""
Zip code to lat/lon lookup via Nominatim (OpenStreetMap).

Results are cached in the zip_geocode_cache table to avoid repeat API calls
and to respect Nominatim's rate limits.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Optional

import requests

import config

DB_PATH = config.DB_PATH
_NOMINATIM_URL = 'https://nominatim.openstreetmap.org/search'
_USER_AGENT = 'MontanaBlotter/1.0'

# Montana bounding box (generous)
_MT_LAT_MIN, _MT_LAT_MAX = 44.0, 49.1
_MT_LON_MIN, _MT_LON_MAX = -116.2, -103.9


def _ensure_cache_table(conn: sqlite3.Connection) -> None:
    conn.execute('''
        CREATE TABLE IF NOT EXISTS zip_geocode_cache (
            zip_code TEXT PRIMARY KEY,
            lat      REAL,
            lon      REAL,
            cached_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    conn.commit()


def _is_montana(lat: float, lon: float) -> bool:
    return (_MT_LAT_MIN <= lat <= _MT_LAT_MAX) and (_MT_LON_MIN <= lon <= _MT_LON_MAX)


def zip_to_latlon(zip_code: str) -> Optional[tuple[float, float]]:
    """
    Return (lat, lon) for a Montana zip code, or None if unknown / outside MT.
    Results are cached in zip_geocode_cache to avoid repeat Nominatim calls.
    """
    zip_code = zip_code.strip()
    if len(zip_code) != 5 or not zip_code.isdigit():
        return None

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_cache_table(conn)
        row = conn.execute(
            'SELECT lat, lon FROM zip_geocode_cache WHERE zip_code = ?', (zip_code,)
        ).fetchone()
        if row:
            if row['lat'] is None:
                return None
            return float(row['lat']), float(row['lon'])

        # Not cached — call Nominatim
        time.sleep(1)  # Nominatim rate-limit courtesy
        try:
            resp = requests.get(
                _NOMINATIM_URL,
                params={'postalcode': zip_code, 'countrycodes': 'US', 'format': 'json', 'limit': 1},
                headers={'User-Agent': _USER_AGENT},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return None

        if not data:
            conn.execute(
                'INSERT OR REPLACE INTO zip_geocode_cache (zip_code, lat, lon) VALUES (?, NULL, NULL)',
                (zip_code,),
            )
            conn.commit()
            return None

        lat, lon = float(data[0]['lat']), float(data[0]['lon'])
        if not _is_montana(lat, lon):
            conn.execute(
                'INSERT OR REPLACE INTO zip_geocode_cache (zip_code, lat, lon) VALUES (?, NULL, NULL)',
                (zip_code,),
            )
            conn.commit()
            return None

        conn.execute(
            'INSERT OR REPLACE INTO zip_geocode_cache (zip_code, lat, lon) VALUES (?, ?, ?)',
            (zip_code, lat, lon),
        )
        conn.commit()
        return lat, lon
    finally:
        conn.close()
