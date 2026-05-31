"""
Daily arrests blotter — "What's Happening in Montana"
Combines new jail bookings + recent blotter records into a daily narrative post.
Runs daily at 7:10am via cron (after morning_briefing).
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


def _get_conn():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_new_bookings(conn, since_iso: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT county_name, person_name, charges_summary, booking_at
        FROM jail_bookings
        WHERE first_seen_at >= ?
        ORDER BY county_name, first_seen_at DESC
        """,
        (since_iso,),
    ).fetchall()
    return [dict(r) for r in rows]


def _fetch_recent_incidents(conn, since_iso: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT county, incident_type, incident, details, date
        FROM records
        WHERE created_at >= ?
          AND (details IS NOT NULL AND details != '')
        ORDER BY county, created_at DESC
        LIMIT 60
        """,
        (since_iso,),
    ).fetchall()
    return [dict(r) for r in rows]


def _build_prompt(bookings: list[dict], incidents: list[dict], date_label: str) -> str:
    booking_lines = []
    for b in bookings[:80]:
        charges = b["charges_summary"] or "charges not listed"
        booking_lines.append(f"  - {b['county_name']}: {b['person_name']} — {charges[:100]}")

    incident_lines = []
    for i in incidents[:40]:
        itype = i["incident_type"] or i["incident"] or "incident"
        detail = (i["details"] or "")[:120]
        incident_lines.append(f"  - {i['county']} ({i['date']}): {itype} — {detail}")

    bookings_block = "\n".join(booking_lines) if booking_lines else "  (none)"
    incidents_block = "\n".join(incident_lines) if incident_lines else "  (none)"

    return f"""You are a staff writer for MontanaBlotter.com, a public-safety news site covering Montana counties.

Write a ~400-word daily summary post titled "What Happened in Montana Overnight — {date_label}".

Structure:
1. One-paragraph lede summarizing overall activity statewide.
2. County-by-county breakdown of notable new jail bookings (group by county, mention names and charge types — no full SSNs, DOBs, or home addresses).
3. Two or three notable blotter incidents worth flagging.
4. One closing sentence pointing readers to /arrests for the full live feed.

Tone: factual, journalistic, neutral. Great Falls / north-central MT audience.

NEW JAIL BOOKINGS (last 24 hours):
{bookings_block}

RECENT BLOTTER INCIDENTS:
{incidents_block}

Write the post body only — no title, no byline."""


def _generate_body(prompt: str) -> str | None:
    if anthropic is None:
        return None
    try:
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model=MODEL,
            max_tokens=700,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception as exc:
        print(f"[daily_arrests_blotter] Claude API unavailable ({exc}), using plain fallback.")
        return None


def run():
    now_utc = datetime.now(timezone.utc)
    date_label = now_utc.strftime("%B %-d, %Y")
    slug = f"montana-daily-{now_utc.strftime('%Y-%m-%d')}"
    title = f"What Happened in Montana Overnight — {date_label}"

    conn = _get_conn()

    existing = conn.execute(
        "SELECT id FROM blog_posts WHERE slug = ?", (slug,)
    ).fetchone()
    if existing:
        print(f"[daily_arrests_blotter] slug={slug} already exists, skipping.")
        conn.close()
        return

    since_iso = (now_utc - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    bookings = _fetch_new_bookings(conn, since_iso)
    incidents = _fetch_recent_incidents(conn, since_iso)

    print(f"[daily_arrests_blotter] bookings={len(bookings)} incidents={len(incidents)}")

    if not bookings and not incidents:
        print("[daily_arrests_blotter] No new data — skipping post.")
        conn.close()
        return

    prompt = _build_prompt(bookings, incidents, date_label)
    body = _generate_body(prompt)

    if body is None:
        # Fallback: structured plain-text summary without Claude.
        # Includes county booking counts AND top incidents so the post
        # is still useful when API credits are depleted.
        print("[daily_arrests_blotter] Running in FALLBACK MODE (Claude API unavailable).")
        county_counts: dict[str, int] = {}
        for b in bookings:
            county_counts[b["county_name"]] = county_counts.get(b["county_name"], 0) + 1
        booking_lines = [
            f"{c}: {n} booking{'s' if n != 1 else ''}"
            for c, n in sorted(county_counts.items())
        ]

        # Include up to 5 notable incidents (incidents list already fetched above)
        incident_lines = []
        for inc in incidents[:5]:
            agency = inc.get("county") or "MT"
            itype = inc.get("incident_type") or inc.get("incident") or "Incident"
            loc = inc.get("location") or ""
            loc_part = f" at {loc}" if loc else ""
            incident_lines.append(f"• {agency}: {itype}{loc_part}")

        parts = [f"Montana jail bookings — {date_label}\n\n"]
        parts.append("\n".join(booking_lines))
        parts.append(f"\n\nTotal new bookings: {len(bookings)}.")
        if incident_lines:
            parts.append("\n\nRecent incidents:\n" + "\n".join(incident_lines))
        parts.append("\n\nSee /arrests for the full live feed.")
        body = "".join(parts)

    excerpt = body[:160].rstrip()

    conn.execute(
        """
        INSERT INTO blog_posts (title, slug, body, excerpt, author, published)
        VALUES (?, ?, ?, ?, ?, 1)
        """,
        (title, slug, body, excerpt, AUTHOR),
    )
    conn.commit()
    conn.close()
    print(f"[daily_arrests_blotter] published slug={slug}")


if __name__ == "__main__":
    run()
