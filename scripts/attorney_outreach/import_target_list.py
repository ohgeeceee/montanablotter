"""Import attorneys from target_list.csv into attorney_referrals.

Usage:
    source venv/bin/activate && python3 scripts/attorney_outreach/import_target_list.py [--dry-run]

The script upserts by (name, firm, county). Existing sponsored rows
(sponsored = 1 or sponsor_tier IS NOT NULL) are never overwritten.
"""
from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
from typing import Any


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CSV_PATH = os.path.join(SCRIPT_DIR, 'target_list.csv')
DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(SCRIPT_DIR)), 'blotter.db')


def _normalize_name(value: str) -> str:
    """Trim and title-case a name-like value."""
    value = (value or '').strip()
    if not value:
        return ''
    return ' '.join(part.capitalize() for part in value.split())


def _build_tagline(practice_areas: str) -> str:
    """Generate a short tagline from practice areas."""
    areas = (practice_areas or '').strip()
    if not areas:
        return 'Montana criminal defense attorney'
    # Trim to two focus areas for readability.
    parts = [a.strip() for a in areas.split(',') if a.strip()][:2]
    if not parts:
        return 'Montana criminal defense attorney'
    joined = ' & '.join(parts)
    return f'{joined} attorney'


def _load_csv_rows(csv_path: str) -> list[dict[str, str]]:
    with open(csv_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        return [row for row in reader]


def _row_key(row: sqlite3.Row | tuple) -> tuple[str, str, str]:
    """Return a lowercase (name, firm, county) key for deduplication.

    Works with both sqlite3.Row (dict-style) connections and plain tuple rows.
    """
    if hasattr(row, 'keys'):
        return (
            str(row['name'] or '').strip().lower(),
            str(row['firm'] or '').strip().lower(),
            str(row['county'] or '').strip().lower(),
        )
    # Plain tuple fallback for the SELECT order: id, county, name, firm, ...
    return (
        str(row[2] or '').strip().lower(),
        str(row[3] or '').strip().lower(),
        str(row[1] or '').strip().lower(),
    )


def _existing_rows(conn: sqlite3.Connection) -> dict[tuple[str, str, str], sqlite3.Row]:
    rows = conn.execute(
        '''
        SELECT id, county, name, firm, sponsored, sponsor_tier
        FROM attorney_referrals
        '''
    ).fetchall()
    return {_row_key(r): r for r in rows}


def import_attorneys(
    conn: sqlite3.Connection,
    csv_path: str = DEFAULT_CSV_PATH,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """Upsert attorneys from the CSV. Returns counts dict."""
    raw_rows = _load_csv_rows(csv_path)
    existing = _existing_rows(conn)

    inserted = 0
    updated = 0
    skipped_sponsored = 0
    skipped_blank = 0

    for raw in raw_rows:
        firm = _normalize_name(raw.get('firm_name', ''))
        contact = _normalize_name(raw.get('contact_name', ''))
        county = _normalize_name(raw.get('county', ''))
        website = (raw.get('website') or '').strip()
        practice_areas = (raw.get('practice_areas') or '').strip()
        notes = (raw.get('notes') or '').strip()

        # Data-quality fix: some rows put the real practice areas in the notes
        # column and leave practice_areas as "needs_research".
        if practice_areas.lower() == 'needs_research' and notes:
            if ',' in notes or any(word in notes.lower() for word in ['criminal', 'dui', 'defense', 'family', 'personal injury']):
                practice_areas = notes
                notes = ''
            else:
                practice_areas = ''

        name = contact or firm
        if not name or not county:
            skipped_blank += 1
            continue

        blurb = notes if notes else f'Criminal defense attorney serving {county} County.'
        tagline = _build_tagline(practice_areas)

        key = _row_key({'name': name, 'firm': firm, 'county': county})
        existing_row = existing.get(key)

        if existing_row:
            sponsored = int(
                existing_row['sponsored'] if hasattr(existing_row, 'keys') else existing_row[4]
            )
            sponsor_tier = (
                existing_row['sponsor_tier'] if hasattr(existing_row, 'keys') else existing_row[5]
            )
            if sponsored == 1 or sponsor_tier:
                skipped_sponsored += 1
                continue
            existing_id = (
                existing_row['id'] if hasattr(existing_row, 'keys') else existing_row[0]
            )
            if not dry_run:
                conn.execute(
                    '''
                    UPDATE attorney_referrals
                    SET name = ?,
                        firm = ?,
                        website = ?,
                        practice_areas = ?,
                        blurb = ?,
                        tagline = ?,
                        is_active = 1
                    WHERE id = ?
                    ''',
                    (name, firm, website, practice_areas, blurb, tagline, existing_id),
                )
            updated += 1
            continue

        if not dry_run:
            conn.execute(
                '''
                INSERT INTO attorney_referrals
                  (county, name, firm, phone, email, website, practice_areas, blurb,
                   is_active, sort_order, sponsored, tagline, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 100, 0, ?, datetime('now'))
                ''',
                (county, name, firm, None, None, website, practice_areas, blurb, tagline),
            )
        inserted += 1

    if not dry_run:
        conn.commit()

    return {
        'inserted': inserted,
        'updated': updated,
        'skipped_sponsored': skipped_sponsored,
        'skipped_blank': skipped_blank,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='Import attorney CSV into attorney_referrals')
    parser.add_argument('--csv', default=DEFAULT_CSV_PATH, help='Path to target_list.csv')
    parser.add_argument('--db', default=DEFAULT_DB_PATH, help='Path to SQLite database')
    parser.add_argument('--dry-run', action='store_true', help='Preview changes without writing')
    args = parser.parse_args()

    db_path = args.db
    if not os.path.exists(db_path):
        print(f'Database not found: {db_path}', file=sys.stderr)
        return 1

    if not os.path.exists(args.csv):
        print(f'CSV not found: {args.csv}', file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    try:
        counts = import_attorneys(conn, args.csv, dry_run=args.dry_run)
    finally:
        conn.close()

    mode = 'DRY RUN — no changes made' if args.dry_run else 'Import complete'
    print(f'{mode}:')
    print(f'  Inserted: {counts["inserted"]}')
    print(f'  Updated:  {counts["updated"]}')
    print(f'  Skipped (sponsored): {counts["skipped_sponsored"]}')
    print(f'  Skipped (blank):     {counts["skipped_blank"]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
