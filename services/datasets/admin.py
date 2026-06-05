from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from typing import Any, Dict, List

from services.datasets.catalog import DATASET_DEFINITIONS
from services.datasets.schema import ensure_dataset_metrics_schema


_STALE_AFTER_HOURS = 24
_FAILING_AFTER_HOURS = 72


def _parse_utc_timestamp(value: Any) -> _dt.datetime | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        parsed = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed.astimezone(_dt.timezone.utc)


def _format_utc_timestamp(value: _dt.datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _format_age_hours(delta_hours: float) -> str:
    if delta_hours < 24:
        return f"{int(delta_hours)}h ago"
    days = delta_hours / 24
    if days < 2:
        return "1d ago"
    return f"{int(days)}d ago"


def _parse_json_list(value: str | None) -> List[Dict[str, Any]]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    return []


def build_data_center_ops_summary(conn: sqlite3.Connection) -> Dict[str, Any]:
    ensure_dataset_metrics_schema(conn)

    rows = conn.execute(
        "SELECT * FROM dataset_metrics"
    ).fetchall()
    rows_by_slug = {row["dataset_slug"]: row for row in rows}

    now = _dt.datetime.now(_dt.timezone.utc)
    datasets: List[Dict[str, Any]] = []
    counts = {
        "fresh": 0,
        "stale": 0,
        "failing": 0,
    }
    latest_refresh_at: _dt.datetime | None = None

    for slug, definition in DATASET_DEFINITIONS.items():
        row = rows_by_slug.get(slug)
        updated_at = _parse_utc_timestamp(row["updated_at"]) if row else None
        if updated_at and (latest_refresh_at is None or updated_at > latest_refresh_at):
            latest_refresh_at = updated_at

        if row is None or updated_at is None:
            freshness_state = "failing"
            freshness_label = "No refresh yet" if row is None else "Invalid refresh timestamp"
            freshness_age = None
        else:
            age_hours = max((now - updated_at).total_seconds() / 3600.0, 0.0)
            freshness_age = _format_age_hours(age_hours)
            if age_hours <= _STALE_AFTER_HOURS:
                freshness_state = "fresh"
                freshness_label = f"Fresh · {freshness_age}"
            elif age_hours <= _FAILING_AFTER_HOURS:
                freshness_state = "stale"
                freshness_label = f"Stale · {freshness_age}"
            else:
                freshness_state = "failing"
                freshness_label = f"Failing · {freshness_age}"

        counts[freshness_state] += 1
        coverage_items = _parse_json_list(row["coverage_json"] if row else None)
        datasets.append(
            {
                "slug": slug,
                "title": definition.title,
                "summary": definition.summary,
                "records_href": definition.records_href,
                "updated_at": _format_utc_timestamp(updated_at),
                "freshness_state": freshness_state,
                "freshness_label": freshness_label,
                "window_1d_count": int(row["window_1d_count"] or 0) if row else 0,
                "window_7d_count": int(row["window_7d_count"] or 0) if row else 0,
                "window_30d_count": int(row["window_30d_count"] or 0) if row else 0,
                "coverage_count": len(coverage_items),
            }
        )

    summary = {
        "dataset_count": len(datasets),
        "fresh_count": counts["fresh"],
        "stale_count": counts["stale"],
        "failing_count": counts["failing"],
        "latest_refresh_at": _format_utc_timestamp(latest_refresh_at) or "Never",
        "stale_after_hours": _STALE_AFTER_HOURS,
        "failing_after_hours": _FAILING_AFTER_HOURS,
    }

    return {
        "summary": summary,
        "datasets": datasets,
    }
