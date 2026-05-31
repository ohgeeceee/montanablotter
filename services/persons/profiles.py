"""
Person profile pages — aggregate jail bookings, court cases, and sex offender
status by name slug for public /person/<slug> pages.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any


def _pretty_name(person_name: str) -> str:
    """'HAACKE, RICHARD JAMES' or 'richard james haacke' → 'Richard James Haacke'"""
    raw = (person_name or '').strip()
    if ',' in raw:
        last, _, rest = raw.partition(',')
        parts = rest.strip().split() + [last.strip()]
    else:
        parts = raw.split()
    return ' '.join(p.capitalize() for p in parts if p)


def _parse_last_first(person_name: str) -> tuple[str, str]:
    """Return (last_name, first_name) from 'Last, First [Middle]' format."""
    raw = (person_name or '').strip()
    if ',' in raw:
        last, _, rest = raw.partition(',')
        first = rest.strip().split()[0] if rest.strip() else ''
        return last.strip().lower(), first.lower()
    parts = raw.split()
    if len(parts) >= 2:
        return parts[-1].lower(), parts[0].lower()
    return raw.lower(), ''


def person_listing_context(
    conn: sqlite3.Connection,
    *,
    q: str = '',
    county: str = '',
    page: int = 1,
    per_page: int = 40,
) -> dict[str, Any]:
    query = (q or '').strip()
    selected_county = (county or '').strip()

    sql = '''
        SELECT
            name_slug,
            person_name,
            county_name,
            COUNT(*) AS booking_count,
            MAX(booking_at) AS last_booking_at
        FROM jail_bookings
        WHERE name_slug IS NOT NULL AND name_slug != ''
    '''
    params: list = []
    if query:
        sql += ' AND (lower(person_name) LIKE ? OR name_slug LIKE ?)'
        params.extend([f'%{query.lower()}%', f'%{query.lower()}%'])
    if selected_county:
        sql += ' AND county_slug = ?'
        params.append(selected_county.lower().replace(' ', '-'))
    sql += ' GROUP BY name_slug ORDER BY last_booking_at DESC NULLS LAST'

    all_rows = conn.execute(sql, params).fetchall()
    total = len(all_rows)
    offset = (max(page, 1) - 1) * per_page
    people = [
        {**dict(r), 'display_name': _pretty_name(r['person_name'])}
        for r in all_rows[offset: offset + per_page]
    ]

    counties = [
        r['county_name'] for r in conn.execute(
            'SELECT DISTINCT county_name FROM jail_bookings WHERE county_name IS NOT NULL ORDER BY county_name'
        ).fetchall()
    ]
    total_bookings = conn.execute('SELECT COUNT(*) AS n FROM jail_bookings').fetchone()['n']

    return {
        'people': people,
        'total': total,
        'total_bookings': total_bookings,
        'page': page,
        'per_page': per_page,
        'total_pages': max(1, (total + per_page - 1) // per_page),
        'q': query,
        'selected_county': selected_county,
        'counties': counties,
    }


def person_profile_context(
    conn: sqlite3.Connection,
    name_slug: str,
) -> dict[str, Any] | None:
    slug = (name_slug or '').strip().lower()
    if not slug:
        return None

    bookings = [dict(r) for r in conn.execute(
        'SELECT * FROM jail_bookings WHERE name_slug = ? ORDER BY booking_at DESC',
        (slug,),
    ).fetchall()]

    if not bookings:
        return None

    display_name = _pretty_name(bookings[0]['person_name'])
    last_name, first_name = _parse_last_first(bookings[0]['person_name'])

    court_cases: list[dict] = []
    if last_name:
        cc_rows = conn.execute(
            '''SELECT cc.*, c.name AS court_name, c.county
               FROM court_cases cc JOIN courts c ON c.id = cc.court_id
               WHERE cc.is_criminal = 1
                 AND lower(cc.defendant_name) LIKE ?
                 AND lower(cc.defendant_name) LIKE ?
               ORDER BY cc.filed_date DESC NULLS LAST''',
            (f'%{last_name}%', f'%{first_name}%' if first_name else '%'),
        ).fetchall()
        court_cases = [dict(r) for r in cc_rows]

    sex_offender: dict | None = None
    if last_name:
        so_row = conn.execute(
            '''SELECT * FROM sex_offenders
               WHERE lower(full_name) LIKE ? AND lower(full_name) LIKE ?
               LIMIT 1''',
            (f'%{last_name}%', f'%{first_name}%' if first_name else '%'),
        ).fetchone()
        if so_row:
            sex_offender = dict(so_row)

    counties = list({b['county_name'] for b in bookings if b.get('county_name')})

    return {
        'name_slug': slug,
        'display_name': display_name,
        'bookings': bookings,
        'court_cases': court_cases,
        'sex_offender': sex_offender,
        'booking_count': len(bookings),
        'counties': counties,
        'last_booking': bookings[0],
    }
