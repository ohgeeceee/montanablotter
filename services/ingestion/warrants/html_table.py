"""Shared HTML table parser for municipal court warrant lists."""

from __future__ import annotations

import html
import logging
import re
from html.parser import HTMLParser

from services.ingestion.warrants.models import WarrantRecord

logger = logging.getLogger(__name__)


class _TableParser(HTMLParser):
    """Extract rows from HTML tables."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._cell_parts: list[str] = []
        self._row_cells: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "table":
            self._in_table = True
        elif self._in_table and tag == "tr":
            self._in_row = True
            self._row_cells = []
        elif self._in_row and tag in {"td", "th"}:
            self._in_cell = True
            self._cell_parts = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._in_cell:
            self._in_cell = False
            self._row_cells.append(" ".join(self._cell_parts).strip())
        elif tag == "tr" and self._in_row:
            self._in_row = False
            if self._row_cells:
                self.rows.append(self._row_cells)
        elif tag == "table" and self._in_table:
            self._in_table = False

    def handle_data(self, data: str) -> None:
        if self._in_cell and data:
            self._cell_parts.append(data.strip())


def normalize_person_name(raw: str) -> str:
    name = html.unescape(raw).strip()
    name = re.sub(r"\s+", " ", name)
    if "," in name:
        last, first = name.split(",", 1)
        return f"{first.strip()} {last.strip()}".title()
    return name.title()


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "unknown"


def _header_index(headers: list[str], *candidates: str) -> int | None:
    normalized = [re.sub(r"[^a-z0-9]+", " ", h.lower()).strip() for h in headers]
    for candidate in candidates:
        key = re.sub(r"[^a-z0-9]+", " ", candidate.lower()).strip()
        for idx, header in enumerate(normalized):
            if key in header or header in key:
                return idx
    return None


def looks_like_name(value: str) -> bool:
    value = value.strip()
    if not value or len(value) < 3:
        return False
    if value.lower() in {"name", "defendant", "last, first", "last name"}:
        return False
    return bool(re.match(r"^[A-Za-z][A-Za-z' \-.,]+$", value))


def parse_warrant_table_page(
    page_html: str,
    *,
    county: str,
    source_url: str,
    source_prefix: str,
    city: str = "",
    issued_by: str = "",
    info_only_markers: tuple[str, ...] = (),
) -> list[WarrantRecord]:
    """Parse an HTML table warrant list into records."""
    text = re.sub(r"<[^>]+>", " ", page_html)
    text = re.sub(r"\s+", " ", html.unescape(text)).strip().lower()
    if info_only_markers and any(marker in text for marker in info_only_markers):
        logger.info(
            "Warrant page at %s is informational only; no records to parse",
            source_url,
        )
        return []

    parser = _TableParser()
    parser.feed(page_html)

    records: list[WarrantRecord] = []
    seen: set[str] = set()

    if not parser.rows:
        logger.info("No warrant tables found on page %s", source_url)
        return records

    headers = [cell.lower() for cell in parser.rows[0]]
    has_header = any(
        token in " ".join(headers)
        for token in ("name", "charge", "warrant", "bond", "defendant")
    )
    data_rows = parser.rows[1:] if has_header else parser.rows

    name_idx = _header_index(parser.rows[0], "name", "defendant", "last") if has_header else None
    charge_idx = _header_index(parser.rows[0], "charge", "offense", "violation") if has_header else None
    bond_idx = _header_index(parser.rows[0], "bond", "bail") if has_header else None

    for row in data_rows:
        cells = [cell.strip() for cell in row if cell.strip()]
        if not cells:
            continue

        if name_idx is not None and name_idx < len(row):
            raw_name = row[name_idx].strip()
            charges = row[charge_idx].strip() if charge_idx is not None and charge_idx < len(row) else ""
            bond = row[bond_idx].strip() if bond_idx is not None and bond_idx < len(row) else ""
        elif len(cells) >= 2 and looks_like_name(cells[0]):
            raw_name, charges = cells[0], cells[1]
            bond = cells[2] if len(cells) > 2 else ""
        elif looks_like_name(cells[0]):
            raw_name, charges, bond = cells[0], "", ""
        else:
            continue

        if not looks_like_name(raw_name):
            continue

        person_name = normalize_person_name(raw_name)
        slug = slugify(person_name)
        source_record_id = f"{source_prefix}:{slug}"
        if source_record_id in seen:
            counter = 2
            while f"{source_record_id}:{counter}" in seen:
                counter += 1
            source_record_id = f"{source_record_id}:{counter}"
        seen.add(source_record_id)

        records.append(
            WarrantRecord(
                source_record_id=source_record_id,
                county=county,
                city=city,
                person_name=person_name,
                charges_text=charges or "Active warrant",
                issued_by=issued_by,
                bond_amount=bond,
                source_url=source_url,
            )
        )

    logger.info("Parsed %d warrant record(s) from %s", len(records), source_url)
    return records
