from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from db import connect_db
from services.ingestion.civil_filings import ingest_civil_filings
from services.ingestion.civil_filings_import import parse_import_file
from services.ingestion.code_violations import ingest_records
from services.ingestion.icourtcase_civil import run_daily_county_ingest

DEFAULT_CIVIL_COUNTIES = [
    "Beaverhead", "Big Horn", "Blaine", "Broadwater", "Carbon", "Carter", "Cascade",
    "Chouteau", "Custer", "Daniels", "Dawson", "Deer Lodge", "Fallon", "Fergus",
    "Flathead", "Gallatin", "Garfield", "Glacier", "Golden Valley", "Granite", "Hill",
    "Jefferson", "Judith Basin", "Lake", "Lewis and Clark", "Liberty", "Lincoln",
    "Madison", "McCone", "Meagher", "Mineral", "Missoula", "Musselshell", "Park",
    "Petroleum", "Phillips", "Pondera", "Powder River", "Powell", "Prairie",
    "Ravalli", "Richland", "Roosevelt", "Rosebud", "Sanders", "Sheridan", "Silver Bow",
    "Stillwater", "Sweet Grass", "Teton", "Toole", "Treasure", "Valley", "Wheatland",
    "Wibaux", "Yellowstone",
]

DEFAULT_CODE_SOURCES = [
    ("billings", "Billings Code Enforcement", "Billings"),
    ("missoula", "Missoula Code Enforcement", "Missoula"),
    ("great_falls", "Great Falls Code Enforcement", "Great Falls"),
    ("bozeman", "Bozeman Code Enforcement", "Bozeman"),
    ("helena", "Helena Code Enforcement", "Helena"),
]


def _county_key(county: str) -> str:
    return county.lower().replace(" ", "_").replace("-", "_")


def _load_local_file_records(base_dir: Path, stem: str) -> tuple[list[dict[str, Any]], str] | None:
    for ext in ("json", "csv"):
        path = base_dir / f"{stem}.{ext}"
        if path.exists():
            return parse_import_file(str(path), file_format=ext), str(path)
    return None


def _load_code_file_records(path: Path) -> list[dict[str, Any]]:
    ext = path.suffix.lower().lstrip(".")
    if ext == "json":
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, dict) and "records" in payload:
            return payload["records"]
        if isinstance(payload, list):
            return payload
        return [payload]
    if ext == "csv":
        import csv

        with path.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))
    raise ValueError(f"Unsupported code violation file format: {path}")


def _seed_demo_civil_rows() -> list[dict[str, Any]]:
    return [
        {
            "county": "Yellowstone",
            "city": "Billings",
            "case_number": "UD-26-1234",
            "case_type_code": "UD",
            "case_type_label": "Unlawful Detainer",
            "caption": "ABC Properties LLC v. Jane Doe",
            "plaintiff_name": "ABC Properties LLC",
            "defendant_name": "Jane Doe",
            "address": "123 Main St, Billings, MT 59101",
            "filing_date": "2026-05-11",
            "case_status": "Open",
            "source_url": "https://icourtcase.mt.gov/",
        },
        {
            "county": "Yellowstone",
            "city": "Billings",
            "case_number": "DV-26-4421",
            "case_type_code": "DV",
            "case_type_label": "Protection Order",
            "caption": "State of Montana v. John Roe",
            "plaintiff_name": "State of Montana",
            "defendant_name": "John Roe",
            "address": "500 Cedar Ave, Billings, MT 59102",
            "filing_date": "2026-05-09",
            "case_status": "Open",
            "source_url": "https://icourtcase.mt.gov/",
        },
        {
            "county": "Yellowstone",
            "city": "Billings",
            "case_number": "CC-26-7822",
            "case_type_code": "CC",
            "case_type_label": "Civil Claim",
            "caption": "First Federal Bank v. Jane Roe Lien Notice",
            "plaintiff_name": "First Federal Bank",
            "defendant_name": "Jane Roe",
            "address": "77 River Rd, Billings, MT 59103",
            "filing_date": "2026-05-08",
            "case_status": "Open",
            "source_url": "https://icourtcase.mt.gov/",
        },
    ]


def _seed_demo_code_rows() -> list[dict[str, Any]]:
    return [
        {
            "address": "123 Main St, Billings, MT 59101",
            "violation_type": "Abandoned Property",
            "status": "open",
            "date_issued": "2026-05-01",
            "owner_name": "ABC Properties LLC",
        },
        {
            "address": "500 Cedar Ave, Billings, MT 59102",
            "violation_type": "Permit Violation",
            "status": "resolved",
            "date_issued": "2026-04-15",
            "date_resolved": "2026-05-02",
            "owner_name": "Red River Rentals",
        },
    ]


def run_pipeline(
    *,
    civil_import_dir: str = "",
    code_import_dir: str = "",
    live: bool = True,
    seed_demo: bool = False,
    max_pages: int = 3,
    delay_seconds: float = 0.25,
) -> dict[str, Any]:
    conn = connect_db()
    try:
        out: dict[str, Any] = {"civil": [], "code": []}
        civil_dir = Path(civil_import_dir).expanduser() if civil_import_dir else None
        code_dir = Path(code_import_dir).expanduser() if code_import_dir else None

        for county in DEFAULT_CIVIL_COUNTIES:
            county_result: dict[str, Any] = {"county": county}
            live_ok = False
            if live:
                try:
                    result = run_daily_county_ingest(
                        conn,
                        county=county,
                        max_pages=max_pages,
                        delay_seconds=delay_seconds,
                    )
                    county_result.update({"mode": "live", **result})
                    live_ok = True
                except Exception as exc:
                    county_result["live_error"] = str(exc)
            if not live_ok and civil_dir and civil_dir.exists():
                payload = _load_local_file_records(civil_dir, _county_key(county))
                if payload:
                    records, source_file = payload
                    result = ingest_civil_filings(
                        conn,
                        source_key=f"import-{_county_key(county)}",
                        display_name=f"Imported {county}",
                        adapter_type="import_file",
                        county=county,
                        records=records,
                    )
                    county_result.update({"mode": "import_file", "source_file": source_file, **result})
                    live_ok = True
            if not live_ok and seed_demo and county == "Yellowstone":
                result = ingest_civil_filings(
                    conn,
                    source_key="demo-yellowstone",
                    display_name="Demo Yellowstone Civil Filings",
                    adapter_type="demo_seed",
                    county="Yellowstone",
                    records=_seed_demo_civil_rows(),
                )
                county_result.update({"mode": "demo_seed", **result})
                live_ok = True
            if not live_ok:
                county_result["mode"] = "failed"
            out["civil"].append(county_result)

        for source_key, display_name, city in DEFAULT_CODE_SOURCES:
            result_row: dict[str, Any] = {"source_key": source_key, "city": city}
            loaded = False
            if code_dir and code_dir.exists():
                for ext in ("json", "csv"):
                    path = code_dir / f"{source_key}.{ext}"
                    if path.exists():
                        records = _load_code_file_records(path)
                        result = ingest_records(
                            conn,
                            source_key=source_key,
                            display_name=display_name,
                            city=city,
                            records=records,
                        )
                        result_row.update({"mode": "import_file", "source_file": str(path), **result})
                        loaded = True
                        break
            if not loaded and seed_demo and source_key == "billings":
                result = ingest_records(
                    conn,
                    source_key=source_key,
                    display_name=display_name,
                    city=city,
                    records=_seed_demo_code_rows(),
                )
                result_row.update({"mode": "demo_seed", **result})
                loaded = True
            if not loaded:
                result_row["mode"] = "skipped"
            out["code"].append(result_row)

        return out
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Civil + code violations ingestion pipeline with live/import/demo fallback."
    )
    parser.add_argument("--civil-import-dir", default="", help="Directory with county json/csv files (e.g. yellowstone.json)")
    parser.add_argument("--code-import-dir", default="", help="Directory with city source files (e.g. billings.csv)")
    parser.add_argument("--no-live", action="store_true", help="Skip live endpoint harvesting")
    parser.add_argument("--seed-demo", action="store_true", help="Seed fallback demo records when no live/import records exist")
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--delay-seconds", type=float, default=0.25)
    args = parser.parse_args()

    result = run_pipeline(
        civil_import_dir=args.civil_import_dir,
        code_import_dir=args.code_import_dir,
        live=not args.no_live,
        seed_demo=args.seed_demo,
        max_pages=args.max_pages,
        delay_seconds=args.delay_seconds,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
