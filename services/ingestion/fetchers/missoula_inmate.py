"""
missoula_inmate_fetcher.py
==========================
Missoula County jail roster fetcher.

Targets the Missoula County inmate information portal at
https://webapps.missoulacounty.us/jailroster/Inmates (related to missoulaso.com).

Parses inmate names, booking dates, charges, and bail amounts.
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from datetime import datetime, timedelta

import requests

from services.ingestion.jail_bookings import (
    JailBookingRecord,
    _extract_hidden_form_fields,
    _extract_text_lines,
    _is_name_line,
    _normalize_datetime,
    _text_from_html,
)

logger = logging.getLogger(__name__)

MISSOULA_CHARGE_LOOKBACK_DAYS = 30
DEFAULT_SOURCE_URL = "https://webapps.missoulacounty.us/jailroster/Inmates"


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


def fetch_missoula_bookings(source_url: str = DEFAULT_SOURCE_URL) -> list[JailBookingRecord]:
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
