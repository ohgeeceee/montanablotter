#!/usr/bin/env python3
"""
warrant_audit.py
================
Daily warranty coverage audit for Montana Blotter.

The ingest job (warrant_ingest.py --all, every 6h) already fetches every
registered county source and upserts new warrants. This audit layer runs
ONCE per day and answers the question "did we miss anything?":

  1. For every registered source it performs a live fetch and records the
     outcome: OK (N records), EMPTY (0 records — may be a dead page), or
     ERROR (fetch/parse failed).
  2. It writes any warrants it finds (idempotent upsert) so a source that
     only updates once a day is never missed between the 6h ingest runs.
  3. It detects warrants first seen in the last 24h (truly NEW today) and
     breaks them down by county.
  4. It emails a digest to MB_ADMIN_ALERT_EMAILS summarizing gaps and new
     warrants. If SMTP is not configured, the report is written to the log
     and to logs/warrant_audit.report.

Cron (daily at 07:00, after the 06:30 ingest pass):
    0 7 * * * /usr/bin/nice -n 19 /root/montanablotter/venv/bin/python3 \
        /root/montanablotter/job_runner.py --name warrant_audit \
        --log /root/montanablotter/logs/warrant_audit.log \
        --workdir /root/montanablotter -- \
        /root/montanablotter/venv/bin/python3 \
        /root/montanablotter/warrant_audit.py
"""

from __future__ import annotations

import logging
import os
import smtplib
import sqlite3
import sys
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

sys.path.insert(0, os.path.dirname(__file__))

import config

from services.ingestion.warrants.models import ensure_warrant_schema
from services.ingestion.warrants.scraper import (
    SOURCES,
    fetch_warrants_for_county,
    resolve_stale_warrants,
    upsert_warrants,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("warrant_audit")

DB_PATH = getattr(config, "DB_PATH", "/root/montanablotter/blotter.db")
RUN_TS = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def run_audit() -> dict:
    """Fetch every source, upsert, and collect coverage + new-warrant stats."""
    results = []  # (slug, county, status, count, error)
    for slug in sorted(SOURCES):
        source = SOURCES[slug]
        county = source.get("county", slug)
        try:
            records = fetch_warrants_for_county(slug)
        except Exception as exc:  # broad: a single bad source must not kill the audit
            logger.exception("ERROR fetching %s (%s)", county, slug)
            results.append((slug, county, "ERROR", 0, str(exc)[:200]))
            continue

        status = "OK" if records else "EMPTY"
        results.append((slug, county, status, len(records), ""))

        if records:
            conn = _get_conn()
            try:
                ensure_warrant_schema(conn)
                upsert_warrants(conn, records, RUN_TS)
                active_ids = {r.source_record_id for r in records}
                resolve_stale_warrants(conn, county, active_ids, RUN_TS)
            except Exception as exc:
                logger.exception("DB write failed for %s (%s)", county, slug)
            finally:
                conn.close()

    return results


def collect_new_warrants(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Warrants first seen in the last 24h (truly new since yesterday's audit)."""
    return conn.execute(
        """
        SELECT county, city, person_name, charges_text, bond_amount, source_url
        FROM warrants
        WHERE first_seen_at >= date('now', '-1 day')
        ORDER BY county, person_name
        """
    ).fetchall()


def build_report(results: list, new_rows: list) -> str:
    ok = [r for r in results if r[2] == "OK"]
    empty = [r for r in results if r[2] == "EMPTY"]
    error = [r for r in results if r[2] == "ERROR"]

    lines = []
    lines.append(f"Montana Blotter — Warrant Coverage Audit — {RUN_TS}")
    lines.append("=" * 64)
    lines.append(
        f"Sources checked: {len(results)}  |  OK: {len(ok)}  "
        f"EMPTY: {len(empty)}  ERROR: {len(error)}"
    )
    lines.append(f"New warrants seen in last 24h: {len(new_rows)}")
    lines.append("")

    if new_rows:
        lines.append("NEW WARRANTS BY COUNTY")
        lines.append("-" * 64)
        by_county: dict[str, int] = {}
        for r in new_rows:
            by_county[r["county"]] = by_county.get(r["county"], 0) + 1
        for county in sorted(by_county):
            lines.append(f"  {county:<22} {by_county[county]}")
        lines.append("")

    if empty:
        lines.append("EMPTY SOURCES (0 records — review for dead pages)")
        lines.append("-" * 64)
        for slug, county, _, count, _ in empty:
            lines.append(f"  {county:<22} ({slug})")
        lines.append("")

    if error:
        lines.append("ERROR SOURCES (fetch/parse failed)")
        lines.append("-" * 64)
        for slug, county, _, count, err in error:
            lines.append(f"  {county:<22} ({slug}) :: {err}")
        lines.append("")

    if not empty and not error:
        lines.append("All sources OK — no coverage gaps detected.")
        lines.append("")

    return "\n".join(lines)


def _send_email(subject: str, body: str) -> bool:
    recipients = getattr(config, "ADMIN_ALERT_EMAILS", [])
    if not recipients:
        logger.warning("ADMIN_ALERT_EMAILS not configured; skipping email")
        return False
    smtp_user = getattr(config, "SMTP_USER", "")
    smtp_pass = getattr(config, "SMTP_PASSWORD", "")
    smtp_server = getattr(config, "SMTP_SERVER", "")
    smtp_port = int(getattr(config, "SMTP_PORT", 587))
    if not smtp_server or not smtp_user:
        logger.warning("SMTP not configured; skipping email")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = smtp_user
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))
        smtp = smtplib.SMTP(smtp_server, smtp_port)
        try:
            smtp.starttls()
            smtp.login(smtp_user, smtp_pass)
            smtp.sendmail(smtp_user, recipients, msg.as_string())
        finally:
            smtp.quit()
        logger.info("Audit digest emailed to %s", ", ".join(recipients))
        return True
    except Exception as exc:
        logger.error("Failed to send audit email: %s", exc)
        return False


def main() -> None:
    logger.info("Starting daily warrant coverage audit (%d sources)...", len(SOURCES))
    results = run_audit()

    conn = _get_conn()
    try:
        new_rows = collect_new_warrants(conn)
    finally:
        conn.close()

    report = build_report(results, new_rows)
    logger.info("\n%s", report)

    # Persist a copy of the report for review/debugging.
    os.makedirs("/root/montanablotter/logs", exist_ok=True)
    with open("/root/montanablotter/logs/warrant_audit.report", "w") as fh:
        fh.write(report + "\n")

    empty = sum(1 for r in results if r[2] == "EMPTY")
    error = sum(1 for r in results if r[2] == "ERROR")
    subject = (
        f"[Warrant Audit {TODAY}] OK={len(results)-empty-error} "
        f"EMPTY={empty} ERR={error} NEW={len(new_rows)}"
    )
    _send_email(subject, report)
    logger.info("Audit complete.")


if __name__ == "__main__":
    main()
