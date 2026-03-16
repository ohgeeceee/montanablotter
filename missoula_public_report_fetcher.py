"""
missoula_public_report_fetcher.py
=================================
Daily puller for Missoula City-County Daily Public Report.
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

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

DEFAULT_URL = "https://webapps.missoulacounty.us/dailypublicreport/"
COUNTY = "Missoula"
CITY = "Missoula"
SOURCE_TYPE = "missoula_public_report"
SOURCE_SENDER = "Missoula City-County Daily Public Report"

RECORD_BLOCK_RE = re.compile(
    r'<tr id="rptrDefault_ctl\d+_header" class="RecordTitle">(?P<header>.*?)</tr>\s*'
    r'<tr id="rptrDefault_ctl\d+_address">(?P<address>.*?)</tr>\s*'
    r'<tr id="rptrDefault_ctl\d+_units">(?P<units>.*?)</tr>',
    re.IGNORECASE | re.DOTALL,
)
HEADER_META_RE = re.compile(
    r'<div class="CFSNumber">\s*(?P<incident_type>.*?)\s*'
    r'<span class="agency">\s*-\s*(?P<agency>[^<]+)</span>\s*<br\s*/?>\s*'
    r'<span>\((?P<cfs_number>[^)]+)\)</span>',
    re.IGNORECASE | re.DOTALL,
)
DATE_STAMP_RE = re.compile(
    r'<span class="IncidentDateStamp">\s*(?P<date>[^<]+?)\s*<br\s*/?>\s*(?P<time>[^<]+?)\s*</span>',
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class IngestStats:
    fetched_incidents: int = 0
    deduped_incidents: int = 0
    skipped_existing: int = 0
    created_blotter: int = 0
    created_posts: int = 0
    skipped_published_source: int = 0


def _should_retry_missoula_response(exc: requests.exceptions.RequestException) -> bool:
    response = getattr(exc, "response", None)
    if response is None:
        return True
    status_code = getattr(response, "status_code", 0) or 0
    return status_code >= 500 or status_code == 429


def _fetch_report_html(
    session: requests.Session,
    url: str,
    *,
    timeout: int = 45,
    max_attempts: int = 3,
    retry_delay_seconds: float = 2.0,
) -> str:
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            return response.text
        except requests.exceptions.RequestException as exc:
            last_error = exc
            should_retry = attempt < max_attempts and _should_retry_missoula_response(exc)
            if not should_retry:
                raise
            logger.warning(
                "Missoula fetch attempt %s/%s failed: %s",
                attempt,
                max_attempts,
                exc,
            )
            time.sleep(retry_delay_seconds * attempt)

    if last_error is not None:
        raise last_error
    raise RuntimeError("Missoula fetch failed without an exception")


def _connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=float(getattr(config, "DB_TIMEOUT_SECONDS", 30)))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {int(getattr(config, 'DB_BUSY_TIMEOUT_MS', 30000))}")
    return conn


def _table_has_column(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row[1] == column_name for row in rows)


def _strip_html(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment or "")
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _parse_report_date(page_html: str, fallback_rows: list[dict]) -> str:
    selected_option = re.search(
        r'<option[^>]*selected="selected"[^>]*value="(?P<value>[^"]+)"',
        page_html,
        re.IGNORECASE,
    )
    if selected_option:
        return selected_option.group("value").strip()
    if fallback_rows:
        dt = fallback_rows[0].get("date", "")
        if dt:
            return dt
    return datetime.now(timezone.utc).strftime("%m/%d/%Y")


def _parse_incidents(page_html: str) -> list[dict]:
    incidents: list[dict] = []

    for match in RECORD_BLOCK_RE.finditer(page_html):
        header_html = match.group("header")
        address_html = match.group("address")
        units_html = match.group("units")

        header_meta = HEADER_META_RE.search(header_html)
        date_meta = DATE_STAMP_RE.search(header_html)
        if not header_meta or not date_meta:
            continue

        raw_incident_type = _strip_html(header_meta.group("incident_type"))
        raw_agency = _strip_html(header_meta.group("agency"))
        cfs_number = _strip_html(header_meta.group("cfs_number"))
        raw_date = _strip_html(date_meta.group("date"))
        raw_time = _strip_html(date_meta.group("time"))
        location = _strip_html(address_html)
        units_line = _strip_html(units_html)
        units_line = re.sub(r"^Responding Unit\(s\):\s*", "", units_line, flags=re.IGNORECASE)

        try:
            date_value = datetime.strptime(raw_date, "%m/%d/%Y").strftime("%m/%d/%y")
        except ValueError:
            date_value = raw_date

        incident_type = raw_incident_type or "Public Report Incident"
        agency = raw_agency or "Missoula Public Safety"
        details = (
            f"Agency: {agency}; "
            f"Call Type: {incident_type}; "
            f"Units: {units_line or 'N/A'}"
        )

        incidents.append(
            {
                "cfs_number": cfs_number,
                "date": date_value,
                "time": raw_time,
                "incident_type": incident_type,
                "incident": incident_type,
                "location": location or f"{CITY}, MT",
                "details": details,
                "county": COUNTY,
                "officer": "",
                "agency_name": agency,
            }
        )

    return incidents


def _load_existing_incident_keys(
    conn: sqlite3.Connection,
    county: str,
    rows: list[dict],
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


def _ingest_rows(
    incidents: list[dict],
    report_date: str,
    source_document_id: int,
    ingestion_job_id: int,
) -> tuple[int, int]:
    conn = _connect_db()
    cursor = conn.cursor()

    try:
        existing_keys = _load_existing_incident_keys(conn, COUNTY, incidents)
        new_rows: list[dict] = []
        skipped_existing = 0
        for incident in incidents:
            keys = incident_keys(incident, county=COUNTY)
            if keys and keys & existing_keys:
                skipped_existing += 1
                continue
            new_rows.append(incident)
            existing_keys.update(keys)

        if not new_rows:
            log_pipeline_event(
                ingestion_job_id,
                "ingest",
                "ok",
                {"message": "duplicate-skip-all-incidents", "skipped_existing": skipped_existing},
            )
            set_ingestion_job_status(ingestion_job_id, "published", finished=True)
            conn.close()
            return 0, skipped_existing

        has_source_type = _table_has_column(conn, "blotters", "source_type")
        has_source_doc = _table_has_column(conn, "blotters", "source_document_id")
        filename = f"missoula-daily-public-report-{report_date.replace('/', '-')}"

        if has_source_type and has_source_doc:
            cursor.execute(
                "INSERT INTO blotters (filename, county, incident_count, source_type, source_document_id) VALUES (?, ?, ?, ?, ?)",
                (filename, COUNTY, len(new_rows), SOURCE_TYPE, source_document_id),
            )
        elif has_source_type:
            cursor.execute(
                "INSERT INTO blotters (filename, county, incident_count, source_type) VALUES (?, ?, ?, ?)",
                (filename, COUNTY, len(new_rows), SOURCE_TYPE),
            )
        elif has_source_doc:
            cursor.execute(
                "INSERT INTO blotters (filename, county, incident_count, source_document_id) VALUES (?, ?, ?, ?)",
                (filename, COUNTY, len(new_rows), source_document_id),
            )
        else:
            cursor.execute(
                "INSERT INTO blotters (filename, county, incident_count) VALUES (?, ?, ?)",
                (filename, COUNTY, len(new_rows)),
            )
        if cursor.lastrowid is None:
            raise RuntimeError("Failed to insert missoula blotter row")
        blotter_id = int(cursor.lastrowid)

        for incident in new_rows:
            cursor.execute(
                """
                INSERT INTO records
                    (blotter_id, cfs_number, date, time, incident_type,
                     incident, location, details, county, officer)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    blotter_id,
                    incident["cfs_number"],
                    incident["date"],
                    incident["time"],
                    incident["incident_type"],
                    incident["incident"],
                    incident["location"],
                    incident["details"],
                    COUNTY,
                    incident["officer"],
                ),
            )

        conn.commit()
        conn.close()

        log_pipeline_event(
            ingestion_job_id,
            "normalize",
            "ok",
            {
                "blotter_id": blotter_id,
                "incident_count": len(new_rows),
                "duplicate_incidents_skipped": skipped_existing,
            },
        )
        set_ingestion_job_status(ingestion_job_id, "normalized")
        return blotter_id, skipped_existing
    except Exception:
        conn.rollback()
        conn.close()
        raise


def ingest_missoula_public_report(url: str, dry_run: bool = False) -> tuple[int, IngestStats]:
    stats = IngestStats()
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://montanablotter.com)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )

    logger.info("Fetching Missoula public report: %s", url)
    page_html = _fetch_report_html(session, url)

    incidents = _parse_incidents(page_html)
    stats.fetched_incidents = len(incidents)
    if not incidents:
        logger.info("No incidents parsed from Missoula report")
        return 0, stats

    report_date = _parse_report_date(page_html, incidents)
    if dry_run:
        logger.info("Dry run: fetched %s incidents for %s", len(incidents), report_date)
        for row in incidents[:10]:
            print(
                f"{row['date']} {row['time']} | {row['incident_type']} | "
                f"{row['location']} | {row['cfs_number']} | {row['agency_name']}"
            )
        return 0, stats

    source_payload = {
        "source": "missoula_daily_public_report",
        "report_date": report_date,
        "incident_count": len(incidents),
        "incidents": incidents,
    }
    serialised_payload = json.dumps(source_payload, sort_keys=True, ensure_ascii=True)
    content_hash = sha256_text(serialised_payload)

    source_document_id = ensure_source_document(
        source_type=SOURCE_TYPE,
        source_sender=SOURCE_SENDER,
        source_subject=f"Missoula Daily Public Report {report_date}",
        source_received_at=datetime.now(timezone.utc).isoformat(),
        filename=None,
        content_sha256=content_hash,
        storage_path=url,
        raw_text=serialised_payload,
        extraction_method="html_scrape",
        extraction_warnings=[],
    )
    ingestion_job_id = ensure_ingestion_job(source_document_id)
    existing_status = get_ingestion_job_status(source_document_id)
    if existing_status == "published":
        stats.skipped_published_source = 1
        logger.info("Missoula report already published for source doc #%s", source_document_id)
        return 0, stats

    log_pipeline_event(
        ingestion_job_id,
        "extract",
        "ok",
        {"url": url, "report_date": report_date, "incident_count": len(incidents)},
    )
    set_ingestion_job_status(ingestion_job_id, "extracted")
    set_ingestion_job_status(ingestion_job_id, "parsed")

    try:
        blotter_id, skipped_existing = _ingest_rows(
            incidents=incidents,
            report_date=report_date,
            source_document_id=source_document_id,
            ingestion_job_id=ingestion_job_id,
        )
        stats.skipped_existing = skipped_existing
        if not blotter_id:
            return 0, stats

        stats.created_blotter = blotter_id
        stats.deduped_incidents = len(incidents) - skipped_existing
        post_count = _publish_blotter_outputs(
            blotter_id,
            ingestion_job_id=ingestion_job_id,
            label="missoula report",
        )
        stats.created_posts = post_count
        return blotter_id, stats
    except Exception as exc:
        increment_ingestion_retry(ingestion_job_id, str(exc))
        set_ingestion_job_status(ingestion_job_id, "failed", last_error=str(exc), finished=True)
        log_pipeline_event(
            ingestion_job_id,
            "publish",
            "error",
            {"error": str(exc)},
        )
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Pull Missoula Daily Public Report into Montana Blotter")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"Report URL (default: {DEFAULT_URL})")
    parser.add_argument("--dry-run", action="store_true", help="Parse and print sample incidents without DB writes")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    blotter_id, stats = ingest_missoula_public_report(args.url, dry_run=args.dry_run)
    print(
        "Missoula fetch complete "
        f"(fetched={stats.fetched_incidents}, deduped={stats.deduped_incidents}, "
        f"skipped_existing={stats.skipped_existing}, created_blotter={blotter_id}, "
        f"created_posts={stats.created_posts}, skipped_published_source={stats.skipped_published_source})"
    )


if __name__ == "__main__":
    main()
