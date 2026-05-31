"""
process_stuck_pdfs.py — One-shot script to process all PDFs stuck in uploads/.

Run as: cd /root/montanablotter && python3 scripts/ops/process_stuck_pdfs.py

Classifies each PDF by county (from filename + text), runs it through
process_new_blotter(), and reports results. Does NOT delete source files.
"""
from __future__ import annotations

import glob
import logging
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import config  # noqa: E402
from services.blotter.processor import process_new_blotter  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("stuck_pdf_processor")

UPLOADS_DIR = "/root/montanablotter/uploads"

FILENAME_COUNTY_MAP: list[tuple[re.Pattern, str]] = [
    (re.compile(r"whitehall|jeffco|jefferson.*cfs|cfs.*jefferson|central.*jeffco|north.*jeffco", re.IGNORECASE), "Jefferson"),
    (re.compile(r"whitefish", re.IGNORECASE), "Flathead"),
    (re.compile(r"havre|hill[_\-.]?co", re.IGNORECASE), "Hill"),
    (re.compile(r"missoula", re.IGNORECASE), "Missoula"),
    (re.compile(r"billings", re.IGNORECASE), "Yellowstone"),
    (re.compile(r"great[_\-.]?falls|cascade|gfpd", re.IGNORECASE), "Cascade"),
    (re.compile(r"gallatin|bozeman|gcso", re.IGNORECASE), "Gallatin"),
    (re.compile(r"flathead|kalispell", re.IGNORECASE), "Flathead"),
]


def _county_from_filename(filename: str) -> str | None:
    for pattern, county in FILENAME_COUNTY_MAP:
        if pattern.search(filename):
            return county
    return None


def main() -> None:
    pdfs = sorted(glob.glob(os.path.join(UPLOADS_DIR, "*.pdf")))
    if not pdfs:
        log.info("No PDFs found in uploads/")
        return

    log.info(f"Found {len(pdfs)} PDFs to process")

    results: dict[str, list[str]] = {"ok": [], "duplicate": [], "error": []}

    for i, pdf_path in enumerate(pdfs, 1):
        filename = os.path.basename(pdf_path)
        county_hint = _county_from_filename(filename)
        log.info(f"[{i}/{len(pdfs)}] {filename} (county: {county_hint or 'auto-detect'})")

        try:
            blotter_id = process_new_blotter(pdf_path, county=county_hint)
            if blotter_id == 0:
                log.info(f"  → duplicate — all incidents already ingested")
                results["duplicate"].append(filename)
            else:
                log.info(f"  → ingested: blotter_id={blotter_id}")
                results["ok"].append(filename)
        except Exception as exc:
            log.error(f"  → FAILED: {exc}")
            results["error"].append(f"{filename}: {exc}")

        time.sleep(0.3)

    log.info("=" * 60)
    log.info(f"DONE: {len(pdfs)} PDFs processed")
    log.info(f"  Ingested:   {len(results['ok'])}")
    log.info(f"  Duplicates: {len(results['duplicate'])}")
    log.info(f"  Errors:     {len(results['error'])}")
    if results["error"]:
        log.info("Errors:")
        for e in results["error"]:
            log.info(f"  - {e}")


if __name__ == "__main__":
    main()
