#!/usr/bin/env python3
"""
County Inventory Builder for Montana Blotter.

Builds a comprehensive inventory of all 56 Montana counties with:
- Current coverage status from blotter.db (blotter PDFs, jail bookings, records)
- Fetcher module availability
- Source registry entries and per-source health (last success, latest error)
- Stale/failing alerts
- Population rank (for prioritization)

Run: python3 county_inventory.py [--output csv|json|db]

Paths come from the environment (MB_DB_PATH, MB_FETCHERS_DIR, MB_DATA_DIR)
with repo-relative defaults, so the script works on any checkout.
"""

import sqlite3
import csv
import json
import os
import re
import sys
from pathlib import Path

# This file lives at the app root; derive every path from __file__ so the
# script keeps working after the /root/montanablotter -> /opt/montanablotter move.
REPO_ROOT = Path(__file__).resolve().parent


def _default_db_path() -> str:
    env = os.environ.get("MB_DB_PATH")
    if env:
        return env
    repo_db = REPO_ROOT / "blotter.db"
    if repo_db.exists():
        return str(repo_db)
    try:
        import config
        return str(config.DB_PATH)
    except Exception:
        return str(repo_db)


DB_PATH = _default_db_path()
FETCHERS_DIR = os.environ.get("MB_FETCHERS_DIR", str(REPO_ROOT / "services" / "ingestion" / "fetchers"))
OUTPUT_DIR = os.environ.get("MB_DATA_DIR", str(REPO_ROOT / "data"))

# All 56 Montana counties (alphabetical). Membership was cross-checked against
# the 2020 census county list — "Brown" and "Pendroy" were incorrectly present
# and Granite and Wibaux were missing.
ALL_COUNTIES = [
    "Beaverhead", "Big Horn", "Blaine", "Broadwater", "Carbon",
    "Carter", "Cascade", "Chouteau", "Custer", "Daniels", "Dawson", "Deer Lodge",
    "Fallon", "Fergus", "Flathead", "Gallatin", "Garfield", "Glacier", "Golden Valley",
    "Granite", "Hill", "Jefferson", "Judith Basin", "Lake", "Lewis and Clark",
    "Liberty", "Lincoln", "Madison", "McCone", "Meagher", "Mineral", "Missoula",
    "Musselshell", "Park", "Petroleum", "Phillips", "Pondera",
    "Powder River", "Powell", "Prairie", "Ravalli", "Richland", "Roosevelt",
    "Rosebud", "Sanders", "Sheridan", "Silver Bow", "Stillwater", "Sweet Grass",
    "Teton", "Toole", "Treasure", "Valley", "Wheatland", "Wibaux", "Yellowstone",
]

# Population rank (2020 census, approximate) — top counties first
POPULATION_RANK = [
    "Yellowstone", "Cascade", "Missoula", "Gallatin", "Flathead", "Richland",
    "Jefferson", "Lake", "Silver Bow", "Lewis and Clark", "Fergus",
    "Beaverhead", "Big Horn", "Blaine", "Broadwater", "Carbon", "Carter",
    "Chouteau", "Custer", "Daniels", "Dawson", "Deer Lodge", "Fallon",
    "Glacier", "Golden Valley", "Hill", "Judith Basin", "Liberty",
    "Lincoln", "Madison", "McCone", "Meagher", "Mineral", "Musselshell",
    "Park", "Phillips", "Pondera", "Powder River", "Powell",
    "Prairie", "Ravalli", "Roosevelt", "Rosebud", "Sanders", "Sheridan",
    "Stillwater", "Sweet Grass", "Teton", "Toole", "Valley",
    "Wheatland", "Granite", "Treasure", "Garfield", "Wibaux", "Petroleum",
]

# A jail feed counts as stale when it has not succeeded within this window.
SOURCE_STALE_DAYS = 7


def get_fetcher_for_county(county: str) -> str:
    """Find a fetcher module that explicitly serves the given county.

    Matching is token-based: the module stem is split on ``_``/``-`` and the
    county's tokens must all be present. Substring matching is deliberately
    avoided — ``"hill" in "phillips_inmate"`` would otherwise claim Phillips
    County's fetcher for Hill County. County-seat aliases (Hill -> havre,
    Silver Bow -> butte, Deer Lodge -> anaconda) match fetchers named after
    the city instead of the county.
    """
    county_tokens = set(re.split(r"[\s\-_]+", county.lower()))
    # Stopwords never appear in module names ("Lewis and Clark" -> lewis_clark_inmate.py).
    county_tokens -= {"and", "of", "the", "county"}
    county_tokens.discard("")
    if not county_tokens:
        return ""
    # County-seat aliases: some fetchers are named after the city, not the county.
    SEAT_ALIASES = {
        "hill": ["havre"],
        "silver bow": ["butte"],
        "deer lodge": ["anaconda"],
    }
    token_variants = [county_tokens]
    token_variants.extend({alias} for alias in SEAT_ALIASES.get(county.lower(), []))
    try:
        filenames = sorted(os.listdir(FETCHERS_DIR))
    except FileNotFoundError:
        return ""
    # Direct county-name matches win over seat aliases.
    for variant in token_variants:
        for fname in filenames:
            if not fname.endswith(".py") or fname.startswith("__"):
                continue
            stem_tokens = set(re.split(r"[\s\-_]+", fname[:-3].lower()))
            if variant <= stem_tokens:
                return fname
    return ""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_source_registry(conn: sqlite3.Connection, county: str) -> list:
    """Get source registry entries for a county.

    Checks both ``sources.source_registry`` (if it exists) and
    ``jail_booking_sources`` so that counties with only a jail bookings
    source still show as "configured."
    """
    rows: list[dict] = []
    # Try source_registry first
    try:
        rows = conn.execute(
            "SELECT * FROM sources.source_registry WHERE display_name LIKE ? OR base_url LIKE ?",
            (f"%{county}%", f"%{county}%"),
        ).fetchall()
        rows = [dict(r) for r in rows]
    except sqlite3.OperationalError:
        rows = []

    # Also include jail_booking_sources entries
    if not rows:
        jbs_rows = conn.execute(
            "SELECT county_name as display_name, roster_url as base_url, 'jail_roster' as source_type "
            "FROM jail_booking_sources WHERE county_name LIKE ?",
            (f"%{county}%",),
        ).fetchall()
        rows = [dict(r) for r in jbs_rows]
    return rows


def get_blotter_count(conn: sqlite3.Connection, county: str, days: int = 30) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM blotters WHERE county = ? AND upload_date >= date('now', ?)",
        (county, f"-{days} days")
    ).fetchone()
    return row[0]


def get_record_count(conn: sqlite3.Connection, county: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM records WHERE county = ?",
        (county,),
    ).fetchone()
    return row[0]


def get_jail_stats(conn: sqlite3.Connection, county: str) -> dict:
    """Aggregate jail booking volume and per-source health for a county."""
    stats = {
        "jail_bookings_total": 0,
        "jail_bookings_30d": 0,
        "last_success_at": None,
        "latest_error": "",
    }
    row = conn.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN first_seen_at >= datetime('now', '-30 days') THEN 1 ELSE 0 END) AS recent
        FROM jail_bookings
        WHERE county_name = ?
        """,
        (county,),
    ).fetchone()
    if row:
        stats["jail_bookings_total"] = int(row["total"] or 0)
        stats["jail_bookings_30d"] = int(row["recent"] or 0)

    src = conn.execute(
        """
        SELECT last_success_at, latest_error
        FROM jail_booking_sources
        WHERE county_name = ?
        ORDER BY COALESCE(last_success_at, '') DESC
        LIMIT 1
        """,
        (county,),
    ).fetchone()
    if src:
        stats["last_success_at"] = src["last_success_at"]
        stats["latest_error"] = (src["latest_error"] or "").strip()
    return stats


def get_stale_alerts(conn: sqlite3.Connection, county: str) -> list:
    # Map county to source_type patterns
    county_l = county.lower().replace(" ", "_")
    try:
        rows = conn.execute(
            "SELECT * FROM ingestion_source_alerts WHERE state = 'open' AND (source_type LIKE ? OR summary LIKE ?)",
            (f"%{county_l}%", f"%{county}%")
        ).fetchall()
    except sqlite3.OperationalError:
        # Table may not exist on older databases; treat as no alerts.
        return []
    return [dict(r) for r in rows]


def _source_is_stale(last_success_at, latest_error: str) -> bool:
    """A source is stale when it has an error on record and no recent success."""
    if not latest_error:
        return False
    if not last_success_at:
        return True
    try:
        from datetime import datetime, timedelta
        parsed = datetime.strptime(str(last_success_at)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return True
    return parsed < datetime.now() - timedelta(days=SOURCE_STALE_DAYS)


def build_inventory():
    conn = _connect()
    inventory = []
    try:
        for county in ALL_COUNTIES:
            fetcher = get_fetcher_for_county(county)
            sources = get_source_registry(conn, county)
            blotter_count = get_blotter_count(conn, county)
            record_count = get_record_count(conn, county)
            jail = get_jail_stats(conn, county)
            stale_alerts = get_stale_alerts(conn, county)
            source_types = []
            for s in sources:
                stype = s.get('source_type')
                if stype and stype not in source_types:
                    source_types.append(stype)
            source_stale = _source_is_stale(jail["last_success_at"], jail["latest_error"])

            # Determine status
            if blotter_count >= 10 or jail["jail_bookings_30d"] >= 10:
                status = "active"
            elif blotter_count >= 1 or jail["jail_bookings_total"] >= 1 or record_count >= 1:
                status = "partial"
            elif source_stale or stale_alerts:
                status = "failing"
            elif fetcher or sources:
                status = "configured"
            else:
                status = "not_covered"

            inventory.append({
                "county": county,
                "status": status,
                "blotters_30d": blotter_count,
                "records_total": record_count,
                "jail_bookings_total": jail["jail_bookings_total"],
                "jail_bookings_30d": jail["jail_bookings_30d"],
                "last_success_at": jail["last_success_at"],
                "latest_error": jail["latest_error"],
                "fetcher_module": fetcher,
                "source_types": ", ".join(source_types) if source_types else "",
                "stale_alerts": len(stale_alerts),
                "population_rank": POPULATION_RANK.index(county) + 1 if county in POPULATION_RANK else 999,
            })
    finally:
        conn.close()

    return inventory


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    inventory = build_inventory()

    # Summary
    print("=== MONTANA BLOTTER COUNTY INVENTORY ===")
    print(f"Total counties: {len(ALL_COUNTIES)}")
    print()
    for status in ["active", "partial", "configured", "failing", "not_covered"]:
        counties = [i['county'] for i in inventory if i['status'] == status]
        print(f"  {status}: {len(counties)} — {', '.join(counties)}")
    print()

    # Per-county detail
    print("=== DETAIL ===")
    for item in sorted(inventory, key=lambda x: x['population_rank']):
        print(f"  [{item['status']:>12}] {item['county']:<20} blotters_30d={item['blotters_30d']:>3}  "
              f"jail={item['jail_bookings_total']:>5} (30d={item['jail_bookings_30d']:>4})  "
              f"fetcher={item['fetcher_module'] or '(none)':<25} "
              f"sources={item['source_types'] or '(none)':<30} "
              f"stale_alerts={item['stale_alerts']}")

    # Write CSV
    csv_path = os.path.join(OUTPUT_DIR, "county_inventory.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "county", "status", "blotters_30d", "records_total",
            "jail_bookings_total", "jail_bookings_30d", "last_success_at",
            "latest_error", "fetcher_module", "source_types", "stale_alerts",
            "population_rank",
        ])
        writer.writeheader()
        writer.writerows(inventory)
    print(f"\nCSV written: {csv_path}")

    # Write JSON
    json_path = os.path.join(OUTPUT_DIR, "county_inventory.json")
    with open(json_path, "w") as f:
        json.dump(inventory, f, indent=2)
    print(f"JSON written: {json_path}")

    # Brief for prioritization: top 10 uncovered by population
    print("\n=== TOP 10 PRIORITY COUNTIES (uncovered/failing, by population) ===")
    uncovered = [i for i in inventory if i['status'] in ('not_covered', 'failing')]
    uncovered.sort(key=lambda x: x['population_rank'])
    for item in uncovered[:10]:
        reason = item['latest_error'][:70] if item['latest_error'] else 'no source'
        print(f"  {item['county']} (rank #{item['population_rank']}): {reason}")


if __name__ == "__main__":
    main()
