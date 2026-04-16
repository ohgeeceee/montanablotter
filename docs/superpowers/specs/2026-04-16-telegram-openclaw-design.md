# Telegram OpenClaw BailBot Integration

**Date:** 2026-04-16  
**Status:** Approved  
**File:** `bail_bonds_alerts.py`

## Overview

Add Telegram channel notifications to the felony booking alert pipeline, running alongside the existing Twilio SMS subscriber alerts. Alerts route to county-specific Telegram group chats (OpenClaw BailBot channels) based on the booking's county slug.

## Architecture

All new code lives in `bail_bonds_alerts.py`, keeping all booking-alert dispatch logic in one place. The Telegram dispatch is structurally separate from Twilio: Twilio is per-subscriber (each bondsman's phone), Telegram is per-county-channel (one message to a shared group chat per county).

## Components

### 1. County Routing — `get_telegram_chat_id(county_slug)`

Maps a county slug to the appropriate config target:

| County slug  | Config var                      |
|--------------|---------------------------------|
| `cascade`    | `config.TELEGRAM_TARGET_CASCADE`    |
| `yellowstone`| `config.TELEGRAM_TARGET_YELLOWSTONE`|
| anything else| `config.TELEGRAM_TARGET_DEFAULT`    |

Returns `None` if the resolved config value is empty/unset — callers treat `None` as a skip.

### 2. Message Builder — `build_telegram_alert(booking)`

Uses Telegram HTML parse mode (more reliable than MarkdownV2 for dynamic content — no escaping edge cases). Format:

```
🚨 <b>{County Name} Booking Alert</b>

<b>Name:</b> {person_name}
<b>Charges:</b> {matched_keywords joined by ", "}
<b>Booked:</b> {booking_at or "recently"}
<b>Agency:</b> {agency or county name}

Montana Blotter — 4-hour bail window
```

### 3. Sender — `send_telegram_message(chat_id, text)`

`POST https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage`  
Payload: `{"chat_id": chat_id, "text": text, "parse_mode": "HTML"}`  
Returns: `(success: bool, message_id: int | None, error: str)`  
Timeout: 20 seconds. Returns `(False, None, "missing_telegram_config")` if token is unset.

### 4. Dedup Table — `telegram_deliveries`

Added inside `ensure_bail_bonds_alert_schema()`:

```sql
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
    delivered_at TEXT,
    UNIQUE(chat_id, booking_id)
)
```

The `UNIQUE(chat_id, booking_id)` constraint prevents duplicate channel alerts for the same booking.

### 5. Dispatcher — `dispatch_telegram_booking_alerts(conn, new_data)`

1. Calls `check_for_felony_bookings(new_data, conn=conn)` to reuse existing charge matching
2. Deduplicates matched bookings by `booking_id` (multiple subscribers may match the same booking; Telegram only needs one send per channel per booking)
3. For each unique booking, resolves `chat_id` via `get_telegram_chat_id(county_slug)`; skips if `None`
4. Checks `telegram_deliveries` for existing `sent` record — skips if already delivered
5. Calls `send_telegram_message(chat_id, text)`, records result in `telegram_deliveries`
6. Returns `{"matched": N, "sent": N, "failed": N, "skipped": N}`

## Integration Point

`jail_booking_ingest.py:1355` — after `dispatch_felony_booking_alerts()`, add:

```python
telegram_summary = dispatch_telegram_booking_alerts(conn, stats.alert_candidates)
```

The note string appended to the run log is extended to include Telegram counts. A Telegram failure does not affect SMS dispatch and vice versa.

## Error Handling

- Missing `TELEGRAM_BOT_TOKEN` → graceful no-op, logs a warning
- Missing county target (`CASCADE`/`YELLOWSTONE`/`DEFAULT` empty) → skips that booking, no error
- Network error → `delivery_status = "failed"`, error logged, next booking continues
- HTTP 4xx/5xx from Bot API → same as network error

## Environment Variables Required

```
MB_TELEGRAM_BOT_TOKEN=<bot token from @BotFather>
MB_TELEGRAM_TARGET_DEFAULT=<chat_id for default channel>
MB_TELEGRAM_TARGET_CASCADE=<chat_id for Cascade County channel>
MB_TELEGRAM_TARGET_YELLOWSTONE=<chat_id for Yellowstone County channel>
```

These are already defined in `config.py:226–230`. They must be set in `.env`.

## Testing

- Unit tests added to `tests/test_bail_bonds_alerts.py` covering:
  - `get_telegram_chat_id()` routing for cascade, yellowstone, and unknown slugs
  - `build_telegram_alert()` output format
  - `send_telegram_message()` with mocked `requests.post`
  - `dispatch_telegram_booking_alerts()` dedup (second call for same booking_id is skipped)
  - Graceful no-op when `TELEGRAM_BOT_TOKEN` is unset
