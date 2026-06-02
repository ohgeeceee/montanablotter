"""Admin helpers for warrant photos and manual social lookup links."""

from __future__ import annotations

import sqlite3
from typing import Any
from urllib.parse import quote_plus

from services.ingestion.warrants.models import ensure_warrant_schema
from services.persons.warrants_public import _decorate_warrant_row, _single_line


def facebook_people_search_url(person_name: str, city: str = '') -> str:
    """Open Facebook people search in the browser — manual review only, not scraped."""
    parts = [_single_line(person_name, max_len=120)]
    city_term = _single_line(city, max_len=80)
    if city_term:
        parts.append(city_term)
    parts.append('Montana')
    query = ' '.join(part for part in parts if part)
    return f'https://www.facebook.com/search/people/?q={quote_plus(query)}'


def warrant_admin_context(
    conn: sqlite3.Connection,
    *,
    q: str = '',
    county: str = '',
    photo_filter: str = '',
    limit: int = 80,
) -> dict[str, Any]:
    ensure_warrant_schema(conn)
    clauses = ['1=1']
    params: list[Any] = []

    search_term = _single_line(q, max_len=120)
    if search_term:
        like = f'%{search_term}%'
        clauses.append(
            '(person_name LIKE ? OR county LIKE ? OR city LIKE ? OR charges_text LIKE ?)'
        )
        params.extend([like, like, like, like])

    county_term = _single_line(county, max_len=120)
    if county_term:
        clauses.append('county = ?')
        params.append(county_term)

    normalized_photo = _single_line(photo_filter, max_len=32).lower()
    if normalized_photo == 'with_photo':
        clauses.append("(COALESCE(photo_url, '') != '' OR COALESCE(mugshot_url, '') != '')")
    elif normalized_photo == 'missing_photo':
        clauses.append("(COALESCE(photo_url, '') = '' AND COALESCE(mugshot_url, '') = '')")

    where_sql = ' AND '.join(clauses)
    rows = conn.execute(
        f"""
        SELECT *
        FROM warrants
        WHERE {where_sql}
        ORDER BY county ASC, person_name ASC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()

    stats = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active,
            SUM(CASE WHEN COALESCE(mugshot_url, '') != '' THEN 1 ELSE 0 END) AS official_photos,
            SUM(CASE WHEN COALESCE(photo_url, '') != '' THEN 1 ELSE 0 END) AS staff_photos,
            SUM(
                CASE
                    WHEN COALESCE(photo_url, '') != '' OR COALESCE(mugshot_url, '') != ''
                    THEN 1 ELSE 0
                END
            ) AS any_photo
        FROM warrants
        """
    ).fetchone()

    counties = [
        str(row['county'])
        for row in conn.execute(
            """
            SELECT DISTINCT county
            FROM warrants
            WHERE COALESCE(county, '') != ''
            ORDER BY county ASC
            """
        ).fetchall()
    ]

    return {
        'rows': [_decorate_warrant_row(row) for row in rows],
        'q': search_term,
        'county_filter': county_term,
        'photo_filter': normalized_photo,
        'counties': counties,
        'stats': {
            'total': int(stats['total'] or 0),
            'active': int(stats['active'] or 0),
            'official_photos': int(stats['official_photos'] or 0),
            'staff_photos': int(stats['staff_photos'] or 0),
            'any_photo': int(stats['any_photo'] or 0),
        },
    }


def get_warrant_by_id(conn: sqlite3.Connection, warrant_id: int) -> dict[str, Any] | None:
    ensure_warrant_schema(conn)
    row = conn.execute('SELECT * FROM warrants WHERE id = ?', (warrant_id,)).fetchone()
    if not row:
        return None
    record = _decorate_warrant_row(row)
    record['facebook_search_url'] = facebook_people_search_url(
        record['person_name'],
        record['city'],
    )
    return record


def update_warrant_photo_fields(
    conn: sqlite3.Connection,
    warrant_id: int,
    *,
    photo_url: str = '',
    social_profile_url: str = '',
    run_ts: str,
) -> dict[str, Any] | None:
    ensure_warrant_schema(conn)
    row = conn.execute('SELECT id FROM warrants WHERE id = ?', (warrant_id,)).fetchone()
    if not row:
        return None
    conn.execute(
        """
        UPDATE warrants
           SET photo_url = ?,
               social_profile_url = ?,
               updated_at = ?
         WHERE id = ?
        """,
        (
            _single_line(photo_url, max_len=500),
            _single_line(social_profile_url, max_len=500),
            run_ts,
            warrant_id,
        ),
    )
    conn.commit()
    return get_warrant_by_id(conn, warrant_id)


def clear_warrant_staff_photo(conn: sqlite3.Connection, warrant_id: int, *, run_ts: str) -> bool:
    ensure_warrant_schema(conn)
    cur = conn.execute(
        """
        UPDATE warrants
           SET photo_url = '', social_profile_url = '', updated_at = ?
         WHERE id = ?
        """,
        (run_ts, warrant_id),
    )
    conn.commit()
    return cur.rowcount > 0


def warrant_photo_coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    ensure_warrant_schema(conn)
    rows = conn.execute(
        """
        SELECT
            county,
            COUNT(*) AS total,
            SUM(
                CASE
                    WHEN COALESCE(photo_url, '') != '' OR COALESCE(mugshot_url, '') != ''
                    THEN 1 ELSE 0
                END
            ) AS with_photo
        FROM warrants
        WHERE status = 'active'
        GROUP BY county
        ORDER BY county ASC
        """
    ).fetchall()
    by_county = [
        {
            'county': row['county'],
            'active': int(row['total'] or 0),
            'with_photo': int(row['with_photo'] or 0),
        }
        for row in rows
    ]
    return {'by_county': by_county}
