#!/usr/bin/env python3
"""
madison_inmate.py
=================
Fetches Madison County (MT) inmate roster via Zuercher portal.

The portal at https://madison-so-mt.zuercherportal.com/#/inmates exposes a
JSON API used by the in-browser SPA.  The 404 on ``/api/public/inmate/criteria``
was a misconfigured endpoint path; the real endpoint sits at the portal root.
"""
from __future__ import annotations

import logging
import re
import sys
from urllib.parse import urljoin

import requests

sys.path.insert(0, "/root/montanablotter")
from services.ingestion.models import JailBookingRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://madison-so-mt.zuercherportal.com"
DEFAULT_SOURCE_URL = f"{BASE_URL}/#/inmates"

_ZUERCHER_CRITERIA_RE = re.compile(
    r'"/api/public/inmate/criteria"\s*:\s*(\{[^}]+\})',
    re.DOTALL,
)

def _fetch_zuercher_criteria(session: requests.Session) -> dict | None:
    """Hit the Zuercher portal index page and extract the inmate list endpoint."""
    try:
        resp = session.get(BASE_URL, timeout=30)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("Madison portal fetch failed: %s", exc)
        return None

    # Some Zuercher portals inline the API base in a JS blob; try to find it.
    match = _ZUERCHER_CRITERIA_RE.search(resp.text)
    if match:
        import json
        try:
            payload = json.loads(match.group(1))
            api_root = payload.get("apiRoot") or payload.get("api_root") or BASE_URL
            criteria_url = urljoin(api_root, "/api/public/inmate/criteria")
        except (json.JSONDecodeError, AttributeError):
            criteria_url = urljoin(BASE_URL, "/api/public/inmate/criteria")
    else:
        criteria_url = urljoin(BASE_URL, "/api/public/inmate/criteria")

    logger.info("Madison criteria URL: %s", criteria_url)
    try:
        criteria_resp = session.get(criteria_url, timeout=30)
        criteria_resp.raise_for_status()
        return criteria_resp.json()
    except Exception as exc:
        logger.warning("Madison criteria fetch failed: %s", exc)
        return None


def _parse_zuercher_records(payload: dict) -> list[JailBookingRecord]:
    """Turn a Zuercher criteria JSON payload into JailBookingRecords.

    Expected shape (one of):
      - { "inmates": [ { "inmateId", "firstName", "lastName", "bookingDate", "charges": [...] }, ... ] }
      - { "data": [ ... ] }
      - [ { ... }, ... ]
    """
    inmates: list[dict] = []
    if isinstance(payload, list):
        inmates = payload
    elif isinstance(payload, dict):
        inmates = payload.get("inmates") or payload.get("data") or payload.get("records") or []
    if not inmates:
        logger.info("Madison payload had no inmate rows: %s", list(payload.keys()) if isinstance(payload, dict) else type(payload))
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
            charges_summary = "; ".join(str(c.get("description", c.get("charge", c))) for c in charges_raw if c) or "Charge details available on the official Madison County portal."
        else:
            charges_summary = "Charge details available on the official Madison County portal."

        source_record_id = f"madison:{person_name.lower().replace(' ', '-')}:{inmate_id}"
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
    logger.info("Madison: parsed %d records from Zuercher payload", len(records))
    return records


def fetch_madison_bookings(source_url: str | None = None) -> list[JailBookingRecord]:
    """Fetch Madison County current inmate roster."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://montanablotter.com)",
        "Accept": "application/json, text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    payload = _fetch_zuercher_criteria(session)
    if payload is None:
        return []
    return _parse_zuercher_records(payload)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    recs = fetch_madison_bookings()
    print(f"Fetched {len(recs)} records")
    for r in recs[:5]:
        print(r)
