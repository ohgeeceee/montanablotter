#!/usr/bin/env python3
"""
weekly_safety_report.py — Per-county public safety trend reports.

Generates one SEO blog post per active county using charge-category analytics
from services.blotter.analytics.py and Claude-written narrative.  Safe to rerun: uses
date-based slugs and skips duplicates unless --force is passed.

Cron: 30 7 * * 1  (Mondays 7:30am, after weekly_county_digest)

Usage:
    python weekly_safety_report.py                  # all active counties
    python weekly_safety_report.py --county Cascade # single county
    python weekly_safety_report.py --stdout         # print, don't save
    python weekly_safety_report.py --force          # overwrite existing
    python weekly_safety_report.py --draft          # save as unpublished
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
from datetime import UTC, date, datetime, timedelta
from typing import Optional

import config
from services.blotter.analytics import aggregate_by_county, CATEGORY_LABELS

DB_PATH = config.DB_PATH
AUTHOR = "Montana Blotter Data Desk"
LOG_PATH = "/root/montanablotter/logs/weekly_safety_report.log"
WINDOW_DAYS = 7
MIN_RECORDS = 5  # skip counties with fewer records than this

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:80]


def _anthropic_client():
    try:
        import anthropic
        api_key = getattr(config, "ANTHROPIC_API_KEY", None)
        return anthropic.Anthropic(api_key=api_key) if api_key else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Active county detection
# ---------------------------------------------------------------------------

def _active_counties(conn: sqlite3.Connection, days: int = WINDOW_DAYS) -> list[str]:
    """Return counties with at least MIN_RECORDS in the last `days` days."""
    # Use the same MM/DD/YY → ISO conversion as blotter_analytics
    ISO = "(CASE WHEN length(date)=8 THEN '20'||substr(date,7,2)||'-'||substr(date,1,2)||'-'||substr(date,4,2) ELSE date END)"
    cutoff = (datetime.now(UTC).date() - timedelta(days=days)).isoformat()
    rows = conn.execute(
        f"""
        SELECT county, COUNT(*) as n
        FROM records
        WHERE {ISO} >= ?
          AND county NOT IN ('Unknown', '', '45', '47')
        GROUP BY county
        HAVING n >= ?
        ORDER BY n DESC
        """,
        (cutoff, MIN_RECORDS),
    ).fetchall()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# Claude narrative generation
# ---------------------------------------------------------------------------

_SYSTEM = (
    "You are a public safety journalist writing weekly trend reports for "
    "MontanaBlotter.com, a community news site covering Montana police blotters. "
    "Your tone is factual, community-focused, and authoritative — 'Montana-tough'. "
    "Never speculate about individuals. Report aggregate trends only. "
    "Respond with valid JSON only — no markdown fences, no extra text."
)


def _build_prompt(county: str, data: dict, week_end: date) -> str:
    week_start = week_end - timedelta(days=WINDOW_DAYS - 1)
    total = sum(data["counts"].values())

    counts_block = []
    for cat, label in CATEGORY_LABELS.items():
        c = data["counts"][cat]
        b = data["baseline_avg"][cat]
        d = data["deltas"][cat]
        delta_str = f"{d:+.1f}% vs baseline" if d is not None else "no baseline yet"
        counts_block.append(f"  {label}: {c} incidents ({delta_str})")

    elevated = [CATEGORY_LABELS[c] for c in data["elevated"]] if data["elevated"] else []

    payload = {
        "county": county,
        "week": f"{week_start.isoformat()} to {week_end.isoformat()}",
        "total_incidents": total,
        "breakdown": counts_block,
        "elevated_categories": elevated,
    }

    return f"""
Write a weekly public safety trend report for {county} County, Montana.

Data for the week of {week_start.strftime('%B %d')}–{week_end.strftime('%B %d, %Y')}:
{json.dumps(payload, indent=2)}

Return a JSON object with exactly these keys:
- "title": SEO headline, 60-70 chars, include county name + "crime" or "public safety" + month/year
- "excerpt": 150-160 char meta description for Google, mention Great Falls or county seat if Cascade, otherwise county name
- "body": full markdown article, 350-500 words
  - H2: "Week at a Glance" — bullet summary of total incidents and top 3 categories
  - H2: "Breakdown by Category" — paragraph covering each category with non-zero count
  - H2: "What's Elevated" — if elevated categories exist, explain the trend. If none, write one sentence saying activity was within normal range.
  - H2: "Stay Informed" — 2-3 sentences encouraging readers to subscribe to county alerts

Do not name any individuals. Report aggregate numbers only.
""".strip()


def _call_claude(client, county: str, data: dict, week_end: date) -> Optional[dict]:
    if client is None:
        return None
    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1200,
            system=_SYSTEM,
            messages=[{"role": "user", "content": _build_prompt(county, data, week_end)}],
        )
        raw = message.content[0].text.strip()
        # Strip markdown fences if Claude adds them despite instructions
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        parsed = json.loads(raw)
        if all(k in parsed for k in ("title", "excerpt", "body")):
            return parsed
        logger.warning("Claude response missing required keys for %s", county)
        return None
    except Exception as e:
        logger.warning("Claude API error for %s: %s", county, e)
        return None


# ---------------------------------------------------------------------------
# Fallback template (no Claude)
# ---------------------------------------------------------------------------

def _fallback_content(county: str, data: dict, week_end: date) -> dict:
    week_start = week_end - timedelta(days=WINDOW_DAYS - 1)
    total = sum(data["counts"].values())
    top = sorted(data["counts"].items(), key=lambda x: x[1], reverse=True)
    top3 = [(CATEGORY_LABELS[c], n) for c, n in top if n > 0][:3]

    title = (
        f"{county} County Public Safety Report — "
        f"{week_end.strftime('%B %Y')}"
    )[:70]
    excerpt = (
        f"{county} County logged {total} public safety incidents "
        f"the week of {week_start.strftime('%b %d')}–{week_end.strftime('%b %d, %Y')}. "
        f"See the full breakdown on MontanaBlotter.com."
    )[:160]

    lines = [
        f"## Week at a Glance\n",
        f"- Total incidents: **{total}**",
    ]
    for label, n in top3:
        lines.append(f"- {label}: {n}")

    lines += [
        "\n## Breakdown by Category\n",
    ]
    for cat, label in CATEGORY_LABELS.items():
        n = data["counts"][cat]
        if n > 0:
            lines.append(f"- **{label}:** {n} incident{'s' if n != 1 else ''}")

    if data["elevated"]:
        elevated_labels = [CATEGORY_LABELS[c] for c in data["elevated"]]
        lines += [
            "\n## What's Elevated\n",
            f"The following categories were above the baseline average this week: "
            f"{', '.join(elevated_labels)}.",
        ]
    else:
        lines += [
            "\n## What's Elevated\n",
            "All categories were within normal range this week.",
        ]

    lines += [
        "\n## Stay Informed\n",
        f"Sign up for {county} County public safety alerts at MontanaBlotter.com. "
        "Get notified when new blotter data is published for your county.",
    ]

    return {"title": title, "excerpt": excerpt, "body": "\n".join(lines)}


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------

def _publish_report(
    conn: sqlite3.Connection,
    county: str,
    week_end: date,
    content: dict,
    force: bool,
    draft: bool,
) -> str:
    slug = f"{_slugify(county)}-county-safety-report-{week_end.isoformat()}"
    published = 0 if draft else 1

    existing = conn.execute(
        "SELECT id FROM blog_posts WHERE slug = ?", (slug,)
    ).fetchone()

    if existing and not force:
        return "skipped"

    if existing:
        conn.execute(
            """
            UPDATE blog_posts
            SET title=?, body=?, excerpt=?, author=?, published=?, updated_at=datetime('now')
            WHERE slug=?
            """,
            (content["title"], content["body"], content["excerpt"], AUTHOR, published, slug),
        )
        return "updated"
    else:
        conn.execute(
            """
            INSERT INTO blog_posts (title, slug, body, excerpt, author, published)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (content["title"], slug, content["body"], content["excerpt"], AUTHOR, published),
        )
        return "created"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def generate_reports(
    county_filter: Optional[str] = None,
    force: bool = False,
    draft: bool = False,
    stdout: bool = False,
) -> list[dict]:
    client = _anthropic_client()
    if client is None:
        logger.warning("No Anthropic API key — using fallback templates")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    week_end = datetime.now(UTC).date() - timedelta(days=1)  # yesterday

    if county_filter:
        counties = [county_filter]
    else:
        counties = _active_counties(conn)
        if not counties:
            logger.info("No active counties found in the last %d days", WINDOW_DAYS)
            conn.close()
            return []

    results = []
    for county in counties:
        data = aggregate_by_county(county, days=WINDOW_DAYS, db_path=DB_PATH)
        total = sum(data["counts"].values())

        if total < MIN_RECORDS:
            logger.info("Skipping %s — only %d categorized records", county, total)
            continue

        content = _call_claude(client, county, data, week_end)
        if content is None:
            content = _fallback_content(county, data, week_end)
            method = "fallback"
        else:
            method = "claude"

        if stdout:
            print(f"\n{'='*60}")
            print(f"COUNTY: {county}  |  method: {method}")
            print(f"{'='*60}")
            print(f"TITLE: {content['title']}")
            print(f"EXCERPT: {content['excerpt']}")
            print()
            print(content["body"])
            results.append({"county": county, "action": "printed", "method": method})
            continue

        action = _publish_report(conn, county, week_end, content, force, draft)
        conn.commit()
        logger.info("%s — %s (%s)", county, action, method)
        print(f"{county}: {action} ({method})")
        results.append({"county": county, "action": action, "method": method})

    conn.close()
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate weekly county safety reports.")
    parser.add_argument("--county", help="Generate for a single county only.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing reports.")
    parser.add_argument("--draft", action="store_true", help="Save as unpublished draft.")
    parser.add_argument("--stdout", action="store_true", help="Print output, don't save.")
    args = parser.parse_args()

    generate_reports(
        county_filter=args.county,
        force=args.force,
        draft=args.draft,
        stdout=args.stdout,
    )


if __name__ == "__main__":
    main()
