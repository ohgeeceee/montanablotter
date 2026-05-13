#!/usr/bin/env python3
from __future__ import annotations

import csv
from pathlib import Path


COUNTIES = [
    "Beaverhead", "Big Horn", "Blaine", "Broadwater", "Carbon", "Carter", "Cascade",
    "Chouteau", "Custer", "Daniels", "Dawson", "Deer Lodge", "Fallon", "Fergus",
    "Flathead", "Gallatin", "Garfield", "Glacier", "Golden Valley", "Granite", "Hill",
    "Jefferson", "Judith Basin", "Lake", "Lewis and Clark", "Liberty", "Lincoln",
    "Madison", "McCone", "Meagher", "Mineral", "Missoula", "Musselshell", "Park",
    "Petroleum", "Phillips", "Pondera", "Powder River", "Powell", "Prairie", "Ravalli",
    "Richland", "Roosevelt", "Rosebud", "Sanders", "Sheridan", "Silver Bow",
    "Stillwater", "Sweet Grass", "Teton", "Toole", "Treasure", "Valley", "Wheatland",
    "Wibaux", "Yellowstone",
]

FIELDS = [
    "county",
    "city",
    "case_number",
    "case_type_code",
    "case_type_label",
    "caption",
    "plaintiff_name",
    "defendant_name",
    "address",
    "filing_date",
    "case_status",
    "source_url",
    "source_record_id",
]


def county_key(county: str) -> str:
    return county.lower().replace(" ", "_").replace("-", "_")


def main() -> None:
    out_dir = Path("data/civil_filings/import_templates")
    out_dir.mkdir(parents=True, exist_ok=True)

    for county in COUNTIES:
        target = out_dir / f"{county_key(county)}.csv"
        with target.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerow(
                {
                    "county": county,
                    "city": "",
                    "case_number": "",
                    "case_type_code": "",
                    "case_type_label": "",
                    "caption": "",
                    "plaintiff_name": "",
                    "defendant_name": "",
                    "address": "",
                    "filing_date": "",
                    "case_status": "",
                    "source_url": "https://icourtcase.mt.gov/",
                    "source_record_id": "",
                }
            )

    print(f"Wrote {len(COUNTIES)} template files to {out_dir}")


if __name__ == "__main__":
    main()
