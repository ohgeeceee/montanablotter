"""Adapters for county jail rosters published as simple HTML or PDF documents."""
from __future__ import annotations

import io
import re
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urljoin

import pdfplumber
import requests

from services.ingestion.models import JailBookingRecord

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"p", "div", "li", "br", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")

    def text(self) -> str:
        return re.sub(r"\n{2,}", "\n", " ".join(self.parts)).strip()


def _text(fragment: str) -> str:
    parser = _TextParser()
    parser.feed(fragment or "")
    return re.sub(r"\s+", " ", parser.text()).strip()


def _get(url: str) -> requests.Response:
    response = requests.get(url, headers={"User-Agent": UA}, timeout=45)
    response.raise_for_status()
    return response


def _date(value: str) -> str | None:
    value = value.strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%m/%d/%Y %H:%M", "%m/%d/%y %H:%M"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    return None


def _record(prefix: str, name: str, details: str, source_url: str, booking_at: str | None = None, booking_number: str = "") -> JailBookingRecord:
    name = re.sub(r"\s+", " ", name).strip()
    if "," in name:
        last, first = (part.strip() for part in name.split(",", 1))
        name = f"{last.title()}, {first.title()}"
    else:
        name = name.title()
    key = booking_number or name.lower()
    return JailBookingRecord(
        source_record_id=f"{prefix}:{key.lower().replace(' ', '-')}",
        person_name=name,
        age=None,
        booking_number=booking_number,
        booking_at=booking_at,
        charges_summary=details or "Charge details available on the official county roster.",
        source_url=source_url,
    )


def fetch_fallon_bookings(source_url: str) -> list[JailBookingRecord]:
    response = _get(source_url)
    match = re.search(r"Current Inmates:\s*</h3>(.*?)(?:Last updated:|Visitation Hours:)", response.text, re.I | re.S)
    if not match:
        return []
    records: list[JailBookingRecord] = []
    for fragment in re.findall(r"<p[^>]*>(.*?)</p>", match.group(1), re.I | re.S):
        line = _text(fragment)
        name_match = re.match(r"([^:]{2,80}):\s*(.+)", line)
        if not name_match:
            continue
        records.append(_record("fallon", name_match.group(1), name_match.group(2), source_url))
    return records


def fetch_fergus_bookings(source_url: str) -> list[JailBookingRecord]:
    response = _get(source_url)
    records: list[JailBookingRecord] = []
    pattern = re.compile(
        r'<span class="sppb-panel-title" aria-label="([^"]+)">.*?</span>.*?'
        r'<div class="sppb-addon-content[^>]*">(.*?)</div>',
        re.I | re.S,
    )
    for match in pattern.finditer(response.text):
        name, body = match.groups()
        details = _text(body)
        booked = re.search(r"Booked in on:\s*([0-9/]+)", details, re.I)
        booking_at = _date(booked.group(1)) if booked else None
        if not booked:
            continue
        records.append(_record("fergus", name, details, source_url, booking_at))
    return records


def _pdf_text(url: str) -> str:
    response = _get(url)
    with pdfplumber.open(io.BytesIO(response.content)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def fetch_glacier_bookings(source_url: str) -> list[JailBookingRecord]:
    search_url = urljoin(source_url, "/wp-json/wp/v2/search?search=Active%20Inmate%20Report&per_page=10")
    search_response = _get(search_url)
    results = search_response.json()
    if not results:
        raise RuntimeError("Glacier County WordPress API returned no active inmate reports.")
    post_url = results[0].get("url", "")
    if not post_url:
        raise RuntimeError("Glacier County active inmate report has no public post URL.")
    page = _get(post_url).text
    links = re.findall(r'href=["\']([^"\']+\.pdf)["\']', page, re.I)
    links = [link for link in links if "inmate" in link.lower() or "report" in link.lower()]
    if not links:
        raise RuntimeError("Glacier County active inmate post did not contain a roster PDF link.")
    pdf_url = urljoin(source_url, links[0])
    records: list[JailBookingRecord] = []
    for line in _pdf_text(pdf_url).splitlines():
        match = re.match(r"\((\d+)\)\s+(.+?)\s+\d+\s+[\d,]+\s+(\d{2}/\d{2}/\d{4})$", line.strip())
        if match:
            records.append(_record("glacier", match.group(2), "Official Glacier County active inmate report.", pdf_url, _date(match.group(3)), match.group(1)))
    return records


def fetch_roosevelt_bookings(source_url: str) -> list[JailBookingRecord]:
    page = _get(source_url).text
    links = re.findall(r'href=["\']([^"\']*CURRENT-INMATES-CHARGES[^"\']*\.pdf)["\']', page, re.I)
    if not links:
        return []
    pdf_url = urljoin(source_url, links[0])
    records: list[JailBookingRecord] = []
    text = _pdf_text(pdf_url)
    pattern = re.compile(r"^\s*(\d{2}/\d{2}/\d{2})\s+([A-Z][A-Z' -]+,\s*[A-Z][A-Z' -]+(?:\s+(?:JR|SR|III))?)\s+(\d{1,3})\s*$", re.I)
    for line in text.splitlines():
        match = re.match(r"^\s*(\d{2}/\d{2}/\d{2})\s+\d{1,2}:\d{2}\s+(.+?,\s+.+?)\s+(\d{1,3})\s*$", line)
        if match and not re.search(r"Booking Date|Last, First|Page ", line, re.I):
            records.append(_record("roosevelt", match.group(2), "Official Roosevelt County current inmates and charges report.", pdf_url, _date(match.group(1))))
    return records


def fetch_big_horn_bookings(source_url: str) -> list[JailBookingRecord]:
    # The county's official detention page links to CitizenRIMS, but its
    # published agency configuration explicitly has inmatesEnabled=false and
    # every inmate field disabled. This is a source-side policy, not a parser
    # or routing failure.
    raise RuntimeError("Big Horn County CitizenRIMS has public inmate access disabled by the agency.")
