"""
Weekly Top Calls — "Top 10 Police Calls of the Week" blog post.
Runs every Sunday at 8pm MT via cron, publishes before the Monday subscriber digest.
Uses Claude to select and narrate the week's 10 most notable/unusual incidents.
Falls back to a plain-text list if the API is unavailable.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import config

try:
    import anthropic
except ImportError:  # pragma: no cover
    anthropic = None

AUTHOR = "Montana Blotter"
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1100


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_week_incidents(conn, since_iso: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT county, incident_type, incident, details, date
        FROM records
        WHERE created_at >= ?
          AND (details IS NOT NULL AND details != '')
        ORDER BY county, date DESC
        LIMIT 200
        """,
        (since_iso,),
    ).fetchall()
    return [dict(r) for r in rows]


def _fetch_week_bookings(conn, since_iso: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT county_name, charges_summary, booking_at
        FROM jail_bookings
        WHERE first_seen_at >= ?
        ORDER BY county_name, first_seen_at DESC
        LIMIT 100
        """,
        (since_iso,),
    ).fetchall()
    return [dict(r) for r in rows]


def _build_prompt(incidents: list[dict], bookings: list[dict], week_label: str) -> str:
    incident_lines = []
    for i in incidents:
        itype = i["incident_type"] or i["incident"] or "incident"
        detail = (i["details"] or "")[:200]
        incident_lines.append(f"  [{i['county']} | {i['date']}] {itype}: {detail}")

    booking_lines = []
    for b in bookings[:50]:
        charges = (b["charges_summary"] or "charges not listed")[:150]
        booking_lines.append(f"  [{b['county_name']}] {charges}")

    incidents_block = "\n".join(incident_lines) if incident_lines else "  (none)"
    bookings_block = "\n".join(booking_lines) if booking_lines else "  (none)"

    return f"""You are a staff writer for MontanaBlotter.com, a Montana public-safety news site.

Your job: read this week's police blotter incidents and jail bookings, then write a \
"Top 10 Police Calls of the Week" post for the week of {week_label}.

Selection criteria — choose calls that are:
- Unusual, unexpected, or surprising (strange locations, odd circumstances)
- Locally significant (high-profile charges, notable counties)
- Illustrative of a trend or pattern across the state
- Simply interesting from a public-safety perspective

DO NOT choose calls just because they are violent or tragic. \
Prioritize variety across counties. Do NOT include full names for minor misdemeanor \
incidents. Do NOT include home addresses or SSNs.

FORMAT your post exactly like this:
- A one-sentence intro paragraph (e.g. "Montana's week in review...")
- 10 numbered entries, each with: a bold short headline (5-8 words), county in parentheses, \
and 2-3 sentences of context. Keep each entry under 60 words.
- A one-sentence closing directing readers to montanablotter.com/arrests for the full feed.

Tone: clear, factual, slightly wry when the situation warrants it. \
Do not editorialize about guilt or innocence.

BLOTTER INCIDENTS (week of {week_label}):
{incidents_block}

JAIL BOOKINGS (week of {week_label}):
{bookings_block}

Write the post body only — no title, no byline, no markdown headers."""


def _generate_body(prompt: str) -> str | None:
    if anthropic is None or not getattr(config, "USE_PAID_LLM", False):
        return None
    try:
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception as exc:
        print(f"[weekly_top_calls] Claude API unavailable ({exc}), using plain fallback.")
        return None


def _plain_fallback(incidents: list[dict], bookings: list[dict], week_label: str) -> str:
    """Minimal plain-text post used when Claude is unavailable."""
    charge_types: dict[str, int] = {}
    for b in bookings:
        charges = (b["charges_summary"] or "").strip()
        if charges:
            charge_types[charges[:60]] = charge_types.get(charges[:60], 0) + 1

    top_charges = sorted(charge_types.items(), key=lambda x: -x[1])[:10]
    lines = [
        f"{i + 1}. {charge} ({count} booking{'s' if count != 1 else ''})"
        for i, (charge, count) in enumerate(top_charges)
    ]

    county_counts: dict[str, int] = {}
    for b in bookings:
        county_counts[b["county_name"]] = county_counts.get(b["county_name"], 0) + 1
    busiest = sorted(county_counts.items(), key=lambda x: -x[1])[:3]
    busiest_str = ", ".join(f"{c} ({n})" for c, n in busiest)

    return (
        f"This week's Montana blotter — {week_label}\n\n"
        f"Busiest counties by bookings: {busiest_str or 'N/A'}.\n\n"
        "Top charges this week:\n" + "\n".join(lines) + "\n\n"
        "See montanablotter.com/arrests for the full live feed."
    )


def run(dry_run: bool = False) -> None:
    now_utc = datetime.now(timezone.utc)
    iso_year, iso_week, _ = now_utc.isocalendar()
    slug = f"montana-top-calls-{iso_year}-w{iso_week:02d}"

    end_date = now_utc.date()
    start_date = end_date - timedelta(days=6)
    if start_date.month == end_date.month:
        week_label = f"{start_date.strftime('%b %-d')}–{end_date.strftime('%-d, %Y')}"
    else:
        week_label = f"{start_date.strftime('%b %-d')}–{end_date.strftime('%b %-d, %Y')}"
    title = f"Top 10 Police Calls of the Week — {week_label}"

    conn = _get_conn()

    existing = conn.execute(
        "SELECT id FROM blog_posts WHERE slug = ?", (slug,)
    ).fetchone()
    if existing:
        print(f"[weekly_top_calls] slug={slug} already exists, skipping.")
        conn.close()
        return

    since_iso = start_date.strftime("%Y-%m-%d 00:00:00")
    incidents = _fetch_week_incidents(conn, since_iso)
    bookings = _fetch_week_bookings(conn, since_iso)

    print(f"[weekly_top_calls] incidents={len(incidents)} bookings={len(bookings)} week={week_label}")

    if not incidents and not bookings:
        print("[weekly_top_calls] No data this week — skipping post.")
        conn.close()
        return

    if dry_run:
        print(f"[weekly_top_calls] dry-run — would publish: {title}")
        conn.close()
        return

    prompt = _build_prompt(incidents, bookings, week_label)
    body = _generate_body(prompt)

    if body is None:
        body = _plain_fallback(incidents, bookings, week_label)

    excerpt = body[:200].rstrip()

    conn.execute(
        """
        INSERT INTO blog_posts (title, slug, body, excerpt, author, published)
        VALUES (?, ?, ?, ?, ?, 1)
        """,
        (title, slug, body, excerpt, AUTHOR),
    )
    conn.commit()
    conn.close()
    print(f"[weekly_top_calls] published slug={slug}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Publish weekly top-10 police calls post")
    parser.add_argument("--dry-run", action="store_true", help="Preview only — no DB write")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
