#!/usr/bin/env python3
"""
blotter_auditor.py — Montana Blotter Legal Assistant
=====================================================
Pre-publication audit for police blotter posts.

Checks performed:
  1. PII scan — SSNs, exact home addresses, DOBs, phone numbers, DL numbers
  2. Public Summary — rewritten to be objective and government-grade professional
  3. Tone validation — flags sensationalism, bias, or non-neutral language
  4. SEO meta-description — auto-generated for Great Falls, MT context

Pipeline integration (called automatically from processor.py):
  from blotter_auditor import audit_blotter_posts

Standalone CLI:
  python blotter_auditor.py --post-id 42
  python blotter_auditor.py --blotter-id 12
  python blotter_auditor.py --all-pending
  python blotter_auditor.py --text "raw blotter text..."
"""

import re
import json
import sqlite3
import logging
import sys
import argparse
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

import anthropic
import config
from services.summarizer.historical_context import append_historical_perspective

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)
DB_TIMEOUT_SECONDS = float(getattr(config, "DB_TIMEOUT_SECONDS", 30))
DB_BUSY_TIMEOUT_MS = int(getattr(config, "DB_BUSY_TIMEOUT_MS", 30000))


def _connect_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=DB_TIMEOUT_SECONDS)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {DB_BUSY_TIMEOUT_MS}")
    return conn

# ---------------------------------------------------------------------------
# ANSI color codes for CLI output
# ---------------------------------------------------------------------------

class C:
    RED    = "\033[91m"
    YELLOW = "\033[93m"
    GREEN  = "\033[92m"
    CYAN   = "\033[96m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    RESET  = "\033[0m"


def _colorize(text: str, *codes: str) -> str:
    if not sys.stdout.isatty():
        return text
    return "".join(codes) + text + C.RESET


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class PiiFlag:
    pii_type: str        # 'ssn' | 'home_address' | 'phone' | 'dob' | 'dl_number' | 'full_name_address'
    severity: str        # 'high' | 'medium' | 'low'
    redacted: str        # masked version of matched text  (e.g. "***-**-6789")
    context: str         # ≤80 chars surrounding the match


@dataclass
class AuditResult:
    post_id: Optional[int]
    audit_passed: bool
    pii_flags: list = field(default_factory=list)       # list[PiiFlag]
    pii_clean: bool = True
    public_summary: str = ""
    tone_ok: bool = True
    tone_notes: str = ""
    meta_description: str = ""
    raw_issues: list = field(default_factory=list)      # list[str]
    audited_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ---------------------------------------------------------------------------
# PII detection — regex pass (no API cost)
# ---------------------------------------------------------------------------

# Social Security Numbers:  NNN-NN-NNNN  or  NNN NN NNNN  or  NNNNNNNNN
_RE_SSN = re.compile(
    r'\b(?!000|666|9\d{2})\d{3}[-\s]\d{2}[-\s](?!0000)\d{4}\b'
)

# US phone numbers in common formats
_RE_PHONE = re.compile(
    r'\b(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b'
)

# Dates of birth — MM/DD/YYYY or MM-DD-YYYY with birth context
_RE_DOB_RAW = re.compile(
    r'\b(?:0?[1-9]|1[0-2])[/\-](?:0?[1-9]|[12]\d|3[01])[/\-](?:19|20)\d{2}\b'
)
_RE_DOB_CONTEXT = re.compile(
    r'(?i)(?:dob|date\s+of\s+birth|born\s+on|birthdate)[:\s]*'
    r'(?:0?[1-9]|1[0-2])[/\-](?:0?[1-9]|[12]\d|3[01])[/\-](?:19|20)\d{2}'
)

# Montana DL numbers: one letter followed by 8–13 digits (MT DDS format)
_RE_MT_DL = re.compile(r'\b[A-Z]\d{8,13}\b')

# Exact numbered street address  (123 Main Street, 456 W. Elm Ave, etc.)
# Flags when a specific building number + named street appears — typical residential PII
_RE_STREET_ADDR = re.compile(
    r'\b\d{1,5}\s+(?:[NSEW]\.\s*)?[A-Za-z]+(?:\s+[A-Za-z]+){0,3}'
    r'\s+(?:Street|St\.?|Avenue|Ave\.?|Boulevard|Blvd\.?|Road|Rd\.?|'
    r'Drive|Dr\.?|Lane|Ln\.?|Court|Ct\.?|Way|Place|Pl\.?|Circle|Cir\.?|'
    r'Trail|Trl\.?|Highway|Hwy\.?|Parkway|Pkwy\.?|Loop|Terrace|Ter\.?)'
    r'(?:\s+(?:Apt\.?|Unit|Suite|Ste\.?|#)\s*[\w-]+)?'
    r'(?:,\s*[A-Za-z\s]+,\s*MT\s+\d{5})?',
    re.IGNORECASE,
)

# Context words that indicate an address is a private residence (not a public location)
_RESIDENCE_CONTEXT_WORDS = re.compile(
    r'(?i)\b(?:residen(?:ce|t|tial)|home\s+address|personal\s+address|'
    r'lives?\s+at|residing\s+at|domicile|private\s+address|home\s+of|'
    r'address\s+of\s+(?:suspect|victim|subject|individual))\b'
)


def _mask(text: str) -> str:
    """Partially redact matched text for safe logging."""
    if len(text) <= 4:
        return "****"
    keep = max(2, len(text) // 4)
    return "*" * (len(text) - keep) + text[-keep:]


def _ctx(text: str, match: re.Match, width: int = 80) -> str:
    """Extract surrounding context around a regex match without storing the raw match."""
    start = max(0, match.start() - width // 2)
    end   = min(len(text), match.end() + width // 2)
    snippet = text[start:end].replace("\n", " ")
    spans = get_pii_spans(snippet)
    if spans:
        for span in reversed(spans):
            snippet = snippet[:span["start"]] + _mask(span["matched"]) + snippet[span["end"]:]
    else:
        relative_start = match.start() - start
        relative_end = match.end() - start
        snippet = snippet[:relative_start] + _mask(match.group()) + snippet[relative_end:]
    return snippet.strip()


def get_pii_spans(text: str) -> list[dict]:
    """
    Return exact character-level spans of PII found in `text`.
    Used by the admin redaction editor to build interactive highlights.

    Each item: {start, end, matched, pii_type, severity}
    Overlapping spans are deduplicated (longest / highest severity wins).
    """
    raw: list[dict] = []

    def _add(m: re.Match, pii_type: str, severity: str) -> None:
        raw.append({
            "start":    m.start(),
            "end":      m.end(),
            "matched":  m.group(),
            "pii_type": pii_type,
            "severity": severity,
        })

    for m in _RE_SSN.finditer(text):
        _add(m, "ssn", "high")

    for m in _RE_PHONE.finditer(text):
        _add(m, "phone", "medium")

    dob_ctx_pos = {m.start() for m in _RE_DOB_CONTEXT.finditer(text)}
    for m in _RE_DOB_CONTEXT.finditer(text):
        _add(m, "dob", "high")
    for m in _RE_DOB_RAW.finditer(text):
        if m.start() not in dob_ctx_pos:
            _add(m, "dob", "low")

    for m in _RE_MT_DL.finditer(text):
        if not re.search(
            r'(?i)(?:case|cfs|incident|report)\s*(?:no|num|#)?\s*:?\s*$',
            text[max(0, m.start() - 60): m.start()],
        ):
            _add(m, "dl_number", "high")

    for m in _RE_STREET_ADDR.finditer(text):
        surrounding = text[max(0, m.start() - 120): m.end() + 120]
        sev = "high" if _RESIDENCE_CONTEXT_WORDS.search(surrounding) else "medium"
        _add(m, "home_address", sev)

    # Sort by start position; for overlaps keep the one that started first (longest wins
    # for same-start ties).
    raw.sort(key=lambda x: (x["start"], -(x["end"] - x["start"])))
    merged: list[dict] = []
    for span in raw:
        if merged and span["start"] < merged[-1]["end"]:
            continue  # overlaps with previous — skip
        merged.append(span)

    return merged


def scan_for_pii(text: str) -> list[PiiFlag]:
    """
    Run all PII regex patterns against `text`.
    Returns a list of PiiFlag objects, one per detected issue.
    Does NOT call any external API.
    """
    flags: list[PiiFlag] = []

    # 1. SSNs — always HIGH severity
    for m in _RE_SSN.finditer(text):
        flags.append(PiiFlag(
            pii_type="ssn",
            severity="high",
            redacted=_mask(m.group()),
            context=_ctx(text, m),
        ))
    # Also catch bare 9-digit SSNs (NNNNNNNNN) when preceded by SSN context
    for m in re.finditer(r'\b\d{9}\b', text):
        ctx = text[max(0, m.start() - 40):m.end() + 10].lower()
        if any(kw in ctx for kw in ('ssn', 'social security', 'social sec')):
            flags.append(PiiFlag(
                pii_type="ssn",
                severity="high",
                redacted=_mask(m.group()),
                context=_ctx(text, m),
            ))

    # 2. Phone numbers — MEDIUM (contextual)
    for m in _RE_PHONE.finditer(text):
        flags.append(PiiFlag(
            pii_type="phone",
            severity="medium",
            redacted=_mask(m.group()),
            context=_ctx(text, m),
        ))

    # 3. DOB with explicit label — HIGH; bare date — LOW
    for m in _RE_DOB_CONTEXT.finditer(text):
        flags.append(PiiFlag(
            pii_type="dob",
            severity="high",
            redacted=_mask(m.group()),
            context=_ctx(text, m),
        ))
    # Bare dates only flag if not already captured by context pattern
    dob_ctx_positions = {m.start() for m in _RE_DOB_CONTEXT.finditer(text)}
    for m in _RE_DOB_RAW.finditer(text):
        if m.start() not in dob_ctx_positions:
            flags.append(PiiFlag(
                pii_type="dob",
                severity="low",
                redacted=_mask(m.group()),
                context=_ctx(text, m),
            ))

    # 4. Montana driver's license numbers — HIGH
    for m in _RE_MT_DL.finditer(text):
        # Avoid false-positives from plain case numbers / CFS numbers
        if not re.search(r'(?i)(?:case|cfs|incident|report)\s*(?:no|num|#)?\s*:?\s*$',
                         text[max(0, m.start()-60):m.start()]):
            flags.append(PiiFlag(
                pii_type="dl_number",
                severity="high",
                redacted=_mask(m.group()),
                context=_ctx(text, m),
            ))

    # 5. Exact street addresses — HIGH if residential context, MEDIUM otherwise
    for m in _RE_STREET_ADDR.finditer(text):
        surrounding = text[max(0, m.start()-120):m.end()+120]
        severity = "high" if _RESIDENCE_CONTEXT_WORDS.search(surrounding) else "medium"
        flags.append(PiiFlag(
            pii_type="home_address",
            severity=severity,
            redacted=_mask(m.group()),
            context=_ctx(text, m),
        ))

    return flags


# ---------------------------------------------------------------------------
# Claude audit — public summary, tone, and SEO meta
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are the Montana Blotter Legal Assistant, the pre-publication editor for \
MontanaBlotter.com, a public safety news portal serving Great Falls, MT and \
all 56 Montana counties.

Your responsibilities:
1. Review raw police log text for any remaining Personally Identifiable \
   Information (PII) that regex scans may have missed — especially SSNs, \
   exact private home addresses, unlisted phone numbers, and full dates of birth.
2. Rewrite the content into a professional "Public Summary" suitable for a \
   government-grade public safety portal. Language must be factual, neutral, \
   and free of sensationalism. Use third-person, past tense. Do not editorialize \
   or speculate. Use "individual" or "subject" rather than "suspect" where \
   charges have not been confirmed. All persons are innocent until convicted.
   If the draft already includes a section titled "// Historical Perspective", \
   preserve it as a short closing note and keep it to 1-2 sentences.
3. Assess the tone: flag any phrasing that is inflammatory, biased, \
   unprofessional, or that could expose the organization to liability.
4. Generate an SEO meta-description optimized for Great Falls, MT and \
   Cascade County. It must be 150–160 characters, mention the agency name \
   and date if known, and end with "- MontanaBlotter.com".

Return ONLY valid JSON — no markdown fences, no extra keys — with this structure:
{
  "missed_pii": [
    {"type": "ssn|home_address|phone|dob|dl_number|other", "note": "brief description"}
  ],
  "public_summary": "Full rewritten summary ready for publication.",
  "tone_ok": true,
  "tone_notes": "Empty string if clean; otherwise explain the issues found.",
  "meta_description": "150-160 char SEO description ending with - MontanaBlotter.com"
}
"""


def _build_audit_prompt(raw_text: str, existing_summary: str, agency_name: str,
                        incident_date: str, county: str, pii_flags: list[PiiFlag]) -> str:
    pii_section = ""
    if pii_flags:
        pii_section = "\n\nPII already detected by regex scanner (do not re-flag these):\n"
        for f in pii_flags:
            pii_section += f"  [{f.severity.upper()}] {f.pii_type}: {f.redacted}\n"

    return f"""Review the following blotter content for publication on MontanaBlotter.com.

Agency: {agency_name or 'Unknown Agency'}
Date: {incident_date or 'Unknown'}
County: {county or 'Unknown'}, Montana

--- RAW / DRAFT CONTENT ---
{raw_text or '(none)'}

--- CURRENT DRAFT SUMMARY (AI-generated, needs audit) ---
{existing_summary or '(none)'}
{pii_section}
Perform all four tasks described in your system prompt and return valid JSON only."""


def _parse_claude_json(raw: str) -> dict:
    raw = raw.strip()
    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    try:
        return json.loads(raw)
    except Exception as e:
        logger.error(f"Failed to parse Claude JSON response: {e}. Raw: {raw[:200]}")
        return {}


def _call_claude_audit(
    client: anthropic.Anthropic,
    raw_text: str,
    existing_summary: str,
    agency_name: str,
    incident_date: str,
    county: str,
    pii_flags: list[PiiFlag],
) -> dict:
    prompt = _build_audit_prompt(
        raw_text, existing_summary, agency_name, incident_date, county, pii_flags
    )
    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            timeout=30.0,
        )
        raw = message.content[0].text.strip()
        result = _parse_claude_json(raw)
        return result
    except Exception as e:
        logger.error(f"Claude audit API error: {e}")
        return {}


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _ensure_audit_columns(conn: sqlite3.Connection) -> None:
    """Add audit-related columns to the posts table if they don't exist."""
    new_cols = [
        ("audit_status",     "TEXT DEFAULT 'pending'"),   # pending | clean | flagged | manual_review
        ("pii_flags",        "TEXT"),                      # JSON array
        ("meta_description", "TEXT"),                      # SEO meta tag content
        ("audited_at",       "TEXT"),                      # ISO timestamp
    ]
    for col, definition in new_cols:
        try:
            conn.execute(f"ALTER TABLE posts ADD COLUMN {col} {definition}")
            conn.commit()
            logger.info(f"Added column posts.{col}")
        except sqlite3.OperationalError:
            pass  # Already exists


def _fetch_post(conn: sqlite3.Connection, post_id: int) -> Optional[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()


def _fetch_pending_posts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        "SELECT * FROM posts WHERE audit_status = 'pending' OR audit_status IS NULL "
        "ORDER BY created_at DESC"
    ).fetchall()


def _fetch_posts_for_blotter(conn: sqlite3.Connection, blotter_id: int) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        "SELECT * FROM posts WHERE blotter_id = ? ORDER BY id", (blotter_id,)
    ).fetchall()


def _fetch_raw_records_text(conn: sqlite3.Connection, blotter_id: int) -> str:
    """Assemble raw incident text from the records table for a given blotter."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT incident_type, location, details FROM records WHERE blotter_id = ? ORDER BY date, time",
        (blotter_id,),
    ).fetchall()
    parts = []
    for r in rows:
        parts.append(
            f"{r['incident_type'] or ''} | {r['location'] or ''} | {r['details'] or ''}"
        )
    return "\n".join(parts)


def _save_audit_result(conn: sqlite3.Connection, result: AuditResult) -> None:
    if result.post_id is None:
        return
    has_high = any(f.severity == "high" for f in result.pii_flags)
    if not result.audit_passed and has_high:
        status = "manual_review"
    elif result.audit_passed:
        status = "clean"
    else:
        status = "flagged"
    try:
        flags_json = json.dumps([asdict(f) for f in result.pii_flags], ensure_ascii=False)
    except Exception as e:
        logger.error(f"Failed to serialize PII flags: {e}")
        flags_json = '[]'
    conn.execute(
        """
        UPDATE posts
           SET audit_status     = ?,
               pii_flags        = ?,
               meta_description = ?,
               audited_at       = ?,
               summary          = CASE WHEN ? != '' THEN ? ELSE summary END
         WHERE id = ?
        """,
        (
            status,
            flags_json,
            result.meta_description,
            result.audited_at,
            result.public_summary,
            result.public_summary,
            result.post_id,
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Core audit function
# ---------------------------------------------------------------------------

def audit_post(
    post_id: Optional[int],
    raw_text: str,
    existing_summary: str,
    agency_name: str,
    incident_date: str,
    county: str,
    client: Optional[anthropic.Anthropic] = None,
) -> AuditResult:
    """
    Full audit pipeline for a single post.

    1. Regex PII scan on both raw_text and existing_summary.
    2. Claude audit for missed PII, public summary rewrite, tone, and SEO meta.
    3. Return AuditResult (does NOT write to DB — caller handles persistence).
    """
    combined = f"{raw_text}\n\n{existing_summary}"
    pii_flags = scan_for_pii(combined)

    high_flags = [f for f in pii_flags if f.severity == "high"]
    issues = []

    if client is None:
        try:
            api_key = getattr(config, "ANTHROPIC_API_KEY", None)
            client = anthropic.Anthropic(api_key=api_key) if api_key else None
        except Exception:
            client = None

    claude_result: dict = {}
    if client:
        claude_result = _call_claude_audit(
            client, raw_text, existing_summary,
            agency_name, incident_date, county, pii_flags,
        )
    else:
        logger.error("No Anthropic client — Claude audit skipped. Running in regex-only mode. Posts with HIGH severity PII flags will NOT be auto-cleared.")
        issues.append("Claude audit skipped (no API key configured)")

    # Merge missed PII from Claude into our flag list
    for missed in claude_result.get("missed_pii", []):
        pii_flags.append(PiiFlag(
            pii_type=missed.get("type", "other"),
            severity="high",
            redacted="[Claude-detected]",
            context=missed.get("note", ""),
        ))
        high_flags.append(pii_flags[-1])

    public_summary = claude_result.get("public_summary", existing_summary)
    public_summary = append_historical_perspective(
        public_summary,
        raw_text=raw_text,
        county=county,
        agency_name=agency_name,
    )
    tone_ok        = bool(claude_result.get("tone_ok", True))
    tone_notes     = claude_result.get("tone_notes", "")
    meta_desc      = claude_result.get("meta_description", "")

    if not tone_ok and tone_notes:
        issues.append(f"Tone issue: {tone_notes}")
    if high_flags:
        issues.append(
            f"{len(high_flags)} HIGH-severity PII flag(s) require manual review before publishing."
        )

    audit_passed = len(high_flags) == 0 and tone_ok and not issues

    return AuditResult(
        post_id=post_id,
        audit_passed=audit_passed,
        pii_flags=pii_flags,
        pii_clean=len(high_flags) == 0,
        public_summary=public_summary,
        tone_ok=tone_ok,
        tone_notes=tone_notes,
        meta_description=meta_desc,
        raw_issues=issues,
    )


# ---------------------------------------------------------------------------
# DB-integrated entry points (for pipeline use)
# ---------------------------------------------------------------------------

def audit_post_by_id(post_id: int, db_path: Optional[str] = None) -> AuditResult:
    """Audit a single post by its DB id and save results back to the DB."""
    db_path = db_path or config.DB_PATH
    conn = _connect_db(db_path)
    _ensure_audit_columns(conn)

    post = _fetch_post(conn, post_id)
    if not post:
        conn.close()
        raise ValueError(f"Post {post_id} not found")

    raw_text = _fetch_raw_records_text(conn, post["blotter_id"])

    client = None
    try:
        api_key = getattr(config, "ANTHROPIC_API_KEY", None)
        client = anthropic.Anthropic(api_key=api_key) if api_key else None
    except Exception:
        pass

    result = audit_post(
        post_id=post_id,
        raw_text=raw_text,
        existing_summary=post["summary"] or "",
        agency_name=post["agency_name"] or "",
        incident_date=post["incident_date"] or "",
        county=post["county"] or "",
        client=client,
    )

    _save_audit_result(conn, result)
    conn.close()
    return result


def audit_blotter_posts(blotter_id: int, db_path: Optional[str] = None) -> list[AuditResult]:
    """
    Audit all posts belonging to a blotter batch.
    Called automatically from the ingestion pipeline.
    """
    db_path = db_path or config.DB_PATH
    conn = _connect_db(db_path)
    _ensure_audit_columns(conn)

    posts = _fetch_posts_for_blotter(conn, blotter_id)
    raw_text = _fetch_raw_records_text(conn, blotter_id)

    client = None
    try:
        api_key = getattr(config, "ANTHROPIC_API_KEY", None)
        client = anthropic.Anthropic(api_key=api_key) if api_key else None
    except Exception:
        pass

    results = []
    for post in posts:
        result = audit_post(
            post_id=post["id"],
            raw_text=raw_text,
            existing_summary=post["summary"] or "",
            agency_name=post["agency_name"] or "",
            incident_date=post["incident_date"] or "",
            county=post["county"] or "",
            client=client,
        )
        _save_audit_result(conn, result)
        results.append(result)
        logger.info(
            f"Audited post #{post['id']}: "
            f"{'PASS' if result.audit_passed else 'FLAGGED'}, "
            f"{len(result.pii_flags)} PII flags"
        )

    conn.close()
    return results


def audit_all_pending(db_path: Optional[str] = None) -> list[AuditResult]:
    """Audit every post currently in 'pending' audit_status."""
    db_path = db_path or config.DB_PATH
    conn = _connect_db(db_path)
    _ensure_audit_columns(conn)
    posts = _fetch_pending_posts(conn)
    conn.close()

    results = []
    for post in posts:
        try:
            r = audit_post_by_id(post["id"], db_path)
            results.append(r)
        except Exception as e:
            logger.error(f"Failed to audit post #{post['id']}: {e}")
    return results


# ---------------------------------------------------------------------------
# CLI output helpers
# ---------------------------------------------------------------------------

def _severity_badge(severity: str) -> str:
    badges = {
        "high":   _colorize(" HIGH ", C.BOLD, C.RED),
        "medium": _colorize(" MED  ", C.BOLD, C.YELLOW),
        "low":    _colorize(" LOW  ", C.BOLD, C.DIM),
    }
    return badges.get(severity, severity)


def print_audit_report(result: AuditResult, post_title: str = "") -> None:
    """Pretty-print an AuditResult to stdout."""
    hr = "─" * 70

    print()
    print(_colorize(hr, C.CYAN))
    status_label = (
        _colorize("✔  AUDIT PASSED", C.BOLD, C.GREEN)
        if result.audit_passed
        else _colorize("✘  AUDIT FLAGGED — DO NOT PUBLISH", C.BOLD, C.RED)
    )
    post_ref = f"Post #{result.post_id}" if result.post_id else "Raw text"
    print(f"  {status_label}  |  {post_ref}")
    if post_title:
        print(f"  {_colorize(post_title, C.DIM)}")
    print(_colorize(hr, C.CYAN))

    # PII section
    if result.pii_flags:
        print(_colorize(f"\n  PII FLAGS ({len(result.pii_flags)} detected):", C.BOLD))
        for f in result.pii_flags:
            badge = _severity_badge(f.severity)
            print(f"  [{badge}] {f.pii_type.upper():15s}  {f.redacted}")
            print(f"           {_colorize('Context: ', C.DIM)}"
                  f"{_colorize(f.context[:100], C.DIM)}")
    else:
        print(_colorize("\n  PII: None detected ✔", C.GREEN))

    # Tone section
    print()
    if result.tone_ok:
        print(_colorize("  TONE: Professional and government-grade ✔", C.GREEN))
    else:
        print(_colorize("  TONE: Issues found", C.YELLOW))
        print(f"  {result.tone_notes}")

    # Public summary
    if result.public_summary:
        print(_colorize("\n  PUBLIC SUMMARY:", C.BOLD))
        for line in result.public_summary.splitlines():
            print(f"  {line}")

    # Meta description
    if result.meta_description:
        meta_len = len(result.meta_description)
        meta_color = C.GREEN if 140 <= meta_len <= 160 else C.YELLOW
        print(_colorize(f"\n  SEO META-DESCRIPTION ({meta_len} chars):", C.BOLD))
        print(f"  {_colorize(result.meta_description, meta_color)}")

    # Raw issues
    if result.raw_issues:
        print(_colorize("\n  ISSUES:", C.BOLD, C.YELLOW))
        for issue in result.raw_issues:
            print(f"    • {issue}")

    print(_colorize(hr, C.CYAN))
    print()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="blotter_auditor",
        description="Montana Blotter Legal Assistant — pre-publication PII/tone audit",
    )
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--post-id", type=int, metavar="ID",
        help="Audit a specific post by DB id",
    )
    group.add_argument(
        "--blotter-id", type=int, metavar="ID",
        help="Audit all posts for a blotter batch",
    )
    group.add_argument(
        "--all-pending", action="store_true",
        help="Audit every post with audit_status='pending'",
    )
    group.add_argument(
        "--text", type=str, metavar="TEXT",
        help="Audit raw text directly (no DB write)",
    )
    p.add_argument(
        "--db", type=str, default=None,
        help=f"Override DB path (default: {config.DB_PATH})",
    )
    p.add_argument(
        "--agency", type=str, default="",
        help="Agency name (used with --text)",
    )
    p.add_argument(
        "--county", type=str, default="Cascade",
        help="County name (used with --text, default: Cascade)",
    )
    p.add_argument(
        "--date", type=str, default="",
        help="Incident date YYYY-MM-DD (used with --text)",
    )
    p.add_argument(
        "--json", action="store_true",
        help="Output results as JSON instead of pretty-print",
    )
    return p


def main() -> None:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    parser = _build_arg_parser()
    args = parser.parse_args()
    db_path = args.db or config.DB_PATH

    results: list[AuditResult] = []

    if args.post_id:
        results = [audit_post_by_id(args.post_id, db_path)]

    elif args.blotter_id:
        results = audit_blotter_posts(args.blotter_id, db_path)
        if not results:
            print(f"No posts found for blotter #{args.blotter_id}.")
            sys.exit(0)

    elif args.all_pending:
        results = audit_all_pending(db_path)
        if not results:
            print("No pending posts to audit.")
            sys.exit(0)

    elif args.text:
        # Standalone raw-text mode — no DB write
        client = None
        try:
            api_key = getattr(config, "ANTHROPIC_API_KEY", None)
            client = anthropic.Anthropic(api_key=api_key) if api_key else None
        except Exception:
            pass

        result = audit_post(
            post_id=None,
            raw_text=args.text,
            existing_summary="",
            agency_name=args.agency,
            incident_date=args.date,
            county=args.county,
            client=client,
        )
        results = [result]

    if getattr(args, "json"):
        print(json.dumps(
            [asdict(r) for r in results],
            indent=2,
            default=str,
            ensure_ascii=False,
        ))
    else:
        for r in results:
            print_audit_report(r)

    # Exit code: 0 if all passed, 1 if any flagged
    if any(not r.audit_passed for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
