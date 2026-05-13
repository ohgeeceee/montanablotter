from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from services.ingestion.property_addresses import ensure_property_address, parse_address_parts


def classify_civil_filing(
    *,
    case_type_code: str = '',
    caption: str = '',
    case_type_label: str = '',
) -> str:
    code = (case_type_code or '').strip().upper()
    haystack = ' '.join([(caption or ''), (case_type_label or '')]).lower()
    if code == 'UD':
        return 'eviction'
    if code == 'DV':
        return 'restraining_order'
    if 'lien' in haystack:
        return 'lien'
    if 'protection order' in haystack or 'restraining order' in haystack:
        return 'restraining_order'
    if code == 'CC':
        return 'civil_judgment'
    if 'judgment' in haystack:
        return 'civil_judgment'
    return 'other'


def extract_parties_from_caption(caption: str) -> tuple[str | None, str | None]:
    value = ' '.join((caption or '').strip().split())
    if not value:
        return None, None
    separators = (' v. ', ' vs. ', ' vs ', ' v ')
    lower = value.lower()
    for sep in separators:
        idx = lower.find(sep.strip())
        if idx != -1:
            left = value[:idx].strip(' ,;')
            right = value[idx + len(sep.strip()):].strip(' ,;')
            return left or None, right or None
    return None, None


def civil_filing_hash(source_id: int, case_number: str, filing_date: str, filing_class: str) -> str:
    payload = f'{source_id}|{case_number}|{filing_date}|{filing_class}'
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def ensure_civil_filing_source(
    conn: sqlite3.Connection,
    *,
    source_key: str,
    display_name: str,
    adapter_type: str,
    county: str,
    source_url: str = '',
) -> int:
    row = conn.execute(
        'SELECT id FROM civil_filing_sources WHERE source_key = ?',
        (source_key,),
    ).fetchone()
    if row:
        conn.execute(
            '''
            UPDATE civil_filing_sources
            SET display_name = ?,
                adapter_type = ?,
                county = ?,
                source_url = ?,
                updated_at = datetime('now')
            WHERE id = ?
            ''',
            (display_name, adapter_type, county, source_url or None, row['id']),
        )
        return row['id']

    cur = conn.execute(
        '''
        INSERT INTO civil_filing_sources (source_key, display_name, adapter_type, county, source_url)
        VALUES (?, ?, ?, ?, ?)
        ''',
        (source_key, display_name, adapter_type, county, source_url or None),
    )
    return cur.lastrowid


def ingest_civil_filings(
    conn: sqlite3.Connection,
    *,
    source_key: str,
    display_name: str,
    adapter_type: str,
    county: str,
    records: list[dict[str, Any]],
    source_url: str = '',
) -> dict[str, int]:
    source_id = ensure_civil_filing_source(
        conn,
        source_key=source_key,
        display_name=display_name,
        adapter_type=adapter_type,
        county=county,
        source_url=source_url,
    )
    inserted = 0
    updated = 0

    for rec in records:
        case_number = (rec.get('case_number') or '').strip()
        if not case_number:
            # Ignore scaffold/invalid rows that do not identify a case.
            continue

        filing_class = classify_civil_filing(
            case_type_code=rec.get('case_type_code', ''),
            caption=rec.get('caption', ''),
            case_type_label=rec.get('case_type_label', ''),
        )
        raw_address = (rec.get('address') or rec.get('raw_address') or '').strip()
        plaintiff_name = (rec.get('plaintiff_name') or '').strip()
        defendant_name = (rec.get('defendant_name') or '').strip()
        if not plaintiff_name or not defendant_name:
            inferred_plaintiff, inferred_defendant = extract_parties_from_caption(rec.get('caption', ''))
            plaintiff_name = plaintiff_name or (inferred_plaintiff or '')
            defendant_name = defendant_name or (inferred_defendant or '')
        property_address_id = None
        if raw_address:
            parts = parse_address_parts(raw_address, fallback_city=(rec.get('city') or '').strip())
            property_address_id = ensure_property_address(
                conn,
                street=parts['street'],
                city=parts['city'],
                state=parts['state'],
                zip_code=parts['zip_code'],
                county=(rec.get('county') or county or '').strip(),
            )

        filing_date = (rec.get('filing_date') or '').strip()
        hash_id = civil_filing_hash(source_id, case_number, filing_date, filing_class)
        existing = conn.execute(
            'SELECT id FROM civil_filings WHERE hash_id = ?',
            (hash_id,),
        ).fetchone()

        if existing:
            conn.execute(
                '''
                UPDATE civil_filings
                SET property_address_id = ?,
                    county = ?,
                    city = ?,
                    case_status = ?,
                    raw_address = ?,
                    source_url = ?,
                    raw_json = ?,
                    last_seen_at = datetime('now'),
                    updated_at = datetime('now')
                WHERE id = ?
                ''',
                (
                    property_address_id,
                    (rec.get('county') or county or '').strip(),
                    (rec.get('city') or '').strip() or None,
                    (rec.get('case_status') or '').strip() or None,
                    raw_address,
                    (rec.get('source_url') or source_url or '').strip() or None,
                    json.dumps(rec),
                    existing['id'],
                ),
            )
            updated += 1
            continue

        conn.execute(
            '''
            INSERT INTO civil_filings (
                source_id, property_address_id, county, city, case_number, case_type_code, case_type_label,
                filing_class, caption, plaintiff_name, defendant_name, raw_address, filing_date, case_status,
                source_record_id, source_url, raw_json, hash_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                source_id,
                property_address_id,
                (rec.get('county') or county or '').strip(),
                (rec.get('city') or '').strip() or None,
                case_number,
                (rec.get('case_type_code') or '').strip() or None,
                (rec.get('case_type_label') or '').strip() or None,
                filing_class,
                (rec.get('caption') or '').strip() or None,
                plaintiff_name or None,
                defendant_name or None,
                raw_address,
                filing_date or None,
                (rec.get('case_status') or '').strip() or None,
                (rec.get('source_record_id') or '').strip() or None,
                (rec.get('source_url') or source_url or '').strip() or None,
                json.dumps(rec),
                hash_id,
            ),
        )
        inserted += 1

    conn.execute(
        '''
        UPDATE civil_filing_sources
        SET last_success_at = datetime('now'),
            last_error = '',
            last_run_count = ?,
            updated_at = datetime('now')
        WHERE id = ?
        ''',
        (len(records), source_id),
    )
    conn.commit()
    return {'inserted': inserted, 'updated': updated, 'source_id': source_id}
