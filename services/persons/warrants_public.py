"""Public warrant listing helpers for /wanted pages and poster widgets."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from typing import Any

from services.ingestion.warrants.models import ensure_warrant_schema

STATUS_ACTIVE = 'active'
STATUS_RESOLVED = 'resolved'
VALID_STATUSES = {STATUS_ACTIVE, STATUS_RESOLVED}


def _single_line(value: Any, *, max_len: int = 240) -> str:
    return ' '.join(str(value or '').strip().split())[:max_len]


def _display_datetime(value: Any) -> str:
    raw = _single_line(value, max_len=40)
    if not raw:
        return 'Not provided'
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            parsed = datetime.strptime(raw, fmt)
            if fmt == '%Y-%m-%d':
                return parsed.strftime('%b %d, %Y')
            return parsed.strftime('%b %d, %Y %I:%M %p').replace(' 0', ' ')
        except ValueError:
            continue
    return raw


def warrant_slug(source_record_id: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', str(source_record_id or '').lower()).strip('-')
    return slug[:120] or 'warrant'


def _row_value(row: sqlite3.Row | dict[str, Any], key: str, default: str = '') -> str:
    if isinstance(row, dict):
        return str(row.get(key) or default)
    try:
        return str(row[key] or default)
    except (KeyError, IndexError):
        return default


def _decorate_warrant_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    source_record_id = row['source_record_id']
    status = _single_line(_row_value(row, 'status'), max_len=32).lower() or STATUS_ACTIVE
    if status not in VALID_STATUSES:
        status = STATUS_ACTIVE
    item = {
        'id': row['id'],
        'source_record_id': source_record_id,
        'slug': warrant_slug(source_record_id),
        'county': row['county'],
        'city': row['city'] or '',
        'person_name': row['person_name'],
        'dob': row['dob'] or '',
        'warrant_type': row['warrant_type'] or '',
        'charges_text': row['charges_text'] or '',
        'issued_by': row['issued_by'] or '',
        'issue_date': row['issue_date'] or '',
        'bond_amount': row['bond_amount'] or '',
        'bond_type': row['bond_type'] or '',
        'status': status,
        'source_url': row['source_url'] or '',
        'updated_at': _row_value(row, 'updated_at'),
        'first_seen_at': _row_value(row, 'first_seen_at'),
        'resolved_at': _row_value(row, 'resolved_at'),
    }
    item['is_active'] = status == STATUS_ACTIVE
    item['status_label'] = 'Active Warrant' if item['is_active'] else 'Resolved'
    item['updated_at_label'] = _display_datetime(item['updated_at'])
    item['resolved_at_label'] = _display_datetime(item['resolved_at'])
    item['issue_date_label'] = _display_datetime(item['issue_date']) if item['issue_date'] else 'Not provided'
    item['public_href'] = f"/wanted/{item['slug']}"
    item['location_label'] = ', '.join(
        part for part in [item['city'], f"{item['county']} County"] if part
    ) or f"{item['county']} County"
    return item


def _build_where(
    *,
    status: str = 'active',
    q: str = '',
    county: str = '',
    city: str = '',
) -> tuple[str, list[Any]]:
    clauses = ['1=1']
    params: list[Any] = []

    normalized_status = _single_line(status, max_len=32).lower() or STATUS_ACTIVE
    if normalized_status == STATUS_ACTIVE:
        clauses.append('status = ?')
        params.append(STATUS_ACTIVE)
    elif normalized_status == STATUS_RESOLVED:
        clauses.append('status = ?')
        params.append(STATUS_RESOLVED)
    elif normalized_status != 'all':
        clauses.append('status = ?')
        params.append(STATUS_ACTIVE)

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

    city_term = _single_line(city, max_len=120)
    if city_term and not county_term:
        clauses.append('city LIKE ?')
        params.append(f'%{city_term}%')

    return ' AND '.join(clauses), params


def _build_city_where(
    *,
    status: str = 'active',
    city: str = '',
    county: str = '',
) -> tuple[str, list[Any]]:
    clauses = ['1=1']
    params: list[Any] = []

    normalized_status = _single_line(status, max_len=32).lower() or STATUS_ACTIVE
    if normalized_status == STATUS_ACTIVE:
        clauses.append('status = ?')
        params.append(STATUS_ACTIVE)
    elif normalized_status == STATUS_RESOLVED:
        clauses.append('status = ?')
        params.append(STATUS_RESOLVED)

    city_term = _single_line(city, max_len=120)
    county_term = _single_line(county, max_len=120)
    if city_term or county_term:
        location_parts: list[str] = []
        if city_term:
            location_parts.append('city LIKE ?')
            params.append(f'%{city_term}%')
        if county_term:
            location_parts.append('county = ?')
            params.append(county_term)
        clauses.append(f"({' OR '.join(location_parts)})")

    return ' AND '.join(clauses), params


def _sort_clause(sort: str) -> str:
    normalized_sort = _single_line(sort, max_len=32).lower() or 'updated_desc'
    if normalized_sort == 'name_asc':
        return 'ORDER BY person_name ASC, id ASC'
    if normalized_sort == 'county_asc':
        return 'ORDER BY county ASC, person_name ASC, id ASC'
    if normalized_sort in {'recently_resolved', 'resolved_desc'}:
        return "ORDER BY CASE WHEN status = 'resolved' THEN 0 ELSE 1 END, datetime(resolved_at) DESC, datetime(updated_at) DESC, id DESC"
    return 'ORDER BY datetime(updated_at) DESC, id DESC'


def _list_warrant_counties(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT county
        FROM warrants
        WHERE COALESCE(county, '') != ''
        ORDER BY county ASC
        """
    ).fetchall()
    return [str(row['county']) for row in rows]


def _fetch_warrant_rows(
    conn: sqlite3.Connection,
    *,
    status: str = 'active',
    q: str = '',
    county: str = '',
    city: str = '',
    sort: str = 'updated_desc',
    limit: int | None = None,
    offset: int = 0,
) -> list[sqlite3.Row]:
    where_sql, params = _build_where(status=status, q=q, county=county, city=city)
    sql = f'SELECT * FROM warrants WHERE {where_sql} {_sort_clause(sort)}'
    if limit is not None:
        sql += ' LIMIT ? OFFSET ?'
        params.extend([limit, offset])
    return conn.execute(sql, params).fetchall()


def _fetch_city_warrant_rows(
    conn: sqlite3.Connection,
    *,
    status: str = 'active',
    city: str = '',
    county: str = '',
    sort: str = 'updated_desc',
    limit: int | None = None,
) -> list[sqlite3.Row]:
    where_sql, params = _build_city_where(status=status, city=city, county=county)
    sql = f'SELECT * FROM warrants WHERE {where_sql} {_sort_clause(sort)}'
    if limit is not None:
        sql += ' LIMIT ?'
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def warrant_public_context(
    conn: sqlite3.Connection,
    *,
    status_filter: str | None = None,
    q: str | None = None,
    county: str | None = None,
    sort: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    ensure_warrant_schema(conn)
    normalized_status = _single_line(status_filter or 'active', max_len=32).lower() or 'active'
    if normalized_status not in {'active', 'resolved', 'all'}:
        normalized_status = 'active'
    search_term = _single_line(q or '', max_len=120)
    county_term = _single_line(county or '', max_len=120)
    normalized_sort = _single_line(sort or 'updated_desc', max_len=32).lower()
    if normalized_sort not in {'updated_desc', 'name_asc', 'county_asc', 'recently_resolved', 'resolved_desc'}:
        normalized_sort = 'updated_desc'

    where_sql, params = _build_where(
        status=normalized_status,
        q=search_term,
        county=county_term,
    )
    total = conn.execute(
        f'SELECT COUNT(*) AS total FROM warrants WHERE {where_sql}',
        params,
    ).fetchone()['total']
    rows = _fetch_warrant_rows(
        conn,
        status=normalized_status,
        q=search_term,
        county=county_term,
        sort=normalized_sort,
        limit=limit,
        offset=offset,
    )

    active_count = conn.execute(
        "SELECT COUNT(*) AS total FROM warrants WHERE status = 'active'",
    ).fetchone()['total']
    resolved_count = conn.execute(
        "SELECT COUNT(*) AS total FROM warrants WHERE status = 'resolved'",
    ).fetchone()['total']
    county_count = conn.execute(
        "SELECT COUNT(DISTINCT county) AS total FROM warrants WHERE status = 'active'",
    ).fetchone()['total']
    latest_updated = conn.execute(
        "SELECT MAX(updated_at) AS latest FROM warrants WHERE status = 'active'",
    ).fetchone()['latest']

    return {
        'rows': [_decorate_warrant_row(row) for row in rows],
        'status_filter': normalized_status,
        'q': search_term,
        'county_filter': county_term,
        'sort_filter': normalized_sort,
        'county_options': _list_warrant_counties(conn),
        'summary': {
            'active': int(active_count or 0),
            'resolved': int(resolved_count or 0),
            'counties': int(county_count or 0),
            'latest_updated': latest_updated or '',
        },
        'pagination': {
            'total': int(total or 0),
            'limit': limit,
            'offset': offset,
            'has_more': offset + len(rows) < int(total or 0),
        },
    }


def warrant_detail_context(conn: sqlite3.Connection, slug: str) -> dict[str, Any] | None:
    ensure_warrant_schema(conn)
    normalized_slug = _single_line(slug, max_len=120).lower()
    if not normalized_slug:
        return None

    rows = conn.execute(
        'SELECT * FROM warrants ORDER BY datetime(updated_at) DESC, id DESC',
    ).fetchall()
    for row in rows:
        if warrant_slug(row['source_record_id']) != normalized_slug:
            continue
        record = _decorate_warrant_row(row)
        related_rows = conn.execute(
            """
            SELECT *
            FROM warrants
            WHERE county = ? AND source_record_id != ? AND status = 'active'
            ORDER BY person_name ASC
            LIMIT 6
            """,
            (row['county'], row['source_record_id']),
        ).fetchall()
        return {
            'record': record,
            'related': [_decorate_warrant_row(item) for item in related_rows],
        }
    return None


def warrant_homepage_context(conn: sqlite3.Connection, *, limit: int = 6) -> dict[str, Any]:
    ensure_warrant_schema(conn)
    active_limit = max(limit // 2, 1)
    resolved_limit = max(limit - active_limit, 1)
    active_rows = _fetch_warrant_rows(
        conn,
        status=STATUS_ACTIVE,
        sort='updated_desc',
        limit=active_limit,
    )
    resolved_rows = _fetch_warrant_rows(
        conn,
        status=STATUS_RESOLVED,
        sort='recently_resolved',
        limit=resolved_limit,
    )
    active_count = conn.execute(
        "SELECT COUNT(*) AS total FROM warrants WHERE status = 'active'",
    ).fetchone()['total']
    latest_updated = conn.execute(
        "SELECT MAX(updated_at) AS latest FROM warrants WHERE status = 'active'",
    ).fetchone()['latest']
    rows = [_decorate_warrant_row(row) for row in active_rows]
    rows.extend(_decorate_warrant_row(row) for row in resolved_rows)
    return {
        'rows': rows,
        'active_rows': [_decorate_warrant_row(row) for row in active_rows],
        'resolved_rows': [_decorate_warrant_row(row) for row in resolved_rows],
        'total_active': int(active_count or 0),
        'latest_update': latest_updated or '',
        'latest_update_label': _display_datetime(latest_updated) if latest_updated else '',
    }


def warrant_city_context(
    conn: sqlite3.Connection,
    city_name: str,
    county_name: str,
    *,
    limit: int = 6,
) -> dict[str, Any]:
    ensure_warrant_schema(conn)
    city_term = _single_line(city_name, max_len=120)
    county_term = _single_line(county_name, max_len=120)

    active_limit = max(limit // 2, 1)
    resolved_limit = max(limit - active_limit, 1)

    active_rows = _fetch_city_warrant_rows(
        conn,
        status=STATUS_ACTIVE,
        city=city_term,
        county=county_term,
        sort='updated_desc',
        limit=active_limit,
    )
    resolved_rows = _fetch_city_warrant_rows(
        conn,
        status=STATUS_RESOLVED,
        city=city_term,
        county=county_term,
        sort='recently_resolved',
        limit=resolved_limit,
    )

    where_sql, count_params = _build_city_where(
        status=STATUS_ACTIVE,
        city=city_term,
        county=county_term,
    )
    active_count = conn.execute(
        f'SELECT COUNT(*) AS total FROM warrants WHERE {where_sql}',
        count_params,
    ).fetchone()['total']

    rows = [_decorate_warrant_row(row) for row in active_rows]
    rows.extend(_decorate_warrant_row(row) for row in resolved_rows)
    return {
        'rows': rows,
        'active_rows': [_decorate_warrant_row(row) for row in active_rows],
        'resolved_rows': [_decorate_warrant_row(row) for row in resolved_rows],
        'total_active': int(active_count or 0),
        'city_name': city_term,
        'county_name': county_term,
    }
