"""
Smoke test for the /health/freshness endpoint.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Spin up the Flask test client with a temp DB."""
    import app
    import services.ops.freshness as freshness
    import sqlite3

    db_path = tmp_path / "blotter.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE records (id INTEGER PRIMARY KEY AUTOINCREMENT,
                              created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                              county TEXT, date TEXT);
        CREATE TABLE blotters (id INTEGER PRIMARY KEY AUTOINCREMENT,
                               upload_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                               county TEXT);
        CREATE TABLE jail_booking_sources (id INTEGER PRIMARY KEY AUTOINCREMENT,
                                            county_slug TEXT UNIQUE NOT NULL,
                                            county_name TEXT NOT NULL,
                                            facility_name TEXT NOT NULL,
                                            is_enabled INTEGER NOT NULL DEFAULT 1,
                                            last_success_at TEXT);
        CREATE TABLE jail_bookings (id INTEGER PRIMARY KEY AUTOINCREMENT,
                                     source_id INTEGER, county_slug TEXT NOT NULL,
                                     booking_at TEXT,
                                     first_seen_at TEXT DEFAULT (datetime('now')),
                                     last_seen_at TEXT DEFAULT (datetime('now')));
        CREATE TABLE ingestion_jobs (id INTEGER PRIMARY KEY AUTOINCREMENT,
                                      source_document_id INTEGER, status TEXT,
                                      started_at TEXT DEFAULT (datetime('now')));
        CREATE TABLE source_documents (id INTEGER PRIMARY KEY AUTOINCREMENT,
                                        source_type TEXT,
                                        created_at TEXT DEFAULT (datetime('now')));
        CREATE TABLE sex_offenders (id INTEGER PRIMARY KEY AUTOINCREMENT,
                                    status TEXT,
                                    address_county TEXT);
        CREATE TABLE sex_offender_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT,
                                             snapshot_date TEXT,
                                             total_count INTEGER,
                                             new_count INTEGER,
                                             removed_count INTEGER,
                                             changed_count INTEGER);
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(freshness, "DB_PATH", str(db_path))

    app.app.config["TESTING"] = True
    return app.app.test_client()


def test_health_freshness_endpoint_returns_json(client):
    response = client.get("/health/freshness")
    assert response.status_code == 200
    data = json.loads(response.data)
    assert "status" in data
    assert "arrests" in data
    assert "jail_rosters" in data
    assert "sex_offender_registry" in data


def test_health_freshness_ignores_malformed_record_timestamp(client, monkeypatch):
    import services.ops.freshness as freshness

    conn = freshness._connect()
    try:
        conn.execute("INSERT INTO records (created_at, county) VALUES (?, ?)", ("Whitford, Austin", "21"))
        conn.execute("INSERT INTO records (created_at, county) VALUES (datetime('now'), ?)", ("Cascade",))
        conn.commit()
    finally:
        conn.close()

    payload = freshness.check_arrests()
    assert payload["latest_created_at"] != "Whitford, Austin"


def test_healthz_still_works(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert json.loads(response.data) == {"status": "ok"}
