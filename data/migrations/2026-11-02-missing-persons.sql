-- Migration: missing_persons table (Montana Missing Persons Index)
-- Source: services/persons/missing.py :: ensure_missing_person_schema
-- Backlog item #2 of docs/MISSING_PERSONS_TEAM.md

CREATE TABLE IF NOT EXISTS missing_persons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE,
    full_name TEXT NOT NULL,
    age INTEGER,
    city TEXT DEFAULT '',
    county TEXT DEFAULT '',
    last_seen_at TEXT DEFAULT '',
    last_seen_location TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL,
    physical_description TEXT DEFAULT '',
    contact_info TEXT DEFAULT '',
    source_name TEXT DEFAULT '',
    source_url TEXT DEFAULT '',
    photo_url TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'missing',
    resolution_summary TEXT DEFAULT '',
    resolved_at TEXT DEFAULT '',
    last_alerted_at TEXT DEFAULT '',
    alert_delivery_count INTEGER NOT NULL DEFAULT 0,
    notification_version INTEGER NOT NULL DEFAULT 1,
    created_by TEXT DEFAULT '',
    updated_by TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_missing_persons_status_updated
    ON missing_persons(status, updated_at);

CREATE INDEX IF NOT EXISTS idx_missing_persons_county_status
    ON missing_persons(county, status);
