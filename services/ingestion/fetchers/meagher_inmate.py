#!/usr/bin/env python3
"""
meagher_inmate.py
==================
Fetches Meagher County (MT) inmate roster via Zuercher portal.

Same pattern as Madison: the portal at https://meagher-so-mt.zuercherportal.com
uses an in-browser SPA backed by a JSON API.  The original fetcher hit a 404
because ``/api/public/inmate/criteria`` is not mounted at that path on this
installation — this version reads the portal's JS payload to discover the real
endpoint.
"""
from __future__ import annotations

import json
import logging
import re
import sys
from urllib.parse import urljoin

import requests

sys.path.insert(0, "/root/montanablotter")
from services.ingestion.models import JailBookingRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://meagher-so-mt.zuercherportal.com"
DEFAULT_SOURCE_URL = f"{BASE_URL}/#/inmates"

_CRITERIA_RE = re.compile(
    r'"/api/public/inmate/criteria"\s*:\s*(\{[^}]+\})',
    re.DOTALL,
)

def _discover_criteria_url(session: requests.Session) -> str:
    """Read the portal homepage JS to find the real criteria endpoint."""
    try:
        resp = session.get(BASE_URL, timeout=30)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("Meagher portal fetch failed: %s", exc)
        return urljoin(BASE_URL, "/api/public/inmate/criteria")

    match = _CRITERIA_RE.search(resp.text)
    if match:
        try:
            payload = json.loads(match.group(1))
            api_root = payload.get("apiRoot") or payload.get("api_root") or BASE_URL
            return urljoin(api_root, "/api/public/inmate/criteria")
        except (json.JSONDecodeError, AttributeError):
            pass
    return urljoin(BASE_URL, "/api/public/inmate/criteria")


def _parse_payload(payload: dict | list) -> list[JailBookingRecord]:
    """Normalise a Zuercher criteria payload into JailBookingRecords."""
    inmates: list[dict] = []
    if isinstance(payload, list):
        inmates = payload
    elif isinstance(payload, dict):
        inmates = payload.get("inmates") or payload.get("data") or payload.get("records") or []
    if not inmates:
        return []

    records: list[JailBookingRecord] = []
    seen: set[str] = set()
    for inmate in inmates:
        inmate_id = str(inmate.get("inmateId") or inmate.get("id") or "")
        first = str(inmate.get("firstName") or "").strip()
        last = str(inmate.get("lastName") or "").strip()
        if not (inmate_id and (first or last)):
            continue
        person_name = f"{last}, {first}".strip(", ") if first else last
        booking_raw = inmate.get("bookingDate") or inmate.get("bookDate") or ""
        booking_at = booking_raw[:19] if len(booking_raw) >= 19 else booking_raw
        charges_raw = inmate.get("charges") or inmate.get("chargeList") or inmate.get("chargesList") or []
        if isinstance(charges_raw, str):
            charges_summary = charges_raw
        elif isinstance(charges_raw, list):
            charges_summary = "; ".join(
                str(c.get("description", c.get("charge", c))) for c in charges_raw if c
            ) or "Charge details available on the official Meagher County portal."
        else:
            charges_summary = "Charge details available on the official Meagher County portal."

        source_record_id = f"meagher:{person_name.lower().replace(' ', '-')}:{inmate_id}"
        if source_record_id in seen:
            continue
        seen.add(source_record_id)
        records.append(JailBookingRecord(
            source_record_id=source_record_id,
            person_name=person_name,
            age=None,
            booking_number=inmate_id,
            booking_at=booking_at or None,
            charges_summary=charges_summary,
            source_url=DEFAULT_SOURCE_URL,
        ))
    logger.info("Meagher: parsed %d records", len(records))
    return records


def fetch_meagher_bookings(source_url: str | None = None) -> list[JailBookingRecord]:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://montanablotter.com)",
        "Accept": "application/json, text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    criteria_url = _discover_criteria_url(session)
    logger.info("Meagher criteria URL: %s", criteria_url)
    try:
        resp = session.get(criteria_url, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        logger.warning("Meagher criteria fetch failed: %s", exc)
        return []
    return _parse_payload(payload)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    recs = fetch_meagher_bookings()
    print(f"Fetched {len(recs)} records")
    for r in recs[:5]:
        print(r)
