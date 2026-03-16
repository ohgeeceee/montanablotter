"""
bozeman_police_fetcher.py
=========================
Pull current public Bozeman Police Department ArcGIS feeds into Montana Blotter.

Supported datasets:
- calls: Calls for service from the public 30-day calls dashboard
- crime: Daily case reports from the public crime dashboard
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

import config
from dedupe import incident_key_set, incident_keys
from pipeline_state import (
    ensure_ingestion_job,
    ensure_source_document,
    get_ingestion_job_status,
    increment_ingestion_retry,
    log_pipeline_event,
    set_ingestion_job_status,
    sha256_text,
)
from processor import _publish_blotter_outputs

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DatasetConfig:
    key: str
    label: str
    source_type: str
    county: str
    city: str
    agency_name: str
    service_url: str
    date_field: str
    default_days_back: int
    static_filters: tuple[str, ...] = ()


DATASETS: dict[str, DatasetConfig] = {
    "calls": DatasetConfig(
        key="calls",
        label="Bozeman Calls for Service",
        source_type="bozeman_calls_for_service",
        county="Gallatin",
        city="Bozeman",
        agency_name="Bozeman Police Department",
        service_url="https://gisweb.bozeman.net/hosted/rest/services/BPD_CFS_Public_30_Days/FeatureServer/0",
        date_field="DATE",
        default_days_back=2,
    ),
    "crime": DatasetConfig(
        key="crime",
        label="Bozeman Daily Case Reports",
        source_type="bozeman_daily_case_reports",
        county="Gallatin",
        city="Bozeman",
        agency_name="Bozeman Police Department",
        service_url="https://services3.arcgis.com/f4hk1qcfxRJ0L2BU/arcgis/rest/services/BPD_Daily_Case_Reports_2_view/FeatureServer/1",
        date_field="REPORTED_DATE",
        default_days_back=7,
        static_filters=(
            "OFFENSE_CATEGORY = 'Non-Traffic Offense'",
            "CRIME_TYPE NOT IN ('Exclude - Juvenile', '** for Attempt', 'Exclude - Enumeration')",
        ),
    ),
}

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://montanablotter.com)",
        "Accept": "application/json, text/plain, */*",
    }
)

DB_TIMEOUT_SECONDS = float(getattr(config, "DB_TIMEOUT_SECONDS", 30))
DB_BUSY_TIMEOUT_MS = int(getattr(config, "DB_BUSY_TIMEOUT_MS", 30000))


def _connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=DB_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {DB_BUSY_TIMEOUT_MS}")
    return conn


def _table_has_column(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row[1] == column_name for row in rows)


def _load_existing_incident_keys(
    conn: sqlite3.Connection,
    county: str,
    rows: list[dict[str, Any]],
) -> set[str]:
    raw_dates = sorted({(row.get("date") or "").strip() for row in rows if row.get("date")})
    cfs_numbers = sorted({(row.get("cfs_number") or "").strip() for row in rows if row.get("cfs_number")})

    clauses = []
    params: list[str] = []
    if raw_dates:
        placeholders = ",".join("?" for _ in raw_dates)
        clauses.append(f"(county = ? AND date IN ({placeholders}))")
        params.extend([county, *raw_dates])
    if cfs_numbers:
        placeholders = ",".join("?" for _ in cfs_numbers)
        clauses.append(f"(cfs_number IN ({placeholders}))")
        params.extend(cfs_numbers)
    if not clauses:
        return set()

    existing_rows = conn.execute(
        f"""
        SELECT cfs_number, date, time,
               COALESCE(incident_type, incident, '') AS incident_type,
               COALESCE(location, '') AS location,
               COALESCE(details, '') AS details,
               county
        FROM records
        WHERE {' OR '.join(clauses)}
        """,
        params,
    ).fetchall()
    return incident_key_set(existing_rows)


def _window_bounds(days_back: int) -> tuple[datetime, datetime]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=max(days_back, 1))
    return start.replace(minute=0, second=0, microsecond=0), end


def _where_clause(dataset: DatasetConfig, start: datetime, end: datetime) -> str:
    start_text = start.strftime("%Y-%m-%d %H:%M:%S")
    end_text = end.strftime("%Y-%m-%d %H:%M:%S")
    parts = [
        f"{dataset.date_field} >= TIMESTAMP '{start_text}'",
        f"{dataset.date_field} < TIMESTAMP '{end_text}'",
        *dataset.static_filters,
    ]
    return " AND ".join(parts)


def _fetch_features(dataset: DatasetConfig, days_back: int) -> tuple[list[dict[str, Any]], datetime, datetime]:
    start, end = _window_bounds(days_back)
    where = _where_clause(dataset, start, end)
    offset = 0
    page_size = 1000
    features: list[dict[str, Any]] = []

    logger.info("Fetching %s from %s to %s", dataset.label, start.isoformat(), end.isoformat())

    while True:
        response = SESSION.get(
            f"{dataset.service_url}/query",
            params={
                "where": where,
                "outFields": "*",
                "orderByFields": f"{dataset.date_field} DESC",
                "resultOffset": offset,
                "resultRecordCount": page_size,
                "f": "json",
            },
            timeout=45,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            raise RuntimeError(f"ArcGIS query failed: {payload['error']}")

        batch = payload.get("features", [])
        features.extend(batch)
        logger.info("%s: fetched %s rows at offset %s", dataset.key, len(batch), offset)
        if len(batch) < page_size:
            break
        offset += page_size

    return features, start, end


def _format_date(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        dt = datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
        return dt.strftime("%m/%d/%y")
    except (TypeError, ValueError, OSError):
        return ""


def _format_time(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        dt = datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
        time_text = dt.strftime("%H:%M")
        return "" if time_text == "00:00" else time_text
    except (TypeError, ValueError, OSError):
        return ""


def _normalise_call(attributes: dict[str, Any], dataset: DatasetConfig) -> dict[str, Any]:
    cfs_number = (attributes.get("INCIDENT_NUMBER") or "").strip()
    case_number = (attributes.get("CASE_NUMBER") or "").strip()
    incident_type = (
        (attributes.get("PRIMARY_CODE") or "").strip()
        or (attributes.get("PRIMARY_DESCRIPTION") or "").strip()
        or (attributes.get("ALL_CALL_TYPES") or "").strip()
        or "Police Call"
    )

    detail_parts = []
    if attributes.get("ALL_CALL_TYPES"):
        detail_parts.append(f"Call types: {attributes['ALL_CALL_TYPES']}")
    if attributes.get("RESPONDING_AGENCIES"):
        detail_parts.append(f"Agencies: {attributes['RESPONDING_AGENCIES']}")
    if attributes.get("RESULT"):
        detail_parts.append(f"Result: {attributes['RESULT']}")
    if case_number and case_number.upper() != "N/A":
        detail_parts.append(f"Case number: {case_number}")

    return {
        "cfs_number": cfs_number or case_number or f"bozeman-call-{attributes.get('OBJECTID')}",
        "date": _format_date(attributes.get("DATE")),
        "time": (attributes.get("TIME") or "").strip(),
        "incident_type": incident_type,
        "incident": incident_type,
        "location": "Bozeman, MT",
        "details": "; ".join(detail_parts),
        "county": dataset.county,
        "officer": "",
    }


def _normalise_crime(attributes: dict[str, Any], dataset: DatasetConfig) -> dict[str, Any]:
    cfs_number = (attributes.get("CFS_NUMBER") or "").strip()
    case_number = (attributes.get("CASE_NUMBER") or "").strip()
    offense = (attributes.get("OFFENSE") or "").strip()
    statute_offense = (attributes.get("STATUTE_OFFENSE") or "").strip()
    incident_type = (
        (attributes.get("CRIME_TYPE") or "").strip()
        or offense
        or (attributes.get("NIBRS_CODE") or "").strip()
        or "Police Offense"
    )

    detail_parts = []
    if offense:
        detail_parts.append(f"Offense: {offense}")
    if statute_offense and statute_offense != offense:
        detail_parts.append(f"Statute offense: {statute_offense}")
    if attributes.get("STATUTE_CODE"):
        detail_parts.append(f"Statute code: {attributes['STATUTE_CODE']}")
    if attributes.get("Crime_Against"):
        detail_parts.append(f"Crime against: {attributes['Crime_Against']}")
    if attributes.get("SEVERITY"):
        detail_parts.append(f"Severity: {attributes['SEVERITY']}")
    if case_number:
        detail_parts.append(f"Case number: {case_number}")

    return {
        "cfs_number": cfs_number or case_number or f"bozeman-crime-{attributes.get('OBJECTID')}",
        "date": _format_date(attributes.get("REPORTED_DATE")),
        "time": _format_time(attributes.get("REPORTED_DATE")),
        "incident_type": incident_type,
        "incident": incident_type,
        "location": "Bozeman, MT",
        "details": "; ".join(detail_parts),
        "county": dataset.county,
        "officer": "",
    }


def _normalise_rows(dataset: DatasetConfig, features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for feature in features:
        attributes = feature.get("attributes") or {}
        if dataset.key == "calls":
            row = _normalise_call(attributes, dataset)
        elif dataset.key == "crime":
            row = _normalise_crime(attributes, dataset)
        else:
            raise ValueError(f"Unsupported dataset key: {dataset.key}")
        rows.append(row)
    return rows


def _insert_blotter(
    conn: sqlite3.Connection,
    dataset: DatasetConfig,
    rows: list[dict[str, Any]],
    source_document_id: int,
    source_label: str,
) -> int:
    cursor = conn.cursor()
    has_source_type = _table_has_column(conn, "blotters", "source_type")
    has_source_doc = _table_has_column(conn, "blotters", "source_document_id")

    if has_source_type and has_source_doc:
        cursor.execute(
            "INSERT INTO blotters (filename, county, incident_count, source_type, source_document_id) VALUES (?, ?, ?, ?, ?)",
            (source_label, dataset.county, len(rows), dataset.source_type, source_document_id),
        )
    elif has_source_type:
        cursor.execute(
            "INSERT INTO blotters (filename, county, incident_count, source_type) VALUES (?, ?, ?, ?)",
            (source_label, dataset.county, len(rows), dataset.source_type),
        )
    elif has_source_doc:
        cursor.execute(
            "INSERT INTO blotters (filename, county, incident_count, source_document_id) VALUES (?, ?, ?, ?)",
            (source_label, dataset.county, len(rows), source_document_id),
        )
    else:
        cursor.execute(
            "INSERT INTO blotters (filename, county, incident_count) VALUES (?, ?, ?)",
            (source_label, dataset.county, len(rows)),
        )

    if cursor.lastrowid is None:
        raise RuntimeError(f"Failed to insert blotter row for {dataset.key}")
    blotter_id = int(cursor.lastrowid)

    for row in rows:
        cursor.execute(
            """
            INSERT INTO records
                (blotter_id, cfs_number, date, time, incident_type,
                 incident, location, details, county, officer)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                blotter_id,
                row["cfs_number"],
                row["date"],
                row["time"],
                row["incident_type"],
                row["incident"],
                row["location"],
                row["details"],
                row["county"],
                row["officer"],
            ),
        )

    conn.commit()
    return blotter_id


def ingest_dataset(dataset: DatasetConfig, days_back: int, dry_run: bool = False) -> tuple[int, int, int]:
    features, start, end = _fetch_features(dataset, days_back=days_back)
    rows = _normalise_rows(dataset, features)

    feature_payload = [feature.get("attributes") or {} for feature in features]
    payload_text = json.dumps(
        {
            "dataset": dataset.key,
            "service_url": dataset.service_url,
            "features": feature_payload,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    if dry_run:
        print(
            f"{dataset.key}: fetched={len(features)} normalized={len(rows)} "
            f"window={start.isoformat()}..{end.isoformat()}"
        )
        for row in rows[:5]:
            print(
                f"{row['date']} {row['time']} | {row['incident_type']} | "
                f"{row['cfs_number']} | {row['details']}"
            )
        return 0, len(features), len(rows)

    source_document_id = ensure_source_document(
        source_type=dataset.source_type,
        source_sender=dataset.agency_name,
        source_subject=f"{dataset.label} {start.date()} to {end.date()}",
        source_received_at=end.isoformat(),
        filename=f"{dataset.key}-{end.strftime('%Y%m%dT%H%M%SZ')}.json",
        content_sha256=sha256_text(payload_text),
        storage_path=None,
        raw_text=None,
        extraction_method="arcgis_query",
        extraction_warnings=[],
    )
    ingestion_job_id = ensure_ingestion_job(source_document_id)
    if get_ingestion_job_status(source_document_id) == "published":
        logger.info("Skipping already published source document for %s", dataset.key)
        return 0, len(features), 0

    log_pipeline_event(
        ingestion_job_id,
        "extract",
        "ok",
        {"dataset": dataset.key, "feature_count": len(features), "days_back": days_back},
    )
    set_ingestion_job_status(ingestion_job_id, "parsed")

    conn = _connect_db()
    try:
        existing_keys = _load_existing_incident_keys(conn, dataset.county, rows)
        new_rows: list[dict[str, Any]] = []
        skipped_existing = 0
        for row in rows:
            row_keys = incident_keys(row, county=dataset.county)
            if row_keys and row_keys & existing_keys:
                skipped_existing += 1
                continue
            new_rows.append(row)
            existing_keys.update(row_keys)

        if not new_rows:
            log_pipeline_event(
                ingestion_job_id,
                "ingest",
                "ok",
                {"message": "duplicate-skip-all-incidents", "skipped_existing": skipped_existing},
            )
            set_ingestion_job_status(ingestion_job_id, "published", finished=True)
            return 0, len(features), 0

        source_label = f"{dataset.key}-{end.strftime('%Y%m%d%H%M')}"
        blotter_id = _insert_blotter(conn, dataset, new_rows, source_document_id, source_label)
        log_pipeline_event(
            ingestion_job_id,
            "ingest",
            "ok",
            {"blotter_id": blotter_id, "created_records": len(new_rows), "skipped_existing": skipped_existing},
        )
        set_ingestion_job_status(ingestion_job_id, "normalized")
    except Exception as exc:
        conn.rollback()
        increment_ingestion_retry(ingestion_job_id, str(exc))
        set_ingestion_job_status(ingestion_job_id, "failed", last_error=str(exc), finished=True)
        log_pipeline_event(
            ingestion_job_id,
            "ingest",
            "error",
            {"error": str(exc), "dataset": dataset.key},
        )
        raise
    finally:
        conn.close()

    post_count = _publish_blotter_outputs(
        blotter_id,
        ingestion_job_id=ingestion_job_id,
        label=dataset.label,
    )
    return blotter_id, len(features), post_count


def main() -> int:
    parser = argparse.ArgumentParser(description="Pull Bozeman police ArcGIS feeds into Montana Blotter")
    parser.add_argument(
        "--dataset",
        choices=("calls", "crime", "all"),
        default="all",
        help="Which Bozeman dataset to ingest",
    )
    parser.add_argument(
        "--days-back",
        type=int,
        default=None,
        help="Override days-back window for the selected dataset(s)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print normalized rows without DB writes")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    dataset_keys = list(DATASETS.keys()) if args.dataset == "all" else [args.dataset]
    for key in dataset_keys:
        dataset = DATASETS[key]
        days_back = args.days_back if args.days_back is not None else dataset.default_days_back
        blotter_id, fetched_count, post_count = ingest_dataset(dataset, days_back=days_back, dry_run=args.dry_run)
        print(
            f"{dataset.key}: fetched={fetched_count} blotter_id={blotter_id} "
            f"posts={post_count} days_back={days_back}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
