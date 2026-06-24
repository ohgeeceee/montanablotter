"""
Montana Supreme Court Criminal Case Outcome Scraper

Fetches case information from the juddocumentservice.mt.gov JSON API for
Supreme Court cases already in court_cases, identifies criminal appeals
(State of Montana as Appellee), and backfills:
  - is_criminal, defendant_name, charges_text
  - disposition (affirmed / reversed / remanded / reversed_and_remanded)
  - sentencing_judge (opinion author)
  - original_court, original_case_number (from the originating district court)

CLI:
    python -m services.court.supreme_court_outcome_scraper [--batch N] [--dry-run] [--json]
"""
from __future__ import annotations

import argparse
import json as _json
import re
import sqlite3
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import config
from init_db import _configure_sqlite
from services.court.tracker import ensure_court_tracker_schema, upsert_court_source

_API_BASE = 'https://juddocumentservice.mt.gov/getCaseInformation?caseId='
_REQUEST_DELAY = 0.5  # seconds between API calls

_OPINION_RE = re.compile(
    r'Opinion\s*-\s*(?:Published|Noncite[^-]*)\s*-\s*Justice\s+(.+?)\s*-\s*(.+)',
    re.IGNORECASE,
)

_CRIMINAL_CASE_TYPE_PREFIXES = ('direct appeal',)

_CHARGES_STRIP_RE = re.compile(r'^direct appeal\s*[-–]\s*', re.IGNORECASE)

_PENDING_QUERY = '''
    SELECT
        cc.id,
        cc.case_number,
        cc.source_url,
        cc.caption
    FROM court_cases cc
    WHERE cc.outcome_scraped_at IS NULL
      AND (
          cc.source_url LIKE '%courts.mt.gov%caseInfo%'
          OR cc.source_url LIKE '%juddocumentservice.mt.gov%'
      )
    ORDER BY cc.id DESC
    LIMIT ?
'''


def fetch_case_info(case_id: str, timeout: int = 15) -> dict | None:
    url = _API_BASE + urllib.parse.quote(case_id)
    req = urllib.request.Request(
        url,
        headers={
            'User-Agent': 'Mozilla/5.0 (compatible; MontanaBlotterCourtTracker/1.0)',
            'Accept': 'application/json',
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return _json.loads(resp.read().decode('utf-8', errors='replace'))
    except Exception as exc:
        print(f'  ⚠️ API fetch failed for {case_id}: {exc}')
        return None


def _extract_outcome(data: dict) -> dict[str, Any]:
    """Parse criminal outcome fields from getCaseInformation JSON."""
    result: dict[str, Any] = {
        'is_criminal': 0,
        'defendant_name': '',
        'charges_text': '',
        'disposition': '',
        'sentencing_judge': '',
        'original_court': '',
        'original_case_number': '',
    }

    case_type = (data.get('caseType') or '').strip()
    original_court = (data.get('originalCourt') or '').strip()
    original_case_number = (data.get('originalCaseNumber') or '').strip()

    # originalCaseNumber sometimes holds the court name instead — skip it
    if 'court' in original_case_number.lower():
        original_case_number = ''

    result['original_court'] = original_court
    result['original_case_number'] = original_case_number

    # Criminal if State of Montana is the Appellee
    parties = data.get('parties') or []
    appellee_names = [
        p.get('partyName', '') for p in parties
        if (p.get('appellateRole') or '').lower() == 'appellee'
    ]
    is_state_case = any('state of montana' in n.lower() for n in appellee_names)
    is_criminal_type = any(case_type.lower().startswith(p) for p in _CRIMINAL_CASE_TYPE_PREFIXES)

    if not (is_state_case and is_criminal_type):
        return result

    result['is_criminal'] = 1

    # Defendant name from the Appellant party
    for party in parties:
        if (party.get('appellateRole') or '').lower() == 'appellant':
            result['defendant_name'] = (party.get('partyName') or '').strip()
            break

    # Charges from caseType: "Direct Appeal - Homicide-Negligent" → "Homicide-Negligent"
    result['charges_text'] = _CHARGES_STRIP_RE.sub('', case_type).strip()

    # Appellate outcome from opinion entries (regAction is newest-first)
    for action in (data.get('regAction') or []):
        desc = (action.get('description') or '').strip()
        m = _OPINION_RE.search(desc)
        if not m:
            continue
        justice = m.group(1).strip().rstrip('.')
        ruling = m.group(2).strip().upper()

        result['sentencing_judge'] = f'Justice {justice}'

        if 'REVERSED' in ruling and 'REMANDED' in ruling:
            result['disposition'] = 'reversed_and_remanded'
        elif 'REVERSED' in ruling:
            result['disposition'] = 'reversed'
        elif 'REMANDED' in ruling:
            result['disposition'] = 'remanded'
        elif 'AFFIRMED' in ruling:
            result['disposition'] = 'affirmed'
        else:
            result['disposition'] = ruling[:80].lower()
        break  # first (most recent) opinion wins

    return result


def run_supreme_court_outcome_backfill(
    conn: sqlite3.Connection,
    *,
    batch: int = 100,
    dry_run: bool = False,
) -> dict[str, Any]:
    ensure_court_tracker_schema(conn)
    source_id = upsert_court_source(
        conn,
        slug='montana-supreme-court-criminal-outcomes',
        name='Montana Supreme Court Criminal Outcomes',
        source_url='https://juddocumentservice.mt.gov/getCaseInformation',
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
        conn.commit()
        return _build_result(source_id, updated=0, skipped=0, errors=0, total=0)

    updated = criminal = skipped = errors = 0

    for row in rows:
        case_id = row['id']
        case_number = row['case_number']

        data = fetch_case_info(case_number)
        time.sleep(_REQUEST_DELAY)

        if data is None:
            errors += 1
            continue

        outcome = _extract_outcome(data)

        if not dry_run:
            conn.execute(
                '''UPDATE court_cases SET
                   is_criminal=?, defendant_name=?, charges_text=?,
                   disposition=?, sentencing_judge=?,
                   original_court=?, original_case_number=?,
                   outcome_scraped_at=datetime('now'), updated_at=datetime('now')
                   WHERE id=?''',
                (
                    outcome['is_criminal'],
                    outcome['defendant_name'],
                    outcome['charges_text'],
                    outcome['disposition'],
                    outcome['sentencing_judge'],
                    outcome['original_court'],
                    outcome['original_case_number'],
                    case_id,
                ),
            )

        updated += 1
        if outcome['is_criminal']:
            criminal += 1
        else:
            skipped += 1

    if not dry_run:
        conn.execute(
            "UPDATE court_sources SET last_success_at=datetime('now'), last_error=NULL, updated_at=datetime('now') WHERE id=?",
            (source_id,),
        )
        conn.commit()

    return _build_result(source_id, updated=updated, skipped=skipped, errors=errors, total=len(rows), criminal=criminal)


def _build_result(source_id: int, *, updated: int, skipped: int, errors: int, total: int, criminal: int = 0) -> dict:
    return {
        'source_slug': 'montana_supreme_court_criminal_outcomes',
        'source_id': source_id,
        'processed': updated,
        'criminal_identified': criminal,
        'civil_skipped': skipped,
        'errors': errors,
        'total_pending': total,
        'synced_at': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Backfill Supreme Court criminal case outcomes')
    parser.add_argument('--batch', type=int, default=100, help='Cases per run (default 100)')
    parser.add_argument('--dry-run', action='store_true', help='Parse but do not write to DB')
    parser.add_argument('--json', action='store_true', dest='json_out', help='Print JSON summary')
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    # Use a longer timeout than the default 30s — this job runs alongside gunicorn workers
    # and other nightly scrapers that can hold write locks for short bursts.
    db_timeout = max(120.0, float(getattr(config, 'DB_TIMEOUT_SECONDS', 30)))
    conn = sqlite3.connect(config.DB_PATH, timeout=db_timeout)
    conn.row_factory = sqlite3.Row
    _configure_sqlite(conn)
    conn.execute('PRAGMA journal_mode = WAL')
    conn.execute(f'PRAGMA busy_timeout = {int(db_timeout * 1000)}')
    try:
        result = run_supreme_court_outcome_backfill(conn, batch=args.batch, dry_run=args.dry_run)
    finally:
        conn.close()

    if args.json_out:
        print(_json.dumps(result, indent=2))
    else:
        print(
            f"supreme_court_outcomes: processed={result['processed']} "
            f"criminal={result['criminal_identified']} civil={result['civil_skipped']} "
            f"errors={result['errors']} pending={result['total_pending']}"
        )
    return 0 if result['errors'] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
