"""
daily_planner.py — Autonomous daily task generator for the Montana Blotter agent fleet.

Runs at 06:45 MT each morning (before the 07:00 digest). Queries the DB and logs
to compute the current state of the pipeline, then writes prioritized work items
to agent-queue/ for each agent profile. No LLM calls — pure stdlib + sqlite3.

Each agent checks their queue directory at the start of each session and works through
outstanding items. This is what makes the fleet autonomous: they always have a concrete,
data-driven work list even without Jon prompting them.

Cron (add to crontab.txt):
  45 6 * * * TZ=America/Denver /root/montanablotter/venv/bin/python3 \\
      /root/montanablotter/job_runner.py --name daily_planner \\
      --log /root/montanablotter/logs/daily_planner.log \\
      --workdir /root/montanablotter -- \\
      /root/montanablotter/venv/bin/python3 -m services.agents.daily_planner
"""
from __future__ import annotations

import os
import re
import sqlite3
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path

import config

QUEUE_ROOT = Path(os.getenv("AGENT_QUEUE", "/root/montanablotter/agent-queue"))
DB_PATH = Path(getattr(config, "DATABASE", "/root/montanablotter/blotter.db"))
LOGS_DIR = Path("/root/montanablotter/logs")
UPLOADS_DIR = Path("/root/montanablotter/uploads")

# Counties with active ingest adapters (blotters table)
ACTIVE_COUNTIES = {
    "cascade", "yellowstone", "missoula", "flathead", "gallatin",
    "jefferson", "madison", "carbon", "stillwater", "meagher",
    "wheatland", "valley", "roosevelt", "sanders", "ravalli",
    "rosebud", "broadwater",
}

GAP_WARN_HOURS = 72
GAP_CRITICAL_HOURS = 120
STUCK_PDF_HOURS = 6


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso_now() -> str:
    return _utcnow().strftime("%Y-%m-%dT%H:%M:%S")


def _stamp() -> str:
    # Date-only so the idempotency key is stable within a day.
    # Using YYYYMMDD_HHMM caused a new unique key every minute, letting
    # each cron run pile up a separate kanban task forever.
    return _utcnow().strftime("%Y%m%d")


def _archive_old_items(max_age_days: int = 2) -> None:
    """Move agent-queue .md files older than max_age_days to archive/ subdirs."""
    cutoff = _utcnow() - timedelta(days=max_age_days)
    for profile_dir in QUEUE_ROOT.iterdir():
        if not profile_dir.is_dir() or profile_dir.name.startswith(
            ("_", "archive", "digests", "bin", "new-counties", "red-tier")
        ):
            continue
        archive_dir = profile_dir / "archive"
        for md in profile_dir.glob("*.md"):
            try:
                mtime = datetime.fromtimestamp(md.stat().st_mtime, tz=UTC)
                if mtime < cutoff:
                    archive_dir.mkdir(exist_ok=True)
                    md.rename(archive_dir / md.name)
            except Exception:
                pass


def _write_item(
    profile: str,
    slug: str,
    tier: str,
    status: str,
    priority: str,
    body: str,
    related_county: str = "",
) -> Path:
    """Write a single work item .md file to the profile's queue directory.

    Skips writing if today's file for this slug already exists — prevents
    duplicate kanban tasks when the planner re-runs (e.g. after a crash).
    """
    queue_dir = QUEUE_ROOT / profile
    queue_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{_stamp()}-{slug}.md"
    item = queue_dir / filename
    if item.exists():
        return item  # already written today — don't create a second task
    frontmatter = textwrap.dedent(f"""\
        ---
        profile: {profile}
        created: {_iso_now()}
        tier: {tier}
        status: {status}
        priority: {priority}
        related_county: "{related_county}"
        related_files: []
        ---

    """)
    item.write_text(frontmatter + body.strip() + "\n", encoding="utf-8")
    return item


def _open_db() -> sqlite3.Connection | None:
    if not DB_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        return conn
    except Exception:
        return None


def _open_page_views() -> sqlite3.Connection | None:
    """Open the local-only page_views.db for read-only analytics queries.

    Returns None if the DB doesn't exist yet (first run, or before any
    request has been served). Callers should treat that as "no data"
    rather than an error.
    """
    try:
        from db import _PAGE_VIEWS_DB_PATH
    except Exception:
        return None
    import os
    if not os.path.exists(_PAGE_VIEWS_DB_PATH):
        return None
    try:
        conn = sqlite3.connect(str(_PAGE_VIEWS_DB_PATH), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        return None


# ── Data-collection helpers ──────────────────────────────────────────────────

def _county_ingest_gaps(conn: sqlite3.Connection) -> list[dict]:
    """Return list of {county, hours} since last blotter, sorted by gap descending."""
    gaps = []
    try:
        rows = conn.execute(
            "SELECT LOWER(county) AS county, MAX(created_at) AS last_at "
            "FROM blotters GROUP BY LOWER(county)"
        ).fetchall()
    except Exception:
        return gaps

    now = _utcnow()
    seen: set[str] = set()
    for row in rows:
        county = str(row["county"])
        seen.add(county)
        try:
            raw = str(row["last_at"]).replace(" ", "T")
            last = datetime.fromisoformat(raw)
            if last.tzinfo is None:
                last = last.replace(tzinfo=UTC)
            gap_h = (now - last).total_seconds() / 3600
            gaps.append({"county": county, "hours": round(gap_h, 1)})
        except Exception:
            continue

    # Counties with adapters but zero records ever
    for county in sorted(ACTIVE_COUNTIES - seen):
        gaps.append({"county": county, "hours": 9999.0})

    gaps.sort(key=lambda x: x["hours"], reverse=True)
    return gaps


def _stuck_pdfs() -> list[dict]:
    """Return PDFs in uploads/ older than STUCK_PDF_HOURS hours."""
    stuck = []
    if not UPLOADS_DIR.exists():
        return stuck
    cutoff = _utcnow() - timedelta(hours=STUCK_PDF_HOURS)
    try:
        for pdf in UPLOADS_DIR.glob("*.pdf"):
            mtime = datetime.fromtimestamp(pdf.stat().st_mtime, tz=UTC)
            if mtime < cutoff:
                age_h = round((_utcnow() - mtime).total_seconds() / 3600, 1)
                stuck.append({"path": str(pdf), "name": pdf.name, "age_h": age_h})
    except Exception:
        pass
    stuck.sort(key=lambda x: x["age_h"], reverse=True)
    return stuck


def _log_tail_errors(log_name: str, hours: int = 24) -> list[str]:
    """Tail a log file and return lines matching ERROR/FAIL/EXCEPTION from the last N hours."""
    log = LOGS_DIR / f"{log_name}.log"
    if not log.exists():
        return []
    errors: list[str] = []
    cutoff = _utcnow() - timedelta(hours=hours)
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-3000:]:
            if not re.search(r"error|fail|exception", line, re.IGNORECASE):
                continue
            m = re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", line)
            if m:
                try:
                    ts = datetime.fromisoformat(m.group()).replace(tzinfo=UTC)
                    if ts < cutoff:
                        continue
                except Exception:
                    pass
            errors.append(line[:220])
    except Exception:
        pass
    return errors[-20:]


def _jail_roster_stale(conn: sqlite3.Connection, threshold_h: float = 4.0) -> list[dict]:
    """Return jail_bookings counties not updated within threshold_h hours."""
    stale: list[dict] = []
    try:
        rows = conn.execute(
            "SELECT LOWER(county) AS county, MAX(last_seen_at) AS last_seen "
            "FROM jail_bookings GROUP BY LOWER(county)"
        ).fetchall()
    except Exception:
        return stale
    now = _utcnow()
    for row in rows:
        try:
            raw = str(row["last_seen"]).replace(" ", "T")
            last = datetime.fromisoformat(raw)
            if last.tzinfo is None:
                last = last.replace(tzinfo=UTC)
            gap_h = round((now - last).total_seconds() / 3600, 1)
            if gap_h > threshold_h:
                stale.append({"county": str(row["county"]), "hours": gap_h})
        except Exception:
            continue
    stale.sort(key=lambda x: x["hours"], reverse=True)
    return stale


def _post_audit_backlog(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM posts "
            "WHERE audit_status IS NULL OR audit_status = 'pending'"
        ).fetchone()
        return int(row["n"]) if row else 0
    except Exception:
        return 0


# ── Per-profile task generators ──────────────────────────────────────────────

def _generate_ops_tasks(conn: sqlite3.Connection | None) -> None:
    gunicorn_errors = _log_tail_errors("gunicorn", 24)
    backup_errors = _log_tail_errors("backup", 48)

    lines = [
        "# Daily Ops Health Check",
        "",
        "## Required actions",
        "",
        "1. Probe montanablotter.com — verify HTTP 200, response time <2s.",
        "2. Check all systemd units:",
        "   `systemctl is-active montanablotter nginx fb-page-manager blog-dup-checker`",
        "3. Disk usage: `df -h / /root` — alert if >80%.",
        "4. TLS cert: check expiry with openssl — alert if <21 days.",
        "5. Backup chain: verify 7 daily .bak files exist in db_backups/.",
        "   If latest is missing or >24h stale, trigger Yellow-tier snapshot.",
        "",
        "## Known issues",
        "",
        "- SSH brute-force sustained elevation (~11k/day) — check fail2ban counts.",
        "- Court calendar WAF block (pubcourts.mt.gov) since 2026-05-17 — note if recovered.",
        "",
    ]

    if gunicorn_errors:
        lines += [f"## Gunicorn errors (last 24h) — investigate", ""]
        for e in gunicorn_errors[:5]:
            lines.append(f"- `{e.strip()}`")
        lines.append("")

    if backup_errors:
        lines += ["## Backup log anomalies", ""]
        for e in backup_errors[:3]:
            lines.append(f"- `{e.strip()}`")
        lines.append("")

    _write_item("ops", "daily-ops-check", "green", "open", "high",
                "\n".join(lines))


def _generate_ingest_tasks(conn: sqlite3.Connection | None) -> None:
    gaps = _county_ingest_gaps(conn) if conn else []
    stuck = _stuck_pdfs()
    jail_stale = _jail_roster_stale(conn) if conn else []
    rq_errors = _log_tail_errors("rq-ingestion", 24)

    critical = [g for g in gaps if g["hours"] >= GAP_CRITICAL_HOURS]
    warn = [g for g in gaps if GAP_WARN_HOURS <= g["hours"] < GAP_CRITICAL_HOURS]

    lines = ["# Daily Pipeline Health Report", ""]

    # County gaps
    lines += ["## County ingest gaps", ""]
    if critical:
        lines.append(f"**CRITICAL (>{GAP_CRITICAL_HOURS}h gap) — {len(critical)} counties:**")
        lines.append("")
        for g in critical[:10]:
            lines.append(f"- {g['county'].title()}: {g['hours']:.0f}h since last blotter")
        lines.append("")
    if warn:
        lines.append(f"**WARNING (>{GAP_WARN_HOURS}h gap) — {len(warn)} counties:**")
        lines.append("")
        for g in warn[:5]:
            lines.append(f"- {g['county'].title()}: {g['hours']:.0f}h since last blotter")
        lines.append("")
    if not critical and not warn:
        lines += ["All counties within 72h window. ✓", ""]

    # Stuck PDFs
    lines += ["## Stuck PDFs", ""]
    if stuck:
        lines.append(f"**{len(stuck)} PDFs stuck >{STUCK_PDF_HOURS}h in uploads/:**")
        lines.append("")
        for s in stuck[:5]:
            lines.append(f"- `{s['name']}` ({s['age_h']}h old)")
        if len(stuck) > 5:
            lines.append(f"- ...and {len(stuck) - 5} more")
        lines += [
            "",
            "For each: run diagnosis in report-only mode.",
            "If parse succeeds in dry-run: back up to uploads/retry/ then re-queue (Yellow tier).",
            "If parse fails: write diagnosis to agent-queue/ingest/ and escalate to blotter-dev.",
            "",
        ]
    else:
        lines += ["No stuck PDFs. ✓", ""]

    # Jail rosters
    lines += ["## Jail roster freshness", ""]
    if jail_stale:
        lines.append(f"**{len(jail_stale)} county jail rosters stale >4h:**")
        lines.append("")
        for j in jail_stale[:5]:
            lines.append(f"- {j['county'].title()}: {j['hours']}h since last update")
        lines.append("")
    else:
        lines += ["All jail rosters current. ✓", ""]

    if rq_errors:
        lines += ["## RQ ingestion errors (last 24h)", ""]
        for e in rq_errors[:5]:
            lines.append(f"- `{e.strip()}`")
        lines.append("")

    lines += [
        "## Known issues",
        "",
        "- Gallatin Zuercher portal: in SKIPPED_SOURCES since 2026-05-11. Verify with blotter-scraper.",
        "- 5 broken county adapters: Lewis and Clark, Cascade, Carbon, Valley, Unknown.",
        "- No new blotters since 2026-05-20 19:10 — confirm email worker is receiving source emails.",
        "",
    ]

    priority = "high" if (critical or len(stuck) >= 10) else "med"
    _write_item("ingest", "daily-pipeline-health", "green", "open", priority,
                "\n".join(lines))

    # Separate P0 triage task if stuck PDF backlog is large
    if len(stuck) >= 10:
        triage_lines = [
            f"# Stuck PDF Batch Triage — {len(stuck)} Files",
            "",
            "Batch-diagnose by age. Prioritize major counties (Gallatin, Missoula, Yellowstone).",
            "",
            "## Top 10 oldest",
            "",
        ]
        for s in stuck[:10]:
            triage_lines.append(f"- `{s['name']}` — {s['age_h']}h old")
        triage_lines += [
            "",
            "## Steps",
            "",
            "1. Identify county from filename pattern.",
            "2. Dry-run parse: `python3 processor.py --dry-run --file <path>`",
            "3. Classify: corrupt PDF / format change / parser bug / encoding issue.",
            "4. Success → back up + re-queue (Yellow). Failure → escalate to blotter-dev.",
            "",
        ]
        _write_item("ingest", "stuck-pdf-triage", "yellow", "open", "high",
                    "\n".join(triage_lines))


def _generate_dev_tasks(conn: sqlite3.Connection | None) -> None:
    new_counties_dir = QUEUE_ROOT / "new-counties"
    pending_counties: list[str] = []
    if new_counties_dir.exists():
        for item in sorted(new_counties_dir.iterdir()):
            if item.is_dir() and not item.name.startswith(("_", "archive")):
                pending_counties.append(item.name)

    lines = [
        "# Daily Dev Queue",
        "",
        "## Priority 1 — Court calendar WAF block (broken since 2026-05-17)",
        "",
        "All 100+ MT courts returning 'Request Rejected' on pubcourts.mt.gov.",
        "Root cause: the server is blocking our IP, user-agent, or request pattern.",
        "",
        "Investigation steps:",
        "1. Check `services/court/colj_portal_scraper.py` — review `_login()` method.",
        "2. Test if rotating the user-agent header resolves the block.",
        "3. Check if adding Referer header or increasing wait time helps.",
        "4. Draft minimal fix as a red-tier proposal in agent-queue/dev/.",
        "",
        "## Priority 2 — Gallatin Zuercher recovery",
        "",
        "Gallatin is in SKIPPED_SOURCES at `services/ingestion/jail_bookings.py:61`.",
        "When blotter-scraper confirms the portal returns valid data, draft the 2-line",
        "removal from SKIPPED_SOURCES as a red-tier proposal.",
        "",
        "## Priority 3 — 5 broken county adapters",
        "",
        "Investigate each; determine if source changed format or feed is dead:",
        "- Lewis and Clark County",
        "- Cascade County",
        "- Carbon County",
        "- Valley County",
        "- Unknown (check for agency normalization issue in blotter parser)",
        "",
    ]

    if pending_counties:
        lines += [
            "## New county adapter scaffolding",
            "",
            f"{len(pending_counties)} counties queued in agent-queue/new-counties/:",
            "",
        ]
        for c in pending_counties[:5]:
            lines.append(f"- {c}")
        lines += [
            "",
            "For each: scaffold adapter using `services/ingestion/fetchers/` as template.",
            "Submit scaffolded file as red-tier proposal; do not merge without Jon approval.",
            "",
        ]

    _write_item("dev", "daily-dev-queue", "green", "open", "high",
                "\n".join(lines))


def _generate_civic_tasks(conn: sqlite3.Connection | None) -> None:
    gaps = _county_ingest_gaps(conn) if conn else []
    long_silent = [g for g in gaps if g["hours"] >= 7 * 24]

    lines = ["# Daily Civic Work", ""]

    if long_silent:
        lines += [
            f"## County outreach targets — {len(long_silent)} counties silent >7 days",
            "",
            "Draft a polite check-in email for each (do NOT send — red tier).",
            "Save each draft to agent-queue/civic/. Use county-outreach-email skill template.",
            "",
        ]
        for g in long_silent[:5]:
            days = g["hours"] / 24
            lines.append(f"- {g['county'].title()}: {days:.0f} days since last blotter")
        if len(long_silent) > 5:
            lines.append(f"- ...and {len(long_silent) - 5} more")
        lines.append("")
    else:
        lines += ["No counties silent >7 days. ✓", ""]

    lines += [
        "## Source expansion research",
        "",
        "Research the following for jail rosters / blotter PDFs / CrimeMapping feeds:",
        "- Beaverhead County: beaverheadcountymt.gov/departments/sheriff/",
        "- Big Horn County: bighorncountymt.gov/239/Detention",
        "- Chouteau County: research sheriff page",
        "- Glacier County: glaciercounty.org sheriff page",
        "- Hill County / Havre PD: expand beyond image-based emails",
        "",
        "For each: document findings in agent-queue/civic/. If source found, draft",
        "source entry for blotter-dev/blotter-scraper.",
        "",
        "## Roster maintenance",
        "",
        "Review agent-queue/civic/_roster.yaml:",
        "- Flag contacts with no outreach in >60 days for refresh.",
        "- Add newly discovered sheriff/PIO contacts.",
        "- Update last_contact_at for any counties contacted this week.",
        "",
    ]

    priority = "high" if long_silent else "med"
    _write_item("civic", "daily-civic-work", "green", "open", priority,
                "\n".join(lines))


def _generate_scraper_tasks(conn: sqlite3.Connection | None) -> None:
    scrapers = [
        ("jail_booking_ingest", "Jail rosters (all counties)", "4h"),
        ("missoula_fetcher", "Missoula public report", "1h"),
        ("crimemapping", "CrimeMapping 8 MT agencies", "12h"),
        ("whitefish_fetcher", "Whitefish PD blotter", "6h"),
        ("mhp_crashes", "MHP crash news releases", "24h"),
        ("sex_offender_daily", "MT sex/violent offender registry", "8h"),
        ("missing_person_watch", "MT missing persons watch", "1h"),
        ("bozeman_calls", "Bozeman PD calls-for-service", "1h"),
    ]

    lines = ["# Daily Scraper Health Check", "", "## Active scraper status", ""]
    any_errors = False
    for log_name, label, expected in scrapers:
        errors = _log_tail_errors(log_name, 26)
        if errors:
            any_errors = True
            lines.append(f"- ⚠️  {label} (expect every {expected}): {len(errors)} errors")
        else:
            lines.append(f"- ✓  {label} (expect every {expected})")
    lines.append("")

    lines += [
        "## Gallatin Zuercher recovery check",
        "",
        "URL: https://gallatin-so-mt.zuercherportal.com/api/inmates",
        "Status (2026-05-11): maintenance mode (in SKIPPED_SOURCES).",
        "Action: HTTP GET to API endpoint. If HTTP 200 + valid JSON array:",
        "  → Write recovery item to agent-queue/ingest/ for blotter-dev to re-enable.",
        "",
        "## Court calendar recovery check",
        "",
        "URL: https://coljportal.pubcourts.mt.gov/fullcourtweb/start.do",
        "Status (2026-05-17): WAF blocking — all courts return 'Request Rejected'.",
        "Action: attempt HEAD request. If no longer blocking, note in agent-queue/ingest/.",
        "If still blocked: confirm status, escalate to blotter-dev for user-agent fix.",
        "",
    ]

    priority = "high" if any_errors else "med"
    _write_item("ops", "daily-scraper-health", "green", "open", priority,
                "\n".join(lines))


def _generate_parser_tasks(conn: sqlite3.Connection | None) -> None:
    audit_backlog = _post_audit_backlog(conn) if conn else 0
    rq_parse_errors = _log_tail_errors("rq-parsing", 24)
    image_errors = _log_tail_errors("email_image_blotter", 24)

    lines = [
        "# Daily Parse Quality Audit",
        "",
        "## Parse success rate — run this query",
        "",
        "```sql",
        "SELECT",
        "    LOWER(county) AS county,",
        "    COUNT(*) AS total,",
        "    SUM(CASE WHEN parse_error IS NULL THEN 1 ELSE 0 END) AS ok,",
        "    ROUND(100.0 * SUM(CASE WHEN parse_error IS NULL THEN 1 ELSE 0 END)",
        "          / COUNT(*), 1) AS pct",
        "FROM blotters",
        "WHERE created_at >= datetime('now', '-7 days')",
        "GROUP BY LOWER(county)",
        "HAVING pct < 95 OR total = 0",
        "ORDER BY pct ASC;",
        "```",
        "",
        "Flag any county below 95% to blotter-dev immediately.",
        "",
        "## Record spot-check",
        "",
        "For 5 random counties, inspect 3 recent records each:",
        "- Name field: proper format, no special chars or truncation.",
        "- Charges: not empty, not garbled.",
        "- Date: within the last 90 days.",
        "- Agency: normalized county/city name.",
        "",
        "Alert on: empty charges, garbled names, dates >3 months ago.",
        "",
    ]

    if audit_backlog > 0:
        lines += [
            f"## PII audit backlog — {audit_backlog} posts pending",
            "",
            "Run blotter_auditor.py --all-pending in dry-run to check scope.",
            "Report count and top priority posts to ops digest.",
            "",
        ]

    if rq_parse_errors:
        lines += ["## RQ parsing errors (last 24h)", ""]
        for e in rq_parse_errors[:5]:
            lines.append(f"- `{e.strip()}`")
        lines.append("")

    if image_errors:
        lines += ["## Image blotter errors (Havre PD)", ""]
        for e in image_errors[:3]:
            lines.append(f"- `{e.strip()}`")
        lines.append("")
    else:
        lines += ["## Image blotter (Havre PD)", "", "No errors in last 24h. ✓", ""]

    priority = "high" if (rq_parse_errors or audit_backlog > 50) else "med"
    _write_item("ingest", "daily-parse-audit", "green", "open", priority,
                "\n".join(lines))


def _top_pages(conn: sqlite3.Connection | None = None, days: int = 7, limit: int = 10) -> list[dict]:
    """Return top pages by view count over the last N days.

    Reads from `page_views.db` (the dedicated local-only table for
    page-view analytics) rather than the main `blotter.db`. The optional
    `conn` arg is preserved for backward compatibility with test
    fixtures; production callers should leave it None.
    """
    pv = conn if conn is not None else _open_page_views()
    if pv is None:
        return []
    try:
        rows = pv.execute(
            "SELECT path, COUNT(*) AS views FROM page_views "
            "WHERE created_at >= datetime('now', ?) "
            "GROUP BY path ORDER BY views DESC LIMIT ?",
            (f"-{days} days", limit),
        ).fetchall()
        return [{"path": r["path"], "views": r["views"]} for r in rows]
    except Exception:
        return []
    finally:
        if conn is None:
            try:
                pv.close()
            except Exception:
                pass


def _top_referrers(conn: sqlite3.Connection | None = None, days: int = 7, limit: int = 10) -> list[dict]:
    """Return top external referrers over the last N days. See `_top_pages`."""
    pv = conn if conn is not None else _open_page_views()
    if pv is None:
        return []
    try:
        rows = pv.execute(
            "SELECT referrer, COUNT(*) AS visits FROM page_views "
            "WHERE created_at >= datetime('now', ?) "
            "AND referrer IS NOT NULL AND referrer != '' "
            "GROUP BY referrer ORDER BY visits DESC LIMIT ?",
            (f"-{days} days", limit),
        ).fetchall()
        return [{"referrer": r["referrer"], "visits": r["visits"]} for r in rows]
    except Exception:
        return []
    finally:
        if conn is None:
            try:
                pv.close()
            except Exception:
                pass


def _total_views(days: int, conn: sqlite3.Connection | None = None) -> int:
    """Return total page views in the last N days. See `_top_pages`."""
    pv = conn if conn is not None else _open_page_views()
    if pv is None:
        return 0
    try:
        row = pv.execute(
            "SELECT COUNT(*) AS n FROM page_views WHERE created_at >= datetime('now', ?)",
            (f"-{days} days",),
        ).fetchone()
        return int(row["n"]) if row else 0
    except Exception:
        return 0
    finally:
        if conn is None:
            try:
                pv.close()
            except Exception:
                pass


# Weekly research track: day-of-week → (slug, title, body-intro)
_GROWTH_WEEKLY_TRACKS: dict[int, tuple[str, str, str]] = {
    0: (  # Monday
        "seo-audit",
        "Weekly SEO Audit",
        """Search for high-intent Montana queries that residents use to find crime and court records.
Examples: "Cascade County arrests", "Missoula jail roster", "Montana court records [county]",
"[city] police blotter", "Montana sex offender registry", "missing persons Montana".

For each: check if montanablotter.com appears in top 10 results. Note position and any competing pages.

**Deliverables (all Green/Yellow tier):**
1. List 5 keywords the site could rank for but currently doesn't — note search volume estimate.
2. For 2 existing high-traffic pages, identify one on-page SEO improvement (title tag, meta description, H1, internal links).
3. Draft improved title tag + meta description for one page. Save as Red-tier proposal in agent-queue/growth/.
""",
    ),
    1: (  # Tuesday
        "social-drafts",
        "Weekly Social Media Drafts",
        """Write 5 ready-to-post social media updates based on top recent content from montanablotter.com.

Query `posts` and `blog_posts` tables for content published in the last 3 days.
Pick posts with the highest public interest (unusual charges, large arrest sweeps, notable names if public figures).

**Format each draft:**
- Platform: Facebook OR X (Twitter)
- Text: 1-3 sentences, factual, no sensationalism. Link to the post.
- Suggested hashtags: #Montana #MontanaNews #[County]

Save all 5 drafts to `agent-queue/growth/YYYYMMDD-social-drafts.md` as a **Red-tier item** — do NOT post without Jon's approval.
""",
    ),
    2: (  # Wednesday
        "link-building",
        "Weekly Link Building Research",
        """Identify Montana online communities and outlets that could link to or share montanablotter.com content.

Research targets:
- Montana subreddits: r/Montana, r/missoula, r/billings, r/greatfalls, r/bozeman
- Local Facebook groups for each major county
- Montana newspaper sites (Billings Gazette, Missoulian, Great Falls Tribune, Bozeman Daily Chronicle)
- Montana civic blogs, local government sites, public defender offices
- County library websites that link to local news

**Deliverables:**
1. List 5 specific communities + the type of content they'd share most.
2. For 2 communities: draft a specific, non-spammy post or outreach message tied to recent content.
3. Identify 1 Montana publication that could be approached for a reciprocal link or guest post.

Save as Yellow-tier item. Outreach drafts that involve external contact → Red tier.
""",
    ),
    3: (  # Thursday
        "email-strategy",
        "Weekly Email Strategy",
        """Analyze the morning briefing email performance and propose improvements.

Check:
- `subscribers` table: total count, recent signups, unsubscribe rate if tracked.
- `morning_briefing.py` / `logs/briefing.log`: any errors or send failures in last 7 days.

**Deliverables:**
1. Draft 3 subject line variants for the next morning briefing (A/B test candidates).
   Current style: factual headline. Test: question format, "X arrests in [county]", local angle.
2. Propose one structural improvement to the email format (e.g., add county traffic stats, add "most wanted" section, add court filing highlight).
3. If subscriber count < 1000: propose 2 ways to grow the list (e.g., prominent signup CTA on top pages).

Save as Yellow-tier item.
""",
    ),
    4: (  # Friday
        "paid-ads-research",
        "Weekly Paid Advertising Research",
        """Research paid advertising options for driving qualified Montana traffic to montanablotter.com.

Research (web search, no spend):
- **Google Ads**: What does a local news/crime blotter campaign look like? What keywords, match types, and landing pages work for local news? Estimate CPC for Montana local intent keywords.
- **Facebook/Meta Ads**: What audience targeting options exist for Montana residents interested in local news and public safety? What ad formats perform best for news sites?
- **Reddit Ads**: r/Montana sponsorship options, cost, typical CTR for local news.

**Deliverable (Red tier — proposal only):**
Draft a campaign brief:
- Platform recommendation (1-2 platforms to start)
- Audience targeting spec
- Suggested monthly budget range ($50-$500)
- 2 ad creative concepts (headline + body copy)
- Landing page recommendation (which site page to drive to)
- Success metric (target CPC, CTR, sessions/day)

Save as `agent-queue/growth/YYYYMMDD-paid-ads-brief.md` — Red tier, requires Jon approval before any spend.
""",
    ),
    5: (  # Saturday
        "content-gap-analysis",
        "Weekly Content Gap Analysis",
        """Find high-volume Montana search queries that montanablotter.com doesn't cover well.

Search for what Montanans ask Google about crime, arrests, courts, and public records.
Think: "[county] sheriff", "[city] police news", "Montana parole search", "Montana warrant search",
"Montana public records", "free background check Montana", "court case lookup Montana".

**Deliverables:**
1. List 5 content gaps — topics people search for that the site either doesn't cover or has thin content on.
2. For each gap: note estimated search intent (informational vs. transactional) and whether it's achievable.
3. Propose 3 specific blog post topics to `blotter-dev` that could capture search traffic.
   Include: suggested title, target keyword, rough outline (3 H2s), and why it would rank.

Save as Yellow-tier item in agent-queue/growth/. Blog post proposals also go to agent-queue/dev/.
""",
    ),
    6: (  # Sunday
        "weekly-growth-review",
        "Weekly Growth Strategy Review",
        """Review all growth work done this week and write a weekly growth summary.

1. List all items written to agent-queue/growth/ this week (ls by date).
2. For each: one-line summary of the recommendation and its estimated impact (High/Med/Low).
3. Note any Red-tier items pending Jon's review.
4. Score this week's top 3 opportunities by: impact × feasibility (1-5 each).
5. Identify one growth experiment to prioritize next week.

Write the summary to `agent-queue/growth/YYYYMMDD-weekly-growth-review.md`.

Also query the DB for this week's total page views vs. last week and include the delta.
""",
    ),
}


def _generate_growth_tasks(conn: sqlite3.Connection | None) -> None:
    now = _utcnow()
    dow = now.weekday()  # 0=Monday … 6=Sunday

    # ── Daily intelligence report ────────────────────────────────────────────
    # Page-view helpers default to opening page_views.db themselves. We
    # only pass `conn` here when the planner was called with a custom
    # test fixture (production callers pass conn=None).
    _pv_conn = conn if conn is not None else None
    views_7d = _total_views(7, _pv_conn)
    views_1d = _total_views(1, _pv_conn)
    top_pages = _top_pages(_pv_conn, days=7)
    top_refs = _top_referrers(_pv_conn, days=7)

    lines = ["# Daily Growth Intelligence", ""]

    lines += [f"## Traffic snapshot (last 7 days)", ""]
    if views_7d:
        lines.append(f"- Total views (7d): **{views_7d:,}**")
        lines.append(f"- Views yesterday: **{views_1d:,}**")
        daily_avg = round(views_7d / 7)
        trend = "↑ above" if views_1d > daily_avg else "↓ below"
        lines.append(f"- 7-day daily average: {daily_avg:,} — yesterday was {trend} average")
        lines.append("")
    else:
        lines += ["*(DB unavailable — run queries manually)*", ""]

    lines += ["## Top pages (last 7 days)", ""]
    if top_pages:
        for p in top_pages:
            lines.append(f"- `{p['path']}` — {p['views']:,} views")
    else:
        lines.append("- Run: `SELECT path, COUNT(*) FROM page_views WHERE created_at >= datetime('now','-7 days') GROUP BY path ORDER BY 2 DESC LIMIT 10;`")
    lines.append("")

    lines += ["## Top referrers (last 7 days)", ""]
    if top_refs:
        for r in top_refs:
            lines.append(f"- `{r['referrer']}` — {r['visits']:,} visits")
    else:
        lines.append("- Run: `SELECT referrer, COUNT(*) FROM page_views WHERE created_at >= datetime('now','-7 days') AND referrer != '' GROUP BY referrer ORDER BY 2 DESC LIMIT 10;`")
    lines.append("")

    lines += [
        "## Today's growth tasks",
        "",
        "1. Identify the single top-performing page above and check: Is there a clear CTA, share button, or email signup? If not, draft a Yellow-tier improvement proposal.",
        "2. Look at the top referrer — is it a community or site we should engage with more? If yes, note in weekly link-building research.",
        "3. Flag any path with unusually high traffic today for social media promotion (Red tier).",
        "",
    ]

    _write_item("growth", "growth-intel", "green", "open", "high", "\n".join(lines))

    # ── Weekly research track ────────────────────────────────────────────────
    if dow in _GROWTH_WEEKLY_TRACKS:
        slug, title, body = _GROWTH_WEEKLY_TRACKS[dow]
        _write_item("growth", slug, "green", "open", "med", f"# {title}\n\n{body}")


# ── Main ─────────────────────────────────────────────────────────────────────

def run() -> None:
    today = _utcnow().strftime("%Y-%m-%d")
    print(f"[daily_planner] {today} — generating autonomous agent tasks")

    _archive_old_items(max_age_days=2)
    print("  ✓ archived stale agent-queue items")

    conn = _open_db()
    if conn is None:
        print("[daily_planner] WARNING: could not open DB — generating tasks from logs only")

    try:
        _generate_ops_tasks(conn)
        print("  ✓ ops — daily-ops-check written")

        _generate_ingest_tasks(conn)
        print("  ✓ ingest — daily-pipeline-health + stuck-pdf-triage written")

        _generate_dev_tasks(conn)
        print("  ✓ dev — daily-dev-queue written")

        _generate_civic_tasks(conn)
        print("  ✓ civic — daily-civic-work written")

        _generate_scraper_tasks(conn)
        print("  ✓ ops — daily-scraper-health written")

        _generate_parser_tasks(conn)
        print("  ✓ ingest — daily-parse-audit written")

        _generate_growth_tasks(conn)
        print("  ✓ growth — growth-intel + weekly track written")

    finally:
        if conn:
            conn.close()

    print(f"[daily_planner] done — tasks written to {QUEUE_ROOT}")


if __name__ == "__main__":
    run()
