"""
Madison County Sheriff's Office Monthly Activities Report Scraper
Sources: https://madisoncountymt.gov/684/Sheriffs-Office-Monthly-Activities-Report

Madison County publishes one-page summary PDFs each month showing:
- Major incidents by category
- Citations by category
- Warnings by category
- Administrative stats (civil, fingerprints, CWP, records, warrants)
- Detention stats (avg inmates, arrests, transports)
- Total calls for service

These are image-based PDFs; we OCR them with pytesseract.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db

logger = logging.getLogger("madison_county_monthly")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
    logger.addHandler(handler)

HEADERS = {
    "User-Agent": "MontanaBlotterBot/1.0 (public records aggregation)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

BASE_URL = "https://madisoncountymt.gov"
INDEX_URL = "https://madisoncountymt.gov/684/Sheriffs-Office-Monthly-Activities-Report"

DB_PATH = os.getenv("MB_DB_PATH", "/root/montanablotter/blotter.db")

# Lazy imports for OCR (only loaded when needed)
_pdf2image = None
_pytesseract = None


def _ensure_ocr():
    global _pdf2image, _pytesseract
    if _pdf2image is None:
        try:
            import pdf2image as pi
            _pdf2image = pi
        except ImportError as exc:
            raise ImportError("pdf2image is required for Madison County scraper") from exc
    if _pytesseract is None:
        try:
            import pytesseract as pt
            _pytesseract = pt
        except ImportError as exc:
            raise ImportError("pytesseract is required for Madison County scraper") from exc


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class MadisonMonthlyReport:
    year: int
    month: int
    period_start: str | None
    period_end: str | None
    total_calls: int | None
    arrests: int | None
    avg_inmates: float | None
    transports: int | None
    warrants: int | None
    pdf_url: str
    raw_ocr_text: str
    parsed_categories: list[dict] = field(default_factory=list)

    def fingerprint(self) -> str:
        payload = f"madison-county|{self.year}|{self.month}|{self.pdf_url}"
        return hashlib.sha256(payload.encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def ensure_schema(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS madison_county_monthly (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            year INTEGER NOT NULL,
            month INTEGER NOT NULL,
            period_start TEXT,
            period_end TEXT,
            total_calls INTEGER,
            arrests INTEGER,
            avg_inmates REAL,
            transports INTEGER,
            warrants INTEGER,
            pdf_url TEXT NOT NULL,
            fingerprint TEXT NOT NULL UNIQUE,
            raw_ocr_text TEXT,
            parsed_categories_json TEXT,
            published_to_blog INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_madison_year_month ON madison_county_monthly(year, month)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_madison_period ON madison_county_monthly(period_start, period_end)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_madison_published ON madison_county_monthly(published_to_blog)"
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Listing / discovery
# ---------------------------------------------------------------------------
def discover_reports() -> list[dict]:
    """Scrape the index page for links to monthly PDF reports."""
    logger.info("Fetching report index: %s", INDEX_URL)
    try:
        resp = requests.get(INDEX_URL, headers=HEADERS, timeout=60)
        resp.raise_for_status()
    except Exception as exc:
        logger.error("Failed to fetch index: %s", exc)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    reports = []

    for link in soup.find_all("a", href=True):
        href = link["href"]
        text = link.get_text(strip=True)
        # Match URLs like /DocumentCenter/View/16207/MCSO-May-Activities-Report-2026
        m = re.search(r"/DocumentCenter/View/(\d+)/MCSO-([A-Za-z]+)-Activities-Report-(\d{4})", href)
        if m:
            doc_id, month_name, year = m.groups()
            month_num = _month_name_to_num(month_name)
            if month_num:
                pdf_url = urljoin(BASE_URL, href)
                reports.append({
                    "doc_id": doc_id,
                    "year": int(year),
                    "month": month_num,
                    "month_name": month_name,
                    "pdf_url": pdf_url,
                    "label": text,
                })

    # Also catch the non-MCSO-prefixed ones like "December Activities Report 2025"
    for link in soup.find_all("a", href=True):
        href = link["href"]
        text = link.get_text(strip=True)
        m = re.search(r"/DocumentCenter/View/(\d+)/", href)
        if m and not any(r["doc_id"] == m.group(1) for r in reports):
            doc_id = m.group(1)
            # Try to infer month/year from link text
            month_num, year = _infer_month_year(text)
            if month_num and year:
                pdf_url = urljoin(BASE_URL, href)
                reports.append({
                    "doc_id": doc_id,
                    "year": year,
                    "month": month_num,
                    "month_name": text,
                    "pdf_url": pdf_url,
                    "label": text,
                })

    # Deduplicate by year+month (keep newest doc_id if duplicates)
    seen = {}
    for r in sorted(reports, key=lambda x: int(x["doc_id"])):
        key = (r["year"], r["month"])
        seen[key] = r

    result = list(seen.values())
    logger.info("Discovered %d unique monthly reports", len(result))
    return result


def _month_name_to_num(name: str) -> int | None:
    mapping = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
    }
    return mapping.get(name.lower().strip())


def _infer_month_year(text: str) -> tuple[int | None, int | None]:
    month_num = None
    for name, num in {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
    }.items():
        if name in text.lower():
            month_num = num
            break
    year_m = re.search(r"\b(20\d{2})\b", text)
    year = int(year_m.group(1)) if year_m else None
    return month_num, year


# ---------------------------------------------------------------------------
# PDF download + OCR
# ---------------------------------------------------------------------------
def _fetch_pdf(url: str) -> bytes | None:
    """Download PDF. Madison County DocumentCenter returns PDF on GET even if HEAD 404s."""
    logger.info("Downloading PDF: %s", url)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=120)
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "").lower()
        if resp.content.startswith(b"%PDF"):
            return resp.content
        if "pdf" in content_type:
            return resp.content
        # Some months publish Word docs instead of PDFs; skip them for now.
        if "wordprocessingml" in content_type or resp.content.startswith(b"PK"):
            logger.warning("Skipping non-PDF document at %s (content-type: %s)", url, content_type)
            return None
        logger.warning("Unexpected content-type for %s: %s", url, content_type)
        # Still try if it starts like a PDF
        if resp.content[:4] == b"%PDF":
            return resp.content
        return None
    except Exception as exc:
        logger.error("Failed to download PDF %s: %s", url, exc)
        return None


def _ocr_pdf(pdf_bytes: bytes) -> str:
    """Convert PDF pages to images and OCR with tesseract."""
    _ensure_ocr()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name

    try:
        images = _pdf2image.convert_from_path(tmp_path, dpi=300)
        all_text = []
        for img in images:
            # Madison County PDFs are landscape letter rotated 270 degrees.
            # Rotating -90 (clockwise) puts text in readable orientation.
            img_rotated = img.rotate(-90, expand=True)
            text = _pytesseract.image_to_string(img_rotated)
            all_text.append(text)
        return "\n".join(all_text)
    finally:
        os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------
def _extract_total_calls(text: str) -> int | None:
    m = re.search(r"Total\s+Calls\s+For\s+Service[:\s]*(\d+)", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


def _extract_arrests(text: str) -> int | None:
    m = re.search(r"Arrests[:\s]*(\d+)", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    # Fallback: look for a standalone number just before "Total Calls For Service"
    m = re.search(r"\n\s*(\d+)\s*\n\s*Total Calls For Service", text, re.IGNORECASE)
    if m:
        val = int(m.group(1))
        # Sanity check: arrests should be a reasonable number (< 500)
        if 0 < val < 500:
            return val
    return None


def _extract_avg_inmates(text: str) -> float | None:
    m = re.search(r"Average\s*#?\s*of\s*Inmates[:\s]*([\d.]+)", text, re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None


def _extract_transports(text: str) -> int | None:
    m = re.search(r"Transports[:\s]*(\d+)", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


def _extract_warrants(text: str) -> int | None:
    m = re.search(r"Warrants[:\s]*(\d+)", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


def _extract_period(text: str) -> tuple[str | None, str | None]:
    """Extract date range like 'April 1, 2026 to April 30, 2026'."""
    m = re.search(
        r"([A-Z][a-z]+ \d{1,2},? \d{4})\s+(?:to|through)\s+([A-Z][a-z]+ \d{1,2},? \d{4})",
        text,
    )
    if m:
        return m.group(1), m.group(2)
    # Try another pattern: "from April 1, 2026 to April 30, 2026"
    m = re.search(
        r"from\s+([A-Z][a-z]+ \d{1,2},? \d{4})\s+(?:to|through)\s+([A-Z][a-z]+ \d{1,2},? \d{4})",
        text,
        re.IGNORECASE,
    )
    if m:
        return m.group(1), m.group(2)
    return None, None


def _extract_categories(text: str) -> list[dict]:
    """
    Extract category-count pairs from OCR text, handling two-column layouts.
    This is best-effort due to OCR quality variations.
    """
    categories = []
    lines = text.splitlines()
    section_headers = ["Major Incidents", "Citations", "Warnings", "Administrative", "Detention"]

    # First pass: identify which lines contain section headers and whether they have 1 or 2 headers
    line_sections = []
    current_section = None
    two_column_active = False
    col_sections = [None, None]  # [left_section, right_section]

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            line_sections.append(None)
            continue

        # Detect section headers on this line
        found_headers = []
        for header in section_headers:
            if header.lower() in stripped.lower():
                found_headers.append(header)

        if found_headers:
            if len(found_headers) >= 2:
                # Two-column layout: e.g., "Major Incidents Warnings"
                two_column_active = True
                col_sections = found_headers[:2]
                current_section = None
            else:
                # Single-column layout
                two_column_active = False
                current_section = found_headers[0]
                col_sections = [current_section, None]
            line_sections.append(col_sections.copy())
        else:
            line_sections.append(None)

    # Second pass: extract category-count pairs from each line
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        ls = line_sections[i]
        if ls is not None:
            # This is a header line; skip category extraction
            continue

        # Find the nearest preceding section assignment
        col_sections = [None, None]
        for j in range(i - 1, -1, -1):
            if line_sections[j] is not None:
                col_sections = line_sections[j]
                break

        if not any(col_sections):
            continue

        # Remove common noise
        clean = stripped
        for header in section_headers:
            clean = re.sub(re.escape(header), "", clean, flags=re.IGNORECASE).strip()
        if not clean:
            continue

        # Find all numbers on the line
        numbers = list(re.finditer(r"\b(\d+(?:\.\d+)?)\b", clean))
        if not numbers:
            continue

        # If two-column mode is active and we have 2+ numbers, try to split
        left_section, right_section = col_sections
        is_two_col = left_section and right_section

        if is_two_col and len(numbers) >= 2:
            # Split at the middle number position
            mid_idx = len(numbers) // 2
            split_pos = numbers[mid_idx].start()
            left_text = clean[:split_pos].strip()
            right_text = clean[split_pos:].strip()

            for section, segment in [(left_section, left_text), (right_section, right_text)]:
                if not section:
                    continue
                seg_nums = list(re.finditer(r"\b(\d+(?:\.\d+)?)\b", segment))
                if not seg_nums:
                    continue
                # Category is text before the last number in the segment
                last_num = seg_nums[-1]
                cat_text = segment[:last_num.start()].strip()
                count_str = last_num.group(1)
                if _is_valid_category(cat_text):
                    count = float(count_str) if "." in count_str else int(count_str)
                    categories.append({
                        "section": section,
                        "category": cat_text,
                        "count": count,
                    })
        else:
            # Single-column: take the last number as the count, everything before as category
            last_num = numbers[-1]
            cat_text = clean[:last_num.start()].strip()
            count_str = last_num.group(1)
            section = left_section or right_section
            if section and _is_valid_category(cat_text):
                count = float(count_str) if "." in count_str else int(count_str)
                categories.append({
                    "section": section,
                    "category": cat_text,
                    "count": count,
                })

    return categories


def _is_valid_category(text: str) -> bool:
    """Filter out false positives like dates, addresses, totals, and noise."""
    if len(text) < 3:
        return False
    if re.match(r"^(?:20\d{2}|MT|PO|Box|Phone|Virginia|Sheriff|Activity|Summary)\b", text, re.IGNORECASE):
        return False
    # Skip totals and summary lines
    if re.search(r"Total\s+Calls|Calls\s+For\s+Service", text, re.IGNORECASE):
        return False
    # Skip lines that are mostly numbers or special chars
    alpha_ratio = sum(1 for c in text if c.isalpha()) / len(text)
    if alpha_ratio < 0.5:
        return False
    # Skip if no real words (at least 2 consecutive letters)
    if not re.search(r"[A-Za-z]{2,}", text):
        return False
    # Skip lines that look like partial OCR garbage
    if re.search(r"\b[a-z]\s+[A-Z]\b", text) and len(text) < 15:
        return False
    return True


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------
def ingest_report(report_info: dict, conn: sqlite3.Connection) -> bool:
    pdf_bytes = _fetch_pdf(report_info["pdf_url"])
    if not pdf_bytes:
        return False

    raw_text = _ocr_pdf(pdf_bytes)
    if not raw_text or not raw_text.strip():
        logger.warning("OCR produced no text for %s", report_info["pdf_url"])
        return False

    period_start, period_end = _extract_period(raw_text)
    total_calls = _extract_total_calls(raw_text)
    arrests = _extract_arrests(raw_text)
    avg_inmates = _extract_avg_inmates(raw_text)
    transports = _extract_transports(raw_text)
    warrants = _extract_warrants(raw_text)
    categories = _extract_categories(raw_text)

    rec = MadisonMonthlyReport(
        year=report_info["year"],
        month=report_info["month"],
        period_start=period_start,
        period_end=period_end,
        total_calls=total_calls,
        arrests=arrests,
        avg_inmates=avg_inmates,
        transports=transports,
        warrants=warrants,
        pdf_url=report_info["pdf_url"],
        raw_ocr_text=raw_text,
        parsed_categories=categories,
    )

    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO madison_county_monthly (
            year, month, period_start, period_end, total_calls, arrests,
            avg_inmates, transports, warrants, pdf_url, fingerprint,
            raw_ocr_text, parsed_categories_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(fingerprint) DO UPDATE SET
            period_start=excluded.period_start,
            period_end=excluded.period_end,
            total_calls=excluded.total_calls,
            arrests=excluded.arrests,
            avg_inmates=excluded.avg_inmates,
            transports=excluded.transports,
            warrants=excluded.warrants,
            raw_ocr_text=excluded.raw_ocr_text,
            parsed_categories_json=excluded.parsed_categories_json,
            updated_at=datetime('now')
        """,
        (
            rec.year,
            rec.month,
            rec.period_start,
            rec.period_end,
            rec.total_calls,
            rec.arrests,
            rec.avg_inmates,
            rec.transports,
            rec.warrants,
            rec.pdf_url,
            rec.fingerprint(),
            rec.raw_ocr_text,
            json.dumps(categories, ensure_ascii=False),
        ),
    )
    conn.commit()
    logger.info(
        "Ingested Madison County %04d-%02d: calls=%s arrests=%s categories=%d",
        rec.year,
        rec.month,
        rec.total_calls,
        rec.arrests,
        len(categories),
    )
    return True


# ---------------------------------------------------------------------------
# Blog post generation
# ---------------------------------------------------------------------------
def generate_blog_post_body(report: dict) -> str:
    """Generate a markdown blog post body from a Madison County monthly report."""
    year = report["year"]
    month = report["month"]
    period_start = report.get("period_start", f"{year}-{month:02d}-01")
    period_end = report.get("period_end", f"{year}-{month:02d}-28")
    total_calls = report.get("total_calls")
    arrests = report.get("arrests")
    avg_inmates = report.get("avg_inmates")
    transports = report.get("transports")
    warrants = report.get("warrants")
    categories_json = report.get("parsed_categories_json", "[]")

    try:
        categories = json.loads(categories_json) if categories_json else []
    except Exception:
        categories = []

    month_name = datetime(year, month, 1).strftime("%B")

    lines = [
        f"# Madison County Sheriff's Office — {month_name} {year} Activity Summary",
        "",
        f"**Period:** {period_start} to {period_end}",
        f"**Agency:** Madison County Sheriff's Office (Virginia City, MT)",
        f"**Source:** [Monthly Activities Report]({report['pdf_url']})",
        "",
        "---",
        "",
        "## Overview",
        "",
    ]

    if total_calls is not None:
        lines.append(f"- **Total Calls For Service:** {total_calls:,}")
    if arrests is not None:
        lines.append(f"- **Arrests:** {arrests}")
    if avg_inmates is not None:
        lines.append(f"- **Average Daily Inmates:** {avg_inmates}")
    if transports is not None:
        lines.append(f"- **Inmate Transports:** {transports}")
    if warrants is not None:
        lines.append(f"- **Warrants Served:** {warrants}")

    lines.append("")

    # Group categories by section
    sections = {}
    for cat in categories:
        sec = cat.get("section", "Other")
        sections.setdefault(sec, []).append(cat)

    section_order = ["Major Incidents", "Citations", "Warnings", "Administrative", "Detention"]
    for sec in section_order:
        if sec not in sections:
            continue
        lines.append(f"## {sec}")
        lines.append("")
        items = sections[sec]
        # Sort by count descending
        items.sort(key=lambda x: x.get("count", 0), reverse=True)
        for item in items:
            name = item.get("category", "Unknown")
            count = item.get("count", 0)
            lines.append(f"- **{name}:** {count}")
        lines.append("")

    lines.extend([
        "---",
        "",
        "*This report is based on the Madison County Sheriff's Office monthly activity summary, extracted via OCR. Some counts may be approximate due to source formatting. For official records, contact the Madison County Sheriff's Office directly.*",
        "",
        "---",
        "",
        "## About Montana Blotter",
        "",
        "Montana Blotter makes public records easier to access. We are not a government office and do not replace official records. [Read our standards](/standards).",
    ])

    return "\n".join(lines)


def publish_unpublished_posts(conn: sqlite3.Connection) -> int:
    """Create blog posts for any Madison County monthly reports not yet published."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, year, month, period_start, period_end, total_calls, arrests,
               avg_inmates, transports, warrants, pdf_url, parsed_categories_json
        FROM madison_county_monthly
        WHERE published_to_blog = 0
        ORDER BY year, month
        """
    )
    rows = cursor.fetchall()
    if not rows:
        logger.info("No unpublished Madison County reports to blog")
        return 0

    published = 0
    for row in rows:
        report = {
            "id": row[0],
            "year": row[1],
            "month": row[2],
            "period_start": row[3],
            "period_end": row[4],
            "total_calls": row[5],
            "arrests": row[6],
            "avg_inmates": row[7],
            "transports": row[8],
            "warrants": row[9],
            "pdf_url": row[10],
            "parsed_categories_json": row[11],
        }

        body = generate_blog_post_body(report)
        month_name = datetime(report["year"], report["month"], 1).strftime("%B")
        title = f"Madison County Sheriff — {month_name} {report['year']} Activity Report"
        slug = f"madison-county-sheriff-{report['year']}-{report['month']:02d}-activity"
        excerpt = f"Madison County Sheriff's Office handled {report.get('total_calls', 'N/A')} calls for service in {month_name} {report['year']}."

        cursor.execute(
            """
            INSERT INTO blog_posts (title, slug, body, excerpt, author, published)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET
                body=excluded.body,
                excerpt=excluded.excerpt,
                updated_at=datetime('now')
            """,
            (title, slug, body, excerpt, "Montana Blotter", 1),
        )
        cursor.execute(
            "UPDATE madison_county_monthly SET published_to_blog = 1 WHERE id = ?",
            (report["id"],),
        )
        conn.commit()
        logger.info("Published blog post: %s", slug)
        published += 1

    logger.info("Published %d Madison County blog posts", published)
    return published


# ---------------------------------------------------------------------------
# CLI / main entry point
# ---------------------------------------------------------------------------
def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Madison County monthly report scraper")
    parser.add_argument("--publish", action="store_true", help="Generate blog posts for unpublished reports")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be published without writing")
    args = parser.parse_args()

    conn = db.connect_db()
    ensure_schema(conn)

    # Run ingestion
    reports = discover_reports()
    if reports:
        success = 0
        for report in reports:
            cursor = conn.cursor()
            fp = hashlib.sha256(
                f"madison-county|{report['year']}|{report['month']}|{report['pdf_url']}".encode()
            ).hexdigest()[:32]
            cursor.execute(
                "SELECT id FROM madison_county_monthly WHERE fingerprint = ?",
                (fp,),
            )
            if cursor.fetchone():
                continue
            if ingest_report(report, conn):
                success += 1
        logger.info("Ingested %d new Madison County reports", success)

    # Run publishing
    if args.publish or args.dry_run:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, year, month, period_start, period_end, total_calls, arrests,
                   avg_inmates, transports, warrants, pdf_url, parsed_categories_json
            FROM madison_county_monthly
            WHERE published_to_blog = 0
            ORDER BY year, month
            """
        )
        rows = cursor.fetchall()
        logger.info("Found %d unpublished reports", len(rows))
        if args.dry_run:
            for row in rows:
                logger.info("Would publish: %04d-%02d (%s calls)", row[1], row[2], row[5])
        else:
            publish_unpublished_posts(conn)

    conn.close()


if __name__ == "__main__":
    main()
