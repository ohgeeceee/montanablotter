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


def _normalized_meeting_title(meeting: MeetingRecord, config: CityScrapeConfig) -> str:
    raw_title = ' '.join((meeting.title or '').strip().split())
    body_name = ' '.join(
        (
            meeting.body_name
            or str(config.metadata.get('body_name') or '')
            or config.name
        ).strip().split()
    ) or config.name
    if not raw_title:
        return body_name

    normalized = raw_title.lower()
    body_normalized = body_name.lower()
    title = raw_title

    generic_map = {
        'agenda': f'{body_name} Agenda',
        'agenda pdf': f'{body_name} Agenda',
        'packet': f'{body_name} Packet',
        'packet pdf': f'{body_name} Packet',
        'packet (pdf)': f'{body_name} Packet',
        'minutes': f'{body_name} Minutes',
        'minutes pdf': f'{body_name} Minutes',
        'minutes (pdf)': f'{body_name} Minutes',
    }
    if normalized in generic_map:
        return generic_map[normalized]
    if normalized == body_normalized:
        return f'{body_name} Meeting'

    alias_map: list[tuple[str, str]] = []
    if 'mayor and city council' in body_normalized:
        alias_map.extend(
            [
                ('mayor and city council', body_name),
                ('city council', body_name),
                ('council', body_name),
            ]
        )
    elif 'city council' in body_normalized:
        alias_map.extend(
            [
                ('city council', body_name),
                ('council', body_name),
            ]
        )
    elif 'city commission' in body_normalized:
        alias_map.extend(
            [
                ('city commission', body_name),
                ('commission', body_name),
            ]
        )
    elif 'commissioners' in body_normalized:
        alias_map.extend(
            [
                ("commissioners'", body_name),
                ('commissioners', body_name),
                ('county commission', body_name),
            ]
        )

    lowered_title = title.lower()
    for alias, replacement in alias_map:
        if lowered_title == alias:
            title = replacement
            lowered_title = title.lower()
            break
        if lowered_title.startswith(f'{alias} '):
            title = f"{replacement}{title[len(alias):]}"
            lowered_title = title.lower()
            break
        if lowered_title.startswith(f'{alias} - '):
            subject = title[len(alias) + 3 :].strip()
            title = f'{replacement} - {subject}'
            lowered_title = title.lower()
            break

    title = re.sub(r'\s+\((?:pdf|docx?)\)\s*$', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s+(?:pdf|docx?)\s*$', '', title, flags=re.IGNORECASE)

    if re.search(r'\b(workshop|work session|meeting|hearing|session)\s+agenda$', title, flags=re.IGNORECASE):
        title = re.sub(r'\s+agenda$', '', title, flags=re.IGNORECASE)
    if re.search(r'\b(workshop|work session|meeting|hearing|session)\s+packet$', title, flags=re.IGNORECASE):
        title = re.sub(r'\s+packet$', '', title, flags=re.IGNORECASE)
    if re.search(r'\b(meeting|session)\s+minutes$', title, flags=re.IGNORECASE):
        title = re.sub(r'\s+minutes$', '', title, flags=re.IGNORECASE)

    if title.startswith(f'{body_name} Agenda - '):
        title = title.replace(f'{body_name} Agenda - ', f'{body_name} Agenda: ', 1)

    return ' '.join(title.split()).strip() or body_name


def _dedupe_identity_title(value: str, *, body_name: str = '') -> str:
    text = ' '.join((value or '').strip().split())
    if not text:
        return ''
    if body_name:
        text = _normalized_meeting_title(
            MeetingRecord(title=text, body_name=body_name),
            CityScrapeConfig(
                slug='dedupe',
                name=body_name,
                provider='custom_html',
                url='',
                metadata={'body_name': body_name},
            ),
        )
    text = re.sub(
        r'^(?:[A-Za-z]{3,9}\s+\d{1,2},\s*\d{4}|\d{1,2}/\d{1,2}/\d{2,4})\s*[-:]\s*',
        '',
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r'\s+\((?:pdf|docx?)\)\s*$', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s+(?:pdf|docx?)\s*$', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s+(agenda|packet|minutes)\s*$', '', text, flags=re.IGNORECASE)
    text = re.sub(r'^(council|city council|city commission|commissioners?)\s+agenda\s*[-:]\s*', '', text, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', text).strip().lower()


def _merged_meeting_record(preferred: MeetingRecord, candidate: MeetingRecord) -> MeetingRecord:
    documents = preferred.documents + [doc for doc in candidate.documents if (doc.document_type, doc.url) not in {(d.document_type, d.url) for d in preferred.documents}]
    return MeetingRecord(
        title=preferred.title or candidate.title,
        starts_at=preferred.starts_at or candidate.starts_at,
        source_url=preferred.source_url or candidate.source_url,
        meeting_page_url=preferred.meeting_page_url or candidate.meeting_page_url,
        location_name=preferred.location_name or candidate.location_name,
        body_name=preferred.body_name or candidate.body_name,
        documents=documents,
    )


def _meeting_document_urls(meeting: MeetingRecord) -> set[str]:
    return {
        (document.url or '').strip()
        for document in meeting.documents
        if (document.url or '').strip()
    }


def _dedupe_scraped_meetings(meetings: list[MeetingRecord]) -> list[MeetingRecord]:
    deduped: list[MeetingRecord] = []

    for meeting in meetings:
        meeting_date, meeting_time = _parse_meeting_start(meeting.starts_at)
        identity = _dedupe_identity_title(meeting.title, body_name=meeting.body_name)
        meeting_doc_urls = _meeting_document_urls(meeting)
        if not meeting_date or not identity:
            deduped.append(meeting)
            continue

        match_index: int | None = None
        for index, existing in enumerate(deduped):
            existing_date, existing_time = _parse_meeting_start(existing.starts_at)
            if existing_date != meeting_date or existing_time != meeting_time:
                continue
            existing_identity = _dedupe_identity_title(existing.title, body_name=existing.body_name)
            existing_doc_urls = _meeting_document_urls(existing)
            same_identity = existing_identity == identity
            shared_docs = bool(meeting_doc_urls and existing_doc_urls and meeting_doc_urls.intersection(existing_doc_urls))
            if not same_identity and not shared_docs:
                continue
            match_index = index
            break

        if match_index is None:
            deduped.append(meeting)
            continue

        existing = deduped[match_index]
        prefer_candidate = (
            len(meeting.documents) > len(existing.documents)
            or (len(meeting.documents) == len(existing.documents) and len((meeting.title or '').strip()) < len((existing.title or '').strip()))
        )
        if prefer_candidate:
            deduped[match_index] = _merged_meeting_record(meeting, existing)
        else:
            deduped[match_index] = _merged_meeting_record(existing, meeting)

    return deduped


def _stored_meeting_preference(row: sqlite3.Row) -> tuple[int, int, int]:
    doc_count = int(row['doc_count'] or 0)
    has_page = 1 if (row['meeting_page_url'] or '').strip() else 0
    title_len = len((row['title'] or '').strip())
    return (doc_count, has_page, -title_len)


def _manual_duplicate_match(
    source_slug: str,
    left_title: str,
    right_title: str,
    *,
    left_doc_count: int = 0,
    right_doc_count: int = 0,
) -> bool:
    if source_slug != 'livingston-city-commission':
        return False

    left = ' '.join((left_title or '').strip().lower().split())
    right = ' '.join((right_title or '').strip().lower().split())
    titles = {left, right}
    has_budget_workshop = any('budget workshop' in title for title in titles)
    has_work_session = any('work session' in title for title in titles)
    has_richer_row = max(left_doc_count, right_doc_count) > min(left_doc_count, right_doc_count)
    return has_budget_workshop and has_work_session and has_richer_row


def dedupe_stored_meetings(conn: sqlite3.Connection, *, source_slug: str = '') -> dict[str, int]:
    ensure_public_meeting_schema(conn)
    sql = '''
        SELECT
            public_meetings.id,
            public_meetings.source_id,
            public_meetings.title,
            public_meetings.body_name,
            public_meetings.starts_at_raw,
            public_meetings.meeting_date,
            public_meetings.meeting_time,
            public_meetings.source_url,
            public_meetings.meeting_page_url,
            public_meetings.location_name,
            public_meetings.is_current,
            COUNT(meeting_documents.id) AS doc_count,
            GROUP_CONCAT(meeting_documents.url) AS doc_urls,
            meeting_sources.slug AS source_slug
        FROM public_meetings
        JOIN meeting_sources ON meeting_sources.id = public_meetings.source_id
        LEFT JOIN meeting_documents ON meeting_documents.meeting_id = public_meetings.id
    '''
    params: list[str] = []
    if source_slug:
        sql += ' WHERE meeting_sources.slug = ?'
        params.append(source_slug)
    sql += '''
        GROUP BY public_meetings.id
        ORDER BY public_meetings.source_id, public_meetings.meeting_date, public_meetings.meeting_time, public_meetings.id
    '''
    rows = conn.execute(sql, params).fetchall()

    grouped: dict[tuple[int, str, str], list[sqlite3.Row]] = {}
    for row in rows:
        meeting_date = (row['meeting_date'] or '').strip()
        if not meeting_date:
            continue
        key = (
            int(row['source_id']),
            meeting_date,
            (row['meeting_time'] or '').strip(),
        )
        grouped.setdefault(key, []).append(row)

    deleted = 0
    migrated_docs = 0
    groups = 0

    for group_rows in grouped.values():
        if len(group_rows) < 2:
            continue
        survivors: list[sqlite3.Row] = []
        for row in group_rows:
            row_identity = _dedupe_identity_title(row['title'] or '', body_name=row['body_name'] or '')
            row_doc_urls = _stored_row_doc_urls(row)
            matched_index: int | None = None
            for index, existing in enumerate(survivors):
                existing_identity = _dedupe_identity_title(existing['title'] or '', body_name=existing['body_name'] or '')
                existing_doc_urls = _stored_row_doc_urls(existing)
                same_identity = row_identity and existing_identity and row_identity == existing_identity
                shared_docs = bool(row_doc_urls and existing_doc_urls and row_doc_urls.intersection(existing_doc_urls))
                manual_match = _manual_duplicate_match(
                    row['source_slug'] or '',
                    row['title'] or '',
                    existing['title'] or '',
                    left_doc_count=int(row['doc_count'] or 0),
                    right_doc_count=int(existing['doc_count'] or 0),
                )
                if same_identity or shared_docs or manual_match:
                    matched_index = index
                    break
            if matched_index is None:
                survivors.append(row)
                continue

            groups += 1
            existing = survivors[matched_index]
            if _stored_meeting_preference(row) > _stored_meeting_preference(existing):
                keeper = row
                duplicate = existing
                survivors[matched_index] = row
            else:
                keeper = existing
                duplicate = row

            keeper_id = int(keeper['id'])
            duplicate_id = int(duplicate['id'])
            dup_docs = conn.execute(
                '''
                SELECT label, document_type, url, target_type
                FROM meeting_documents
                WHERE meeting_id = ?
                ''',
                (duplicate_id,),
            ).fetchall()
            for doc in dup_docs:
                conn.execute(
                    '''
                    INSERT OR IGNORE INTO meeting_documents (meeting_id, label, document_type, url, target_type)
                    VALUES (?, ?, ?, ?, ?)
                    ''',
                    (keeper_id, doc['label'], doc['document_type'], doc['url'], doc['target_type']),
                )
                if conn.execute('SELECT changes()').fetchone()[0]:
                    migrated_docs += 1
            conn.execute('DELETE FROM public_meetings WHERE id = ?', (duplicate_id,))
            deleted += 1

    return {
        'groups': groups,
        'deleted': deleted,
        'migrated_docs': migrated_docs,
    }


def _stored_row_doc_urls(row: sqlite3.Row) -> set[str]:
    raw = (row['doc_urls'] or '').strip()
    if not raw:
        return set()
    return {item.strip() for item in raw.split(',') if item.strip()}


def _duplicate_review_key(source_slug: str, meeting_date: str, meeting_time: str, meeting_ids: list[int]) -> str:
    normalized_ids = ','.join(str(meeting_id) for meeting_id in sorted(int(meeting_id) for meeting_id in meeting_ids))
    return '||'.join(
        [
            (source_slug or '').strip(),
            (meeting_date or '').strip(),
            (meeting_time or '').strip(),
            normalized_ids,
        ]
    )


def review_duplicate_meeting_group(
    conn: sqlite3.Connection,
    *,
    source_slug: str,
    meeting_date: str,
    meeting_time: str = '',
    meeting_ids: list[int],
    action: str,
    keeper_meeting_id: int | None = None,
    duplicate_meeting_id: int | None = None,
    decided_by_user_id: int | None = None,
) -> dict[str, int | str]:
    ensure_public_meeting_schema(conn)
    normalized_ids = sorted({int(meeting_id) for meeting_id in meeting_ids if int(meeting_id) > 0})
    if len(normalized_ids) < 2:
        raise ValueError('At least two meeting ids are required.')
    if action not in {'keep_both', 'merge'}:
        raise ValueError('Invalid duplicate review action.')

    placeholders = ','.join('?' for _ in normalized_ids)
    rows = conn.execute(
        f'''
        SELECT
            public_meetings.id,
            public_meetings.meeting_date,
            COALESCE(public_meetings.meeting_time, '') AS meeting_time,
            public_meetings.title,
            meeting_sources.slug AS source_slug
        FROM public_meetings
        JOIN meeting_sources ON meeting_sources.id = public_meetings.source_id
        WHERE public_meetings.id IN ({placeholders})
        ''',
        normalized_ids,
    ).fetchall()
    if len(rows) != len(normalized_ids):
        raise ValueError('One or more meetings no longer exist.')
    for row in rows:
        if (row['source_slug'] or '') != (source_slug or '').strip():
            raise ValueError('Meetings do not belong to the selected source.')
        if (row['meeting_date'] or '') != (meeting_date or '').strip():
            raise ValueError('Meetings do not match the selected date.')
        if (row['meeting_time'] or '') != (meeting_time or '').strip():
            raise ValueError('Meetings do not match the selected time.')

    review_key = _duplicate_review_key(source_slug, meeting_date, meeting_time, normalized_ids)

    if action == 'keep_both':
        conn.execute(
            '''
            INSERT INTO meeting_duplicate_reviews (
                review_key, source_slug, meeting_date, meeting_time,
                keeper_meeting_id, duplicate_meeting_id, action, decided_by_user_id,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, datetime('now'), datetime('now'))
            ON CONFLICT(review_key) DO UPDATE SET
                action = excluded.action,
                decided_by_user_id = excluded.decided_by_user_id,
                updated_at = datetime('now')
            ''',
            (
                review_key,
                (source_slug or '').strip(),
                (meeting_date or '').strip(),
                (meeting_time or '').strip(),
                action,
                decided_by_user_id,
            ),
        )
        return {'action': action, 'review_key': review_key, 'deleted': 0, 'migrated_docs': 0}

    if keeper_meeting_id is None or duplicate_meeting_id is None:
        raise ValueError('Keeper and duplicate meeting ids are required for merge.')
    keeper_meeting_id = int(keeper_meeting_id)
    duplicate_meeting_id = int(duplicate_meeting_id)
    if keeper_meeting_id == duplicate_meeting_id:
        raise ValueError('Keeper and duplicate meeting ids must be different.')
    if keeper_meeting_id not in normalized_ids or duplicate_meeting_id not in normalized_ids:
        raise ValueError('Merge ids must belong to the selected duplicate group.')

    duplicate_docs = conn.execute(
        '''
        SELECT label, document_type, url, target_type
        FROM meeting_documents
        WHERE meeting_id = ?
        ''',
        (duplicate_meeting_id,),
    ).fetchall()
    migrated_docs = 0
    for doc in duplicate_docs:
        conn.execute(
            '''
            INSERT OR IGNORE INTO meeting_documents (meeting_id, label, document_type, url, target_type)
            VALUES (?, ?, ?, ?, ?)
            ''',
            (keeper_meeting_id, doc['label'], doc['document_type'], doc['url'], doc['target_type']),
        )
        if conn.execute('SELECT changes()').fetchone()[0]:
            migrated_docs += 1

    conn.execute('DELETE FROM public_meetings WHERE id = ?', (duplicate_meeting_id,))
    conn.execute(
        '''
        INSERT INTO meeting_duplicate_reviews (
            review_key, source_slug, meeting_date, meeting_time,
            keeper_meeting_id, duplicate_meeting_id, action, decided_by_user_id,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        ON CONFLICT(review_key) DO UPDATE SET
            keeper_meeting_id = excluded.keeper_meeting_id,
            duplicate_meeting_id = excluded.duplicate_meeting_id,
            action = excluded.action,
            decided_by_user_id = excluded.decided_by_user_id,
            updated_at = datetime('now')
        ''',
        (
            review_key,
            (source_slug or '').strip(),
            (meeting_date or '').strip(),
            (meeting_time or '').strip(),
            keeper_meeting_id,
            duplicate_meeting_id,
            action,
            decided_by_user_id,
        ),
    )
    return {
        'action': action,
        'review_key': review_key,
        'deleted': 1,
        'migrated_docs': migrated_docs,
        'keeper_meeting_id': keeper_meeting_id,
        'duplicate_meeting_id': duplicate_meeting_id,
    }


def _meeting_status(meeting_date: str) -> str:
    if not meeting_date:
        return 'scheduled'
    if meeting_date < date.today().isoformat():
        return 'archived'
    return 'upcoming'


def _effective_meeting_status(meeting_date: str, stored_status: str = 'scheduled') -> str:
    if meeting_date:
        if meeting_date < date.today().isoformat():
            return 'archived'
        return 'upcoming'
    return (stored_status or 'scheduled').strip() or 'scheduled'


def _effective_meeting_status_sql(*, table_alias: str = 'public_meetings') -> str:
    meeting_date = f'{table_alias}.meeting_date'
    status = f'{table_alias}.status'
    return (
        "CASE "
        f"WHEN COALESCE({meeting_date}, '') != '' AND {meeting_date} < date('now') THEN 'archived' "
        f"WHEN COALESCE({meeting_date}, '') != '' THEN 'upcoming' "
        f"ELSE COALESCE(NULLIF({status}, ''), 'scheduled') "
        "END"
    )


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
        '''
        CREATE TABLE IF NOT EXISTS meeting_duplicate_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            review_key TEXT NOT NULL UNIQUE,
            source_slug TEXT NOT NULL,
            meeting_date TEXT NOT NULL,
            meeting_time TEXT NOT NULL DEFAULT '',
            keeper_meeting_id INTEGER,
            duplicate_meeting_id INTEGER,
            action TEXT NOT NULL,
            decided_by_user_id INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        '''
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_meeting_duplicate_reviews_slot ON meeting_duplicate_reviews(source_slug, meeting_date, meeting_time)'
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
    meetings = _dedupe_scraped_meetings(meetings)
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
                _normalized_meeting_title(meeting, config),
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
    effective_status_sql = _effective_meeting_status_sql()

    sources = [
        dict(row)
        for row in conn.execute(
            f'''
            SELECT
                meeting_sources.*,
                meeting_locations.display_name AS location_display_name,
                meeting_locations.location_type,
                meeting_locations.county_name,
                meeting_locations.city_name,
                COUNT(DISTINCT CASE WHEN public_meetings.is_current = 1 THEN public_meetings.id END) AS current_meeting_count,
                COUNT(DISTINCT CASE WHEN public_meetings.is_current = 1 AND {effective_status_sql} = 'upcoming' THEN public_meetings.id END) AS upcoming_meeting_count,
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
        f"SELECT COUNT(*) AS cnt FROM public_meetings WHERE is_current = 1 AND {_effective_meeting_status_sql()} = 'upcoming'"
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

    reviewed_keep_both = {
        row['review_key']
        for row in conn.execute(
            '''
            SELECT review_key
            FROM meeting_duplicate_reviews
            WHERE action = 'keep_both'
            '''
        ).fetchall()
    }
    duplicate_review_groups: list[dict] = []
    duplicate_groups = conn.execute(
        '''
        SELECT
            meeting_sources.slug AS source_slug,
            meeting_sources.name AS source_name,
            public_meetings.body_name,
            public_meetings.meeting_date,
            COALESCE(public_meetings.meeting_time, '') AS meeting_time,
            COUNT(*) AS meeting_count
        FROM public_meetings
        JOIN meeting_sources ON meeting_sources.id = public_meetings.source_id
        WHERE public_meetings.is_current = 1
        GROUP BY public_meetings.source_id, public_meetings.meeting_date, COALESCE(public_meetings.meeting_time, '')
        HAVING COUNT(*) > 1
        ORDER BY meeting_sources.name ASC, public_meetings.meeting_date ASC, meeting_time ASC
        '''
    ).fetchall()

    for group in duplicate_groups:
        meetings = [
            dict(row)
            for row in conn.execute(
                '''
                SELECT
                    public_meetings.id,
                    public_meetings.title,
                    public_meetings.starts_at_raw,
                    public_meetings.meeting_page_url,
                    public_meetings.source_url,
                    COUNT(meeting_documents.id) AS document_count,
                    GROUP_CONCAT(meeting_documents.document_type, ', ') AS document_types
                FROM public_meetings
                LEFT JOIN meeting_documents ON meeting_documents.meeting_id = public_meetings.id
                JOIN meeting_sources ON meeting_sources.id = public_meetings.source_id
                WHERE meeting_sources.slug = ?
                  AND public_meetings.is_current = 1
                  AND COALESCE(public_meetings.meeting_date, '') = ?
                  AND COALESCE(public_meetings.meeting_time, '') = ?
                GROUP BY public_meetings.id
                ORDER BY public_meetings.id ASC
                ''',
                (
                    group['source_slug'],
                    group['meeting_date'] or '',
                    group['meeting_time'] or '',
                ),
            ).fetchall()
        ]
        review_key = _duplicate_review_key(
            group['source_slug'],
            group['meeting_date'] or '',
            group['meeting_time'] or '',
            [int(meeting['id']) for meeting in meetings],
        )
        if review_key in reviewed_keep_both:
            continue
        duplicate_review_groups.append(
            {
                'source_slug': group['source_slug'],
                'source_name': group['source_name'],
                'body_name': group['body_name'],
                'meeting_date': group['meeting_date'],
                'meeting_time': group['meeting_time'],
                'meeting_count': group['meeting_count'],
                'review_key': review_key,
                'meetings': meetings,
            }
        )

    return {
        'summary': summary,
        'sources': sources,
        'location_rollup': location_rollup,
        'health_counts': health_counts,
        'stale_cutoff_hours': stale_cutoff_hours,
        'active_alerts': active_alerts,
        'duplicate_review_groups': duplicate_review_groups,
    }
