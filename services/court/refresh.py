import argparse
import json
import sqlite3
from datetime import UTC, datetime

import config
from services.court.source_adapters import (
    SUPREME_COURT_DAILY_ORDERS_URL,
    SUPREME_COURT_ORAL_ARGUMENTS_URL,
    SUPREME_COURT_PREVIOUS_ORAL_ARGUMENTS_URL,
    sync_montana_supreme_court_daily_opinions,
    sync_montana_supreme_court_oral_arguments,
    sync_montana_supreme_court_previous_oral_arguments,
)
from services.court.tracker import ensure_court_tracker_schema, upsert_court_source
from init_db import _configure_sqlite


SOURCE_REGISTRY = {
    'montana_supreme_court_oral_arguments': {
        'name': 'Montana Supreme Court Oral Argument Schedule',
        'source_url': SUPREME_COURT_ORAL_ARGUMENTS_URL,
        'provider_type': 'court_calendar',
        'runner': sync_montana_supreme_court_oral_arguments,
    },
    'montana_supreme_court_previous_oral_arguments': {
        'name': 'Montana Supreme Court Previous Oral Arguments',
        'source_url': SUPREME_COURT_PREVIOUS_ORAL_ARGUMENTS_URL,
        'provider_type': 'court_calendar',
        'runner': sync_montana_supreme_court_previous_oral_arguments,
    },
    'montana_supreme_court_daily_opinions': {
        'name': 'Montana Supreme Court Daily Opinions',
        'source_url': SUPREME_COURT_DAILY_ORDERS_URL,
        'provider_type': 'document_feed',
        'runner': sync_montana_supreme_court_daily_opinions,
    },
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Refresh all configured Montana court sources.')
    parser.add_argument(
        '--source',
        action='append',
        choices=sorted(SOURCE_REGISTRY.keys()),
        help='Optional source slug to run. Repeat to run more than one specific source.',
    )
    parser.add_argument('--dry-run', action='store_true', help='Run refreshes without committing database changes.')
    parser.add_argument('--json', action='store_true', help='Print JSON summary output.')
    return parser


def _mark_source_failure(conn: sqlite3.Connection, slug: str, error_message: str) -> None:
    metadata = SOURCE_REGISTRY[slug]
    source_id = upsert_court_source(
        conn,
        slug=slug,
        name=metadata['name'],
        source_url=metadata['source_url'],
        provider_type=metadata['provider_type'],
        status='active',
    )
    conn.execute(
        '''
        UPDATE court_sources
        SET last_scraped_at = datetime('now'),
            last_error = ?,
            updated_at = datetime('now')
        WHERE id = ?
        ''',
        (error_message[:1000], source_id),
    )


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    selected_sources = args.source or list(SOURCE_REGISTRY.keys())

    conn = sqlite3.connect(config.DB_PATH, timeout=float(getattr(config, 'DB_TIMEOUT_SECONDS', 30)))
    conn.row_factory = sqlite3.Row
    _configure_sqlite(conn)
    ensure_court_tracker_schema(conn)

    results = []
    failures = []
    try:
        for slug in selected_sources:
            metadata = SOURCE_REGISTRY[slug]
            started_at = datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')
            try:
                summary = metadata['runner'](conn)
                summary['started_at'] = started_at
                results.append(summary)
            except Exception as exc:
                _mark_source_failure(conn, slug, str(exc))
                failures.append({'source_slug': slug, 'error': str(exc), 'started_at': started_at})

        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
    finally:
        conn.close()

    payload = {
        'ran_at': datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S'),
        'sources_requested': selected_sources,
        'results': results,
        'failures': failures,
        'ok': not failures,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for result in results:
            print(
                f"{result['source_slug']}: cases={result.get('case_count', 0)} "
                f"events={result.get('event_count', 0)} filings={result.get('filing_count', 0)}"
            )
        for failure in failures:
            print(f"{failure['source_slug']}: ERROR {failure['error']}")
    return 0 if not failures else 1


if __name__ == '__main__':
    raise SystemExit(main())
