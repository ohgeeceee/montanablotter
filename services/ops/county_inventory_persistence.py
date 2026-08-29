#!/usr/bin/env python3
"""
County inventory persistence helper.

Provides a Denormalized county_inventory table, a refresh command, and helpers
for the public coverage page and admin dashboard widget.
"""

import sqlite3
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = os.getenv("MB_DB_PATH", "/root/montanablotter/blotter.db").strip() or "/root/montanablotter/blotter.db"
COUNTY_INVENTORY_MODULE = "/root/montanablotter/county_inventory.py"


def _configure_sqlite(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")


def _safe_add_column(cursor: sqlite3.Cursor, table: str, col: str, definition: str) -> bool:
    try:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
        return True
    except sqlite3.OperationalError as exc:
        if "duplicate column" in str(exc).lower():
            return False
        raise


def ensure_county_inventory_schema(conn: sqlite3.Connection) -> None:
    """Create the county_inventory table and supporting indexes if missing."""
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS county_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            county TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'not_covered',
            blotters_30d INTEGER NOT NULL DEFAULT 0,
            fetcher_module TEXT,
            source_types TEXT,
            stale_alerts INTEGER NOT NULL DEFAULT 0,
            population_rank INTEGER NOT NULL DEFAULT 999,
            refreshed_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_county_inventory_status ON county_inventory(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_county_inventory_county ON county_inventory(county)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_county_inventory_rank ON county_inventory(population_rank)")
    conn.commit()


def refresh_county_inventory(conn: sqlite3.Connection) -> int:
    """Refresh every row in county_inventory from the live script logic.

    Delegates to county_inventory.build_inventory() so the DB table stays in
    sync with whatever the script knows about fetcher modules and sources.
    Returns the number of rows refreshed.
    """
    # Import the builder without mutating sys.path in a way that surprises callers.
    import importlib.util
    spec = importlib.util.spec_from_file_location("county_inventory_builder", COUNTY_INVENTORY_MODULE)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)

    inventory = builder.build_inventory()
    cursor = conn.cursor()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    refreshed = 0
    for row in inventory:
        cursor.execute("""
            INSERT INTO county_inventory AS ci (county, status, blotters_30d, fetcher_module, source_types, stale_alerts, population_rank, refreshed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(county) DO UPDATE SET
                status = excluded.status,
                blotters_30d = excluded.blotters_30d,
                fetcher_module = excluded.fetcher_module,
                source_types = excluded.source_types,
                stale_alerts = excluded.stale_alerts,
                population_rank = excluded.population_rank,
                refreshed_at = excluded.refreshed_at
        """, (
            row["county"],
            row["status"],
            row["blotters_30d"],
            row["fetcher_module"],
            row["source_types"],
            row["stale_alerts"],
            row["population_rank"],
            now,
        ))
        refreshed += 1

    conn.commit()
    return refreshed


def refresh_county_inventory_cli() -> None:
    """Standalone CLI for cron or manual refresh."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    _configure_sqlite(conn)
    ensure_county_inventory_schema(conn)
    n = refresh_county_inventory(conn)
    print(f"Refreshed {n} county inventory rows at {datetime.now(timezone.utc).isoformat()}")
    conn.close()


if __name__ == "__main__":
    refresh_county_inventory_cli()
