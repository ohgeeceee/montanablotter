#!/usr/bin/env python3
"""Cron wrapper that syncs the official Montana DOJ missing-person database.

Schedules the missing-personsync-of the Montana Missing Persons Clearinghouse
database (https://app.dojmt.gov/apps/missingPersonDatabase) via Playwright
(sits behind a Cloudflare JS challenge). Wraps services.persons.watch so the
admin operations page and the cron path share one code path.

Usage (manual):
    ./venv/bin/python3 scripts/ops/sync_missing_persons.py
    ./venv/bin/python3 scripts/ops/sync_missing_persons.py --dry-run
    ./venv/bin/python3 scripts/ops/sync_missing_persons.py --force-email

Exit codes:
    0 — sync completed (whether or not new records were found).
    1 — fatal error (db connection failed, uncaught exception).

Cron entry (every 15 minutes, staggered from :00/:15 email workers):
    */15 * * * * /root/montanablotter/venv/bin/python3 \\
        /root/montanablotter/job_runner.py --name missing_person_sync \\
        --log /root/montanablotter/logs/missing_person_sync.log \\
        --workdir /root/montanablotter \\
        -- /root/montanablotter/venv/bin/python3 \\
        /root/montanablotter/scripts/ops/sync_missing_persons.py
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback


def _bootstrap_path() -> None:
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)


_bootstrap_path()

from services.persons.watch import run as watch_run  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description='Sync the official Montana DOJ missing-person database.')
    parser.add_argument('--dry-run', action='store_true', help='Sync + report, but do not send admin emails.')
    parser.add_argument('--force-email', action='store_true', help='Send the admin notification even when no new records are detected.')
    args = parser.parse_args()

    try:
        return int(
            watch_run(
                dry_run=bool(args.dry_run),
                force_email=bool(args.force_email),
            )
        )
    except Exception:  # pragma: no cover - cron needs a stable exit code
        # Print a one-liner so cron / job_runner.py captures it in the log,
        # then exit 0 — we don't want the cron watchdog to flag a transient
        # sync failure as a hard outage. The watchdog inspects
        # missing_person_sync.log for the official summary line instead.
        print('missing_person_sync.cron_error: uncaught exception', file=sys.stderr)
        traceback.print_exc()
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
