from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable


ACTIVE_STATUS = 'active'
RESOLVED_STATUS = 'resolved'
EMAIL_CHANNEL = 'email'
SMS_CHANNEL = 'sms'
INCIDENT_EVENT_CREATED = 'incident_created'
INCIDENT_EVENT_RESOLVED = 'incident_resolved'
VALID_STATUSES = {ACTIVE_STATUS, RESOLVED_STATUS}
VALID_CHANNELS = {EMAIL_CHANNEL, SMS_CHANNEL}
VALID_EVENTS = {INCIDENT_EVENT_CREATED, INCIDENT_EVENT_RESOLVED}


INCIDENT_JSON_SCHEMA: dict[str, Any] = {
    'type': 'object',
    'required': [
        'id',
        'status',
        'type',
        'location',
        'initialDescription',
        'createdAt',
        'updatedAt',
    ],
    'properties': {
        'id': {'type': 'string'},
        'status': {'type': 'string', 'enum': [ACTIVE_STATUS, RESOLVED_STATUS]},
        'type': {'type': 'string'},
        'location': {
            'type': 'object',
            'required': ['label'],
            'properties': {
                'label': {'type': 'string'},
                'county': {'type': 'string'},
                'lat': {'type': ['number', 'null']},
                'lng': {'type': ['number', 'null']},
            },
        },
        'initialDescription': {'type': 'string'},
        'resolutionSummary': {'type': ['string', 'null']},
        'clearanceTime': {'type': ['string', 'null']},
        'source': {
            'type': 'object',
            'properties': {
                'name': {'type': 'string'},
                'url': {'type': 'string'},
            },
        },
        'createdAt': {'type': 'string'},
        'updatedAt': {'type': 'string'},
        'createdBy': {'type': ['string', 'null']},
        'updatedBy': {'type': ['string', 'null']},
        'notificationVersion': {'type': 'integer'},
    },
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _clean_text(value: Any, *, max_len: int = 1000) -> str:
    text = str(value or '').strip()
    if not text:
        return ''
    return ' '.join(text.split())[:max_len]


def _parse_csv(raw_value: Any) -> list[str]:
    if not raw_value:
        return []
    values = []
    for part in str(raw_value).split(','):
        token = part.strip()
        if token:
            values.append(token)
    return values


def _normalize_channel_list(raw_value: Any) -> list[str]:
    channels = []
    for channel in _parse_csv(raw_value):
        token = channel.lower()
        if token in VALID_CHANNELS and token not in channels:
            channels.append(token)
    return channels


def _normalize_counties(raw_value: Any) -> list[str]:
    counties = []
    for county in _parse_csv(raw_value):
        token = county.lower()
        if token not in counties:
            counties.append(token)
    return counties


def _isoish(value: Any, fallback: str) -> str:
    text = _clean_text(value, max_len=64)
    return text or fallback


def incident_status_meta(status: str) -> dict[str, str]:
    normalized = (status or '').strip().lower()
    if normalized == RESOLVED_STATUS:
        return {
            'badgeLabel': 'Resolved',
            'badgeTone': 'slate',
            'mapColor': '#64748b',
            'listAccent': 'border-slate-400',
        }
    return {
        'badgeLabel': 'In Progress',
        'badgeTone': 'rose',
        'mapColor': '#dc2626',
        'listAccent': 'border-rose-500',
    }


def build_incident_object(payload: dict[str, Any]) -> dict[str, Any]:
    incident_id = _clean_text(payload.get('id'), max_len=80) or f'inc_{int(datetime.now().timestamp())}'
    status = (_clean_text(payload.get('status'), max_len=32) or ACTIVE_STATUS).lower()
    if status not in VALID_STATUSES:
        raise ValueError(f'Unsupported incident status: {status}')

    location_payload = payload.get('location') or {}
    location_label = _clean_text(location_payload.get('label') or payload.get('locationLabel'), max_len=200)
    if not location_label:
        raise ValueError('location.label is required')

    incident_type = _clean_text(payload.get('type'), max_len=120)
    initial_description = _clean_text(payload.get('initialDescription'), max_len=1000)
    if not incident_type:
        raise ValueError('type is required')
    if not initial_description:
        raise ValueError('initialDescription is required')

    created_at = _isoish(payload.get('createdAt'), _utc_now_iso())
    updated_at = _isoish(payload.get('updatedAt'), created_at)
    resolution_summary = _clean_text(payload.get('resolutionSummary'), max_len=1000) or None
    clearance_time = _clean_text(payload.get('clearanceTime'), max_len=64) or None

    if status == RESOLVED_STATUS and not resolution_summary:
        raise ValueError('resolutionSummary is required when status is resolved')

    incident = {
        'id': incident_id,
        'status': status,
        'type': incident_type,
        'location': {
            'label': location_label,
            'county': _clean_text(location_payload.get('county'), max_len=80),
            'lat': location_payload.get('lat'),
            'lng': location_payload.get('lng'),
        },
        'initialDescription': initial_description,
        'resolutionSummary': resolution_summary,
        'clearanceTime': clearance_time,
        'source': {
            'name': _clean_text((payload.get('source') or {}).get('name'), max_len=120),
            'url': _clean_text((payload.get('source') or {}).get('url'), max_len=400),
        },
        'createdAt': created_at,
        'updatedAt': updated_at,
        'createdBy': _clean_text(payload.get('createdBy'), max_len=120) or None,
        'updatedBy': _clean_text(payload.get('updatedBy'), max_len=120) or None,
        'notificationVersion': int(payload.get('notificationVersion') or 1),
    }
    return incident


def ensure_incident_notification_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS incident_updates (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            incident_type TEXT NOT NULL,
            location_label TEXT NOT NULL,
            county TEXT DEFAULT '',
            latitude REAL,
            longitude REAL,
            initial_description TEXT NOT NULL,
            resolution_summary TEXT,
            clearance_time TEXT,
            source_name TEXT,
            source_url TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            created_by TEXT,
            updated_by TEXT,
            notification_version INTEGER NOT NULL DEFAULT 1
        )
        '''
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS incident_notification_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            channel TEXT NOT NULL,
            recipient TEXT NOT NULL,
            delivery_status TEXT NOT NULL DEFAULT 'queued',
            error_message TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        )
        '''
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_incident_updates_status '
        'ON incident_updates(status, updated_at)'
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_incident_updates_county '
        'ON incident_updates(county, status)'
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_incident_notification_deliveries_incident '
        'ON incident_notification_deliveries(incident_id, created_at)'
    )

    existing_columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info('subscribers')").fetchall()
    }
    for col, definition in [
        ('phone', 'TEXT'),
        ('wants_notifications', 'INTEGER NOT NULL DEFAULT 0'),
        ('notification_channels', "TEXT NOT NULL DEFAULT 'email'"),
    ]:
        if col not in existing_columns:
            conn.execute(f'ALTER TABLE subscribers ADD COLUMN {col} {definition}')
            existing_columns.add(col)


def upsert_incident_update(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    incident = build_incident_object(payload)
    location = incident['location']
    source = incident['source']
    conn.execute(
        '''
        INSERT INTO incident_updates (
            id,
            status,
            incident_type,
            location_label,
            county,
            latitude,
            longitude,
            initial_description,
            resolution_summary,
            clearance_time,
            source_name,
            source_url,
            created_at,
            updated_at,
            created_by,
            updated_by,
            notification_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            status = excluded.status,
            incident_type = excluded.incident_type,
            location_label = excluded.location_label,
            county = excluded.county,
            latitude = excluded.latitude,
            longitude = excluded.longitude,
            initial_description = excluded.initial_description,
            resolution_summary = excluded.resolution_summary,
            clearance_time = excluded.clearance_time,
            source_name = excluded.source_name,
            source_url = excluded.source_url,
            updated_at = excluded.updated_at,
            updated_by = excluded.updated_by,
            notification_version = excluded.notification_version
        ''',
        (
            incident['id'],
            incident['status'],
            incident['type'],
            location['label'],
            location['county'],
            location['lat'],
            location['lng'],
            incident['initialDescription'],
            incident['resolutionSummary'],
            incident['clearanceTime'],
            source['name'],
            source['url'],
            incident['createdAt'],
            incident['updatedAt'],
            incident['createdBy'],
            incident['updatedBy'],
            incident['notificationVersion'],
        ),
    )
    return incident


def filter_notification_subscribers(
    conn: sqlite3.Connection,
    *,
    county: str = '',
    channel: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    normalized_county = _clean_text(county, max_len=80).lower()
    normalized_channel = (channel or '').strip().lower() or None
    if normalized_channel and normalized_channel not in VALID_CHANNELS:
        raise ValueError(f'Unsupported channel filter: {normalized_channel}')

    rows = conn.execute(
        '''
        SELECT id, email, phone, counties, active, wants_notifications, notification_channels
        FROM subscribers
        WHERE active = 1 AND wants_notifications = 1
        ORDER BY datetime(created_at) DESC, email ASC
        '''
    ).fetchall()

    subscribed: list[dict[str, Any]] = []
    for row in rows:
        channels = _normalize_channel_list(row['notification_channels'] or 'email')
        counties = _normalize_counties(row['counties'])
        if normalized_channel and normalized_channel not in channels:
            continue
        if counties and normalized_county and normalized_county not in counties:
            continue
        subscribed.append(
            {
                'id': row['id'],
                'role': 'subscriber',
                'email': (row['email'] or '').strip().lower(),
                'phone': _clean_text(row['phone'], max_len=40),
                'channels': channels,
                'counties': counties,
            }
        )

    return {'subscribed': subscribed}


def build_email_alert(incident: dict[str, Any], event_type: str) -> dict[str, str]:
    if event_type not in VALID_EVENTS:
        raise ValueError(f'Unsupported event type: {event_type}')
    incident = build_incident_object(incident)
    location_label = incident['location']['label']
    if event_type == INCIDENT_EVENT_RESOLVED:
        subject = f"Resolved: {incident['type']} at {location_label}"
        body = (
            "The following incident has been marked resolved.\n\n"
            f"Type: {incident['type']}\n"
            f"Location: {location_label}\n"
            f"Resolution: {incident['resolutionSummary'] or 'Resolution details pending.'}\n"
            f"Cleared At: {incident['clearanceTime'] or 'Not provided'}\n\n"
            "This alert is now closed. Archived details may remain available for public record and timeline reference."
        )
        return {'subject': subject, 'body': body}

    subject = f"Urgent Incident Alert: {incident['type']} near {location_label}"
    body = (
        "A new incident has been reported.\n\n"
        f"Type: {incident['type']}\n"
        f"Location: {location_label}\n"
        f"Details: {incident['initialDescription']}\n"
        f"Reported: {incident['createdAt']}\n\n"
        "This incident is currently active. Follow Montana Blotter for updates as official information changes."
    )
    return {'subject': subject, 'body': body}


def build_sms_alert(incident: dict[str, Any], event_type: str, incident_url: str = '') -> str:
    if event_type not in VALID_EVENTS:
        raise ValueError(f'Unsupported event type: {event_type}')
    incident = build_incident_object(incident)
    location_label = incident['location']['label']
    detail_url = _clean_text(incident_url, max_len=400)
    if event_type == INCIDENT_EVENT_RESOLVED:
        return (
            f"RESOLVED: {incident['type']} at {location_label}. "
            f"{incident['resolutionSummary'] or 'Resolution details pending.'} "
            f"Cleared: {incident['clearanceTime'] or 'n/a'}"
            + (f" Details: {detail_url}" if detail_url else '')
        )
    return (
        f"URGENT: {incident['type']} reported near {location_label}. "
        f"{incident['initialDescription']} Status: ACTIVE."
        + (f" Updates: {detail_url}" if detail_url else '')
    )


def log_incident_delivery(
    conn: sqlite3.Connection,
    *,
    incident_id: str,
    event_type: str,
    channel: str,
    recipient: str,
    delivery_status: str,
    error_message: str = '',
) -> None:
    conn.execute(
        '''
        INSERT INTO incident_notification_deliveries (
            incident_id,
            event_type,
            channel,
            recipient,
            delivery_status,
            error_message
        ) VALUES (?, ?, ?, ?, ?, ?)
        ''',
        (
            _clean_text(incident_id, max_len=80),
            _clean_text(event_type, max_len=40),
            _clean_text(channel, max_len=20),
            _clean_text(recipient, max_len=160),
            _clean_text(delivery_status, max_len=20) or 'queued',
            _clean_text(error_message, max_len=400),
        ),
    )


def dispatch_incident_notifications(
    conn: sqlite3.Connection,
    *,
    incident: dict[str, Any],
    event_type: str,
    send_email: Callable[[list[str], str, str], bool] | None = None,
    send_sms: Callable[[str, str], bool] | None = None,
    incident_url: str = '',
) -> dict[str, int]:
    if event_type not in VALID_EVENTS:
        raise ValueError(f'Unsupported event type: {event_type}')

    incident = upsert_incident_update(conn, incident)
    audience = filter_notification_subscribers(conn, county=incident['location']['county'])
    email_payload = build_email_alert(incident, event_type)
    sms_payload = build_sms_alert(incident, event_type, incident_url=incident_url)

    delivered = 0
    failed = 0
    skipped = 0
    for subscriber in audience['subscribed']:
        channels = subscriber['channels']
        if EMAIL_CHANNEL in channels:
            recipient_email = subscriber['email']
            if recipient_email and send_email:
                try:
                    sent = bool(send_email([recipient_email], email_payload['subject'], email_payload['body']))
                except Exception as _exc:
                    logging.error(f"send_email raised exception for {recipient_email}: {_exc}")
                    sent = False
                log_incident_delivery(
                    conn,
                    incident_id=incident['id'],
                    event_type=event_type,
                    channel=EMAIL_CHANNEL,
                    recipient=recipient_email,
                    delivery_status='sent' if sent else 'failed',
                    error_message='' if sent else 'email_sender_returned_false',
                )
                if sent:
                    delivered += 1
                else:
                    failed += 1
            else:
                log_incident_delivery(
                    conn,
                    incident_id=incident['id'],
                    event_type=event_type,
                    channel=EMAIL_CHANNEL,
                    recipient=recipient_email or f"subscriber:{subscriber['id']}",
                    delivery_status='skipped',
                    error_message='missing_email_sender_or_recipient',
                )
                skipped += 1
        if SMS_CHANNEL in channels:
            recipient_phone = subscriber['phone']
            if recipient_phone and send_sms:
                try:
                    sent = bool(send_sms(recipient_phone, sms_payload))
                except Exception as _exc:
                    logging.error(f"send_sms raised exception for {recipient_phone}: {_exc}")
                    sent = False
                log_incident_delivery(
                    conn,
                    incident_id=incident['id'],
                    event_type=event_type,
                    channel=SMS_CHANNEL,
                    recipient=recipient_phone,
                    delivery_status='sent' if sent else 'failed',
                    error_message='' if sent else 'sms_sender_returned_false',
                )
                if sent:
                    delivered += 1
                else:
                    failed += 1
            else:
                log_incident_delivery(
                    conn,
                    incident_id=incident['id'],
                    event_type=event_type,
                    channel=SMS_CHANNEL,
                    recipient=recipient_phone or f"subscriber:{subscriber['id']}",
                    delivery_status='skipped',
                    error_message='missing_sms_sender_or_recipient',
                )
                skipped += 1

    return {
        'subscribers': len(audience['subscribed']),
        'delivered': delivered,
        'failed': failed,
        'skipped': skipped,
    }


def incident_record_to_object(row: sqlite3.Row) -> dict[str, Any]:
    return build_incident_object(
        {
            'id': row['id'],
            'status': row['status'],
            'type': row['incident_type'],
            'location': {
                'label': row['location_label'],
                'county': row['county'],
                'lat': row['latitude'],
                'lng': row['longitude'],
            },
            'initialDescription': row['initial_description'],
            'resolutionSummary': row['resolution_summary'],
            'clearanceTime': row['clearance_time'],
            'source': {
                'name': row['source_name'],
                'url': row['source_url'],
            },
            'createdAt': row['created_at'],
            'updatedAt': row['updated_at'],
            'createdBy': row['created_by'],
            'updatedBy': row['updated_by'],
            'notificationVersion': row['notification_version'],
        }
    )


def incident_schema_json(indent: int = 2) -> str:
    return json.dumps(INCIDENT_JSON_SCHEMA, indent=indent, sort_keys=True)
