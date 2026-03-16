from __future__ import annotations

import hashlib
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
