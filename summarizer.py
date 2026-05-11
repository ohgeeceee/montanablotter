import json
import logging
import os
import re
import sqlite3
from typing import Optional

import requests

from agency_normalization import normalize_agency_identity, normalize_post_title
import config
from dedupe import incident_key_set
from historical_context import append_historical_perspective
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
            COALESCE(r.details, r.summary, '') AS details,
            COALESCE(r.charge_category, '') AS charge_category,
            COALESCE(r.cfs_number, '') AS cfs_number
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
    agency_name, agency_type = normalize_agency_identity(
        agency_name,
        agency_type,
        county=county,
    )

    def _extract_key_details(details: str, charge_category: str, cfs: str) -> str:
        """Extract only the most readable and relevant details from verbose blotter data."""
        if not details:
            return ""
        parts = []
        # Severity
        severity_match = re.search(r'Severity:\s*(Felony|Misdemeanor)', details, re.IGNORECASE)
        if severity_match:
            parts.append(f"{severity_match.group(1)}")
        # Concise offense description (first 'Offense:' line, skip statute-only entries)
        offense_match = re.search(r'Offense:\s*([^;]+)', details)
        if offense_match:
            offense = offense_match.group(1).strip()
            if not offense.startswith('45-'):
                parts.append(offense)
        # Case number
        case_match = re.search(r'Case number:\s*(\S+)', details, re.IGNORECASE)
        if case_match:
            parts.append(f"Case {case_match.group(1)}")
        # If details is short and simple (no structured fields), use it directly
        if len(details) < 60 and not re.search(r'(Offense:|Statute|Severity:|Case number:)', details):
            parts.insert(0, details)
        # Charge category
        if charge_category and charge_category.lower() not in ('unknown', '', 'n/a'):
            if charge_category not in parts:
                parts.append(f"Cat: {charge_category}")
        # CFS number
        if cfs and cfs.lower() not in ('unknown', '', 'n/a'):
            parts.append(f"CFS: {cfs}")
        return " — ".join(parts) if parts else ""

    incident_lines = []
    for r in rows:
        time_str = r["time"] or ""
        itype = r["incident_type"] or "Unknown"
        loc = r["location"] or ""
        charge_cat = r["charge_category"] or ""
        cfs = r["cfs_number"] or ""
        rich_detail = _extract_key_details(r["details"] or "", charge_cat, cfs)
        incident_lines.append(f"- {time_str}  {itype}  |  {loc}  |  {rich_detail}".strip(" |"))

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

    city = post_data.get("city") or ""
    final_agency_name, final_agency_type = normalize_agency_identity(
        post_data.get("agency_name") or agency_name,
        post_data.get("agency_type") or agency_type,
        county=county,
        city=city,
    )
    final_title = normalize_post_title(
        post_data.get("title") or f"Daily Activity Report - {final_agency_name or county}",
        post_data.get("agency_name") or agency_name,
        final_agency_name,
        county=county,
        city=city,
        agency_type=final_agency_type,
    )
    raw_summary = post_data.get("summary") or _fallback_summary(final_agency_name, rows)
    # Convert sqlite3.Row list to plain dicts for append_historical_perspective
    _rows_as_dicts = [dict(r) for r in rows]
    final_summary = append_historical_perspective(
        raw_summary,
        records=_rows_as_dicts,
        county=county,
        agency_name=final_agency_name,
        agency_type=final_agency_type,
    )

    # Auto-generate a compelling meta description for SEO
    meta_description = _generate_meta_description(final_agency_name, county, incident_date, rows, raw_summary)

    cursor.execute(
        """
        INSERT INTO posts
            (blotter_id, title, summary, city, county,
             agency_type, agency_name, incident_date, incident_type, meta_description)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            blotter_id,
            final_title,
            final_summary,
            city,
            county,
            final_agency_type,
            final_agency_name,
            incident_date,
            "Daily Digest",
            meta_description,
        ),
    )
    post_id = int(cursor.lastrowid or 0)
    conn.commit()
    conn.close()

    if ingestion_job_id is not None:
        details = {
            **summary_method,
            'post_id': post_id,
            'historical_perspective': '// Historical Perspective:' in final_summary,
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

[HH:MM AM/PM] – [Incident type] at [location]. [Optional: severity, statute, case number, or offense details if available].
[HH:MM AM/PM] – [Incident type] at [location]. [Optional: severity, statute, case number, or offense details if available].
..."

Rules:
- Include EVERY incident — do not skip or omit any.
- One line per incident, no combining or grouping.
- Use natural times like "8:20 AM" not raw timestamps.
- Include available details from the incident (severity, statute code, case number, CFS number, specific offense description) to make each entry informative and credible.
- Keep the core line concise but append 1-2 key details when available: e.g., "Felony – Statute 45-6-301(1)[1] – Case BI26-01831" or "Misdemeanor – Criminal Trespass To Property".

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
        logger.error("OpenAI response was not valid JSON – using fallback/provider fallback")
        return {}
    except Exception as e:
        logger.error(f"OpenAI API error: {e} – trying Anthropic or fallback digest")
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
        if not parsed.get("title") or not parsed.get("summary"):
            logger.error("Claude response missing required fields (title/summary), using fallback digest")
            return {}
        if parsed:
            return parsed
        logger.error("Claude response was not valid JSON – using fallback digest")
        return {}
    except Exception as e:
        logger.error(f"Claude API error: {e} – using fallback digest")
        return {}


def _fallback_summary(agency_name: str, rows) -> str:
    """Plain-text digest when Claude/OpenAI is unavailable."""
    lines = [f"The {agency_name or 'agency'} responded to the following incidents:"]
    for r in rows:
        # sqlite3.Row supports dict-style access via __getitem__ but not .get()
        time_str = r["time"] or ""
        itype = r["incident_type"] or "Incident"
        loc = r["location"] or ""
        detail = r["details"] or ""
        charge_cat = r["charge_category"] or ""
        cfs = r["cfs_number"] or ""
        # Build rich detail suffix using same extraction logic
        suffix_parts = []
        severity_match = re.search(r'Severity:\s*(Felony|Misdemeanor)', detail, re.IGNORECASE)
        if severity_match:
            suffix_parts.append(f"{severity_match.group(1)}")
        offense_match = re.search(r'Offense:\s*([^;]+)', detail)
        if offense_match:
            offense = offense_match.group(1).strip()
            if not offense.startswith('45-'):
                suffix_parts.append(offense)
        case_match = re.search(r'Case number:\s*(\S+)', detail, re.IGNORECASE)
        if case_match:
            suffix_parts.append(f"Case {case_match.group(1)}")
        if len(detail) < 60 and not re.search(r'(Offense:|Statute|Severity:|Case number:)', detail):
            suffix_parts.insert(0, detail)
        if charge_cat and charge_cat.lower() not in ('unknown', '', 'n/a'):
            if charge_cat not in suffix_parts:
                suffix_parts.append(f"Cat: {charge_cat}")
        if cfs and cfs.lower() not in ('unknown', '', 'n/a'):
            suffix_parts.append(f"CFS: {cfs}")
        suffix = f" ({' — '.join(suffix_parts)})" if suffix_parts else ""
        lines.append(f"{time_str} – {itype}" + (f" at {loc}" if loc else "") + suffix)
    return "\n".join(lines)


def _generate_meta_description(agency_name: str, county: str, incident_date: str, rows, raw_summary: str) -> str:
    """Generate a compelling meta description (150-160 chars) for SEO."""
    import re
    
    # Count incidents
    incident_count = len(rows)
    
    # Extract severity highlights
    felonies = sum(1 for r in rows if re.search(r'Severity:\s*Felony', r["details"] or '', re.IGNORECASE))
    misdemeanors = sum(1 for r in rows if re.search(r'Severity:\s*Misdemeanor', r["details"] or '', re.IGNORECASE))
    
    # Extract unique incident types (top 3)
    type_counts = {}
    for r in rows:
        itype = r["incident_type"] or "Incident"
        type_counts[itype] = type_counts.get(itype, 0) + 1
    top_types = sorted(type_counts.items(), key=lambda x: x[1], reverse=True)[:3]
    type_names = [t[0] for t in top_types]
    
    # Build description pieces
    pieces = []
    
    # Start with agency + county + date
    if agency_name:
        pieces.append(f"{agency_name}")
    elif county:
        pieces.append(f"{county} County law enforcement")
    else:
        pieces.append("Montana law enforcement")
    
    # Add incident count
    pieces.append(f"responded to {incident_count} incident{'s' if incident_count != 1 else ''}")
    
    # Add date if available
    if incident_date:
        pieces.append(f"on {incident_date}")
    
    # Add severity highlights if any
    severity_parts = []
    if felonies > 0:
        severity_parts.append(f"{felonies} felony{'s' if felonies != 1 else ''}")
    if misdemeanors > 0:
        severity_parts.append(f"{misdemeanors} misdemeanor{'s' if misdemeanors != 1 else ''}")
    if severity_parts:
        pieces.append(f"including {', '.join(severity_parts)}")
    
    # Add top incident types
    if type_names:
        type_str = ", ".join(type_names[:2])
        pieces.append(f"— {type_str}")
    
    # Add source traceability hook
    pieces.append("Full blotter with case numbers and source records.")
    
    # Join and truncate to ~155 chars
    desc = " ".join(pieces)
    if len(desc) > 155:
        # Try a shorter version
        short_pieces = []
        if agency_name:
            short_pieces.append(f"{agency_name}")
        elif county:
            short_pieces.append(f"{county} County")
        else:
            short_pieces.append("Montana")
        short_pieces.append(f"{incident_count} incidents")
        if incident_date:
            short_pieces.append(incident_date)
        if felonies > 0:
            short_pieces.append(f"{felonies} felony")
        short_pieces.append("Full blotter with case numbers.")
        desc = " ".join(short_pieces)
        if len(desc) > 155:
            desc = desc[:152] + "..."
    
    return desc
