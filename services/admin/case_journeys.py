import re
import sqlite3
from datetime import datetime
from typing import Optional


_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    return _NON_ALNUM_RE.sub("-", (value or "").strip().lower()).strip("-")


def _normalize_date_for_slug(value: Optional[str]) -> str:
    raw = (value or "").strip()
    if not raw:
        return "undated"
    for fmt in ("%m/%d/%y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return _slugify(raw) or "undated"


def _journey_status(record: sqlite3.Row, log_count: int) -> str:
    text = " ".join(
        [
            str(record["incident_type"] or ""),
            str(record["incident"] or ""),
            str(record["details"] or ""),
        ]
    ).lower()
    if any(token in text for token in ("arrest", "booked", "booking", "custody", "jail")):
        return "arrested"
    if log_count >= 4:
        return "monitoring"
    return "open"


def _confidence(record: sqlite3.Row) -> tuple[str, int]:
    if (record["cfs_number"] or "").strip():
        return "exact", 94
    if (record["location"] or "").strip():
        return "strong", 82
    return "probable", 68


def ensure_case_journey_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS case_journeys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            county TEXT,
            primary_record_id INTEGER,
            primary_post_id INTEGER,
            current_status TEXT NOT NULL DEFAULT 'open',
            summary TEXT,
            started_at TEXT,
            last_event_at TEXT,
            link_confidence_label TEXT NOT NULL DEFAULT 'probable',
            link_confidence_score INTEGER NOT NULL DEFAULT 60,
            is_featured_homepage INTEGER NOT NULL DEFAULT 1,
            is_published INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (primary_record_id) REFERENCES records(id) ON DELETE SET NULL,
            FOREIGN KEY (primary_post_id) REFERENCES posts(id) ON DELETE SET NULL
        )
        '''
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS case_journey_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            journey_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            event_at TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0,
            source_kind TEXT,
            source_table TEXT,
            source_row_id INTEGER,
            source_url TEXT,
            title TEXT,
            summary TEXT,
            raw_reference TEXT,
            match_confidence_label TEXT NOT NULL DEFAULT 'probable',
            match_confidence_score INTEGER NOT NULL DEFAULT 60,
            match_reason TEXT,
            is_public INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (journey_id) REFERENCES case_journeys(id) ON DELETE CASCADE
        )
        '''
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS case_journey_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            journey_id INTEGER NOT NULL,
            left_source_table TEXT NOT NULL,
            left_source_row_id INTEGER NOT NULL,
            right_source_table TEXT NOT NULL,
            right_source_row_id INTEGER NOT NULL,
            link_type TEXT NOT NULL,
            confidence_label TEXT NOT NULL DEFAULT 'probable',
            confidence_score INTEGER NOT NULL DEFAULT 60,
            reason_json TEXT,
            review_status TEXT NOT NULL DEFAULT 'accepted',
            reviewed_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (journey_id) REFERENCES case_journeys(id) ON DELETE CASCADE
        )
        '''
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_case_journeys_publish ON case_journeys(is_published, is_featured_homepage, last_event_at)'
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_case_journeys_record ON case_journeys(primary_record_id)'
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_case_journey_events_journey ON case_journey_events(journey_id, sort_order)'
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_case_journey_events_source ON case_journey_events(source_table, source_row_id)'
    )


def seed_case_journeys(conn: sqlite3.Connection) -> int:
    ensure_case_journey_schema(conn)
    records = conn.execute(
        '''
        SELECT records.*,
               posts.id AS post_id,
               posts.title AS post_title,
               COUNT(command_logs.id) AS log_count,
               MAX(COALESCE(command_logs.timestamp, command_logs.created_at)) AS latest_log_at
        FROM records
        JOIN command_logs ON command_logs.record_id = records.id
        LEFT JOIN posts ON posts.blotter_id = records.blotter_id
        GROUP BY records.id
        ORDER BY COUNT(command_logs.id) DESC, records.id DESC
        '''
    ).fetchall()

    created = 0
    for record in records:
        incident_title = (record["incident_type"] or record["incident"] or "Incident").strip()
        slug = "-".join(
            part
            for part in (
                _slugify(record["county"] or "montana"),
                _normalize_date_for_slug(record["date"]),
                _slugify(incident_title)[:48],
                str(record["id"]),
            )
            if part
        )
        if not slug:
            slug = f"journey-{record['id']}"

        existing = conn.execute(
            'SELECT id FROM case_journeys WHERE slug = ?',
            (slug,),
        ).fetchone()
        if existing:
            continue

        confidence_label, confidence_score = _confidence(record)
        current_status = _journey_status(record, int(record["log_count"] or 0))
        last_event_at = record["latest_log_at"] or record["date"]
        summary = (
            f"Montana Blotter captured {int(record['log_count'] or 0)} follow-up log "
            f"{'entry' if int(record['log_count'] or 0) == 1 else 'entries'} after the initial record."
        )

        cursor = conn.execute(
            '''
            INSERT INTO case_journeys (
                slug, title, county, primary_record_id, primary_post_id, current_status,
                summary, started_at, last_event_at, link_confidence_label, link_confidence_score,
                is_featured_homepage, is_published
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1)
            ''',
            (
                slug,
                incident_title,
                record["county"],
                record["id"],
                record["post_id"],
                current_status,
                summary,
                record["date"],
                last_event_at,
                confidence_label,
                confidence_score,
            ),
        )
        journey_id = int(cursor.lastrowid or 0)

        conn.execute(
            '''
            INSERT INTO case_journey_events (
                journey_id, event_type, event_at, sort_order, source_kind, source_table, source_row_id,
                title, summary, raw_reference, match_confidence_label, match_confidence_score, match_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                journey_id,
                'dispatch_record',
                record["date"],
                0,
                'montana_blotter_record',
                'records',
                record["id"],
                incident_title,
                (record["details"] or '').strip() or 'Initial incident record.',
                record["cfs_number"] or record["location"] or '',
                confidence_label,
                confidence_score,
                'Primary Montana Blotter incident record.',
            ),
        )

        logs = conn.execute(
            '''
            SELECT id, timestamp, officer, entry
            FROM command_logs
            WHERE record_id = ?
            ORDER BY timestamp, id
            ''',
            (record["id"],),
        ).fetchall()
        for offset, log in enumerate(logs, start=1):
            title = (log["officer"] or "Command log update").strip() or "Command log update"
            conn.execute(
                '''
                INSERT INTO case_journey_events (
                    journey_id, event_type, event_at, sort_order, source_kind, source_table, source_row_id,
                    title, summary, raw_reference, match_confidence_label, match_confidence_score, match_reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    journey_id,
                    'command_log',
                    log["timestamp"] or record["date"],
                    offset,
                    'montana_blotter_command_log',
                    'command_logs',
                    log["id"],
                    title,
                    (log["entry"] or '').strip(),
                    log["officer"] or '',
                    'exact',
                    100,
                    'Direct command-log entry tied to the same incident record.',
                ),
            )
            conn.execute(
                '''
                INSERT INTO case_journey_links (
                    journey_id, left_source_table, left_source_row_id, right_source_table, right_source_row_id,
                    link_type, confidence_label, confidence_score, reason_json, review_status, reviewed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'accepted', datetime('now'))
                ''',
                (
                    journey_id,
                    'records',
                    record["id"],
                    'command_logs',
                    log["id"],
                    'record_to_command_log',
                    'exact',
                    100,
                    '{"reason":"same_record_id"}',
                ),
            )

        created += 1
    return created
