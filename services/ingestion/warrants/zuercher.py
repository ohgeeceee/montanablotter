"""Zuercher portal active warrant list adapter."""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timezone

import requests

from services.ingestion.warrants.html_table import normalize_person_name, slugify
from services.ingestion.warrants.models import WarrantRecord

logger = logging.getLogger(__name__)

JAIL_HOLD_WARRANT_TYPE = "jail-hold-warrant"
_JAIL_HOLD_PREFIX = (
    "[Jail roster hold — warrant-related charge; not an official county warrant list] "
)


def _portal_base(source_url: str) -> str:
    base = source_url.rstrip("/")
    if "#/" in base:
        base = base.split("#", 1)[0].rstrip("/")
    return base


def parse_zuercher_warrant_rows(
    rows: list[dict],
    *,
    county: str,
    source_url: str,
) -> list[WarrantRecord]:
    records: list[WarrantRecord] = []
    seen: set[str] = set()

    for row in rows:
        raw_name = (row.get("name") or row.get("defendant") or "").strip()
        if not raw_name:
            continue

        person_name = normalize_person_name(raw_name)
        slug = slugify(person_name)
        source_record_id = f"zuercher-warrant:{county.lower().replace(' ', '-')}:{slug}"
        if source_record_id in seen:
            counter = 2
            while f"{source_record_id}:{counter}" in seen:
                counter += 1
            source_record_id = f"{source_record_id}:{counter}"
        seen.add(source_record_id)

        charges = (row.get("charges") or row.get("charge") or row.get("offense") or "").strip()
        if not charges:
            charges = (row.get("warrant_type") or row.get("type") or "Active warrant").strip()

        bond = str(row.get("bond") or row.get("bond_amount") or "").strip()
        issued_by = (row.get("issued_by") or row.get("court") or row.get("issuing_agency") or "").strip()

        records.append(
            WarrantRecord(
                source_record_id=source_record_id,
                county=county,
                person_name=person_name,
                charges_text=charges,
                issued_by=issued_by,
                bond_amount=bond,
                source_url=source_url,
            )
        )

    return records


def _text_from_html(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment or "")
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def _summarize_hold_reasons(raw_value: str) -> str:
    raw = (raw_value or "").strip()
    if not raw:
        return ""
    parts = [
        _text_from_html(fragment)
        for fragment in re.split(r"<br\s*/?>", raw, flags=re.IGNORECASE)
        if _text_from_html(fragment)
    ]
    return "; ".join(parts[:4])


def _hold_has_warrant(raw_value: str) -> bool:
    return "warrant" in (raw_value or "").lower()


def parse_zuercher_jail_warrant_holds(
    rows: list[dict],
    *,
    county: str,
    source_url: str,
) -> list[WarrantRecord]:
    """Build warrant records from in-custody inmates with warrant-related holds."""
    records: list[WarrantRecord] = []
    seen: set[str] = set()
    inmates_url = source_url.replace("#/warrants", "#/inmates")
    issued_by = f"{county} County Detention (jail roster hold)"

    for row in rows:
        hold_reasons = row.get("hold_reasons") or ""
        if not _hold_has_warrant(hold_reasons):
            continue

        raw_name = (row.get("name") or "").strip()
        if not raw_name:
            continue

        person_name = normalize_person_name(raw_name)
        slug = slugify(person_name)
        source_record_id = f"zuercher-jail-hold:{county.lower().replace(' ', '-')}:{slug}"
        if source_record_id in seen:
            counter = 2
            while f"{source_record_id}:{counter}" in seen:
                counter += 1
            source_record_id = f"{source_record_id}:{counter}"
        seen.add(source_record_id)

        hold_summary = _summarize_hold_reasons(hold_reasons)
        records.append(
            WarrantRecord(
                source_record_id=source_record_id,
                county=county,
                person_name=person_name,
                warrant_type=JAIL_HOLD_WARRANT_TYPE,
                charges_text=f"{_JAIL_HOLD_PREFIX}{hold_summary or 'Warrant-related jail hold'}",
                issued_by=issued_by,
                source_url=inmates_url,
            )
        )

    return records


def fetch_zuercher_jail_warrant_holds(
    session: requests.Session,
    source_url: str,
    *,
    county: str,
) -> list[WarrantRecord]:
    """Fallback: inmates in custody with warrant-related hold reasons."""
    api_base = _portal_base(source_url)
    inmates_url = f"{api_base}/#/inmates"
    session.headers.setdefault(
        "User-Agent",
        "Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://montanablotter.com)",
    )
    session.headers.setdefault("Accept", "application/json,text/plain,*/*")
    session.headers["Referer"] = inmates_url

    records: list[WarrantRecord] = []
    seen: set[str] = set()
    offset = 0
    page_size = 50
    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00.000Z")

    while True:
        try:
            response = session.post(
                f"{api_base}/api/portal/inmates/load",
                json={
                    "name": "",
                    "race": "all",
                    "sex": "all",
                    "cell_block": "all",
                    "held_for_agency": "any",
                    "in_custody": today,
                    "paging": {"start": offset, "count": page_size},
                    "sorting": {"sort_by_column_tag": "name", "sort_descending": False},
                },
                timeout=45,
            )
        except requests.RequestException as exc:
            logger.warning("Failed to fetch Zuercher inmates for %s: %s", county, exc)
            return records

        if response.status_code == 404:
            logger.info("Zuercher inmates API not available for %s", county)
            return records

        content_type = response.headers.get("Content-Type", "")
        if "text/html" in content_type or response.content[:100].strip().lower().startswith(b"<"):
            logger.info("Zuercher inmates portal for %s returned HTML (maintenance)", county)
            return records

        try:
            payload = response.json() or {}
        except ValueError:
            logger.warning("Zuercher inmates response for %s was not JSON", county)
            return records

        rows = payload.get("records") or []
        for record in parse_zuercher_jail_warrant_holds(rows, county=county, source_url=inmates_url):
            if record.source_record_id in seen:
                continue
            seen.add(record.source_record_id)
            records.append(record)

        offset += len(rows)
        total = int(payload.get("total_record_count") or 0)
        if not rows or offset >= total:
            break

    if records:
        logger.info(
            "Fetched %d Zuercher jail-hold warrant record(s) for %s (supplement)",
            len(records),
            county,
        )
    return records


def fetch_zuercher_warrants(
    session: requests.Session,
    source_url: str,
    *,
    county: str,
) -> list[WarrantRecord]:
    """Fetch warrant records from a Zuercher portal warrants section."""
    api_base = _portal_base(source_url)
    referer = source_url if "#/" in source_url else f"{api_base}/#/warrants"
    session.headers.setdefault(
        "User-Agent",
        "Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://montanablotter.com)",
    )
    session.headers.setdefault("Accept", "application/json,text/plain,*/*")
    session.headers["Referer"] = referer

    url = f"{api_base}/api/portal/warrants/load"
    try:
        response = session.get(url, timeout=45)
    except requests.RequestException as exc:
        logger.warning("Failed to fetch Zuercher warrants for %s: %s", county, exc)
        return fetch_zuercher_jail_warrant_holds(session, source_url, county=county)

    content_type = response.headers.get("Content-Type", "")
    if "text/html" in content_type or response.content[:100].strip().lower().startswith(b"<"):
        logger.info("Zuercher warrants portal for %s returned HTML (maintenance or blocked)", county)
        return fetch_zuercher_jail_warrant_holds(session, source_url, county=county)

    if response.status_code == 404:
        logger.info("Zuercher warrants API not available for %s", county)
        return fetch_zuercher_jail_warrant_holds(session, source_url, county=county)

    try:
        payload = response.json() or {}
    except ValueError:
        logger.warning("Zuercher warrants response for %s was not JSON", county)
        return fetch_zuercher_jail_warrant_holds(session, source_url, county=county)

    if payload.get("status") not in (None, 200) and not payload.get("records"):
        logger.info(
            "Zuercher warrants API for %s returned status %s",
            county,
            payload.get("status") or response.status_code,
        )
        return fetch_zuercher_jail_warrant_holds(session, source_url, county=county)

    rows = payload.get("records") or []
    if not rows:
        logger.info("No Zuercher warrant records returned for %s", county)
        return fetch_zuercher_jail_warrant_holds(session, source_url, county=county)

    records = parse_zuercher_warrant_rows(rows, county=county, source_url=source_url)
    logger.info("Fetched %d Zuercher warrant record(s) for %s", len(records), county)
    return records
