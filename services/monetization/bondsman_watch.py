from __future__ import annotations

import argparse
import json

import config
from services.monetization.bondsman import run_watchlist_match_cycle


def main() -> None:
    parser = argparse.ArgumentParser(description="Match bondsman watchlists against current jail bookings.")
    parser.add_argument("--public-user-id", type=int, default=None, help="Optional public user id to scope the scan")
    args = parser.parse_args()

    result = run_watchlist_match_cycle(config.DB_PATH, public_user_id=args.public_user_id)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
