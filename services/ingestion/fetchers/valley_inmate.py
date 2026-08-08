"""Fetch and parse Valley County's daily CivicPlus jail-roster PDF."""
from __future__ import annotations

import io
import re
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urljoin

import pdfplumber
import requests

from services.ingestion.models import JailBookingRecord

BASE_URL = "https://www.valleycountymt.gov/1288/Jail-Roster"
UA = "Mozilla/5.0 (X11; Linux x86_64) Chrome/124 Safari/537.36"


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[tuple[str, str]] = []
        self.href = ""
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        if tag == "a":
            self.href = dict(attrs).get("href", "")
            self.text = []

    def handle_data(self, data) -> None:
        if self.href:
            self.text.append(data)

    def handle_endtag(self, tag) -> None:
        if tag == "a" and self.href:
            self.items.append((" ".join(self.text).strip(), self.href))
            self.href = ""


def _get(url: str) -> requests.Response:
    response = requests.get(url, headers={"User-Agent": UA}, timeout=60)
    response.raise_for_status()
    return response


def _discover_pdf_url(source_url: str) -> str:
    parser = _Links()
    parser.feed(_get(source_url).text)
    months = [(text, href) for text, href in parser.items if re.match(r"^(January|February|March|April|May|June|July|August|September|October|November|December) 20\d{2}$", text)]
    if not months:
        raise RuntimeError("Valley County roster page did not list monthly archive pages.")
    months.sort(key=lambda item: datetime.strptime(item[0], "%B %Y"), reverse=True)
    month_url = urljoin(source_url, months[0][1])
    parser = _Links()
    parser.feed(_get(month_url).text)
    documents = [(text, href) for text, href in parser.items if "/DocumentCenter/View/" in href]
    if not documents:
        raise RuntimeError("Valley County latest month did not contain a jail roster document.")
    documents.sort(key=lambda item: int(re.search(r"/View/(\d+)", item[1]).group(1)), reverse=True)
    return urljoin(month_url, documents[0][1])


def _parse_roster_text(text: str, source_url: str) -> list[JailBookingRecord]:
    lines = [line.strip() for line in text.splitlines()]
    held_markers = (
        "County Sheriff's Office", "Department of Corrections",
        "Glasgow Police Department", "Fort Peck Tribes", "US Marshals",
    )
    starts: list[tuple[int, str, str, str]] = []
    name_pattern = re.compile(r"^([A-Z][A-Z'. -]+,\s+[A-Z][A-Z'. -]+?(?:\s+(?:JR\.?|SR\.?|II|III))?)\s+(.*)$")
    for index, line in enumerate(lines):
        match = name_pattern.match(line)
        if not match or match.group(1) == "NAME":
            continue
        remainder = match.group(2)
        marker = next((value for value in held_markers if value in remainder), None)
        if not marker:
            continue
        before, after = remainder.split(marker, 1)
        held_for = f"{before}{marker}".strip()
        starts.append((index, match.group(1), held_for, after.strip()))

    records: list[JailBookingRecord] = []
    seen: set[str] = set()
    for position, (index, raw_name, held_for, first_charge) in enumerate(starts):
        name = re.sub(r"\s+", " ", raw_name).strip().title()
        charge_parts = [first_charge] if first_charge else []
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        for continuation in lines[index + 1:end]:
            if not continuation or continuation.startswith("Page ") or continuation == "NAME HELD FOR CHARGES" or continuation.startswith("Total Records:"):
                continue
            charge_parts.append(continuation)
        charges = re.sub(r"\s+", " ", " ".join(charge_parts)).strip()
        key = f"valley:{name.lower().replace(' ', '-')}"
        if key in seen:
            continue
        seen.add(key)
        records.append(JailBookingRecord(
            source_record_id=key,
            person_name=name,
            age=None,
            booking_number="",
            booking_at=None,
            charges_summary=f"Held for {held_for}. {charges}".strip(),
            source_url=source_url,
        ))
    return records


def fetch_valley_bookings(source_url: str = BASE_URL) -> list[JailBookingRecord]:
    pdf_url = _discover_pdf_url(source_url)
    response = _get(pdf_url)
    if not response.content.startswith(b"%PDF"):
        raise RuntimeError("Valley County roster document did not return a PDF.")
    with pdfplumber.open(io.BytesIO(response.content)) as pdf:
        text = "\n".join(page.extract_text(x_tolerance=2, y_tolerance=3) or "" for page in pdf.pages)
    records = _parse_roster_text(text, pdf_url)
    if not records:
        raise RuntimeError("Valley County roster PDF parsed zero inmate rows.")
    return records
