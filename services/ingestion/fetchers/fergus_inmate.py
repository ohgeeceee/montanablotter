#!/usr/bin/env python3
"""
fergus_inmate.py
=================
Fetches Fergus County (MT) detention center roster from
https://fergusmt.gov/detention-center-roster.

The page is a server-rendered Joomla Sppb accordion.  Each panel
(button.sppb-panel-heading) has an aria-label like
"Aamold, Melvin Ralph", and the panel body contains <p> tags with
"Booked in on: MM/DD/YYYY", charges, court refs, and bond info.

We parse the raw HTML directly — no JS needed.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import NamedTuple

import requests

from services.ingestion.models import JailBookingRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://fergusmt.gov/detention-center-roster"
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://montanablotter.com)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
})


class _Panel(NamedTuple):
    name: str
    booked_raw: str
    lines: list[str]


def _date_to_iso(raw: str) -> str | None:
    raw = raw.strip()
    try:
        dt = datetime.strptime(raw, "%m/%d/%Y")
        return dt.replace(tzinfo=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


class _FergusParser(HTMLParser):
    """Extract inmate panels from the Joomla accordion HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.panels: list[_Panel] = []
        self._current: _Panel | None = None
        self._in_body = False
        self._capture_p = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {name.lower(): (value or "") for name, value in attrs}

        # Each accordion panel heading is a <button> with the inmate name
        # in its aria-label (on a child <span>.sppb-panel-title).
        if tag == "span" and "sppb-panel-title" in attr_map.get("class", ""):
            aria = attr_map.get("aria-label", "").strip()
            if aria and "," in aria:
                if self._current is not None:
                    self.panels.append(self._current)
                self._current = _Panel(name=aria, booked_raw="", lines=[])
                self._in_body = False

        # Panel body starts here.
        if self._current is not None and tag == "div":
            if "sppb-panel-body" in attr_map.get("class", ""):
                self._in_body = True

        # Capture <p> text inside panel body.
        if self._in_body and tag == "p":
            self._capture_p = True

    def handle_endtag(self, tag: str) -> None:
        if self._in_body and tag == "div":
            self._in_body = False
        if tag == "p":
            self._capture_p = False

    def handle_data(self, data: str) -> None:
        if self._capture_p and self._current is not None:
            text = data.strip()
            if text:
                self._current.lines.append(text)

    def finalize(self) -> None:
        if self._current is not None:
            self.panels.append(self._current)
            self._current = None


def _build_record(panel: _Panel, idx: int) -> JailBookingRecord | None:
    if not panel.name or not panel.booked_raw:
        return None

    person_name = panel.name.strip().title()
    booking_at = _date_to_iso(panel.booked_raw)
    source_record_id = hashlib.sha1(
        f"fergus:{person_name.lower().replace(' ', '-')}:{panel.booked_raw}".encode()
    ).hexdigest()[:20]

    # Combine all panel body lines as charges summary
    charges_summary = "; ".join(panel.lines) if panel.lines else \
        "Charge details available on the official Fergus County detention roster."

    return JailBookingRecord(
        source_record_id=source_record_id,
        person_name=person_name,
        age=None,
        booking_number="",
        booking_at=booking_at,
        charges_summary=charges_summary,
        source_url=BASE_URL,
    )


def fetch_fergus_bookings(source_url: str | None = None) -> list[JailBookingRecord]:
    url = source_url or BASE_URL
    try:
        resp = SESSION.get(url, timeout=45)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("Fergus page fetch failed: %s", exc)
        return []

    parser = _FergusParser()
    parser.feed(resp.text)
    parser.finalize()

    records: list[JailBookingRecord] = []
    for i, panel in enumerate(parser.panels):
        # Find "Booked in on:" line
        booked_raw = ""
        body_lines: list[str] = []
        for line in panel.lines:
            if line.startswith("Booked in on:"):
                booked_raw = line[len("Booked in on:"):].strip()
            else:
                body_lines.append(line)

        if not booked_raw:
            continue

        record = _build_record(_Panel(name=panel.name, booked_raw=booked_raw, lines=body_lines), i)
        if record:
            records.append(record)

    logger.info("Fergus: parsed %d records from %d panels", len(records), len(parser.panels))
    return records


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    recs = fetch_fergus_bookings()
    print(f"Fetched {len(recs)} Fergus records")
    for r in recs[:5]:
        print(f"  {r.person_name} | booked={r.booking_at}")
        print(f"    {r.charges_summary[:150]}")
