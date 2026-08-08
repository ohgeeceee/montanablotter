#!/usr/bin/env python3
"""Generate personalized jail-roster data-request drafts from the gap CSV.

This script never sends email. It writes reviewable Markdown drafts under
agent-queue/ops/jail-roster-outreach/.
"""
from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GAPS = ROOT / "docs" / "montana_jail_roster_gap_contacts.csv"
TEMPLATE = ROOT / "docs" / "templates" / "jail_roster_data_request.md"
OUTPUT = ROOT / "agent-queue" / "ops" / "jail-roster-outreach"


def slugify(value: str) -> str:
    return "-".join(value.lower().replace("&", "and").split())


def main() -> None:
    template = TEMPLATE.read_text(encoding="utf-8")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    written = 0
    with GAPS.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["coverage_mode"] != "outreach":
                continue
            body = template.replace("[COUNTY]", row["county"])
            frontmatter = (
                "---\n"
                f"county: {row['county']}\n"
                f"to: {row['contact_email']}\n"
                f"phone: {row['contact_phone']}\n"
                f"official_url: {row['official_url']}\n"
                "status: draft\n"
                "send_approved: false\n"
                "---\n\n"
            )
            target = OUTPUT / f"{slugify(row['county'])}.md"
            target.write_text(frontmatter + body, encoding="utf-8")
            written += 1
    print(f"Wrote {written} outreach draft(s) to {OUTPUT}")


if __name__ == "__main__":
    main()
