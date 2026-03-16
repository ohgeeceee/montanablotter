"""
jail_booking_ingest.py
======================

Fetch and sync current jail roster entries into the Montana Blotter
jail-bookings tables.

This first pass automates Missoula County, where the current inmate roster is
publicly accessible as server-rendered HTML. Other large counties are kept in
the source registry and reported as unsupported or temporarily unavailable
until county-specific adapters are added.
"""

from __future__ import annotations

import argparse
import html
import logging
import re
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Iterable

import requests

import config

logger = logging.getLogger(__name__)

DB_TIMEOUT_SECONDS = float(getattr(config, "DB_TIMEOUT_SECONDS", 30))
DB_BUSY_TIMEOUT_MS = int(getattr(config, "DB_BUSY_TIMEOUT_MS", 30000))
MISSOULA_CHARGE_LOOKBACK_DAYS = int(getattr(config, "MISSOULA_CHARGE_LOOKBACK_DAYS", 30))

SUPPORTED_ADAPTERS = {"missoula", "yellowstone"}
TEMPORARILY_UNAVAILABLE = {"gallatin"}
TRACKED_SOURCES = {
    "yellowstone": {
        "county_name": "Yellowstone",
        "facility_name": "Yellowstone County Detention Facility",
        "roster_url": "https://www.yellowstonecountymt.gov/sheriff/detention/dcsearch.asp",
        "phone": "406-256-2929",
        "coverage_tier": "major",
        "is_featured": 1,
    },
    "missoula": {
        "county_name": "Missoula",
        "facility_name": "Missoula County Detention Facility",
        "roster_url": "https://webapps.missoulacounty.us/jailroster/Inmates",
        "phone": "406-258-4780",
        "coverage_tier": "major",
        "is_featured": 1,
    },
    "gallatin": {
        "county_name": "Gallatin",
        "facility_name": "Gallatin County Detention Center",
        "roster_url": "https://gallatin-so-mt.zuercherportal.com/#/inmates",
        "phone": "406-582-2100",
        "coverage_tier": "major",
        "is_featured": 1,
    },
    "flathead": {
        "county_name": "Flathead",
        "facility_name": "Flathead County Detention Center",
        "roster_url": "https://apps.flathead.mt.gov/jailroster/",
        "phone": "406-758-5610",
        "coverage_tier": "major",
        "is_featured": 1,
    },
    "cascade": {
        "county_name": "Cascade",
        "facility_name": "Cascade County Detention Center",
        "roster_url": "https://www.cascadecountymt.gov/314/Inmate-Roster",
        "phone": "406-454-6840",
        "coverage_tier": "major",
        "is_featured": 1,
    },
}


@dataclass(frozen=True)
class JailBookingRecord:
    source_record_id: str
    person_name: str
    age: int | None
    booking_number: str
    booking_at: str | None
    charges_summary: str
    source_url: str | None = None


@dataclass
class SyncStats:
    fetched_count: int = 0
    new_count: int = 0
    updated_count: int = 0
    missing_count: int = 0


class _RosterTextExtractor(HTMLParser):
    """Convert HTML into a newline-friendly plain-text stream."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"br", "hr"}:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"p", "div", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if data:
            self._parts.append(data)

    def text(self) -> str:
        text = html.unescape("".join(self._parts))
        text = text.replace("\xa0", " ")
        text = re.sub(r"\r", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text


def _connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=DB_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {DB_BUSY_TIMEOUT_MS}")
    return conn


def _ensure_tracked_sources(conn: sqlite3.Connection) -> None:
    for county_slug, meta in TRACKED_SOURCES.items():
        conn.execute(
            '''
            INSERT OR IGNORE INTO jail_booking_sources (
                county_slug,
                county_name,
                facility_name,
                roster_url,
                phone,
                source_type,
                coverage_tier,
                is_enabled,
                is_featured,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, 'official_roster', ?, 1, ?, datetime('now'), datetime('now'))
            ''',
            (
                county_slug,
                meta["county_name"],
                meta["facility_name"],
                meta["roster_url"],
                meta["phone"],
                meta["coverage_tier"],
                meta["is_featured"],
            ),
        )
    conn.commit()


def _fetch_html(url: str, *, timeout: int = 45) -> str:
    response = requests.get(
        url,
        timeout=timeout,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://montanablotter.com)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    response.raise_for_status()
    return response.text


def _text_from_html(fragment: str) -> str:
    parser = _RosterTextExtractor()
    parser.feed(fragment or "")
    text = parser.text()
    return re.sub(r"\s+", " ", text).strip()


def _normalize_datetime(value: str) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    candidates = (
        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%y %I:%M:%S %p",
        "%m/%d/%y %I:%M %p",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    )
    for fmt in candidates:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return None


def _extract_text_lines(page_html: str) -> list[str]:
    parser = _RosterTextExtractor()
    parser.feed(page_html)
    plain_text = parser.text()
    lines = [re.sub(r"\s+", " ", line).strip() for line in plain_text.splitlines()]
    return [line for line in lines if line]


def _extract_hidden_form_fields(page_html: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in re.finditer(r"<input\b[^>]*>", page_html, re.IGNORECASE):
        tag = match.group(0)
        if not re.search(r'type="hidden"', tag, re.IGNORECASE):
            continue
        name_match = re.search(r'name="([^"]+)"', tag, re.IGNORECASE)
        if not name_match:
            continue
        value_match = re.search(r'value="([^"]*)"', tag, re.IGNORECASE)
        fields[html.unescape(name_match.group(1))] = html.unescape(value_match.group(1)) if value_match else ""
    return fields


def _extract_missoula_charge_targets(page_html: str) -> list[str]:
    matches = re.findall(
        r"__doPostBack\(&#39;(ctl00\$MainContent\$ParentRepeater\$ctl\d+\$lnkCharges)&#39;,\s*&#39;&#39;\)",
        page_html,
        re.IGNORECASE,
    )
    seen: set[str] = set()
    targets: list[str] = []
    for match in matches:
        if match in seen:
            continue
        seen.add(match)
        targets.append(match)
    return targets


def _is_name_line(line: str) -> bool:
    if "," not in line:
        return False
    if line.upper() != line:
        return False
    return bool(re.match(r"^[A-Z' .-]+,\s*[A-Z' .-]+$", line))


def _parse_missoula_lines(lines: list[str], source_url: str) -> list[JailBookingRecord]:
    records: list[JailBookingRecord] = []
    in_list = False
    idx = 0

    while idx < len(lines):
        line = lines[idx]
        if line.startswith("Current Inmate List for Today:"):
            in_list = True
            idx += 1
            continue
        if in_list and line.startswith("© "):
            break
        if not in_list:
            idx += 1
            continue
        if line in {
            "For details on an inmates charges or court schedule, please use the buttons to access that information.",
            "Name Age Booking ID Global/Jacket No Booking Date Charge Details",
        }:
            idx += 1
            continue
        if not _is_name_line(line):
            idx += 1
            continue

        name = line.title()
        age_line = lines[idx + 1] if idx + 1 < len(lines) else ""
        booking_id_line = lines[idx + 2] if idx + 2 < len(lines) else ""
        jacket_line = lines[idx + 3] if idx + 3 < len(lines) else ""
        booking_date_line = lines[idx + 4] if idx + 4 < len(lines) else ""
        charges_line = lines[idx + 5] if idx + 5 < len(lines) else ""
        if (
            re.fullmatch(r"\d{1,3}", age_line)
            and re.fullmatch(r"\d{4}-\d{8}", booking_id_line)
            and re.fullmatch(r"\d+", jacket_line)
            and _normalize_datetime(booking_date_line)
            and charges_line == "Charges"
        ):
            records.append(
                JailBookingRecord(
                    source_record_id=booking_id_line,
                    person_name=name,
                    age=int(age_line),
                    booking_number=booking_id_line,
                    booking_at=_normalize_datetime(booking_date_line),
                    charges_summary="Charge details available on the official Missoula County inmate portal.",
                    source_url=source_url,
                )
            )
            idx += 6
            continue

        meta_line = lines[idx + 1] if idx + 1 < len(lines) else ""
        match = re.match(
            r"^(?P<age>\d{1,3})\s+"
            r"(?P<booking_id>\d{4}-\d{8})\s+"
            r"(?P<jacket>\d+)\s+"
            r"(?P<booking_date>\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M)"
            r"(?:\s+Charges)?$",
            meta_line,
        )
        if not match:
            idx += 1
            continue

        booking_number = match.group("booking_id")
        records.append(
            JailBookingRecord(
                source_record_id=booking_number,
                person_name=name,
                age=int(match.group("age")),
                booking_number=booking_number,
                booking_at=_normalize_datetime(match.group("booking_date")),
                charges_summary="Charge details available on the official Missoula County inmate portal.",
                source_url=source_url,
            )
        )
        idx += 2

    return records


def _parse_missoula_charges(page_html: str) -> str:
    table_match = re.search(
        r'<table class="table table-bordered table-striped">\s*<tr class="ChargeRecordHeaderTopRow">(.*?)</table>',
        page_html,
        re.IGNORECASE | re.DOTALL,
    )
    if not table_match:
        return "Charge details available on the official Missoula County inmate portal."

    summaries: list[str] = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", table_match.group(0), re.IGNORECASE | re.DOTALL):
        cell_matches = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.IGNORECASE | re.DOTALL)
        if len(cell_matches) != 6:
            continue
        charge_text = _text_from_html(cell_matches[0])
        crime_type = _text_from_html(cell_matches[1])
        agency = _text_from_html(cell_matches[2]).replace(" /", "/").replace("/ ", "/")
        bond = _text_from_html(cell_matches[3])
        cash_surety = _text_from_html(cell_matches[4])
        parts = [charge_text]
        if crime_type:
            parts.append(crime_type)
        if agency:
            parts.append(agency)
        if bond:
            parts.append(f"Bond {bond}")
        if cash_surety:
            parts.append(cash_surety)
        summary = " | ".join(part for part in parts if part)
        if summary and not summary.startswith("Charge(s)"):
            summaries.append(summary)

    if not summaries:
        return "Charge details available on the official Missoula County inmate portal."
    return "; ".join(summaries[:3])


def _should_fetch_missoula_charge_detail(record: JailBookingRecord) -> bool:
    if not record.booking_at:
        return True
    try:
        booked_at = datetime.strptime(record.booking_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return True
    cutoff = datetime.now() - timedelta(days=MISSOULA_CHARGE_LOOKBACK_DAYS)
    return booked_at >= cutoff


def fetch_missoula_bookings(source_url: str) -> list[JailBookingRecord]:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://montanablotter.com)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": source_url,
        }
    )
    response = session.get(source_url, timeout=45)
    response.raise_for_status()
    page_html = response.text

    payload = _extract_hidden_form_fields(page_html)
    if "__VIEWSTATE" in payload:
        payload.update(
            {
                "__EVENTTARGET": "ctl00$MainContent$li9",
                "__EVENTARGUMENT": "",
                "__LASTFOCUS": "",
            }
        )
        all_response = session.post(source_url, data=payload, timeout=45)
        all_response.raise_for_status()
        all_html = all_response.text
        all_lines = _extract_text_lines(all_html)
        records = _parse_missoula_lines(all_lines, source_url)
        if records:
            charge_targets = _extract_missoula_charge_targets(all_html)
            if len(charge_targets) == len(records):
                detail_payload = _extract_hidden_form_fields(all_html)
                enriched_records: list[JailBookingRecord] = []
                for record, target in zip(records, charge_targets):
                    if not _should_fetch_missoula_charge_detail(record):
                        enriched_records.append(record)
                        continue
                    per_record_payload = dict(detail_payload)
                    per_record_payload.update(
                        {
                            "__EVENTTARGET": target,
                            "__EVENTARGUMENT": "",
                            "__LASTFOCUS": "",
                        }
                    )
                    try:
                        detail_response = session.post(source_url, data=per_record_payload, timeout=45)
                        detail_response.raise_for_status()
                        enriched_records.append(
                            replace(
                                record,
                                charges_summary=_parse_missoula_charges(detail_response.text),
                                source_url=detail_response.url or source_url,
                            )
                        )
                    except requests.RequestException:
                        logger.warning("Missoula charge lookup failed for %s", record.booking_number)
                        enriched_records.append(record)
                return enriched_records
            return records

    lines = _extract_text_lines(page_html)
    return _parse_missoula_lines(lines, source_url)


def _solve_yellowstone_prompt(page_html: str) -> str:
    match = re.search(r'<label for="Answer"[^>]*>([^<]+)</label>', page_html)
    if not match:
        raise RuntimeError("Yellowstone verification prompt not found")
    label = _text_from_html(match.group(1))
    expr = label.split("=")[0].strip()
    parts = expr.split()
    if len(parts) != 3:
        raise RuntimeError(f"Unexpected Yellowstone prompt: {label}")

    words = {
        "zero": 0,
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "eleven": 11,
        "twelve": 12,
    }

    def parse_value(raw: str) -> int:
        token = raw.strip()
        if token.isdigit():
            return int(token)
        lowered = token.lower()
        if lowered not in words:
            raise RuntimeError(f"Unsupported Yellowstone prompt token: {token}")
        return words[lowered]

    left = parse_value(parts[0])
    op = parts[1]
    right = parse_value(parts[2])
    if op == "+":
        return str(left + right)
    if op == "-":
        return str(left - right)
    raise RuntimeError(f"Unsupported Yellowstone operator: {op}")


def _parse_yellowstone_roster(page_html: str, base_url: str) -> list[dict[str, str | None]]:
    table_match = re.search(
        r'<table class="table table-striped _table-sm caption-top data-table">(.*?)</table>',
        page_html,
        re.IGNORECASE | re.DOTALL,
    )
    if not table_match:
        raise RuntimeError("Yellowstone roster table not found")

    rows: list[dict[str, str | None]] = []
    for row_html in re.findall(r"<tr>(.*?)</tr>", table_match.group(1), re.IGNORECASE | re.DOTALL):
        cell_matches = re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.IGNORECASE | re.DOTALL)
        if len(cell_matches) != 8:
            continue
        detail_match = re.search(r'href="([^"]*inmatedet\.asp\?[^"]+)"', cell_matches[0], re.IGNORECASE)
        rows.append(
            {
                "last_name": _text_from_html(cell_matches[0]),
                "first_name": _text_from_html(cell_matches[1]),
                "middle_name": _text_from_html(cell_matches[2]),
                "jacket_number": _text_from_html(cell_matches[3]),
                "housing_unit": _text_from_html(cell_matches[4]),
                "total_bond": _text_from_html(cell_matches[5]),
                "booking_date": _text_from_html(cell_matches[6]),
                "date_of_birth": _text_from_html(cell_matches[7]),
                "detail_url": f"{base_url}/{detail_match.group(1)}" if detail_match else None,
            }
        )
    return rows


def _parse_yellowstone_charges(page_html: str) -> str:
    table_match = re.search(
        r'<table class="table table-striped text-center data-table">(.*?)</table>',
        page_html,
        re.IGNORECASE | re.DOTALL,
    )
    if not table_match:
        return "Charge details available on the official Yellowstone County inmate page."

    summaries: list[str] = []
    for row_html in re.findall(r"<tr>(.*?)</tr>", table_match.group(1), re.IGNORECASE | re.DOTALL):
        cell_matches = re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.IGNORECASE | re.DOTALL)
        if len(cell_matches) != 5:
            continue
        charge_type = _text_from_html(cell_matches[2])
        charge = _text_from_html(cell_matches[3])
        bond_amount = _text_from_html(cell_matches[4])
        parts = [charge]
        if charge_type:
            parts.append(charge_type)
        if bond_amount:
            parts.append(f"Bond {bond_amount}")
        summaries.append(" | ".join([part for part in parts if part]))

    if not summaries:
        return "Charge details available on the official Yellowstone County inmate page."
    return "; ".join(summaries[:3])


def fetch_yellowstone_bookings(source_url: str) -> list[JailBookingRecord]:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://montanablotter.com)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": source_url,
        }
    )
    page_html = session.get(source_url, timeout=45).text
    answer = _solve_yellowstone_prompt(page_html)
    response = session.post(
        source_url,
        data={
            "ViewFullRoster": "True",
            "Answer": answer,
            "action": "Search",
        },
        timeout=45,
    )
    response.raise_for_status()
    roster_rows = _parse_yellowstone_roster(response.text, "https://www.yellowstonecountymt.gov/Sheriff/Detention")

    records: list[JailBookingRecord] = []
    for row in roster_rows:
        detail_url = row["detail_url"]
        charge_summary = "Charge details available on the official Yellowstone County inmate page."
        if detail_url:
            detail_html = session.get(detail_url, timeout=45).text
            charge_summary = _parse_yellowstone_charges(detail_html)
        person_name = ", ".join(
            [
                row["last_name"].title(),
                " ".join(part.title() for part in [row["first_name"], row["middle_name"]] if part),
            ]
        )
        records.append(
            JailBookingRecord(
                source_record_id=(detail_url or row["jacket_number"] or "").strip(),
                person_name=person_name.strip(),
                age=None,
                booking_number=(detail_url or "").split("Booknum=")[-1].split("&")[0] if detail_url and "Booknum=" in detail_url else row["jacket_number"],
                booking_at=_normalize_datetime(f"{row['booking_date']} 12:00 AM"),
                charges_summary=charge_summary,
                source_url=detail_url or source_url,
            )
        )
    return records


def _record_run(
    conn: sqlite3.Connection,
    *,
    source_id: int,
    run_type: str,
    status: str,
    fetched_count: int = 0,
    new_count: int = 0,
    updated_count: int = 0,
    missing_count: int = 0,
    notes: str = "",
) -> None:
    conn.execute(
        '''
        INSERT INTO jail_booking_runs (
            source_id,
            run_type,
            status,
            fetched_count,
            new_count,
            updated_count,
            missing_count,
            started_at,
            completed_at,
            notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'), ?)
        ''',
        (
            source_id,
            run_type,
            status,
            fetched_count,
            new_count,
            updated_count,
            missing_count,
            notes[:1000],
        ),
    )


def _mark_source_checked(
    conn: sqlite3.Connection,
    source_id: int,
    *,
    success: bool,
    notes: str = "",
) -> None:
    if success:
        conn.execute(
            '''
            UPDATE jail_booking_sources
            SET last_checked_at = datetime('now'),
                last_success_at = datetime('now'),
                notes = CASE WHEN ? != '' THEN ? ELSE notes END
            WHERE id = ?
            ''',
            (notes[:1000], notes[:1000], source_id),
        )
    else:
        conn.execute(
            '''
            UPDATE jail_booking_sources
            SET last_checked_at = datetime('now'),
                notes = CASE WHEN ? != '' THEN ? ELSE notes END
            WHERE id = ?
            ''',
            (notes[:1000], notes[:1000], source_id),
        )


def _sync_records(
    conn: sqlite3.Connection,
    source: sqlite3.Row,
    records: Iterable[JailBookingRecord],
    *,
    dry_run: bool = False,
) -> SyncStats:
    now_sql = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    stats = SyncStats()
    payload = list(records)
    stats.fetched_count = len(payload)

    existing_rows = conn.execute(
        '''
        SELECT id, source_record_id, person_name, booking_at, charges_summary, is_current
        FROM jail_bookings
        WHERE source_id = ?
        ''',
        (source["id"],),
    ).fetchall()
    existing_by_key = {row["source_record_id"]: row for row in existing_rows if row["source_record_id"]}
    seen_ids: set[str] = set()

    for record in payload:
        seen_ids.add(record.source_record_id)
        current = existing_by_key.get(record.source_record_id)
        if current is None:
            stats.new_count += 1
            if dry_run:
                continue
            conn.execute(
                '''
                INSERT INTO jail_bookings (
                    source_id,
                    county_slug,
                    county_name,
                    facility_name,
                    person_name,
                    age,
                    booking_number,
                    booking_at,
                    charges_summary,
                    source_url,
                    source_record_id,
                    booking_status,
                    is_current,
                    first_seen_at,
                    last_seen_at,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'current', 1, datetime('now'), datetime('now'), datetime('now'), datetime('now'))
                ''',
                (
                    source["id"],
                    source["county_slug"],
                    source["county_name"],
                    source["facility_name"],
                    record.person_name,
                    record.age,
                    record.booking_number,
                    record.booking_at,
                    record.charges_summary,
                    record.source_url or source["roster_url"],
                    record.source_record_id,
                ),
            )
            continue

        changed = any(
            [
                (current["person_name"] or "") != record.person_name,
                (current["booking_at"] or "") != (record.booking_at or ""),
                (current["charges_summary"] or "") != record.charges_summary,
                int(current["is_current"] or 0) != 1,
            ]
        )
        if changed:
            stats.updated_count += 1
        if dry_run:
            continue
        conn.execute(
            '''
            UPDATE jail_bookings
            SET person_name = ?,
                age = ?,
                booking_number = ?,
                booking_at = ?,
                charges_summary = ?,
                source_url = ?,
                booking_status = 'current',
                is_current = 1,
                release_at = NULL,
                last_seen_at = ?,
                updated_at = ?
            WHERE id = ?
            ''',
            (
                record.person_name,
                record.age,
                record.booking_number,
                record.booking_at,
                record.charges_summary,
                record.source_url or source["roster_url"],
                now_sql,
                now_sql,
                current["id"],
            ),
        )

    for row in existing_rows:
        if row["source_record_id"] in seen_ids or int(row["is_current"] or 0) == 0:
            continue
        stats.missing_count += 1
        if dry_run:
            continue
        conn.execute(
            '''
            UPDATE jail_bookings
            SET booking_status = 'released',
                is_current = 0,
                release_at = COALESCE(release_at, ?),
                last_seen_at = ?,
                updated_at = ?
            WHERE id = ?
            ''',
            (now_sql, now_sql, now_sql, row["id"]),
        )

    return stats


def _run_source(conn: sqlite3.Connection, source: sqlite3.Row, *, dry_run: bool = False) -> SyncStats:
    county_slug = source["county_slug"]
    roster_url = (source["roster_url"] or "").strip()
    if county_slug in TEMPORARILY_UNAVAILABLE:
        note = "Official roster portal is currently unavailable or in maintenance mode."
        _record_run(conn, source_id=source["id"], run_type="scheduled", status="skipped", notes=note)
        _mark_source_checked(conn, source["id"], success=False, notes=note)
        return SyncStats()
    if county_slug not in SUPPORTED_ADAPTERS:
        note = "No automated county adapter has been added yet."
        _record_run(conn, source_id=source["id"], run_type="scheduled", status="skipped", notes=note)
        _mark_source_checked(conn, source["id"], success=False, notes=note)
        return SyncStats()

    if county_slug == "missoula":
        records = fetch_missoula_bookings(roster_url)
    elif county_slug == "yellowstone":
        records = fetch_yellowstone_bookings(roster_url)
    else:
        raise RuntimeError(f"No adapter for county slug: {county_slug}")

    stats = _sync_records(conn, source, records, dry_run=dry_run)
    note = f"Fetched {stats.fetched_count} records from {source['county_name']}."
    _record_run(
        conn,
        source_id=source["id"],
        run_type="scheduled" if not dry_run else "dry_run",
        status="success",
        fetched_count=stats.fetched_count,
        new_count=stats.new_count,
        updated_count=stats.updated_count,
        missing_count=stats.missing_count,
        notes=note,
    )
    _mark_source_checked(conn, source["id"], success=True, notes=note)
    return stats


def ingest_jail_bookings(*, county_slug: str = "", dry_run: bool = False) -> dict[str, SyncStats]:
    conn = _connect_db()
    try:
        _ensure_tracked_sources(conn)
        if county_slug:
            sources = conn.execute(
                '''
                SELECT *
                FROM jail_booking_sources
                WHERE county_slug = ? AND COALESCE(is_enabled, 1) = 1
                ORDER BY county_name ASC
                ''',
                (county_slug,),
            ).fetchall()
        else:
            sources = conn.execute(
                '''
                SELECT *
                FROM jail_booking_sources
                WHERE COALESCE(is_enabled, 1) = 1
                ORDER BY COALESCE(is_featured, 0) DESC, county_name ASC
                '''
            ).fetchall()

        results: dict[str, SyncStats] = {}
        for source in sources:
            logger.info("Processing jail roster source: %s", source["county_name"])
            try:
                stats = _run_source(conn, source, dry_run=dry_run)
                results[source["county_slug"]] = stats
                conn.commit()
            except Exception as exc:
                logger.exception("Jail booking ingest failed for %s", source["county_slug"])
                _record_run(
                    conn,
                    source_id=source["id"],
                    run_type="scheduled" if not dry_run else "dry_run",
                    status="failed",
                    notes=str(exc),
                )
                _mark_source_checked(conn, source["id"], success=False, notes=str(exc))
                conn.commit()
                raise
        return results
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync jail roster bookings into Montana Blotter.")
    parser.add_argument("--county", default="", help="Optional county slug, for example missoula")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and parse without writing booking rows")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    results = ingest_jail_bookings(county_slug=(args.county or "").strip().lower(), dry_run=args.dry_run)

    for county_slug, stats in results.items():
        print(
            f"{county_slug}: fetched={stats.fetched_count} new={stats.new_count} "
            f"updated={stats.updated_count} missing={stats.missing_count}"
        )


if __name__ == "__main__":
    main()
