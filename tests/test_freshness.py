"""
Tests for services.ops.freshness — per-source data freshness for arrests and jail rosters.

These tests intentionally do NOT touch the real production database. They use a
tempdir with a throwaway SQLite DB populated via the same schema as
data/blotter.db, but only the tables the freshness module cares about.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import services.ops.freshness as freshness


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Create a temp blotter.db with the tables freshness queries against."""
    db_path = tmp_path / "blotter.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            county TEXT,
            date TEXT
        );
        CREATE TABLE blotters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            upload_date DATETIME DEFAULT CURRENT_TIMESTAMP,
            county TEXT
        );
        CREATE TABLE jail_booking_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            county_slug TEXT UNIQUE NOT NULL,
            county_name TEXT NOT NULL,
            facility_name TEXT NOT NULL,
            is_enabled INTEGER NOT NULL DEFAULT 1,
            last_success_at TEXT
        );
        CREATE TABLE jail_bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER,
            county_slug TEXT NOT NULL,
            booking_at TEXT,
            first_seen_at TEXT DEFAULT (datetime('now')),
            last_seen_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE ingestion_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_document_id INTEGER,
            status TEXT,
            started_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE source_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(freshness, "DB_PATH", str(db_path))
    return db_path


def _now() -> datetime:
    return datetime.now(timezone.utc)


def test_freshness_summary_returns_overall_status(temp_db):
    """Summary exposes overall ok/stale status and per-section counts."""
    summary = freshness.summarize()
    assert "status" in summary
    assert summary["status"] in {"ok", "stale", "missing"}
    assert "arrests" in summary
    assert "jail_rosters" in summary
    assert "sources" in summary


def test_freshness_arrests_fresh_when_records_today(temp_db):
    """If records.created_at is today, arrests section reports fresh."""
    conn = sqlite3.connect(temp_db)
    conn.execute(
        "INSERT INTO records (created_at, county, date) VALUES (?, 'Gallatin', '2026-06-11')",
        (_now().strftime("%Y-%m-%d %H:%M:%S"),),
    )
    conn.commit()
    conn.close()

    section = freshness.check_arrests()
    assert section["status"] == "fresh"
    assert section["latest_created_at"] is not None
    assert section["rows_last_24h"] >= 1


def test_freshness_arrests_stale_when_no_records_in_48h(temp_db):
    """If no records in last 48h, arrests section reports stale."""
    two_days_ago = (_now() - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(temp_db)
    conn.execute(
        "INSERT INTO records (created_at, county, date) VALUES (?, 'Missoula', '2026-06-09')",
        (two_days_ago,),
    )
    conn.commit()
    conn.close()

    section = freshness.check_arrests()
    assert section["status"] == "stale"
    assert section["rows_last_24h"] == 0


def test_freshness_jail_rosters_reports_per_source_lag(temp_db):
    """Per-source jail roster lag should be exposed, including zero-row sources."""
    conn = sqlite3.connect(temp_db)
    conn.execute(
        "INSERT INTO jail_booking_sources (county_slug, county_name, facility_name, is_enabled) "
        "VALUES ('yellowstone', 'Yellowstone', 'YCDC', 1)"
    )
    conn.execute(
        "INSERT INTO jail_booking_sources (county_slug, county_name, facility_name, is_enabled) "
        "VALUES ('missoula', 'Missoula', 'MCDC', 1)"
    )
    missoula_latest = (_now() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO jail_bookings (source_id, county_slug, booking_at, first_seen_at, last_seen_at) "
        "VALUES (2, 'missoula', ?, ?, ?)",
        (missoula_latest, missoula_latest, missoula_latest),
    )
    conn.commit()
    conn.close()

    section = freshness.check_jail_rosters()
    assert "sources" in section
    by_slug = {s["county_slug"]: s for s in section["sources"]}
    assert by_slug["missoula"]["status"] == "fresh"
    assert by_slug["yellowstone"]["status"] in {"stale", "missing", "never_run"}


def test_freshness_summary_flips_to_stale_when_arrests_stale(temp_db):
    """Overall summary is stale if arrests section is stale."""
    two_days_ago = (_now() - timedelta(hours=50)).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(temp_db)
    conn.execute(
        "INSERT INTO records (created_at, county, date) VALUES (?, 'Cascade', '2026-06-09')",
        (two_days_ago,),
    )
    conn.commit()
    conn.close()

    summary = freshness.summarize()
    assert summary["status"] in {"stale", "missing"}
    assert summary["arrests"]["status"] == "stale"


def test_freshness_jail_source_never_run_status(temp_db):
    """A source with is_enabled=1 but zero rows + no last_success reports never_run."""
    conn = sqlite3.connect(temp_db)
    conn.execute(
        "INSERT INTO jail_booking_sources (county_slug, county_name, facility_name, is_enabled) "
        "VALUES ('beaverhead', 'Beaverhead', 'BCDC', 1)"
    )
    conn.commit()
    conn.close()

    section = freshness.check_jail_rosters()
    beaverhead = next(s for s in section["sources"] if s["county_slug"] == "beaverhead")
    assert beaverhead["status"] == "never_run"
    assert beaverhead["total_rows"] == 0
