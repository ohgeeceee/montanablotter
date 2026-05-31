"""
Warrant record data model and DB schema helpers.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass
class WarrantRecord:
    source_record_id: str   # unique key, e.g. "rosebud-warrant:doe-john"
    county: str
    person_name: str
    city: str = ""
    dob: str = ""
    warrant_type: str = ""  # bench | arrest | felony | misdemeanor
    charges_text: str = ""
    issued_by: str = ""     # court / judge
    issue_date: str = ""
    bond_amount: str = ""
    bond_type: str = ""
    status: str = "active"
    source_url: str = ""


def ensure_warrant_schema(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS warrants (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            source_record_id TEXT    UNIQUE NOT NULL,
            county           TEXT    NOT NULL,
            city             TEXT    NOT NULL DEFAULT '',
            person_name      TEXT    NOT NULL,
            dob              TEXT    NOT NULL DEFAULT '',
            warrant_type     TEXT    NOT NULL DEFAULT '',
            charges_text     TEXT    NOT NULL DEFAULT '',
            issued_by        TEXT    NOT NULL DEFAULT '',
            issue_date       TEXT    NOT NULL DEFAULT '',
            bond_amount      TEXT    NOT NULL DEFAULT '',
            bond_type        TEXT    NOT NULL DEFAULT '',
            status           TEXT    NOT NULL DEFAULT 'active',
            source_url       TEXT    NOT NULL DEFAULT '',
            resolved_at      TEXT    NOT NULL DEFAULT '',
            scraped_at       TEXT    NOT NULL,
            first_seen_at    TEXT    NOT NULL,
            updated_at       TEXT    NOT NULL
        )
        """
    )
    try:
        cursor.execute(
            "ALTER TABLE warrants ADD COLUMN resolved_at TEXT NOT NULL DEFAULT ''"
        )
    except sqlite3.OperationalError:
        pass
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_warrants_county_status "
        "ON warrants(county, status, updated_at)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_warrants_person "
        "ON warrants(person_name, status)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_warrants_source_id "
        "ON warrants(source_record_id)"
    )
    conn.commit()
