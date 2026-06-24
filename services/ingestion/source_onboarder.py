#!/usr/bin/env python3
"""
source_onboarder.py
===================
Agent 3 of 3 in the autonomous source-discovery pipeline.

Runs daily at 8:00am MT. Pulls approved candidates from source_candidates,
uses Claude to generate a complete fetcher.py following existing patterns,
writes it to services/ingestion/fetchers/, appends the cron entry to
configs/pending_cron_entries.txt, and registers the source in source_registry.

Once registered, the normal pipeline (blotters -> records -> posts ->
_publish_blotter_outputs) handles all publishing automatically:
  - Web: montanablotter.com posts
  - Email: digest/subscriber alerts
  - Facebook: fb-page-manager.service auto-posts

Cron (add to crontab.txt):
  0 8 * * * TZ=America/Denver /root/montanablotter/venv/bin/python3 \\
      /root/montanablotter/job_runner.py --name source_onboarder \\
      --log /root/montanablotter/logs/source_onboarder.log \\
      --workdir /root/montanablotter -- \\
      /root/montanablotter/venv/bin/python3 -m services.ingestion.source_onboarder
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import requests

import config
from services.agents.events_bus import publish_agent_event

try:
    import anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

DB_PATH = config.DB_PATH
BASE_DIR = Path(getattr(config, "BASE_DIR", "/root/montanablotter"))
FETCHERS_DIR = BASE_DIR / "services" / "ingestion" / "fetchers"
PENDING_CRON_PATH = BASE_DIR / "configs" / "pending_cron_entries.txt"
LOG_PATH = str(BASE_DIR / "logs" / "source_onboarder.log")
UA = "Mozilla/5.0 (compatible; MontanaBlotter-Onboarder/1.0; +https://montanablotter.com)"
MODEL = "claude-sonnet-4-6"
MAX_SAMPLE_CHARS = 8000

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("source_onboarder")

# Shown to Claude as a few-shot example — teaches it the exact DB/pipeline pattern
_FETCHER_TEMPLATE = textwrap.dedent("""\
    # EXAMPLE FETCHER (missoula.py) — follow this pattern exactly
    from __future__ import annotations
    import json, logging, re, sqlite3
    from datetime import datetime, timezone
    import requests
    import config
    from core.pipeline_state import (
        ensure_ingestion_job, ensure_source_document,
        get_ingestion_job_status, increment_ingestion_retry,
        log_pipeline_event, set_ingestion_job_status, sha256_text,
    )
    from services.blotter.processor import _publish_blotter_outputs

    logger = logging.getLogger(__name__)
    DEFAULT_URL = "https://example.com/roster"
    COUNTY = "ExampleCounty"
    SOURCE_TYPE = "example_source_type"
    SOURCE_SENDER = "Example Agency Public Roster"

    def _connect_db():
        conn = sqlite3.connect(config.DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _fetch_page(url, timeout=30):
        resp = requests.get(url, timeout=timeout,
            headers={"User-Agent": "MontanaBlotter/1.0"})
        resp.raise_for_status()
        return resp.text

    def _parse_records(html):
        # Returns list of dicts with keys:
        # date, time, incident_type, incident, location, details,
        # county, officer, cfs_number (empty string if N/A)
        raise NotImplementedError

    def ingest(url=DEFAULT_URL, dry_run=False):
        page = _fetch_page(url)
        records = _parse_records(page)
        if not records:
            return 0, 0
        payload = json.dumps({"source": SOURCE_TYPE, "records": records}, sort_keys=True)
        content_hash = sha256_text(payload)
        source_doc_id = ensure_source_document(
            source_type=SOURCE_TYPE, source_sender=SOURCE_SENDER,
            source_subject=f"{SOURCE_SENDER} {datetime.now(timezone.utc).date()}",
            source_received_at=datetime.now(timezone.utc).isoformat(),
            filename=None, content_sha256=content_hash,
            storage_path=url, raw_text=payload,
            extraction_method="html_scrape", extraction_warnings=[],
        )
        job_id = ensure_ingestion_job(source_doc_id)
        if get_ingestion_job_status(source_doc_id) == "published":
            return 0, 0
        conn = _connect_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO blotters (filename, county, incident_count, source_type, source_document_id)"
            " VALUES (?, ?, ?, ?, ?)",
            (f"{COUNTY.lower()}-roster-{datetime.now(timezone.utc).date()}",
             COUNTY, len(records), SOURCE_TYPE, source_doc_id),
        )
        blotter_id = cur.lastrowid
        for r in records:
            cur.execute(
                "INSERT INTO records (blotter_id, cfs_number, date, time, incident_type,"
                " incident, location, details, county, officer) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (blotter_id, r.get("cfs_number",""), r["date"], r.get("time",""),
                 r["incident_type"], r["incident"], r["location"],
                 r.get("details",""), COUNTY, r.get("officer","")),
            )
        conn.commit()
        conn.close()
        set_ingestion_job_status(job_id, "normalized")
        _publish_blotter_outputs(blotter_id, ingestion_job_id=job_id, label=COUNTY.lower())
        return blotter_id, len(records)

    def main():
        import argparse
        p = argparse.ArgumentParser()
        p.add_argument("--url", default=DEFAULT_URL)
        p.add_argument("--dry-run", action="store_true")
        args = p.parse_args()
        blotter_id, count = ingest(args.url, args.dry_run)
        print(f"blotter_id={blotter_id} records={count}")

    if __name__ == "__main__":
        main()
    # END EXAMPLE
""")

ONBOARD_SYSTEM = textwrap.dedent("""\
    You are a Python engineer building data ingestion scripts for Montana Blotter,
    a public-interest journalism site aggregating Montana law enforcement records.

    Write a COMPLETE, production-ready Python fetcher module for the given source.
    The script MUST:
    1. Follow the example pattern exactly (same imports, DB write, _publish_blotter_outputs)
    2. Implement _parse_records() to parse the specific HTML/data format in the page sample
    3. Set COUNTY, SOURCE_TYPE, SOURCE_SENDER, DEFAULT_URL correctly
    4. Handle network errors with retries (requests, timeout=30)
    5. Include main() with --url and --dry-run arguments

    For Zuercher portals: API endpoint is https://{agency}-mt.zuercherportal.com/api/inmates
    For PDFs: use PyMuPDF (fitz) if available, else note a TODO comment
    For HTML tables: use re/string parsing — do NOT import BeautifulSoup

    Output ONLY the Python source code — no markdown fences, no commentary outside code.
""")


def _fetch_sample(url: str) -> str:
    try:
        resp = requests.get(url, timeout=20, headers={"User-Agent": UA}, allow_redirects=True, stream=True)
        resp.raise_for_status()
        if "pdf" in resp.headers.get("content-type", "").lower():
            return f"[PDF at {url} — parse with PyMuPDF or pdfminer]"
        chunks: list[str] = []
        total = 0
        for chunk in resp.iter_content(chunk_size=4096, decode_unicode=True):
            if isinstance(chunk, bytes):
                chunk = chunk.decode("utf-8", errors="replace")
            chunks.append(chunk)
            total += len(chunk)
            if total >= MAX_SAMPLE_CHARS:
                break
        return "".join(chunks)[:MAX_SAMPLE_CHARS]
    except Exception as exc:
        return f"[fetch error: {exc}]"


def _slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text.lower().strip())
    return re.sub(r"[\s_-]+", "_", text)[:40].strip("_")


def _generate_fetcher(candidate: dict, sample: str) -> str:
    if not _ANTHROPIC_AVAILABLE:
        raise RuntimeError("anthropic package not available")
    if not getattr(config, "USE_PAID_LLM", False):
        raise RuntimeError("Paid LLM disabled via MB_USE_PAID_LLM")
    api_key = getattr(config, "ANTHROPIC_API_KEY", None)
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")

    review = {}
    if candidate.get("review_notes"):
        try:
            review = json.loads(candidate["review_notes"])
        except (json.JSONDecodeError, TypeError):
            pass

    user_msg = (
        f"URL: {candidate['url']}\n"
        f"County: {candidate.get('county', 'Unknown')}\n"
        f"Agency: {candidate.get('agency_name', 'Unknown Agency')}\n"
        f"Source format: {review.get('source_format', candidate.get('source_type', 'unknown'))}\n"
        f"Update frequency: {review.get('update_frequency', 'daily')}\n\n"
        f"EXAMPLE FETCHER TO FOLLOW:\n{_FETCHER_TEMPLATE}\n\n"
        f"PAGE SAMPLE:\n{sample}"
    )

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=ONBOARD_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    )
    code = response.content[0].text.strip()
    code = re.sub(r'^```python\s*', '', code, flags=re.M)
    code = re.sub(r'^```\s*$', '', code, flags=re.M)
    return code.strip()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _register_source(conn: sqlite3.Connection, candidate: dict, adapter_slug: str, poll_seconds: int) -> None:
    review = {}
    if candidate.get("review_notes"):
        try:
            review = json.loads(candidate["review_notes"])
        except (json.JSONDecodeError, TypeError):
            pass
    source_key = f"{_slugify(candidate.get('county', 'mt'))}_{adapter_slug}"
    conn.execute(
        """
        INSERT OR IGNORE INTO source_registry
            (source_key, source_type, display_name, base_url, adapter_name,
             poll_interval_seconds, is_enabled, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 0, ?, datetime('now'), datetime('now'))
        """,
        (
            source_key,
            review.get("source_format", candidate.get("source_type", "unknown")),
            candidate.get("agency_name", "Unknown Agency"),
            candidate["url"],
            adapter_slug,
            poll_seconds,
            f"Auto-onboarded. County: {candidate.get('county', '')}. "
            f"Fetcher: services/ingestion/fetchers/{adapter_slug}.py. "
            f"Set is_enabled=1 after manual verification.",
        ),
    )
    conn.commit()


def _append_cron_entry(agency_name: str, adapter_module: str, poll_seconds: int) -> None:
    PENDING_CRON_PATH.parent.mkdir(parents=True, exist_ok=True)
    if poll_seconds <= 3600:
        schedule, freq = "*/30 * * * *", "every 30 min"
    elif poll_seconds <= 7200:
        schedule, freq = "0 * * * *", "hourly"
    elif poll_seconds <= 14400:
        schedule, freq = "0 */2 * * *", "every 2h"
    elif poll_seconds <= 43200:
        schedule, freq = "0 */6 * * *", "every 6h"
    else:
        schedule, freq = "0 8 * * *", "daily"

    job_name = adapter_module.replace(".", "_")
    entry = (
        f"\n# {agency_name} — auto-onboarded by source_onboarder ({freq})\n"
        f"# REVIEW BEFORE ACTIVATING: verify the fetcher parses correctly with --dry-run\n"
        f"{schedule} /root/montanablotter/venv/bin/python3 "
        f"/root/montanablotter/job_runner.py "
        f"--name {job_name} "
        f"--log /root/montanablotter/logs/{job_name}.log "
        f"--workdir /root/montanablotter -- "
        f"/root/montanablotter/venv/bin/python3 -m {adapter_module}\n"
    )
    with open(PENDING_CRON_PATH, "a") as f:
        f.write(entry)


def onboard_approved(dry_run: bool = False) -> dict:
    run_at = datetime.now(timezone.utc).isoformat()
    stats = {"processed": 0, "onboarded": 0, "errors": 0}
    conn = _connect()

    approved = conn.execute(
        "SELECT * FROM source_candidates WHERE review_status = 'approved' AND adapter_generated IS NULL"
    ).fetchall()

    if not approved:
        logger.info("No approved candidates to onboard")
        conn.close()
        return stats

    logger.info("Onboarding %d approved candidates", len(approved))

    for row in approved:
        cid = row["id"]
        url = row["url"]
        agency_name = row["agency_name"] or "Unknown Agency"
        county = row["county"] or "MT"
        logger.info("Onboarding #%d: %s — %s", cid, agency_name, url)
        stats["processed"] += 1

        try:
            sample = _fetch_sample(url)
            code = _generate_fetcher(dict(row), sample)

            review = {}
            if row["review_notes"]:
                try:
                    review = json.loads(row["review_notes"])
                except (json.JSONDecodeError, TypeError):
                    pass

            adapter_slug = _slugify(f"{county}_{agency_name}")
            fetcher_path = FETCHERS_DIR / f"{adapter_slug}.py"
            adapter_module = f"services.ingestion.fetchers.{adapter_slug}"
            poll_seconds = int(review.get("suggested_poll_seconds", 86400))

            if not dry_run:
                fetcher_path.write_text(code, encoding="utf-8")
                logger.info("Wrote fetcher: %s", fetcher_path)

                _append_cron_entry(agency_name, adapter_module, poll_seconds)
                logger.info("Appended cron entry to %s", PENDING_CRON_PATH)

                _register_source(conn, dict(row), adapter_slug, poll_seconds)

                conn.execute(
                    """
                    UPDATE source_candidates
                    SET review_status = 'onboarded',
                        adapter_generated = ?,
                        cron_entry = ?,
                        onboarded_at = ?
                    WHERE id = ?
                    """,
                    (str(fetcher_path), adapter_module, run_at, cid),
                )
                conn.commit()
                stats["onboarded"] += 1
                logger.info("Onboarded #%d: %s -> %s", cid, agency_name, fetcher_path)

                try:
                    publish_agent_event({
                        "agent": "source_onboarder",
                        "event": "source_onboarded",
                        "agency_name": agency_name,
                        "county": county,
                        "url": url,
                        "fetcher": str(fetcher_path),
                        "run_at": run_at,
                    })
                except Exception:
                    pass
            else:
                logger.info("[dry-run] Would write %s + cron entry for %s", fetcher_path, adapter_module)
                stats["onboarded"] += 1

        except Exception as exc:
            stats["errors"] += 1
            logger.error("Onboard error #%d %s: %s", cid, url, exc)
            if not dry_run:
                conn.execute(
                    "UPDATE source_candidates SET review_notes=? WHERE id=?",
                    (f"onboard_error: {exc}", cid),
                )
                conn.commit()

    conn.close()
    logger.info(
        "Onboarder complete — processed=%d onboarded=%d errors=%d",
        stats["processed"], stats["onboarded"], stats["errors"],
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Auto-generate fetcher scripts for approved source candidates")
    parser.add_argument("--dry-run", action="store_true", help="Generate but do not write files or update DB")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    stats = onboard_approved(dry_run=args.dry_run)
    print(f"Onboarder: processed={stats['processed']} onboarded={stats['onboarded']} errors={stats['errors']}")


if __name__ == "__main__":
    main()
