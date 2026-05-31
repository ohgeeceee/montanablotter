"""
kanban_push.py — Bridge between agent-queue/ .md files and the Hermes kanban board.

Reads open work items written by daily_planner.py and pushes them as kanban
tasks via `hermes kanban create`.  Idempotency keys prevent duplicates on
re-runs.  Should run at 06:50 MT daily, right after daily_planner (06:45).

Cron (add to crontab.txt):
  50 6 * * * TZ=America/Denver /root/montanablotter/venv/bin/python3 \\
      /root/montanablotter/job_runner.py --name kanban_push \\
      --log /root/montanablotter/logs/kanban_push.log \\
      --workdir /root/montanablotter -- \\
      /root/montanablotter/venv/bin/python3 -m services.agents.kanban_push
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

QUEUE_ROOT = Path(os.getenv("AGENT_QUEUE", "/root/montanablotter/agent-queue"))
HERMES = Path(os.getenv("HERMES_BIN", "/root/.local/bin/hermes"))

# agent-queue profile dir → kanban assignee name
PROFILE_MAP = {
    "ops": "blotter-ops",
    "ingest": "blotter-ingest",
    "dev": "blotter-dev",
    "civic": "blotter-civic",
}

PRIORITY_MAP = {
    "high": 10,
    "med": 5,
    "low": 1,
}

TIER_PRIORITY_BOOST = {
    "red": 15,
    "yellow": 5,
    "green": 0,
}


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split YAML-ish frontmatter from body. Returns (fields, body)."""
    if not text.startswith("---\n"):
        return {}, text
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return {}, text
    fields: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fields[k.strip()] = v.strip().strip('"\'')
    return fields, parts[2].strip()


def _task_title_from_slug(slug: str) -> str:
    """Convert filename slug to a readable title, e.g. '20260528_0650-daily-ops-check' → 'Daily Ops Check'."""
    parts = slug.split("-", 1)
    if len(parts) == 2:
        slug = parts[1]
    return slug.replace("-", " ").title()


def push_file(md_path: Path, assignee: str) -> bool:
    """Create a kanban task from a queue .md file. Returns True if created/found."""
    text = md_path.read_text(encoding="utf-8")
    fm, body = _parse_frontmatter(text)

    if fm.get("status", "open") != "open":
        return False

    slug = md_path.stem
    idempotency_key = slug

    title = ""
    for line in body.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    if not title:
        title = _task_title_from_slug(slug)

    priority_label = fm.get("priority", "med")
    tier = fm.get("tier", "green")
    priority = PRIORITY_MAP.get(priority_label, 5) + TIER_PRIORITY_BOOST.get(tier, 0)

    cmd = [
        str(HERMES), "kanban", "create",
        title,
        "--assignee", assignee,
        "--body", body,
        "--priority", str(priority),
        "--idempotency-key", idempotency_key,
        "--created-by", "daily_planner",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ERROR creating task for {md_path.name}: {result.stderr.strip()}", file=sys.stderr)
        return False

    task_id = result.stdout.strip()
    print(f"  ✓ {assignee} — {title[:60]} [{task_id}]")
    return True


def run() -> None:
    if not HERMES.exists():
        print(f"[kanban_push] ERROR: hermes not found at {HERMES}", file=sys.stderr)
        sys.exit(1)

    total_pushed = 0
    total_skipped = 0

    for profile_dir, assignee in PROFILE_MAP.items():
        queue_dir = QUEUE_ROOT / profile_dir
        if not queue_dir.exists():
            continue

        for md_path in sorted(queue_dir.glob("*.md")):
            if md_path.name.startswith("_"):
                continue
            if push_file(md_path, assignee):
                total_pushed += 1
            else:
                total_skipped += 1

    print(f"[kanban_push] done — {total_pushed} pushed, {total_skipped} skipped/already-exists")


if __name__ == "__main__":
    run()
