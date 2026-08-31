#!/usr/bin/env python3
"""Generate one seasonal Montana public-safety post per season.

Unlike the daily roundup (which summarizes one day's blotter), this produces a
seasonal reflection/analysis piece -- what tends to happen in Montana policing
across a season, grounded in a few real aggregates from the window. The slug is
keyed by season + year (e.g. `montana-summer-2026-roundup`), so re-running in
the same season is a safe idempotent UPDATE, while the next season creates a
fresh post.

Voice follows the tightened daily-blog standard: concrete, varied, no stock
phrases, a reader angle.

Usage (run from repo root):
    venv/bin/python3 scripts/maintenance/seasonal_roundup.py            # current season
    venv/bin/python3 scripts/maintenance/seasonal_roundup.py --dry-run
    venv/bin/python3 scripts/maintenance/seasonal_roundup.py --force    # regenerate current season

This is intended to be run by cron on season boundaries (see crontab.txt).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, date, timedelta

import config
from daily_blog_worker import AUTHOR, DB_PATH, _parse_json_block, _upsert_blog_post

SEASON_FOR_MONTH = {
    12: "winter", 1: "winter", 2: "winter",
    3: "spring", 4: "spring", 5: "spring",
    6: "summer", 7: "summer", 8: "summer",
    9: "fall", 10: "fall", 11: "fall",
}


def _season_slug(d: date) -> str:
    season = SEASON_FOR_MONTH[d.month]
    # Winter wraps the year: Dec belongs to the upcoming winter season label.
    year = d.year if d.month != 12 else d.year + 1
    return f"montana-{season}-{year}-public-safety"


def _real_aggregates(window_days: int = 90) -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    since = (date.today() - timedelta(days=window_days)).isoformat()
    out: dict = {"window_days": window_days}
    try:
        out["bookings"] = conn.execute(
            "SELECT COUNT(*) FROM jail_bookings WHERE booking_at >= ?", (since,)
        ).fetchone()[0]
    except Exception:
        out["bookings"] = None
    try:
        out["warrants_active"] = conn.execute(
            "SELECT COUNT(*) FROM warrants WHERE status='active'"
        ).fetchone()[0]
    except Exception:
        out["warrants_active"] = None
    try:
        out["top_counties"] = [
            {"county": r["county"], "n": r["n"]}
            for r in conn.execute(
                "SELECT county_name AS county, COUNT(*) n FROM jail_bookings "
                "WHERE booking_at >= ? GROUP BY county_name ORDER BY n DESC LIMIT 5",
                (since,),
            )
        ]
    except Exception:
        out["top_counties"] = []
    conn.close()
    return out


def _call_claude_seasonal(season: str, year: int, agg: dict) -> dict:
    api_key = getattr(config, "ANTHROPIC_API_KEY", None)
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    prompt = f"""Write one seasonal Montana public-safety blog post in Markdown for {season.title()} {year}.

Use only the facts below. Do not invent incidents, numbers, agencies, or trends.

Real aggregates (last {agg.get('window_days')} days):
{json.dumps(agg, indent=2)}

Voice (most important -- readers say prior posts felt robotic, so break it):
- Write like a Montana journalist who actually covers public safety, not a wire summary. Plain-spoken, concrete, never corporate.
- OPEN with a specific, seasonal scene-setting sentence. Never lead with "[Season] in Montana saw..." or "[County] dominated." Vary the opener.
- VARY sentence length. Mix a short punchy line with a longer one.
- Give the season a human angle: what Montana residents actually experience in {season} (weather, tourism, holidays, road conditions, hunting season, school schedules) and how that shows up in public-safety logs.
- FORBIDDEN phrases: "dominated Montana's blotter", "busiest jurisdiction", "heaviest law enforcement workload", "in broader context", "the latest data shows", "most frequently reported".
- Include a short "What this means for readers" section.
- Do not name private people or speculate about guilt.
- Aim for 400-600 words. Return valid JSON only with keys: title, excerpt, body.
"""
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        system=(
            "You are a Montana public-safety journalist writing for Montana Blotter. "
            "You write grounded, varied, human-voiced seasonal analysis -- never a "
            "formulaic wire summary. You vary your opener and sentence rhythm, lead "
            "with concrete detail, avoid stock phrases, and stay factual. Return JSON only."
        ),
        messages=[{"role": "user", "content": prompt}],
        timeout=60,
    )
    return _parse_json_block(message.content[0].text.strip())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="regenerate even if this season's post exists")
    args = ap.parse_args()

    today = date.today()
    season = SEASON_FOR_MONTH[today.month]
    year = today.year if today.month != 12 else today.year + 1
    slug = _season_slug(today)

    agg = _real_aggregates()
    if args.dry_run:
        print(json.dumps({"slug": slug, "season": season, "year": year, "aggregates": agg}, indent=2))
        return 0

    try:
        article = _call_claude_seasonal(season, year, agg)
    except Exception as exc:
        print(f"seasonal Claude unavailable ({exc}); using local fallback", file=__import__("sys").stderr)
        article = _local_fallback(season, year, agg)

    conn = sqlite3.connect(DB_PATH)
    status, post_id = _upsert_blog_post(
        conn=conn,
        slug=slug,
        title=article["title"].strip(),
        excerpt=(article.get("excerpt") or "").strip(),
        body=article["body"].strip(),
        force=args.force,
    )
    conn.close()
    print(f"{status}: /blog/{slug} (post_id={post_id})")
    return 0


def _local_fallback(season: str, year: int, agg: dict) -> dict:
    """Credit-less seasonal post. Reads like a human wrote it; no LLM needed."""
    bookings = agg.get("bookings")
    warrants = agg.get("warrants_active")
    top = agg.get("top_counties", [])[:3]
    top_line = ", ".join(f"{c['county']} ({c['n']})" for c in top) or "no county breakout available"
    lede = {
        "winter": f"Winter tightens Montana's roads and homes, and the public-safety log shifts with it -- more welfare checks, more weather-driven calls, more stranded-motorist responses.",
        "spring": f"Spring thaw in Montana means more people outside, more travel, and a public-safety log that wakes up with the weather.",
        "summer": f"Summer is Montana's busy season -- tourists, festivals, and open roads -- and the blotter fills right alongside the visitor numbers.",
        "fall": f"Fall in Montana is hunting seasons, school schedules, and the slow turn toward winter, and each leaves a mark on the public-safety log.",
    }.get(season, f"{season.title()} in Montana leaves its own mark on the public-safety log.")
    body = [
        f"## Montana Public Safety, {season.title()} {year}",
        "",
        lede,
        "",
        "## What the Numbers Show",
        "",
    ]
    if bookings is not None:
        body.append(f"- About **{bookings:,}** jail-booking records in the last {agg.get('window_days')} days point to steady, routine intake across the state.")
    if warrants is not None:
        body.append(f"- Roughly **{warrants:,}** active warrants remain on the books -- a backlog of unresolved court matters, not a scoreboard of danger.")
    body.append(f"- Counties logging the most activity lately: {top_line}.")
    body.extend([
        "",
        "## What This Means for You",
        "",
        "Seasonal patterns are real but modest. Tourism and weather move the call volume more than any crime wave.",
        "When you read Montana Blotter, treat a busy season as a busy season -- not a verdict on a place.",
        "",
        "Want the detail? Open the agency reports behind each record. The log is the start of the story, not the end.",
    ])
    return {
        "title": f"Montana Public Safety: {season.title()} {year}",
        "excerpt": f"A seasonal look at Montana public safety for {season} {year}, grounded in recent booking and warrant data.",
        "body": "\n".join(body),
    }


if __name__ == "__main__":
    raise SystemExit(main())
