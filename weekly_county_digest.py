#!/usr/bin/env python3
"""
Publish one weekly county digest blog post from recent Montana Blotter records.

The script is safe to rerun: it uses a date-based slug and skips duplicates
unless --force is supplied.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, date, datetime, timedelta
import logging
import re
import sqlite3
from typing import Iterable, Optional

import config

AUTHOR = "Montana Blotter Data Desk"
DB_PATH = config.DB_PATH
LOG_PATH = "/root/montanablotter/weekly_county_digest.log"
WINDOW_DAYS = 7
MAX_TOP_COUNTIES = 5
MAX_TOP_INCIDENTS = 6
MAX_HIGHLIGHTS = 8
MIN_COUNTY_RECORDS = 3

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:80]


def _parse_date(raw_value: Optional[str]) -> Optional[date]:
    value = (raw_value or "").strip()
    if not value:
        return None

    formats = (
        "%m/%d/%Y",
        "%m/%d/%y",
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m/%d/%y %H:%M:%S",
    )
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _format_long_date(value: date) -> str:
    return f"{value.strftime('%B')} {value.day}, {value.year}"


def _build_in_clause(values: Iterable[str]) -> tuple[str, list[str]]:
    values_list = list(values)
    placeholders = ",".join("?" for _ in values_list)
    return placeholders, values_list


def _truncate(text: str, limit: int = 160) -> str:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _fetch_available_dates(conn: sqlite3.Connection) -> dict[date, set[str]]:
    date_map: dict[date, set[str]] = {}
    rows = conn.execute(
        """
        SELECT DISTINCT date
        FROM records
        WHERE date IS NOT NULL AND TRIM(date) != ''
        """
    ).fetchall()
    for row in rows:
        raw_value = row["date"]
        normalized = _parse_date(raw_value)
        if normalized is None:
            continue
        date_map.setdefault(normalized, set()).add(str(raw_value))
    return date_map


def _pick_window_end(conn: sqlite3.Connection, requested_date: Optional[str]) -> tuple[date, dict[date, set[str]]]:
    date_map = _fetch_available_dates(conn)
    if not date_map:
        raise RuntimeError("No incident dates found in records")

    if requested_date:
        parsed_requested = _parse_date(requested_date)
        if parsed_requested is None:
            raise RuntimeError(f"Could not parse requested date: {requested_date}")
        if parsed_requested not in date_map:
            raise RuntimeError(f"No records found for requested end date: {requested_date}")
        return parsed_requested, date_map

    latest_date = max(date_map)
    if latest_date < (datetime.now(UTC).date() - timedelta(days=14)):
        raise RuntimeError(
            f"Latest blotter content is too old for auto-publish: {_format_long_date(latest_date)}"
        )
    return latest_date, date_map


def _raw_dates_in_window(date_map: dict[date, set[str]], end_date: date) -> list[str]:
    floor_date = end_date - timedelta(days=WINDOW_DAYS - 1)
    raw_values: set[str] = set()
    for normalized, originals in date_map.items():
        if floor_date <= normalized <= end_date:
            raw_values.update(originals)
    return sorted(raw_values)


def _fetch_window_records(conn: sqlite3.Connection, raw_dates: list[str]) -> list[sqlite3.Row]:
    placeholders, params = _build_in_clause(raw_dates)
    return conn.execute(
        f"""
        SELECT
            id,
            date,
            COALESCE(NULLIF(county, ''), 'Unknown') AS county,
            COALESCE(NULLIF(location, ''), 'an unspecified location') AS location,
            COALESCE(NULLIF(incident_type, ''), NULLIF(incident, ''), 'Incident') AS incident_type,
            COALESCE(NULLIF(details, ''), NULLIF(summary, ''), '') AS details
        FROM records
        WHERE date IN ({placeholders})
        ORDER BY date DESC, county ASC, id DESC
        """,
        params,
    ).fetchall()


def _build_county_links(county_name: str) -> list[str]:
    slug = _slugify(county_name)
    links = [
        f"[View the {county_name} County page](/county/{slug})",
        f"[Check recent {county_name} arrests](/arrests?county={county_name.replace(' ', '%20')})",
        f"[Open the {county_name} County warrant guide](/warrants/{slug})",
    ]
    return links


def _select_highlights(records: list[sqlite3.Row]) -> list[str]:
    highlights: list[str] = []
    seen_keys: set[tuple[str, str, str]] = set()
    county_hits: Counter = Counter()

    for row in records:
        county = row["county"] or "Unknown"
        incident_type = row["incident_type"] or "Incident"
        location = row["location"] or "an unspecified location"
        if county_hits[county] >= 2:
            continue
        key = (county, incident_type, location)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        county_hits[county] += 1

        details = _truncate(row["details"], 140)
        if details:
            highlights.append(
                f"- **{county} County:** {incident_type} near {location}. Details noted: {details}"
            )
        else:
            highlights.append(
                f"- **{county} County:** {incident_type} near {location}."
            )
        if len(highlights) >= MAX_HIGHLIGHTS:
            break

    return highlights


def _build_markdown(
    start_date: date,
    end_date: date,
    records: list[sqlite3.Row],
) -> tuple[str, str, str]:
    county_counts = Counter(row["county"] for row in records)
    incident_counts = Counter(row["incident_type"] for row in records)

    top_counties = [
        (name, count)
        for name, count in county_counts.most_common()
        if count >= MIN_COUNTY_RECORDS
    ][:MAX_TOP_COUNTIES]
    top_incidents = incident_counts.most_common(MAX_TOP_INCIDENTS)
    highlights = _select_highlights(records)

    lead_counties = [name for name, _ in top_counties[:3]]
    title_counties = ", ".join(lead_counties[:-1]) + f", and {lead_counties[-1]}" if len(lead_counties) > 2 else " and ".join(lead_counties)
    title = (
        f"Montana Weekly Crime Roundup: {title_counties} Led Activity Through "
        f"{_format_long_date(end_date)}"
        if lead_counties
        else f"Montana Weekly Crime Roundup Through {_format_long_date(end_date)}"
    )

    excerpt = (
        f"Montana Blotter reviewed {len(records)} public safety records from "
        f"{_format_long_date(start_date)} through {_format_long_date(end_date)}. "
        f"{title_counties} generated the most visible county-level activity this week."
        if lead_counties
        else f"Montana Blotter reviewed {len(records)} public safety records from "
        f"{_format_long_date(start_date)} through {_format_long_date(end_date)}."
    )

    body_lines = [
        f"Montana Blotter reviewed **{len(records)} public safety records** logged between **{_format_long_date(start_date)}** and **{_format_long_date(end_date)}**.",
        "",
        "This weekly digest is designed to make the archive easier to scan. Instead of reading every report one by one, readers can see which counties were most active, which incident types appeared most often, and where to click next for county pages, arrest logs, and warrant resources.",
        "",
        "## Weekly Snapshot",
        "",
        f"- Total records reviewed: {len(records)}",
        f"- Counties with visible activity: {len(county_counts)}",
    ]

    if top_counties:
        body_lines.append(
            f"- Most active county: {top_counties[0][0]} County ({top_counties[0][1]} records)"
        )
    if top_incidents:
        incident_summary = ", ".join(
            f"{name} ({count})" for name, count in top_incidents[:3]
        )
        body_lines.append(f"- Most common incident types: {incident_summary}")

    body_lines.extend([
        "",
        "## Top Counties This Week",
        "",
    ])

    for index, (county_name, count) in enumerate(top_counties, start=1):
        body_lines.append(f"### {index}. {county_name} County")
        body_lines.append("")
        body_lines.append(
            f"{county_name} County accounted for **{count} record{'s' if count != 1 else ''}** during this seven-day window."
        )
        body_lines.append("")
        for link in _build_county_links(county_name):
            body_lines.append(f"- {link}")
        body_lines.append("")

    body_lines.extend([
        "## Incident Trends",
        "",
    ])
    for incident_name, count in top_incidents:
        body_lines.append(f"- **{incident_name}:** {count} records")

    body_lines.extend([
        "",
        "## Notable Records in the Weekly Window",
        "",
    ])
    body_lines.extend(highlights or ["- No distinct highlights were available in this weekly window."])

    body_lines.extend([
        "",
        "## How to Use This Digest",
        "",
        "- Start with the county pages if you want a stable archive view with local links and recent report history.",
        "- Use the arrest log when you want to narrow the archive to records that mention an arrest.",
        "- Use the warrant pages when you need court and sheriff resources for counties that publish warrant data online.",
        "",
        "For day-by-day monitoring, the homepage feed and morning briefing remain the fastest way to track new Montana blotter activity.",
    ])

    return title, excerpt, "\n".join(body_lines)


def publish_weekly_digest(requested_date: Optional[str], force: bool, draft: bool, stdout: bool) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    end_date, date_map = _pick_window_end(conn, requested_date)
    start_date = end_date - timedelta(days=WINDOW_DAYS - 1)
    raw_dates = _raw_dates_in_window(date_map, end_date)
    records = _fetch_window_records(conn, raw_dates)
    if not records:
        raise RuntimeError("No records found in the requested weekly window")

    title, excerpt, body = _build_markdown(start_date, end_date, records)
    slug = f"montana-weekly-county-digest-{end_date.isoformat()}"

    if stdout:
        print(title)
        print()
        print(excerpt)
        print()
        print(body)
        conn.close()
        return

    existing = conn.execute(
        "SELECT id FROM blog_posts WHERE slug = ?",
        (slug,),
    ).fetchone()

    published = 0 if draft else 1
    if existing and not force:
        logger.info("Weekly digest already exists for slug %s; skipping", slug)
        print(f"Weekly digest already exists: {slug}")
        conn.close()
        return

    if existing:
        conn.execute(
            """
            UPDATE blog_posts
            SET title = ?, body = ?, excerpt = ?, author = ?, published = ?, updated_at = datetime('now')
            WHERE slug = ?
            """,
            (title, body, excerpt, AUTHOR, published, slug),
        )
        action = "updated"
    else:
        conn.execute(
            """
            INSERT INTO blog_posts (title, slug, body, excerpt, author, published)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (title, slug, body, excerpt, AUTHOR, published),
        )
        action = "created"

    conn.commit()
    conn.close()
    logger.info("Weekly digest %s for %s", action, slug)
    print(f"Weekly digest {action}: {slug}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish a weekly county digest blog post.")
    parser.add_argument(
        "--date",
        help="Use a specific end date for the weekly window (YYYY-MM-DD or MM/DD/YY).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Update the existing weekly digest if the slug already exists.",
    )
    parser.add_argument(
        "--draft",
        action="store_true",
        help="Create the digest as an unpublished draft.",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print the generated markdown instead of writing to the database.",
    )
    args = parser.parse_args()

    publish_weekly_digest(
        requested_date=args.date,
        force=args.force,
        draft=args.draft,
        stdout=args.stdout,
    )


if __name__ == "__main__":
    main()
