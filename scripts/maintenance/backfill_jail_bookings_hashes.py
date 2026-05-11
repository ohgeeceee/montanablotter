#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3

import config
from services.ingestion.jail_bookings import _compute_booking_hash



def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=float(getattr(config, "DB_TIMEOUT_SECONDS", 30)))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {int(getattr(config, 'DB_BUSY_TIMEOUT_MS', 30000))}")
    return conn



def build_raw_json(row: sqlite3.Row) -> str:
    payload = {
        "source_record_id": row["source_record_id"],
        "person_name": row["person_name"],
        "age": row["age"],
        "booking_number": row["booking_number"],
        "booking_at": row["booking_at"],
        "charges_summary": row["charges_summary"],
        "source_url": row["source_url"],
    }
    return json.dumps(payload, ensure_ascii=False)



def main() -> None:
    with connect_db() as conn:
        rows = conn.execute(
            """
            SELECT id, county_slug, county_name, facility_name, person_name, age,
                   booking_number, booking_at, charges_summary, source_url, source_record_id,
                   hash_id, raw_json
            FROM jail_bookings
            """
        ).fetchall()

        updated = 0
        for row in rows:
            hash_payload = {
                "county_slug": row["county_slug"],
                "county_name": row["county_name"],
                "facility_name": row["facility_name"],
                "person_name": row["person_name"],
                "booking_number": row["booking_number"],
                "booking_at": row["booking_at"],
                "charges_summary": row["charges_summary"],
                "source_url": row["source_url"],
                "source_record_id": row["source_record_id"],
            }
            hash_id = _compute_booking_hash(hash_payload)
            raw_json = row["raw_json"] or build_raw_json(row)
            if row["hash_id"] == hash_id and row["raw_json"]:
                continue
            conn.execute(
                "UPDATE jail_bookings SET hash_id = ?, raw_json = ?, updated_at = datetime('now') WHERE id = ?",
                (hash_id, raw_json, row["id"]),
            )
            updated += 1

        conn.commit()
        print(f"Backfilled {updated} jail_bookings rows")


if __name__ == "__main__":
    main()
