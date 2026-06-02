"""
Disposition lookup service — given a person name and/or case number, return all
matching court cases (with full criminal outcomes) and any related jail bookings.

Powers the paid /api/v1/disposition/lookup endpoint.

Matching strategy (best to worst):
1. case_number exact match (1.0 confidence)
2. slug exact match on normalized name (1.0)
3. last_name + first_name indexed match (0.9 if first is also a match, 0.8 if last-only)
4. last_name only (0.7) — only used if no other match exists

For each court case hit, we cross-link to jail_bookings by last+first name LIKE
(jail_bookings has no last/first columns, so we filter on the raw person_name
column, scoped to the relevant county when supplied).

This module is read-only — pure SQL queries, no side effects.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Optional

from services.persons.profiles import _parse_last_first

log = logging.getLogger(__name__)

DEFAULT_LIMIT = 25
MAX_LIMIT = 100


def _slugify(name: str) -> str:
    """Mirror court_cases.defendant_slug formula: lowercase, space→dash, strip punct."""
    if not name:
        return ''
    n = name.strip().lower()
    n = n.replace(' ', '-')
    for ch in '.\'",':
        n = n.replace(ch, '')
    return n


def _normalize_input(name: str) -> tuple[str, str, str, str]:
    """
    Parse a free-form name into (slug, last, first, display_last_first).
    Handles 'First Last', 'First Middle Last', and 'Last, First [Middle]'.
    """
    raw = (name or '').strip()
    if not raw:
        return '', '', '', ''
    last, first = _parse_last_first(raw)
    slug = _slugify(raw)
    display = f"{last.capitalize()}, {first.capitalize()}" if first else last.capitalize()
    return slug, last, first, display


def _row_to_case(row: sqlite3.Row) -> dict:
    """Project a court_cases + courts row into the API response shape."""
    return {
        'id': row['id'],
        'case_number': row['case_number'],
        'court_name': row['court_name'],
        'court_county': row['county'],
        'case_type': row['case_type'],
        'status': row['status'],
        'filed_date': row['filed_date'],
        'charges_text': row['charges_text'],
        'plea': row['plea'],
        'disposition': row['disposition'],
        'sentence_text': row['sentence_text'],
        'sentence_date': row['sentence_date'],
        'sentencing_judge': row['sentencing_judge'],
        'original_court': row['original_court'],
        'original_case_number': row['original_case_number'],
        'outcome_scraped_at': row['outcome_scraped_at'],
        'source_url': row['source_url'],
    }


def _row_to_booking(row: sqlite3.Row) -> dict:
    """Project a jail_bookings row into the API response shape."""
    return {
        'id': row['id'],
        'person_name': row['person_name'],
        'name_slug': row['name_slug'],
        'age': row['age'],
        'booking_number': row['booking_number'],
        'booking_at': row['booking_at'],
        'release_at': row['release_at'],
        'charges_summary': row['charges_summary'],
        'arresting_agency': row['arresting_agency'],
        'county_name': row['county_name'],
        'facility_name': row['facility_name'],
        'booking_status': row['booking_status'],
        'is_current': row['is_current'],
        'source_url': row['source_url'],
    }


def _find_related_bookings(
    conn: sqlite3.Connection,
    *,
    last: str,
    first: str,
    county: Optional[str],
    limit: int = 10,
) -> list[dict]:
    """Find jail_bookings that match a (last, first) name pair. Scoped to county when provided."""
    if not last:
        return []
    where_parts = ["LOWER(jb.person_name) LIKE ?"]
    params: list = [f'%{last}%']
    if first:
        where_parts.append("LOWER(jb.person_name) LIKE ?")
        params.append(f'%{first}%')
    if county:
        where_parts.append('jb.county_name = ?')
        params.append(county)
    where_sql = 'WHERE ' + ' AND '.join(where_parts)
    rows = conn.execute(
        f'''
        SELECT id, person_name, name_slug, age, booking_number, booking_at,
               release_at, charges_summary, arresting_agency, county_name,
               facility_name, booking_status, is_current, source_url
        FROM jail_bookings jb
        {where_sql}
        ORDER BY booking_at DESC
        LIMIT ?
        ''',
        params + [limit],
    ).fetchall()
    return [_row_to_booking(r) for r in rows]


def lookup_disposition(
    conn: sqlite3.Connection,
    *,
    name: Optional[str] = None,
    county: Optional[str] = None,
    case_number: Optional[str] = None,
    include_bookings: bool = True,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """
    Run a disposition lookup. Returns a structured dict with the query echo
    and a list of matches. Each match represents one person and includes their
    court cases + related jail bookings.

    Args:
        conn: SQLite connection.
        name: Person name, free form ('Brian Laird', 'Laird, Brian', 'Laird, Brian Q').
        county: Optional county filter — narrows court_cases (via courts.county or
            original_court) and jail_bookings (via county_name).
        case_number: Optional case number exact match (skips name).
        include_bookings: If False, skip the jail_bookings cross-link (faster).
        limit: Max court_cases per person. Capped at MAX_LIMIT.

    Returns:
        dict with: query, match_count, matches[], data_as_of, warnings[]
    """
    limit = max(1, min(MAX_LIMIT, limit))
    name = (name or '').strip() or None
    county = (county or '').strip() or None
    case_number = (case_number or '').strip() or None

    if not name and not case_number:
        return {
            'query': {'name': name, 'county': county, 'case_number': case_number},
            'match_count': 0,
            'matches': [],
            'warnings': ['Provide at least a name or case_number.'],
        }

    warnings: list[str] = []
    matches: list[dict] = []

    # ----------------- Strategy 1: case_number exact match -----------------
    if case_number:
        where = ['cc.case_number = ?']
        params: list = [case_number]
        if county:
            where.append('(c.county = ? OR cc.original_court LIKE ?)')
            params.extend([county, f'%{county}%'])
        rows = conn.execute(
            f'''
            SELECT cc.*, c.name AS court_name, c.county
            FROM court_cases cc
            JOIN courts c ON c.id = cc.court_id
            WHERE {' AND '.join(where)}
            ORDER BY cc.filed_date DESC NULLS LAST
            ''',
            params,
        ).fetchall()
        if rows:
            for row in rows:
                _, last, first, display = _normalize_input(row['defendant_name'] or '')
                case = _row_to_case(row)
                if include_bookings and last:
                    case['related_jail_bookings'] = _find_related_bookings(
                        conn, last=last, first=first, county=county
                    )
                else:
                    case['related_jail_bookings'] = []
                matches.append({
                    'match_type': 'case_number',
                    'confidence': 1.0,
                    'person': {
                        'name': row['defendant_name'],
                        'name_slug': row['defendant_slug'],
                        'county': row['county'],
                        'display_name': display or row['defendant_name'],
                    },
                    'court_cases': [case],
                })
        else:
            warnings.append(f'No court case found for case_number={case_number!r}.')

    # ----------------- Strategy 2: name match -----------------
    if name and not matches:
        slug, last, first, display = _normalize_input(name)
        if not last:
            return {
                'query': {'name': name, 'county': county, 'case_number': case_number},
                'match_count': 0,
                'matches': [],
                'warnings': ['Could not parse a last name from the input.'],
            }

        where_parts: list[str] = ['cc.is_criminal = 1']
        params = []
        if county:
            where_parts.append('(c.county = ? OR cc.original_court LIKE ?)')
            params.extend([county, f'%{county}%'])

        # Strategy 2a: slug exact match
        slug_where = where_parts + ['cc.defendant_slug = ?']
        slug_params = params + [slug] if slug else None
        rows: list[sqlite3.Row] = []
        if slug_params:
            rows = conn.execute(
                f'''
                SELECT cc.*, c.name AS court_name, c.county
                FROM court_cases cc
                JOIN courts c ON c.id = cc.court_id
                WHERE {' AND '.join(slug_where)}
                ORDER BY cc.filed_date DESC NULLS LAST
                LIMIT ?
                ''',
                slug_params + [limit],
            ).fetchall()

        # Strategy 2b: last + first match (fallback if slug miss)
        if not rows:
            last_first_where = where_parts + ['cc.defendant_last = ?']
            last_first_params = params + [last]
            if first:
                last_first_where.append("cc.defendant_first LIKE ? || '%'")
                last_first_params.append(first)
            rows = conn.execute(
                f'''
                SELECT cc.*, c.name AS court_name, c.county
                FROM court_cases cc
                JOIN courts c ON c.id = cc.court_id
                WHERE {' AND '.join(last_first_where)}
                ORDER BY cc.filed_date DESC NULLS LAST
                LIMIT ?
                ''',
                last_first_params + [limit],
            ).fetchall()

        if not rows and not first:
            # Strategy 2c: last-only fallback (no first supplied).
            # Reuse the same county filter as strategies 2a/2b so a county-scoped
            # query doesn't leak matches from other counties.
            last_only_where = where_parts + ['cc.defendant_last = ?']
            last_only_params = params + [last]
            rows = conn.execute(
                f'''
                SELECT cc.*, c.name AS court_name, c.county
                FROM court_cases cc
                JOIN courts c ON c.id = cc.court_id
                WHERE {' AND '.join(last_only_where)}
                ORDER BY cc.filed_date DESC NULLS LAST
                LIMIT ?
                ''',
                last_only_params + [limit],
            ).fetchall()

        if rows:
            # Group rows by person (slug). One person may have multiple cases.
            by_person: dict[str, dict] = {}
            for row in rows:
                person_slug = row['defendant_slug'] or slug
                if person_slug not in by_person:
                    person_display = display or _normalize_input(row['defendant_name'] or '')[3]
                    by_person[person_slug] = {
                        'match_type': None,  # set below
                        'confidence': 0.0,  # set below
                        'person': {
                            'name': row['defendant_name'],
                            'name_slug': row['defendant_slug'],
                            'county': row['county'],
                            'display_name': person_display or row['defendant_name'],
                        },
                        'court_cases': [],
                    }
                case = _row_to_case(row)
                if include_bookings:
                    case['related_jail_bookings'] = _find_related_bookings(
                        conn, last=last, first=first, county=county
                    )
                else:
                    case['related_jail_bookings'] = []
                by_person[person_slug]['court_cases'].append(case)

            # Determine match_type + confidence for each person
            for person_slug, match in by_person.items():
                first_matched = any(
                    row['defendant_first'] and first and row['defendant_first'].startswith(first)
                    for row in rows
                    if (row['defendant_slug'] or slug) == person_slug
                )
                if slug and person_slug == slug:
                    match['match_type'] = 'exact_slug'
                    match['confidence'] = 1.0
                elif first_matched:
                    match['match_type'] = 'last_first'
                    match['confidence'] = 0.9
                else:
                    match['match_type'] = 'last_only'
                    match['confidence'] = 0.7
            matches.extend(by_person.values())
        else:
            warnings.append(
                f'No court cases found for name={name!r}' + (f' in county={county!r}' if county else '') + '.'
            )

    # Sort by confidence DESC, then by person name
    matches.sort(key=lambda m: (-m['confidence'], m['person'].get('name') or ''))

    # Data-as-of: latest outcome_scraped_at across all hits
    data_as_of = None
    for m in matches:
        for c in m['court_cases']:
            ts = c.get('outcome_scraped_at')
            if ts and (data_as_of is None or ts > data_as_of):
                data_as_of = ts

    return {
        'query': {'name': name, 'county': county, 'case_number': case_number},
        'match_count': len(matches),
        'matches': matches,
        'data_as_of': data_as_of,
        'warnings': warnings,
    }
