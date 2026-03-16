import hashlib
import re
import sqlite3
from datetime import UTC, date, datetime, timedelta

from agendas_scraper.config import CityScrapeConfig
from agendas_scraper.models import MeetingRecord


def _slugify(value: str) -> str:
    cleaned = ''.join(ch.lower() if ch.isalnum() else '-' for ch in (value or '').strip())
    while '--' in cleaned:
        cleaned = cleaned.replace('--', '-')
    return cleaned.strip('-')


def _humanize_slug(value: str) -> str:
    tokens = [token for token in (value or '').replace('_', '-').split('-') if token]
    if not tokens:
        return 'Montana'
    return ' '.join(token.capitalize() for token in tokens)


def _normalized_scope(config: CityScrapeConfig) -> str:
    raw = (
        config.metadata.get('meeting_scope')
        or config.metadata.get('meeting_type')
        or 'city'
    )
    scope = str(raw).strip().lower()
    if scope not in {'city', 'county'}:
        return 'city'
    return scope


def _resolved_location_metadata(config: CityScrapeConfig) -> dict[str, str]:
    scope = _normalized_scope(config)
    raw_slug = str(config.metadata.get('location_slug') or config.slug).strip()
    slug = _slugify(raw_slug) or _slugify(config.slug) or 'montana'

    raw_location_name = (
        config.metadata.get('location_name')
        or config.metadata.get('city_name')
        or config.metadata.get('county_name')
        or _humanize_slug(slug)
    )
    location_name = str(raw_location_name).strip() or _humanize_slug(slug)

    county_name = str(config.metadata.get('county_name') or '').strip()
    city_name = str(config.metadata.get('city_name') or '').strip()
    if scope == 'county' and not county_name:
        county_name = location_name.replace(' County', '').strip()
    if scope == 'city' and not city_name:
        city_name = location_name

    return {
        'slug': slug,
        'display_name': location_name,
        'location_type': scope,
        'county_name': county_name,
        'city_name': city_name,
    }


def _parse_meeting_start(value: str) -> tuple[str, str]:
    raw = ' '.join((value or '').strip().split())
    if not raw:
        return ('', '')
    raw = re.split(r'\s+[—-]\s+(?:Amended|Posted)\b', raw, maxsplit=1)[0].strip()
    raw = re.sub(r'(?<=\d)(st|nd|rd|th)\b', '', raw, flags=re.IGNORECASE)
    raw = raw.replace('@', ' at ')

    fmts = (
        '%Y-%m-%d',
        '%Y-%m-%d %H:%M',
        '%Y-%m-%d %H:%M:%S',
        '%m/%d/%y',
        '%m/%d/%Y',
        '%m/%d/%y - %I:%M%p',
        '%m/%d/%Y - %I:%M%p',
        '%m/%d/%y - %I:%M %p',
        '%m/%d/%Y - %I:%M %p',
        '%m/%d/%y %I:%M %p',
        '%m/%d/%Y %I:%M %p',
        '%m/%d/%y %I:%M%p',
        '%m/%d/%Y %I:%M%p',
        '%m/%d/%y %H:%M',
        '%m/%d/%Y %H:%M',
        '%B %d, %Y',
        '%B %d, %Y at %I:%M %p',
        '%B %d, %Y %I:%M %p',
        '%b %d, %Y',
        '%b %d, %Y at %I:%M %p',
        '%b %d, %Y %I:%M %p',
    )

    candidates = [raw]
    patterns = (
        r'(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+([A-Za-z]{3,9}\s+\d{1,2},\s*\d{4}(?:\s+at\s+\d{1,2}:\d{2}\s*[APMapm]{2})?)',
        r'([A-Za-z]{3,9}\s+\d{1,2},\s*\d{4}(?:\s+at\s+\d{1,2}:\d{2}\s*[APMapm]{2})?)',
        r'(\d{1,2}/\d{1,2}/\d{2,4}(?:\s*-\s*\d{1,2}:\d{2}\s*[APMapm]{2})?)',
    )
    for pattern in patterns:
        for match in re.findall(pattern, raw, flags=re.IGNORECASE):
            candidate = ' '.join(match.split())
            if candidate not in candidates:
                candidates.append(candidate)

    for candidate in candidates:
        for fmt in fmts:
            try:
                dt = datetime.strptime(candidate, fmt)
                meeting_date = dt.strftime('%Y-%m-%d')
                meeting_time = '' if fmt in {'%Y-%m-%d', '%m/%d/%y', '%m/%d/%Y', '%B %d, %Y', '%b %d, %Y'} else dt.strftime('%H:%M')
                return (meeting_date, meeting_time)
            except ValueError:
                continue
    return ('', '')


def _meeting_status(meeting_date: str) -> str:
    if not meeting_date:
        return 'scheduled'
    if meeting_date < date.today().isoformat():
        return 'archived'
    return 'upcoming'


def _external_key(meeting: MeetingRecord) -> str:
    doc_urls = '|'.join(sorted(document.url for document in meeting.documents))
    raw = '||'.join(
        [
            (meeting.title or '').strip().lower(),
            (meeting.starts_at or '').strip().lower(),
            (meeting.source_url or '').strip().lower(),
            (meeting.meeting_page_url or '').strip().lower(),
            doc_urls.lower(),
        ]
    )
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()[:24]


def _parse_timestamp(value: str | None) -> datetime | None:
    raw = (value or '').strip()
    if not raw:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def ensure_public_meeting_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS meeting_locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            location_type TEXT NOT NULL DEFAULT 'city',
            county_name TEXT,
            city_name TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        '''
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS meeting_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            provider_type TEXT NOT NULL,
            source_url TEXT NOT NULL,
            meeting_scope TEXT NOT NULL DEFAULT 'city',
            body_name TEXT,
            location_id INTEGER,
            source_config_path TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            last_scraped_at TEXT,
            last_success_at TEXT,
            last_error TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (location_id) REFERENCES meeting_locations(id) ON DELETE SET NULL
        )
        '''
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS public_meetings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            location_id INTEGER,
            external_key TEXT NOT NULL,
            title TEXT NOT NULL,
            body_name TEXT,
            meeting_scope TEXT NOT NULL DEFAULT 'city',
            starts_at_raw TEXT,
            meeting_date TEXT,
            meeting_time TEXT,
            status TEXT NOT NULL DEFAULT 'scheduled',
            source_url TEXT,
            meeting_page_url TEXT,
            location_name TEXT,
            is_current INTEGER NOT NULL DEFAULT 1,
            last_seen_at TEXT DEFAULT (datetime('now')),
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (source_id) REFERENCES meeting_sources(id) ON DELETE CASCADE,
            FOREIGN KEY (location_id) REFERENCES meeting_locations(id) ON DELETE SET NULL,
            UNIQUE (source_id, external_key)
        )
        '''
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS meeting_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meeting_id INTEGER NOT NULL,
            label TEXT NOT NULL,
            document_type TEXT NOT NULL DEFAULT 'agenda',
            url TEXT NOT NULL,
            target_type TEXT NOT NULL DEFAULT 'unknown',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (meeting_id) REFERENCES public_meetings(id) ON DELETE CASCADE,
            UNIQUE (meeting_id, document_type, url)
        )
        '''
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS meeting_source_alerts (
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
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_meeting_locations_scope ON meeting_locations(location_type, county_name, city_name)'
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_meeting_sources_location ON meeting_sources(location_id, meeting_scope, is_active)'
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_public_meetings_feed ON public_meetings(is_current, status, meeting_date, meeting_time)'
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_public_meetings_location ON public_meetings(location_id, meeting_scope, meeting_date)'
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_meeting_documents_meeting ON meeting_documents(meeting_id, document_type)'
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_meeting_source_alerts_open '
        'ON meeting_source_alerts(source_slug, alert_kind, state)'
    )


def _upsert_location(conn: sqlite3.Connection, config: CityScrapeConfig) -> int:
    meta = _resolved_location_metadata(config)
    conn.execute(
        '''
        INSERT INTO meeting_locations (slug, display_name, location_type, county_name, city_name)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(slug) DO UPDATE SET
            display_name = excluded.display_name,
            location_type = excluded.location_type,
            county_name = excluded.county_name,
            city_name = excluded.city_name,
            updated_at = datetime('now')
        ''',
        (
            meta['slug'],
            meta['display_name'],
            meta['location_type'],
            meta['county_name'],
            meta['city_name'],
        ),
    )
    row = conn.execute(
        'SELECT id FROM meeting_locations WHERE slug = ?',
        (meta['slug'],),
    ).fetchone()
    return int(row['id'])


def _upsert_source(conn: sqlite3.Connection, config: CityScrapeConfig, *, config_path: str = '') -> int:
    ensure_public_meeting_schema(conn)
    location_id = _upsert_location(conn, config)
    body_name = str(config.metadata.get('body_name') or config.name).strip() or config.name
    conn.execute(
        '''
        INSERT INTO meeting_sources (
            slug, name, provider_type, source_url, meeting_scope, body_name, location_id, source_config_path
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(slug) DO UPDATE SET
            name = excluded.name,
            provider_type = excluded.provider_type,
            source_url = excluded.source_url,
            meeting_scope = excluded.meeting_scope,
            body_name = excluded.body_name,
            location_id = excluded.location_id,
            source_config_path = excluded.source_config_path,
            is_active = 1,
            updated_at = datetime('now')
        ''',
        (
            config.slug,
            config.name,
            config.provider,
            config.url,
            _normalized_scope(config),
            body_name,
            location_id,
            config_path,
        ),
    )
    row = conn.execute(
        'SELECT id FROM meeting_sources WHERE slug = ?',
        (config.slug,),
    ).fetchone()
    return int(row['id'])


def record_source_scrape_error(
    conn: sqlite3.Connection,
    config: CityScrapeConfig,
    message: str,
    *,
    config_path: str = '',
) -> None:
    source_id = _upsert_source(conn, config, config_path=config_path)
    conn.execute(
        '''
        UPDATE meeting_sources
        SET last_scraped_at = datetime('now'),
            last_error = ?,
            updated_at = datetime('now')
        WHERE id = ?
        ''',
        ((message or '').strip()[:500], source_id),
    )


def sync_scraped_meetings(
    conn: sqlite3.Connection,
    config: CityScrapeConfig,
    meetings: list[MeetingRecord],
    *,
    config_path: str = '',
) -> dict[str, int]:
    ensure_public_meeting_schema(conn)
    source_id = _upsert_source(conn, config, config_path=config_path)
    location_row = conn.execute(
        '''
        SELECT meeting_locations.id, meeting_locations.display_name
        FROM meeting_sources
        LEFT JOIN meeting_locations ON meeting_locations.id = meeting_sources.location_id
        WHERE meeting_sources.id = ?
        ''',
        (source_id,),
    ).fetchone()
    location_id = int(location_row['id']) if location_row and location_row['id'] is not None else None
    location_name = location_row['display_name'] if location_row else ''

    seen_keys: list[str] = []
    created = 0
    updated = 0

    for meeting in meetings:
        external_key = _external_key(meeting)
        seen_keys.append(external_key)
        meeting_date, meeting_time = _parse_meeting_start(meeting.starts_at)
        status = _meeting_status(meeting_date)
        existing = conn.execute(
            'SELECT id FROM public_meetings WHERE source_id = ? AND external_key = ?',
            (source_id, external_key),
        ).fetchone()

        conn.execute(
            '''
            INSERT INTO public_meetings (
                source_id, location_id, external_key, title, body_name, meeting_scope,
                starts_at_raw, meeting_date, meeting_time, status, source_url,
                meeting_page_url, location_name, is_current, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, datetime('now'))
            ON CONFLICT(source_id, external_key) DO UPDATE SET
                location_id = excluded.location_id,
                title = excluded.title,
                body_name = excluded.body_name,
                meeting_scope = excluded.meeting_scope,
                starts_at_raw = excluded.starts_at_raw,
                meeting_date = excluded.meeting_date,
                meeting_time = excluded.meeting_time,
                status = excluded.status,
                source_url = excluded.source_url,
                meeting_page_url = excluded.meeting_page_url,
                location_name = excluded.location_name,
                is_current = 1,
                last_seen_at = datetime('now'),
                updated_at = datetime('now')
            ''',
            (
                source_id,
                location_id,
                external_key,
                (meeting.title or config.name).strip() or config.name,
                (meeting.body_name or config.metadata.get('body_name') or config.name).strip() or config.name,
                _normalized_scope(config),
                (meeting.starts_at or '').strip(),
                meeting_date,
                meeting_time,
                status,
                (meeting.source_url or config.url).strip(),
                (meeting.meeting_page_url or '').strip(),
                location_name or (meeting.location_name or '').strip(),
            ),
        )
        row = conn.execute(
            'SELECT id FROM public_meetings WHERE source_id = ? AND external_key = ?',
            (source_id, external_key),
        ).fetchone()
        meeting_id = int(row['id'])

        conn.execute('DELETE FROM meeting_documents WHERE meeting_id = ?', (meeting_id,))
        for document in meeting.documents:
            conn.execute(
                '''
                INSERT OR IGNORE INTO meeting_documents (meeting_id, label, document_type, url, target_type)
                VALUES (?, ?, ?, ?, ?)
                ''',
                (
                    meeting_id,
                    (document.label or document.document_type.title()).strip() or document.document_type.title(),
                    (document.document_type or 'agenda').strip() or 'agenda',
                    (document.url or '').strip(),
                    (document.target_type or 'unknown').strip() or 'unknown',
                ),
            )

        if existing:
            updated += 1
        else:
            created += 1

    if seen_keys:
        placeholders = ','.join('?' for _ in seen_keys)
        conn.execute(
            f'''
            UPDATE public_meetings
            SET is_current = 0,
                updated_at = datetime('now')
            WHERE source_id = ?
              AND external_key NOT IN ({placeholders})
            ''',
            [source_id, *seen_keys],
        )

    conn.execute(
        '''
        UPDATE meeting_sources
        SET last_scraped_at = datetime('now'),
            last_success_at = datetime('now'),
            last_error = NULL,
            updated_at = datetime('now')
        WHERE id = ?
        ''',
        (source_id,),
    )
    return {
        'source_id': source_id,
        'created': created,
        'updated': updated,
        'total': len(meetings),
    }


def meeting_admin_context(conn: sqlite3.Connection) -> dict:
    ensure_public_meeting_schema(conn)
    stale_cutoff_hours = 18
    stale_cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=stale_cutoff_hours)

    sources = [
        dict(row)
        for row in conn.execute(
            '''
            SELECT
                meeting_sources.*,
                meeting_locations.display_name AS location_display_name,
                meeting_locations.location_type,
                meeting_locations.county_name,
                meeting_locations.city_name,
                COUNT(DISTINCT CASE WHEN public_meetings.is_current = 1 THEN public_meetings.id END) AS current_meeting_count,
                COUNT(DISTINCT CASE WHEN public_meetings.is_current = 1 AND public_meetings.status = 'upcoming' THEN public_meetings.id END) AS upcoming_meeting_count,
                COUNT(DISTINCT meeting_documents.id) AS document_count
            FROM meeting_sources
            LEFT JOIN meeting_locations ON meeting_locations.id = meeting_sources.location_id
            LEFT JOIN public_meetings ON public_meetings.source_id = meeting_sources.id
            LEFT JOIN meeting_documents ON meeting_documents.meeting_id = public_meetings.id
            WHERE meeting_sources.is_active = 1
            GROUP BY meeting_sources.id
            ORDER BY meeting_sources.name ASC
            '''
        ).fetchall()
    ]

    health_counts = {
        'healthy': 0,
        'stale': 0,
        'error': 0,
        'never': 0,
    }

    for source in sources:
        last_success = _parse_timestamp(source.get('last_success_at'))
        if source.get('last_error'):
            health_state = 'error'
        elif last_success is None and not source.get('last_scraped_at'):
            health_state = 'never'
        elif last_success is None or last_success < stale_cutoff:
            health_state = 'stale'
        else:
            health_state = 'healthy'
        source['health_state'] = health_state
        health_counts[health_state] += 1

    summary = dict(
        conn.execute(
            '''
            SELECT
                COUNT(*) AS source_count,
                COUNT(DISTINCT location_id) AS location_count
            FROM meeting_sources
            WHERE is_active = 1
            '''
        ).fetchone()
    )
    summary['meeting_count'] = conn.execute(
        'SELECT COUNT(*) AS cnt FROM public_meetings WHERE is_current = 1'
    ).fetchone()['cnt']
    summary['upcoming_count'] = conn.execute(
        "SELECT COUNT(*) AS cnt FROM public_meetings WHERE is_current = 1 AND status = 'upcoming'"
    ).fetchone()['cnt']
    summary['document_count'] = conn.execute(
        '''
        SELECT COUNT(*) AS cnt
        FROM meeting_documents
        JOIN public_meetings ON public_meetings.id = meeting_documents.meeting_id
        WHERE public_meetings.is_current = 1
        '''
    ).fetchone()['cnt']

    location_rollup = [
        dict(row)
        for row in conn.execute(
            '''
            SELECT
                meeting_locations.display_name,
                meeting_locations.location_type,
                COUNT(DISTINCT meeting_sources.id) AS source_count
            FROM meeting_locations
            JOIN meeting_sources ON meeting_sources.location_id = meeting_locations.id
            WHERE meeting_sources.is_active = 1
            GROUP BY meeting_locations.id
            ORDER BY meeting_locations.location_type ASC, meeting_locations.display_name ASC
            '''
        ).fetchall()
    ]

    active_alerts = [
        dict(row)
        for row in conn.execute(
            '''
            SELECT *
            FROM meeting_source_alerts
            WHERE state = 'open'
            ORDER BY first_detected_at DESC, id DESC
            '''
        ).fetchall()
    ]

    return {
        'summary': summary,
        'sources': sources,
        'location_rollup': location_rollup,
        'health_counts': health_counts,
        'stale_cutoff_hours': stale_cutoff_hours,
        'active_alerts': active_alerts,
    }
