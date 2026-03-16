import hashlib
import re
import sqlite3
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta


_NON_ALNUM_RE = re.compile(r'[^a-z0-9]+')


def _parse_timestamp(value: str) -> datetime | None:
    raw = (value or '').strip()
    if not raw:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _slugify(value: str) -> str:
    return _NON_ALNUM_RE.sub('-', (value or '').strip().lower()).strip('-')


def _case_slug(case_number: str, caption: str, court_slug: str) -> str:
    base = '-'.join(
        token
        for token in (
            _slugify(court_slug)[:36],
            _slugify(case_number)[:40] or _slugify(caption)[:40],
        )
        if token
    )
    if base:
        return base
    raw = '||'.join((case_number or '', caption or '', court_slug or ''))
    return f"case-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]}"


def ensure_court_tracker_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS court_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            provider_type TEXT NOT NULL DEFAULT 'custom',
            source_url TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            last_scraped_at TEXT,
            last_success_at TEXT,
            last_error TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        '''
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS courts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER,
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            court_type TEXT NOT NULL,
            county TEXT NOT NULL,
            city TEXT,
            portal_url TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (source_id) REFERENCES court_sources(id) ON DELETE SET NULL
        )
        '''
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS court_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            court_id INTEGER NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            external_case_id TEXT,
            case_number TEXT NOT NULL,
            caption TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            filed_date TEXT,
            case_type TEXT,
            source_url TEXT,
            last_seen_at TEXT DEFAULT (datetime('now')),
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (court_id) REFERENCES courts(id) ON DELETE CASCADE,
            UNIQUE (court_id, case_number)
        )
        '''
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS court_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            event_date TEXT NOT NULL,
            event_time TEXT,
            event_title TEXT NOT NULL,
            judge TEXT,
            room TEXT,
            notes TEXT,
            source_url TEXT,
            source_hash TEXT,
            last_seen_at TEXT DEFAULT (datetime('now')),
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (case_id) REFERENCES court_cases(id) ON DELETE CASCADE
        )
        '''
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS court_filings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id INTEGER NOT NULL,
            filing_date TEXT NOT NULL,
            filing_title TEXT NOT NULL,
            document_number TEXT,
            source_url TEXT,
            source_hash TEXT,
            last_seen_at TEXT DEFAULT (datetime('now')),
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (case_id) REFERENCES court_cases(id) ON DELETE CASCADE
        )
        '''
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS court_case_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id INTEGER NOT NULL,
            snapshot_json TEXT NOT NULL,
            captured_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (case_id) REFERENCES court_cases(id) ON DELETE CASCADE
        )
        '''
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS court_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            alert_type TEXT NOT NULL,
            query_json TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        '''
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS court_alert_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id INTEGER NOT NULL,
            case_id INTEGER,
            event_id INTEGER,
            filing_id INTEGER,
            sent_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (alert_id) REFERENCES court_alerts(id) ON DELETE CASCADE,
            FOREIGN KEY (case_id) REFERENCES court_cases(id) ON DELETE SET NULL,
            FOREIGN KEY (event_id) REFERENCES court_events(id) ON DELETE SET NULL,
            FOREIGN KEY (filing_id) REFERENCES court_filings(id) ON DELETE SET NULL
        )
        '''
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS court_source_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_slug TEXT NOT NULL,
            alert_kind TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'open',
            summary TEXT,
            first_detected_at TEXT DEFAULT (datetime('now')),
            last_detected_at TEXT DEFAULT (datetime('now')),
            last_sent_at TEXT,
            resolved_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        '''
    )
    conn.execute('CREATE INDEX IF NOT EXISTS idx_court_sources_status ON court_sources(status, last_success_at)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_courts_county_type ON courts(county, court_type, active)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_court_cases_court ON court_cases(court_id, status, filed_date)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_court_cases_case_type ON court_cases(case_type, filed_date)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_court_events_feed ON court_events(event_date, event_time, event_type)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_court_events_case ON court_events(case_id, event_date, event_time)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_court_filings_case ON court_filings(case_id, filing_date)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_court_alerts_active ON court_alerts(active, alert_type)')
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_court_source_alerts_open '
        'ON court_source_alerts(source_slug, alert_kind, state)'
    )


def upsert_court_source(
    conn: sqlite3.Connection,
    *,
    slug: str,
    name: str,
    source_url: str,
    provider_type: str = 'custom',
    status: str = 'active',
) -> int:
    ensure_court_tracker_schema(conn)
    normalized_slug = _slugify(slug) or 'court-source'
    conn.execute(
        '''
        INSERT INTO court_sources (slug, name, provider_type, source_url, status)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(slug) DO UPDATE SET
            name = excluded.name,
            provider_type = excluded.provider_type,
            source_url = excluded.source_url,
            status = excluded.status,
            updated_at = datetime('now')
        ''',
        (normalized_slug, name.strip(), provider_type.strip() or 'custom', source_url.strip(), status.strip() or 'active'),
    )
    row = conn.execute('SELECT id FROM court_sources WHERE slug = ?', (normalized_slug,)).fetchone()
    return int(row['id'])


def upsert_court(
    conn: sqlite3.Connection,
    *,
    source_id: int | None,
    slug: str,
    name: str,
    court_type: str,
    county: str,
    city: str = '',
    portal_url: str = '',
) -> int:
    ensure_court_tracker_schema(conn)
    normalized_slug = _slugify(slug) or _slugify(name) or 'montana-court'
    conn.execute(
        '''
        INSERT INTO courts (source_id, slug, name, court_type, county, city, portal_url, active)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        ON CONFLICT(slug) DO UPDATE SET
            source_id = excluded.source_id,
            name = excluded.name,
            court_type = excluded.court_type,
            county = excluded.county,
            city = excluded.city,
            portal_url = excluded.portal_url,
            active = 1,
            updated_at = datetime('now')
        ''',
        (source_id, normalized_slug, name.strip(), court_type.strip(), county.strip(), city.strip(), portal_url.strip()),
    )
    row = conn.execute('SELECT id FROM courts WHERE slug = ?', (normalized_slug,)).fetchone()
    return int(row['id'])


def upsert_court_case(
    conn: sqlite3.Connection,
    *,
    court_id: int,
    case_number: str,
    caption: str,
    status: str = 'open',
    filed_date: str = '',
    case_type: str = '',
    source_url: str = '',
    external_case_id: str = '',
) -> int:
    ensure_court_tracker_schema(conn)
    court_row = conn.execute('SELECT slug FROM courts WHERE id = ?', (court_id,)).fetchone()
    court_slug = court_row['slug'] if court_row else ''
    slug = _case_slug(case_number, caption, court_slug)
    conn.execute(
        '''
        INSERT INTO court_cases (
            court_id, slug, external_case_id, case_number, caption, status,
            filed_date, case_type, source_url, last_seen_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(court_id, case_number) DO UPDATE SET
            slug = excluded.slug,
            external_case_id = excluded.external_case_id,
            caption = excluded.caption,
            status = excluded.status,
            filed_date = excluded.filed_date,
            case_type = excluded.case_type,
            source_url = excluded.source_url,
            last_seen_at = datetime('now'),
            updated_at = datetime('now')
        ''',
        (
            court_id,
            slug,
            external_case_id.strip(),
            case_number.strip(),
            caption.strip(),
            status.strip() or 'open',
            filed_date.strip(),
            case_type.strip(),
            source_url.strip(),
        ),
    )
    row = conn.execute(
        'SELECT id FROM court_cases WHERE court_id = ? AND case_number = ?',
        (court_id, case_number.strip()),
    ).fetchone()
    return int(row['id'])


def add_court_event(
    conn: sqlite3.Connection,
    *,
    case_id: int,
    event_type: str,
    event_date: str,
    event_title: str,
    event_time: str = '',
    judge: str = '',
    room: str = '',
    notes: str = '',
    source_url: str = '',
) -> int:
    ensure_court_tracker_schema(conn)
    exact_match = conn.execute(
        '''
        SELECT id
        FROM court_events
        WHERE case_id = ?
          AND event_type = ?
          AND event_date = ?
          AND COALESCE(event_time, '') = ?
          AND COALESCE(source_url, '') = ?
        ''',
        (
            case_id,
            event_type.strip(),
            event_date.strip(),
            event_time.strip(),
            source_url.strip(),
        ),
    ).fetchone()
    source_hash = hashlib.sha1(
        '||'.join(
            (
                str(case_id),
                event_type.strip().lower(),
                event_date.strip(),
                event_time.strip(),
                event_title.strip().lower(),
                judge.strip().lower(),
                room.strip().lower(),
            )
        ).encode('utf-8')
    ).hexdigest()[:24]
    if exact_match:
        conn.execute(
            '''
            UPDATE court_events
            SET event_title = ?,
                judge = ?,
                room = ?,
                notes = ?,
                source_url = ?,
                source_hash = ?,
                last_seen_at = datetime('now')
            WHERE id = ?
            ''',
            (
                event_title.strip(),
                judge.strip(),
                room.strip(),
                notes.strip(),
                source_url.strip(),
                source_hash,
                exact_match['id'],
            ),
        )
        return int(exact_match['id'])
    existing = conn.execute(
        'SELECT id FROM court_events WHERE case_id = ? AND source_hash = ?',
        (case_id, source_hash),
    ).fetchone()
    if existing:
        conn.execute(
            '''
            UPDATE court_events
            SET notes = ?, source_url = ?, last_seen_at = datetime('now')
            WHERE id = ?
            ''',
            (notes.strip(), source_url.strip(), existing['id']),
        )
        return int(existing['id'])
    cursor = conn.execute(
        '''
        INSERT INTO court_events (
            case_id, event_type, event_date, event_time, event_title,
            judge, room, notes, source_url, source_hash, last_seen_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ''',
        (
            case_id,
            event_type.strip(),
            event_date.strip(),
            event_time.strip(),
            event_title.strip(),
            judge.strip(),
            room.strip(),
            notes.strip(),
            source_url.strip(),
            source_hash,
        ),
    )
    return int(cursor.lastrowid or 0)


def add_court_filing(
    conn: sqlite3.Connection,
    *,
    case_id: int,
    filing_date: str,
    filing_title: str,
    document_number: str = '',
    source_url: str = '',
) -> int:
    ensure_court_tracker_schema(conn)
    source_hash = hashlib.sha1(
        '||'.join(
            (
                str(case_id),
                filing_date.strip(),
                filing_title.strip().lower(),
                document_number.strip().lower(),
            )
        ).encode('utf-8')
    ).hexdigest()[:24]
    existing = conn.execute(
        'SELECT id FROM court_filings WHERE case_id = ? AND source_hash = ?',
        (case_id, source_hash),
    ).fetchone()
    if existing:
        conn.execute(
            '''
            UPDATE court_filings
            SET source_url = ?, last_seen_at = datetime('now')
            WHERE id = ?
            ''',
            (source_url.strip(), existing['id']),
        )
        return int(existing['id'])
    cursor = conn.execute(
        '''
        INSERT INTO court_filings (
            case_id, filing_date, filing_title, document_number, source_url, source_hash, last_seen_at
        )
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        ''',
        (
            case_id,
            filing_date.strip(),
            filing_title.strip(),
            document_number.strip(),
            source_url.strip(),
            source_hash,
        ),
    )
    return int(cursor.lastrowid or 0)


def court_directory_context(conn: sqlite3.Connection, *, q: str = '', county: str = '', court_type: str = '') -> dict:
    ensure_court_tracker_schema(conn)
    query = (q or '').strip()
    selected_county = (county or '').strip()
    selected_type = (court_type or '').strip().lower()
    sql = '''
        SELECT
            courts.*,
            court_sources.name AS source_name,
            court_sources.last_success_at,
            COUNT(DISTINCT court_cases.id) AS case_count,
            MIN(CASE WHEN court_events.event_date >= date('now') THEN court_events.event_date END) AS next_hearing_date
        FROM courts
        LEFT JOIN court_sources ON court_sources.id = courts.source_id
        LEFT JOIN court_cases ON court_cases.court_id = courts.id
        LEFT JOIN court_events ON court_events.case_id = court_cases.id
        WHERE courts.active = 1
    '''
    params = []
    if query:
        token = f'%{query}%'
        sql += ' AND (courts.name LIKE ? OR courts.county LIKE ? OR COALESCE(courts.city, \'\') LIKE ?)'
        params.extend([token, token, token])
    if selected_county:
        sql += ' AND courts.county = ?'
        params.append(selected_county)
    if selected_type:
        sql += ' AND lower(courts.court_type) = ?'
        params.append(selected_type)
    sql += '''
        GROUP BY courts.id
        ORDER BY courts.county ASC, courts.name ASC
    '''
    courts = [dict(row) for row in conn.execute(sql, params).fetchall()]
    summary = conn.execute(
        '''
        SELECT
            COUNT(*) AS court_count,
            COUNT(DISTINCT county) AS county_count
        FROM courts
        WHERE active = 1
        '''
    ).fetchone()
    upcoming_count = conn.execute(
        '''
        SELECT COUNT(*) AS total
        FROM court_events
        WHERE event_date >= date('now')
        '''
    ).fetchone()
    counties = [row['county'] for row in conn.execute('SELECT DISTINCT county FROM courts WHERE county != \'\' ORDER BY county').fetchall()]
    court_types = [row['court_type'] for row in conn.execute('SELECT DISTINCT court_type FROM courts WHERE court_type != \'\' ORDER BY court_type').fetchall()]
    return {
        'courts': courts,
        'court_count': int(summary['court_count'] or 0),
        'county_count': int(summary['county_count'] or 0),
        'upcoming_hearing_count': int(upcoming_count['total'] or 0),
        'counties': counties,
        'court_types': court_types,
        'selected_county': selected_county,
        'selected_court_type': selected_type,
        'q': query,
    }


def court_hearing_feed_context(
    conn: sqlite3.Connection,
    *,
    q: str = '',
    county: str = '',
    court_type: str = '',
    hearing_date: str = '',
    include_past: bool = False,
) -> dict:
    ensure_court_tracker_schema(conn)
    query = (q or '').strip()
    selected_county = (county or '').strip()
    selected_type = (court_type or '').strip().lower()
    selected_date = (hearing_date or '').strip()
    event_sql = '''
        SELECT
            'event' AS record_kind,
            court_events.*,
            court_cases.slug AS case_slug,
            court_cases.case_number,
            court_cases.caption,
            court_cases.case_type,
            courts.name AS court_name,
            courts.court_type,
            courts.county,
            courts.city
        FROM court_events
        JOIN court_cases ON court_cases.id = court_events.case_id
        JOIN courts ON courts.id = court_cases.court_id
        WHERE court_events.event_date != ''
    '''
    params = []
    if query:
        token = f'%{query}%'
        event_sql += '''
            AND (
                court_cases.case_number LIKE ?
                OR court_cases.caption LIKE ?
                OR COALESCE(court_events.event_title, '') LIKE ?
                OR COALESCE(court_events.notes, '') LIKE ?
            )
        '''
        params.extend([token, token, token, token])
    if selected_county:
        event_sql += ' AND courts.county = ?'
        params.append(selected_county)
    if selected_type:
        event_sql += ' AND lower(courts.court_type) = ?'
        params.append(selected_type)
    if selected_date:
        event_sql += ' AND court_events.event_date = ?'
        params.append(selected_date)
    elif not include_past:
        event_sql += ' AND court_events.event_date >= date(\'now\')'
    records = [dict(row) for row in conn.execute(event_sql, params).fetchall()]

    if include_past:
        filing_sql = '''
            SELECT
                'filing' AS record_kind,
                NULL AS id,
                court_cases.id AS case_id,
                'Opinion Filing' AS event_type,
                court_filings.filing_date AS event_date,
                '' AS event_time,
                court_filings.filing_title AS event_title,
                '' AS judge,
                '' AS room,
                '' AS notes,
                court_filings.source_url AS source_url,
                court_filings.source_hash AS source_hash,
                court_filings.last_seen_at,
                court_filings.created_at,
                court_cases.slug AS case_slug,
                court_cases.case_number,
                court_cases.caption,
                court_cases.case_type,
                courts.name AS court_name,
                courts.court_type,
                courts.county,
                courts.city
            FROM court_filings
            JOIN court_cases ON court_cases.id = court_filings.case_id
            JOIN courts ON courts.id = court_cases.court_id
            WHERE court_filings.filing_date != ''
        '''
        filing_params = []
        if query:
            token = f'%{query}%'
            filing_sql += '''
                AND (
                    court_cases.case_number LIKE ?
                    OR court_cases.caption LIKE ?
                    OR COALESCE(court_filings.filing_title, '') LIKE ?
                )
            '''
            filing_params.extend([token, token, token])
        if selected_county:
            filing_sql += ' AND courts.county = ?'
            filing_params.append(selected_county)
        if selected_type:
            filing_sql += ' AND lower(courts.court_type) = ?'
            filing_params.append(selected_type)
        if selected_date:
            filing_sql += ' AND court_filings.filing_date = ?'
            filing_params.append(selected_date)
        records.extend(dict(row) for row in conn.execute(filing_sql, filing_params).fetchall())

    def _record_sort_key(item: dict) -> tuple[str, str, str, str]:
        return (
            item.get('event_date') or '',
            item.get('event_time') or '',
            item.get('county') or '',
            item.get('court_name') or '',
        )

    descending = include_past and not selected_date
    records.sort(key=_record_sort_key, reverse=descending)
    hearings = records[:200]
    counties = [row['county'] for row in conn.execute('SELECT DISTINCT county FROM courts WHERE county != \'\' ORDER BY county').fetchall()]
    court_types = [row['court_type'] for row in conn.execute('SELECT DISTINCT court_type FROM courts WHERE court_type != \'\' ORDER BY court_type').fetchall()]
    return {
        'hearings': hearings,
        'hearing_count': len(hearings),
        'counties': counties,
        'court_types': court_types,
        'selected_county': selected_county,
        'selected_court_type': selected_type,
        'selected_date': selected_date,
        'include_past': include_past,
        'q': query,
    }


def court_case_detail(conn: sqlite3.Connection, slug: str) -> dict | None:
    ensure_court_tracker_schema(conn)
    row = conn.execute(
        '''
        SELECT
            court_cases.*,
            courts.name AS court_name,
            courts.court_type,
            courts.county,
            courts.city,
            courts.portal_url,
            court_sources.name AS source_name,
            court_sources.source_url AS source_listing_url,
            court_sources.last_success_at
        FROM court_cases
        JOIN courts ON courts.id = court_cases.court_id
        LEFT JOIN court_sources ON court_sources.id = courts.source_id
        WHERE court_cases.slug = ?
        ''',
        ((slug or '').strip(),),
    ).fetchone()
    if not row:
        return None
    case = dict(row)
    case['events'] = [dict(item) for item in conn.execute(
        '''
        SELECT *
        FROM court_events
        WHERE case_id = ?
        ORDER BY event_date ASC, COALESCE(event_time, ''), id ASC
        ''',
        (case['id'],),
    ).fetchall()]
    case['filings'] = [dict(item) for item in conn.execute(
        '''
        SELECT *
        FROM court_filings
        WHERE case_id = ?
        ORDER BY filing_date DESC, id DESC
        ''',
        (case['id'],),
    ).fetchall()]
    case['next_hearing'] = next(
        (event for event in case['events'] if (event.get('event_date') or '') >= date.today().isoformat()),
        None,
    )
    return case


def court_admin_context(conn: sqlite3.Connection) -> dict:
    ensure_court_tracker_schema(conn)
    now = datetime.now(UTC).replace(tzinfo=None)
    stale_cutoff = now - timedelta(hours=12)
    summary = conn.execute(
        '''
        SELECT
            (SELECT COUNT(*) FROM court_sources) AS source_count,
            (SELECT COUNT(*) FROM courts WHERE active = 1) AS court_count,
            (SELECT COUNT(*) FROM court_cases) AS case_count,
            (SELECT COUNT(*) FROM court_events WHERE event_date >= date('now')) AS upcoming_hearings,
            (SELECT COUNT(*) FROM court_filings) AS filing_count
        '''
    ).fetchone()
    sources = [dict(row) for row in conn.execute(
        '''
        SELECT
            court_sources.*,
            COUNT(DISTINCT courts.id) AS court_count,
            COUNT(DISTINCT court_cases.id) AS case_count,
            COUNT(DISTINCT court_filings.id) AS filing_count,
            COUNT(DISTINCT CASE WHEN court_events.event_date >= date('now') THEN court_events.id END) AS upcoming_hearing_count
        FROM court_sources
        LEFT JOIN courts ON courts.source_id = court_sources.id
        LEFT JOIN court_cases ON court_cases.court_id = courts.id
        LEFT JOIN court_events ON court_events.case_id = court_cases.id
        LEFT JOIN court_filings ON court_filings.case_id = court_cases.id
        GROUP BY court_sources.id
        ORDER BY court_sources.name ASC
        '''
    ).fetchall()]
    health_counts = {
        'healthy': 0,
        'stale': 0,
        'error': 0,
        'never': 0,
    }
    for source in sources:
        last_success = _parse_timestamp(source.get('last_success_at') or '')
        last_scraped = _parse_timestamp(source.get('last_scraped_at') or '')
        if source.get('last_error'):
            health_state = 'error'
        elif not last_success:
            health_state = 'never'
        elif last_success < stale_cutoff:
            health_state = 'stale'
        else:
            health_state = 'healthy'
        source['health_state'] = health_state
        source['last_seen_at'] = source.get('last_success_at') or source.get('last_scraped_at') or ''
        source['is_stale'] = bool(last_success and last_success < stale_cutoff)
        source['has_recent_scrape'] = bool(last_scraped and last_scraped >= stale_cutoff)
        health_counts[health_state] += 1
    by_county = defaultdict(int)
    for row in conn.execute('SELECT county FROM courts WHERE active = 1').fetchall():
        by_county[row['county']] += 1
    county_rollup = [{'county': county, 'court_count': total} for county, total in sorted(by_county.items())]
    active_alerts = [dict(row) for row in conn.execute(
        '''
        SELECT *
        FROM court_source_alerts
        WHERE state = 'open'
        ORDER BY
            CASE alert_kind WHEN 'error' THEN 0 WHEN 'stale' THEN 1 ELSE 2 END,
            COALESCE(last_detected_at, updated_at) DESC,
            id DESC
        '''
    ).fetchall()]
    return {
        'summary': dict(summary),
        'sources': sources,
        'county_rollup': county_rollup,
        'health_counts': health_counts,
        'stale_cutoff_hours': 12,
        'active_alerts': active_alerts,
    }
