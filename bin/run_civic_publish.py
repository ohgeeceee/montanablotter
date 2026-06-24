#!/usr/bin/env python3
"""
bin/run_civic_publish.py — Montana Blotter civic queue publisher.

Scans /root/montanablotter/agent-queue/civic/ for items in
``status=approved`` or ``status=rejected`` state, persists approved items to
``blotter.db`` (blog_posts or posts), fans them out to syndication targets,
archives the originals, and notifies Telegram chat -1003964766408 via the
Hermes bot.

Two entry points share one execution path:

* Cron / bulk: ``bin/run_civic_publish.py`` — scans the entire queue.
* Slash-command: ``bin/run_civic_publish.py --record-id <id> [--action approve|reject]``
  — processes a single item. Used by the active Hermes session when a user
  types ``/approve <draft_id>`` or ``/reject <draft_id> <reason>`` in Telegram.

Reference: see the companion skill
``blotter-publication-and-social-syndication`` for the full design.

This script is intentionally stdlib-only at the top level so it can run
without a venv if necessary. The MB venv is recommended for real runs
because it owns the schema migrations.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

# ---------------------------------------------------------------------------
# Constants — paths, env vars, status vocabulary
# ---------------------------------------------------------------------------

REPO_ROOT = Path("/root/montanablotter")
CIVIC_QUEUE_DIR = REPO_ROOT / "agent-queue" / "civic"
CIVIC_ARCHIVE_DIR = CIVIC_QUEUE_DIR / "archive"
DB_PATH = REPO_ROOT / "blotter.db"
DB_BACKUP_DIR = REPO_ROOT / "db_backups"

TELEGRAM_CHAT_ID = "-1003964766408"
TELEGRAM_BOT_TOKEN_ENV = "HERMES_BOT_TOKEN"
TELEGRAM_API_BASE = "https://api.telegram.org"

LOG_FILE = Path("/var/log/civic-publish.log")

# Statuses this script acts on. Anything else is left alone.
ACTIONABLE_STATUSES = frozenset({"approved", "rejected"})

# draft_id shape: 8 lowercase alphanumeric chars (first 8 of the ingest UUID)
DRAFT_ID_RE = re.compile(r"^[a-z0-9]{8}$")

# Allowed tier values. Red tier is a manual-override-only publish.
VALID_TIERS = frozenset({"green", "yellow", "red"})

# Bullet-proof Markdown-safe subset for Telegram messages. Anything outside
# this gets quoted or stripped by send_telegram().
TELEGRAM_MAX_MESSAGE_CHARS = 4000

# Map of publish_target -> SQL table name.
TARGET_TABLES = {
    "blog_posts": "blog_posts",
    "posts": "posts",
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _build_logger() -> logging.Logger:
    """
    Build a logger that writes structured one-line JSON to both stdout and
    ``/var/log/civic-publish.log``. The JSON shape is stable so the Hermes
    supervisor can parse it without regex hacks.

    Log format: ``{"ts": "...", "level": "...", "event": "...", ...fields}``
    """
    logger = logging.getLogger("civic_publish")
    logger.setLevel(logging.INFO)
    logger.propagate = False  # don't double-log via root

    class JsonFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
            payload: dict[str, Any] = {
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "level": record.levelname,
                "event": record.msg if isinstance(record.msg, str) else str(record.msg),
            }
            # Anything passed via ``logger.info("msg", extra={"k": v})`` lands here.
            for key, value in record.__dict__.items():
                if key in ("args", "asctime", "created", "exc_info", "exc_text",
                           "filename", "funcName", "levelname", "levelno",
                           "lineno", "message", "module", "msecs", "msg", "name",
                           "pathname", "process", "processName", "relativeCreated",
                           "stack_info", "thread", "threadName", "taskName"):
                    continue
                payload[key] = value
            if record.exc_info:
                payload["exc"] = self.formatException(record.exc_info)
            return json.dumps(payload, default=str, ensure_ascii=False)

    formatter = JsonFormatter()

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    logger.addHandler(stream)

    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        fileh = logging.FileHandler(LOG_FILE, encoding="utf-8")
        fileh.setFormatter(formatter)
        logger.addHandler(fileh)
    except OSError:
        # If we can't write to the log file (permissions, /var/log readonly
        # in some test environments), fall back to stdout only. Don't crash.
        logger.warning("log_file_unavailable", extra={"path": str(LOG_FILE)})

    return logger


log = _build_logger()


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Frontmatter:
    """Parsed YAML-ish frontmatter from an ITEM.md file.

    This is deliberately NOT a full YAML parser — it handles the limited
    shape we control in the ingestion skill: ``key: value`` lines plus
    ``key: [a, b]`` lists. If the frontmatter ever grows beyond that,
    swap this for ``yaml.safe_load``.
    """

    raw: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.raw[key] = value

    @property
    def status(self) -> str:
        return str(self.raw.get("status", "")).strip()

    @property
    def draft_id(self) -> str:
        return str(self.raw.get("draft_id", "")).strip()

    @property
    def tier(self) -> str:
        return str(self.raw.get("tier", "green")).strip()

    @property
    def audit_status(self) -> str:
        return str(self.raw.get("audit_status", "")).strip()

    @property
    def publish_target(self) -> str:
        return str(self.raw.get("publish_target", "blog_posts")).strip()

    @property
    def syndicate_to(self) -> list[str]:
        raw = self.raw.get("syndicate_to", ["facebook_page"])
        if isinstance(raw, list):
            return [str(x).strip() for x in raw if str(x).strip()]
        if isinstance(raw, str) and raw.strip():
            return [s.strip() for s in raw.split(",") if s.strip()]
        return ["facebook_page"]


@dataclass
class DraftItem:
    """A single civic queue item, with its on-disk location and parsed metadata."""

    path: Path  # directory containing ITEM.md
    item_md: Path
    frontmatter: Frontmatter
    body: str  # markdown body after the frontmatter

    @property
    def draft_id(self) -> str:
        return self.frontmatter.draft_id

    @property
    def status(self) -> str:
        return self.frontmatter.status


# ---------------------------------------------------------------------------
# Frontmatter parsing & writing
# ---------------------------------------------------------------------------


_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(?P<fm>.*?)\n---\s*\n(?P<body>.*)\Z",
    re.DOTALL,
)


def parse_frontmatter(text: str) -> tuple[Frontmatter, str]:
    """
    Split ``---\\n...\\n---\\n<body>`` into (frontmatter, body).

    The frontmatter mini-parser supports:
      * ``key: value`` — string value
      * ``key: [a, b, c]`` — list value
      * ``key: "quoted value"`` — quoted string
      * ``key: 2026-06-17T08:40:00-06:00`` — kept as string (no date parsing)
      * blank lines and ``#`` comments — ignored

    If the file has no frontmatter delimiters, returns an empty frontmatter
    and the full text as body.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return Frontmatter(), text

    fm_text = match.group("fm")
    body = match.group("body")
    parsed: dict[str, Any] = {}

    for line in fm_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        parsed[key] = _parse_fm_value(value)

    return Frontmatter(raw=parsed), body


def _parse_fm_value(value: str) -> Any:
    """Parse a single frontmatter value: list, quoted string, or bare string."""
    if not value:
        return ""
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_fm_value(part.strip()) for part in inner.split(",")]
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    return value


def render_item_md(frontmatter: Frontmatter, body: str) -> str:
    """Serialize (frontmatter, body) back to an ITEM.md file."""
    lines = ["---"]
    for key, value in frontmatter.raw.items():
        if isinstance(value, list):
            rendered = "[" + ", ".join(_render_fm_scalar(v) for v in value) + "]"
            lines.append(f"{key}: {rendered}")
        else:
            lines.append(f"{key}: {_render_fm_scalar(value)}")
    lines.append("---")
    lines.append("")
    if body and not body.startswith("\n"):
        lines.append("")
    lines.append(body.rstrip("\n"))
    lines.append("")
    return "\n".join(lines)


def _render_fm_scalar(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    if text == "":
        return ""
    if any(ch in text for ch in [":", "#", '"', "'", "[", "]", "{", "}"]):
        return json.dumps(text, ensure_ascii=False)
    return text


def write_item_md(item: DraftItem) -> None:
    """Persist the in-memory frontmatter + body back to disk atomically."""
    tmp = item.item_md.with_suffix(".md.tmp")
    tmp.write_text(render_item_md(item.frontmatter, item.body), encoding="utf-8")
    tmp.replace(item.item_md)


# ---------------------------------------------------------------------------
# Queue scanning
# ---------------------------------------------------------------------------


def iter_civic_items() -> Iterable[DraftItem]:
    """
    Yield every parseable ITEM.md under ``civic/`` (NOT recursing into
    ``archive/``). Items with no ``draft_id`` or unparseable frontmatter
    are logged and skipped — they will not crash the run.
    """
    if not CIVIC_QUEUE_DIR.is_dir():
        log.warning("civic_queue_missing", extra={"path": str(CIVIC_QUEUE_DIR)})
        return

    for entry in sorted(CIVIC_QUEUE_DIR.iterdir()):
        if not entry.is_dir() or entry.name == "archive" or entry.name.startswith("."):
            continue
        item_md = entry / "ITEM.md"
        if not item_md.is_file():
            log.info("skip_no_item_md", extra={"dir": entry.name})
            continue
        try:
            text = item_md.read_text(encoding="utf-8")
        except OSError as exc:
            log.warning("read_failed", extra={"dir": entry.name, "error": str(exc)})
            continue
        fm, body = parse_frontmatter(text)
        yield DraftItem(path=entry, item_md=item_md, frontmatter=fm, body=body)


def find_item_by_draft_id(draft_id: str) -> Optional[DraftItem]:
    """Locate a single item in the queue by ``draft_id``."""
    if not DRAFT_ID_RE.match(draft_id):
        return None
    for item in iter_civic_items():
        if item.draft_id == draft_id:
            return item
    return None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class PublishRefused(Exception):
    """Raised when an item is well-formed but cannot be published."""

    def __init__(self, reason: str, **fields: Any) -> None:
        super().__init__(reason)
        self.reason = reason
        self.fields = fields


def validate_approved(item: DraftItem, *, force_red: bool, is_cron: bool) -> dict[str, Any]:
    """
    Pre-flight validation for status=approved items. Returns a dict of
    derived fields (target table, slug, etc.) the caller will use. Raises
    ``PublishRefused`` on any blocking issue.
    """
    if not DRAFT_ID_RE.match(item.draft_id):
        raise PublishRefused("bad_draft_id", draft_id=item.draft_id)

    audit_path = item.frontmatter.get("audit_report_path")
    if not audit_path:
        raise PublishRefused("missing_audit_path")
    audit_full = (item.path / audit_path).resolve()
    if not audit_full.is_file():
        raise PublishRefused("audit_report_missing", path=str(audit_full))

    audit_status = item.frontmatter.audit_status
    if audit_status != "pass":
        raise PublishRefused("audit_not_pass", audit_status=audit_status)

    tier = item.frontmatter.tier
    if tier not in VALID_TIERS:
        raise PublishRefused("bad_tier", tier=tier)
    if tier == "red" and is_cron and not force_red:
        raise PublishRefused("red_tier_blocked_in_cron")
    if tier == "red" and not force_red:
        raise PublishRefused("red_tier_blocked", note="use --force-red to override")

    if "related_county" not in item.frontmatter.raw:
        raise PublishRefused("missing_related_county")

    target = item.frontmatter.publish_target
    if target not in TARGET_TABLES:
        raise PublishRefused("bad_publish_target", target=target)

    return {"target_table": TARGET_TABLES[target]}


def compute_slug(item: DraftItem) -> str:
    """Compute a unique slug from frontmatter title + ingest_run_id tail."""
    title = str(item.frontmatter.get("title", "untitled")).strip().lower()
    slug_text = re.sub(r"[^a-z0-9]+", "-", title).strip("-")[:60] or "untitled"
    tail = item.draft_id[:6]
    return f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}-{slug_text}-{tail}"


def slug_exists(conn: sqlite3.Connection, table: str, slug: str) -> bool:
    cur = conn.execute(f"SELECT 1 FROM {table} WHERE slug = ? LIMIT 1", (slug,))
    return cur.fetchone() is not None


# ---------------------------------------------------------------------------
# DB operations
# ---------------------------------------------------------------------------


def open_db() -> sqlite3.Connection:
    """Open blotter.db with a sane busy_timeout and read-only-safe row factory."""
    if not DB_PATH.is_file():
        raise PublishRefused("db_missing", path=str(DB_PATH))
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.row_factory = sqlite3.Row
    return conn


def daily_backup_if_needed() -> None:
    """
    Take a once-per-day backup before the first publish of a new day.
    Idempotent — if today's backup exists, this is a no-op.
    """
    if not DB_PATH.is_file():
        return
    DB_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    backup_path = DB_BACKUP_DIR / f"blotter.db.{today}.pre-publish"
    if backup_path.is_file():
        return
    try:
        shutil.copy2(str(DB_PATH), str(backup_path))
        log.info("daily_backup_created", extra={"path": str(backup_path)})
    except OSError as exc:
        log.warning("daily_backup_failed", extra={"path": str(backup_path), "error": str(exc)})


def insert_blog_post(conn: sqlite3.Connection, item: DraftItem, slug: str) -> int:
    """Insert into blog_posts; return the new rowid.

    NOTE: published=1 is required. ``facebook_page_manager.py --mode post``
    scans for blog_posts with published=1 that are not already in the
    facebook_post_queue. Leaving published=0 would make the row invisible
    to the worker.
    """
    cur = conn.execute(
        """
        INSERT INTO blog_posts (title, slug, body, excerpt, author, published, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 1, datetime('now'), datetime('now'))
        """,
        (
            str(item.frontmatter.get("title", "Untitled")).strip()[:255],
            slug,
            item.body.strip(),
            _first_paragraph(item.body),
            str(item.frontmatter.get("author", "Montana Blotter")).strip()[:120],
        ),
    )
    rid = cur.lastrowid
    if rid is None:
        raise sqlite3.OperationalError("insert_blog_post: no rowid returned")
    return int(rid)


def insert_post(conn: sqlite3.Connection, item: DraftItem, blotter_id: int) -> int:
    """Insert into posts (single-incident path). Requires a blotter_id frontmatter value."""
    title = str(item.frontmatter.get("title", "Untitled")).strip()[:255]
    summary = _first_paragraph(item.body) or item.body[:500]
    county = str(item.frontmatter.get("related_county", "")).strip()[:80]
    cur = conn.execute(
        """
        INSERT INTO posts (
            record_id, blotter_id, title, summary,
            county, agency_type, agency_name, incident_type, created_at
        )
        VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            blotter_id,
            title,
            summary[:1000],
            county,
            str(item.frontmatter.get("agency_type", "other")).strip()[:40],
            str(item.frontmatter.get("agency_name", "")).strip()[:160],
            str(item.frontmatter.get("incident_type", "")).strip()[:80],
        ),
    )
    rid = cur.lastrowid
    if rid is None:
        raise sqlite3.OperationalError("insert_post: no rowid returned")
    return int(rid)


def _first_paragraph(markdown: str) -> str:
    """Return the first non-empty paragraph of markdown, stripped of formatting."""
    for block in re.split(r"\n\s*\n", markdown.strip()):
        cleaned = re.sub(r"[#*_`>]+", "", block).strip()
        if cleaned:
            return cleaned[:280]
    return ""


# ---------------------------------------------------------------------------
# Syndication
# ---------------------------------------------------------------------------


def syndicate(
    target: str,
    *,
    table: str,
    rowid: int,
    title: str,
    slug: str,
) -> tuple[str, str]:
    """
    Fire one syndication target. Returns (status, message) where status is
    one of ``ok``, ``failed``, ``skipped``, ``deferred``. NEVER raises —
    syndication failures must not roll back the DB insert.

    The Facebook target delegates to ``facebook_page_manager.py --mode post``,
    which is a self-contained worker that scans blotter.db for new blog_posts
    rows and posts them on its own. We do NOT pass a specific post id —
    the worker picks the most recent unpublished row, and we trust that to
    be ours. If the worker has its own cron running on a faster cadence than
    ours, it will pick up the row before we even get here; the shell-out is
    a nudge, not the only path.
    """
    if target == "facebook_page":
        try:
            proc = subprocess.run(
                [sys.executable, str(REPO_ROOT / "facebook_page_manager.py"),
                 "--mode", "post", "--limit", "1"],
                check=False, timeout=60, capture_output=True, text=True,
            )
            if proc.returncode == 0:
                return "ok", "facebook worker ok (may have posted our row or a more recent one)"
            return "failed", f"facebook worker exit {proc.returncode}: {(proc.stderr or proc.stdout)[:200]}"
        except subprocess.TimeoutExpired:
            return "failed", "facebook worker timeout"
        except OSError as exc:
            return "failed", f"facebook worker os error: {exc}"
    if target == "rss_feed":
        return "ok", "rss auto-renders"
    if target == "email_digest":
        return "pending", "queued for next daily_blog_worker"
    return "skipped", f"unknown target: {target}"


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------


def archive_item(item: DraftItem, *, prefix: str = "") -> Path:
    """
    Move the item directory from ``civic/`` to ``civic/archive/<YYYY-MM>/<dir>``.
    Returns the new path. If the destination already exists, suffix with a
    timestamp to avoid clobbering. ``prefix`` is ``rejected-`` for rejects.
    """
    month_dir = datetime.now(timezone.utc).strftime("%Y-%m")
    target_dir = CIVIC_ARCHIVE_DIR / month_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    dest_name = (prefix + item.path.name) if prefix else item.path.name
    dest = target_dir / dest_name
    if dest.exists():
        stamp = datetime.now(timezone.utc).strftime("%H%M%S")
        dest = target_dir / f"{dest_name}-{stamp}"
    shutil.move(str(item.path), str(dest))
    return dest


# ---------------------------------------------------------------------------
# Telegram notification
# ---------------------------------------------------------------------------


def send_telegram(text: str) -> tuple[bool, str]:
    """
    Send ``text`` to TELEGRAM_CHAT_ID using the Hermes bot. Returns
    ``(ok, detail)``. NEVER raises.
    """
    token = os.environ.get(TELEGRAM_BOT_TOKEN_ENV, "").strip()
    if not token:
        return False, f"{TELEGRAM_BOT_TOKEN_ENV} env var not set"

    if len(text) > TELEGRAM_MAX_MESSAGE_CHARS:
        text = text[: TELEGRAM_MAX_MESSAGE_CHARS - 3] + "..."

    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    payload = json.dumps(
        {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown",
         "disable_web_page_preview": True}
    ).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            if resp.status == 200:
                return True, f"http {resp.status}"
            return False, f"http {resp.status}: {body[:200]}"
    except urllib.error.HTTPError as exc:
        return False, f"http {exc.code}: {exc.reason}"
    except urllib.error.URLError as exc:
        return False, f"url error: {exc.reason}"
    except OSError as exc:
        return False, f"os error: {exc}"


def preflight_telegram() -> tuple[bool, str]:
    """Return (ok, detail) for the bot-token pre-flight check."""
    token = os.environ.get(TELEGRAM_BOT_TOKEN_ENV, "").strip()
    if not token:
        return False, (
            f"{TELEGRAM_BOT_TOKEN_ENV} is not set. Add it to /etc/environment, "
            "the systemd unit's EnvironmentFile, or the cron wrapper before "
            "running this script in notify mode."
        )
    url = f"{TELEGRAM_API_BASE}/bot{token}/getMe"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            if resp.status == 200:
                return True, f"http {resp.status}"
            return False, f"http {resp.status}"
    except urllib.error.HTTPError as exc:
        return False, f"http {exc.code}: bot token invalid or revoked"
    except urllib.error.URLError as exc:
        return False, f"url error: {exc.reason}"


# ---------------------------------------------------------------------------
# Core flows
# ---------------------------------------------------------------------------


def process_approved(
    item: DraftItem,
    *,
    force_red: bool,
    is_cron: bool,
    dry_run: bool,
    skip_syndication: bool = False,
) -> dict[str, Any]:
    """
    Process a single approved item end-to-end. Returns a result dict that
    describes what happened, suitable for the session reply or cron summary.
    """
    result: dict[str, Any] = {
        "draft_id": item.draft_id,
        "action": "publish",
        "status": "ok",
        "dry_run": dry_run,
    }
    try:
        derived = validate_approved(item, force_red=force_red, is_cron=is_cron)
    except PublishRefused as exc:
        result["status"] = "refused"
        result["reason"] = exc.reason
        result["fields"] = exc.fields
        log.info("publish_refused", extra=result)
        return result

    result["target_table"] = derived["target_table"]

    if dry_run:
        slug = compute_slug(item)
        result["slug"] = slug
        result["note"] = "dry-run: validation passed, no DB write, no syndication, no archive"
        log.info("publish_dry_run_ok", extra=result)
        return result

    daily_backup_if_needed()

    conn = open_db()
    try:
        slug = compute_slug(item)
        if slug_exists(conn, result["target_table"], slug):
            result["status"] = "refused"
            result["reason"] = "slug_collision"
            result["slug"] = slug
            item.frontmatter.set("status", "publish_failed")
            item.frontmatter.set("publish_error", "slug_collision")
            write_item_md(item)
            log.warning("publish_slug_collision", extra=result)
            return result

        if result["target_table"] == "blog_posts":
            rowid = insert_blog_post(conn, item, slug)
            published_url = f"https://montanablotter.com/blog/{urllib.parse.quote(slug)}"
        else:
            blotter_id = int(item.frontmatter.get("blotter_id", 0) or 0)
            if blotter_id <= 0:
                conn.rollback()
                result["status"] = "refused"
                result["reason"] = "missing_blotter_id"
                log.warning("publish_missing_blotter_id", extra=result)
                return result
            rowid = insert_post(conn, item, blotter_id)
            published_url = f"https://montanablotter.com/posts/{rowid}"
        conn.commit()
        result["rowid"] = rowid
        result["slug"] = slug
        result["published_url"] = published_url
    except sqlite3.Error as exc:
        conn.rollback()
        result["status"] = "db_error"
        result["reason"] = str(exc)
        log.error("publish_db_error", extra=result, exc_info=True)
        return result
    finally:
        conn.close()

    synd_results: dict[str, tuple[str, str]] = {}
    if skip_syndication:
        for target in item.frontmatter.syndicate_to:
            synd_results[target] = ("skipped", "syndication skipped by --skip-syndication")
        log.info("syndication_skipped", extra={
            "draft_id": item.draft_id,
            "targets": list(synd_results.keys()),
        })
    else:
        for target in item.frontmatter.syndicate_to:
            synd_results[target] = syndicate(
                target,
                table=result["target_table"],
                rowid=rowid,
                title=str(item.frontmatter.get("title", "")),
                slug=slug,
            )
    result["syndication"] = {k: list(v) for k, v in synd_results.items()}

    item.frontmatter.set("status", "published")
    item.frontmatter.set("published_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    item.frontmatter.set("published_url", published_url)
    item.frontmatter.set("published_id", rowid)
    item.frontmatter.set("publish_error", "")
    synd_summary = ", ".join(
        f"{k}={v[0]}" for k, v in synd_results.items()
    ) or "none"
    item.body = item.body.rstrip() + f"\n\n# Publish Outcome\n\n- published_id: {rowid}\n- published_url: {published_url}\n- syndication: {synd_summary}\n"
    write_item_md(item)

    archived_to = archive_item(item)
    result["archived_to"] = str(archived_to)

    log.info("publish_ok", extra=result)
    return result


def process_rejected(item: DraftItem, *, dry_run: bool) -> dict[str, Any]:
    """
    Archive a rejected item, appending the outcome note. No DB write, no
    syndication.
    """
    result = {
        "draft_id": item.draft_id,
        "action": "reject",
        "status": "ok",
        "dry_run": dry_run,
    }
    reason = str(item.frontmatter.get("reject_reason", "")).strip()
    rejected_by = str(item.frontmatter.get("rejected_by", "")).strip()
    rejected_at = str(item.frontmatter.get("rejected_at", "")).strip()
    if not reason:
        result["status"] = "refused"
        result["reason"] = "missing_reject_reason"
        log.warning("reject_missing_reason", extra=result)
        return result

    result["rejected_by"] = rejected_by
    result["rejected_at"] = rejected_at
    result["reason"] = reason

    if dry_run:
        result["note"] = "dry-run: validation passed, no archive move"
        log.info("reject_dry_run_ok", extra=result)
        return result

    item.body = (
        item.body.rstrip()
        + f"\n\n# Reject Outcome\n\n- rejected_by: {rejected_by or 'unknown'}\n- rejected_at: {rejected_at or datetime.now(timezone.utc).isoformat(timespec='seconds')}\n- reason: {reason}\n"
    )
    write_item_md(item)
    archived_to = archive_item(item, prefix="rejected-")
    result["archived_to"] = str(archived_to)
    log.info("reject_ok", extra=result)
    return result


# ---------------------------------------------------------------------------
# CLI entry points
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_civic_publish",
        description=(
            "Publish or archive Montana Blotter civic queue drafts. "
            "Default mode is bulk (cron); use --record-id for slash-command "
            "single-item processing."
        ),
    )
    p.add_argument(
        "--record-id", metavar="DRAFT_ID",
        help="Process only this draft_id (slash-command entry point).",
    )
    p.add_argument(
        "--action", choices=("approve", "reject"),
        help="Required with --record-id. Tells the script which transition to apply.",
    )
    p.add_argument(
        "--reject-reason", metavar="TEXT",
        help="Used with --action reject to write reject_reason into frontmatter before archiving.",
    )
    p.add_argument(
        "--rejected-by", metavar="USER_ID",
        help="Telegram user id (integer) for the reject decision. Used with --action reject.",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Scan and validate, but make no DB writes, no syndication calls, no archive moves.",
    )
    p.add_argument(
        "--force-red", action="store_true",
        help="Override red-tier block. Manual invocations only; cron never force-publishes.",
    )
    p.add_argument(
        "--skip-preflight", action="store_true",
        help="Skip the Telegram bot token pre-flight check (useful for batch runs where notifications aren't needed).",
    )
    p.add_argument(
        "--notify", action="store_true",
        help="Send a Telegram summary to -1003964766408 at the end of the run.",
    )
    p.add_argument(
        "--skip-syndication", action="store_true",
        help="Skip the syndication step (Facebook worker nudge, etc). Useful for smoke tests "
             "and maintenance runs that should not trigger external side effects.",
    )
    return p


def apply_slash_command_mutation(
    item: DraftItem,
    *,
    action: str,
    reject_reason: str,
    rejected_by: str,
) -> None:
    """
    For the slash-command path, write the status flip + audit fields into
    frontmatter BEFORE handing off to process_approved/process_rejected.
    The cron path never calls this — it reads status from disk directly.
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if action == "approve":
        item.frontmatter.set("status", "approved")
        item.frontmatter.set("approved_at", now)
    else:
        item.frontmatter.set("status", "rejected")
        item.frontmatter.set("rejected_at", now)
        item.frontmatter.set("rejected_by", rejected_by or "unknown")
        item.frontmatter.set("reject_reason", reject_reason)
    write_item_md(item)


def run_bulk(args: argparse.Namespace) -> list[dict[str, Any]]:
    """
    Cron entry point: scan all items, process those with actionable status.
    Skips draft_blocked and draft states silently.
    """
    results: list[dict[str, Any]] = []
    for item in iter_civic_items():
        status = item.status
        if status not in ACTIONABLE_STATUSES:
            continue
        if status == "approved":
            results.append(process_approved(
                item, force_red=args.force_red, is_cron=True, dry_run=args.dry_run,
                skip_syndication=args.skip_syndication,
            ))
        elif status == "rejected":
            results.append(process_rejected(item, dry_run=args.dry_run))
    return results


def _refuse(reason: str, **fields: Any) -> dict[str, Any]:
    """
    Build a slash-command refusal dict AND log it as a structured event.
    Centralized so the refusal log shape stays consistent across every
    early-exit branch in run_single.
    """
    payload: dict[str, Any] = {"status": "refused", "reason": reason}
    payload.update(fields)
    log.info("slash_command_refused", extra=payload)
    return payload


def run_single(args: argparse.Namespace) -> dict[str, Any]:
    """
    Slash-command entry point: locate a single item, mutate its frontmatter
    to the requested state, then process it.
    """
    if not args.record_id:
        return _refuse("missing_record_id")
    if not args.action:
        return _refuse("missing_action")
    if args.action == "reject" and not args.reject_reason:
        return _refuse("missing_reject_reason")

    item = find_item_by_draft_id(args.record_id)
    if item is None:
        return _refuse("draft_id_not_found", draft_id=args.record_id)

    current = item.status
    if current in {"approved", "rejected", "published"}:
        return _refuse(
            "wrong_state_for_transition",
            draft_id=item.draft_id,
            current_status=current,
            requested_action=args.action,
        )
    if current not in {"draft", "draft_blocked"}:
        return _refuse(
            "not_approvable",
            draft_id=item.draft_id,
            current_status=current,
        )

    apply_slash_command_mutation(
        item,
        action=args.action,
        reject_reason=args.reject_reason or "",
        rejected_by=args.rejected_by or "",
    )

    if args.action == "approve":
        return process_approved(
            item, force_red=args.force_red, is_cron=False, dry_run=args.dry_run,
            skip_syndication=args.skip_syndication,
        )
    return process_rejected(item, dry_run=args.dry_run)


def summarize_for_session(result: dict[str, Any]) -> str:
    """
    Build a one- or two-line Telegram-friendly summary for the slash-command
    reply path. Used by the active Hermes session to format its in-chat
    response.
    """
    if result.get("status") == "ok":
        if result.get("action") == "publish":
            url = result.get("published_url", "(no url)")
            slug = result.get("slug", "")
            synd = result.get("syndication", {})
            synd_str = ", ".join(f"{k}={v[0]}" for k, v in synd.items()) or "none"
            return f"✅ Published `{result.get('draft_id')}` → {url} | slug: {slug} | syndication: {synd_str}"
        if result.get("action") == "reject":
            return f"🗑 Rejected `{result.get('draft_id')}` — reason: {result.get('reason')}"
    return f"❌ `{result.get('draft_id', '?')}`: {result.get('reason', 'unknown error')}"


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    log.info(
        "run_start",
        extra={
            "record_id": args.record_id,
            "action": args.action,
            "dry_run": args.dry_run,
            "force_red": args.force_red,
            "skip_preflight": args.skip_preflight,
            "skip_syndication": args.skip_syndication,
        },
    )

    if not args.skip_preflight:
        ok, detail = preflight_telegram()
        if not ok:
            log.error("preflight_telegram_failed", extra={"detail": detail})
            # Don't hard-fail the run; just record that notifications will
            # be skipped. Cron jobs without notify= may still complete.
        else:
            log.info("preflight_telegram_ok", extra={"detail": detail})

    started = time.monotonic()
    try:
        if args.record_id:
            results = [run_single(args)]
        else:
            results = run_bulk(args)
    except Exception:
        log.exception("run_crashed")
        return 2

    elapsed = time.monotonic() - started
    summary = {
        "elapsed_seconds": round(elapsed, 3),
        "total": len(results),
        "ok": sum(1 for r in results if r.get("status") == "ok"),
        "refused": sum(1 for r in results if r.get("status") == "refused"),
        "db_error": sum(1 for r in results if r.get("status") == "db_error"),
        "dry_run": args.dry_run,
    }
    log.info("run_done", extra=summary)

    if args.notify and not args.dry_run:
        lines = [f"Civic publisher: {summary['ok']} ok, {summary['refused']} refused, {summary['db_error']} db_error in {summary['elapsed_seconds']}s"]
        for r in results:
            lines.append(summarize_for_session(r))
        msg = "\n".join(lines)
        ok, detail = send_telegram(msg)
        log.info("telegram_notify", extra={"ok": ok, "detail": detail})

    return 0 if summary["db_error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
