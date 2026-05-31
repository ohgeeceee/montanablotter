from __future__ import annotations

import sqlite3


def ensure_dataset_metrics_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dataset_metrics (
            dataset_slug TEXT PRIMARY KEY,
            updated_at TEXT NOT NULL,
            window_1d_count INTEGER NOT NULL DEFAULT 0,
            window_7d_count INTEGER NOT NULL DEFAULT 0,
            window_30d_count INTEGER NOT NULL DEFAULT 0,
            trend_30d_json TEXT NOT NULL DEFAULT '[]',
            top_categories_json TEXT NOT NULL DEFAULT '[]',
            coverage_json TEXT NOT NULL DEFAULT '[]'
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_dataset_metrics_updated_at ON dataset_metrics(updated_at)"
    )

