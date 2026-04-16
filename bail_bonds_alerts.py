from __future__ import annotations

import json
import logging
import re
import sqlite3
from typing import Any

import config
import requests


logger = logging.getLogger(__name__)


HIGH_INTENT_CHARGE_RULES: dict[str, tuple[str, ...]] = {
    'felony': ('felony',),
    'assault': ('assault', 'aggravated assault', 'partner or family member assault'),
    'dui_repeat': ('dui-3rd', 'dui 3rd', 'dui third', 'dui 4th', 'dui fourth'),
    'burglary': ('burglary', 'burglary tools', 'aggravated burglary'),
}

SUBSCRIBER_ALERT_COLUMNS: tuple[tuple[str, str], ...] = (
    ('agency_name', "TEXT NOT NULL DEFAULT ''"),
    ('phone_number_sms', "TEXT NOT NULL DEFAULT ''"),
    ('counties_of_interest', "TEXT NOT NULL DEFAULT '[]'"),
    ('charge_types_of_interest', "TEXT NOT NULL DEFAULT '[\"all\"]'"),
    ('subscription_status', "TEXT NOT NULL DEFAULT 'active'"),
)


def ensure_bail_bonds_alert_schema(conn: sqlite3.Connection) -> None:
    existing_columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info('subscribers')").fetchall()
    }
    for column_name, definition in SUBSCRIBER_ALERT_COLUMNS:
        if column_name not in existing_columns:
            conn.execute(f'ALTER TABLE subscribers ADD COLUMN {column_name} {definition}')
            existing_columns.add(column_name)

    conn.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_subscribers_bail_bonds_active
        ON subscribers(subscription_status, active)
        '''
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS bail_bonds_sms_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subscriber_id INTEGER NOT NULL,
            booking_id INTEGER NOT NULL,
            county_slug TEXT NOT NULL DEFAULT '',
            phone_number_sms TEXT NOT NULL DEFAULT '',
            sms_body TEXT NOT NULL DEFAULT '',
            delivery_status TEXT NOT NULL DEFAULT 'queued',
            provider_message_id TEXT,
            error_message TEXT NOT NULL DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            delivered_at TEXT
        )
        '''
    )
    conn.execute(
        '''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_bail_bonds_sms_delivery_unique
        ON bail_bonds_sms_deliveries(subscriber_id, booking_id)
        '''
    )
    conn.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_bail_bonds_sms_delivery_status
        ON bail_bonds_sms_deliveries(delivery_status, created_at)
        '''
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS telegram_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            booking_id INTEGER NOT NULL,
            county_slug TEXT NOT NULL DEFAULT '',
            message_text TEXT NOT NULL DEFAULT '',
            delivery_status TEXT NOT NULL DEFAULT 'queued',
            telegram_message_id INTEGER,
            error_message TEXT NOT NULL DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            delivered_at TEXT
        )
        '''
    )
    conn.execute(
        '''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_telegram_deliveries_unique
        ON telegram_deliveries(chat_id, booking_id)
        '''
    )
    conn.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_telegram_deliveries_status
        ON telegram_deliveries(delivery_status, created_at)
        '''
    )


def _normalize_county(value: Any) -> str:
    text = str(value or '').strip().lower()
    text = re.sub(r'\s+county$', '', text)
    return re.sub(r'[^a-z0-9]+', '-', text).strip('-')


def _normalize_phone(value: Any) -> str:
    digits = ''.join(ch for ch in str(value or '') if ch.isdigit())
    if len(digits) == 10:
        return f'+1{digits}'
    if len(digits) == 11 and digits.startswith('1'):
        return f'+{digits}'
    return str(value or '').strip()


def _json_list(raw_value: Any) -> list[str]:
    if raw_value in (None, ''):
        return []
    if isinstance(raw_value, list):
        values = raw_value
    else:
        try:
            values = json.loads(raw_value)
        except (TypeError, ValueError, json.JSONDecodeError):
            values = str(raw_value).split(',')
    output: list[str] = []
    for value in values:
        token = str(value or '').strip()
        if token:
            output.append(token)
    return output


def _extract_charge_text(entry: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ('charges_summary', 'charge', 'charges', 'details', 'incident_type'):
        value = entry.get(key)
        if isinstance(value, list):
            parts.extend(str(item or '').strip() for item in value if str(item or '').strip())
        elif value:
            parts.append(str(value).strip())
    return ' | '.join(parts)


def _match_charge_types(charge_text: str) -> tuple[list[str], list[str]]:
    haystack = charge_text.lower()
    matched_categories: list[str] = []
    matched_keywords: list[str] = []

    for category, keywords in HIGH_INTENT_CHARGE_RULES.items():
        category_hit = False
        for keyword in keywords:
            if keyword in haystack:
                category_hit = True
                if keyword not in matched_keywords:
                    matched_keywords.append(keyword)
        if category_hit:
            matched_categories.append(category)

    return matched_categories, matched_keywords


def _county_match(subscriber: sqlite3.Row, county_slug: str) -> bool:
    counties = _json_list(subscriber['counties_of_interest'] or subscriber['counties'])
    if not counties:
        return True
    normalized_counties = {_normalize_county(value) for value in counties}
    return 'all' in normalized_counties or county_slug in normalized_counties


def _charge_match(subscriber: sqlite3.Row, matched_categories: list[str], matched_keywords: list[str]) -> bool:
    wanted_charge_types = _json_list(subscriber['charge_types_of_interest'])
    if not wanted_charge_types:
        return True
    normalized_targets = {str(value or '').strip().lower() for value in wanted_charge_types if str(value or '').strip()}
    if 'all' in normalized_targets:
        return True
    actual_matches = {value.lower() for value in [*matched_categories, *matched_keywords]}
    return bool(normalized_targets & actual_matches)


def build_sms_alert(alert: dict[str, Any]) -> str:
    booking = alert['booking']
    charge_labels = ', '.join(alert['matched_keywords']) or 'felony booking'
    return (
        f"{booking['county_name']} County alert: {booking['person_name']} booked on {charge_labels}. "
        f"Booked {booking.get('booking_at') or 'recently'}. Montana Blotter lead window: 4 hours."
    )


def get_telegram_chat_id(county_slug: str) -> str | None:
    mapping = {
        'cascade': (getattr(config, 'TELEGRAM_TARGET_CASCADE', '') or '').strip(),
        'yellowstone': (getattr(config, 'TELEGRAM_TARGET_YELLOWSTONE', '') or '').strip(),
    }
    chat_id = mapping.get(county_slug) or (getattr(config, 'TELEGRAM_TARGET_DEFAULT', '') or '').strip()
    return chat_id or None


def build_telegram_alert(booking: dict[str, Any], matched_keywords: list[str]) -> str:
    county = booking.get('county_name') or booking.get('county_slug', '').title()
    name = booking.get('person_name', 'Unknown')
    charges = ', '.join(matched_keywords) or 'felony booking'
    booked_at = booking.get('booking_at') or 'recently'
    agency = booking.get('agency') or f'{county} County'
    return (
        f'🚨 <b>{county} County Booking Alert</b>\n\n'
        f'<b>Name:</b> {name}\n'
        f'<b>Charges:</b> {charges}\n'
        f'<b>Booked:</b> {booked_at}\n'
        f'<b>Agency:</b> {agency}\n\n'
        f'Montana Blotter — 4-hour bail window'
    )


def send_telegram_message(chat_id: str, text: str) -> tuple[bool, int | None, str]:
    token = (getattr(config, 'TELEGRAM_BOT_TOKEN', '') or '').strip()
    if not token:
        return False, None, 'missing_telegram_config'

    try:
        response = requests.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            json={'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'},
            timeout=20,
        )
    except requests.RequestException as exc:
        logger.warning('Telegram request failed for chat %s: %s', chat_id, exc)
        return False, None, str(exc)[:300]

    try:
        payload = response.json()
    except ValueError:
        payload = {}

    if response.status_code >= 400:
        error_message = str(payload.get('description') or response.text or 'telegram_http_error')
        logger.warning('Telegram returned HTTP %s for chat %s: %s', response.status_code, chat_id, error_message)
        return False, None, error_message[:300]

    message_id = payload.get('result', {}).get('message_id')
    return True, message_id, ''


def check_for_felony_bookings(
    new_data: list[dict[str, Any]],
    *,
    conn: sqlite3.Connection | None = None,
    db_path: str = config.DB_PATH,
) -> list[dict[str, Any]]:
    """
    Return alert payloads for active bail-bond subscribers.

    The matcher requires a county match when a subscriber has county preferences
    and a charge-type match when a subscriber has charge preferences. If either
    preference list is empty, that dimension is treated as a wildcard.
    """
    if not new_data:
        return []

    should_close = conn is None
    if conn is None:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
    ensure_bail_bonds_alert_schema(conn)

    subscribers = conn.execute(
        '''
        SELECT
            id,
            email,
            counties,
            active,
            agency_name,
            COALESCE(NULLIF(phone_number_sms, ''), NULLIF(phone, ''), '') AS phone_number_sms,
            counties_of_interest,
            charge_types_of_interest,
            subscription_status
        FROM subscribers
        WHERE active = 1
          AND lower(COALESCE(subscription_status, 'active')) IN ('active', 'trialing')
          AND trim(COALESCE(phone_number_sms, phone, '')) != ''
        ORDER BY id ASC
        '''
    ).fetchall()
    if should_close:
        conn.close()

    alerts: list[dict[str, Any]] = []
    for entry in new_data:
        charge_text = _extract_charge_text(entry)
        matched_categories, matched_keywords = _match_charge_types(charge_text)
        if not matched_categories:
            continue

        county_name = str(entry.get('county_name') or entry.get('county') or '').strip()
        county_slug = _normalize_county(entry.get('county_slug') or county_name)
        normalized_entry = {
            **entry,
            'county_name': county_name or str(entry.get('county_slug') or '').strip().title(),
            'county_slug': county_slug,
            'charges_summary': charge_text,
            'person_name': str(entry.get('person_name') or entry.get('name') or 'Unknown').strip(),
        }

        for subscriber in subscribers:
            if not _county_match(subscriber, county_slug):
                continue
            if not _charge_match(subscriber, matched_categories, matched_keywords):
                continue

            alert = {
                'subscriber_id': subscriber['id'],
                'agency_name': subscriber['agency_name'] or subscriber['email'],
                'phone_number_sms': _normalize_phone(subscriber['phone_number_sms']),
                'county_name': normalized_entry['county_name'],
                'booking_id': normalized_entry.get('booking_id'),
                'county_slug': normalized_entry.get('county_slug', ''),
                'matched_charge_types': matched_categories,
                'matched_keywords': matched_keywords,
                'booking': normalized_entry,
            }
            alert['sms_body'] = build_sms_alert(alert)
            alerts.append(alert)

    return alerts


def send_twilio_sms(phone_number_sms: str, sms_body: str) -> tuple[bool, str, str]:
    account_sid = (getattr(config, 'TWILIO_ACCOUNT_SID', '') or '').strip()
    auth_token = (getattr(config, 'TWILIO_AUTH_TOKEN', '') or '').strip()
    from_number = (getattr(config, 'TWILIO_FROM_NUMBER', '') or '').strip()
    if not (account_sid and auth_token and from_number):
        return False, '', 'missing_twilio_config'

    try:
        response = requests.post(
            f'https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json',
            auth=(account_sid, auth_token),
            data={
                'To': phone_number_sms,
                'From': from_number,
                'Body': sms_body,
            },
            timeout=20,
        )
    except requests.RequestException as exc:
        logger.warning('Twilio request failed for %s: %s', phone_number_sms, exc)
        return False, '', str(exc)[:300]

    try:
        payload = response.json()
    except ValueError:
        payload = {}

    provider_message_id = str(payload.get('sid') or '')
    if response.status_code >= 400:
        error_message = str(payload.get('message') or response.text or 'twilio_http_error')
        logger.warning(
            'Twilio returned HTTP %s for %s: %s',
            response.status_code,
            phone_number_sms,
            error_message,
        )
        return False, provider_message_id, error_message[:300]

    return True, provider_message_id, ''


def dispatch_felony_booking_alerts(
    conn: sqlite3.Connection,
    new_data: list[dict[str, Any]],
    *,
    send_sms: Any = None,
) -> dict[str, int]:
    ensure_bail_bonds_alert_schema(conn)
    sender = send_sms or send_twilio_sms
    alerts = check_for_felony_bookings(new_data, conn=conn)

    sent = 0
    failed = 0
    skipped = 0
    for alert in alerts:
        booking_id = alert.get('booking_id')
        if not booking_id:
            skipped += 1
            continue

        existing = conn.execute(
            '''
            SELECT id, delivery_status
            FROM bail_bonds_sms_deliveries
            WHERE subscriber_id = ? AND booking_id = ?
            ''',
            (alert['subscriber_id'], booking_id),
        ).fetchone()
        if existing and existing['delivery_status'] == 'sent':
            skipped += 1
            continue

        was_sent, provider_message_id, error_message = sender(
            alert['phone_number_sms'],
            alert['sms_body'],
        )
        delivery_status = 'sent' if was_sent else 'failed'

        if existing:
            conn.execute(
                '''
                UPDATE bail_bonds_sms_deliveries
                SET county_slug = ?,
                    phone_number_sms = ?,
                    sms_body = ?,
                    delivery_status = ?,
                    provider_message_id = ?,
                    error_message = ?,
                    delivered_at = CASE WHEN ? = 'sent' THEN datetime('now') ELSE NULL END
                WHERE id = ?
                ''',
                (
                    alert.get('county_slug', ''),
                    alert['phone_number_sms'],
                    alert['sms_body'],
                    delivery_status,
                    provider_message_id,
                    error_message,
                    delivery_status,
                    existing['id'],
                ),
            )
        else:
            conn.execute(
                '''
                INSERT INTO bail_bonds_sms_deliveries (
                    subscriber_id,
                    booking_id,
                    county_slug,
                    phone_number_sms,
                    sms_body,
                    delivery_status,
                    provider_message_id,
                    error_message,
                    delivered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CASE WHEN ? = 'sent' THEN datetime('now') ELSE NULL END)
                ''',
                (
                    alert['subscriber_id'],
                    booking_id,
                    alert.get('county_slug', ''),
                    alert['phone_number_sms'],
                    alert['sms_body'],
                    delivery_status,
                    provider_message_id,
                    error_message,
                    delivery_status,
                ),
            )

        if was_sent:
            sent += 1
        else:
            failed += 1

    return {
        'matched': len(alerts),
        'sent': sent,
        'failed': failed,
        'skipped': skipped,
    }


def dispatch_telegram_booking_alerts(
    conn: sqlite3.Connection,
    new_data: list[dict[str, Any]],
    *,
    send_telegram: Any = None,
) -> dict[str, int]:
    ensure_bail_bonds_alert_schema(conn)
    sender = send_telegram or send_telegram_message
    alerts = check_for_felony_bookings(new_data, conn=conn)

    # Deduplicate to one alert per booking_id (Telegram is per-channel, not per-subscriber)
    seen_booking_ids: set[int] = set()
    unique_alerts: list[dict[str, Any]] = []
    for alert in alerts:
        bid = alert.get('booking_id')
        if bid and bid not in seen_booking_ids:
            seen_booking_ids.add(bid)
            unique_alerts.append(alert)

    sent = 0
    failed = 0
    skipped = 0

    for alert in unique_alerts:
        booking_id = alert['booking_id']
        booking = alert['booking']
        county_slug = alert.get('county_slug', '')
        chat_id = get_telegram_chat_id(county_slug)
        if not chat_id:
            skipped += 1
            continue

        existing = conn.execute(
            'SELECT id, delivery_status FROM telegram_deliveries WHERE chat_id = ? AND booking_id = ?',
            (chat_id, booking_id),
        ).fetchone()
        if existing and existing['delivery_status'] == 'sent':
            skipped += 1
            continue

        text = build_telegram_alert(booking, alert['matched_keywords'])
        was_sent, telegram_message_id, error_message = sender(chat_id, text)
        delivery_status = 'sent' if was_sent else 'failed'

        if existing:
            conn.execute(
                '''
                UPDATE telegram_deliveries
                SET county_slug = ?, message_text = ?, delivery_status = ?,
                    telegram_message_id = ?, error_message = ?,
                    delivered_at = CASE WHEN ? = 'sent' THEN datetime('now') ELSE NULL END
                WHERE id = ?
                ''',
                (county_slug, text, delivery_status, telegram_message_id,
                 error_message, delivery_status, existing['id']),
            )
        else:
            conn.execute(
                '''
                INSERT INTO telegram_deliveries (
                    chat_id, booking_id, county_slug, message_text,
                    delivery_status, telegram_message_id, error_message,
                    delivered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, CASE WHEN ? = 'sent' THEN datetime('now') ELSE NULL END)
                ''',
                (chat_id, booking_id, county_slug, text,
                 delivery_status, telegram_message_id, error_message, delivery_status),
            )

        if was_sent:
            sent += 1
        else:
            failed += 1

    return {
        'matched': len(unique_alerts),
        'sent': sent,
        'failed': failed,
        'skipped': skipped,
    }
