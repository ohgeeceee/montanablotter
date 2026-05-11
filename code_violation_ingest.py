"""
Code Enforcement Violation Ingestion Worker

Reads city-exported files (PDF, Excel, CSV, JSON) and writes to
code_violations / property_addresses tables.

Usage:
    python code_violation_ingest.py --source billings --file /path/to/export.pdf
    python code_violation_ingest.py --source missoula --file /path/to/export.xlsx
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from typing import Any

from db import connect_db

DB_PATH = os.getenv('MB_DB_PATH', '/root/montanablotter/blotter.db')


def _slugify_address(street: str, city: str, state: str = 'MT', zip_code: str = '') -> str:
    parts = [street, city, state, zip_code]
    raw = ' '.join(p for p in parts if p)
    slug = raw.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_-]+", "-", slug)
    return slug.strip("-") or 'unknown'


def _hash_record(source_id: int, raw_address: str, violation_type: str, date_issued: str) -> str:
    payload = f"{source_id}|{raw_address}|{violation_type}|{date_issued}"
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _normalize_date(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(value, fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return value[:10] if len(value) >= 10 else value


def _ensure_source(conn: sqlite3.Connection, source_key: str, display_name: str, city: str) -> int:
    row = conn.execute(
        'SELECT id FROM code_violation_sources WHERE source_key = ?',
        (source_key,),
    ).fetchone()
    if row:
        return row['id']
    cur = conn.execute(
        'INSERT INTO code_violation_sources (source_key, display_name, city) VALUES (?, ?, ?)',
        (source_key, display_name, city),
    )
    conn.commit()
    return cur.lastrowid


def _ensure_property_address(conn: sqlite3.Connection, street: str, city: str, state: str = 'MT', zip_code: str = '', county: str = '') -> int:
    slug = _slugify_address(street, city, state, zip_code)
    row = conn.execute(
        'SELECT id FROM property_addresses WHERE address_slug = ?',
        (slug,),
    ).fetchone()
    if row:
        conn.execute(
            'UPDATE property_addresses SET last_seen_at = datetime("now") WHERE id = ?',
            (row['id'],),
        )
        conn.commit()
        return row['id']
    cur = conn.execute(
        '''
        INSERT INTO property_addresses (address_slug, street, city, state, zip, county)
        VALUES (?, ?, ?, ?, ?, ?)
        ''',
        (slug, street, city, state, zip_code, county),
    )
    conn.commit()
    return cur.lastrowid


def ingest_records(
    conn: sqlite3.Connection,
    *,
    source_key: str,
    display_name: str,
    city: str,
    records: list[dict[str, Any]],
) -> dict[str, int]:
    """Insert or update violation records. Returns counts."""
    source_id = _ensure_source(conn, source_key, display_name, city)
    inserted = updated = 0

    for rec in records:
        raw_address = (rec.get('address') or rec.get('property_address') or '').strip()
        violation_type = (rec.get('violation_type') or rec.get('type') or 'Unknown').strip()
        status = (rec.get('status') or 'open').strip().lower()
        date_issued = _normalize_date(rec.get('date_issued') or rec.get('issued_date'))
        date_resolved = _normalize_date(rec.get('date_resolved') or rec.get('resolved_date'))
        owner_name = (rec.get('owner_name') or rec.get('owner') or '').strip() or None
        description = (rec.get('description') or '').strip() or None
        fine_amount = rec.get('fine_amount')
        source_record_id = (rec.get('source_record_id') or '').strip() or None
        source_url = (rec.get('source_url') or '').strip() or None

        # Normalize address into property_addresses
        property_address_id = None
        if raw_address:
            # Naive split: assume "123 Main St, Missoula, MT 59801"
            street = raw_address
            zip_code = ''
            m = re.search(r'\b(\d{5}(?:-\d{4})?)\s*$', raw_address)
            if m:
                zip_code = m.group(1)
                street = raw_address[:m.start()].strip().rstrip(',').strip()
            property_address_id = _ensure_property_address(
                conn, street, city, 'MT', zip_code
            )

        hash_id = _hash_record(source_id, raw_address, violation_type, date_issued or '')

        existing = conn.execute(
            'SELECT id FROM code_violations WHERE hash_id = ?',
            (hash_id,),
        ).fetchone()

        if existing:
            conn.execute(
                '''
                UPDATE code_violations
                SET status = ?, date_resolved = ?, owner_name = ?,
                    description = ?, fine_amount = ?, source_url = ?,
                    last_seen_at = datetime('now'), updated_at = datetime('now')
                WHERE id = ?
                ''',
                (status, date_resolved, owner_name, description, fine_amount, source_url, existing['id']),
            )
            updated += 1
        else:
            conn.execute(
                '''
                INSERT INTO code_violations
                (source_id, property_address_id, raw_address, violation_type, status,
                 date_issued, date_resolved, owner_name, description, fine_amount,
                 source_record_id, source_url, raw_json, hash_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    source_id, property_address_id, raw_address, violation_type, status,
                    date_issued, date_resolved, owner_name, description, fine_amount,
                    source_record_id, source_url, json.dumps(rec), hash_id,
                ),
            )
            inserted += 1

    conn.commit()
    conn.execute(
        'UPDATE code_violation_sources SET last_success_at = datetime("now"), updated_at = datetime("now") WHERE id = ?',
        (source_id,),
    )
    conn.commit()
    return {'inserted': inserted, 'updated': updated, 'source_id': source_id}


def _parse_pdf(file_path: str) -> list[dict[str, Any]]:
    """Placeholder: integrate pdf_parser.py or Kimi extraction here."""
    raise NotImplementedError('PDF parsing not yet implemented — use --json or manual preprocessing.')


def _parse_excel(file_path: str) -> list[dict[str, Any]]:
    try:
        import pandas as pd
    except ImportError:
        raise RuntimeError('pandas required for Excel parsing: pip install pandas openpyxl')
    df = pd.read_excel(file_path)
    return df.fillna('').to_dict(orient='records')


def _parse_csv(file_path: str) -> list[dict[str, Any]]:
    import csv
    with open(file_path, newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def _parse_json(file_path: str) -> list[dict[str, Any]]:
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if isinstance(data, dict) and 'records' in data:
        return data['records']
    return data if isinstance(data, list) else [data]


def main():
    parser = argparse.ArgumentParser(description='Ingest code enforcement violations')
    parser.add_argument('--source', required=True, help='Source key (e.g. billings)')
    parser.add_argument('--display-name', default='', help='Human-readable source name')
    parser.add_argument('--city', required=True, help='City name')
    parser.add_argument('--file', required=True, help='Path to export file')
    parser.add_argument('--format', choices=['pdf', 'excel', 'csv', 'json'], help='File format (auto-detected from extension if omitted)')
    args = parser.parse_args()

    ext = (args.format or os.path.splitext(args.file)[1].lstrip('.').lower())
    if ext in ('xlsx', 'xls'):
        ext = 'excel'

    parsers = {
        'pdf': _parse_pdf,
        'excel': _parse_excel,
        'csv': _parse_csv,
        'json': _parse_json,
    }
    parse_fn = parsers.get(ext)
    if not parse_fn:
        print(f'Unsupported format: {ext}')
        sys.exit(1)

    records = parse_fn(args.file)
    print(f'Parsed {len(records)} records from {args.file}')

    conn = connect_db()
    try:
        result = ingest_records(
            conn,
            source_key=args.source,
            display_name=args.display_name or args.city,
            city=args.city,
            records=records,
        )
        print(f"Inserted: {result['inserted']}, Updated: {result['updated']}")
    finally:
        conn.close()


if __name__ == '__main__':
    main()
