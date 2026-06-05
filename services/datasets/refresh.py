from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Callable, Dict, Iterable, List

from services.datasets.catalog import DATASET_DEFINITIONS
from services.datasets.metrics import (
    DATASET_SLUG_ARRESTS,
    DATASET_SLUG_JAIL_BOOKINGS,
    DATASET_SLUG_POLICE_CALLS,
    DATASET_SLUG_PUBLIC_MEETINGS,
    DATASET_SLUG_WARRANTS,
    compute_arrests_metrics,
    compute_jail_bookings_metrics,
    compute_police_calls_metrics,
    compute_public_meetings_metrics,
    compute_warrants_metrics,
)
from services.datasets.schema import ensure_dataset_metrics_schema


DEFAULT_DATASET_SLUGS: List[str] = list(DATASET_DEFINITIONS.keys())

_DATASET_METRIC_FUNCTIONS: Dict[str, Callable[[sqlite3.Connection], Dict[str, object]]] = {
    DATASET_SLUG_JAIL_BOOKINGS: compute_jail_bookings_metrics,
    DATASET_SLUG_WARRANTS: compute_warrants_metrics,
    DATASET_SLUG_ARRESTS: compute_arrests_metrics,
    DATASET_SLUG_PUBLIC_MEETINGS: compute_public_meetings_metrics,
    DATASET_SLUG_POLICE_CALLS: compute_police_calls_metrics,
}


@contextmanager
def file_lock(lock_path: str, stale_seconds: int = 6 * 60 * 60):
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    now = time.time()
    if os.path.exists(lock_path):
        try:
            age = now - os.path.getmtime(lock_path)
            if age < stale_seconds:
                raise RuntimeError(f"lock exists: {lock_path}")
        except OSError:
            pass
    with open(lock_path, "w", encoding="utf-8") as fh:
        fh.write(str(int(now)))
    try:
        yield
    finally:
        try:
            os.unlink(lock_path)
        except OSError:
            pass


def refresh_dataset_metrics(conn: sqlite3.Connection, dataset_slug: str) -> Dict[str, object]:
    compute_metrics = _DATASET_METRIC_FUNCTIONS.get(dataset_slug)
    if compute_metrics is None:
        raise ValueError(f"unknown dataset slug: {dataset_slug}")
    metrics = compute_metrics(conn)

    conn.execute(
        """
        INSERT INTO dataset_metrics (
            dataset_slug, updated_at,
            window_1d_count, window_7d_count, window_30d_count,
            trend_30d_json, top_categories_json, coverage_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(dataset_slug) DO UPDATE SET
            updated_at=excluded.updated_at,
            window_1d_count=excluded.window_1d_count,
            window_7d_count=excluded.window_7d_count,
            window_30d_count=excluded.window_30d_count,
            trend_30d_json=excluded.trend_30d_json,
            top_categories_json=excluded.top_categories_json,
            coverage_json=excluded.coverage_json
        """,
        (
            dataset_slug,
            str(metrics["updated_at"]),
            int(metrics["window_1d_count"]),
            int(metrics["window_7d_count"]),
            int(metrics["window_30d_count"]),
            str(metrics["trend_30d_json"]),
            str(metrics["top_categories_json"]),
            str(metrics["coverage_json"]),
        ),
    )
    return metrics


def refresh_all_dataset_metrics(
    conn: sqlite3.Connection,
    dataset_slugs: Iterable[str] = DEFAULT_DATASET_SLUGS,
) -> Dict[str, Dict[str, object]]:
    ensure_dataset_metrics_schema(conn)
    results: Dict[str, Dict[str, object]] = {}
    for slug in dataset_slugs:
        results[slug] = refresh_dataset_metrics(conn, slug)
    return results
