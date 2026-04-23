#!/usr/bin/env python3
"""
charge_explainer_worker.py — Evergreen SEO charge explainer pages.

Generates a plain-language explainer for each legally significant incident
type that appears in Montana Blotter records. Content is written by Claude
and stored in the charge_explainers table, served at /laws/charge/<slug>.

Safe to rerun: skips existing slugs unless --force is passed.

Usage:
    python charge_explainer_worker.py              # top 25 charge types
    python charge_explainer_worker.py --limit 10   # top N
    python charge_explainer_worker.py --charge "Assault"  # single charge
    python charge_explainer_worker.py --force       # regenerate existing
    python charge_explainer_worker.py --stdout      # print, don't save
    python charge_explainer_worker.py --new-only    # only types not yet in DB

Cron: 35 7 * * 1  (Mondays 7:35am, after weekly_safety_report)
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
from typing import Optional

import config
from blotter_analytics import CATEGORY_LABELS

DB_PATH = config.DB_PATH
LOG_PATH = "/root/montanablotter/charge_explainer.log"

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Charge categories worth generating explainers for.
# Excludes pure operational/admin call types (welfare checks, patrol, etc.)
EXPLAINER_CATEGORIES = {'violent', 'domestic', 'drug', 'dui', 'weapons', 'property', 'traffic'}

# Operational incident types to skip even if they match a category
SKIP_TYPES = {
    'traffic stop', 'motor vehicle stop', 'patrol check', 'follow up',
    'police incident', 'unknown', 'stop', 'check', 'citizen', 'security',
    '911 hang up', 'hazards', 'courts', 'court papers served',
    'person to be removed', 'complaint', 'assist citizen', 'public assist',
    'needs officer\'s advice', 'animal', 'animal control',
    'welfare check', 'person needs assistance',
}

_SYSTEM = (
    "You are a legal journalist writing plain-language charge explainers for "
    "MontanaBlotter.com, a community news site in Montana. Your audience is "
    "general readers who saw a charge type in a police blotter and want to "
    "understand what it means. Tone: factual, clear, authoritative. "
    "Never speculate about specific cases or individuals. "
    "Always note that readers should consult an attorney for legal advice. "
    "Respond with valid JSON only — no markdown fences, no extra text."
)


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:60]


def _anthropic_client():
    try:
        import anthropic
        key = getattr(config, "ANTHROPIC_API_KEY", None)
        return anthropic.Anthropic(api_key=key) if key else None
    except Exception:
        return None


def _build_prompt(incident_type: str, category: str, occurrence_count: int) -> str:
    cat_label = CATEGORY_LABELS.get(category, category)
    return f"""
Write a plain-language explainer for the charge or incident type "{incident_type}" as it
appears in Montana police blotters.

Context:
- This charge type has appeared {occurrence_count} times in Montana blotter records.
- It falls under the "{cat_label}" category.
- Montana Code Annotated (MCA) governs criminal law in Montana.

Return a JSON object with exactly these keys:
- "title": SEO headline, 55-70 chars. Pattern: "What Is [Charge] in Montana? MCA Explained"
- "excerpt": 150-160 char meta description. Include "Montana" and the charge name.
- "statute_ref": The most relevant MCA section (e.g., "MCA §45-5-201") or null if not applicable.
- "body": Full markdown article, 450-600 words structured as:
  ## What Is {incident_type}?
  Plain-language definition. What must prosecutors prove. How it typically appears in a police blotter.

  ## Montana Law
  Reference the specific MCA section(s). Summarize the legal elements concisely.
  If this is a common-law term rather than a specific statute, explain how Montana courts classify it.

  ## Typical Penalties
  Class of offense (felony/misdemeanor), sentencing range, fines. Note that actual sentences vary.

  ## How It Appears in Blotters
  2-3 sentences on what a blotter entry for this charge typically looks like and what it means for readers.

  ## Next Steps
  Brief guidance: presumption of innocence, right to counsel, court process overview.
  End with a note that readers should consult a licensed Montana attorney for legal advice.

Do not reference any specific individual, case, or jurisdiction name within Montana.
Write for a general Montana audience.
""".strip()


def _call_claude(client, incident_type: str, category: str, count: int) -> Optional[dict]:
    if client is None:
        return None
    try:
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            system=_SYSTEM,
            messages=[{"role": "user", "content": _build_prompt(incident_type, category, count)}],
        )
        raw = msg.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        parsed = json.loads(raw)
        if all(k in parsed for k in ("title", "excerpt", "body")):
            return parsed
        logger.warning("Claude response missing keys for %s", incident_type)
        return None
    except Exception as e:
        logger.warning("Claude API error for %s: %s", incident_type, e)
        return None


def _fallback_content(incident_type: str, category: str) -> dict:
    cat_label = CATEGORY_LABELS.get(category, category)
    title = f"What Is {incident_type} in Montana? Charge Explained"[:70]
    excerpt = (
        f"Learn what {incident_type} means in a Montana police blotter, "
        f"how it is classified under Montana law, and what comes next."
    )[:160]
    body = f"""## What Is {incident_type}?

**{incident_type}** is a {cat_label.lower()} charge that appears in Montana law enforcement blotters.
When this charge appears in a blotter record, it indicates that law enforcement documented
an incident of this type during a call for service or arrest.

## Montana Law

Montana criminal offenses are defined under the Montana Code Annotated (MCA).
Charges in the "{cat_label}" category are generally governed by Title 45 of the MCA
(Montana Criminal Code). The specific statute depends on the circumstances of the incident.

## Typical Penalties

Penalties in Montana vary based on whether the offense is classified as a felony or
misdemeanor and the specific facts of the case. Sentences can range from fines and
probation to incarceration.

## How It Appears in Blotters

When you see **{incident_type}** in a Montana police blotter, it means an officer
responded to or documented an incident of this type. A blotter entry is a public record
of law enforcement activity — it does not constitute a criminal conviction.

## Next Steps

Every person charged with a crime is presumed innocent until proven guilty in a court
of law. Anyone facing criminal charges has the right to legal representation.
**Consult a licensed Montana attorney for legal advice specific to your situation.**
"""
    return {"title": title, "excerpt": excerpt, "body": body, "statute_ref": None}


def _candidate_charges(conn: sqlite3.Connection, limit: int) -> list[tuple[str, str, int]]:
    """Return (incident_type, charge_category, count) for explainer-worthy types."""
    rows = conn.execute(
        """
        SELECT incident_type, charge_category, COUNT(*) as n
        FROM records
        WHERE charge_category IN ('violent','domestic','drug','dui','weapons','property','traffic')
          AND length(incident_type) > 3
        GROUP BY incident_type, charge_category
        ORDER BY n DESC
        LIMIT ?
        """,
        (limit * 3,),  # over-fetch to allow for deduplication
    ).fetchall()

    seen_slugs: set[str] = set()
    results: list[tuple[str, str, int]] = []
    for row in rows:
        itype, cat, n = row[0], row[1], row[2]
        if itype.lower().strip() in SKIP_TYPES:
            continue
        slug = _slugify(itype)
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        results.append((itype, cat, n))
        if len(results) >= limit:
            break
    return results


def generate_explainers(
    charge_filter: Optional[str] = None,
    limit: int = 25,
    force: bool = False,
    stdout: bool = False,
    new_only: bool = False,
) -> list[dict]:
    client = _anthropic_client()
    if client is None:
        logger.warning("No Anthropic API key — using fallback content")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    if charge_filter:
        # Get category for the specific charge
        row = conn.execute(
            "SELECT charge_category, COUNT(*) as n FROM records WHERE incident_type=? GROUP BY charge_category",
            (charge_filter,),
        ).fetchone()
        if not row:
            print(f"No records found for '{charge_filter}'")
            conn.close()
            return []
        candidates = [(charge_filter, row[0] or 'other', row[1])]
    else:
        candidates = _candidate_charges(conn, limit)

    results = []
    for incident_type, category, count in candidates:
        slug = _slugify(incident_type)

        existing = conn.execute(
            "SELECT id FROM charge_explainers WHERE slug=?", (slug,)
        ).fetchone()

        if existing and not force:
            if new_only or not stdout:
                continue  # skip — already exists

        content = _call_claude(client, incident_type, category, count)
        method = "claude"
        if content is None:
            content = _fallback_content(incident_type, category)
            method = "fallback"

        # Merge statute_ref from Claude response (may be absent in fallback)
        statute_ref = content.get("statute_ref")

        if stdout:
            print(f"\n{'='*60}")
            print(f"CHARGE: {incident_type}  |  {category}  |  n={count}  |  {method}")
            print(f"SLUG: {slug}")
            print(f"TITLE: {content['title']}")
            print(f"STATUTE: {statute_ref}")
            print(f"EXCERPT: {content['excerpt']}")
            print()
            print(content["body"][:400] + "...")
            results.append({"incident_type": incident_type, "action": "printed", "method": method})
            continue

        if existing:
            conn.execute(
                """
                UPDATE charge_explainers
                SET title=?, body=?, excerpt=?, statute_ref=?, charge_category=?,
                    generated_by=?, updated_at=datetime('now')
                WHERE slug=?
                """,
                (content["title"], content["body"], content["excerpt"],
                 statute_ref, category, method, slug),
            )
            action = "updated"
        else:
            conn.execute(
                """
                INSERT INTO charge_explainers
                    (incident_type, slug, title, body, excerpt, statute_ref, charge_category, generated_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (incident_type, slug, content["title"], content["body"],
                 content["excerpt"], statute_ref, category, method),
            )
            action = "created"

        conn.commit()
        logger.info("%s — %s (%s, n=%d)", incident_type, action, method, count)
        print(f"{incident_type}: {action} ({method}, {count} occurrences)")
        results.append({"incident_type": incident_type, "slug": slug, "action": action, "method": method})

    conn.close()
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate charge explainer pages.")
    parser.add_argument("--charge", help="Generate for a single charge type only.")
    parser.add_argument("--limit", type=int, default=25, help="Max charge types to process (default: 25).")
    parser.add_argument("--force", action="store_true", help="Regenerate existing explainers.")
    parser.add_argument("--stdout", action="store_true", help="Print output, don't save.")
    parser.add_argument("--new-only", action="store_true", help="Only generate for charge types not yet in DB.")
    args = parser.parse_args()

    generate_explainers(
        charge_filter=args.charge,
        limit=args.limit,
        force=args.force,
        stdout=args.stdout,
        new_only=args.new_only,
    )


if __name__ == "__main__":
    main()
