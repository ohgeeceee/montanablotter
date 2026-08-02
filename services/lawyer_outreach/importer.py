"""Import lawyers from target_list.csv into lawyer_outreach_prospects.

Mirrors scripts/attorney_outreach/import_target_list.py but writes into the
per-firm workflow table instead of attorney_referrals. The two coexist:
attorney_referrals is the public free directory; lawyer_outreach_prospects is
the operator-only sales pipeline that drives `/admin/lawyer-outreach`.

Import behavior:
  - Rows with empty firm_name or empty county are skipped.
  - Rows where contact_email is blank are still imported, but the cadence
    worker skips them (no email = no Day 1 draft). This is intentional:
    operators often have the firm but not yet the contact; the prospect row
    is the anchor for phone / letter / manual research.
  - Existing prospects (matched on lower(firm_name), lower(county)) are
    UPDATED in place but the importer never resets `stage`, `status`,
    `last_action_at`, or `next_action_at`. Those are operator-controlled.

Usage:
    source venv/bin/activate && python3 -m services.lawyer_outreach.importer [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys


DEFAULT_CSV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'scripts', 'attorney_outreach', 'target_list.csv',
)


def _normalize(value: str) -> str:
    return (value or '').strip()


def _row_key(firm: str, county: str) -> tuple[str, str]:
    return (firm.strip().lower(), county.strip().lower())


def _load_rows(csv_path: str) -> list[dict[str, str]]:
    with open(csv_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        return [row for row in reader]


def import_prospects_from_csv(
    conn: sqlite3.Connection,
    csv_path: str = DEFAULT_CSV_PATH,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """Upsert lawyers from the CSV into lawyer_outreach_prospects.

    Returns a counts dict {inserted, updated, skipped_blank}.
    """
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f'target_list.csv not found at {csv_path}')

    rows = _load_rows(csv_path)
    counts = {'inserted': 0, 'updated': 0, 'skipped_blank': 0}

    for raw in rows:
        firm = _normalize(raw.get('firm_name', ''))
        county = _normalize(raw.get('county', ''))
        if not firm or not county:
            counts['skipped_blank'] += 1
            continue

        existing = conn.execute(
            '''SELECT id FROM lawyer_outreach_prospects
               WHERE lower(firm_name) = ? AND lower(county) = ? LIMIT 1''',
            (_row_key(firm, county)[0], _row_key(firm, county)[1]),
        ).fetchone()

        params = (
            firm,
            county,
            _normalize(raw.get('city', '')) or None,
            _normalize(raw.get('website', '')) or None,
            _normalize(raw.get('contact_name', '')) or None,
            _normalize(raw.get('contact_email', '')) or None,
            _normalize(raw.get('practice_areas', '')) or None,
            _normalize(raw.get('notes', '')) or None,
        )

        if existing:
            if not dry_run:
                conn.execute(
                    '''UPDATE lawyer_outreach_prospects
                       SET city = ?, website = ?, contact_name = ?,
                           contact_email = ?, practice_areas = ?, notes = ?,
                           updated_at = datetime('now')
                       WHERE id = ?''',
                    (
                        params[2], params[3], params[4],
                        params[5], params[6], params[7],
                        existing['id'],
                    ),
                )
            counts['updated'] += 1
        else:
            if not dry_run:
                conn.execute(
                    '''INSERT INTO lawyer_outreach_prospects
                       (firm_name, county, city, website, contact_name,
                        contact_email, practice_areas, notes, stage, status,
                        last_action_at, next_action_at, source)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'day_1', 'queued',
                               datetime('now'), datetime('now'), 'target_list.csv')''',
                    params,
                )
            counts['inserted'] += 1

    if not dry_run:
        conn.commit()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv', default=DEFAULT_CSV_PATH,
                        help='Path to target_list.csv (default: scripts/attorney_outreach/target_list.csv)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Report counts without writing to the DB')
    args = parser.parse_args()

    import init_db
    conn = sqlite3.connect(init_db.DB_PATH)
    conn.row_factory = sqlite3.Row
    init_db.ensure_lawyer_outreach_schema(conn)

    counts = import_prospects_from_csv(conn, args.csv, dry_run=args.dry_run)
    print(
        f"[importer] inserted={counts['inserted']} updated={counts['updated']} "
        f"skipped_blank={counts['skipped_blank']} dry_run={args.dry_run}"
    )
    conn.close()
    return 0


if __name__ == '__main__':  # pragma: no cover
    sys.exit(main())