#!/usr/bin/env python3
"""
source_reviewer.py
==================
Agent 2 of 3 in the autonomous source-discovery pipeline.

Runs every 2 hours staggered 30 min after source_scout. Pulls pending candidates
from source_candidates, fetches a page sample, and asks Claude to evaluate whether
it is a genuine, parseable Montana law enforcement data source.

Approved candidates get review_status='approved' and are picked up by
source_onboarder.py. Rejected ones get 'rejected' with a reason stored in
review_notes (JSON).

Cron (add to crontab.txt):
  30 */2 * * * TZ=America/Denver /root/montanablotter/venv/bin/python3 \\
      /root/montanablotter/job_runner.py --name source_reviewer \\
      --log /root/montanablotter/logs/source_reviewer.log \\
      --workdir /root/montanablotter -- \\
      /root/montanablotter/venv/bin/python3 -m services.ingestion.source_reviewer
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import textwrap
from datetime import datetime, timezone

import requests

import config
from services.agents.events_bus import publish_agent_event

try:
    import anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

DB_PATH = config.DB_PATH
LOG_PATH = "/root/montanablotter/logs/source_reviewer.log"
UA = "Mozilla/5.0 (compatible; MontanaBlotter-Reviewer/1.0; +https://montanablotter.com)"
MODEL = "claude-sonnet-4-6"
MAX_SAMPLE_CHARS = 6000
MAX_PER_RUN = 20

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("source_reviewer")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _fetch_sample(url: str) -> str:
    try:
        resp = requests.get(
            url, timeout=20, headers={"User-Agent": UA},
            allow_redirects=True, stream=True,
        )
        resp.raise_for_status()
        if "pdf" in resp.headers.get("content-type", "").lower():
            return f"[PDF document at {url}]"
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


def _strip_tags(html: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', html)
    return re.sub(r'\s+', ' ', text).strip()[:MAX_SAMPLE_CHARS]


REVIEW_SYSTEM = textwrap.dedent("""\
    You are a data-quality reviewer for Montana Blotter, a public-interest journalism
    site that aggregates Montana law enforcement arrest and incident records.

    Given a candidate URL and page sample, decide whether Montana Blotter should ingest it.
    Respond ONLY with valid JSON — no text outside the JSON object:
    {
      "approved": true | false,
      "confidence": 0-100,
      "source_format": "zuercher" | "pdf" | "html_table" | "json_api" | "csv" | "dashboard" | "unknown",
      "update_frequency": "hourly" | "daily" | "weekly" | "monthly" | "unknown",
      "agency_name": "<best guess>",
      "county": "<Montana county or empty string>",
      "notes": "<1-2 sentences>",
      "suggested_poll_seconds": <integer>
    }

    REJECT if: 404/login wall, not law enforcement data, outside Montana, or duplicates
    an already-active source (Yellowstone, Missoula, Flathead, Jefferson, Madison, Carbon,
    Stillwater, Meagher, Wheatland, Valley, Roosevelt, Sanders, Ravalli, Rosebud, Gallatin,
    Cascade, Bozeman PD, Whitefish PD, CrimeMapping Montana agencies).

    APPROVE if: shows actual arrest records / inmate listings / incident logs / calls-for-service,
    is publicly accessible, has dates within the last 90 days, and covers a Montana agency
    NOT already in the active list above.
""")


def _review_with_claude(candidate: dict) -> dict:
    if not _ANTHROPIC_AVAILABLE:
        return _fallback_review(candidate)
    try:
        api_key = getattr(config, "ANTHROPIC_API_KEY", None)
        if not api_key:
            return _fallback_review(candidate)

        client = anthropic.Anthropic(api_key=api_key)
        url = candidate["url"]
        sample_raw = _fetch_sample(url)
        sample_text = _strip_tags(sample_raw) if "<html" in sample_raw.lower() else sample_raw

        user_msg = (
            f"URL: {url}\n"
            f"Source type hint: {candidate.get('source_type', 'unknown')}\n"
            f"Agency: {candidate.get('agency_name', '')}\n"
            f"County: {candidate.get('county', '')}\n\n"
            f"Page sample:\n{sample_text}"
        )

        response = client.messages.create(
            model=MODEL,
            max_tokens=512,
            system=REVIEW_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = response.content[0].text.strip()
        json_match = re.search(r'\{.*\}', raw, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(0))
        return json.loads(raw)

    except json.JSONDecodeError as exc:
        logger.warning("Claude returned non-JSON for %s: %s", candidate["url"], exc)
        return _fallback_review(candidate)
    except Exception as exc:
        logger.error("Claude review error for %s: %s", candidate["url"], exc)
        return _fallback_review(candidate)


def _fallback_review(candidate: dict) -> dict:
    sample = _fetch_sample(candidate["url"])
    text = _strip_tags(sample).lower()
    keywords = ["inmate", "booking", "arrest", "blotter", "roster", "incident", "warrant"]
    hits = sum(1 for kw in keywords if kw in text)
    approved = hits >= 2 and "[fetch error" not in sample
    return {
        "approved": approved,
        "confidence": min(hits * 15, 75),
        "source_format": "unknown",
        "update_frequency": "unknown",
        "agency_name": candidate.get("agency_name", ""),
        "county": candidate.get("county", ""),
        "notes": f"Fallback regex review: {hits} blotter keywords found",
        "suggested_poll_seconds": 86400,
    }


def review_pending(dry_run: bool = False, limit: int = MAX_PER_RUN) -> dict:
    run_at = datetime.now(timezone.utc).isoformat()
    stats = {"reviewed": 0, "approved": 0, "rejected": 0, "errors": 0}
    conn = _connect()

    pending = conn.execute(
        "SELECT * FROM source_candidates WHERE review_status = 'pending' LIMIT ?",
        (limit,),
    ).fetchall()

    if not pending:
        logger.info("No pending candidates to review")
        conn.close()
        return stats

    logger.info("Reviewing %d pending candidates", len(pending))

    for row in pending:
        cid = row["id"]
        url = row["url"]
        logger.info("Reviewing #%d: %s", cid, url)
        stats["reviewed"] += 1

        try:
            result = _review_with_claude(dict(row))
            approved = bool(result.get("approved", False))
            review_status = "approved" if approved else "rejected"
            notes = result.get("notes", "")

            logger.info("#%d %s (confidence=%s): %s", cid, review_status, result.get("confidence"), notes)

            if not dry_run:
                conn.execute(
                    """
                    UPDATE source_candidates
                    SET review_status = ?,
                        review_notes = ?,
                        reviewed_at = ?,
                        agency_name = COALESCE(NULLIF(?, ''), agency_name),
                        county = COALESCE(NULLIF(?, ''), county)
                    WHERE id = ?
                    """,
                    (
                        review_status,
                        json.dumps(result),
                        run_at,
                        result.get("agency_name", ""),
                        result.get("county", ""),
                        cid,
                    ),
                )
                conn.commit()

            if approved:
                stats["approved"] += 1
            else:
                stats["rejected"] += 1

        except Exception as exc:
            stats["errors"] += 1
            logger.error("Review error #%d %s: %s", cid, url, exc)
            if not dry_run:
                conn.execute(
                    "UPDATE source_candidates SET review_notes=? WHERE id=?",
                    (f"error: {exc}", cid),
                )
                conn.commit()

    conn.close()

    if stats["approved"] > 0:
        try:
            publish_agent_event({
                "agent": "source_reviewer",
                "event": "sources_approved",
                "count": stats["approved"],
                "run_at": run_at,
                "stats": stats,
            })
        except Exception:
            pass

    logger.info(
        "Review complete — reviewed=%d approved=%d rejected=%d errors=%d",
        stats["reviewed"], stats["approved"], stats["rejected"], stats["errors"],
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Claude-powered review of source_candidates")
    parser.add_argument("--dry-run", action="store_true", help="Review but do not update DB")
    parser.add_argument("--limit", type=int, default=MAX_PER_RUN, help="Max candidates per run")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    stats = review_pending(dry_run=args.dry_run, limit=args.limit)
    print(f"Reviewer: reviewed={stats['reviewed']} approved={stats['approved']} rejected={stats['rejected']} errors={stats['errors']}")


if __name__ == "__main__":
    main()
