"""
Data freshness checks for Montana Blotter.

Reports how fresh the arrests feed (records/blotters) and the jail rosters
(jail_bookings) are, per source. Designed to back both a CLI command and a
/health/freshness JSON endpoint.

Pure stdlib + the project DB. No external network.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# DB path is overridable for tests via monkeypatch on this module attribute.
DB_PATH = os.environ.get(
    "MB_DB_PATH", "/root/montanablotter/data/blotter.db"
)

# Freshness thresholds (in hours).
ARRESTS_FRESH_HOURS = 24
ARRESTS_STALE_HOURS = 48
JAIL_FRESH_HOURS = 24
JAIL_STALE_HOURS = 48


@dataclass(frozen=True)
class Thresholds:
    fresh_hours: int
    stale_hours: int


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _connect() -> sqlite3.Connection:
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"DB not found at {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _safe_query(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    """Run a query; on error return empty list (freshness is best-effort)."""
    try:
        conn = _connect()
        try:
            return list(conn.execute(sql, params).fetchall())
        finally:
            conn.close()
    except sqlite3.Error:
        return []


def _age_hours(ts: str | None, now: datetime) -> float | None:
    if not ts:
        return None
    raw = ts.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        try:
            parsed = datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return round((now - parsed).total_seconds() / 3600, 2)


def _classify(age_hours: float | None, thresholds: Thresholds) -> str:
    if age_hours is None:
        return "missing"
    # Strict less-than for fresh, less-than-or-equal for lagging so anything
    # exactly at the stale threshold flips to stale.
    if age_hours < thresholds.fresh_hours:
        return "fresh"
    if age_hours < thresholds.stale_hours:
        return "lagging"
    return "stale"


def check_arrests() -> dict[str, Any]:
    """Inspect records + blotters + source_documents for fresh arrest data."""
    now = _now()
    thresholds = Thresholds(ARRESTS_FRESH_HOURS, ARRESTS_STALE_HOURS)

    records_latest = _safe_query("SELECT MAX(created_at) AS m FROM records")
    blotters_latest = _safe_query("SELECT MAX(upload_date) AS m FROM blotters")
    docs_latest = _safe_query(
        "SELECT MAX(created_at) AS m FROM source_documents "
        "WHERE source_type IN ('imap_pdf','email')"
    )

    records_age = _age_hours(
        records_latest[0]["m"] if records_latest else None, now
    )
    blotters_age = _age_hours(
        blotters_latest[0]["m"] if blotters_latest else None, now
    )
    docs_age = _age_hours(docs_latest[0]["m"] if docs_latest else None, now)

    rows_24h = _safe_query(
        "SELECT COUNT(*) AS c FROM records WHERE created_at >= datetime('now','-1 day')"
    )
    rows_7d = _safe_query(
        "SELECT COUNT(*) AS c FROM records WHERE created_at >= datetime('now','-7 days')"
    )

    # Worst-of across the three signals.
    candidates = [a for a in (records_age, blotters_age, docs_age) if a is not None]
    worst_age = max(candidates) if candidates else None
    status = _classify(worst_age, thresholds)
    if status == "missing":
        # If we have data, just old; never_run is reserved for jail sources.
        if records_age is None and blotters_age is None and docs_age is None:
            status = "missing"

    by_county = _safe_query(
        """
        SELECT county, MAX(created_at) AS latest, COUNT(*) AS n
        FROM records
        WHERE created_at >= datetime('now','-14 days')
        GROUP BY county
        ORDER BY latest DESC
        """
    )

    return {
        "status": status,
        "latest_created_at": records_latest[0]["m"] if records_latest else None,
        "latest_record_age_hours": records_age,
        "latest_blotter_upload": blotters_latest[0]["m"] if blotters_latest else None,
        "latest_blotter_age_hours": blotters_age,
        "latest_source_document": docs_latest[0]["m"] if docs_latest else None,
        "latest_source_document_age_hours": docs_age,
        "rows_last_24h": rows_24h[0]["c"] if rows_24h else 0,
        "rows_last_7d": rows_7d[0]["c"] if rows_7d else 0,
        "active_counties_14d": [
            {"county": r["county"], "latest": r["latest"], "rows": r["n"]}
            for r in by_county
        ],
    }


def check_jail_rosters() -> dict[str, Any]:
    """Inspect jail_booking_sources + jail_bookings for fresh jail rosters."""
    now = _now()
    thresholds = Thresholds(JAIL_FRESH_HOURS, JAIL_STALE_HOURS)

    sources = _safe_query(
        """
        SELECT
            s.id,
            s.county_slug,
            s.county_name,
            s.facility_name,
            s.is_enabled,
            s.last_success_at,
            (SELECT MAX(jb.last_seen_at) FROM jail_bookings jb WHERE jb.source_id = s.id) AS latest_seen,
            (SELECT MAX(jb.booking_at)  FROM jail_bookings jb WHERE jb.source_id = s.id) AS latest_booking,
            (SELECT COUNT(*)             FROM jail_bookings jb WHERE jb.source_id = s.id) AS total_rows
        FROM jail_booking_sources s
        ORDER BY s.county_name
        """
    )

    out_sources: list[dict[str, Any]] = []
    enabled_with_rows = 0
    enabled_without_rows: list[str] = []
    enabled_stale: list[str] = []

    for row in sources:
        latest_seen = row["latest_seen"]
        latest_booking = row["latest_booking"]
        total = row["total_rows"] or 0
        is_enabled = bool(row["is_enabled"])

        # Decide status.
        if not is_enabled:
            status = "disabled"
        elif total == 0 and not row["last_success_at"]:
            status = "never_run"
        else:
            age = _age_hours(latest_seen or row["last_success_at"], now)
            if age is None:
                status = "missing"
            else:
                if age <= thresholds.fresh_hours:
                    status = "fresh"
                elif age <= thresholds.stale_hours:
                    status = "lagging"
                else:
                    status = "stale"

        if is_enabled:
            if total > 0:
                enabled_with_rows += 1
            else:
                enabled_without_rows.append(row["county_slug"])
            if status in {"stale", "missing", "never_run"}:
                enabled_stale.append(row["county_slug"])

        out_sources.append(
            {
                "id": row["id"],
                "county_slug": row["county_slug"],
                "county_name": row["county_name"],
                "facility_name": row["facility_name"],
                "is_enabled": is_enabled,
                "status": status,
                "total_rows": total,
                "latest_booking_at": latest_booking,
                "latest_seen_at": latest_seen,
                "last_success_at": row["last_success_at"],
            }
        )

    # Overall jail roster status: stale if any enabled source is stale/never_run
    # AND no enabled source is fresh. Otherwise, fresh.
    enabled_statuses = [
        s["status"] for s in out_sources if s["is_enabled"]
    ]
    if any(s == "fresh" for s in enabled_statuses):
        overall = "fresh"
    elif any(s == "lagging" for s in enabled_statuses):
        overall = "lagging"
    elif enabled_statuses and all(
        s in {"stale", "missing", "never_run"} for s in enabled_statuses
    ):
        overall = "stale"
    else:
        overall = "missing"

    return {
        "status": overall,
        "source_count": len(out_sources),
        "enabled_with_rows": enabled_with_rows,
        "enabled_without_rows": enabled_without_rows,
        "enabled_stale": enabled_stale,
        "sources": out_sources,
    }


def summarize() -> dict[str, Any]:
    arrests = check_arrests()
    jail = check_jail_rosters()

    # Overall: worst-of
    rank = {"fresh": 0, "lagging": 1, "stale": 2, "missing": 3}
    statuses = [arrests["status"], jail["status"]]
    overall = max(statuses, key=lambda s: rank.get(s, 3))

    return {
        "checked_at": _now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": overall,
        "arrests": arrests,
        "jail_rosters": jail,
        "sources": {
            "arrests_latest": arrests.get("latest_created_at"),
            "jail_latest_seen": max(
                (
                    s["latest_seen_at"]
                    for s in jail["sources"]
                    if s.get("latest_seen_at")
                ),
                default=None,
            ),
        },
    }


def main(argv: list[str] | None = None) -> int:
    """CLI: services.ops.freshness [--json]"""
    argv = argv if argv is not None else sys.argv[1:]
    json_mode = "--json" in argv

    payload = summarize()
    if json_mode:
        print(json.dumps(payload, indent=2))
    else:
        print(
            f"[freshness] status={payload['status']} "
            f"arrests={payload['arrests']['status']} "
            f"jail={payload['jail_rosters']['status']} "
            f"arrests_latest={payload['arrests']['latest_created_at']} "
            f"jail_latest={payload['sources']['jail_latest_seen']}"
        )
        if payload["arrests"]["status"] != "fresh":
            print(
                f"  arrests: {payload['arrests']['rows_last_24h']} rows in last 24h, "
                f"{payload['arrests']['rows_last_7d']} in last 7d"
            )
        stale = payload["jail_rosters"]["enabled_stale"]
        if stale:
            print(f"  jail_rosters stale/never_run: {', '.join(stale)}")

    return 0 if payload["status"] == "fresh" else 1


if __name__ == "__main__":
    sys.exit(main())
