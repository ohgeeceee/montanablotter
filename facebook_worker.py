#!/usr/bin/env python3
import argparse
import fcntl
import json
import sys

from facebook_publisher import run_facebook_queue

# Prevent concurrent runs from causing sqlite3 "database is locked" errors.
_LOCKFILE = "/tmp/facebook_worker.lock"
_lock_fh = open(_LOCKFILE, "w")
try:
    fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    sys.exit(0)  # another instance is already running


def main() -> int:
    parser = argparse.ArgumentParser(description="Process queued Facebook posts.")
    parser.add_argument("--max-items", type=int, default=None, help="Maximum queue items to process in this run.")
    parser.add_argument(
        "--manual-trigger",
        action="store_true",
        help="Ignore auto-publish toggle and force a manual publisher run.",
    )
    args = parser.parse_args()

    summary = run_facebook_queue(max_items=args.max_items, manual_trigger=args.manual_trigger)
    print(json.dumps(summary, separators=(",", ":"), sort_keys=True))
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
