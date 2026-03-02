"""
Daily blog worker for Montana Blotter.

Creates one statewide blog post per day from the latest available blotter
records and public digest posts. The worker is safe to rerun: it uses a
date-based slug and skips duplicates unless --force is passed.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Iterable, Optional

import config

DB_PATH = config.DB_PATH
AUTHOR = "Montana Blotter Auto Desk"
LOOKBACK_DAYS = 7
MAX_HIGHLIGHTS = 6
MAX_RELATED_POSTS = 6
WINDOW_DAYS = 3
LOG_PATH = "/root/montanablotter/daily_blog.log"

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:80]


def _parse_date(raw_value: Optional[str]) -> Optional[date]:
    value = (raw_value or "").strip()
    if not value:
        return None

    formats = (
        "%m/%d/%Y",
        "%m/%d/%y",
        "%Y-%m-%d",
        "%Y/%m/%d",
    )
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _format_long_date(value: date) -> str:
    return f"{value.strftime('%B')} {value.day}, {value.year}"


def _fetch_recent_date_map(conn: sqlite3.Connection) -> dict[date, set[str]]:
    date_map: dict[date, set[str]] = {}
    for table_name, column_name in (("records", "date"), ("posts", "incident_date")):
        rows = conn.execute(
            f"""
            SELECT DISTINCT {column_name}
            FROM {table_name}
            WHERE {column_name} IS NOT NULL AND TRIM({column_name}) != ''
            """
        ).fetchall()
        for row in rows:
            raw_value = row[0]
            normalized = _parse_date(raw_value)
            if normalized is None:
                continue
            date_map.setdefault(normalized, set()).add(str(raw_value))
    return date_map


def _pick_analysis_date(conn: sqlite3.Connection, requested: Optional[str]) -> tuple[date, list[str]]:
    date_map = _fetch_recent_date_map(conn)
    if not date_map:
        raise RuntimeError("No incident dates found in posts or records")

    if requested:
        parsed_requested = _parse_date(requested)
        if parsed_requested is None:
            raise RuntimeError(f"Could not parse requested date: {requested}")
        raw_values = sorted(date_map.get(parsed_requested, set()))
        if not raw_values:
            raise RuntimeError(f"No blotter content found for requested date: {requested}")
        return parsed_requested, raw_values

    latest_date = max(date_map)
    if latest_date < (datetime.now(UTC).date() - timedelta(days=LOOKBACK_DAYS)):
        raise RuntimeError(
            f"Latest blotter content is too old for auto-publish: {_format_long_date(latest_date)}"
        )
    return latest_date, sorted(date_map[latest_date])


def _raw_dates_for_window(
    date_map: dict[date, set[str]],
    analysis_date: date,
    window_days: int = WINDOW_DAYS,
) -> list[str]:
    raw_values: set[str] = set()
    floor_date = analysis_date - timedelta(days=window_days - 1)
    for normalized_date, originals in date_map.items():
        if floor_date <= normalized_date <= analysis_date:
            raw_values.update(originals)
    return sorted(raw_values)


def _build_in_clause(values: Iterable[str]) -> tuple[str, list[str]]:
    values_list = list(values)
    placeholders = ",".join("?" for _ in values_list)
    return placeholders, values_list


def _fetch_records_for_date(conn: sqlite3.Connection, raw_dates: list[str]) -> list[sqlite3.Row]:
    placeholders, params = _build_in_clause(raw_dates)
    return conn.execute(
        f"""
        SELECT
            id,
            date,
            COALESCE(time, '') AS time,
            COALESCE(county, 'Unknown') AS county,
            COALESCE(location, '') AS location,
            COALESCE(NULLIF(incident_type, ''), NULLIF(incident, ''), 'Incident') AS incident_type,
            COALESCE(details, summary, '') AS details,
            blotter_id
        FROM records
        WHERE date IN ({placeholders})
        ORDER BY county, time, id
        """,
        params,
    ).fetchall()


def _fetch_related_posts(conn: sqlite3.Connection, analysis_date: date) -> list[sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT id, title, county, city, agency_name, incident_date, created_at
        FROM posts
        ORDER BY created_at DESC
        LIMIT 250
        """
    ).fetchall()

    related = []
    for row in rows:
        row_date = _parse_date(row["incident_date"])
        if row_date is None:
            continue
        if abs((analysis_date - row_date).days) > 2:
            continue
        related.append(row)
        if len(related) >= MAX_RELATED_POSTS:
            break
    return related


def _filter_related_posts(
    related_posts: list[sqlite3.Row],
    county: Optional[str] = None,
    incident_keyword: Optional[str] = None,
) -> list[sqlite3.Row]:
    filtered = []
    keyword = (incident_keyword or "").lower()
    for row in related_posts:
        if county and (row["county"] or "").lower() != county.lower():
            continue
        if keyword:
            haystack = " ".join(
                [
                    row["title"] or "",
                    row["agency_name"] or "",
                    row["county"] or "",
                    row["city"] or "",
                ]
            ).lower()
            if keyword not in haystack:
                continue
        filtered.append(row)
        if len(filtered) >= MAX_RELATED_POSTS:
            break
    return filtered or related_posts[:MAX_RELATED_POSTS]


def _select_highlights(records: list[sqlite3.Row]) -> list[str]:
    highlights: list[str] = []
    seen_keys: set[tuple[str, str, str]] = set()

    for row in records:
        county = row["county"] or "Unknown County"
        incident_type = row["incident_type"] or "Incident"
        location = row["location"] or "an unspecified location"
        details = re.sub(r"\s+", " ", (row["details"] or "").strip())
        key = (county, incident_type, location)
        if key in seen_keys:
            continue
        seen_keys.add(key)

        detail_tail = ""
        if details:
            detail_tail = f" Details noted: {details[:140].rstrip('.')}."
        highlights.append(f"{county}: {incident_type} near {location}.{detail_tail}")
        if len(highlights) >= MAX_HIGHLIGHTS:
            break

    return highlights


def _build_stats(records: list[sqlite3.Row], related_posts: list[sqlite3.Row]) -> dict[str, object]:
    county_counts = Counter()
    incident_counts = Counter()

    for row in records:
        county_counts[row["county"] or "Unknown"] += 1
        incident_counts[row["incident_type"] or "Incident"] += 1

    agencies = sorted(
        {
            (row["agency_name"] or "").strip()
            for row in related_posts
            if (row["agency_name"] or "").strip()
        }
    )

    return {
        "total_incidents": len(records),
        "county_counts": county_counts,
        "incident_counts": incident_counts,
        "agency_count": len(agencies),
        "agencies": agencies[:8],
        "highlights": _select_highlights(records),
    }


def _top_counter_entries(counter: Counter, limit: int, key_name: str) -> list[dict[str, object]]:
    return [
        {key_name: name, "count": count}
        for name, count in counter.most_common(limit)
    ]


def _pick_story_mode(analysis_date: date, daily_stats: dict[str, object], window_stats: dict[str, object]) -> str:
    preferred_modes = ("statewide", "county_focus", "incident_focus")
    preferred = preferred_modes[analysis_date.toordinal() % len(preferred_modes)]

    county_entries = window_stats["county_counts"].most_common(1)
    incident_entries = window_stats["incident_counts"].most_common(1)
    county_ready = bool(county_entries and county_entries[0][1] >= 3)
    incident_ready = bool(incident_entries and incident_entries[0][1] >= 2)

    if preferred == "county_focus" and county_ready:
        return preferred
    if preferred == "incident_focus" and incident_ready:
        return preferred
    if preferred == "statewide":
        return preferred
    if county_ready:
        return "county_focus"
    if incident_ready:
        return "incident_focus"
    return "statewide"


def _build_context_payload(
    analysis_date: date,
    story_mode: str,
    daily_stats: dict[str, object],
    window_stats: dict[str, object],
    related_posts: list[sqlite3.Row],
) -> dict[str, object]:
    top_counties = _top_counter_entries(daily_stats["county_counts"], 5, "county")
    top_incident_types = _top_counter_entries(daily_stats["incident_counts"], 6, "incident_type")
    window_top_counties = _top_counter_entries(window_stats["county_counts"], 5, "county")
    window_top_incident_types = _top_counter_entries(
        window_stats["incident_counts"], 6, "incident_type"
    )
    links = []
    for row in related_posts:
        label_bits = [row["county"]]
        if row["city"]:
            label_bits.append(row["city"])
        if row["agency_name"]:
            label_bits.append(row["agency_name"])
        links.append(
            {
                "title": row["title"],
                "url": f"/post/{row['id']}",
                "context": " | ".join(bit for bit in label_bits if bit),
            }
        )

    return {
        "story_mode": story_mode,
        "analysis_date": analysis_date.isoformat(),
        "analysis_date_long": _format_long_date(analysis_date),
        "total_incidents": daily_stats["total_incidents"],
        "agency_count": daily_stats["agency_count"],
        "top_counties": top_counties,
        "top_incident_types": top_incident_types,
        "highlights": daily_stats["highlights"],
        "window_total_incidents": window_stats["total_incidents"],
        "window_top_counties": window_top_counties,
        "window_top_incident_types": window_top_incident_types,
        "window_highlights": window_stats["highlights"],
        "related_posts": links,
    }


def _parse_json_block(raw_text: str) -> dict[str, str]:
    cleaned = (raw_text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    if not cleaned:
        return {}
    try:
        parsed = json.loads(cleaned)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _call_claude(context_payload: dict[str, object]) -> dict[str, str]:
    try:
        import anthropic
    except ImportError:
        return {}

    api_key = getattr(config, "ANTHROPIC_API_KEY", None)
    if not api_key:
        return {}

    client = anthropic.Anthropic(api_key=api_key)
    prompt = f"""Write one Montana-focused public blog post in Markdown.

Use only the facts in this context. Do not invent incidents, numbers, agencies,
locations, motives, arrests, or trend claims that are not supported below.

Context:
{json.dumps(context_payload, indent=2)}

Rules:
- Keep it about Montana policing, crime, and public-safety activity.
- Write a strong factual title and a 1-2 sentence excerpt.
- Write the body in Markdown with short sections and bullets where useful.
- The post must feel like one of these editorial formats depending on story_mode:
  - statewide: statewide roundup with what stood out and where to read more
  - county_focus: focus on the busiest county in the recent window
  - incident_focus: focus on the most common incident type in the recent window
- Include a section that links readers back to Montana Blotter report pages.
- Do not name private people or speculate about guilt.
- Return valid JSON only with keys: title, excerpt, body.
"""

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1800,
            system=(
                "You write grounded local-news analysis for a Montana public-safety site. "
                "Be specific, restrained, and factual. Return JSON only."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = message.content[0].text.strip()
        return _parse_json_block(raw_text)
    except Exception as exc:
        logger.warning("Claude daily blog generation failed: %s", exc)
        return {}


def _fallback_article(context_payload: dict[str, object]) -> dict[str, str]:
    story_mode = context_payload["story_mode"]
    date_long = context_payload["analysis_date_long"]
    total_incidents = int(context_payload["total_incidents"])
    agency_count = int(context_payload["agency_count"])
    top_counties = context_payload["top_counties"]
    top_incident_types = context_payload["top_incident_types"]
    highlights = context_payload["highlights"]
    related_posts = context_payload["related_posts"]
    window_total_incidents = int(context_payload["window_total_incidents"])
    window_top_counties = context_payload["window_top_counties"]
    window_top_incident_types = context_payload["window_top_incident_types"]
    window_highlights = context_payload["window_highlights"]

    county_line = ", ".join(
        f"{entry['county']} ({entry['count']})" for entry in top_counties[:3]
    ) or "no county breakout available"
    incident_line = ", ".join(
        f"{entry['incident_type']} ({entry['count']})" for entry in top_incident_types[:4]
    ) or "no incident-type breakout available"

    if story_mode == "county_focus" and window_top_counties:
        lead_county = window_top_counties[0]["county"]
        lead_count = window_top_counties[0]["count"]
        county_links = [post for post in related_posts if lead_county.lower() in (post["context"] or "").lower()]
        body_lines = [
            f"## {lead_county} County Police Blotter Focus for {date_long}",
            "",
            (
                f"Across the recent Montana blotter window ending **{date_long}**, "
                f"**{lead_county} County** showed the heaviest visible activity with **{lead_count} incident entries**."
            ),
            (
                f"The same window contains **{window_total_incidents} incident entries statewide**, "
                f"which gives a little more context than a single-day snapshot."
            ),
            "",
            "## Why This County Stood Out",
            "",
            f"- {lead_county} County led the current window by visible incident volume.",
            f"- Same-day incident mix on {date_long}: {incident_line}.",
            "",
            f"## What Appeared In {lead_county} County Logs",
            "",
        ]
        county_highlights = [line for line in window_highlights if line.startswith(f"{lead_county}:")]
        for line in county_highlights[:MAX_HIGHLIGHTS]:
            body_lines.append(f"- {line}")
        if not county_highlights:
            body_lines.append(f"- Detailed {lead_county} County highlights were limited in the current parsed records.")
        body_lines.extend(["", "## Read The Local Reports", ""])
        for post in county_links[:MAX_RELATED_POSTS]:
            context_text = f" ({post['context']})" if post["context"] else ""
            body_lines.append(f"- [{post['title']}]({post['url']}){context_text}")
        if not county_links:
            body_lines.append(f"- Browse [/posts?county={lead_county.replace(' ', '%20')}](/posts?county={lead_county.replace(' ', '%20')}) for the latest {lead_county} County reports.")
        body_lines.extend(
            [
                "",
                "## What To Watch Next",
                "",
                (
                    "One busy county does not define a statewide trend, but these bursts often show where calls for service, "
                    "quality-of-life complaints, or weapons-related dispatches are concentrating."
                ),
            ]
        )
        return {
            "title": f"{lead_county} County Led Montana's Blotter Activity on {date_long}",
            "excerpt": (
                f"A daily Montana Blotter focus on {lead_county} County, which posted the heaviest visible activity "
                f"in the recent reporting window ending {date_long}."
            ),
            "body": "\n".join(body_lines),
        }

    if story_mode == "incident_focus" and window_top_incident_types:
        lead_incident = window_top_incident_types[0]["incident_type"]
        lead_count = window_top_incident_types[0]["count"]
        body_lines = [
            f"## Montana Incident Trend Watch for {date_long}",
            "",
            (
                f"In the recent Montana blotter window ending **{date_long}**, "
                f"**{lead_incident}** appeared **{lead_count} times**, making it the clearest repeated incident label in the parsed records."
            ),
            (
                f"The current day itself contains **{total_incidents} incident entries**, with activity concentrated in {county_line}."
            ),
            "",
            "## What The Logs Showed",
            "",
        ]
        incident_highlights = [line for line in window_highlights if lead_incident.lower() in line.lower()]
        for line in incident_highlights[:MAX_HIGHLIGHTS]:
            body_lines.append(f"- {line}")
        if not incident_highlights:
            body_lines.append(f"- Specific {lead_incident} highlights were limited in the currently parsed records.")
        body_lines.extend(
            [
                "",
                "## Same-Day Context",
                "",
                f"- Leading incident labels on {date_long}: {incident_line}.",
                f"- Counties most visible in the current day: {county_line}.",
                "",
                "## Reports To Read Next",
                "",
            ]
        )
        for post in related_posts:
            context_text = f" ({post['context']})" if post["context"] else ""
            body_lines.append(f"- [{post['title']}]({post['url']}){context_text}")
        if not related_posts:
            body_lines.append("- New public report links were not available for this roundup window yet.")
        body_lines.extend(
            [
                "",
                "## Why This Matters",
                "",
                (
                    "Repeated incident labels in blotter data do not prove a long-term statewide pattern, "
                    "but they do show which call types are dominating the current operational window."
                ),
            ]
        )
        return {
            "title": f"{lead_incident} Calls Stood Out In Montana Blotters on {date_long}",
            "excerpt": (
                f"A Montana Blotter trend watch on {lead_incident} activity, which appeared most often in the recent "
                f"police and sheriff logs ending {date_long}."
            ),
            "body": "\n".join(body_lines),
        }

    body_lines = [
        f"## Montana Crime and Police Roundup for {date_long}",
        "",
        (
            f"Montana Blotter reviewed **{total_incidents} incident entries** tied to "
            f"Montana law-enforcement activity dated **{date_long}**."
        ),
        (
            f"Public digest posts from **{agency_count} agencies** were available in the current "
            f"roundup window."
            if agency_count
            else "Agency digest coverage was limited in the current roundup window, so this post leans on raw incident records."
        ),
        "",
        "## What Stood Out",
        "",
        f"- Counties with the most reported activity: {county_line}.",
        f"- Most common incident labels in the available records: {incident_line}.",
        "",
        "## Field Notes From The Daily Log",
        "",
    ]
    for line in highlights or ["No clean record highlights were available from the current date window."]:
        body_lines.append(f"- {line}")
    body_lines.extend(["", "## Reports To Read Next", ""])
    for post in related_posts:
        context_text = f" ({post['context']})" if post["context"] else ""
        body_lines.append(f"- [{post['title']}]({post['url']}){context_text}")
    if not related_posts:
        body_lines.append("- New public report links were not available for this roundup window yet.")
    body_lines.extend(
        [
            "",
            "## Why This Matters",
            "",
            (
                "Daily blotter coverage is not a full crime rate measure, but it does show where calls, "
                "enforcement activity, and public-safety workload are concentrating across Montana."
            ),
            (
                "Readers should treat this roundup as a same-day operational snapshot and use the linked "
                "agency reports for more direct source context."
            ),
        ]
    )
    return {
        "title": f"Montana Crime and Police Roundup for {date_long}",
        "excerpt": (
            f"A Montana-focused roundup of {total_incidents} incident entries dated {date_long}, "
            f"with the busiest counties, common call types, and links back to local agency reports."
        ),
        "body": "\n".join(body_lines),
    }


def _article_from_context(context_payload: dict[str, object]) -> dict[str, str]:
    article = _call_claude(context_payload)
    if article.get("title") and article.get("body"):
        return article
    return _fallback_article(context_payload)


def _upsert_blog_post(
    conn: sqlite3.Connection,
    slug: str,
    title: str,
    excerpt: str,
    body: str,
    force: bool,
) -> tuple[str, int]:
    existing = conn.execute(
        "SELECT id FROM blog_posts WHERE slug = ?",
        (slug,),
    ).fetchone()

    if existing and not force:
        return "skipped", int(existing["id"])

    if existing:
        conn.execute(
            """
            UPDATE blog_posts
            SET title = ?, body = ?, excerpt = ?, author = ?, published = 1,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (title, body, excerpt, AUTHOR, int(existing["id"])),
        )
        conn.commit()
        return "updated", int(existing["id"])

    cursor = conn.execute(
        """
        INSERT INTO blog_posts (title, slug, body, excerpt, author, published)
        VALUES (?, ?, ?, ?, ?, 1)
        """,
        (title, slug, body, excerpt, AUTHOR),
    )
    conn.commit()
    return "created", int(cursor.lastrowid)


def run_daily_blog(date_override: Optional[str] = None, dry_run: bool = False, force: bool = False) -> int:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        analysis_date, raw_dates = _pick_analysis_date(conn, date_override)
        date_map = _fetch_recent_date_map(conn)
        window_raw_dates = _raw_dates_for_window(date_map, analysis_date)

        daily_records = _fetch_records_for_date(conn, raw_dates)
        if not daily_records:
            raise RuntimeError(f"No records found for {_format_long_date(analysis_date)}")

        window_records = _fetch_records_for_date(conn, window_raw_dates) if window_raw_dates else daily_records
        all_related_posts = _fetch_related_posts(conn, analysis_date)
        daily_stats = _build_stats(daily_records, all_related_posts)
        window_stats = _build_stats(window_records, all_related_posts)
        story_mode = _pick_story_mode(analysis_date, daily_stats, window_stats)

        focused_related_posts = all_related_posts
        if story_mode == "county_focus" and window_stats["county_counts"]:
            lead_county = window_stats["county_counts"].most_common(1)[0][0]
            focused_related_posts = _filter_related_posts(all_related_posts, county=lead_county)
        elif story_mode == "incident_focus" and window_stats["incident_counts"]:
            lead_incident = window_stats["incident_counts"].most_common(1)[0][0]
            focused_related_posts = _filter_related_posts(all_related_posts, incident_keyword=lead_incident)

        context_payload = _build_context_payload(
            analysis_date,
            story_mode,
            daily_stats,
            window_stats,
            focused_related_posts,
        )
        article = _article_from_context(context_payload)

        slug = _slugify(f"montana-crime-roundup-{analysis_date.isoformat()}")
        title = article["title"].strip()
        excerpt = (article.get("excerpt") or "").strip()
        body = article["body"].strip()

        if dry_run:
            print(json.dumps(
                {
                    "slug": slug,
                    "title": title,
                    "excerpt": excerpt,
                    "body_preview": body[:500],
                    "story_mode": story_mode,
                    "analysis_date": analysis_date.isoformat(),
                    "record_count": len(daily_records),
                    "window_record_count": len(window_records),
                    "related_post_count": len(focused_related_posts),
                },
                indent=2,
            ))
            logger.info("Daily blog dry run succeeded for %s", analysis_date.isoformat())
            return 0

        status, blog_post_id = _upsert_blog_post(
            conn=conn,
            slug=slug,
            title=title,
            excerpt=excerpt,
            body=body,
            force=force,
        )
        logger.info(
            "Daily blog worker %s blog_post_id=%s slug=%s analysis_date=%s",
            status,
            blog_post_id,
            slug,
            analysis_date.isoformat(),
        )
        print(f"{status}: /blog/{slug} (post_id={blog_post_id})")
        return 0
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Create one automatic Montana daily blog post")
    parser.add_argument("--date", dest="date_override", help="Specific incident date to summarize")
    parser.add_argument("--dry-run", action="store_true", help="Build the post without saving it")
    parser.add_argument("--force", action="store_true", help="Update today's auto-generated slug if it already exists")
    args = parser.parse_args()

    try:
        return run_daily_blog(
            date_override=args.date_override,
            dry_run=args.dry_run,
            force=args.force,
        )
    except Exception as exc:
        logger.exception("Daily blog worker failed: %s", exc)
        print(f"error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
