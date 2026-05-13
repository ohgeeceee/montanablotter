from __future__ import annotations

import argparse
import csv
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from db import connect_db
from services.persons.sex_offender_scraper import _normalize_date, _normalize_name, _upsert_offender


def _load_records(file_path: str, file_format: str | None = None) -> list[dict[str, Any]]:
    ext = (file_format or Path(file_path).suffix.lstrip(".").lower()).lower()
    if ext == "json":
        with open(file_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, dict) and "records" in payload:
            records = payload["records"]
        elif isinstance(payload, list):
            records = payload
        else:
            records = [payload]
        return [dict(item) for item in records]
    if ext == "csv":
        with open(file_path, "r", encoding="utf-8", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    raise ValueError(f"Unsupported import format: {ext}")


def _first_non_empty(raw: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(raw.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _normalize_import_record(raw: dict[str, Any], *, source_label: str) -> dict[str, Any]:
    registry_id = _first_non_empty(raw, "registry_id", "id", "offender_id", "external_id")
    full_name = _normalize_name(_first_non_empty(raw, "full_name", "name"))
    if not registry_id or not full_name:
        raise ValueError("Missing required fields: registry_id/full_name")
    return {
        "registry_id": registry_id,
        "full_name": full_name,
        "date_of_birth": _normalize_date(_first_non_empty(raw, "date_of_birth", "dob")),
        "tier": _first_non_empty(raw, "tier"),
        "risk_level": _first_non_empty(raw, "risk_level", "risk"),
        "status": _first_non_empty(raw, "status") or "active",
        "address_street": _first_non_empty(raw, "address_street", "street", "address"),
        "address_city": _first_non_empty(raw, "address_city", "city"),
        "address_county": _first_non_empty(raw, "address_county", "county"),
        "address_zip": _first_non_empty(raw, "address_zip", "zip", "zipcode"),
        "employer_name": _first_non_empty(raw, "employer_name", "employer"),
        "employer_address": _first_non_empty(raw, "employer_address"),
        "school_name": _first_non_empty(raw, "school_name", "school"),
        "school_address": _first_non_empty(raw, "school_address"),
        "offense_description": _first_non_empty(raw, "offense_description", "offense"),
        "conviction_date": _normalize_date(_first_non_empty(raw, "conviction_date")),
        "conviction_state": _first_non_empty(raw, "conviction_state") or "MT",
        "conviction_county": _first_non_empty(raw, "conviction_county"),
        "photo_url": _first_non_empty(raw, "photo_url"),
        "source_url": _first_non_empty(raw, "source_url") or source_label,
        "offender_type": _first_non_empty(raw, "offender_type", "registry_type", "type"),
        "raw_json": json.dumps(raw, ensure_ascii=True),
    }


def import_sex_offender_cache(
    *,
    file_path: str,
    file_format: str | None = None,
    full_sync: bool = False,
    source_label: str = "cache_import",
) -> dict[str, int]:
    started = time.time()
    records = _load_records(file_path, file_format=file_format)
    conn = connect_db()
    try:
        imported_ids: set[str] = set()
        new_count = 0
        updated_count = 0
        error_count = 0
        for raw in records:
            try:
                normalized = _normalize_import_record(raw, source_label=source_label)
                imported_ids.add(normalized["registry_id"])
                _, is_new = _upsert_offender(conn, normalized)
                if is_new:
                    new_count += 1
                else:
                    updated_count += 1
            except Exception:
                error_count += 1

        removed_count = 0
        if full_sync and imported_ids:
            placeholders = ",".join("?" for _ in imported_ids)
            conn.execute(
                f"UPDATE sex_offenders SET status='removed', updated_at=datetime('now') "
                f"WHERE registry_id NOT IN ({placeholders}) AND status='active'",
                tuple(imported_ids),
            )
            removed_count = conn.execute(
                "SELECT COUNT(*) FROM sex_offenders WHERE status='removed' AND updated_at > datetime('now', '-5 minutes')"
            ).fetchone()[0]
            conn.commit()

        total_active = conn.execute(
            "SELECT COUNT(*) FROM sex_offenders WHERE status='active'"
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO sex_offender_snapshots
            (snapshot_date, total_count, new_count, removed_count, changed_count, scrape_duration_seconds, notes)
            VALUES (datetime('now'), ?, ?, ?, ?, ?, ?)
            """,
            (
                total_active,
                new_count,
                removed_count,
                updated_count,
                int(time.time() - started),
                f"cache import from {source_label}",
            ),
        )
        conn.commit()
        return {
            "records": len(records),
            "new": new_count,
            "updated": updated_count,
            "removed": removed_count,
            "errors": error_count,
        }
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Import violent/sexual offender cache file (JSON/CSV)")
    parser.add_argument("--file", required=True, help="Path to JSON/CSV cache file")
    parser.add_argument("--format", choices=["json", "csv"], help="Optional format override")
    parser.add_argument("--full-sync", action="store_true", help="Mark not-in-file records as removed")
    parser.add_argument("--source-label", default="", help="Source label for provenance notes")
    args = parser.parse_args()

    source_label = args.source_label.strip() or os.path.basename(args.file)
    result = import_sex_offender_cache(
        file_path=args.file,
        file_format=args.format,
        full_sync=args.full_sync,
        source_label=source_label,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
