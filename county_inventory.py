#!/usr/bin/env python3
"""
County Inventory Builder for Montana Blotter.

Builds a comprehensive inventory of all 56 Montana counties with:
- Current coverage status from blotter.db
- Fetcher module availability
- Source registry entries
- Stale/failing alerts
- Population rank (for prioritization)

Run: python3 county_inventory.py [--output csv|json|db]
"""

import sqlite3
import csv
import json
import os
import sys
from pathlib import Path

DB_PATH = "/root/montanablotter/blotter.db"
FETCHERS_DIR = "/root/montanablotter/services/ingestion/fetchers"
OUTPUT_DIR = "/root/montanablotter/data"

# All 56 Montana counties (alphabetical)
ALL_COUNTIES = [
    "Beaverhead", "Big Horn", "Blaine", "Broadwater", "Brown", "Carbon",
    "Carter", "Cascade", "Chouteau", "Custer", "Daniels", "Dawson", "Deer Lodge",
    "Fallon", "Fergus", "Flathead", "Gallatin", "Garfield", "Glacier", "Golden Valley",
    "Hill", "Jefferson", "Judith Basin", "Lake", "Lewis and Clark",
    "Liberty", "Lincoln", "Madison", "McCone", "Meagher", "Mineral", "Missoula",
    "Musselshell", "Park", "Pendroy", "Petroleum", "Phillips", "Pondera",
    "Powder River", "Powell", "Prairie", "Ravalli", "Richland", "Roosevelt",
    "Rosebud", "Sanders", "Sheridan", "Silver Bow", "Stillwater", "Sweet Grass",
    "Teton", "Toole", "Treasure", "Valley", "Wheatland", "Yellowstone",
]

# Population rank (2020 census, approximate) — top counties first
POPULATION_RANK = [
    "Yellowstone", "Cascade", "Missoula", "Gallatin", "Flathead", "Richland",
    "Jefferson", "Lake", "Silver Bow", "Lewis and Clark", "Fergus",
    "Beaverhead", "Big Horn", "Blaine", "Broadwater", "Carbon", "Carter",
    "Chouteau", "Custer", "Daniels", "Dawson", "Deer Lodge", "Fallon",
    "Glacier", "Golden Valley", "Hill", "Judith Basin", "Liberty",
    "Lincoln", "Madison", "McCone", "Meagher", "Mineral", "Musselshell",
    "Park", "Petroleum", "Phillips", "Pondera", "Powder River", "Powell",
    "Prairie", "Ravalli", "Roosevelt", "Rosebud", "Sanders", "Sheridan",
    "Stillwater", "Sweet Grass", "Teton", "Toole", "Treasure", "Valley",
    "Wheatland", "Brown", "Garfield", "Pendroy",
]

def get_fetcher_for_county(county: str) -> str:
    """Find a fetcher module that explicitly serves the given county.

    The module filename must contain the lowercased county name (or a known
    hyphenated variant) — loose prefix matching like ``hill in phillips`` is
    rejected so that e.g. ``phillips_inmate.py`` is not incorrectly reported
    as serving Hill County.
    """
    county_l = county.lower()
    # Known hyphenated / underscorified variants so module discovery is robust.
    variants = {county_l}
    if " " in county_l:
        variants.add(county_l.replace(" ", "-"))
        variants.add(county_l.replace(" ", "_"))
    for fname in os.listdir(FETCHERS_DIR):
        if fname.endswith(".py") and not fname.startswith("__"):
            fname_base = fname[:-3].lower()
            for v in variants:
                if v in fname_base:
                    return fname
    return ""

def get_source_registry(county: str) -> list:
    """Get source registry entries for a county.

    Checks both ``sources.source_registry`` (if it exists) and
    ``jail_booking_sources`` so that counties with only a jail bookings
    source still show as "configured."
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
    conn.close()
    return rows

def get_blotter_count(county: str, days: int = 30) -> int:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT COUNT(*) FROM blotters WHERE county = ? AND upload_date >= date('now', ?)",
        (county, f"-{days} days")
    ).fetchone()
    conn.close()
    return row[0]

def get_stale_alerts(county: str) -> list:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Map county to source_type patterns
    county_l = county.lower().replace(" ", "_")
    rows = conn.execute(
        "SELECT * FROM ingestion_source_alerts WHERE state = 'open' AND (source_type LIKE ? OR summary LIKE ?)",
        (f"%{county_l}%", f"%{county}%")
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def build_inventory():
    inventory = []
    for county in ALL_COUNTIES:
        fetcher = get_fetcher_for_county(county)
        sources = get_source_registry(county)
        blotter_count = get_blotter_count(county)
        stale_alerts = get_stale_alerts(county)
        source_types = [s['source_type'] for s in sources] if sources else []

        # Determine status
        if blotter_count >= 10:
            status = "active"
        elif blotter_count >= 1:
            status = "partial"
        elif fetcher or sources:
            status = "configured"
        elif stale_alerts:
            status = "failing"
        else:
            status = "not_covered"

        inventory.append({
            "county": county,
            "status": status,
            "blotters_30d": blotter_count,
            "fetcher_module": fetcher,
            "source_types": ", ".join(source_types) if source_types else "",
            "stale_alerts": len(stale_alerts),
            "population_rank": POPULATION_RANK.index(county) + 1 if county in POPULATION_RANK else 999,
        })

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
              f"fetcher={item['fetcher_module'] or '(none)':<25} "
              f"sources={item['source_types'] or '(none)':<30} "
              f"stale_alerts={item['stale_alerts']}")

    # Write CSV
    csv_path = os.path.join(OUTPUT_DIR, "county_inventory.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "county", "status", "blotters_30d", "fetcher_module",
            "source_types", "stale_alerts", "population_rank"
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
    print("\n=== TOP 10 PRIORITY COUNTIES (uncovered, by population) ===")
    uncovered = [i for i in inventory if i['status'] == 'not_covered']
    uncovered.sort(key=lambda x: x['population_rank'])
    for item in uncovered[:10]:
        print(f"  {item['county']} (rank #{item['population_rank']})")

if __name__ == "__main__":
    main()
