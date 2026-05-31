"""
Criminal Case Outcome Backfill Scraper

Finds court_cases flagged is_criminal=1 that have a past hearing event but
have not yet had their outcome scraped (outcome_scraped_at IS NULL), then
re-visits each case detail page via Playwright to pull plea, disposition,
and sentence data.

CLI:
    python -m services.court.outcome_scraper [--batch N] [--dry-run] [--json]
"""
from __future__ import annotations

import argparse
import json as _json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import config
from init_db import _configure_sqlite
from services.court.district_portal_scraper import (
    DistrictPortalScraper,
)
from services.court.tracker import ensure_court_tracker_schema, upsert_court_source

_PENDING_QUERY = '''
    SELECT
        cc.id,
        cc.case_number,
        cc.source_url,
        cc.caption,
        cc.case_type,
        c.slug AS court_slug,
        c.name AS court_name
    FROM court_cases cc
    JOIN courts c ON c.id = cc.court_id
    WHERE cc.is_criminal = 1
      AND cc.outcome_scraped_at IS NULL
      AND cc.source_url != ''
      AND EXISTS (
          SELECT 1 FROM court_events ce
          WHERE ce.case_id = cc.id
            AND ce.event_date < date('now')
      )
    ORDER BY cc.updated_at DESC
    LIMIT ?
'''


def run_outcome_backfill(
    conn: sqlite3.Connection,
    *,
    batch: int = 50,
    dry_run: bool = False,
) -> dict[str, Any]:
    ensure_court_tracker_schema(conn)
    source_id = upsert_court_source(
        conn,
        slug='montana-criminal-outcomes',
        name='Montana District Court Criminal Outcomes',
        source_url='https://dcportal.pubcourts.mt.gov/fullcourtweb/start.do',
        provider_type='case_outcomes',
        status='active',
    )
    conn.execute(
        "UPDATE court_sources SET last_scraped_at=datetime('now'), updated_at=datetime('now') WHERE id=?",
        (source_id,),
    )

    rows = conn.execute(_PENDING_QUERY, (batch,)).fetchall()
    if not rows:
        conn.execute(
            "UPDATE court_sources SET last_success_at=datetime('now'), last_error=NULL, updated_at=datetime('now') WHERE id=?",
            (source_id,),
        )
        return {
            'source_slug': 'montana_criminal_outcomes',
            'source_id': source_id,
            'updated': 0,
            'skipped': 0,
            'errors': 0,
            'total_pending': 0,
            'synced_at': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
        }

    scraper = DistrictPortalScraper()
    updated = 0
    skipped = 0
    errors = 0

    try:
        scraper._start()
        for row in rows:
            case_id = row['id']
            source_url = row['source_url']
            case_number = row['case_number']
            try:
                detail = scraper._scrape_case_detail(source_url)
                if not detail:
                    skipped += 1
                    continue
                if not dry_run:
                    conn.execute(
                        '''UPDATE court_cases SET
                           defendant_name=?, is_criminal=1, charges_text=?,
                           charges_json=?, plea=?, disposition=?,
                           sentence_text=?, sentence_date=?, sentencing_judge=?,
                           outcome_scraped_at=datetime('now'), updated_at=datetime('now')
                           WHERE id=?''',
                        (
                            detail.get('defendant_name', ''),
                            detail.get('charges_text', ''),
                            detail.get('charges_json', ''),
                            detail.get('plea', ''),
                            detail.get('disposition', ''),
                            detail.get('sentence_text', ''),
                            detail.get('sentence_date', ''),
                            detail.get('sentencing_judge', ''),
                            case_id,
                        ),
                    )
                updated += 1
            except Exception as exc:
                print(f'  ⚠️ outcome scrape failed for {case_number}: {exc}')
                errors += 1
    finally:
        scraper._stop()

    if not dry_run:
        conn.execute(
            "UPDATE court_sources SET last_success_at=datetime('now'), last_error=NULL, updated_at=datetime('now') WHERE id=?",
            (source_id,),
        )
        conn.commit()

    return {
        'source_slug': 'montana_criminal_outcomes',
        'source_id': source_id,
        'updated': updated,
        'skipped': skipped,
        'errors': errors,
        'total_pending': len(rows),
        'dry_run': dry_run,
        'synced_at': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Backfill criminal case outcomes')
    parser.add_argument('--batch', type=int, default=50, help='Cases per run (default 50)')
    parser.add_argument('--dry-run', action='store_true', help='Parse but do not write to DB')
    parser.add_argument('--json', action='store_true', dest='json_out', help='Print JSON summary')
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    conn = sqlite3.connect(config.DB_PATH, timeout=float(getattr(config, 'DB_TIMEOUT_SECONDS', 30)))
    conn.row_factory = sqlite3.Row
    _configure_sqlite(conn)
    try:
        result = run_outcome_backfill(conn, batch=args.batch, dry_run=args.dry_run)
    finally:
        conn.close()

    if args.json_out:
        print(_json.dumps(result, indent=2))
    else:
        print(
            f"outcome_scraper: updated={result['updated']} skipped={result['skipped']} "
            f"errors={result['errors']} pending={result['total_pending']}"
        )
    return 0 if result['errors'] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
