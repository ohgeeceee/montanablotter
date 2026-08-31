#!/usr/bin/env python3
"""Rephrase existing daily crime roundups with the tightened Claude voice.

The daily_blog_worker prompt was rewritten to sound less robotic (varied
openers, forbidden stock phrases, a reader-angle). This script replays the
worker's own generator over already-published roundup dates so the stale,
formulaic posts get rewritten in place -- same slug, same date, updated body.

Because we call run_daily_blog(..., force=True), the existing post is UPDATED
(not duplicated) and subscribers are NOT re-notified (the worker only
broadcasts on a fresh "created").

Usage (run from repo root):
    venv/bin/python3 scripts/maintenance/backfill_roundups.py            # robotic-phrased titles only
    venv/bin/python3 scripts/maintenance/backfill_roundups.py --all      # every roundup
    venv/bin/python3 scripts/maintenance/backfill_roundups.py --since 2026-07-01
    venv/bin/python3 scripts/maintenance/backfill_roundups.py --limit 5 --dry-run

This is a one-time cleanup, NOT a cron job. Re-running it just regenerates the
same posts again (harmless, but costs API credits).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys

import config
from daily_blog_worker import run_daily_blog

DB_PATH = config.DB_PATH

# Title phrases that mark the old robotic cadence. These are the exact strings
# the tightened prompt now forbids, so any existing title containing one is a
# candidate for a rewrite.
ROBOTIC_TITLE_HINTS = (
    "dominated",
    "busiest jurisdiction",
    "heaviest law enforcement",
    "most frequently reported",
    "in broader context",
)


def _target_dates(only_robotic: bool, since: str | None, limit: int | None) -> list[str]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT slug, title, created_at FROM blog_posts
           WHERE slug LIKE 'montana-crime-roundup-%' AND published=1
           ORDER BY created_at ASC"""
    ).fetchall()
    conn.close()

    dated: list[tuple[str, str]] = []
    for r in rows:
        slug = r["slug"]
        # slug form: montana-crime-roundup-YYYY-MM-DD
        date_part = slug.replace("montana-crime-roundup-", "")
        if since and date_part < since:
            continue
        if only_robotic and not any(h in (r["title"] or "").lower() for h in ROBOTIC_TITLE_HINTS):
            continue
        dated.append((date_part, r["title"] or ""))

    if limit:
        dated = dated[-limit:]  # most recent N
    return [d for d, _ in dated]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--all", action="store_true", help="rephrase every roundup, not just robotic ones")
    ap.add_argument("--since", help="only roundups on/after this date (YYYY-MM-DD)")
    ap.add_argument("--limit", type=int, help="max number of posts to rephrase (most recent)")
    ap.add_argument("--dry-run", action="store_true", help="list targets, do not regenerate")
    args = ap.parse_args()

    dates = _target_dates(only_robotic=not args.all, since=args.since, limit=args.limit)
    if not dates:
        print("No target roundups found.")
        return 0

    print(f"Target roundups: {len(dates)}")
    if args.dry_run:
        for d in dates:
            print(f"  would rephrase {d}")
        return 0

    ok = fail = 0
    for d in dates:
        try:
            run_daily_blog(date_override=d, force=True)
            ok += 1
        except Exception as exc:  # keep going through the batch
            fail += 1
            print(f"FAILED {d}: {exc}", file=sys.stderr)
    print(f"backfill complete: ok={ok} failed={fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
