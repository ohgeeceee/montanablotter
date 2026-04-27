from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from html import unescape
from html import escape
from typing import Any
from urllib.parse import urljoin

import config
import requests
from morning_briefing import send_email as send_digest_email


STATUS_MISSING = 'missing'
STATUS_LOCATED = 'located'
VALID_STATUSES = {STATUS_MISSING, STATUS_LOCATED}
BASE_URL = getattr(config, 'BASE_URL', 'https://montanablotter.com').rstrip('/')
OFFICIAL_SOURCE_NAME = 'Montana Department of Justice Missing Persons Database'
OFFICIAL_SOURCE_BASE_URL = 'https://app.doj.mt.gov/apps/missingPersonDatabase/search/'
OFFICIAL_SOURCE_RESULTS_URL = urljoin(OFFICIAL_SOURCE_BASE_URL, 'results.php')
OFFICIAL_SOURCE_CHILDREN_URL = f'{OFFICIAL_SOURCE_RESULTS_URL}?filter=minors'
OFFICIAL_SOURCE_INDIGENOUS_URL = f'{OFFICIAL_SOURCE_RESULTS_URL}?filter=indigenous'
OFFICIAL_CLEARINGHOUSE_PHONE = '(406) 444-1526'
OFFICIAL_CLEARINGHOUSE_EMAIL = 'missingpersons@mt.gov'


def _clean_text(value: Any, *, max_len: int = 1000) -> str:
    text = str(value or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    if not text:
        return ''
    return text[:max_len]


def _single_line(value: Any, *, max_len: int = 240) -> str:
    return ' '.join(_clean_text(value, max_len=max_len).split())


def _slugify(value: Any) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', _single_line(value, max_len=160).lower()).strip('-')
    return slug[:80] or 'missing-person'


def _normalize_status(value: Any) -> str:
    status = _single_line(value, max_len=32).lower() or STATUS_MISSING
    if status not in VALID_STATUSES:
        raise ValueError('Choose a valid missing-person status.')
    return status


def _normalize_optional_int(value: Any, *, label: str, minimum: int = 0, maximum: int = 120) -> int | None:
    raw = _single_line(value, max_len=16)
    if not raw:
        return None
    try:
        number = int(raw)
    except ValueError as exc:
        raise ValueError(f'{label} must be a whole number.') from exc
    if number < minimum or number > maximum:
        raise ValueError(f'{label} must be between {minimum} and {maximum}.')
    return number


def _normalize_datetime(value: Any) -> str:
    raw = _single_line(value, max_len=40)
    if not raw:
        return ''
    for fmt in (
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%dT%H:%M',
        '%Y-%m-%d %H:%M',
        '%Y-%m-%d',
        '%m/%d/%Y %H:%M',
        '%m/%d/%Y',
    ):
        try:
            parsed = datetime.strptime(raw, fmt)
            if fmt == '%Y-%m-%d':
                return parsed.strftime('%Y-%m-%d 00:00:00')
            if fmt == '%m/%d/%Y':
                return parsed.strftime('%Y-%m-%d 00:00:00')
            return parsed.strftime('%Y-%m-%d %H:%M:%S')
        except ValueError:
            continue
    raise ValueError('Last seen time must be a valid date or datetime.')


def _normalize_directory_record(raw: dict[str, Any]) -> dict[str, Any]:
    status = _normalize_status(raw.get('status'))
    date_last_seen = ''
    raw_date_last_seen = _single_line(raw.get('date_last_seen'), max_len=40)
    if raw_date_last_seen:
        try:
            date_last_seen = _normalize_datetime(raw_date_last_seen)
        except ValueError:
            date_last_seen = ''

    return {
        'full_name': _single_line(raw.get('full_name'), max_len=160),
        'age': _normalize_optional_int(raw.get('age'), label='Age', maximum=150),
        'missing_from': _single_line(raw.get('missing_from'), max_len=240),
        'date_last_seen': date_last_seen,
        'height_weight': _single_line(raw.get('height_weight'), max_len=240),
        'case_number': _single_line(raw.get('case_number'), max_len=80),
        'status': status,
        'is_active': status == STATUS_MISSING,
    }


def _status_to_is_active(status: str) -> int:
    return 1 if status == STATUS_MISSING else 0


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


def _parse_height_label(raw_height: Any) -> str:
    token = _single_line(raw_height, max_len=16)
    if len(token) == 3 and token.isdigit():
        return f"{token[0]}'{token[1:]}\""
    if len(token) == 2 and token.isdigit():
        return f"{token[0]}'{token[1]}\""
    return token


def _build_height_weight_value(*, height_raw: Any = '', weight_lbs: Any = None, fallback: Any = '') -> str:
    if fallback:
        return _single_line(fallback, max_len=240)
    parts: list[str] = []
    height_label = _parse_height_label(height_raw)
    if height_label:
        parts.append(height_label)
    if weight_lbs not in (None, ''):
        parts.append(f'{weight_lbs} lbs')
    return ' / '.join(parts)[:240]


def _parse_aliases(raw_aliases: Any) -> list[str]:
    value = _clean_text(raw_aliases, max_len=800)
    if not value:
        return []
    seen: set[str] = set()
    aliases: list[str] = []
    for part in value.split(';'):
        alias = _single_line(part, max_len=160)
        if not alias:
            continue
        lowered = alias.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        aliases.append(alias)
    return aliases


def _ensure_missing_person_columns(conn: sqlite3.Connection) -> None:
    existing_columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info('missing_persons')").fetchall()
    }
    added_is_active_column = False
    for column_name, definition in [
        ('case_number', "TEXT DEFAULT ''"),
        ('missing_from', "TEXT DEFAULT ''"),
        ('height_weight', "TEXT DEFAULT ''"),
        ('last_synced', "TEXT DEFAULT ''"),
        ('is_active', 'INTEGER NOT NULL DEFAULT 1'),
        ('source_person_id', "TEXT DEFAULT ''"),
        ('gender', "TEXT DEFAULT ''"),
        ('race', "TEXT DEFAULT ''"),
        ('hair_color', "TEXT DEFAULT ''"),
        ('eye_color', "TEXT DEFAULT ''"),
        ('height_raw', "TEXT DEFAULT ''"),
        ('weight_lbs', 'INTEGER'),
        ('age_missing', 'INTEGER'),
        ('investigating_agency', "TEXT DEFAULT ''"),
        ('aliases', "TEXT DEFAULT ''"),
        ('is_indigenous', 'INTEGER NOT NULL DEFAULT 0'),
        ('is_child', 'INTEGER NOT NULL DEFAULT 0'),
        ('photo_gallery_json', "TEXT DEFAULT '[]'"),
        ('official_last_updated', "TEXT DEFAULT ''"),
        ('official_last_checked', "TEXT DEFAULT ''"),
    ]:
        if column_name not in existing_columns:
            conn.execute(f'ALTER TABLE missing_persons ADD COLUMN {column_name} {definition}')
            existing_columns.add(column_name)
            if column_name == 'is_active':
                added_is_active_column = True
    if added_is_active_column:
        conn.execute(
            'UPDATE missing_persons SET is_active = CASE WHEN status = ? THEN 1 ELSE 0 END',
            (STATUS_MISSING,),
        )


def _get_missing_person_source_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    ensure_missing_person_schema(conn)
    row = conn.execute(
        '''
        SELECT *
        FROM missing_person_source_stats
        WHERE id = 1
        LIMIT 1
        '''
    ).fetchone()
    if not row:
        return {
            'total_active': 0,
            'children': 0,
            'indigenous': 0,
            'missing_less_than_year': 0,
            'missing_more_than_year': 0,
            'official_last_updated': '',
            'official_last_checked': '',
            'synced_at': '',
        }
    return dict(row)


def _absolute_official_url(path: Any) -> str:
    raw = _single_line(path, max_len=600).replace('\\', '/')
    if not raw:
        return ''
    return urljoin(OFFICIAL_SOURCE_BASE_URL, raw)


def _ensure_subscriber_columns(conn: sqlite3.Connection) -> None:
    existing_columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info('subscribers')").fetchall()
    }
    for column_name, definition in [
        ('updated_at', 'TEXT'),
        ('source', 'TEXT'),
        ('notes', 'TEXT'),
        ('missing_person_email_opt_in', 'INTEGER NOT NULL DEFAULT 1'),
        ('missing_person_sms_opt_in', 'INTEGER NOT NULL DEFAULT 0'),
        ('missing_person_push_opt_in', 'INTEGER NOT NULL DEFAULT 0'),
        ('phone_verified_at', "TEXT DEFAULT ''"),
    ]:
        if column_name not in existing_columns:
            conn.execute(f'ALTER TABLE subscribers ADD COLUMN {column_name} {definition}')
            existing_columns.add(column_name)


def _ensure_missing_person_delivery_schema(conn: sqlite3.Connection) -> None:
    delivery_columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info('missing_person_alert_deliveries')").fetchall()
    }
    for column_name, definition in [
        ('subscriber_id', 'INTEGER'),
        ('channel', "TEXT NOT NULL DEFAULT 'email'"),
        ('recipient', "TEXT DEFAULT ''"),
        ('provider_message_id', "TEXT DEFAULT ''"),
        ('updated_at', 'TEXT'),
    ]:
        if column_name not in delivery_columns:
            conn.execute(f'ALTER TABLE missing_person_alert_deliveries ADD COLUMN {column_name} {definition}')
            delivery_columns.add(column_name)

    if 'recipient' in delivery_columns and 'recipient_email' in delivery_columns:
        conn.execute(
            '''
            UPDATE missing_person_alert_deliveries
            SET recipient = recipient_email
            WHERE TRIM(COALESCE(recipient, '')) = ''
              AND TRIM(COALESCE(recipient_email, '')) != ''
            '''
        )
    if 'updated_at' in delivery_columns:
        conn.execute(
            '''
            UPDATE missing_person_alert_deliveries
            SET updated_at = COALESCE(
                NULLIF(updated_at, ''),
                NULLIF(created_at, ''),
                datetime('now')
            )
            WHERE COALESCE(updated_at, '') = ''
            '''
        )

    conn.execute('DROP INDEX IF EXISTS idx_missing_person_alert_delivery_unique')
    conn.execute(
        '''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_missing_person_alert_delivery_unique
        ON missing_person_alert_deliveries(
            missing_person_id,
            notification_version,
            channel,
            COALESCE(
                NULLIF(COALESCE(recipient, ''), ''),
                CASE
                    WHEN channel = 'email' THEN NULLIF(COALESCE(recipient_email, ''), '')
                    ELSE NULL
                END,
                CASE
                    WHEN subscriber_id IS NOT NULL THEN '__subscriber__' || CAST(subscriber_id AS TEXT)
                    ELSE NULL
                END,
                '__row__' || CAST(id AS TEXT)
            )
        )
        '''
    )


def ensure_missing_person_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS missing_persons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT UNIQUE,
            full_name TEXT NOT NULL,
            age INTEGER,
            city TEXT DEFAULT '',
            county TEXT DEFAULT '',
            last_seen_at TEXT DEFAULT '',
            last_seen_location TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL,
            physical_description TEXT DEFAULT '',
            contact_info TEXT DEFAULT '',
            source_name TEXT DEFAULT '',
            source_url TEXT DEFAULT '',
            photo_url TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'missing',
            resolution_summary TEXT DEFAULT '',
            resolved_at TEXT DEFAULT '',
            last_alerted_at TEXT DEFAULT '',
            alert_delivery_count INTEGER NOT NULL DEFAULT 0,
            notification_version INTEGER NOT NULL DEFAULT 1,
            created_by TEXT DEFAULT '',
            updated_by TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        '''
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS missing_person_alert_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            missing_person_id INTEGER NOT NULL,
            notification_version INTEGER NOT NULL DEFAULT 1,
            recipient_email TEXT NOT NULL,
            subscriber_id INTEGER,
            channel TEXT NOT NULL DEFAULT 'email',
            recipient TEXT DEFAULT '',
            provider_message_id TEXT DEFAULT '',
            delivery_status TEXT NOT NULL DEFAULT 'queued',
            error_message TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (missing_person_id) REFERENCES missing_persons(id) ON DELETE CASCADE
        )
        '''
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS missing_person_push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subscriber_id INTEGER NOT NULL,
            endpoint TEXT NOT NULL,
            p256dh_key TEXT NOT NULL,
            auth_key TEXT NOT NULL,
            user_agent TEXT DEFAULT '',
            device_label TEXT DEFAULT '',
            last_seen_county TEXT DEFAULT '',
            last_seen_city TEXT DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        '''
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS missing_person_source_stats (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            source_name TEXT NOT NULL DEFAULT '',
            total_active INTEGER NOT NULL DEFAULT 0,
            children INTEGER NOT NULL DEFAULT 0,
            indigenous INTEGER NOT NULL DEFAULT 0,
            missing_less_than_year INTEGER NOT NULL DEFAULT 0,
            missing_more_than_year INTEGER NOT NULL DEFAULT 0,
            official_last_updated TEXT DEFAULT '',
            official_last_checked TEXT DEFAULT '',
            synced_at TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        '''
    )
    _ensure_missing_person_delivery_schema(conn)
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_missing_persons_status_updated '
        'ON missing_persons(status, updated_at)'
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_missing_persons_county_status '
        'ON missing_persons(county, status)'
    )
    _ensure_missing_person_columns(conn)
    conn.execute(
        'CREATE UNIQUE INDEX IF NOT EXISTS idx_missing_persons_source_person_id '
        "ON missing_persons(source_person_id) WHERE COALESCE(source_person_id, '') != ''"
    )
    _ensure_subscriber_columns(conn)


def _decorate_person_row(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    item = dict(row)
    item['status_label'] = 'Active Missing Alert' if item['status'] == STATUS_MISSING else 'Located / Resolved'
    item['last_seen_at_label'] = _display_datetime(item.get('last_seen_at'))
    item['resolved_at_label'] = _display_datetime(item.get('resolved_at'))
    item['last_alerted_at_label'] = _display_datetime(item.get('last_alerted_at'))
    item['updated_at_label'] = _display_datetime(item.get('updated_at'))
    item['created_at_label'] = _display_datetime(item.get('created_at'))
    item['public_href'] = f"/missing-persons/{item['slug']}" if item.get('slug') else '/missing-persons'
    item['search_location'] = ', '.join(
        part for part in [item.get('last_seen_location'), item.get('city'), item.get('county')] if part
    )
    item['is_active'] = item['status'] == STATUS_MISSING
    item['height_label'] = _parse_height_label(item.get('height_raw'))
    item['aliases_list'] = _parse_aliases(item.get('aliases'))
    item['photo_gallery'] = []
    try:
        gallery = json.loads(item.get('photo_gallery_json') or '[]')
        if isinstance(gallery, list):
            item['photo_gallery'] = [entry for entry in gallery if isinstance(entry, dict)]
    except (TypeError, ValueError, json.JSONDecodeError):
        item['photo_gallery'] = []
    if item.get('photo_url') and not item['photo_gallery']:
        item['photo_gallery'] = [{'url': item['photo_url'], 'label': ''}]
    return item


def _fetch_missing_person_rows(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    q: str = '',
    county: str = '',
    sort: str = 'updated_desc',
    limit: int | None = None,
) -> list[sqlite3.Row]:
    sql = ['SELECT * FROM missing_persons WHERE 1 = 1']
    params: list[Any] = []
    if status:
        sql.append('AND status = ?')
        params.append(status)
    search_term = _single_line(q, max_len=120)
    if search_term:
        token = f'%{search_term}%'
        sql.append(
            '''
            AND (
                full_name LIKE ?
                OR county LIKE ?
                OR city LIKE ?
                OR last_seen_location LIKE ?
                OR summary LIKE ?
            )
            '''
        )
        params.extend([token, token, token, token, token])
    county_term = _single_line(county, max_len=120)
    if county_term:
        sql.append('AND county = ?')
        params.append(county_term)

    normalized_sort = _single_line(sort, max_len=32).lower()
    if normalized_sort in {'newest', 'newest_alerts', 'created_desc'}:
        sql.append('ORDER BY CASE WHEN status = ? THEN 0 ELSE 1 END, datetime(created_at) DESC, datetime(updated_at) DESC, id DESC')
        params.append(STATUS_MISSING)
    elif normalized_sort in {'recently_resolved', 'found', 'resolved_desc'}:
        sql.append('ORDER BY CASE WHEN status = ? THEN 0 ELSE 1 END, datetime(resolved_at) DESC, datetime(updated_at) DESC, id DESC')
        params.append(STATUS_LOCATED)
    elif normalized_sort in {'name', 'name_asc'}:
        sql.append('ORDER BY full_name COLLATE NOCASE ASC, datetime(updated_at) DESC, id DESC')
    else:
        sql.append('ORDER BY CASE WHEN status = ? THEN 0 ELSE 1 END, datetime(updated_at) DESC, id DESC')
        params.append(STATUS_MISSING)

    if limit is not None:
        sql.append('LIMIT ?')
        params.append(int(limit))

    return conn.execute(' '.join(sql), params).fetchall()


def _list_missing_person_counties(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        '''
        SELECT DISTINCT county
        FROM missing_persons
        WHERE TRIM(COALESCE(county, '')) != ''
        ORDER BY county COLLATE NOCASE ASC
        '''
    ).fetchall()
    return [str(row['county']) for row in rows if row['county']]


def get_missing_person_by_id(conn: sqlite3.Connection, person_id: int) -> dict[str, Any] | None:
    ensure_missing_person_schema(conn)
    row = conn.execute(
        'SELECT * FROM missing_persons WHERE id = ? LIMIT 1',
        (int(person_id),),
    ).fetchone()
    return _decorate_person_row(row)


def get_missing_person_by_slug(conn: sqlite3.Connection, slug: str) -> dict[str, Any] | None:
    ensure_missing_person_schema(conn)
    row = conn.execute(
        'SELECT * FROM missing_persons WHERE slug = ? LIMIT 1',
        ((slug or '').strip(),),
    ).fetchone()
    return _decorate_person_row(row)


def missing_person_homepage_context(conn: sqlite3.Connection, *, limit: int = 3) -> dict[str, Any]:
    ensure_missing_person_schema(conn)
    rows = _fetch_missing_person_rows(conn, status=STATUS_MISSING, sort='newest_alerts', limit=limit)
    resolved_rows = _fetch_missing_person_rows(conn, status=STATUS_LOCATED, sort='recently_resolved', limit=limit)
    total_active = conn.execute(
        'SELECT COUNT(*) AS total FROM missing_persons WHERE status = ?',
        (STATUS_MISSING,),
    ).fetchone()['total']
    latest_update = conn.execute(
        '''
        SELECT MAX(updated_at) AS latest_update
        FROM missing_persons
        WHERE status = ?
        ''',
        (STATUS_MISSING,),
    ).fetchone()['latest_update']
    source_stats = _get_missing_person_source_stats(conn)
    return {
        'rows': [_decorate_person_row(row) for row in rows],
        'resolved_rows': [_decorate_person_row(row) for row in resolved_rows],
        'total_active': int(source_stats.get('total_active') or total_active or 0),
        'latest_update': latest_update,
        'latest_update_label': source_stats.get('official_last_updated') or (_display_datetime(latest_update) if latest_update else ''),
        'official_last_checked': source_stats.get('official_last_checked') or '',
    }


def missing_person_public_context(
    conn: sqlite3.Connection,
    *,
    status_filter: str = 'active',
    q: str = '',
    county: str = '',
    sort: str = 'updated_desc',
) -> dict[str, Any]:
    ensure_missing_person_schema(conn)
    normalized_status = (status_filter or 'active').strip().lower()
    if normalized_status not in {'active', 'located', 'all'}:
        normalized_status = 'active'
    search_term = _single_line(q, max_len=120)
    county_term = _single_line(county, max_len=120)
    normalized_sort = _single_line(sort, max_len=32).lower()
    if normalized_sort not in {'updated_desc', 'newest', 'newest_alerts', 'recently_resolved', 'found', 'resolved_desc', 'name', 'name_asc'}:
        normalized_sort = 'updated_desc'

    rows = _fetch_missing_person_rows(
        conn,
        status=STATUS_MISSING if normalized_status == 'active' else (STATUS_LOCATED if normalized_status == 'located' else None),
        q=search_term,
        county=county_term,
        sort=normalized_sort,
    )
    newest_alerts = _fetch_missing_person_rows(
        conn,
        status=STATUS_MISSING,
        q=search_term,
        county=county_term,
        sort='newest_alerts',
        limit=4,
    )
    recent_resolved = _fetch_missing_person_rows(
        conn,
        status=STATUS_LOCATED,
        q=search_term,
        county=county_term,
        sort='recently_resolved',
        limit=4,
    )
    active_count = conn.execute(
        'SELECT COUNT(*) AS total FROM missing_persons WHERE status = ?',
        (STATUS_MISSING,),
    ).fetchone()['total']
    located_count = conn.execute(
        'SELECT COUNT(*) AS total FROM missing_persons WHERE status = ?',
        (STATUS_LOCATED,),
    ).fetchone()['total']
    new_7d = conn.execute(
        '''
        SELECT COUNT(*) AS total
        FROM missing_persons
        WHERE status = ?
          AND datetime(created_at) >= datetime('now', '-7 days')
        ''',
        (STATUS_MISSING,),
    ).fetchone()['total']
    latest_active = conn.execute(
        '''
        SELECT *
        FROM missing_persons
        WHERE status = ?
        ORDER BY datetime(created_at) DESC, datetime(updated_at) DESC, id DESC
        LIMIT 1
        ''',
        (STATUS_MISSING,),
    ).fetchone()
    source_stats = _get_missing_person_source_stats(conn)
    county_options = _list_missing_person_counties(conn)

    return {
        'rows': [_decorate_person_row(row) for row in rows],
        'status_filter': normalized_status,
        'q': search_term,
        'county_filter': county_term,
        'sort_filter': normalized_sort,
        'county_options': county_options,
        'summary': {
            'active': int(source_stats.get('total_active') or active_count or 0),
            'located': int(located_count or 0),
            'new_7d': int(new_7d or 0),
            'children': int(source_stats.get('children') or 0),
            'indigenous': int(source_stats.get('indigenous') or 0),
            'missing_less_than_year': int(source_stats.get('missing_less_than_year') or 0),
            'missing_more_than_year': int(source_stats.get('missing_more_than_year') or 0),
            'official_last_updated': source_stats.get('official_last_updated') or '',
            'official_last_checked': source_stats.get('official_last_checked') or '',
            'synced_at': source_stats.get('synced_at') or '',
        },
        'latest_active': _decorate_person_row(latest_active),
        'newest_alerts': [_decorate_person_row(row) for row in newest_alerts],
        'recent_resolved': [_decorate_person_row(row) for row in recent_resolved],
        'source_stats': source_stats,
    }


def missing_person_detail_context(conn: sqlite3.Connection, slug: str) -> dict[str, Any] | None:
    ensure_missing_person_schema(conn)
    person = get_missing_person_by_slug(conn, slug)
    if not person:
        return None
    related_rows = conn.execute(
        '''
        SELECT *
        FROM missing_persons
        WHERE status = ?
          AND id != ?
          AND (
            (? != '' AND county = ?)
            OR (? = '')
          )
        ORDER BY datetime(updated_at) DESC, id DESC
        LIMIT 4
        ''',
        (
            STATUS_MISSING,
            int(person['id']),
            person.get('county') or '',
            person.get('county') or '',
            person.get('county') or '',
        ),
    ).fetchall()
    return {
        'person': person,
        'related_rows': [_decorate_person_row(row) for row in related_rows],
    }


def missing_person_admin_context(
    conn: sqlite3.Connection,
    *,
    status_filter: str = 'all',
    q: str = '',
) -> dict[str, Any]:
    public_context = missing_person_public_context(conn, status_filter=status_filter, q=q)
    public_context['rows'] = public_context['rows'][:200]
    return public_context


def create_missing_person(conn: sqlite3.Connection, payload: dict[str, Any], *, actor: str = '') -> dict[str, Any]:
    ensure_missing_person_schema(conn)
    directory_record = _normalize_directory_record(
        {
            'full_name': payload.get('full_name'),
            'age': payload.get('age'),
            'missing_from': payload.get('missing_from') or payload.get('last_seen_location'),
            'date_last_seen': payload.get('last_seen_at'),
            'height_weight': payload.get('height_weight'),
            'case_number': payload.get('case_number'),
            'status': payload.get('status'),
        }
    )
    full_name = directory_record['full_name']
    if len(full_name) < 3:
        raise ValueError('Enter the missing person name.')
    summary = _clean_text(payload.get('summary'), max_len=1600)
    if len(summary) < 12:
        raise ValueError('Enter a concise public summary for the alert.')
    last_seen_location = _single_line(payload.get('last_seen_location'), max_len=220)
    if len(last_seen_location) < 3:
        raise ValueError('Enter the last seen location.')
    status = directory_record['status']
    last_seen_at = directory_record['date_last_seen']

    cursor = conn.execute(
        '''
        INSERT INTO missing_persons (
            full_name,
            age,
            city,
            county,
            last_seen_at,
            last_seen_location,
            summary,
            physical_description,
            contact_info,
            source_name,
            source_url,
            photo_url,
            case_number,
            missing_from,
            height_weight,
            last_synced,
            status,
            is_active,
            resolution_summary,
            created_by,
            updated_by,
            created_at,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        ''',
        (
            full_name,
            directory_record['age'],
            _single_line(payload.get('city'), max_len=120),
            _single_line(payload.get('county'), max_len=120),
            last_seen_at,
            last_seen_location,
            summary,
            _clean_text(payload.get('physical_description'), max_len=1200),
            _clean_text(payload.get('contact_info'), max_len=800),
            _single_line(payload.get('source_name'), max_len=120) or 'Montana Blotter',
            _single_line(payload.get('source_url'), max_len=500),
            _single_line(payload.get('photo_url'), max_len=500),
            directory_record['case_number'],
            directory_record['missing_from'] or last_seen_location,
            _build_height_weight_value(fallback=directory_record['height_weight']),
            '',
            status,
            _status_to_is_active(status),
            _clean_text(payload.get('resolution_summary'), max_len=1200),
            _single_line(actor, max_len=120),
            _single_line(actor, max_len=120),
        ),
    )
    person_id = int(cursor.lastrowid)
    conn.execute(
        'UPDATE missing_persons SET slug = ? WHERE id = ?',
        (f'{_slugify(full_name)}-{person_id}', person_id),
    )
    return get_missing_person_by_id(conn, person_id)


def update_missing_person(conn: sqlite3.Connection, person_id: int, payload: dict[str, Any], *, actor: str = '') -> tuple[dict[str, Any], bool]:
    ensure_missing_person_schema(conn)
    existing = conn.execute(
        'SELECT * FROM missing_persons WHERE id = ? LIMIT 1',
        (int(person_id),),
    ).fetchone()
    if not existing:
        raise ValueError('Missing-person record not found.')

    directory_record = _normalize_directory_record(
        {
            'full_name': payload.get('full_name'),
            'age': payload.get('age'),
            'missing_from': payload.get('missing_from') or payload.get('last_seen_location'),
            'date_last_seen': payload.get('last_seen_at'),
            'height_weight': payload.get('height_weight'),
            'case_number': payload.get('case_number'),
            'status': payload.get('status'),
        }
    )
    full_name = directory_record['full_name']
    if len(full_name) < 3:
        raise ValueError('Enter the missing person name.')
    summary = _clean_text(payload.get('summary'), max_len=1600)
    if len(summary) < 12:
        raise ValueError('Enter a concise public summary for the alert.')
    last_seen_location = _single_line(payload.get('last_seen_location'), max_len=220)
    if len(last_seen_location) < 3:
        raise ValueError('Enter the last seen location.')
    last_seen_at = directory_record['date_last_seen']

    new_status = directory_record['status']
    should_notify = existing['status'] != STATUS_MISSING and new_status == STATUS_MISSING
    notification_version = int(existing['notification_version'] or 1)
    if should_notify:
        notification_version += 1
    resolved_at = existing['resolved_at'] or ''
    resolution_summary = _clean_text(payload.get('resolution_summary'), max_len=1200)
    if new_status == STATUS_LOCATED and not resolved_at:
        resolved_at = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    if new_status == STATUS_MISSING:
        resolved_at = ''
        resolution_summary = ''
        if not should_notify:
            notification_version = int(existing['notification_version'] or 1)

    conn.execute(
        '''
        UPDATE missing_persons
        SET full_name = ?,
            age = ?,
            city = ?,
            county = ?,
            last_seen_at = ?,
            last_seen_location = ?,
            summary = ?,
            physical_description = ?,
            contact_info = ?,
            source_name = ?,
            source_url = ?,
            photo_url = ?,
            case_number = ?,
            missing_from = ?,
            height_weight = ?,
            status = ?,
            is_active = ?,
            resolution_summary = ?,
            resolved_at = ?,
            notification_version = ?,
            updated_by = ?,
            updated_at = datetime('now')
        WHERE id = ?
        ''',
        (
            full_name,
            directory_record['age'],
            _single_line(payload.get('city'), max_len=120),
            _single_line(payload.get('county'), max_len=120),
            last_seen_at,
            last_seen_location,
            summary,
            _clean_text(payload.get('physical_description'), max_len=1200),
            _clean_text(payload.get('contact_info'), max_len=800),
            _single_line(payload.get('source_name'), max_len=120) or 'Montana Blotter',
            _single_line(payload.get('source_url'), max_len=500),
            _single_line(payload.get('photo_url'), max_len=500),
            directory_record['case_number'],
            directory_record['missing_from'] or last_seen_location,
            _build_height_weight_value(fallback=directory_record['height_weight']),
            new_status,
            _status_to_is_active(new_status),
            resolution_summary,
            resolved_at,
            notification_version,
            _single_line(actor, max_len=120),
            int(person_id),
        ),
    )
    if not existing['slug']:
        conn.execute(
            'UPDATE missing_persons SET slug = ? WHERE id = ?',
            (f'{_slugify(full_name)}-{int(person_id)}', int(person_id)),
        )
    return get_missing_person_by_id(conn, int(person_id)), should_notify


def update_missing_person_status(
    conn: sqlite3.Connection,
    person_id: int,
    *,
    status: str,
    actor: str = '',
    resolution_summary: str = '',
) -> tuple[dict[str, Any], bool]:
    current = get_missing_person_by_id(conn, person_id)
    if not current:
        raise ValueError('Missing-person record not found.')
    payload = {
        'full_name': current['full_name'],
        'age': current.get('age'),
        'city': current.get('city'),
        'county': current.get('county'),
        'last_seen_at': current.get('last_seen_at'),
        'last_seen_location': current.get('last_seen_location'),
        'case_number': current.get('case_number'),
        'missing_from': current.get('missing_from'),
        'height_weight': current.get('height_weight'),
        'summary': current.get('summary'),
        'physical_description': current.get('physical_description'),
        'contact_info': current.get('contact_info'),
        'source_name': current.get('source_name'),
        'source_url': current.get('source_url'),
        'photo_url': current.get('photo_url'),
        'status': status,
        'resolution_summary': resolution_summary or current.get('resolution_summary'),
    }
    return update_missing_person(conn, person_id, payload, actor=actor)


def _parse_official_counts(html: str) -> dict[str, int]:
    counts = {
        'total_active': 0,
        'missing_less_than_year': 0,
        'missing_more_than_year': 0,
    }
    patterns = {
        'total_active': (
            r'totalCount"><span class="underline">(\d+)\s+Individuals',
            r'>(\d+)\s+individuals\s+match\s+search\s+criteria\s*<',
        ),
        'missing_less_than_year': (
            r'lessThanAYearCount"><span class="underline">(\d+)</span>',
            r'>(\d+)\s+missing\s+less\s+than\s+a\s+year\s*<',
        ),
        'missing_more_than_year': (
            r'moreThanAYearCount"><span class="underline">(\d+)</span>',
            r'>(\d+)\s+missing\s+more\s+than\s+a\s+year\s*<',
        ),
    }
    for key, candidates in patterns.items():
        for pattern in candidates:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                counts[key] = int(match.group(1))
                break
    return counts


def _parse_official_tooltip_label(html: str, label: str) -> str:
    match = re.search(rf'{re.escape(label)}:\s*([^<]+)', html, re.IGNORECASE)
    return _single_line(unescape(match.group(1)), max_len=120) if match else ''


def _parse_official_card_records(html: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    card_pattern = re.compile(
        r'<(?:div|li)\s+class="personCard"(?P<attrs>.*?)>(?P<body>.*?)onclick=\'(?:detailOnClickXML|OpenDetailsWindow)\((?P<args>.*?)\)\'',
        re.IGNORECASE | re.DOTALL,
    )
    attr_pattern = re.compile(r'data-(?P<name>[a-z]+)="(?P<value>[^"]*)"')
    image_pattern = re.compile(r'<img src="(?P<src>[^"]+)"', re.IGNORECASE)

    for match in card_pattern.finditer(html):
        attrs = {
            attr_match.group('name'): unescape(attr_match.group('value'))
            for attr_match in attr_pattern.finditer(match.group('attrs'))
        }
        try:
            args = json.loads(f'[{match.group("args")}]')
        except json.JSONDecodeError:
            continue
        if len(args) < 14:
            continue

        image_match = image_pattern.search(match.group('body'))
        gallery_urls: list[dict[str, str]] = []
        for index, image_hash_obj in enumerate(args[0] or []):
            image_hash = ''
            if isinstance(image_hash_obj, dict):
                image_hash = _single_line(image_hash_obj.get('0'), max_len=256)
            if not image_hash:
                continue
            label = ''
            if isinstance(args[1], list) and index < len(args[1]):
                label = _single_line(args[1][index], max_len=40)
            gallery_urls.append(
                {
                    'url': _absolute_official_url(f'images/mmps_img/{image_hash}.jpg'),
                    'label': label,
                }
            )

        primary_image = _absolute_official_url(image_match.group('src')) if image_match else ''
        if not primary_image and gallery_urls:
            primary_image = gallery_urls[0]['url']

        name = _single_line(args[2], max_len=160)
        source_person_id = _single_line(args[13], max_len=32) or _single_line(attrs.get('sort'), max_len=32)
        if not (name and source_person_id):
            continue

        race = _single_line(args[5], max_len=120)
        age_now = _normalize_optional_int(args[4], label='Age', maximum=150)
        age_missing = _normalize_optional_int(args[4] if attrs.get('agemissing') is None else attrs.get('agemissing'), label='Age missing', maximum=150)
        if attrs.get('agemissing') not in (None, ''):
            age_missing = _normalize_optional_int(attrs.get('agemissing'), label='Age missing', maximum=150)

        records.append(
            {
                'source_person_id': source_person_id,
                'full_name': name,
                'gender': _single_line(args[3], max_len=32),
                'age_now': age_now,
                'age_missing': age_missing,
                'race': race,
                'hair_color': _single_line(args[6], max_len=80),
                'eye_color': _single_line(args[7], max_len=80),
                'height_raw': _single_line(args[8], max_len=16),
                'weight_lbs': _normalize_optional_int(args[9], label='Weight', maximum=1000),
                'last_seen_at': _normalize_datetime(args[10]) if _single_line(args[10], max_len=40) else '',
                'investigating_agency': _single_line(args[11], max_len=160),
                'aliases': _single_line(args[12], max_len=500),
                'photo_url': primary_image,
                'photo_gallery': gallery_urls,
                'is_indigenous': 1 if race == 'AMERICAN INDIAN OR ALASKAN NATIVE' else 0,
                'is_child': 1 if age_now is not None and age_now < 18 else 0,
            }
        )

    records.sort(key=lambda item: int(item['source_person_id']))
    return records


def fetch_official_missing_person_snapshot(
    *,
    session: requests.Session | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    http = session or requests.Session()
    headers = {
        'User-Agent': 'MontanaBlotterMissingPersonSync/1.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    }

    landing_html = http.get(OFFICIAL_SOURCE_BASE_URL, headers=headers, timeout=timeout).text
    results_html = http.get(OFFICIAL_SOURCE_RESULTS_URL, headers=headers, timeout=timeout).text
    indigenous_html = http.get(OFFICIAL_SOURCE_INDIGENOUS_URL, headers=headers, timeout=timeout).text
    children_html = http.get(OFFICIAL_SOURCE_CHILDREN_URL, headers=headers, timeout=timeout).text

    counts = _parse_official_counts(results_html)
    records = _parse_official_card_records(results_html)
    counts['total_active'] = max(int(counts.get('total_active') or 0), len(records))
    counts['indigenous'] = len(_parse_official_card_records(indigenous_html))
    counts['children'] = len(_parse_official_card_records(children_html))

    return {
        'source_name': OFFICIAL_SOURCE_NAME,
        'official_last_updated': _parse_official_tooltip_label(landing_html, 'Last Updated'),
        'official_last_checked': _parse_official_tooltip_label(landing_html, 'Last Checked'),
        'stats': counts,
        'records': records,
    }


def _infer_county_from_agency(agency: str) -> str:
    token = _single_line(agency, max_len=160)
    if ' COUNTY ' in f' {token} ':
        return _single_line(token.split(' COUNTY ', 1)[0].replace('/', ' '), max_len=80).title()
    return ''


def _infer_city_from_agency(agency: str) -> str:
    token = _single_line(agency, max_len=160)
    suffixes = [
        ' POLICE DEPARTMENT',
        ' COUNTY SHERIFF',
        ' COUNTY LED',
        ' COUNTY LEA',
        ' TRIBAL LAW ENFORCEMENT',
        ' LAW ENFORCEMENT SERVICES',
        ' INVESTIGATIVE SERVICES',
        ' LAW ENFORCEMENT',
    ]
    for suffix in suffixes:
        if token.endswith(suffix):
            value = token[: -len(suffix)].replace('/', ' ')
            value = re.sub(r'\s+', ' ', value).strip()
            if value and 'COUNTY' not in value:
                return value.title()
    return ''


def _build_official_summary(record: dict[str, Any]) -> str:
    parts = [
        'Listed as an active missing person in the official Montana Department of Justice database.',
    ]
    if record.get('last_seen_at'):
        parts.append(f"Date of last contact: {_display_datetime(record['last_seen_at'])}.")
    if record.get('investigating_agency'):
        parts.append(f"Investigating agency: {record['investigating_agency']}.")
    if record.get('age_now') is not None:
        parts.append(f"Current age listed: {record['age_now']}.")
    return ' '.join(parts)


def _build_official_physical_description(record: dict[str, Any]) -> str:
    parts: list[str] = []
    if record.get('gender'):
        parts.append(record['gender'].title())
    if record.get('race'):
        parts.append(record['race'].title())
    if record.get('hair_color'):
        parts.append(f"Hair: {record['hair_color'].title()}")
    if record.get('eye_color'):
        parts.append(f"Eyes: {record['eye_color'].title()}")
    if record.get('height_raw'):
        parts.append(f"Height: {_parse_height_label(record['height_raw'])}")
    if record.get('weight_lbs') is not None:
        parts.append(f"Weight: {record['weight_lbs']} lbs")
    aliases = _parse_aliases(record.get('aliases'))
    if aliases:
        parts.append(f"Aliases: {', '.join(aliases)}")
    return '. '.join(parts)


def _build_official_contact_info(record: dict[str, Any]) -> str:
    agency = _single_line(record.get('investigating_agency'), max_len=160)
    agency_sentence = f" and the investigating agency ({agency})" if agency else ''
    return (
        f"If you have information, contact the Montana Missing Persons Clearinghouse at "
        f"{OFFICIAL_CLEARINGHOUSE_PHONE} or {OFFICIAL_CLEARINGHOUSE_EMAIL}{agency_sentence}."
    )


def _upsert_missing_person_source_stats(conn: sqlite3.Connection, snapshot: dict[str, Any]) -> None:
    stats = snapshot.get('stats') or {}
    conn.execute(
        '''
        INSERT INTO missing_person_source_stats (
            id,
            source_name,
            total_active,
            children,
            indigenous,
            missing_less_than_year,
            missing_more_than_year,
            official_last_updated,
            official_last_checked,
            synced_at,
            updated_at
        ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        ON CONFLICT(id) DO UPDATE SET
            source_name = excluded.source_name,
            total_active = excluded.total_active,
            children = excluded.children,
            indigenous = excluded.indigenous,
            missing_less_than_year = excluded.missing_less_than_year,
            missing_more_than_year = excluded.missing_more_than_year,
            official_last_updated = excluded.official_last_updated,
            official_last_checked = excluded.official_last_checked,
            synced_at = datetime('now'),
            updated_at = datetime('now')
        ''',
        (
            snapshot.get('source_name') or OFFICIAL_SOURCE_NAME,
            int(stats.get('total_active') or 0),
            int(stats.get('children') or 0),
            int(stats.get('indigenous') or 0),
            int(stats.get('missing_less_than_year') or 0),
            int(stats.get('missing_more_than_year') or 0),
            _single_line(snapshot.get('official_last_updated'), max_len=120),
            _single_line(snapshot.get('official_last_checked'), max_len=120),
        ),
    )


def sync_official_missing_persons(
    conn: sqlite3.Connection,
    *,
    actor: str = 'official_sync',
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_missing_person_schema(conn)
    if snapshot is None:
        snapshot = fetch_official_missing_person_snapshot()

    records = snapshot.get('records') or []
    _upsert_missing_person_source_stats(conn, snapshot)

    existing_rows = conn.execute(
        '''
        SELECT *
        FROM missing_persons
        WHERE COALESCE(source_person_id, '') != ''
        '''
    ).fetchall()
    existing_by_source = {str(row['source_person_id']): row for row in existing_rows if row['source_person_id']}
    active_source_ids = {str(record['source_person_id']) for record in records if record.get('source_person_id')}

    created = 0
    updated = 0
    reactivated = 0
    resolved = 0
    new_records: list[dict[str, Any]] = []

    for record in records:
        source_person_id = str(record['source_person_id'])
        agency = record.get('investigating_agency') or ''
        county = _infer_county_from_agency(agency)
        city = _infer_city_from_agency(agency)
        last_seen_location = city or county or agency or 'See official state source'
        summary = _build_official_summary(record)
        physical_description = _build_official_physical_description(record)
        contact_info = _build_official_contact_info(record)
        gallery_json = json.dumps(record.get('photo_gallery') or [])
        source_url = _absolute_official_url(f'MissingPersonDetails.php?id={source_person_id}')
        case_number = _single_line(record.get('case_number'), max_len=80)
        missing_from = _single_line(record.get('missing_from'), max_len=240) or last_seen_location
        height_weight = _build_height_weight_value(
            height_raw=record.get('height_raw'),
            weight_lbs=record.get('weight_lbs'),
            fallback=record.get('height_weight'),
        )
        last_synced = _single_line(snapshot.get('official_last_checked'), max_len=120) or datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        existing = existing_by_source.get(source_person_id)

        if existing is None:
            cursor = conn.execute(
                '''
                INSERT INTO missing_persons (
                    slug,
                    full_name,
                    age,
                    city,
                    county,
                    last_seen_at,
                    last_seen_location,
                    summary,
                    physical_description,
                    contact_info,
                    source_name,
                    source_url,
                    photo_url,
                    case_number,
                    missing_from,
                    height_weight,
                    last_synced,
                    status,
                    is_active,
                    resolution_summary,
                    created_by,
                    updated_by,
                    source_person_id,
                    gender,
                    race,
                    hair_color,
                    eye_color,
                    height_raw,
                    weight_lbs,
                    age_missing,
                    investigating_agency,
                    aliases,
                    is_indigenous,
                    is_child,
                    photo_gallery_json,
                    official_last_updated,
                    official_last_checked,
                    created_at,
                    updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '',
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    datetime('now'), datetime('now')
                )
                ''',
                (
                    '',
                    record['full_name'],
                    record.get('age_now'),
                    city,
                    county,
                    record.get('last_seen_at') or '',
                    last_seen_location,
                    summary,
                    physical_description,
                    contact_info,
                    OFFICIAL_SOURCE_NAME,
                    source_url,
                    record.get('photo_url') or '',
                    case_number,
                    missing_from,
                    height_weight,
                    last_synced,
                    STATUS_MISSING,
                    _status_to_is_active(STATUS_MISSING),
                    actor,
                    actor,
                    source_person_id,
                    record.get('gender') or '',
                    record.get('race') or '',
                    record.get('hair_color') or '',
                    record.get('eye_color') or '',
                    record.get('height_raw') or '',
                    record.get('weight_lbs'),
                    record.get('age_missing'),
                    agency,
                    record.get('aliases') or '',
                    int(record.get('is_indigenous') or 0),
                    int(record.get('is_child') or 0),
                    gallery_json,
                    _single_line(snapshot.get('official_last_updated'), max_len=120),
                    _single_line(snapshot.get('official_last_checked'), max_len=120),
                ),
            )
            person_id = int(cursor.lastrowid)
            conn.execute(
                'UPDATE missing_persons SET slug = ? WHERE id = ?',
                (f'{_slugify(record["full_name"])}-{person_id}', person_id),
            )
            created += 1
            new_person = get_missing_person_by_id(conn, person_id)
            if new_person:
                new_records.append(new_person)
            continue

        was_active = existing['status'] == STATUS_MISSING
        if not was_active:
            reactivated += 1

        conn.execute(
            '''
            UPDATE missing_persons
            SET full_name = ?,
                age = ?,
                city = ?,
                county = ?,
                last_seen_at = ?,
                last_seen_location = ?,
                summary = ?,
                physical_description = ?,
                contact_info = ?,
                source_name = ?,
                source_url = ?,
                photo_url = ?,
                case_number = ?,
                missing_from = ?,
                height_weight = ?,
                last_synced = ?,
                status = ?,
                is_active = ?,
                resolution_summary = '',
                resolved_at = '',
                updated_by = ?,
                gender = ?,
                race = ?,
                hair_color = ?,
                eye_color = ?,
                height_raw = ?,
                weight_lbs = ?,
                age_missing = ?,
                investigating_agency = ?,
                aliases = ?,
                is_indigenous = ?,
                is_child = ?,
                photo_gallery_json = ?,
                official_last_updated = ?,
                official_last_checked = ?,
                updated_at = datetime('now')
            WHERE id = ?
            ''',
            (
                record['full_name'],
                record.get('age_now'),
                city,
                county,
                record.get('last_seen_at') or '',
                last_seen_location,
                summary,
                physical_description,
                contact_info,
                OFFICIAL_SOURCE_NAME,
                source_url,
                record.get('photo_url') or '',
                case_number,
                missing_from,
                height_weight,
                last_synced,
                STATUS_MISSING,
                _status_to_is_active(STATUS_MISSING),
                actor,
                record.get('gender') or '',
                record.get('race') or '',
                record.get('hair_color') or '',
                record.get('eye_color') or '',
                record.get('height_raw') or '',
                record.get('weight_lbs'),
                record.get('age_missing'),
                agency,
                record.get('aliases') or '',
                int(record.get('is_indigenous') or 0),
                int(record.get('is_child') or 0),
                gallery_json,
                _single_line(snapshot.get('official_last_updated'), max_len=120),
                _single_line(snapshot.get('official_last_checked'), max_len=120),
                int(existing['id']),
            ),
        )
        updated += 1
        if not was_active:
            reactivated_person = get_missing_person_by_id(conn, int(existing['id']))
            if reactivated_person:
                new_records.append(reactivated_person)

    active_existing_rows = conn.execute(
        '''
        SELECT id, source_person_id, full_name
        FROM missing_persons
        WHERE status = ?
          AND COALESCE(source_person_id, '') != ''
        ''',
        (STATUS_MISSING,),
    ).fetchall()
    for row in active_existing_rows:
        source_person_id = str(row['source_person_id'])
        if source_person_id in active_source_ids:
            continue
        resolved += 1
        conn.execute(
            '''
            UPDATE missing_persons
            SET status = ?,
                is_active = ?,
                last_synced = ?,
                resolution_summary = ?,
                resolved_at = CASE WHEN COALESCE(resolved_at, '') = '' THEN datetime('now') ELSE resolved_at END,
                updated_by = ?,
                updated_at = datetime('now')
            WHERE id = ?
            ''',
            (
                STATUS_LOCATED,
                _status_to_is_active(STATUS_LOCATED),
                _single_line(snapshot.get('official_last_checked'), max_len=120) or datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
                f'No longer listed in the official Montana DOJ Missing Persons Database as of {snapshot.get("official_last_checked") or "the latest sync"}.',
                actor,
                int(row['id']),
            ),
        )

    return {
        'source_name': snapshot.get('source_name') or OFFICIAL_SOURCE_NAME,
        'official_last_updated': snapshot.get('official_last_updated') or '',
        'official_last_checked': snapshot.get('official_last_checked') or '',
        'active_total': int((snapshot.get('stats') or {}).get('total_active') or len(records)),
        'created': created,
        'updated': updated,
        'reactivated': reactivated,
        'resolved': resolved,
        'new_records': new_records,
        'stats': snapshot.get('stats') or {},
    }


def build_missing_person_subject(person: dict[str, Any]) -> str:
    county = _single_line(person.get('county'), max_len=120)
    if county:
        return f"Missing Person Alert: {person['full_name']} — {county} County"
    return f"Missing Person Alert: {person['full_name']}"


def build_missing_person_email_html(person: dict[str, Any], *, unsubscribe_url: str = '') -> str:
    detail_url = f"{BASE_URL}{person['public_href']}"
    headline = escape(person['full_name'])
    county_label = escape(person.get('county') or 'Montana')
    location_label = escape(person.get('search_location') or person.get('last_seen_location') or 'Location pending')
    summary = escape(person.get('summary') or '')
    physical_description = escape(person.get('physical_description') or '')
    contact_info = escape(person.get('contact_info') or '')
    source_name = escape(person.get('source_name') or 'Montana Blotter')
    body = f"""
    <div style="font-family:Arial,sans-serif;max-width:640px;margin:0 auto;background:#fff7ed;border:1px solid #fdba74;border-radius:18px;overflow:hidden;">
        <div style="background:#7c2d12;color:#ffffff;padding:28px 28px 24px;">
            <p style="margin:0;color:#fed7aa;font-size:12px;font-weight:700;letter-spacing:0.18em;text-transform:uppercase;">Montana Blotter Missing Person Alert</p>
            <h2 style="margin:14px 0 0;font-size:28px;line-height:1.15;">{headline}</h2>
            <p style="margin:14px 0 0;color:#ffedd5;font-size:14px;line-height:1.6;">Active public alert for {county_label} County.</p>
        </div>
        <div style="padding:28px;background:#ffffff;">
            <p style="margin:0;color:#0f172a;font-size:16px;line-height:1.7;">{summary}</p>
            <div style="margin-top:18px;padding:16px;border-radius:14px;background:#fff7ed;border:1px solid #fed7aa;">
                <p style="margin:0;color:#9a3412;font-size:12px;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;">Last Seen</p>
                <p style="margin:8px 0 0;color:#431407;font-size:15px;font-weight:700;">{location_label}</p>
                <p style="margin:6px 0 0;color:#7c2d12;font-size:14px;">{escape(person.get('last_seen_at_label') or 'Not provided')}</p>
            </div>
    """
    if physical_description:
        body += f'<p style="margin:18px 0 0;color:#334155;line-height:1.7;"><strong>Physical description:</strong> {physical_description}</p>'
    if contact_info:
        body += f'<p style="margin:14px 0 0;color:#334155;line-height:1.7;"><strong>How to help:</strong> {contact_info}</p>'
    body += (
        f'<p style="margin:22px 0 0;">'
        f'<a href="{escape(detail_url)}" style="display:inline-block;background:#c2410c;color:#ffffff;padding:12px 16px;border-radius:8px;text-decoration:none;font-weight:700;">'
        f'Open missing-person record</a></p>'
        f'<hr style="border:none;border-top:1px solid #fdba74;margin:24px 0;">'
        f'<p style="margin:0;color:#7c2d12;font-size:13px;line-height:1.6;">Source: {source_name}</p>'
    )
    if unsubscribe_url:
        body += (
            f'<p style="margin:12px 0 0;color:#9a3412;font-size:12px;">'
            f'<a href="{escape(unsubscribe_url)}" style="color:#9a3412;">Unsubscribe</a>'
            f'</p>'
        )
    body += '</div></div>'
    return body


def dispatch_missing_person_alerts(
    conn: sqlite3.Connection,
    person: dict[str, Any],
    *,
    send_email=None,
) -> dict[str, int]:
    ensure_missing_person_schema(conn)
    if person.get('status') != STATUS_MISSING:
        return {'subscribers': 0, 'sent': 0, 'failed': 0, 'skipped': 0}

    if send_email is None:
        send_email = send_digest_email

    subscribers = conn.execute(
        '''
        SELECT email, token
        FROM subscribers
        WHERE COALESCE(active, 1) = 1
          AND trim(COALESCE(email, '')) != ''
        ORDER BY datetime(created_at) DESC, email ASC
        '''
    ).fetchall()

    sent = 0
    failed = 0
    skipped = 0
    version = int(person.get('notification_version') or 1)
    subject = build_missing_person_subject(person)
    for subscriber in subscribers:
        recipient_email = _single_line(subscriber['email'], max_len=160).lower()
        if not recipient_email or '@' not in recipient_email:
            skipped += 1
            continue
        try:
            conn.execute(
                '''
                INSERT INTO missing_person_alert_deliveries (
                    missing_person_id,
                    notification_version,
                    recipient_email,
                    delivery_status
                ) VALUES (?, ?, ?, 'queued')
                ''',
                (int(person['id']), version, recipient_email),
            )
        except sqlite3.IntegrityError:
            skipped += 1
            continue

        unsubscribe_url = f"{BASE_URL}/unsubscribe?token={subscriber['token']}" if subscriber['token'] else ''
        html = build_missing_person_email_html(person, unsubscribe_url=unsubscribe_url)
        delivery_status = 'sent'
        error_message = ''
        try:
            result = send_email(recipient_email, subject, html)
            if result is False:
                raise RuntimeError('email_sender_returned_false')
            sent += 1
        except Exception as exc:
            failed += 1
            delivery_status = 'failed'
            error_message = _single_line(exc, max_len=400)

        conn.execute(
            '''
            UPDATE missing_person_alert_deliveries
            SET delivery_status = ?, error_message = ?
            WHERE missing_person_id = ?
              AND notification_version = ?
              AND recipient_email = ?
            ''',
            (delivery_status, error_message, int(person['id']), version, recipient_email),
        )

    if sent:
        conn.execute(
            '''
            UPDATE missing_persons
            SET last_alerted_at = datetime('now'),
                alert_delivery_count = COALESCE(alert_delivery_count, 0) + ?
            WHERE id = ?
            ''',
            (sent, int(person['id'])),
        )

    return {
        'subscribers': len(subscribers),
        'sent': sent,
        'failed': failed,
        'skipped': skipped,
    }
