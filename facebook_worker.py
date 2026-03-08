#!/usr/bin/env python3
import argparse
import json
import sys

from facebook_publisher import run_facebook_queue


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
