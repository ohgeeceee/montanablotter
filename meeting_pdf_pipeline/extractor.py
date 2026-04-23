from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import config


@dataclass
class ExtractionResult:
    pdf_path: str
    markdown: str
    text: str
    extraction_method: str
    is_scanned: bool
    page_count: int
    content_sha256: str


KNOWN_ROSTER_COLUMNS = {
    "name": {"name", "inmate", "defendant", "person"},
    "charge": {"charge", "charges", "offense", "offence", "crime"},
    "date": {"date", "booking date", "booked", "book date", "arrest date"},
    "bond": {"bond", "bond amount", "bail", "bail amount"},
}


def _normalise_column_token(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _column_key(raw_header: str) -> str | None:
    token = _normalise_column_token(raw_header)
    if not token:
        return None
    for key, aliases in KNOWN_ROSTER_COLUMNS.items():
        if token in aliases:
            return key
        if any(alias in token for alias in aliases):
            return key
    return None


def _clean_money(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if raw.lower() in {"none", "n/a", "no bond"}:
        return raw
    if re.search(r"\d", raw):
        numeric = re.sub(r"[^\d.]", "", raw)
        return f"${numeric}" if numeric else raw
    return raw


def _normalise_date(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    # Keep original text if parsing fails; downstream can still render.
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            from datetime import datetime

            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def _extract_row_from_line(line: str) -> dict | None:
    text = re.sub(r"\s+", " ", (line or "").strip())
    if len(text) < 8:
        return None
    date_match = re.search(r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}[/-]\d{1,2}[/-]\d{1,2})", text)
    bond_match = re.search(r"(\$?\s?\d[\d,]*(?:\.\d{2})?|no bond|n/?a)\s*$", text, re.IGNORECASE)
    if not date_match:
        return None
    date_text = date_match.group(1)
    bond_text = bond_match.group(1) if bond_match else ""
    name_and_charge = text[: date_match.start()].strip(" -|")
    charge = ""
    name = name_and_charge
    if " - " in name_and_charge:
        name, charge = [part.strip() for part in name_and_charge.split(" - ", 1)]
    elif "," in name_and_charge:
        parts = [part.strip() for part in name_and_charge.split(",", 1)]
        name = parts[0]
        charge = parts[1] if len(parts) > 1 else ""
    trailer_start = date_match.end()
    if bond_match and bond_match.start() > trailer_start:
        charge_mid = text[trailer_start : bond_match.start()].strip(" -|")
        if charge_mid:
            charge = f"{charge} {charge_mid}".strip()
    if not name or len(name) < 2:
        return None
    return {
        "name": name,
        "charge": charge or "Unknown",
        "date": _normalise_date(date_text),
        "bond": _clean_money(bond_text),
    }


def extract_jail_roster_rows(pdf_path: str, *, county: str = "", max_pages: int = 0) -> list[dict]:
    """
    Extract jail roster rows from a PDF into resilient structured JSON-like dicts.
    Handles table-based PDFs and OCR/text fallback when layout shifts.
    """
    rows: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()

    try:
        import pdfplumber

        with pdfplumber.open(pdf_path) as pdf:
            for page_index, page in enumerate(pdf.pages, start=1):
                if max_pages and page_index > max_pages:
                    break
                for table in (page.extract_tables() or []):
                    if not table or len(table) < 2:
                        continue
                    header = [(_normalise_column_token(cell or "")) for cell in table[0]]
                    column_map: dict[int, str] = {}
                    for idx, heading in enumerate(header):
                        key = _column_key(heading)
                        if key:
                            column_map[idx] = key
                    if not {"name", "charge", "date"}.issubset(set(column_map.values())):
                        continue
                    for raw_row in table[1:]:
                        if not raw_row:
                            continue
                        record = {"name": "", "charge": "", "date": "", "bond": ""}
                        for idx, cell in enumerate(raw_row):
                            mapped = column_map.get(idx)
                            if not mapped:
                                continue
                            record[mapped] = re.sub(r"\s+", " ", (cell or "").strip())
                        if not record["name"] or not record["date"]:
                            continue
                        normalised = {
                            "name": record["name"],
                            "charge": record["charge"] or "Unknown",
                            "date": _normalise_date(record["date"]),
                            "bond": _clean_money(record["bond"]),
                        }
                        signature = (
                            normalised["name"].lower(),
                            normalised["charge"].lower(),
                            normalised["date"],
                            normalised["bond"].lower(),
                        )
                        if signature in seen:
                            continue
                        seen.add(signature)
                        rows.append(normalised)
    except Exception:
        # fall through to OCR/text fallback
        rows = []

    if rows:
        if county:
            return [{**row, "county": county} for row in rows]
        return rows

    extractor = MeetingPDFMarkdownExtractor()
    extracted = extractor.convert(pdf_path)
    for line in extracted.text.splitlines():
        candidate = _extract_row_from_line(line)
        if not candidate:
            continue
        signature = (
            candidate["name"].lower(),
            candidate["charge"].lower(),
            candidate["date"],
            candidate["bond"].lower(),
        )
        if signature in seen:
            continue
        seen.add(signature)
        rows.append(candidate)

    if county:
        return [{**row, "county": county} for row in rows]
    return rows


class MeetingPDFMarkdownExtractor:
    def __init__(self, *, ocr_dpi: int = 300, scanned_text_threshold: int = 80) -> None:
        self.ocr_dpi = ocr_dpi
        self.scanned_text_threshold = scanned_text_threshold

    def convert(self, pdf_path: str) -> ExtractionResult:
        path = Path(pdf_path)
        if not path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        probe = self._probe_pdf(path)
        if probe["is_scanned"]:
            markdown = self._ocr_to_markdown(path)
            return self._build_result(path, markdown, "tesseract_ocr", True, probe["page_count"])

        for converter in (self._convert_with_markitdown, self._convert_with_pymupdf, self._convert_with_pdfplumber):
            markdown = converter(path)
            if self._looks_useful(markdown):
                return self._build_result(path, markdown, converter.__name__.removeprefix("_convert_with_"), False, probe["page_count"])

        markdown = self._ocr_to_markdown(path)
        return self._build_result(path, markdown, "tesseract_ocr_fallback", True, probe["page_count"])

    def _build_result(self, path: Path, markdown: str, method: str, is_scanned: bool, page_count: int) -> ExtractionResult:
        text = self._markdown_to_text(markdown)
        digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
        return ExtractionResult(
            pdf_path=str(path),
            markdown=markdown,
            text=text,
            extraction_method=method,
            is_scanned=is_scanned,
            page_count=page_count,
            content_sha256=digest,
        )

    def _probe_pdf(self, path: Path) -> dict:
        import pdfplumber

        page_count = 0
        extracted_chars = 0
        image_pages = 0
        text_pages = 0
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                page_count += 1
                text = page.extract_text() or ""
                if text.strip():
                    text_pages += 1
                    extracted_chars += len(text.strip())
                if getattr(page, "images", None):
                    image_pages += 1

        is_scanned = extracted_chars < self.scanned_text_threshold or (page_count > 0 and text_pages == 0 and image_pages > 0)
        return {
            "page_count": page_count,
            "text_pages": text_pages,
            "image_pages": image_pages,
            "extracted_chars": extracted_chars,
            "is_scanned": is_scanned,
        }

    def _convert_with_markitdown(self, path: Path) -> str:
        try:
            from markitdown import MarkItDown
        except ImportError:
            return ""

        converter = MarkItDown()
        result = converter.convert(str(path))
        return (getattr(result, "text_content", "") or "").strip()

    def _convert_with_pymupdf(self, path: Path) -> str:
        try:
            import fitz
        except ImportError:
            return ""

        doc = fitz.open(str(path))
        try:
            pages = []
            for page in doc:
                try:
                    pages.append(page.get_text("markdown"))
                except (TypeError, ValueError):
                    pages.append(page.get_text())
            return "\n\n".join(part.strip() for part in pages if part and part.strip()).strip()
        finally:
            doc.close()

    def _convert_with_pdfplumber(self, path: Path) -> str:
        import pdfplumber

        pages: list[str] = []
        with pdfplumber.open(str(path)) as pdf:
            for index, page in enumerate(pdf.pages, start=1):
                text = (page.extract_text() or "").strip()
                if not text:
                    continue
                pages.append(f"## Page {index}\n\n{text}")
        return "\n\n".join(pages).strip()

    def _ocr_to_markdown(self, path: Path) -> str:
        from pdf2image import convert_from_path
        import pytesseract

        if config.TESSERACT_CMD:
            pytesseract.pytesseract.tesseract_cmd = config.TESSERACT_CMD

        pages = convert_from_path(str(path), dpi=self.ocr_dpi)
        markdown_pages: list[str] = []
        for index, page in enumerate(pages, start=1):
            text = pytesseract.image_to_string(page, config="--oem 3 --psm 6").strip()
            if not text:
                continue
            markdown_pages.append(f"## Page {index}\n\n{text}")
        return "\n\n".join(markdown_pages).strip()

    @staticmethod
    def _looks_useful(markdown: str) -> bool:
        text = (markdown or "").strip()
        alnum_count = sum(1 for char in text if char.isalnum())
        return alnum_count >= 50

    @staticmethod
    def _markdown_to_text(markdown: str) -> str:
        lines = []
        for raw_line in (markdown or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            line = line.lstrip("#").strip()
            if line:
                lines.append(line)
        return "\n".join(lines).strip()
