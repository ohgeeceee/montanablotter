#!/usr/bin/env python3
"""Generate COUNTY_MAP / AGENCY_MAP covering every Montana county.

Source of truth is the verified sources manifest, whose section headings carry
all 56 counties with their seat city. county_inventory alone is NOT reliable:
it was missing Granite and carried a stray "Pendroy" row.

Ordering matters: a county's own name must win over a seat city that happens to
share a name with another county (the town of Deer Lodge sits in Powell County,
while Deer Lodge *County*'s seat is Anaconda).
"""
import json
import re
import sqlite3
import sys

sys.path.insert(0, "/opt/montanablotter")
import config

MANIFEST = "/opt/montanablotter/county_sources_manifest.md"
OUT_PATH = "/opt/montanablotter/data/county_detection_maps.json"

# Canonical Montana county list, used as a completeness check.
CANONICAL_56 = {
    "Beaverhead", "Big Horn", "Blaine", "Broadwater", "Carbon", "Carter",
    "Cascade", "Chouteau", "Custer", "Daniels", "Dawson", "Deer Lodge",
    "Fallon", "Fergus", "Flathead", "Gallatin", "Garfield", "Glacier",
    "Golden Valley", "Granite", "Hill", "Jefferson", "Judith Basin", "Lake",
    "Lewis and Clark", "Liberty", "Lincoln", "Madison", "McCone", "Meagher",
    "Mineral", "Missoula", "Musselshell", "Park", "Petroleum", "Phillips",
    "Pondera", "Powder River", "Powell", "Prairie", "Ravalli", "Richland",
    "Roosevelt", "Rosebud", "Sanders", "Sheridan", "Silver Bow", "Stillwater",
    "Sweet Grass", "Teton", "Toole", "Treasure", "Valley", "Wheatland",
    "Wibaux", "Yellowstone",
}

seats = {}
manifest_counties = []
try:
    text = open(MANIFEST, encoding="utf-8", errors="replace").read()
    for match in re.finditer(r"^##\s+(.+?)\s+\((.+?)\)\s*$", text, re.M):
        name = match.group(1).strip()
        manifest_counties.append(name)
        seats[name] = match.group(2).strip()
except Exception as exc:
    print("manifest read failed:", exc)

# Inventory is advisory only; report drift instead of trusting it as truth.
inventory = []
try:
    conn = sqlite3.connect(config.DB_PATH)
    inventory = [r[0] for r in conn.execute("SELECT DISTINCT county FROM county_inventory")]
    conn.close()
except Exception as exc:
    print("inventory read failed:", exc)

counties = [c for c in manifest_counties if c in CANONICAL_56]
missing_from_manifest = sorted(CANONICAL_56 - set(counties))
if missing_from_manifest:
    print("WARNING counties missing from manifest:", missing_from_manifest)

county_map = {}
agency_map = {}

# 1) Seat-city aliases first...
for county in counties:
    seat = seats.get(county)
    if seat:
        county_map.setdefault(seat.lower(), county)
        agency_map.setdefault(seat.lower(), f"{seat} Police Department")

# 2) ...then county names, so a shared place name never shadows the county.
for county in counties:
    key = county.lower()
    variants = {key}
    if county == "Lewis and Clark":
        variants |= {"lewis & clark", "l&c"}
    for variant in sorted(variants, key=len, reverse=True):
        county_map[variant] = county
    agency_map[key] = f"{county} County Sheriff's Office"

print(json.dumps({
    "manifest_sections": len(manifest_counties),
    "canonical_matched": len(counties),
    "inventory_rows": len(inventory),
    "inventory_missing_vs_canonical": sorted(CANONICAL_56 - set(inventory)),
    "inventory_not_in_canonical": sorted(set(inventory) - CANONICAL_56),
}, indent=1))

with open(OUT_PATH, "w") as fh:
    json.dump({"county_map": county_map, "agency_map": agency_map}, fh, indent=2, sort_keys=True)
    fh.write("\n")
print(f"\nwrote {OUT_PATH}: {len(county_map)} county keys, {len(agency_map)} agency keys")
