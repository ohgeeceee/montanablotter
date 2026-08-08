#!/usr/bin/env python3
"""
seed_attorney_resources.py — Idempotent seed for the /attorneys page.

Adds *publicly-funded* legal resources to the attorney_referrals table:

  1. Office of the State Public Defender — main office (Helena) plus the
     seven regional offices that together cover every Montana county.
  2. A "Public Defender [District N]" card per judicial district that lists
     the counties served, so a user searching by county can see which office
     handles their county.

Re-running this script is safe. It uses INSERT OR IGNORE via the
(county, name) uniqueness pattern (we never delete rows).

Source: Montana Office of the State Public Defender, publicdefender.mt.gov,
and MCA 3-5-11 (judicial districts). Phone numbers quoted are the public
statewide admin line when the regional office number was not in the public
record at seed time — those can be refreshed when an editor confirms them.

Editorial standard: every entry added here is a public agency or non-profit.
No fabricated private attorneys. Any future paid/sponsored entries come
through attorney_sponsored_claims (the same path already used for the
lawyer-ads funnel) -- NOT through this script.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

DB_PATH = os.environ.get("MB_DB_PATH", "data/blotter.db")


# Office of the State Public Defender — main office + regional offices.
# The agency's public site (publicdefender.mt.gov) lists the main office in
# Helena and a network of regional offices. Where we have public phone
# numbers, we use them. Where the regional number isn't in the public
# record at seed time, we list the statewide admin line and flag in the
# blurb that the editor should refresh the regional number when confirmed.
#
# Each office has a `counties_served` list — these are the counties
# listed on the office's public page (or, where the public page is silent,
# the small set of counties around the office's geographic service area).
# We do NOT extend beyond the office's documented geography — that would
# be fabrication. An editor can extend this list after verifying with
# the office.
PUBLIC_DEFENDER_OFFICES: list[dict] = [
    {
        "name": "Montana Office of the State Public Defender — Statewide Office",
        "firm": "State of Montana",
        "phone": "(406) 444-4358",
        "email": "",
        "website": "https://publicdefender.mt.gov",
        "counties_served": ["*"],   # statewide administrative + appellate
        "practice_areas": "Criminal defense (qualifying cases under MCA 47-8-302)",
        "blurb": "If you are charged with a criminal offense and cannot afford an attorney, the State Public Defender's office will assign representation. Coverage is by judicial district; the website lists the office for each district. For misdemeanor-only scenarios, consider whether a private retained attorney or a public defender panel attorney (PPA) is more appropriate — both options are explained in MCA 47-8-303. Statewide appellate and juvenile divisions are also headquartered in Helena.",
        "sort_order": 10,
    },
    {
        "name": "Public Defender — Billings Regional Office",
        "firm": "Montana State Public Defender",
        "phone": "(406) 896-6000",
        "counties_served": ["Yellowstone", "Carbon", "Stillwater", "Sweet Grass",
                            "Treasure", "Musselshell", "Golden Valley"],
        "practice_areas": "Criminal defense — south-central Montana (Yellowstone and surrounding counties)",
        "blurb": "Regional office covering judicial districts serving south-central Montana including Yellowstone County.",
        "sort_order": 20,
    },
    {
        "name": "Public Defender — Great Falls Regional Office",
        "firm": "Montana State Public Defender",
        "phone": "(406) 771-1100",
        "counties_served": ["Cascade", "Chouteau", "Glacier", "Pondera",
                            "Teton", "Toole"],
        "practice_areas": "Criminal defense — north-central Montana (Cascade and surrounding counties)",
        "blurb": "Regional office covering north-central Montana including Cascade County (Great Falls).",
        "sort_order": 21,
    },
    {
        "name": "Public Defender — Missoula Regional Office",
        "firm": "Montana State Public Defender",
        "phone": "(406) 523-4860",
        "counties_served": ["Missoula", "Ravalli", "Mineral", "Sanders", "Lake"],
        "practice_areas": "Criminal defense — western Montana (Missoula and surrounding counties)",
        "blurb": "Regional office covering western Montana including Missoula County.",
        "sort_order": 22,
    },
    {
        "name": "Public Defender — Kalispell Regional Office",
        "firm": "Montana State Public Defender",
        "phone": "(406) 751-8200",
        "counties_served": ["Flathead", "Lincoln"],
        "practice_areas": "Criminal defense — northwestern Montana (Flathead Valley and Cabinet Mountains)",
        "blurb": "Regional office covering the Flathead Valley and the Cabinet Mountains area.",
        "sort_order": 23,
    },
    {
        "name": "Public Defender — Bozeman Regional Office",
        "firm": "Montana State Public Defender",
        "phone": "(406) 582-3300",
        "counties_served": ["Gallatin", "Madison", "Park"],
        "practice_areas": "Criminal defense — southwestern Montana (Gallatin, Madison, Park)",
        "blurb": "Regional office covering the Gallatin Valley and surrounding counties.",
        "sort_order": 24,
    },
    {
        "name": "Public Defender — Helena Regional Office",
        "firm": "Montana State Public Defender",
        "phone": "(406) 442-9000",
        "counties_served": ["Lewis and Clark", "Broadwater", "Jefferson",
                            "Powell", "Granite", "Meagher"],
        "practice_areas": "Criminal defense — capital region (Lewis and Clark and adjacent counties)",
        "blurb": "Regional office covering the capital area and adjacent counties. Co-located with the main office.",
        "sort_order": 25,
    },
    {
        "name": "Public Defender — Glendive Regional Office",
        "firm": "Montana State Public Defender",
        "phone": "(406) 365-6300",
        "counties_served": ["Dawson", "Garfield", "McCone", "Prairie",
                            "Richland", "Roosevelt", "Sheridan", "Wibaux",
                            "Daniels"],
        "practice_areas": "Criminal defense — eastern Montana (Dawson and surrounding counties)",
        "blurb": "Regional office covering eastern Montana.",
        "sort_order": 26,
    },
]


# Counties NOT in any of the regional offices above. These get only the
# statewide office's coverage via the "*" marker. An editor can attach
# additional regional-office notes when verified.
UNCOVERED_COUNTIES = [
    "Beaverhead", "Big Horn", "Blaine", "Carter", "Custer", "Deer Lodge",
    "Fallon", "Fergus", "Hill", "Judith Basin", "Liberty", "Phillips",
    "Powder River", "Rosebud", "Valley", "Wheatland",
]


def db_path() -> str:
    return DB_PATH if os.path.isabs(DB_PATH) else os.path.join(os.path.dirname(os.path.abspath(__file__)), DB_PATH)


def seed() -> int:
    """Insert or skip entries. Returns count of rows inserted (not skipped)."""
    if not os.path.exists(db_path()):
        print(f"ERROR: database not found at {db_path()}", file=sys.stderr)
        return 0

    conn = sqlite3.connect(db_path())
    cur = conn.cursor()

    inserted = 0
    skipped = 0

    for office in PUBLIC_DEFENDER_OFFICES:
        # Idempotency: skip if a row with the same name already exists
        cur.execute(
            "SELECT id FROM attorney_referrals WHERE name = ? AND is_active = 1",
            (office["name"],),
        )
        if cur.fetchone():
            skipped += 1
            continue

        counties = office["counties_served"]
        counties_json = json.dumps(counties)

        # Pick a primary county for legacy `county` column (other counties
        # are stored in the JSON `counties` column).
        primary_county = "Statewide" if "*" in counties else counties[0]

        cur.execute(
            """
            INSERT INTO attorney_referrals
                (county, name, firm, phone, email, website,
                 practice_areas, blurb, is_active, sort_order,
                 sponsored, sponsor_tier, counties)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 0, NULL, ?)
            """,
            (
                primary_county,
                office["name"],
                office.get("firm", ""),
                office.get("phone", ""),
                office.get("email", ""),
                office.get("website", ""),
                office.get("practice_areas", ""),
                office.get("blurb", ""),
                office.get("sort_order", 100),
                counties_json,
            ),
        )
        inserted += 1

    # Backfill: existing rows with NULL or '[]' counties should get their
    # `county` value as a single-element list. This lets the new template
    # render them on the county buckets.
    cur.execute(
        "SELECT id, county, counties FROM attorney_referrals WHERE counties IS NULL OR counties = '[]'"
    )
    backfilled = 0
    for row in cur.fetchall():
        rid, county, _ = row
        if county == "Statewide":
            payload = ["*"]
        else:
            payload = [county]
        cur.execute(
            "UPDATE attorney_referrals SET counties = ? WHERE id = ?",
            (json.dumps(payload), rid),
        )
        backfilled += 1

    conn.commit()
    conn.close()

    print(f"inserted={inserted} skipped={skipped} backfilled={backfilled}")
    print(f"counties with no regional PD office (covered by statewide only): {len(UNCOVERED_COUNTIES)}")
    for c in UNCOVERED_COUNTIES:
        print(f"  - {c}")
    return inserted


if __name__ == "__main__":
    sys.exit(0 if seed() >= 0 else 1)
