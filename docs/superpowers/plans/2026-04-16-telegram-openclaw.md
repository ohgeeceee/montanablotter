# Telegram OpenClaw BailBot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Telegram channel notifications to the felony booking alert pipeline, routing by county, running alongside existing Twilio SMS.

**Architecture:** All new code lives in `bail_bonds_alerts.py`. Telegram is per-county-channel (not per-subscriber like Twilio), so dispatch deduplicates by `(chat_id, booking_id)` in a new `telegram_deliveries` table. Called from `jail_booking_ingest.py` after the Twilio dispatch.

**Tech Stack:** Python 3.12, `requests`, Telegram Bot API (`sendMessage`), SQLite, `unittest`

---

## File Map

| File | Change |
|------|--------|
| `bail_bonds_alerts.py` | Add: `telegram_deliveries` schema, `get_telegram_chat_id()`, `build_telegram_alert()`, `send_telegram_message()`, `dispatch_telegram_booking_alerts()` |
| `jail_booking_ingest.py` | Modify: import + call `dispatch_telegram_booking_alerts()` after Twilio dispatch at line ~1356 |
| `tests/test_bail_bonds_alerts.py` | Add: 5 new test methods to the existing `BailBondsAlertTests` class |

---

### Task 1: Add `telegram_deliveries` table to schema

**Files:**
- Modify: `bail_bonds_alerts.py` (inside `ensure_bail_bonds_alert_schema()`)
- Test: `tests/test_bail_bonds_alerts.py`

- [ ] **Step 1: Write the failing test**

Add this method to the `BailBondsAlertTests` class in `tests/test_bail_bonds_alerts.py`:

```python
def test_ensure_schema_creates_telegram_deliveries_table(self) -> None:
    path = self._make_db()
    try:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        ensure_bail_bonds_alert_schema(conn)
        conn.commit()

        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        self.assertIn('telegram_deliveries', tables)

        cols = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info('telegram_deliveries')"
            ).fetchall()
        }
        for col in ('id', 'chat_id', 'booking_id', 'delivery_status',
                    'telegram_message_id', 'error_message', 'created_at', 'delivered_at'):
            self.assertIn(col, cols)

        conn.close()
    finally:
        os.remove(path)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /root/montanablotter && source venv/bin/activate
python -m pytest tests/test_bail_bonds_alerts.py::BailBondsAlertTests::test_ensure_schema_creates_telegram_deliveries_table -v
```

Expected: `FAILED` — `AssertionError: 'telegram_deliveries' not found`

- [ ] **Step 3: Add the table to `ensure_bail_bonds_alert_schema()`**

In `bail_bonds_alerts.py`, at the end of `ensure_bail_bonds_alert_schema()`, add:

```python
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
        delivered_at TEXT,
        UNIQUE(chat_id, booking_id)
    )
    '''
)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_bail_bonds_alerts.py::BailBondsAlertTests::test_ensure_schema_creates_telegram_deliveries_table -v
```

Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git add bail_bonds_alerts.py tests/test_bail_bonds_alerts.py
git commit -m "feat(telegram): add telegram_deliveries schema"
```

---

### Task 2: Add `get_telegram_chat_id()` and `build_telegram_alert()`

**Files:**
- Modify: `bail_bonds_alerts.py`
- Test: `tests/test_bail_bonds_alerts.py`

- [ ] **Step 1: Write the failing tests**

Add these two test methods to `BailBondsAlertTests`:

```python
def test_get_telegram_chat_id_routes_by_county(self) -> None:
    import unittest.mock as mock
    with mock.patch.object(__import__('config'), 'TELEGRAM_TARGET_CASCADE', '-1001111111111'), \
         mock.patch.object(__import__('config'), 'TELEGRAM_TARGET_YELLOWSTONE', '-1002222222222'), \
         mock.patch.object(__import__('config'), 'TELEGRAM_TARGET_DEFAULT', '-1003333333333'):
        from bail_bonds_alerts import get_telegram_chat_id
        self.assertEqual(get_telegram_chat_id('cascade'), '-1001111111111')
        self.assertEqual(get_telegram_chat_id('yellowstone'), '-1002222222222')
        self.assertEqual(get_telegram_chat_id('missoula'), '-1003333333333')
        self.assertEqual(get_telegram_chat_id('flathead'), '-1003333333333')

def test_get_telegram_chat_id_returns_none_when_target_unset(self) -> None:
    import unittest.mock as mock
    with mock.patch.object(__import__('config'), 'TELEGRAM_TARGET_DEFAULT', ''), \
         mock.patch.object(__import__('config'), 'TELEGRAM_TARGET_CASCADE', ''), \
         mock.patch.object(__import__('config'), 'TELEGRAM_TARGET_YELLOWSTONE', ''):
        from bail_bonds_alerts import get_telegram_chat_id
        self.assertIsNone(get_telegram_chat_id('cascade'))
        self.assertIsNone(get_telegram_chat_id('missoula'))

def test_build_telegram_alert_contains_key_fields(self) -> None:
    from bail_bonds_alerts import build_telegram_alert
    booking = {
        'county_name': 'Cascade',
        'county_slug': 'cascade',
        'person_name': 'John Smith',
        'booking_at': '2026-04-16 14:32:00',
        'charges_summary': 'Felony Burglary',
    }
    matched_keywords = ['felony', 'burglary']
    text = build_telegram_alert(booking, matched_keywords)
    self.assertIn('Cascade', text)
    self.assertIn('John Smith', text)
    self.assertIn('felony', text)
    self.assertIn('burglary', text)
    self.assertIn('2026-04-16', text)
    self.assertIn('<b>', text)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_bail_bonds_alerts.py::BailBondsAlertTests::test_get_telegram_chat_id_routes_by_county tests/test_bail_bonds_alerts.py::BailBondsAlertTests::test_get_telegram_chat_id_returns_none_when_target_unset tests/test_bail_bonds_alerts.py::BailBondsAlertTests::test_build_telegram_alert_contains_key_fields -v
```

Expected: `FAILED` — `ImportError: cannot import name 'get_telegram_chat_id'`

- [ ] **Step 3: Implement the two functions in `bail_bonds_alerts.py`**

Add after the existing `build_sms_alert()` function:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_bail_bonds_alerts.py::BailBondsAlertTests::test_get_telegram_chat_id_routes_by_county tests/test_bail_bonds_alerts.py::BailBondsAlertTests::test_get_telegram_chat_id_returns_none_when_target_unset tests/test_bail_bonds_alerts.py::BailBondsAlertTests::test_build_telegram_alert_contains_key_fields -v
```

Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add bail_bonds_alerts.py tests/test_bail_bonds_alerts.py
git commit -m "feat(telegram): add county routing and message builder"
```

---

### Task 3: Add `send_telegram_message()`

**Files:**
- Modify: `bail_bonds_alerts.py`
- Test: `tests/test_bail_bonds_alerts.py`

- [ ] **Step 1: Write the failing tests**

Add to `BailBondsAlertTests`:

```python
def test_send_telegram_message_returns_false_when_token_unset(self) -> None:
    import unittest.mock as mock
    with mock.patch.object(__import__('config'), 'TELEGRAM_BOT_TOKEN', ''):
        from bail_bonds_alerts import send_telegram_message
        success, message_id, error = send_telegram_message('-1001111111111', 'hello')
        self.assertFalse(success)
        self.assertIsNone(message_id)
        self.assertEqual(error, 'missing_telegram_config')

def test_send_telegram_message_posts_to_bot_api(self) -> None:
    import unittest.mock as mock
    fake_response = mock.Mock()
    fake_response.status_code = 200
    fake_response.json.return_value = {'ok': True, 'result': {'message_id': 42}}

    with mock.patch.object(__import__('config'), 'TELEGRAM_BOT_TOKEN', 'bot123:ABC'), \
         mock.patch('bail_bonds_alerts.requests.post', return_value=fake_response) as mock_post:
        from bail_bonds_alerts import send_telegram_message
        success, message_id, error = send_telegram_message('-1001111111111', '<b>Alert</b>')
        self.assertTrue(success)
        self.assertEqual(message_id, 42)
        self.assertEqual(error, '')
        call_kwargs = mock_post.call_args
        self.assertIn('bot123:ABC', call_kwargs[0][0])
        self.assertEqual(call_kwargs[1]['json']['chat_id'], '-1001111111111')
        self.assertEqual(call_kwargs[1]['json']['parse_mode'], 'HTML')

def test_send_telegram_message_returns_false_on_api_error(self) -> None:
    import unittest.mock as mock
    fake_response = mock.Mock()
    fake_response.status_code = 400
    fake_response.json.return_value = {'ok': False, 'description': 'Bad Request: chat not found'}

    with mock.patch.object(__import__('config'), 'TELEGRAM_BOT_TOKEN', 'bot123:ABC'), \
         mock.patch('bail_bonds_alerts.requests.post', return_value=fake_response):
        from bail_bonds_alerts import send_telegram_message
        success, message_id, error = send_telegram_message('-9999', 'test')
        self.assertFalse(success)
        self.assertIsNone(message_id)
        self.assertIn('chat not found', error)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_bail_bonds_alerts.py::BailBondsAlertTests::test_send_telegram_message_returns_false_when_token_unset tests/test_bail_bonds_alerts.py::BailBondsAlertTests::test_send_telegram_message_posts_to_bot_api tests/test_bail_bonds_alerts.py::BailBondsAlertTests::test_send_telegram_message_returns_false_on_api_error -v
```

Expected: `FAILED` — `ImportError: cannot import name 'send_telegram_message'`

- [ ] **Step 3: Implement `send_telegram_message()` in `bail_bonds_alerts.py`**

Add after `build_telegram_alert()`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_bail_bonds_alerts.py::BailBondsAlertTests::test_send_telegram_message_returns_false_when_token_unset tests/test_bail_bonds_alerts.py::BailBondsAlertTests::test_send_telegram_message_posts_to_bot_api tests/test_bail_bonds_alerts.py::BailBondsAlertTests::test_send_telegram_message_returns_false_on_api_error -v
```

Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add bail_bonds_alerts.py tests/test_bail_bonds_alerts.py
git commit -m "feat(telegram): add send_telegram_message with error handling"
```

---

### Task 4: Add `dispatch_telegram_booking_alerts()`

**Files:**
- Modify: `bail_bonds_alerts.py`
- Test: `tests/test_bail_bonds_alerts.py`

- [ ] **Step 1: Write the failing tests**

Add to `BailBondsAlertTests`:

```python
def test_dispatch_telegram_sends_to_channel_and_dedupes(self) -> None:
    import unittest.mock as mock
    path = self._make_db()
    sent_calls: list[tuple[str, str]] = []

    def fake_send(chat_id: str, text: str) -> tuple[bool, int | None, str]:
        sent_calls.append((chat_id, text))
        return True, 99, ''

    try:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        ensure_bail_bonds_alert_schema(conn)
        conn.execute(
            '''
            INSERT INTO subscribers (
                email, token, phone_number_sms, agency_name,
                counties_of_interest, charge_types_of_interest, subscription_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            ('a@b.com', 'tok', '4065551234', 'Bonds Inc',
             '["cascade"]', '["felony"]', 'active'),
        )
        conn.commit()

        payload = [{
            'booking_id': 55,
            'county_slug': 'cascade',
            'county_name': 'Cascade',
            'person_name': 'Jane Doe',
            'booking_at': '2026-04-16 10:00:00',
            'charges_summary': 'Felony Assault',
        }]

        with mock.patch.object(__import__('config'), 'TELEGRAM_TARGET_CASCADE', '-1001111'), \
             mock.patch.object(__import__('config'), 'TELEGRAM_TARGET_YELLOWSTONE', ''), \
             mock.patch.object(__import__('config'), 'TELEGRAM_TARGET_DEFAULT', '-1009999'), \
             mock.patch.object(__import__('config'), 'TELEGRAM_BOT_TOKEN', 'tok:ABC'):
            from bail_bonds_alerts import dispatch_telegram_booking_alerts
            first = dispatch_telegram_booking_alerts(conn, payload, send_telegram=fake_send)
            second = dispatch_telegram_booking_alerts(conn, payload, send_telegram=fake_send)

        conn.commit()
        row = conn.execute(
            "SELECT delivery_status, telegram_message_id FROM telegram_deliveries WHERE booking_id = 55"
        ).fetchone()
        conn.close()

        self.assertEqual(first['sent'], 1)
        self.assertEqual(second['skipped'], 1)
        self.assertEqual(len(sent_calls), 1)
        self.assertEqual(sent_calls[0][0], '-1001111')
        self.assertIn('Jane Doe', sent_calls[0][1])
        self.assertEqual(row['delivery_status'], 'sent')
        self.assertEqual(row['telegram_message_id'], 99)
    finally:
        os.remove(path)

def test_dispatch_telegram_skips_when_no_matching_charges(self) -> None:
    import unittest.mock as mock
    path = self._make_db()
    try:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        ensure_bail_bonds_alert_schema(conn)
        conn.commit()

        payload = [{
            'booking_id': 66,
            'county_slug': 'cascade',
            'county_name': 'Cascade',
            'person_name': 'Nobody',
            'booking_at': '2026-04-16 11:00:00',
            'charges_summary': 'Jaywalking',
        }]

        with mock.patch.object(__import__('config'), 'TELEGRAM_TARGET_CASCADE', '-1001111'), \
             mock.patch.object(__import__('config'), 'TELEGRAM_TARGET_YELLOWSTONE', ''), \
             mock.patch.object(__import__('config'), 'TELEGRAM_TARGET_DEFAULT', '-1009999'), \
             mock.patch.object(__import__('config'), 'TELEGRAM_BOT_TOKEN', 'tok:ABC'):
            from bail_bonds_alerts import dispatch_telegram_booking_alerts
            result = dispatch_telegram_booking_alerts(conn, payload)

        conn.close()
        self.assertEqual(result['matched'], 0)
        self.assertEqual(result['sent'], 0)
    finally:
        os.remove(path)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_bail_bonds_alerts.py::BailBondsAlertTests::test_dispatch_telegram_sends_to_channel_and_dedupes tests/test_bail_bonds_alerts.py::BailBondsAlertTests::test_dispatch_telegram_skips_when_no_matching_charges -v
```

Expected: `FAILED` — `ImportError: cannot import name 'dispatch_telegram_booking_alerts'`

- [ ] **Step 3: Implement `dispatch_telegram_booking_alerts()` in `bail_bonds_alerts.py`**

Add at the end of `bail_bonds_alerts.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_bail_bonds_alerts.py::BailBondsAlertTests::test_dispatch_telegram_sends_to_channel_and_dedupes tests/test_bail_bonds_alerts.py::BailBondsAlertTests::test_dispatch_telegram_skips_when_no_matching_charges -v
```

Expected: `2 passed`

- [ ] **Step 5: Run the full test suite**

```bash
python -m pytest tests/test_bail_bonds_alerts.py -v
```

Expected: all tests pass (no regressions)

- [ ] **Step 6: Commit**

```bash
git add bail_bonds_alerts.py tests/test_bail_bonds_alerts.py
git commit -m "feat(telegram): add dispatch_telegram_booking_alerts"
```

---

### Task 5: Wire into `jail_booking_ingest.py`

**Files:**
- Modify: `jail_booking_ingest.py` (import at line ~34, call at line ~1356)

- [ ] **Step 1: Update the import at the top of `jail_booking_ingest.py`**

Find line ~34:
```python
from bail_bonds_alerts import dispatch_felony_booking_alerts
```

Replace with:
```python
from bail_bonds_alerts import dispatch_felony_booking_alerts, dispatch_telegram_booking_alerts
```

- [ ] **Step 2: Add the Telegram dispatch call after the Twilio call**

Find this block at line ~1354:

```python
    alert_summary = {'matched': 0, 'sent': 0, 'failed': 0, 'skipped': 0}
    if not dry_run and getattr(config, 'BAIL_BONDS_ALERTS_ENABLED', True) and stats.alert_candidates:
        alert_summary = dispatch_felony_booking_alerts(conn, stats.alert_candidates)
    note = f"Fetched {stats.fetched_count} records from {source['county_name']}."
    if stats.alert_candidates:
        note += (
            f" Bondsman SMS matched={alert_summary['matched']} sent={alert_summary['sent']} "
            f"failed={alert_summary['failed']} skipped={alert_summary['skipped']}."
        )
```

Replace with:

```python
    alert_summary = {'matched': 0, 'sent': 0, 'failed': 0, 'skipped': 0}
    telegram_summary = {'matched': 0, 'sent': 0, 'failed': 0, 'skipped': 0}
    if not dry_run and getattr(config, 'BAIL_BONDS_ALERTS_ENABLED', True) and stats.alert_candidates:
        alert_summary = dispatch_felony_booking_alerts(conn, stats.alert_candidates)
        telegram_summary = dispatch_telegram_booking_alerts(conn, stats.alert_candidates)
    note = f"Fetched {stats.fetched_count} records from {source['county_name']}."
    if stats.alert_candidates:
        note += (
            f" Bondsman SMS matched={alert_summary['matched']} sent={alert_summary['sent']} "
            f"failed={alert_summary['failed']} skipped={alert_summary['skipped']}."
            f" Telegram matched={telegram_summary['matched']} sent={telegram_summary['sent']} "
            f"failed={telegram_summary['failed']} skipped={telegram_summary['skipped']}."
        )
```

- [ ] **Step 3: Verify the app imports cleanly**

```bash
cd /root/montanablotter && source venv/bin/activate
python -c "import jail_booking_ingest; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Run the full test suite one final time**

```bash
python -m pytest tests/test_bail_bonds_alerts.py -v
```

Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add jail_booking_ingest.py
git commit -m "feat(telegram): wire dispatch_telegram_booking_alerts into ingest pipeline"
```

---

### Task 6: Set env vars and smoke test

- [ ] **Step 1: Add Telegram vars to `.env` if not already set**

Open `/root/montanablotter/.env` and ensure these lines exist with real values:

```
MB_TELEGRAM_BOT_TOKEN=<token from @BotFather>
MB_TELEGRAM_TARGET_DEFAULT=<chat_id>
MB_TELEGRAM_TARGET_CASCADE=<chat_id>
MB_TELEGRAM_TARGET_YELLOWSTONE=<chat_id>
```

To get a channel's `chat_id`: add the bot to the channel as admin, send a message, then call:
`https://api.telegram.org/bot<TOKEN>/getUpdates`

- [ ] **Step 2: Smoke test the sender directly**

```bash
cd /root/montanablotter && source venv/bin/activate
python - <<'EOF'
import config
from bail_bonds_alerts import send_telegram_message
ok, mid, err = send_telegram_message(config.TELEGRAM_TARGET_DEFAULT, '🚨 <b>Montana Blotter test</b>\n\nSmoke test — ignore.')
print(f"sent={ok} message_id={mid} error={err!r}")
EOF
```

Expected: `sent=True message_id=<integer> error=''`

- [ ] **Step 3: Restart the service**

```bash
systemctl restart montanablotter
systemctl status montanablotter
```

Expected: `active (running)`
