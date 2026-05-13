from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

import requests

from db import connect_db
from services.persons.sex_offender_scraper import _normalize_name, _upsert_offender

LAYER_URL = (
    "https://services.arcgis.com/lnWnvGNQtyvDCFPs/arcgis/rest/services/"
    "Montana_SVOR_Web/FeatureServer/0"
)
QUERY_URL = f"{LAYER_URL}/query"
DETAIL_BASE = "https://app.dojmt.gov/apps/svow/"
REQUEST_DELAY = 0.1


def _fetch_batch(*, offset: int, limit: int) -> list[dict[str, Any]]:
    resp = requests.get(
        QUERY_URL,
        params={
            "where": "1=1",
            "outFields": "*",
            "returnGeometry": "true",
            "f": "json",
            "orderByFields": "OBJECTID ASC",
            "resultOffset": offset,
            "resultRecordCount": limit,
        },
        timeout=45,
        headers={"User-Agent": "MontanaBlotter/1.0"},
    )
    resp.raise_for_status()
    payload = resp.json()
    return payload.get("features", []) or []


def _to_record(feature: dict[str, Any]) -> dict[str, Any] | None:
    attrs = feature.get("attributes") or {}
    geom = feature.get("geometry") or {}
    sid = str(attrs.get("PERS_SID") or "").strip()
    name = _normalize_name(str(attrs.get("NAME") or "").strip())
    if not sid or not name:
        return None

    detail_path = str(attrs.get("DETAIL_URL") or "").strip()
    detail_url = urljoin(DETAIL_BASE, detail_path) if detail_path else DETAIL_BASE
    offense = str(attrs.get("OFF_TYP") or "").strip().upper()
    tier_val = attrs.get("TIER_LVL")
    tier = "" if tier_val is None else str(tier_val)
    if offense == "VIOLENT":
        tier = tier or "VIOLENT"

    photo = str(attrs.get("PHOTO_URL") or "").strip()
    if photo and not photo.startswith("http"):
        photo = urljoin(DETAIL_BASE, photo.lstrip("./"))

    return {
        "registry_id": sid,
        "full_name": name,
        "date_of_birth": None,
        "tier": tier,
        "risk_level": str(attrs.get("DESIGNATION") or "").strip(),
        "status": "active",
        "address_street": str(attrs.get("ADDRESS") or "").strip(),
        "address_city": str(attrs.get("CITY") or "").strip(),
        "address_county": str(attrs.get("COUNTY") or "").strip(),
        "address_zip": "" if attrs.get("ZIP") is None else str(attrs.get("ZIP")),
        "lat": attrs.get("LATITUDE") if attrs.get("LATITUDE") is not None else geom.get("y"),
        "lon": attrs.get("LONGITUDE") if attrs.get("LONGITUDE") is not None else geom.get("x"),
        "employer_name": "",
        "employer_address": "",
        "school_name": "",
        "school_address": "",
        "offense_description": offense,
        "conviction_date": None,
        "conviction_state": "MT",
        "conviction_county": str(attrs.get("COUNTY") or "").strip(),
        "photo_url": photo,
        "source_url": detail_url,
        "raw_json": json.dumps(
            {
                "attributes": attrs,
                "geometry": geom,
            },
            ensure_ascii=True,
        ),
    }


def run_sync(*, dry_run: bool = False, full_sync: bool = False, batch_size: int = 2000) -> dict[str, Any]:
    started = time.time()
    conn = connect_db()
    try:
        seen_ids: set[str] = set()
        new_count = 0
        updated_count = 0
        error_count = 0
        offset = 0
        rows_seen = 0

        while True:
            batch = _fetch_batch(offset=offset, limit=batch_size)
            if not batch:
                break
            rows_seen += len(batch)
            for feature in batch:
                record = _to_record(feature)
                if not record:
                    continue
                seen_ids.add(record["registry_id"])
                if dry_run:
                    continue
                try:
                    _, is_new = _upsert_offender(conn, record)
                    if is_new:
                        new_count += 1
                    else:
                        updated_count += 1
                except Exception:
                    error_count += 1
            offset += len(batch)
            if len(batch) < batch_size:
                break
            time.sleep(REQUEST_DELAY)

        removed_count = 0
        if not dry_run and full_sync and seen_ids:
            placeholders = ",".join("?" for _ in seen_ids)
            conn.execute(
                f"UPDATE sex_offenders SET status='removed', updated_at=datetime('now') "
                f"WHERE registry_id NOT IN ({placeholders}) AND status='active'",
                tuple(seen_ids),
            )
            removed_count = conn.execute(
                "SELECT COUNT(*) FROM sex_offenders WHERE status='removed' AND updated_at > datetime('now', '-5 minutes')"
            ).fetchone()[0]

        if not dry_run:
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
                    f"arcgis feed sync; rows_seen={rows_seen}; unique_ids={len(seen_ids)}; full_sync={'yes' if full_sync else 'no'}",
                ),
            )
            conn.commit()

        return {
            "rows_seen": rows_seen,
            "unique_ids": len(seen_ids),
            "new": new_count,
            "updated": updated_count,
            "removed": removed_count,
            "errors": error_count,
            "duration": int(time.time() - started),
            "dry_run": dry_run,
            "full_sync": full_sync,
        }
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Montana SVOR offenders from ArcGIS feed")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--full-sync", action="store_true")
    parser.add_argument("--batch-size", type=int, default=2000)
    args = parser.parse_args()
    result = run_sync(dry_run=args.dry_run, full_sync=args.full_sync, batch_size=args.batch_size)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
