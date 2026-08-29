"""
Monthly ingest of the Montana ODC Public Discipline List PDF.

Source: https://montanaodc.org/attorney-discipline
PDF drops monthly with a date-stamped filename, e.g.
    2026.07.24 Website Public Discipline.pdf

The page HTML contains a direct Download link to the current PDF. This script:
  1. Visits the page and extracts the PDF URL.
  2. Parses the filename date stamp to identify the version (e.g. 2026.07.24).
  3. Skips if that version is already in odc_discipline.pdf_version.
  4. Downloads the PDF, extracts structured rows via pdfplumber word-position
     column parsing, and upserts into odc_discipline.

Run monthly via cron (see crontab.txt). Safe to run ad-hoc with --dry-run.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from datetime import datetime, timezone
from urllib.parse import urljoin

import pdfplumber
import requests

logger = logging.getLogger("odc_discipline")
_HOMEPAGE = "https://montanaodc.org/attorney-discipline"
_PDF_HOST = "img1.wsimg.com"  # the PDF is hosted on a WebStringer blob endpoint

# ---------------------------------------------------------------------------
# Column boundaries (points, from pdfplumber word extraction on the live PDF)
#   name:    x0 < 180
#   cause:  180 <= x0 < 275
#   disc:   275 <= x0 < 490
#   date:   x0 >= 490
# These were derived from the July 2026 PDF and should hold for future drops
# unless ODC renames the layout. If parsing starts missing rows, re-derive
# by inspecting extract_words() on the new PDF.
# ---------------------------------------------------------------------------
_NAME_MAX_X0 = 180
_CAUSE_MAX_X0 = 275
_DISC_MAX_X0 = 490

_DATE_RE = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{2,4})\s*$")


def _parse_date(s: str) -> str | None:
    m = _DATE_RE.match(s)
    if not m:
        return None
    mon, day, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if yr < 100:
        yr += 2000
    try:
        return datetime(yr, mon, day).strftime("%Y-%m-%d")
    except ValueError:
        return None


def _derive_pdf_url() -> str | None:
    """Hit the ODC attorney-discipline page and extract the Download link."""
    try:
        resp = requests.get(_HOMEPAGE, timeout=30,
                            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"})
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Failed to fetch ODC page: %s", exc)
        return None

    html = resp.text
    # The download link contains "Public Discipline.pdf" on img1.wsimg.com.
    # The date stamp 2026.07.24 is embedded in the filename; extract it from the URL.
    m = re.search(
        r'href="(https?://img1\.wsimg\.com/blobby/go/[^"]*?20\d{2}\.\d{2}\.\d{2}[^"]*?Discipline\.pdf[^"]*)"',
        html,
    )
    if not m:
        # Fallback: protocol-relative URL
        m = re.search(
            r'href="(//img1\.wsimg\.com/blobby/go/[^"]*?20\d{2}\.\d{2}\.\d{2}[^"]*?Discipline\.pdf[^"]*)"',
            html,
        )
    if not m:
        logger.error("Could not find PDF download link on ODC page")
        return None
    raw_url = m.group(1)
    if raw_url.startswith("//"):
        raw_url = "https:" + raw_url
    # Extract the date-stamp version directly from the URL.
    vm = re.search(r"(20\d{2}\.\d{2}\.\d{2})", raw_url)
    if not vm:
        logger.error("Found PDF link but could not extract version from: %s", raw_url)
        return None
    return raw_url, vm.group(1)


def _extract_pdf_version_from_url(pdf_url: str) -> str | None:
    # The filename is date-stamped: "2026.07.24 Website Public Discipline.pdf"
    # In the URL it may appear as "%20" or literal spaces.
    m = re.search(r"(\d{4}\.\d{2}\.\d{2})", pdf_url)
    return m.group(1) if m else None


def _parse_pdf(path: str) -> list[dict]:
    """Return a list of row dicts from the ODC discipline PDF.

    Uses pdfplumber word-position columns to separate the four visual fields
    (name, cause no., discipline, date ordered) even when discipline text spans
    multiple lines.
    """
    entries = []
    with pdfplumber.open(path) as pdf:
        for pi, page in enumerate(pdf.pages):
            if pi == 0:  # intro page
                continue
            words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
            if not words:
                continue

            # Group words into visual lines by top position.
            lines: list[tuple[float, list]] = []
            cur_line: list = []
            cur_top: float | None = None
            for w in words:
                if cur_top is None or abs(w["top"] - cur_top) < 8:
                    cur_line.append(w)
                    if cur_top is None:
                        cur_top = w["top"]
                else:
                    lines.append((cur_top, cur_line))
                    cur_line = [w]
                    cur_top = w["top"]
            if cur_line:
                lines.append((cur_top, cur_line))

            # Skip header lines.
            _HEADER_TEXTS = {
                "SUPREME DATE ATTORNEY NAME COURT FORM OF DISCIPLINE ORDERED CAUSE NO.",
                "SUPREME",
                "DATE",
                "ATTORNEY NAME COURT FORM OF DISCIPLINE",
                "ORDERED",
                "CAUSE NO.",
            }
            _SKIP_FRAGMENTS = (
                "The following", "Although", "Please note", "State Bar of",
                "P.O. Box", "Helena, MT", "www.montanabar", "Copyright",
                "Powered by", "Home", "Filing a", "Resources", "Annual Reports",
                "FAQ",
            )

            for _top, line_words in lines:
                text = " ".join(w["text"] for w in line_words)
                if text in _HEADER_TEXTS:
                    continue
                if any(frag in text for frag in _SKIP_FRAGMENTS):
                    continue

                name_w, cause_w, disc_w, date_w = [], [], [], []
                for w in line_words:
                    x0 = w["x0"]
                    if x0 < _NAME_MAX_X0:
                        name_w.append(w["text"])
                    elif x0 < _CAUSE_MAX_X0:
                        cause_w.append(w["text"])
                    elif x0 < _DISC_MAX_X0:
                        disc_w.append(w["text"])
                    else:
                        date_w.append(w["text"])

                name = " ".join(name_w).strip()
                cause_raw = " ".join(cause_w).strip()
                disc = " ".join(disc_w).strip()
                date_raw = " ".join(date_w).strip()

                # Continuation line: no name, no cause, just discipline text.
                if not name and not cause_raw:
                    if entries and disc:
                        entries[-1]["discipline"] += " " + disc
                        if date_raw:
                            parsed = _parse_date(date_raw)
                            if parsed:
                                entries[-1]["date_ordered"] = parsed
                    continue

                if not name or not cause_raw:
                    continue

                parsed_date = _parse_date(date_raw) if date_raw else None

                entries.append({
                    "attorney_name": name,
                    "cause_no": cause_raw,
                    "discipline": disc,
                    "date_ordered": parsed_date or date_raw or None,
                })

    return entries


def run_monthly(dry_run: bool = False, pdf_url: str | None = None,
                pdf_version: str | None = None) -> dict:
    """Download and ingest the latest ODC discipline PDF.

    Returns a summary dict suitable for logging / cron output.
    """
    # --- resolve source --------------------------------------------------------
    if pdf_url and pdf_version:
        url = pdf_url
        version = pdf_version
    else:
        resolved = _derive_pdf_url()
        if not resolved:
            return {"error": "could not resolve PDF URL from ODC page"}
        url, version = resolved

    logger.info("ODC discipline PDF version=%s url=%s", version, url)

    # --- check whether we already have this version -----------------------------
    # Ensure the project root is on the path so init_db is importable.
    _proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, _proj_root)

    import sqlite3
    from init_db import DB_PATH

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM odc_discipline WHERE pdf_version = ?", (version,)
        ).fetchone()
        if row["cnt"] > 0:
            logger.info("Version %s already ingested (%d rows) — skipping", version, row["cnt"])
            return {"skipped": True, "version": version, "existing_rows": row["cnt"]}
    finally:
        conn.close()

    # --- download ---------------------------------------------------------------
    if dry_run:
        logger.info("DRY RUN — would download %s", url)
        return {"dry_run": True, "version": version, "url": url}

    tmp_path = f"/tmp/odc_discipline_{version.replace('.', '_')}.pdf"
    try:
        resp = requests.get(url, timeout=60, stream=True,
                            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"})
        resp.raise_for_status()
        with open(tmp_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=65536):
                fh.write(chunk)
        logger.info("Downloaded PDF to %s (%d bytes)", tmp_path, os.path.getsize(tmp_path))
    except requests.RequestException as exc:
        logger.error("Failed to download PDF: %s", exc)
        return {"error": f"download failed: {exc}"}

    # --- parse -----------------------------------------------------------------
    try:
        rows = _parse_pdf(tmp_path)
    except Exception as exc:
        logger.exception("PDF parse failed: %s", exc)
        return {"error": f"parse failed: {exc}"}
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    if not rows:
        logger.warning("Parsed 0 rows from PDF — possible layout change")
        return {"error": "parsed 0 rows", "version": version}

    logger.info("Parsed %d rows from PDF v%s", len(rows), version)

    # --- upsert ----------------------------------------------------------------
    fetched_at = datetime.now(timezone.utc).isoformat()
    source_url = url

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        inserted = 0
        skipped = 0
        for r in rows:
            try:
                conn.execute(
                    """INSERT INTO odc_discipline
                       (attorney_name, cause_no, discipline, date_ordered, pdf_version,
                        source_url, fetched_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        r["attorney_name"],
                        r["cause_no"],
                        r["discipline"],
                        r["date_ordered"],
                        version,
                        source_url,
                        fetched_at,
                    ),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                # Unique index on (pdf_version, attorney_name, cause_no) — already present
                skipped += 1
        conn.commit()
    finally:
        conn.close()

    logger.info("Ingested %d new rows (+%d skipped as duplicates) for v%s",
                inserted, skipped, version)
    return {
        "version": version,
        "total_rows": len(rows),
        "inserted": inserted,
        "skipped": skipped,
        "source_url": source_url,
        "fetched_at": fetched_at,
    }


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    dry_run = "--dry-run" in sys.argv
    custom_url = None
    custom_version = None
    for i, arg in enumerate(sys.argv):
        if arg == "--url" and i + 1 < len(sys.argv):
            custom_url = sys.argv[i + 1]
        if arg == "--version" and i + 1 < len(sys.argv):
            custom_version = sys.argv[i + 1]

    result = run_monthly(dry_run=dry_run, pdf_url=custom_url,
                         pdf_version=custom_version)

    if "error" in result:
        print(f"FAIL: {result['error']}")
        return 1

    if result.get("skipped") is True:
        print(f"SKIP: version {result['version']} already present ({result['existing_rows']} rows)")
        return 0

    if result.get("dry_run"):
        print(f"DRY RUN: would ingest v{result['version']} from {result['url']} "
              f"(~{result.get('total_rows', '?')} rows)")
        return 0

    print(f"OK: ingested v{result['version']} — "
          f"{result['inserted']} new / {result['skipped']} dup / "
          f"{result['total_rows']} total rows "
          f"from {result['source_url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
