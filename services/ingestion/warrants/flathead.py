"""Flathead County Sheriff active warrant list adapter."""

from __future__ import annotations

import html
import logging
import re
from html import unescape
from html.parser import HTMLParser

import requests

from services.ingestion.warrants.models import WarrantRecord

logger = logging.getLogger(__name__)

FLATHEAD_LIST_URL = "https://apps.flathead.mt.gov/warrants/warrants_list.php"
FLATHEAD_BASE_URL = "https://apps.flathead.mt.gov/warrants/"
_COUNTY = "Flathead"

_ENTRY_RE = re.compile(
    r"\[\s*([^\]]+?)\s*###### Age:\s*(\d+)\s*###### Last Known Location:\s*\n?\s*([^\n\]]+?)\s*\n\s*([^\]]+?)\s*"
    r"\]\(warrants_view\.php\?line=(\d+)",
    re.DOTALL,
)

_HTML_LINK_RE = re.compile(
    r'<a[^>]+class="warrant-link"[^>]+href="warrants_view\.php\?line=(\d+)[^"]*"[^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
_MUGSHOT_RE = re.compile(
    r"image_thumb_script\.php\?f=([A-Za-z0-9]+)",
    re.IGNORECASE,
)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"br", "hr"}:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if data:
            self._parts.append(data)

    def text(self) -> str:
        text = html.unescape("".join(self._parts))
        text = text.replace("\xa0", " ")
        return re.sub(r"\s+", " ", text).strip()


def flathead_mugshot_url_from_html(fragment: str) -> str:
    """Return absolute Flathead sheriff mugshot URL when the list entry includes a photo."""
    match = _MUGSHOT_RE.search(fragment or "")
    if not match:
        return ""
    file_id = match.group(1).strip()
    if not file_id:
        return ""
    return f"{FLATHEAD_BASE_URL}image_thumb_script.php?f={file_id}"


def _text_from_html(fragment: str) -> str:
    parser = _TextExtractor()
    parser.feed(fragment or "")
    return parser.text()


def _normalize_name(raw: str) -> str:
    name = unescape(raw).strip()
    if "," in name:
        last, first = name.split(",", 1)
        return f"{first.strip()} {last.strip()}".title()
    return name.title()


def _html_to_markdownish(page_html: str) -> str:
    blocks: list[str] = []
    for match in _HTML_LINK_RE.finditer(page_html):
        line_id, inner = match.groups()

        name_match = re.search(
            r'<div class="warrant-name">\s*<p>\s*(.*?)\s*</p>',
            inner,
            re.DOTALL | re.IGNORECASE,
        )
        raw_name = _text_from_html(name_match.group(1)) if name_match else ""

        age_match = re.search(
            r'<div class="warrant-stat">\s*<h6>\s*Age:\s*</h6>\s*<p>\s*(.*?)\s*</p>\s*</div>',
            inner,
            re.DOTALL | re.IGNORECASE,
        )
        age = _text_from_html(age_match.group(1)) if age_match else ""

        location_match = re.search(
            r'<div class="warrant-stat">\s*<h6>\s*Last Known Location:\s*</h6>\s*<p>\s*(.*?)\s*</p>\s*</div>',
            inner,
            re.DOTALL | re.IGNORECASE,
        )
        location = _text_from_html(location_match.group(1)) if location_match else ""

        charge_match = re.search(
            r'<div class="warrant-disposition">\s*<p[^>]*>\s*(.*?)\s*</p>\s*</div>',
            inner,
            re.DOTALL | re.IGNORECASE,
        )
        charge = _text_from_html(charge_match.group(1)) if charge_match else ""

        if not raw_name:
            continue

        blocks.append(
            f"[\n{raw_name}\n###### Age:\n{age}\n###### Last Known Location:\n{location}\n{charge}\n"
            f"](warrants_view.php?line={line_id}&letter=)"
        )
    return "\n".join(blocks)


def parse_flathead_warrant_page(html: str, *, source_url: str = FLATHEAD_LIST_URL) -> list[WarrantRecord]:
    parser_input = html if _ENTRY_RE.search(html) else _html_to_markdownish(html)
    records: list[WarrantRecord] = []
    seen: set[str] = set()

    mugshot_by_line: dict[str, str] = {}
    if _HTML_LINK_RE.search(html):
        for link_match in _HTML_LINK_RE.finditer(html):
            line_id, inner = link_match.groups()
            thumb = flathead_mugshot_url_from_html(inner)
            if thumb:
                mugshot_by_line[line_id.strip()] = thumb

    for match in _ENTRY_RE.finditer(parser_input):
        raw_name, _age, location, charge, line_id = match.groups()
        line_key = line_id.strip()
        source_record_id = f"flathead-warrant:{line_key}"
        if source_record_id in seen:
            continue
        seen.add(source_record_id)

        person_name = _normalize_name(raw_name)
        city = " ".join(location.strip().split())

        records.append(
            WarrantRecord(
                source_record_id=source_record_id,
                county=_COUNTY,
                person_name=person_name,
                city=city,
                dob="",
                warrant_type="active",
                charges_text=" ".join(charge.strip().split()),
                issued_by="Flathead County Sheriff's Office",
                issue_date="",
                source_url=f"{FLATHEAD_BASE_URL}warrants_view.php?line={line_key}",
                mugshot_url=mugshot_by_line.get(line_key, ""),
            )
        )

    logger.info("Parsed %d Flathead warrant record(s) from %s", len(records), source_url)
    return records


def fetch_flathead_warrants(session: requests.Session) -> list[WarrantRecord]:
    records: list[WarrantRecord] = []
    seen: set[str] = set()

    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        url = f"{FLATHEAD_LIST_URL}?letter={letter}"
        try:
            resp = session.get(url, timeout=60)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Failed to fetch Flathead warrant page %s: %s", url, exc)
            continue

        for record in parse_flathead_warrant_page(resp.text, source_url=url):
            if record.source_record_id in seen:
                continue
            seen.add(record.source_record_id)
            records.append(record)

    return records
