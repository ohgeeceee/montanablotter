#!/usr/bin/env python3
"""
Aggregate per-profile raw digest sections into a daily or weekly digest.

Usage:
    aggregator.py daily                 # today
    aggregator.py daily 2026-05-15
    aggregator.py weekly                # current ISO week
    aggregator.py weekly 2026-W20

Reads:  $AGENT_QUEUE/digests/raw/<profile>/<date>.md
Writes: $AGENT_QUEUE/digests/daily/<date>.md
        $AGENT_QUEUE/digests/weekly/<YYYY-Www>.md

Stdlib only. Dumb concatenator — no LLM, no network. If a raw section is
missing, the aggregate says so loudly. That's a feature.
"""
import os
import re
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

ROOT = Path(os.environ.get("AGENT_QUEUE", "/root/montanablotter/agent-queue"))
PROFILES = ["ops", "ingest", "dev", "civic"]
ITEM_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-\d{4}-")


def read_raw(profile, day):
    p = ROOT / "digests" / "raw" / profile / f"{day}.md"
    return p.read_text() if p.exists() else None


def split_frontmatter(text):
    if not text or not text.startswith("---\n"):
        return {}, text or ""
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return {}, text
    fm = {}
    for line in parts[1].splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip().strip("'\"")
    return fm, parts[2]


def list_items(subdir):
    d = ROOT / subdir
    if not d.exists():
        return []
    return [
        x for x in d.iterdir()
        if x.is_dir() and not x.name.startswith(("_", "."))
    ]


def item_age_days(item, today):
    m = ITEM_NAME_RE.match(item.name)
    if not m:
        return 0
    try:
        d = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        return (today - d).days
    except ValueError:
        return 0


def overall_from(statuses):
    s = [x for x in statuses if x in ("green", "yellow", "red")]
    if "red" in s:
        return "RED"
    if "yellow" in s:
        return "YELLOW"
    if not s:
        return "UNKNOWN"
    return "GREEN"


def count_yellow(body):
    m = re.search(
        r"##\s*Yellow-tier actions[^\n]*\n(.*?)(?=\n##\s|\Z)",
        body, re.DOTALL | re.IGNORECASE,
    )
    if not m:
        return 0
    chunk = m.group(1).strip()
    if not chunk or re.search(r"\(none\)|no actions|^-+\s*$",
                              chunk, re.IGNORECASE):
        return 0
    return sum(1 for ln in chunk.splitlines()
               if ln.strip().startswith(("-", "*")))


def build_daily(day_str):
    today = datetime.strptime(day_str, "%Y-%m-%d").date()
    raws = {p: read_raw(p, day_str) for p in PROFILES}

    sections, statuses = {}, {}
    for p, raw in raws.items():
        if raw is None:
            statuses[p] = "missing"
            sections[p] = None
        else:
            fm, body = split_frontmatter(raw)
            statuses[p] = fm.get("status", "unknown").lower()
            sections[p] = body.strip()

    open_by = {sub: len(list_items(sub))
               for sub in PROFILES + ["new-counties", "red-tier"]}
    aged = {sub: sum(1 for it in list_items(sub)
                     if item_age_days(it, today) > 7)
            for sub in PROFILES + ["new-counties", "red-tier"]}

    total_open = sum(open_by[p] for p in PROFILES + ["new-counties"])
    red_open = open_by["red-tier"]
    yellow_total = sum(count_yellow(s) for s in sections.values() if s)
    overall = overall_from(statuses.values())

    out = [
        f"# Montana Blotter — Daily Digest — {day_str}",
        "",
        f"**Overall: {overall}** • Open queue items: {total_open}"
        f" • Yellow actions (24h): {yellow_total}"
        f" • Red-tier proposals open: {red_open}",
        "",
    ]
    for p in PROFILES:
        out.append(f"## {p} — {statuses[p].upper()}")
        if sections[p] is None:
            out.append(
                f"_No raw section for {day_str}. The {p} profile may "
                f"not have run, or its digest task failed._"
            )
        else:
            out.append(sections[p])
        out.append("")

    if sum(aged.values()):
        out.append("## Items aged >7 days")
        for sub, n in aged.items():
            if n:
                out.append(f"- `{sub}/`: {n}")
        out.append("")

    out_path = ROOT / "digests" / "daily" / f"{day_str}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(out))
    return out_path


def build_weekly(week_str):
    try:
        year, wk = week_str.split("-W")
        monday = datetime.strptime(
            f"{year}-W{wk}-1", "%G-W%V-%u"
        ).date()
    except ValueError:
        sys.exit(f"Bad week format: {week_str} (expected YYYY-Www)")

    days = [(monday + timedelta(days=i)).isoformat() for i in range(7)]
    daily_paths = [
        ROOT / "digests" / "daily" / f"{d}.md"
        for d in days
    ]
    present = [p for p in daily_paths if p.exists()]
    today = date.today()

    open_by = {sub: len(list_items(sub))
               for sub in PROFILES + ["new-counties", "red-tier"]}
    aged = {sub: sum(1 for it in list_items(sub)
                     if item_age_days(it, today) > 7)
            for sub in PROFILES + ["new-counties", "red-tier"]}

    out = [
        f"# Montana Blotter — Weekly Digest — {week_str}",
        f"_Week of {days[0]} through {days[-1]}_",
        "",
        f"Daily digests present this week: {len(present)}/7",
        "",
        "## Daily digests",
    ]
    for p in daily_paths:
        marker = "✓" if p.exists() else "✗"
        out.append(f"- {marker} {p.stem}")
    out.append("")

    out.append("## Queue at end of week")
    for sub in PROFILES + ["new-counties", "red-tier"]:
        suffix = " ⚠ aged" if aged[sub] else ""
        out.append(f"- {sub}: {open_by[sub]} open{suffix}")
    out.append("")

    out.append("## For Jon's weekly review")
    out.append("- Disposition any items aged >7 days")
    out.append("- Review draft PRs in `dev/`")
    out.append("- Review outreach drafts in `civic/`")
    out.append("- Review Red-tier proposals in `red-tier/` if any")
    out.append("")

    out_path = ROOT / "digests" / "weekly" / f"{week_str}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(out))
    return out_path


def main():
    if len(sys.argv) < 2:
        sys.exit("Usage: aggregator.py daily|weekly [date|YYYY-Www]")
    mode = sys.argv[1]
    if mode == "daily":
        day = sys.argv[2] if len(sys.argv) > 2 else date.today().isoformat()
        print(build_daily(day))
    elif mode == "weekly":
        wk = sys.argv[2] if len(sys.argv) > 2 else date.today().strftime("%G-W%V")
        print(build_weekly(wk))
    else:
        sys.exit(f"Unknown mode: {mode}")


if __name__ == "__main__":
    main()
