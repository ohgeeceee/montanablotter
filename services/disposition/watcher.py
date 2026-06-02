"""
Disposition watcher — background jobs that link jail bookings to court cases
and track outcome updates over time.

Two entry points:
- link_recent_bookings(conn, since_minutes=60): find new jail bookings, look up
  matching court cases, write the links. Idempotent via UNIQUE(booking_id, court_case_id).
- refresh_outcome_data(conn): for existing links, re-fetch the court_cases row
  and detect outcome changes. Sets has_outcome=1 once a disposition is observed,
  and resets notified_admin_at so the admin gets pinged for new outcomes.

Designed to be called by a cron job (see crontab.txt). Each function is also
importable so admin tools / tests can drive them directly.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Optional

from services.disposition.lookup import lookup_disposition
from services.persons.profiles import _parse_last_first

log = logging.getLogger(__name__)

OUTCOME_FIELDS = (
    'disposition', 'sentence_text', 'sentence_date', 'sentencing_judge',
    'plea', 'outcome_scraped_at', 'charges_text', 'status',
)


def _outcome_snapshot(case_row: sqlite3.Row) -> dict:
    """Build a JSON-serializable dict of the outcome-relevant fields for change detection."""
    return {f: case_row[f] for f in OUTCOME_FIELDS if f in case_row.keys()}


def link_recent_bookings(
    conn: sqlite3.Connection,
    *,
    since_minutes: int = 60,
    limit: int = 500,
) -> dict[str, Any]:
    """
    Find jail bookings seen in the last `since_minutes` and link them to any
    matching court cases (criminal). Returns a stats dict.

    Skips bookings already linked (the UNIQUE constraint deduplicates).
    """
    stats = {
        'scanned': 0,
        'linked': 0,
        'skipped_no_match': 0,
        'errors': 0,
    }
    rows = conn.execute(
        '''
        SELECT jb.id, jb.person_name, jb.county_name
        FROM jail_bookings jb
        WHERE jb.first_seen_at >= datetime('now', ? || ' minutes')
          AND jb.person_name IS NOT NULL
          AND TRIM(jb.person_name) != ''
        ORDER BY jb.first_seen_at DESC
        LIMIT ?
        ''',
        (-since_minutes, limit),
    ).fetchall()
    stats['scanned'] = len(rows)

    for row in rows:
        try:
            last, first = _parse_last_first(row['person_name'])
            if not last:
                stats['skipped_no_match'] += 1
                continue
            result = lookup_disposition(
                conn,
                name=row['person_name'],
                county=row['county_name'],
                include_bookings=False,
                limit=10,
            )
            for m in result['matches']:
                for case in m['court_cases']:
                    try:
                        # Build an initial outcome snapshot so the link is immediately
                        # usable by find_pending_notifications / admin views. Without
                        # this, has_outcome=0 until the next refresh_outcome_data run.
                        snap = {f: case.get(f) for f in OUTCOME_FIELDS}
                        has_outcome = 1 if (
                            case.get('disposition') or case.get('sentence_text') or case.get('sentence_date')
                        ) else 0
                        cur = conn.execute(
                            '''
                            INSERT OR IGNORE INTO booking_case_links
                                (booking_id, court_case_id, match_type, confidence,
                                 last_checked_at, last_outcome_snapshot, has_outcome)
                            VALUES (?, ?, ?, ?, datetime('now'), ?, ?)
                            ''',
                            (row['id'], case['id'], m['match_type'], m['confidence'],
                             json.dumps(snap, sort_keys=True), has_outcome),
                        )
                        if cur.rowcount > 0:
                            stats['linked'] += 1
                    except sqlite3.IntegrityError:
                        pass
        except Exception as e:  # noqa: BLE001 — log and keep going
            log.warning('link_recent_bookings: failed for booking id=%s: %s', row['id'], e)
            stats['errors'] += 1
    conn.commit()
    log.info('link_recent_bookings: %s', stats)
    return stats


def refresh_outcome_data(
    conn: sqlite3.Connection,
    *,
    max_age_hours: int = 24,
    limit: int = 1000,
) -> dict[str, Any]:
    """
    For each existing link, re-fetch the court_cases row and detect outcome changes.

    - Updates last_checked_at on every link we touch.
    - If the outcome snapshot changed, stores the new snapshot and clears
      notified_admin_at so the admin gets a fresh notification.
    - Sets has_outcome=1 once any outcome field is populated.
    """
    stats = {
        'scanned': 0,
        'outcome_changes': 0,
        'new_outcomes': 0,
        'unchanged': 0,
        'errors': 0,
    }
    rows = conn.execute(
        '''
        SELECT bcl.id AS link_id, bcl.last_outcome_snapshot,
               cc.disposition, cc.sentence_text, cc.sentence_date,
               cc.sentencing_judge, cc.plea, cc.outcome_scraped_at,
               cc.charges_text, cc.status
        FROM booking_case_links bcl
        JOIN court_cases cc ON cc.id = bcl.court_case_id
        WHERE bcl.last_checked_at IS NULL
           OR bcl.last_checked_at < datetime('now', ? || ' hours')
        ORDER BY bcl.last_checked_at ASC NULLS FIRST
        LIMIT ?
        ''',
        (-max_age_hours, limit),
    ).fetchall()
    stats['scanned'] = len(rows)

    for row in rows:
        try:
            new_snap = _outcome_snapshot(row)
            has_outcome = 1 if (row['disposition'] or row['sentence_text'] or row['sentence_date']) else 0
            old_snap_json = row['last_outcome_snapshot']
            old_snap = json.loads(old_snap_json) if old_snap_json else None
            changed = (old_snap != new_snap)
            if changed:
                if old_snap is None and has_outcome:
                    stats['new_outcomes'] += 1
                elif has_outcome:
                    stats['outcome_changes'] += 1
            else:
                stats['unchanged'] += 1
            conn.execute(
                '''
                UPDATE booking_case_links
                SET last_checked_at = datetime('now'),
                    last_outcome_snapshot = ?,
                    has_outcome = ?,
                    notified_admin_at = CASE WHEN ? THEN NULL ELSE notified_admin_at END
                WHERE id = ?
                ''',
                (json.dumps(new_snap, sort_keys=True), has_outcome, changed, row['link_id']),
            )
        except Exception as e:  # noqa: BLE001
            log.warning('refresh_outcome_data: failed for link id=%s: %s', row['link_id'], e)
            stats['errors'] += 1
    conn.commit()
    log.info('refresh_outcome_data: %s', stats)
    return stats


def find_pending_notifications(conn: sqlite3.Connection, *, limit: int = 100) -> list[dict]:
    """
    Return links that have an outcome the admin hasn't been notified about yet.
    """
    rows = conn.execute(
        '''
        SELECT bcl.id, bcl.booking_id, bcl.court_case_id, bcl.match_type, bcl.confidence,
               bcl.linked_at, bcl.has_outcome, bcl.last_outcome_snapshot,
               jb.person_name, jb.county_name, jb.booking_at,
               cc.case_number, cc.charges_text, cc.disposition, cc.sentence_date,
               cc.sentencing_judge, cc.outcome_scraped_at
        FROM booking_case_links bcl
        JOIN jail_bookings jb ON jb.id = bcl.booking_id
        JOIN court_cases cc ON cc.id = bcl.court_case_id
        WHERE bcl.has_outcome = 1
          AND bcl.notified_admin_at IS NULL
        ORDER BY bcl.last_checked_at DESC
        LIMIT ?
        ''',
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_notified(conn: sqlite3.Connection, link_ids: list[int]) -> int:
    """Mark a batch of links as notified. Returns the rowcount."""
    if not link_ids:
        return 0
    placeholders = ','.join('?' * len(link_ids))
    cur = conn.execute(
        f'UPDATE booking_case_links SET notified_admin_at = datetime("now") WHERE id IN ({placeholders})',
        link_ids,
    )
    conn.commit()
    return cur.rowcount


def notify_admin_of_new_outcomes(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
    mark_after_send: bool = True,
) -> dict[str, Any]:
    """
    Email the admin a digest of pending outcome notifications and (on success)
    stamp notified_admin_at on the listed links so they don't re-fire next run.

    Returns a stats dict: {sent, recipients, links_in_email, total_pending, error}.
    The email is best-effort — if SMTP isn't configured or the send raises, the
    links are NOT marked notified, so the next cron run will retry and the admin
    dashboard at /admin/case-watch still surfaces them.
    """
    pending = find_pending_notifications(conn, limit=max(limit, 1))
    if not pending:
        return {'sent': False, 'recipients': 0, 'links_in_email': 0,
                'total_pending': 0, 'error': None}

    # Lazy import: avoids a hard dep on services.alerts.legacy at module load.
    from services.alerts.legacy import collect_alert_recipients, send_plaintext_email

    recipients = collect_alert_recipients(conn)
    if not recipients:
        log.info('notify_admin_of_new_outcomes: no recipients configured, skipping send')
        return {'sent': False, 'recipients': 0, 'links_in_email': len(pending),
                'total_pending': len(pending), 'error': 'no_recipients'}

    total_pending = conn.execute(
        'SELECT COUNT(*) AS n FROM booking_case_links '
        'WHERE has_outcome = 1 AND notified_admin_at IS NULL'
    ).fetchone()['n']

    subject = (
        f'Montana Blotter: {len(pending)} new court outcome'
        f'{"s" if len(pending) != 1 else ""} for tracked arrests'
    )

    lines = [
        f'{len(pending)} newly-observed court outcome(s) for jail bookings '
        f'previously linked by the disposition watcher.',
        '',
    ]
    for r in pending:
        person = (r.get('person_name') or 'Unknown').strip()
        case = r.get('case_number') or '—'
        county = r.get('county_name') or '—'
        disposition = (r.get('disposition') or '').strip()
        sentence = (r.get('sentence_text') or '').strip()
        sentence_date = (r.get('sentence_date') or '').strip()[:10]
        judge = (r.get('sentencing_judge') or '').strip()
        outcome_bits = []
        if disposition:
            outcome_bits.append(disposition)
        if sentence:
            outcome_bits.append(sentence)
        if sentence_date:
            outcome_bits.append(f'sentenced {sentence_date}')
        if judge:
            outcome_bits.append(f'by {judge}')
        outcome_line = '; '.join(outcome_bits) or '(no outcome details yet)'

        lines.append(f'• {person} ({county}) — {case}')
        lines.append(f'   {outcome_line}')

    if total_pending > len(pending):
        lines.append('')
        lines.append(
            f'...and {total_pending - len(pending)} more pending. '
            f'See /admin/case-watch for the full list.'
        )

    lines.extend([
        '',
        'Review & dismiss: /admin/case-watch?pending=1',
        '',
        '— Montana Blotter disposition watcher',
    ])
    body = '\n'.join(lines)

    try:
        ok = send_plaintext_email(recipients, subject, body)
    except Exception as e:  # noqa: BLE001
        log.warning('notify_admin_of_new_outcomes: send failed: %s', e)
        return {'sent': False, 'recipients': len(recipients),
                'links_in_email': len(pending), 'total_pending': total_pending,
                'error': str(e)}

    if not ok:
        return {'sent': False, 'recipients': len(recipients),
                'links_in_email': len(pending), 'total_pending': total_pending,
                'error': 'smtp_not_configured'}

    if mark_after_send:
        link_ids = [int(r['id']) for r in pending]
        marked = mark_notified(conn, link_ids)
        log.info('notify_admin_of_new_outcomes: sent to %d recipient(s), marked %d links',
                 len(recipients), marked)
    else:
        marked = 0

    return {'sent': True, 'recipients': len(recipients),
            'links_in_email': len(pending), 'total_pending': total_pending,
            'marked': marked, 'error': None}


def run_all(conn: sqlite3.Connection) -> dict[str, Any]:
    """Run both watchers in sequence and return combined stats."""
    link_stats = link_recent_bookings(conn)
    refresh_stats = refresh_outcome_data(conn)
    notify_stats = notify_admin_of_new_outcomes(conn)
    return {
        'link': link_stats,
        'refresh': refresh_stats,
        'notify': notify_stats,
    }
