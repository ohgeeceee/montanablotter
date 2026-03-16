import argparse
import json

import config
from court_source_adapters import (
    sync_montana_district_court_directory,
    sync_montana_supreme_court_daily_opinions,
    sync_montana_supreme_court_oral_arguments,
    sync_montana_supreme_court_previous_oral_arguments,
)
from init_db import _configure_sqlite
import sqlite3


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Montana court source ingesters')
    parser.add_argument(
        '--source',
        choices=[
            'montana_district_courts',
            'montana_supreme_court_daily_opinions',
            'montana_supreme_court_oral_arguments',
            'montana_supreme_court_previous_oral_arguments',
        ],
        default='montana_district_courts',
        help='Which court source adapter to run.',
    )
    parser.add_argument(
        '--html-file',
        help='Optional local HTML file for parsing/testing instead of live fetch.',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Parse and print results without committing to the database.',
    )
    parser.add_argument(
        '--json',
        action='store_true',
        help='Print JSON summary output.',
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    html_text = None
    if args.html_file:
        with open(args.html_file, 'r', encoding='utf-8') as handle:
            html_text = handle.read()

    conn = sqlite3.connect(config.DB_PATH, timeout=float(getattr(config, 'DB_TIMEOUT_SECONDS', 30)))
    conn.row_factory = sqlite3.Row
    _configure_sqlite(conn)
    try:
        if args.source == 'montana_district_courts':
            summary = sync_montana_district_court_directory(conn, html_text=html_text)
        elif args.source == 'montana_supreme_court_daily_opinions':
            summary = sync_montana_supreme_court_daily_opinions(conn)
        elif args.source == 'montana_supreme_court_oral_arguments':
            summary = sync_montana_supreme_court_oral_arguments(conn, html_text=html_text)
        elif args.source == 'montana_supreme_court_previous_oral_arguments':
            summary = sync_montana_supreme_court_previous_oral_arguments(conn, html_text=html_text)
        else:
            raise ValueError(f'Unsupported source: {args.source}')

        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
    finally:
        conn.close()

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(
            f"source={summary['source_slug']} courts={summary.get('court_count', 0)} "
            f"cases={summary.get('case_count', 0)} events={summary.get('event_count', 0)} "
            f"filings={summary.get('filing_count', 0)} "
            f"fetched_live={summary['fetched_live']}"
        )
        for name in summary.get('court_names', []):
            print(f' - {name}')
        for case_number in summary.get('case_numbers', []):
            print(f' - {case_number}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
