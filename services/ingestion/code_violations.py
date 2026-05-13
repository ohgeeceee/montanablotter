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
from services.ingestion.property_addresses import (
    ensure_property_address,
    parse_address_parts,
    slugify_address,
)

DB_PATH = os.getenv('MB_DB_PATH', '/root/montanablotter/blotter.db')


def _slugify_address(street: str, city: str, state: str = 'MT', zip_code: str = '') -> str:
    return slugify_address(street, city, state, zip_code)


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


def normalize_address_record(raw_address: str, *, fallback_city: str) -> dict[str, str]:
    """Normalize an address into a consistent structured object for storage/linking."""
    return parse_address_parts(raw_address, fallback_city=fallback_city)


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


def mark_source_failure(conn: sqlite3.Connection, source_id: int, error_message: str) -> None:
    conn.execute(
        '''
        UPDATE code_violation_sources
        SET latest_error = ?, updated_at = datetime("now")
        WHERE id = ?
        ''',
        (error_message[:500], source_id),
    )
    conn.commit()


def _ensure_property_address(conn: sqlite3.Connection, street: str, city: str, state: str = 'MT', zip_code: str = '', county: str = '') -> int:
    property_address_id = ensure_property_address(
        conn,
        street=street,
        city=city,
        state=state,
        zip_code=zip_code,
        county=county,
    )
    conn.commit()
    return property_address_id


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
            parts = normalize_address_record(raw_address, fallback_city=city)
            property_address_id = _ensure_property_address(
                conn,
                parts['street'],
                parts['city'],
                parts['state'],
                parts['zip_code'],
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
        '''
        UPDATE code_violation_sources
        SET last_success_at = datetime("now"),
            latest_error = '',
            updated_at = datetime("now")
        WHERE id = ?
        ''',
        (source_id,),
    )
    conn.commit()
    return {'inserted': inserted, 'updated': updated, 'source_id': source_id}


def _parse_pdf(file_path: str) -> list[dict[str, Any]]:
    """
    Best-effort parser for exported PDF violation lists.
    Expected line format examples:
      123 Main St, Billings, MT 59101 | Abandoned Property | Open | 2026-05-01
      123 Main St, Billings, MT 59101, Abandoned Property, Open, 2026-05-01
    """
    try:
        from pypdf import PdfReader
    except Exception as exc:  # pragma: no cover - env dependent
        raise RuntimeError('pypdf required for PDF parsing: pip install pypdf') from exc

    reader = PdfReader(file_path)
    rows: list[dict[str, Any]] = []
    split_pattern = re.compile(r'\s*[|]\s*|\s{2,}')
    for page in reader.pages:
        text = page.extract_text() or ''
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or len(line) < 12:
                continue
            lower = line.lower()
            if any(token in lower for token in ('address', 'violation', 'issued', 'resolved', 'status')):
                continue
            parts = [part.strip() for part in split_pattern.split(line) if part.strip()]
            if len(parts) < 3:
                comma_parts = [part.strip() for part in line.split(',') if part.strip()]
                if len(comma_parts) >= 4:
                    parts = [', '.join(comma_parts[:-3]), comma_parts[-3], comma_parts[-2], comma_parts[-1]]
            if len(parts) < 3:
                continue
            address = parts[0]
            violation_type = parts[1]
            status = parts[2] if len(parts) >= 3 else 'open'
            issued = parts[3] if len(parts) >= 4 else ''
            resolved = parts[4] if len(parts) >= 5 else ''
            rows.append(
                {
                    'address': address,
                    'violation_type': violation_type,
                    'status': status,
                    'date_issued': issued,
                    'date_resolved': resolved,
                    'description': line,
                }
            )
    return rows


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
