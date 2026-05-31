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
from services.court.district_portal_scraper import DISTRICT_COURT_PORTAL_URL, sync_montana_district_court_calendar
from services.court.colj_portal_scraper import COLJ_PORTAL_URL, sync_montana_colj_calendar
from services.court.tracker import ensure_court_tracker_schema, upsert_court_source
from services.court.watercourt_scraper import WATER_COURT_BASE_URL, sync_montana_water_court
from services.court.taxappeal_scraper import TAX_APPEAL_BASE_URL, sync_montana_tax_appeal_board
from init_db import _configure_sqlite


def _sync_montana_criminal_outcomes(conn: sqlite3.Connection) -> dict:
    from services.court.outcome_scraper import run_outcome_backfill
    return run_outcome_backfill(conn, batch=50)


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
    'montana_district_court_calendar': {
        'name': 'Montana District Court Calendar',
        'source_url': DISTRICT_COURT_PORTAL_URL,
        'provider_type': 'court_calendar',
        'runner': sync_montana_district_court_calendar,
    },
    'montana_colj_calendar': {
        'name': 'Montana Courts of Limited Jurisdiction Calendar',
        'source_url': COLJ_PORTAL_URL,
        'provider_type': 'court_calendar',
        'runner': sync_montana_colj_calendar,
    },
    'montana_criminal_outcomes': {
        'name': 'Montana District Court Criminal Outcomes',
        'source_url': DISTRICT_COURT_PORTAL_URL,
        'provider_type': 'case_outcomes',
        'runner': _sync_montana_criminal_outcomes,
    },
    'montana_water_court': {
        'name': 'Montana Water Court Notices',
        'source_url': WATER_COURT_BASE_URL,
        'provider_type': 'document_feed',
        'runner': sync_montana_water_court,
    },
    'montana_tax_appeal_board': {
        'name': 'Montana Tax Appeal Board Decisions',
        'source_url': TAX_APPEAL_BASE_URL,
        'provider_type': 'document_feed',
        'runner': sync_montana_tax_appeal_board,
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
                if summary.get('error'):
                    failures.append({'source_slug': slug, 'error': summary['error'], 'started_at': started_at})
                else:
                    results.append(summary)
            except Exception as exc:
                _mark_source_failure(conn, slug, str(exc))
                failures.append({'source_slug': slug, 'error': str(exc), 'started_at': started_at})
            # Commit per-source to avoid holding a write lock across HTTP scrapes.
            if not args.dry_run:
                conn.commit()

        if args.dry_run:
            conn.rollback()
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
