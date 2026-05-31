#!/usr/bin/env python3
"""
Auto-enable Gallatin County jail roster ingest once the Zuercher portal
leaves maintenance mode.  Run weekly via cron.
"""

import os
import re
import sqlite3
import sys
from pathlib import Path

import requests

MONTANA_DIR = Path("/root/montanablotter")
JAIL_BOOKINGS_PY = MONTANA_DIR / "services" / "ingestion" / "jail_bookings.py"
DB_PATH = MONTANA_DIR / "blotter.db"
CRONTAB_USER = "root"

GALLATIN_API = "https://gallatin-so-mt.zuercherportal.com/api/portal/inmates/load"
GALLATIN_ROSTER_URL = "https://gallatin-so-mt.zuercherportal.com/#/inmates"


def is_portal_ready() -> bool:
    """Return True if the Zuercher API returns real JSON data (not maintenance HTML)."""
    try:
        r = requests.post(
            GALLATIN_API,
            json={
                "name": "",
                "race": "all",
                "sex": "all",
                "cell_block": "all",
                "held_for_agency": "any",
                "in_custody": "2026-01-01T00:00:00.000Z",
                "paging": {"start": 0, "count": 5},
                "sorting": {"sort_by_column_tag": "name", "sort_descending": False},
            },
            headers={"Referer": GALLATIN_ROSTER_URL},
            timeout=15,
        )
        if r.status_code != 200:
            return False
        # If we get valid JSON with a records key, the portal is up.
        data = r.json()
        return isinstance(data, dict) and "records" in data
    except Exception:
        return False


def remove_from_skipped_sources() -> bool:
    """Remove 'gallatin' from SKIPPED_SOURCES in jail_bookings.py.  Idempotent."""
    text = JAIL_BOOKINGS_PY.read_text()
    if '"gallatin":' not in text.split("SKIPPED_SOURCES = {")[1].split("}")[0]:
        return False  # already removed

    new_text = re.sub(
        r'\s*"gallatin":\s*"[^"]+",?\n',
        "\n",
        text,
    )
    # Clean up double blank lines inside the dict
    new_text = re.sub(
        r'(SKIPPED_SOURCES = \{[^}]+)\n\n+([^}]+\})',
        r'\1\n\2',
        new_text,
        flags=re.DOTALL,
    )
    JAIL_BOOKINGS_PY.write_text(new_text)
    return True


def ensure_source_registry() -> bool:
    """Insert or update the gallatin row in source_registry."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, is_enabled FROM source_registry WHERE source_key = ?",
            ("gallatin",),
        ).fetchone()
        if row:
            if row["is_enabled"]:
                return False  # already enabled
            conn.execute(
                """UPDATE source_registry
                   SET is_enabled = 1, base_url = ?, updated_at = datetime('now')
                   WHERE id = ?""",
                (GALLATIN_ROSTER_URL, row["id"]),
            )
        else:
            conn.execute(
                """INSERT INTO source_registry
                   (source_key, source_type, display_name, base_url, adapter_name,
                    poll_interval_seconds, is_enabled, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "gallatin",
                    "jail_roster",
                    "Gallatin County Detention Center",
                    GALLATIN_ROSTER_URL,
                    "zuercher",
                    7200,
                    1,
                    "Auto-enabled after Zuercher portal maintenance ended",
                ),
            )
        conn.commit()
        return True
    finally:
        conn.close()


def is_in_crontab() -> bool:
    """Check if gallatin jail ingest is already in root's crontab."""
    cron = os.popen("crontab -l 2>/dev/null").read()
    return "gallatin" in cron


def add_to_crontab() -> bool:
    """Add a 2-hourly gallatin jail roster cron line.  Idempotent."""
    if is_in_crontab():
        return False

    cron = os.popen("crontab -l 2>/dev/null").read()
    new_line = (
        "# Gallatin County jail roster — poll every 2 hours\n"
        "25 */2 * * * /root/montanablotter/venv/bin/python3 /root/montanablotter/job_runner.py "
        "--name jail_booking_ingest_gallatin --log /root/montanablotter/logs/jail_booking_ingest.log "
        "--workdir /root/montanablotter -- /root/montanablotter/venv/bin/python3 "
        "/root/montanablotter/services/ingestion/jail_bookings.py --county gallatin\n"
    )
    # Insert before the "All remaining" batch line if present, otherwise append
    if "# All remaining jail roster sources" in cron:
        cron = cron.replace(
            "# All remaining jail roster sources",
            new_line + "# All remaining jail roster sources",
        )
    else:
        cron = cron + new_line

    with os.popen("crontab -", "w") as f:
        f.write(cron)
    return True


def main() -> int:
    if not is_portal_ready():
        print("Gallatin Zuercher portal is still in maintenance mode. Nothing to do.")
        return 0

    changed = False

    if remove_from_skipped_sources():
        print("Removed gallatin from SKIPPED_SOURCES.")
        changed = True

    if ensure_source_registry():
        print("Re-enabled gallatin in source_registry.")
        changed = True

    if add_to_crontab():
        print("Added gallatin to jail roster cron schedule.")
        changed = True

    if changed:
        print("Gallatin jail roster ingest is now fully enabled.")
    else:
        print("Gallatin was already enabled; no changes needed.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
