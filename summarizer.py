import json
import logging
import os
import re
import sqlite3
from typing import Optional

import requests

import config
from dedupe import incident_key_set
from pipeline_state import log_pipeline_event

DB_PATH = config.DB_PATH
DB_TIMEOUT_SECONDS = float(getattr(config, "DB_TIMEOUT_SECONDS", 30))
DB_BUSY_TIMEOUT_MS = int(getattr(config, "DB_BUSY_TIMEOUT_MS", 30000))
logger = logging.getLogger(__name__)


def _connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {DB_BUSY_TIMEOUT_MS}")
    return conn


# ---------------------------------------------------------------------------
# Agency detection
# ---------------------------------------------------------------------------

def _detect_agency(content: str, sender_email: Optional[str] = None,
                   filename: Optional[str] = None, county: Optional[str] = None) -> tuple[str, str]:
    """
    Return (agency_type, agency_name) by checking filename first, then
    content keywords, then sender_email.
    agency_type: 'sheriff' | 'police' | 'other'
    """
    # Known agency abbreviations in filenames
    fname = (filename or "").upper()
    if "GCSO" in fname:
        return "sheriff", f"{county or 'Gallatin'} County Sheriff's Office"
    if "LCSO" in fname:
        return "sheriff", f"{county or 'Lewis and Clark'} County Sheriff's Office"
    if re.search(r'\bSO\b', fname):          # e.g. "MTSO", "YCSO"
        return "sheriff", f"{county or ''} County Sheriff's Office".strip()
    if re.search(r'\bPD\b', fname):
        return "police", f"{county or ''} Police Department".strip()

    content_upper = content.upper() if content else ""

    if "SHERIFF" in content_upper:
        m = re.search(r"([A-Za-z\s]+(?:County)?\s+Sheriff(?:'?s)?\s+Office)", content, re.IGNORECASE)
        return "sheriff", (m.group(1).strip() if m else f"{county or ''} Sheriff's Office".strip())

    if "POLICE DEPARTMENT" in content_upper or re.search(r'\bPD\b', content or ""):
        m = re.search(r"([A-Za-z\s]+Police\s+Department)", content, re.IGNORECASE)
        return "police", (m.group(1).strip() if m else f"{county or ''} Police Department".strip())

    if sender_email:
        # Handle "Display Name <user@domain>" format
        addr_match = re.search(r'[\w.+-]+@[\w.-]+', sender_email)
        if addr_match:
            addr = addr_match.group(0).lower()
            local, domain = addr.split('@', 1)
            if 'sheriff' in local or 'sheriff' in domain:
                return "sheriff", f"{county or ''} Sheriff's Office".strip()
            if 'police' in local or 'pd' == local:
                return "police", f"{county or ''} Police Department".strip()
            # City email domains (helenamt.gov, greatfallsmt.gov, etc.) indicate city police
            city_match = re.match(r'([a-z]+)mt\.gov', domain)
            if city_match:
                city_name = city_match.group(1).replace('greatfalls', 'Great Falls') \
                    .replace('helena', 'Helena').replace('missoula', 'Missoula') \
                    .replace('billings', 'Billings').replace('bozeman', 'Bozeman') \
                    .replace('havre', 'Havre').replace('kalispell', 'Kalispell')
                city_name = city_name.title() if city_name == city_match.group(1) else city_name
                return "police", f"{city_name} Police Department"
            # ci.<city>.mt.us or ci.<city>.<state>.us patterns
            city_match2 = re.match(r'ci\.([^.]+)\.', domain)
            if city_match2:
                city_name = city_match2.group(1).replace('-', ' ').title()
                return "police", f"{city_name} Police Department"

    return "other", ""


# ---------------------------------------------------------------------------
# Core public function
# ---------------------------------------------------------------------------

def generate_posts(
    blotter_id: int,
    sender_email: Optional[str] = None,
    ingestion_job_id: Optional[int] = None,
) -> int:
    openai_api_key = os.getenv("OPENAI_API_KEY") or getattr(config, "OPENAI_API_KEY", None)
    openai_model = os.getenv("OPENAI_MODEL") or getattr(config, "OPENAI_MODEL", "gpt-4o-mini")

    anthropic_client = None
    try:
        import anthropic
        api_key = getattr(config, "ANTHROPIC_API_KEY", None)
        anthropic_client = anthropic.Anthropic(api_key=api_key) if api_key else None
    except ImportError:
        anthropic_client = None

    if not openai_api_key and anthropic_client is None:
        logger.warning("No LLM API key configured (OPENAI_API_KEY/ANTHROPIC_API_KEY) – using fallback digest")

    conn = _connect_db()
    cursor = conn.cursor()

    # Skip if a post already exists for this blotter
    existing = cursor.execute(
        "SELECT id FROM posts WHERE blotter_id = ?", (blotter_id,)
    ).fetchone()
    if existing:
        if ingestion_job_id is not None:
            log_pipeline_event(
                ingestion_job_id,
                'summary_method',
                'ok',
                {'method': 'existing_post', 'generated': False, 'post_id': int(existing['id'])},
            )
        conn.close()
        logger.info(f"Post already exists for blotter {blotter_id} – skipping")
        return 0

    # Fetch blotter metadata
    blotter_row = cursor.execute(
        "SELECT county, upload_date, filename FROM blotters WHERE id = ?", (blotter_id,)
    ).fetchone()
    blotter_county = blotter_row["county"] if blotter_row else "Unknown"
    blotter_date = (blotter_row["upload_date"] or "")[:10] if blotter_row else ""
    blotter_filename = blotter_row["filename"] if blotter_row else ""

    # Fetch all records for this blotter, sorted chronologically
    rows = cursor.execute(
        """
        SELECT
            COALESCE(r.incident_type, r.incident, '') AS incident_type,
            r.location,
            r.date,
            COALESCE(r.time, '') AS time,
            r.county,
            COALESCE(r.officer, '') AS officer,
            COALESCE(r.details, r.summary, '') AS details
        FROM records r
        WHERE r.blotter_id = ?
        ORDER BY r.date, r.time
        """,
        (blotter_id,),
    ).fetchall()

    if not rows:
        conn.close()
        logger.info(f"No records for blotter {blotter_id} – nothing to post")
        return 0

    # Determine county and date from first record
    county = rows[0]["county"] or blotter_county
    incident_date = rows[0]["date"] or blotter_date
    current_keys = incident_key_set(rows, county=county)

    if current_keys:
        candidate_posts = cursor.execute(
            """
            SELECT id, blotter_id
            FROM posts
            WHERE county = ?
              AND incident_date = ?
              AND blotter_id != ?
            ORDER BY created_at DESC
            """,
            (county, incident_date, blotter_id),
        ).fetchall()
        for candidate in candidate_posts:
            sibling_rows = cursor.execute(
                """
                SELECT
                    COALESCE(r.incident_type, r.incident, '') AS incident_type,
                    r.location,
                    r.date,
                    COALESCE(r.time, '') AS time,
                    r.county,
                    COALESCE(r.details, r.summary, '') AS details,
                    COALESCE(r.cfs_number, '') AS cfs_number
                FROM records r
                WHERE r.blotter_id = ?
                """,
                (candidate["blotter_id"],),
            ).fetchall()
            sibling_keys = incident_key_set(sibling_rows, county=county)
            if not sibling_keys:
                continue
            overlap = len(current_keys & sibling_keys) / max(len(current_keys), 1)
            if overlap >= 0.7:
                if ingestion_job_id is not None:
                    log_pipeline_event(
                        ingestion_job_id,
                        'summary_method',
                        'ok',
                        {
                            'method': 'skipped_duplicate_post',
                            'generated': False,
                            'matched_post_id': int(candidate['id']),
                            'overlap_ratio': round(overlap, 3),
                        },
                    )
                conn.close()
                logger.info(
                    f"Skipping near-duplicate post for blotter {blotter_id}; "
                    f"overlaps post {candidate['id']} at {overlap:.0%}"
                )
                return 0

    # Build combined text for agency detection
    combined_text = " ".join(
        f"{r['incident_type']} {r['location']} {r['details']}" for r in rows
    )
    agency_type, agency_name = _detect_agency(
        combined_text, sender_email, filename=blotter_filename, county=county
    )

    incident_lines = []
    for r in rows:
        time_str = r["time"] or ""
        itype = r["incident_type"] or "Unknown"
        loc = r["location"] or ""
        detail = r["details"] or ""
        incident_lines.append(f"- {time_str}  {itype}  |  {loc}  |  {detail}".strip(" |"))

    post_data = {}
    summary_method = {'method': 'fallback', 'provider': 'fallback', 'generated': False}
    if openai_api_key:
        post_data = _call_openai(
            api_key=openai_api_key,
            model=openai_model,
            county=county,
            date=incident_date,
            agency_type=agency_type,
            agency_name=agency_name,
            filename=blotter_filename,
            incident_lines=incident_lines,
        )
        if post_data:
            summary_method = {'method': 'ai_generated', 'provider': 'openai', 'generated': True}

    if not post_data and anthropic_client is not None:
        post_data = _call_claude(
            client=anthropic_client,
            county=county,
            date=incident_date,
            agency_type=agency_type,
            agency_name=agency_name,
            filename=blotter_filename,
            incident_lines=incident_lines,
        )
        if post_data:
            summary_method = {'method': 'ai_generated', 'provider': 'anthropic', 'generated': True}

    final_agency_type = post_data.get("agency_type") or agency_type
    final_agency_name = post_data.get("agency_name") or agency_name
    city = post_data.get("city") or ""

    cursor.execute(
        """
        INSERT INTO posts
            (blotter_id, title, summary, city, county,
             agency_type, agency_name, incident_date, incident_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            blotter_id,
            post_data.get("title") or f"Daily Activity Report – {final_agency_name or county}",
            post_data.get("summary") or _fallback_summary(agency_name, rows),
            city,
            county,
            final_agency_type,
            final_agency_name,
            incident_date,
            "Daily Digest",
        ),
    )
    post_id = int(cursor.lastrowid or 0)
    conn.commit()
    conn.close()

    # Optional social automation: enqueue freshly-generated posts for Facebook publishing.
    try:
        from facebook_publisher import auto_queue_post_if_enabled
        auto_queue_post_if_enabled(post_id)
    except Exception as exc:
        logger.warning("facebook auto-queue failed for post_id=%s: %s", post_id, exc)

    if ingestion_job_id is not None:
        details = {
            **summary_method,
            'post_id': post_id,
        }
        log_pipeline_event(ingestion_job_id, 'summary_method', 'ok', details)

    logger.info(f"generate_posts(blotter_id={blotter_id}): created 1 digest post")
    return 1


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _llm_user_prompt(county, date, agency_type, agency_name, filename, incident_lines) -> str:
    agency_label = agency_name or f"{county} County {'Sheriff' if agency_type == 'sheriff' else 'Police'}"
    incidents_block = "\n".join(incident_lines)
    return f"""Write a daily police activity report for publication.

Agency: {agency_label}
Agency type: {agency_type}
Source file: {filename}
Date: {date}
County: {county}

Incidents (time | type | location | details):
{incidents_block}

Format the summary with a short intro sentence followed by EVERY incident on its own line — one line per incident, no grouping, no skipping:

"The [Agency Name] responded to [N] incidents. Below is a full log:

[HH:MM AM/PM] – [Incident type] at [location].
[HH:MM AM/PM] – [Incident type] at [location].
..."

Rules:
- Include EVERY incident — do not skip or omit any.
- One line per incident, no combining or grouping.
- Use natural times like "8:20 AM" not raw timestamps.
- Keep each line concise: time – type at location.

Return ONLY valid JSON with these keys:
{{
  "title": "Daily Police Activity Report - [Agency Name]",
  "summary": "[the full formatted report as described above]",
  "city": "primary city or town if determinable, else empty string",
  "agency_type": "sheriff or police or other",
  "agency_name": "full agency name"
}}"""


def _parse_json_block(raw: str) -> dict:
    if not raw:
        return {}
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except Exception:
        return {}


def _call_openai(api_key, model, county, date, agency_type, agency_name, filename, incident_lines) -> dict:
    user_content = _llm_user_prompt(county, date, agency_type, agency_name, filename, incident_lines)
    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a journalist writing daily police activity summaries for a public news site. "
                            "Write clearly and factually. Respond with valid JSON only."
                        ),
                    },
                    {"role": "user", "content": user_content},
                ],
                "temperature": 0.2,
            },
            timeout=60,
        )
        resp.raise_for_status()
        payload = resp.json()
        raw = (
            payload.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        parsed = _parse_json_block(raw)
        if parsed:
            return parsed
        logger.warning("OpenAI response was not valid JSON – using fallback/provider fallback")
        return {}
    except Exception as e:
        logger.warning(f"OpenAI API error: {e} – trying Anthropic or fallback digest")
        return {}


def _call_claude(client, county, date, agency_type, agency_name, filename, incident_lines) -> dict:
    """
    Call Claude to produce a single daily digest post.
    Returns dict with keys: title, summary, city, agency_type, agency_name.
    """
    if client is None:
        return {}

    user_content = _llm_user_prompt(county, date, agency_type, agency_name, filename, incident_lines)

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=(
                "You are a journalist writing daily police activity summaries for a public news site. "
                "Write clearly and factually. Respond with valid JSON only."
            ),
            messages=[{"role": "user", "content": user_content}],
        )
        raw = message.content[0].text.strip()
        parsed = _parse_json_block(raw)
        if parsed:
            return parsed
        logger.warning("Claude response was not valid JSON – using fallback digest")
        return {}
    except Exception as e:
        logger.warning(f"Claude API error: {e} – using fallback digest")
        return {}


def _fallback_summary(agency_name: str, rows) -> str:
    """Plain-text digest when Claude is unavailable."""
    lines = [f"The {agency_name or 'agency'} responded to the following incidents:"]
    for r in rows:
        time_str = r["time"] or ""
        itype = r["incident_type"] or "Incident"
        loc = r["location"] or ""
        lines.append(f"{time_str} – {itype}" + (f" at {loc}" if loc else ""))
    return "\n".join(lines)
