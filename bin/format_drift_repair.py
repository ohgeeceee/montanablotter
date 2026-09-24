#!/usr/bin/env python3
"""
format_drift_repair.py
======================
Detects county blotter parse-format drift and uses Claude to propose
parser patches. High-confidence patches land as red-tier proposals in
agent-queue/; low-confidence ones escalate to the dev queue with an
evidence package.

Tier: Green (read-only detection) + Yellow (draft proposal writing).
Never modifies code or DB outside the agent-queue.

Cron (add to crontab.txt):
  30 7 * * * TZ=America/Denver /root/montanablotter/venv/bin/python3 \
      /root/montanablotter/job_runner.py --name format_drift_repair \
      --log /root/montanablotter/logs/format_drift_repair.log \
      --workdir /root/montanablotter -- \
      /root/montanablotter/venv/bin/python3 -m bin.format_drift_repair
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import config

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DB_PATH = Path(getattr(config, "DATABASE", "/root/montanablotter/blotter.db"))
BASE_DIR = Path(getattr(config, "BASE_DIR", "/root/montanablotter"))
QUEUE_ROOT = Path(os.getenv("AGENT_QUEUE", str(BASE_DIR / "agent-queue")))
UPLOADS_DIR = BASE_DIR / "uploads"
LOG_PATH = str(BASE_DIR / "logs" / "format_drift_repair.log")

# Drift detection thresholds
DRIFT_WINDOW_DAYS = int(os.getenv("DRIFT_WINDOW_DAYS", "7"))
DRIFT_SUCCESS_THRESHOLD = float(os.getenv("DRIFT_SUCCESS_THRESHOLD", "0.95"))  # flag if success rate below this
MIN_SAMPLES = int(os.getenv("DRIFT_MIN_SAMPLES", "5"))  # need at least N blotters in window to be meaningful

# Transient errors that look like parse failures but aren't format drift
TRANSIENT_ERROR_PATTERNS = [
    re.compile(r"database is locked", re.I),
    re.compile(r"deadlock", re.I),
    re.compile(r"timeout", re.I),
    re.compile(r"connection.*reset", re.I),
    re.compile(r"temporary", re.I),
]


def _is_transient_error(error_text: str) -> bool:
    """Return True if the error is transient (not format drift)."""
    if not error_text:
        return False
    return any(p.search(error_text) for p in TRANSIENT_ERROR_PATTERNS)

# Patch generation
SAMPLE_PDF_COUNT = 3  # how many recent PDFs to sample for evidence
MAX_PDF_TEXT_CHARS = 6000  # truncate per PDF for the prompt
CLAUDE_CONFIDENCE_THRESHOLD = 0.85  # above this → red-tier proposal

try:
    import anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

MODEL = "claude-sonnet-4-6"

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("format_drift_repair")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def detect_drift(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return counties whose parse success rate dropped below threshold.

    Transient errors (database locked, deadlocks, timeouts) are excluded
    from the denominator — they aren't format drift.
    """
    cutoff = (_utcnow() - timedelta(days=DRIFT_WINDOW_DAYS)).isoformat()
    rows = conn.execute(
        """
        SELECT
            b.id AS blotter_id,
            LOWER(b.county) AS county,
            pe.status AS parse_status,
            pe.details_json
        FROM pipeline_events pe
        JOIN ingestion_jobs ij ON pe.ingestion_job_id = ij.id
        JOIN source_documents sd ON ij.source_document_id = sd.id
        JOIN blotters b ON b.source_document_id = sd.id
        WHERE pe.stage = 'parse'
          AND pe.created_at >= ?
          AND b.county IS NOT NULL AND TRIM(b.county) != ''
        """,
        (cutoff,),
    ).fetchall()

    # Aggregate by county, excluding transient errors from the denominator
    from collections import defaultdict
    county_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "ok": 0})

    for row in rows:
        county = row["county"]
        status = row["parse_status"]
        details = row["details_json"] or ""

        # Skip transient errors — they aren't format drift
        if status != "ok" and _is_transient_error(details):
            continue

        county_stats[county]["total"] += 1
        if status == "ok":
            county_stats[county]["ok"] += 1

    # Filter to counties below threshold with enough samples
    results = []
    for county, stats in county_stats.items():
        if stats["total"] < MIN_SAMPLES:
            continue
        success_pct = 100.0 * stats["ok"] / stats["total"]
        if success_pct < DRIFT_SUCCESS_THRESHOLD * 100:
            results.append(
                {
                    "county": county,
                    "total": stats["total"],
                    "ok": stats["ok"],
                    "success_pct": round(success_pct, 1),
                }
            )

    results.sort(key=lambda x: x["success_pct"])
    return results


def get_sample_pdfs(
    conn: sqlite3.Connection, county: str, limit: int = SAMPLE_PDF_COUNT
) -> list[dict[str, Any]]:
    """Return the most recent PDFs for a county that had parse errors.

    Transient errors (database locked, timeouts) are excluded — they aren't
    format drift and won't help diagnose a parser issue.
    """
    rows = conn.execute(
        """
        SELECT b.id, b.filename, b.file_path,
               COALESCE(
                   json_extract(pe.details_json, '$.error'),
                   pe.details_json,
                   'parse error'
               ) AS parse_error,
               pe.created_at
        FROM pipeline_events pe
        JOIN ingestion_jobs ij ON pe.ingestion_job_id = ij.id
        JOIN source_documents sd ON ij.source_document_id = sd.id
        JOIN blotters b ON b.source_document_id = sd.id
        WHERE pe.stage = 'parse' AND pe.status IN ('error', 'warn')
          AND LOWER(b.county) = LOWER(?)
        ORDER BY b.upload_date DESC
        """,
        (county,),
    ).fetchall()

    results = []
    for row in rows:
        error_text = row["parse_error"] or ""
        if _is_transient_error(error_text):
            continue
        results.append(
            {
                "blotter_id": int(row["id"]),
                "filename": row["filename"],
                "file_path": row["file_path"],
                "parse_error": error_text,
                "created_at": row["created_at"],
            }
        )
        if len(results) >= limit:
            break

    return results


def get_recent_good_pdfs(
    conn: sqlite3.Connection, county: str, limit: int = 2
) -> list[dict[str, Any]]:
    """Return recent successfully-parsed PDFs for comparison."""
    rows = conn.execute(
        """
        SELECT b.id, b.filename, b.file_path, b.incident_count
        FROM blotters b
        WHERE LOWER(b.county) = LOWER(?)
          AND b.incident_count > 0
        ORDER BY b.upload_date DESC
        LIMIT ?
        """,
        (county, limit),
    ).fetchall()

    results = []
    for row in rows:
        results.append(
            {
                "blotter_id": int(row["id"]),
                "filename": row["filename"],
                "file_path": row["file_path"],
                "incident_count": int(row["incident_count"] or 0),
            }
        )
    return results


# ---------------------------------------------------------------------------
# PDF text extraction (reuse existing parser)
# ---------------------------------------------------------------------------
def extract_pdf_text(pdf_path: str) -> str:
    """Extract raw text from a PDF using pdfplumber (no OCR for speed)."""
    try:
        import pdfplumber
    except ImportError:
        return ""

    text_parts: list[str] = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text_parts.append(t)
    except Exception as e:
        logger.warning("pdfplumber failed for %s: %s", pdf_path, e)
        return ""

    full = "\n".join(text_parts)
    if not full.strip():
        return ""
    return full[:MAX_PDF_TEXT_CHARS]


# ---------------------------------------------------------------------------
# Parser code retrieval
# ---------------------------------------------------------------------------
def get_parser_source() -> str:
    """Read the current parser.py source to include in the prompt."""
    parser_path = BASE_DIR / "services" / "blotter" / "parser.py"
    if not parser_path.exists():
        return "# parser.py not found"
    return parser_path.read_text(encoding="utf-8")[:15000]


def get_processor_source_snippet() -> str:
    """Read the relevant parse_pdf / store_parsed_pdf section."""
    proc_path = BASE_DIR / "services" / "blotter" / "processor.py"
    if not proc_path.exists():
        return "# processor.py not found"
    text = proc_path.read_text(encoding="utf-8")
    # Extract the parse_pdf function and store_parsed_pdf
    match = re.search(
        r"(def parse_pdf\(.*?\n)(.*?)(?=\ndef |\nclass |\Z)",
        text,
        re.DOTALL,
    )
    if match:
        return match.group(0)[:8000]
    return text[:8000]


# ---------------------------------------------------------------------------
# Claude interaction
# ---------------------------------------------------------------------------
def build_drift_prompt(
    county: str,
    drift_info: dict[str, Any],
    bad_samples: list[dict],
    good_samples: list[dict],
    parser_source: str,
    processor_snippet: str,
) -> str:
    """Build the prompt for Claude to diagnose and propose a fix."""

    bad_blocks = []
    for s in bad_samples:
        text = ""
        if s.get("file_path") and os.path.exists(s["file_path"]):
            text = extract_pdf_text(s["file_path"])
        bad_blocks.append(
            textwrap.dedent(f"""\
            ### Bad PDF: {s['filename']} (blotter #{s['blotter_id']})
            Parse error: {s.get('parse_error', 'unknown')}
            Created: {s.get('created_at', 'unknown')}

            Raw text (first {MAX_PDF_TEXT_CHARS} chars):
            ```
            {text[:MAX_PDF_TEXT_CHARS] if text else '(could not extract text)'}
            ```
            """)
        )

    good_blocks = []
    for s in good_samples:
        text = ""
        if s.get("file_path") and os.path.exists(s["file_path"]):
            text = extract_pdf_text(s["file_path"])
        good_blocks.append(
            textwrap.dedent(f"""\
            ### Good PDF: {s['filename']} (blotter #{s['blotter_id']})
            Incidents parsed: {s.get('incident_count', '?')}

            Raw text (first 2000 chars for format reference):
            ```
            {text[:2000] if text else '(could not extract text)'}
            ```
            """)
        )

    bad_text = "\n".join(bad_blocks) if bad_blocks else "(no bad samples with extractable text)"
    good_text = "\n".join(good_blocks) if good_blocks else "(no recent good samples for comparison)"

    return textwrap.dedent(f"""\
    You are a parser engineer for Montana Blotter. A county's blotter format has drifted.

    ## County: {county.title()}
    ## Drift info
    - Success rate (trailing {DRIFT_WINDOW_DAYS}d): {drift_info['success_pct']}%
    - Total blotters in window: {drift_info['total']}
    - Successfully parsed: {drift_info['ok']}
    - Transient errors (database locked, timeouts) are excluded from these numbers — only genuine format/parse failures count.

    ## Bad samples (parse errors — format drift, not transient)
    {bad_text}

    ## Good samples (recent successful parses for format reference)
    {good_text}

    ## Current parser code (services/blotter/parser.py)
    ```python
    {parser_source}
    ```

    ## Processor entry point (services/blotter/processor.py)
    ```python
    {processor_snippet}
    ```

    ## Your task
    1. Diagnose what changed in the PDF format (new header, different date format, shifted columns, etc.)
    2. Propose a minimal, surgical fix to parser.py that handles the new format
    3. Rate your confidence in the fix (0.0 to 1.0)

    Return JSON only, no prose:
    {{
      "diagnosis": "one-paragraph explanation of the format change",
      "confidence": 0.0-1.0,
      "proposed_diff": "unified diff string (patch format) for services/blotter/parser.py",
      "test_case": "a short raw-text snippet the new regex must match",
      "risk": "low/medium/high — chance of breaking existing parses"
    }}

    Rules:
    - The diff must be a valid unified diff that `patch` can apply
    - Only modify parser.py, not processor.py or db.py
    - Prefer adding a new adapter or extending an existing regex over rewriting
    - If you cannot diagnose with confidence < 0.6, say so and propose escalation
    """)


def call_claude(prompt: str) -> dict[str, Any]:
    """Call Claude and return parsed JSON response."""
    if not _ANTHROPIC_AVAILABLE:
        logger.error("anthropic package not available")
        return {"error": "anthropic not installed"}

    api_key = getattr(config, "ANTHROPIC_API_KEY", "") or os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not set")
        return {"error": "no API key"}

    client = anthropic.Anthropic(api_key=api_key)
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            temperature=0.2,
            system="You are a parser engineer. Return only valid JSON, no markdown fences.",
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        logger.error("Claude API call failed: %s", e)
        return {"error": str(e)}

    raw = resp.content[0].text if resp.content else ""
    # Strip markdown fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("Claude returned non-JSON: %s (raw: %s)", e, raw[:500])
        return {"error": f"non-JSON response: {e}", "raw": raw[:1000]}


# ---------------------------------------------------------------------------
# Queue writing
# ---------------------------------------------------------------------------
def write_red_tier_proposal(
    county: str,
    drift_info: dict[str, Any],
    diagnosis: str,
    diff: str,
    confidence: float,
    risk: str,
    test_case: str,
) -> Path:
    """Write a red-tier proposal to agent-queue/red-tier/."""
    ts = _utcnow().strftime("%Y%m%dT%H%M%SZ")
    slug = f"{county}-parser-drift-fix"
    item_dir = QUEUE_ROOT / "red-tier" / f"{ts}-{slug}"
    item_dir.mkdir(parents=True, exist_ok=True)

    body = textwrap.dedent(f"""\
    # Parser Drift Fix — {county.title()} County

    ## Summary
    {diagnosis}

    ## Drift metrics
    - Success rate (trailing {DRIFT_WINDOW_DAYS}d): {drift_info['success_pct']}%
    - Total blotters in window: {drift_info['total']}
    - Successfully parsed: {drift_info['ok']}

    ## Confidence: {confidence:.2f}
    ## Risk: {risk}

    ## Proposed diff

    ```diff
    {diff}
    ```

    ## Test case (must match after patch)
    ```
    {test_case}
    ```

    ## Reasoning
    Auto-generated by format_drift_repair agent. Patch proposed by Claude
    after analyzing {SAMPLE_PDF_COUNT} recent failed parses and comparing
    against recent successful parses.

    ## Rollback plan
    `git checkout -- services/blotter/parser.py` — single-file revert.
    No DB changes, no schema changes.
    """)

    frontmatter = textwrap.dedent(f"""\
    ---
    profile: blotter-dev
    created: {_utcnow().isoformat()}
    tier: red
    status: draft
    priority: high
    related_county: "{county}"
    related_files: ["services/blotter/parser.py"]
    confidence: {confidence:.2f}
    risk: {risk}
    auto_generated: true
    ---

    """)

    item_path = item_dir / "ITEM.md"
    item_path.write_text(frontmatter + body + "\n", encoding="utf-8")
    logger.info("Wrote red-tier proposal: %s", item_path)
    return item_path


def write_dev_escalation(
    county: str,
    drift_info: dict[str, Any],
    diagnosis: str,
    evidence: dict[str, Any],
) -> Path:
    """Write an escalation item to agent-queue/blotter-dev/."""
    ts = _utcnow().strftime("%Y%m%dT%H%M%SZ")
    slug = f"{county}-parser-drift-escalation"
    item_dir = QUEUE_ROOT / "blotter-dev" / f"{ts}-{slug}"
    item_dir.mkdir(parents=True, exist_ok=True)

    # Write evidence as attachment
    att_dir = item_dir / "attachments"
    att_dir.mkdir(exist_ok=True)
    evidence_path = att_dir / "evidence.json"
    evidence_path.write_text(json.dumps(evidence, indent=2, default=str), encoding="utf-8")
    logger.info("Wrote evidence: %s", evidence_path)

    body = textwrap.dedent(f"""\
    # Parser Drift Escalation — {county.title()} County

    ## Summary
    Format drift detected but auto-repair confidence too low for a red-tier proposal.
    Manual investigation required.

    ## Drift metrics
    - Success rate (trailing {DRIFT_WINDOW_DAYS}d): {drift_info['success_pct']}%
    - Total blotters in window: {drift_info['total']}
    - Successfully parsed: {drift_info['ok']}

    ## Claude's best-effort diagnosis
    {diagnosis}

    ## Evidence package
    See attachments/evidence.json for:
    - Recent failed parses (with raw text)
    - Recent successful parses (for comparison)
    - Full parser.py source at time of detection

    ## Suggested investigation
    1. Pull a recent failed PDF from uploads/
    2. Run `python3 -m services.blotter.parser <pdf>` to see actual parse output
    3. Compare against the good samples in evidence.json
    4. Determine if source format changed or parser has a regression
    """)

    frontmatter = textwrap.dedent(f"""\
    ---
    profile: blotter-dev
    created: {_utcnow().isoformat()}
    tier: green
    status: open
    priority: high
    related_county: "{county}"
    related_files: ["services/blotter/parser.py"]
    auto_generated: true
    ---

    """)

    item_path = item_dir / "ITEM.md"
    item_path.write_text(frontmatter + body + "\n", encoding="utf-8")
    logger.info("Wrote dev escalation: %s", item_path)
    return item_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run(dry_run: bool = False, county_filter: str | None = None) -> list[dict[str, Any]]:
    """Run drift detection + repair pipeline. Returns list of actions taken."""
    actions: list[dict[str, Any]] = []

    conn = _connect()
    try:
        drift_counties = detect_drift(conn)
    finally:
        conn.close()

    if county_filter:
        drift_counties = [c for c in drift_counties if c["county"] == county_filter.lower()]

    if not drift_counties:
        logger.info("No format drift detected.")
        return actions

    logger.info("Detected drift in %d counties: %s", len(drift_counties), [c["county"] for c in drift_counties])

    parser_source = get_parser_source()
    processor_snippet = get_processor_source_snippet()

    for drift in drift_counties:
        county = drift["county"]
        logger.info("Processing drift for %s (%.1f%% success)", county, drift["success_pct"])

        conn = _connect()
        try:
            bad_samples = get_sample_pdfs(conn, county)
            good_samples = get_recent_good_pdfs(conn, county)
        finally:
            conn.close()

        prompt = build_drift_prompt(county, drift, bad_samples, good_samples, parser_source, processor_snippet)

        if dry_run:
            logger.info("[DRY RUN] Would call Claude for %s", county)
            actions.append({"county": county, "action": "dry_run", "prompt_len": len(prompt)})
            continue

        result = call_claude(prompt)

        if "error" in result:
            logger.error("Claude failed for %s: %s", county, result["error"])
            actions.append({"county": county, "action": "claude_error", "error": result["error"]})
            continue

        confidence = float(result.get("confidence", 0.0))
        diagnosis = result.get("diagnosis", "No diagnosis returned")
        diff = result.get("proposed_diff", "")
        risk = result.get("risk", "medium")
        test_case = result.get("test_case", "")

        logger.info("Claude result for %s: confidence=%s risk=%s", county, confidence, risk)

        if confidence >= CLAUDE_CONFIDENCE_THRESHOLD and diff:
            item_path = write_red_tier_proposal(
                county, drift, diagnosis, diff, confidence, risk, test_case
            )
            actions.append(
                {
                    "county": county,
                    "action": "red_tier_proposal",
                    "confidence": confidence,
                    "path": str(item_path),
                }
            )
        else:
            # Build evidence package
            evidence = {
                "drift": drift,
                "bad_samples": [
                    {k: v for k, v in s.items() if k != "file_path"}
                    for s in bad_samples
                ],
                "good_samples": [
                    {k: v for k, v in s.items() if k != "file_path"}
                    for s in good_samples
                ],
                "claude_diagnosis": diagnosis,
                "claude_confidence": confidence,
                "parser_source_excerpt": parser_source[:3000],
            }
            item_path = write_dev_escalation(county, drift, diagnosis, evidence)
            actions.append(
                {
                    "county": county,
                    "action": "dev_escalation",
                    "confidence": confidence,
                    "path": str(item_path),
                }
            )

    return actions


def main() -> None:
    parser = argparse.ArgumentParser(description="Detect blotter format drift and propose parser fixes")
    parser.add_argument("--dry-run", action="store_true", help="Detect only, do not call Claude or write proposals")
    parser.add_argument("--county", help="Restrict to a single county slug")
    parser.add_argument("--json", action="store_true", help="Output JSON summary to stdout")
    args = parser.parse_args()

    actions = run(dry_run=args.dry_run, county_filter=args.county)

    if args.json:
        print(json.dumps(actions, indent=2, default=str))
    else:
        if not actions:
            print("No drift detected.")
        else:
            for a in actions:
                print(f"  {a['county']}: {a['action']} (confidence={a.get('confidence', 'n/a')})")


if __name__ == "__main__":
    main()
