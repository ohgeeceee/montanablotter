"""Auto-post missing/found person alerts to the Montana Blotter Facebook page.

Usage:
    python facebook_missing_poster.py               # queue all pending
    python facebook_missing_poster.py --person-id 7 # queue a specific person
    python facebook_missing_poster.py --dry-run     # print messages without queuing
    python facebook_missing_poster.py --run         # queue AND flush the FB queue immediately
    python facebook_missing_poster.py --limit 50    # cap batch size (default 50)
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from typing import Any

import config
from facebook_publisher import queue_post, run_facebook_queue

LOGGER = logging.getLogger(__name__)

DB_PATH = config.DB_PATH
BASE_URL = getattr(config, "BASE_URL", "https://montanablotter.com").rstrip("/")

MISSING_HASHTAGS = "#MissingPerson #Montana #MontanaBlotter #PublicSafety"
LOCATED_HASHTAGS = "#MontanaBlotter #Montana #MissingPerson #GoodNews"
CLEARINGHOUSE_PHONE = "(406) 444-1526"
CLEARINGHOUSE_EMAIL = "missingpersons@mt.gov"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _ensure_fb_columns(conn: sqlite3.Connection) -> bool:
    """Add the two Facebook tracking columns to missing_persons if they don't exist.

    Returns True if columns are ready, False if the DB was locked (caller should retry later).
    """
    existing = {row[1] for row in conn.execute("PRAGMA table_info('missing_persons')").fetchall()}
    needed = [
        ("fb_missing_posted_at", "TEXT DEFAULT ''"),
        ("fb_located_posted_at", "TEXT DEFAULT ''"),
    ]
    if all(col in existing for col, _ in needed):
        return True
    try:
        for col, defn in needed:
            if col not in existing:
                conn.execute(f"ALTER TABLE missing_persons ADD COLUMN {col} {defn}")
        conn.commit()
        return True
    except sqlite3.OperationalError as exc:
        LOGGER.warning("Could not add FB tracking columns (DB busy): %s — will retry next run", exc)
        return False


# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------

def _age_label(row: sqlite3.Row) -> str:
    age = row["age"] or row["age_missing"]
    return f", age {age}" if age else ""


def _physical_line(row: sqlite3.Row) -> str:
    parts: list[str] = []
    if row["gender"]:
        parts.append(row["gender"].title())
    if row["race"]:
        parts.append(row["race"].title())
    hw = row["height_weight"] or ""
    if not hw:
        h = row["height_raw"] or ""
        w = row["weight_lbs"]
        if h:
            hw = h
        if w:
            hw = f"{hw}, {w} lbs".strip(", ")
    if hw:
        parts.append(hw)
    if row["hair_color"]:
        parts.append(f"{row['hair_color'].title()} hair")
    if row["eye_color"]:
        parts.append(f"{row['eye_color'].title()} eyes")
    return " | ".join(parts) if parts else ""


def _location_line(row: sqlite3.Row) -> str:
    parts = [p for p in [row["last_seen_location"], row["city"], row["county"]] if p]
    return ", ".join(parts)


def _build_missing_message(row: sqlite3.Row) -> str:
    name = row["full_name"]
    age_label = _age_label(row)
    location = _location_line(row)
    last_seen = row["last_seen_at"] or ""
    physical = _physical_line(row)
    summary = (row["summary"] or "").strip()
    county = (row["county"] or "").replace(" County", "").strip()
    agency = (row["investigating_agency"] or "").strip()
    profile_url = (
        f"{BASE_URL}/missing-persons/{row['slug']}" if row["slug"] else f"{BASE_URL}/missing-persons"
    )

    lines: list[str] = [f"MISSING PERSON ALERT — {name}{age_label}", ""]
    if last_seen:
        lines.append(f"Last seen: {last_seen}" + (f" near {location}" if location else ""))
    elif location:
        lines.append(f"Last known location: {location}")
    if physical:
        lines.append(f"Description: {physical}")
    if summary:
        lines += ["", summary]
    lines += [
        "",
        "If you have information, please contact:",
        f"MT Missing Persons Clearinghouse: {CLEARINGHOUSE_PHONE}",
        f"Email: {CLEARINGHOUSE_EMAIL}",
    ]
    if agency:
        lines.append(f"Or your local agency: {agency}")
    lines += ["", f"Full profile: {profile_url}", ""]
    tags = MISSING_HASHTAGS
    if county:
        tags += f" #{county.replace(' ', '')}County"
    lines.append(tags)
    return "\n".join(lines)


def _build_located_message(row: sqlite3.Row) -> str:
    name = row["full_name"]
    resolution = (row["resolution_summary"] or "").strip()
    profile_url = (
        f"{BASE_URL}/missing-persons/{row['slug']}" if row["slug"] else f"{BASE_URL}/missing-persons"
    )

    lines: list[str] = [
        f"UPDATE: {name.upper()} HAS BEEN LOCATED",
        "",
        f"We are relieved to report that {name}, who was reported missing in Montana, "
        "has been safely located.",
    ]
    if resolution:
        lines += ["", resolution]
    lines += [
        "",
        "Thank you to everyone who shared this alert and helped with the search.",
        "",
        f"Details: {profile_url}",
        "",
        LOCATED_HASHTAGS,
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core queuing logic
# ---------------------------------------------------------------------------

def _queue_person(
    row: sqlite3.Row,
    *,
    dry_run: bool = False,
    conn: sqlite3.Connection,
) -> dict[str, Any]:
    person_id: int = row["id"]
    status: str = row["status"]

    if status == "located" and not (row["fb_located_posted_at"] or ""):
        action = "located"
        message = _build_located_message(row)
        col_to_update = "fb_located_posted_at"
    elif status == "missing" and not (row["fb_missing_posted_at"] or ""):
        action = "missing"
        message = _build_missing_message(row)
        col_to_update = "fb_missing_posted_at"
    else:
        return {"person_id": person_id, "action": "skip", "ok": True, "queue_id": None}

    if dry_run:
        print(f"\n{'='*60}")
        print(f"[DRY RUN] {action.upper()} — {row['full_name']} (id={person_id})")
        print(f"{'='*60}")
        print(message)
        return {"person_id": person_id, "action": action, "ok": True, "queue_id": None, "dry_run": True}

    profile_url = (
        f"{BASE_URL}/missing-persons/{row['slug']}" if row["slug"] else f"{BASE_URL}/missing-persons"
    )
    result = queue_post(
        content_type="custom",
        custom_message=message,
        link_url=profile_url,
        enqueue_source="missing_poster",
        conn=conn,
    )

    if result.get("ok"):
        conn.execute(
            f"UPDATE missing_persons SET {col_to_update} = datetime('now') WHERE id = ?",
            (person_id,),
        )

    return {"person_id": person_id, "action": action, **result}


def queue_pending(
    *,
    limit: int = 50,
    dry_run: bool = False,
    conn: sqlite3.Connection,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM missing_persons
        WHERE (
            (status = 'missing' AND COALESCE(fb_missing_posted_at, '') = '')
         OR (status = 'located' AND COALESCE(fb_located_posted_at, '') = '')
        )
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
        result = _queue_person(row, dry_run=dry_run, conn=conn)
        results.append(result)
        if not dry_run:
            conn.commit()
    return results


def queue_one(
    person_id: int,
    *,
    dry_run: bool = False,
    conn: sqlite3.Connection,
) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM missing_persons WHERE id = ?", (person_id,)).fetchone()
    if not row:
        return {"ok": False, "error": f"person {person_id} not found"}
    result = _queue_person(row, dry_run=dry_run, conn=conn)
    if not dry_run:
        conn.commit()
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Queue missing/found person posts to Facebook.")
    parser.add_argument("--person-id", type=int, help="Queue a single person by DB id")
    parser.add_argument("--dry-run", action="store_true", help="Print messages without queuing")
    parser.add_argument("--run", action="store_true", help="Also flush the Facebook queue after queuing")
    parser.add_argument("--limit", type=int, default=50, help="Max persons to process (default 50)")
    args = parser.parse_args(argv)

    conn = _connect()
    try:
        if not _ensure_fb_columns(conn):
            LOGGER.warning("Exiting early — DB locked, columns not yet added. Try again when DB is free.")
            return 0
        if args.person_id:
            results = [queue_one(args.person_id, dry_run=args.dry_run, conn=conn)]
        else:
            results = queue_pending(limit=args.limit, dry_run=args.dry_run, conn=conn)

        queued = [r for r in results if r.get("action") != "skip" and not r.get("dry_run")]
        skipped = [r for r in results if r.get("action") == "skip"]
        dry = [r for r in results if r.get("dry_run")]

        if not args.dry_run:
            LOGGER.info(
                "Done — %d queued, %d already posted (skipped), %d errors",
                len(queued),
                len(skipped),
                sum(1 for r in queued if not r.get("ok")),
            )
        else:
            LOGGER.info("Dry run — %d message(s) previewed", len(dry))

        if args.run and not args.dry_run:
            LOGGER.info("Flushing Facebook queue…")
            summary = run_facebook_queue()
            LOGGER.info("Queue flush: %s", summary)
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
