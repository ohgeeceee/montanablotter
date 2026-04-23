"""
Weekly Safety Snapshot — generates a Cascade County (or any county) weekly
public-safety digest and saves it as an unpublished blog_posts draft.

Usage:
    python weekly_snapshot.py                          # Cascade County, dry-run off
    python weekly_snapshot.py --county "Lewis and Clark"
    python weekly_snapshot.py --dry-run                # print markdown, no DB write
    python weekly_snapshot.py --publish                # mark published immediately
    python weekly_snapshot.py --sponsor "Bob's Bail Bonds|Call Bob first.|https://example.com"
"""

import argparse
import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta

import config

DB_PATH = config.DB_PATH
DB_TIMEOUT_SECONDS = float(getattr(config, "DB_TIMEOUT_SECONDS", 30))
DB_BUSY_TIMEOUT_MS = int(getattr(config, "DB_BUSY_TIMEOUT_MS", 30000))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DAYS_OF_WEEK = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {DB_BUSY_TIMEOUT_MS}")
    return conn


def _slugify(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^\w\s-]", "", value)
    value = re.sub(r"[\s_-]+", "-", value)
    return value


# ---------------------------------------------------------------------------
# Stats queries
# ---------------------------------------------------------------------------

def _date_range(weeks_ago: int = 0) -> tuple[str, str]:
    """Return (start, end) YYYY-MM-DD strings for a given week offset."""
    today = datetime.now().date()
    end = today - timedelta(days=7 * weeks_ago)
    start = end - timedelta(days=6)
    return start.isoformat(), end.isoformat()


def _parse_record_date(raw: str) -> str | None:
    """Normalise MM/DD/YY, MM/DD/YYYY, or YYYY-MM-DD to YYYY-MM-DD."""
    if not raw:
        return None
    for fmt in ("%m/%d/%y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def gather_stats(conn: sqlite3.Connection, county: str, start: str, end: str) -> dict:
    """Pull all stats needed for the snapshot from the records table."""

    # Fetch all records in window — normalise dates in Python since the DB
    # stores them in mixed formats (MM/DD/YY vs YYYY-MM-DD).
    rows = conn.execute(
        """
        SELECT date, incident_type, charge_category
        FROM records
        WHERE county = ?
        ORDER BY date, time
        """,
        (county,),
    ).fetchall()

    # Filter to window
    in_window, in_prev_window = [], []
    for r in rows:
        iso = _parse_record_date(r["date"])
        if iso is None:
            continue
        if start <= iso <= end:
            in_window.append(r)
        else:
            prev_start = (datetime.fromisoformat(start) - timedelta(days=7)).strftime("%Y-%m-%d")
            prev_end   = (datetime.fromisoformat(start) - timedelta(days=1)).strftime("%Y-%m-%d")
            if prev_start <= iso <= prev_end:
                in_prev_window.append(r)

    total = len(in_window)
    prev_total = len(in_prev_window)

    # Top 3 charges
    charge_counts: dict[str, int] = {}
    for r in in_window:
        label = (r["incident_type"] or r["charge_category"] or "Unknown").strip()
        charge_counts[label] = charge_counts.get(label, 0) + 1
    top3 = sorted(charge_counts.items(), key=lambda x: x[1], reverse=True)[:3]

    # Busiest day
    day_counts: dict[str, int] = {}
    for r in in_window:
        iso = _parse_record_date(r["date"])
        if iso:
            dow = datetime.fromisoformat(iso).strftime("%A")
            day_counts[dow] = day_counts.get(dow, 0) + 1
    busiest_day = max(day_counts, key=day_counts.get) if day_counts else "N/A"
    busiest_day_count = day_counts.get(busiest_day, 0)

    # Category breakdown for safety tip context
    felony_keywords = {"assault", "burglary", "arson", "robbery", "homicide", "rape", "theft"}
    felony_count = sum(
        1 for r in in_window
        if any(k in (r["incident_type"] or "").lower() for k in felony_keywords)
    )
    misd_count = total - felony_count

    return {
        "total": total,
        "prev_total": prev_total,
        "top3": top3,
        "busiest_day": busiest_day,
        "busiest_day_count": busiest_day_count,
        "felony_count": felony_count,
        "misd_count": misd_count,
        "charge_counts": charge_counts,
    }


# ---------------------------------------------------------------------------
# Claude generation
# ---------------------------------------------------------------------------

def _call_claude(stats: dict, county: str, start: str, end: str) -> dict:
    """Ask Claude for a lead paragraph and safety tip. Returns dict or {}."""
    try:
        import anthropic
        api_key = getattr(config, "ANTHROPIC_API_KEY", None)
        if not api_key:
            return {}
        client = anthropic.Anthropic(api_key=api_key)
    except ImportError:
        return {}

    top3_text = "\n".join(f"  {i+1}. {charge} ({count})" for i, (charge, count) in enumerate(stats["top3"]))
    delta = stats["total"] - stats["prev_total"]
    delta_str = f"+{delta}" if delta > 0 else str(delta)

    prompt = f"""You are a data journalist writing a weekly public-safety snapshot for {county} County, Montana.

Week: {start} to {end}
Total bookings: {stats['total']} ({delta_str} vs prior week)
Top charges:
{top3_text}
Busiest day: {stats['busiest_day']} ({stats['busiest_day_count']} incidents)

Write two things and return ONLY valid JSON:
1. "lead": A 2-sentence neutral summary of the week's activity. Vary the phrasing — don't always start with "A quiet week." Be specific about the data.
2. "safety_tip_headline": A short (5-8 word) headline for a practical safety tip.
3. "safety_tip_body": 2 sentences of practical advice for residents, directly tied to the top charge category.

JSON format:
{{
  "lead": "...",
  "safety_tip_headline": "...",
  "safety_tip_body": "..."
}}"""

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=400,
            system="You write concise, factual public-safety journalism. Respond with valid JSON only.",
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        return json.loads(raw)
    except Exception as e:
        logger.warning("Claude API error: %s — using fallback text", e)
        return {}


def _fallback_text(stats: dict, county: str) -> dict:
    """Plain-text fallback when Claude is unavailable."""
    delta = stats["total"] - stats["prev_total"]
    direction = "up" if delta > 0 else ("down" if delta < 0 else "flat")
    top_charge = stats["top3"][0][0] if stats["top3"] else "incidents"
    lead = (
        f"{county} County logged {stats['total']} bookings this week, "
        f"{abs(delta)} {'more' if delta > 0 else 'fewer'} than the prior week ({direction}). "
        f"{top_charge} led all charge categories."
    )
    return {
        "lead": lead,
        "safety_tip_headline": f"Tip: Awareness Around {top_charge}",
        "safety_tip_body": (
            f"{top_charge} was the top charge category this week in {county} County. "
            "Stay aware of your surroundings and report suspicious activity to local authorities."
        ),
    }


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------

def render_markdown(
    stats: dict,
    generated: dict,
    county: str,
    start: str,
    end: str,
    sponsor_name: str = "",
    sponsor_pitch: str = "",
    sponsor_url: str = "",
) -> tuple[str, str]:
    """Return (title, markdown_body)."""

    start_fmt = datetime.fromisoformat(start).strftime("%B %-d")
    end_fmt   = datetime.fromisoformat(end).strftime("%B %-d, %Y")
    title = f"Weekly {county} County Safety Snapshot: {start_fmt}–{end_fmt}"

    delta = stats["total"] - stats["prev_total"]
    delta_str = f"+{delta}" if delta > 0 else str(delta)

    sponsor_block = ""
    if sponsor_name:
        pitch = sponsor_pitch or f"Call {sponsor_name} when you need help fast."
        url_md = f" [{sponsor_url}]({sponsor_url})" if sponsor_url else ""
        sponsor_block = f"\n> *Presented by **{sponsor_name}** — {pitch}*{url_md}\n"

    top3_rows = "\n".join(
        f"| {i+1} | {charge} | {count} |"
        for i, (charge, count) in enumerate(stats["top3"])
    )

    lead      = generated.get("lead", "")
    tip_head  = generated.get("safety_tip_headline", "This Week's Safety Tip")
    tip_body  = generated.get("safety_tip_body", "")

    body = f"""# {title}
{sponsor_block}
{lead}

---

## By the Numbers

| Metric | This Week | vs. Last Week |
|---|---|---|
| Total Bookings | {stats['total']} | {delta_str} |
| Felony-Class Charges | {stats['felony_count']} | — |
| Misdemeanor-Class | {stats['misd_count']} | — |
| Busiest Day | {stats['busiest_day']} | {stats['busiest_day_count']} incidents |

**Top 3 Charges This Week**

| # | Charge | Count |
|---|---|---|
{top3_rows}

---

## {tip_head}

> {tip_body}

---

*Data compiled from public booking records. All individuals are presumed innocent until convicted.*
*[View full records → montanablotter.com](https://montanablotter.com)*
"""
    return title, body


# ---------------------------------------------------------------------------
# DB write
# ---------------------------------------------------------------------------

def save_to_blog_posts(
    conn: sqlite3.Connection,
    title: str,
    body: str,
    county: str,
    start: str,
    end: str,
    publish: bool = False,
) -> int:
    """Insert snapshot as a blog_posts draft. Returns new post id."""
    slug_base = _slugify(f"weekly-snapshot-{county}-{start}")
    slug = slug_base

    # Ensure unique slug
    existing = conn.execute("SELECT id FROM blog_posts WHERE slug = ?", (slug,)).fetchone()
    if existing:
        slug = f"{slug_base}-{end}"
        existing2 = conn.execute("SELECT id FROM blog_posts WHERE slug = ?", (slug,)).fetchone()
        if existing2:
            logger.info("Snapshot for %s %s already exists — skipping insert", county, start)
            return int(existing2["id"])

    # First 160 chars of body as excerpt, stripping markdown
    plain = re.sub(r"[#>*`\[\]|_]", "", body).strip()
    excerpt = " ".join(plain.split())[:160]

    cursor = conn.execute(
        """
        INSERT INTO blog_posts (title, slug, body, excerpt, author, published, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (
            title,
            slug,
            body,
            excerpt,
            "Montana Blotter",
            1 if publish else 0,
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(county: str, dry_run: bool, publish: bool, sponsor: str) -> None:
    start, end = _date_range(weeks_ago=0)

    # Parse sponsor string "Name|Pitch|URL"
    sponsor_parts = [s.strip() for s in (sponsor or "").split("|")]
    sponsor_name  = sponsor_parts[0] if len(sponsor_parts) > 0 else ""
    sponsor_pitch = sponsor_parts[1] if len(sponsor_parts) > 1 else ""
    sponsor_url   = sponsor_parts[2] if len(sponsor_parts) > 2 else ""

    conn = _connect()
    stats = gather_stats(conn, county, start, end)

    if stats["total"] == 0:
        logger.info("No records found for %s County between %s and %s.", county, start, end)
        conn.close()
        return

    generated = _call_claude(stats, county, start, end) or _fallback_text(stats, county)

    title, body = render_markdown(
        stats, generated, county, start, end,
        sponsor_name=sponsor_name,
        sponsor_pitch=sponsor_pitch,
        sponsor_url=sponsor_url,
    )

    if dry_run:
        print(f"\n{'='*60}\n{body}\n{'='*60}")
        logger.info("Dry run — nothing written to DB.")
        conn.close()
        return

    post_id = save_to_blog_posts(conn, title, body, county, start, end, publish=publish)
    status = "published" if publish else "draft"
    logger.info("Snapshot saved as blog_posts id=%d (%s): %s", post_id, status, title)
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate weekly safety snapshot blog post.")
    parser.add_argument("--county",   default="Cascade", help="County name (default: Cascade)")
    parser.add_argument("--dry-run",  action="store_true", help="Print markdown, skip DB write")
    parser.add_argument("--publish",  action="store_true", help="Mark post published immediately")
    parser.add_argument(
        "--sponsor",
        default="",
        help='Sponsor string: "Name|One-sentence pitch|https://url"',
    )
    args = parser.parse_args()
    run(county=args.county, dry_run=args.dry_run, publish=args.publish, sponsor=args.sponsor)
