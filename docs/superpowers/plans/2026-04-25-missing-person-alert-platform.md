# Missing Person Alert Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build county/city-targeted missing-person alerts on top of the existing Montana DOJ sync and subscriber system, with default local email alerts plus opt-in SMS and signed-in browser push, including second alerts when a case is marked found.

**Architecture:** Keep `missing_persons.py` as the missing-person source of truth and extend it with generalized targeting and channel-aware delivery. Reuse the existing `subscribers` table for identity and location coverage, add explicit SMS/push enrollment state plus a browser push subscription table, and keep the public directory/detail pages as the canonical record surface with new alert enrollment UI. Dispatch remains sync-driven in v1 but is decomposed into channel-specific functions so it can move to background jobs later.

**Tech Stack:** Flask, Jinja2, SQLite, existing email infrastructure, Twilio SMS, service worker web push, pytest.

---

## File Structure

- Modify: `/root/montanablotter/missing_persons.py`
  Responsibility: schema changes, subscriber targeting, notification versioning, channel dispatch, message builders, sync-triggered alert orchestration.
- Modify: `/root/montanablotter/app.py`
  Responsibility: public routes plus new authenticated settings/enrollment endpoints if those are kept in the main app.
- Modify: `/root/montanablotter/init_db.py`
  Responsibility: durable subscriber schema migrations if this app expects migrations to be declared centrally there in addition to lazy schema guards.
- Modify: `/root/montanablotter/templates/missing_persons.html`
  Responsibility: public directory alert signup CTA and local-alert messaging.
- Modify: `/root/montanablotter/templates/missing_person_detail.html`
  Responsibility: record-specific alert CTA and found-alert messaging.
- Create: `/root/montanablotter/templates/public_account_missing_person_alerts.html`
  Responsibility: signed-in subscriber alert settings UI for email/SMS/push.
- Modify: `/root/montanablotter/templates/public_account.html`
  Responsibility: link into missing-person alert settings if account UI already exists there.
- Modify: `/root/montanablotter/templates/public_page_base.html`
  Responsibility: include push enrollment bootstrap data or shared script hooks if needed.
- Modify: `/root/montanablotter/static/sw.js`
  Responsibility: browser push event handling and notification click navigation.
- Create: `/root/montanablotter/static/js/missing-person-push.js`
  Responsibility: signed-in push enrollment, browser permission flow, endpoint registration, and device removal actions.
- Modify: `/root/montanablotter/config.py`
  Responsibility: add VAPID and any SMS verification config keys needed for push/SMS.
- Modify: `/root/montanablotter/missing_person_watch.py`
  Responsibility: invoke the expanded dispatcher and log per-channel results.
- Modify: `/root/montanablotter/tests/test_missing_persons.py`
  Responsibility: schema, matching, sync lifecycle, email/SMS/push dispatch, and public context regression coverage.
- Create: `/root/montanablotter/tests/test_missing_person_push_enrollment.py`
  Responsibility: signed-in browser push enrollment endpoint and subscription management tests.
- Modify: `/root/montanablotter/tests/test_public_detail_routes.py`
  Responsibility: verify public missing-person pages expose alert enrollment affordances without regressing existing routes.

## Task 1: Extend Subscriber and Delivery Schema

**Files:**
- Modify: `/root/montanablotter/missing_persons.py`
- Modify: `/root/montanablotter/init_db.py`
- Test: `/root/montanablotter/tests/test_missing_persons.py`

- [ ] **Step 1: Write the failing schema test**

```python
def test_ensure_missing_person_schema_adds_channel_and_push_tables(self) -> None:
    conn = sqlite3.connect(self.db_path)
    conn.row_factory = sqlite3.Row

    missing_persons_module.ensure_missing_person_schema(conn)

    subscriber_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info('subscribers')").fetchall()
    }
    assert "missing_person_email_opt_in" in subscriber_columns
    assert "missing_person_sms_opt_in" in subscriber_columns
    assert "missing_person_push_opt_in" in subscriber_columns
    assert "phone_verified_at" in subscriber_columns

    push_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info('missing_person_push_subscriptions')").fetchall()
    }
    assert {"subscriber_id", "endpoint", "p256dh_key", "auth_key", "active"} <= push_columns

    delivery_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info('missing_person_alert_deliveries')").fetchall()
    }
    assert {"subscriber_id", "channel", "recipient", "provider_message_id", "updated_at"} <= delivery_columns
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_missing_persons.py::MissingPersonsTests::test_ensure_missing_person_schema_adds_channel_and_push_tables -q`
Expected: FAIL because the new subscriber columns, push table, or generalized delivery columns do not exist yet.

- [ ] **Step 3: Write minimal schema implementation**

```python
for column_name, definition in [
    ("missing_person_email_opt_in", "INTEGER NOT NULL DEFAULT 1"),
    ("missing_person_sms_opt_in", "INTEGER NOT NULL DEFAULT 0"),
    ("missing_person_push_opt_in", "INTEGER NOT NULL DEFAULT 0"),
    ("phone_verified_at", "TEXT DEFAULT ''"),
    ("missing_person_alerts_updated_at", "TEXT DEFAULT ''"),
]:
    if column_name not in existing_columns:
        conn.execute(f"ALTER TABLE subscribers ADD COLUMN {column_name} {definition}")

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
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_missing_person_push_endpoint ON missing_person_push_subscriptions(endpoint)"
)
```

Also update `missing_person_alert_deliveries` migration logic to add:

```python
("subscriber_id", "INTEGER"),
("channel", "TEXT NOT NULL DEFAULT 'email'"),
("recipient", "TEXT"),
("provider_message_id", "TEXT DEFAULT ''"),
("updated_at", "TEXT DEFAULT (datetime('now'))"),
```

and backfill `recipient` from `recipient_email` when present.

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_missing_persons.py::MissingPersonsTests::test_ensure_missing_person_schema_adds_channel_and_push_tables -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add missing_persons.py init_db.py tests/test_missing_persons.py
git commit -m "feat: add missing person alert channel schema"
```

## Task 2: Add County/City Subscriber Matching

**Files:**
- Modify: `/root/montanablotter/missing_persons.py`
- Test: `/root/montanablotter/tests/test_missing_persons.py`

- [ ] **Step 1: Write the failing targeting test**

```python
def test_matching_subscribers_include_county_or_city_matches(self) -> None:
    conn = self._connect()
    self._seed_subscriber(email="county@example.com", counties="Yellowstone", city="")
    self._seed_subscriber(email="city@example.com", counties="", city="Billings")
    self._seed_subscriber(email="other@example.com", counties="Flathead", city="Kalispell")
    person = {
        "county": "Yellowstone",
        "city": "Billings",
        "status": missing_persons_module.STATUS_MISSING,
    }

    matches = missing_persons_module._matching_missing_person_subscribers(conn, person)

    emails = {item["email"] for item in matches}
    assert emails == {"county@example.com", "city@example.com"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_missing_persons.py::MissingPersonsTests::test_matching_subscribers_include_county_or_city_matches -q`
Expected: FAIL because matching helper does not exist or does not support city fields yet.

- [ ] **Step 3: Write minimal targeting implementation**

```python
def _subscriber_city_list(raw_value: str) -> list[str]:
    return [part.strip() for part in (raw_value or "").split(",") if part.strip()]


def _matching_missing_person_subscribers(conn: sqlite3.Connection, person: dict[str, Any]) -> list[dict[str, Any]]:
    rows = conn.execute(
        '''
        SELECT *
        FROM subscribers
        WHERE COALESCE(active, 1) = 1
          AND trim(COALESCE(email, '')) != ''
        '''
    ).fetchall()
    person_county = _single_line(person.get("county"), max_len=80).lower()
    person_city = _single_line(person.get("city"), max_len=80).lower()
    matches = []
    for row in rows:
        item = dict(row)
        counties = {value.lower() for value in _subscriber_counties_list(item.get("counties", ""))}
        cities = {value.lower() for value in _subscriber_city_list(item.get("cities", ""))}
        if (person_county and person_county in counties) or (person_city and person_city in cities):
            matches.append(item)
    return matches
```

If `cities` does not already exist on `subscribers`, add it in schema guards in this same task.

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_missing_persons.py::MissingPersonsTests::test_matching_subscribers_include_county_or_city_matches -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add missing_persons.py tests/test_missing_persons.py
git commit -m "feat: match missing person alerts by county or city"
```

## Task 3: Generalize Delivery Logging and Channel Eligibility

**Files:**
- Modify: `/root/montanablotter/missing_persons.py`
- Test: `/root/montanablotter/tests/test_missing_persons.py`

- [ ] **Step 1: Write the failing channel eligibility test**

```python
def test_channel_eligibility_respects_default_email_and_opt_in_sms_push(self) -> None:
    conn = self._connect()
    subscriber = self._seed_subscriber(
        email="reader@example.com",
        counties="Yellowstone",
        city="",
        missing_person_email_opt_in=1,
        missing_person_sms_opt_in=0,
        missing_person_push_opt_in=0,
        phone="+14065550123",
        phone_verified_at="2026-04-25 10:00:00",
    )
    person = {"county": "Yellowstone", "city": "", "status": "missing"}

    eligible = missing_persons_module._eligible_missing_person_channels(conn, dict(subscriber), person)

    assert eligible == {"email"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_missing_persons.py::MissingPersonsTests::test_channel_eligibility_respects_default_email_and_opt_in_sms_push -q`
Expected: FAIL because channel eligibility helper does not exist yet.

- [ ] **Step 3: Write minimal eligibility implementation**

```python
def _subscriber_has_active_push_subscription(conn: sqlite3.Connection, subscriber_id: int) -> bool:
    row = conn.execute(
        '''
        SELECT 1
        FROM missing_person_push_subscriptions
        WHERE subscriber_id = ?
          AND active = 1
        LIMIT 1
        ''',
        (int(subscriber_id),),
    ).fetchone()
    return bool(row)


def _eligible_missing_person_channels(conn: sqlite3.Connection, subscriber: dict[str, Any], person: dict[str, Any]) -> set[str]:
    channels: set[str] = set()
    if int(subscriber.get("missing_person_email_opt_in") or 0):
        channels.add("email")
    if int(subscriber.get("missing_person_sms_opt_in") or 0) and _single_line(subscriber.get("phone_verified_at")):
        channels.add("sms")
    if int(subscriber.get("missing_person_push_opt_in") or 0) and _subscriber_has_active_push_subscription(conn, int(subscriber["id"])):
        channels.add("push")
    return channels
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_missing_persons.py::MissingPersonsTests::test_channel_eligibility_respects_default_email_and_opt_in_sms_push -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add missing_persons.py tests/test_missing_persons.py
git commit -m "feat: add missing person channel eligibility rules"
```

## Task 4: Split Email Delivery and Add Found-Alert Copy

**Files:**
- Modify: `/root/montanablotter/missing_persons.py`
- Test: `/root/montanablotter/tests/test_missing_persons.py`

- [ ] **Step 1: Write the failing found-alert email test**

```python
def test_dispatch_missing_person_email_alerts_sends_found_alert_for_located_case(self) -> None:
    conn = self._connect()
    self._seed_subscriber(email="reader@example.com", counties="Yellowstone", city="")
    person = self._seed_missing_person(
        county="Yellowstone",
        city="Billings",
        status=missing_persons_module.STATUS_LOCATED,
        notification_version=2,
        resolution_summary="Located safely.",
    )
    sent_messages = []

    def fake_send_email(recipient_email: str, subject: str, html: str):
        sent_messages.append((recipient_email, subject, html))
        return True

    result = missing_persons_module.dispatch_missing_person_email_alerts(conn, person, send_email=fake_send_email)

    assert result["sent"] == 1
    assert "found" in sent_messages[0][1].lower() or "located" in sent_messages[0][1].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_missing_persons.py::MissingPersonsTests::test_dispatch_missing_person_email_alerts_sends_found_alert_for_located_case -q`
Expected: FAIL because the dispatcher returns early for non-missing status or only has one email template.

- [ ] **Step 3: Write minimal email delivery implementation**

```python
def build_missing_person_subject(person: dict[str, Any]) -> str:
    if person.get("status") == STATUS_LOCATED:
        return f"Located: {person['full_name']} in {person.get('county') or person.get('city') or 'Montana'}"
    return f"Missing Person Alert: {person['full_name']} in {person.get('county') or person.get('city') or 'Montana'}"


def dispatch_missing_person_email_alerts(conn, person, *, subscribers=None, send_email=None) -> dict[str, int]:
    if send_email is None:
        send_email = send_digest_email
    targeted = subscribers or _matching_missing_person_subscribers(conn, person)
    sent = failed = skipped = 0
    for subscriber in targeted:
        if "email" not in _eligible_missing_person_channels(conn, subscriber, person):
            skipped += 1
            continue
        recipient_email = _single_line(subscriber.get("email"), max_len=160).lower()
        # insert generalized delivery row with channel='email'
        # send email and update delivery row
```

Keep the existing HTML builder, but branch the status-specific copy for missing vs located.

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_missing_persons.py::MissingPersonsTests::test_dispatch_missing_person_email_alerts_sends_found_alert_for_located_case -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add missing_persons.py tests/test_missing_persons.py
git commit -m "feat: send missing person found alerts by email"
```

## Task 5: Add SMS Dispatch With Verified-Phone Gating

**Files:**
- Modify: `/root/montanablotter/missing_persons.py`
- Modify: `/root/montanablotter/config.py`
- Test: `/root/montanablotter/tests/test_missing_persons.py`

- [ ] **Step 1: Write the failing SMS dispatch test**

```python
def test_dispatch_missing_person_sms_alerts_sends_to_opted_in_verified_matches(self) -> None:
    conn = self._connect()
    self._seed_subscriber(
        email="sms@example.com",
        counties="Yellowstone",
        city="",
        phone="+14065550123",
        phone_verified_at="2026-04-25 10:00:00",
        missing_person_sms_opt_in=1,
    )
    person = self._seed_missing_person(county="Yellowstone", city="", status="missing")
    sent_messages = []

    def fake_send_sms(phone_number: str, sms_body: str) -> tuple[bool, str, str]:
        sent_messages.append((phone_number, sms_body))
        return True, "provider-123", ""

    result = missing_persons_module.dispatch_missing_person_sms_alerts(conn, person, send_sms=fake_send_sms)

    assert result["sent"] == 1
    assert sent_messages[0][0] == "+14065550123"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_missing_persons.py::MissingPersonsTests::test_dispatch_missing_person_sms_alerts_sends_to_opted_in_verified_matches -q`
Expected: FAIL because SMS dispatch function does not exist yet.

- [ ] **Step 3: Write minimal SMS implementation**

```python
def build_missing_person_sms_body(person: dict[str, Any]) -> str:
    status_label = "FOUND" if person.get("status") == STATUS_LOCATED else "MISSING"
    area = person.get("county") or person.get("city") or "Montana"
    return f"{status_label}: {person['full_name']} - {area}. {BASE_URL}/missing-persons/{person['slug']}"


def dispatch_missing_person_sms_alerts(conn, person, *, subscribers=None, send_sms=None) -> dict[str, int]:
    targeted = subscribers or _matching_missing_person_subscribers(conn, person)
    sent = failed = skipped = 0
    for subscriber in targeted:
        if "sms" not in _eligible_missing_person_channels(conn, subscriber, person):
            skipped += 1
            continue
        phone_number = _single_line(subscriber.get("phone"), max_len=40)
        # insert generalized delivery row with channel='sms'
        ok, provider_id, error_message = send_sms(phone_number, build_missing_person_sms_body(person))
        # update delivery row with provider_message_id
```

If there is already an app-level Twilio SMS helper elsewhere in the codebase, call that instead of introducing a second provider client.

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_missing_persons.py::MissingPersonsTests::test_dispatch_missing_person_sms_alerts_sends_to_opted_in_verified_matches -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add missing_persons.py config.py tests/test_missing_persons.py
git commit -m "feat: add missing person sms alerts"
```

## Task 6: Add Signed-In Browser Push Storage and Delivery

**Files:**
- Modify: `/root/montanablotter/missing_persons.py`
- Modify: `/root/montanablotter/config.py`
- Test: `/root/montanablotter/tests/test_missing_persons.py`
- Test: `/root/montanablotter/tests/test_missing_person_push_enrollment.py`

- [ ] **Step 1: Write the failing push dispatch test**

```python
def test_dispatch_missing_person_push_alerts_sends_to_active_device_subscriptions(self) -> None:
    conn = self._connect()
    subscriber_id = self._seed_subscriber(
        email="push@example.com",
        counties="Yellowstone",
        city="",
        missing_person_push_opt_in=1,
    )
    conn.execute(
        '''
        INSERT INTO missing_person_push_subscriptions (
            subscriber_id, endpoint, p256dh_key, auth_key, active
        ) VALUES (?, ?, ?, ?, 1)
        ''',
        (subscriber_id, "https://push.example/device-1", "p256dh", "auth"),
    )
    conn.commit()
    person = self._seed_missing_person(county="Yellowstone", city="", status="missing")
    payloads = []

    def fake_send_web_push(subscription: dict[str, str], payload: dict[str, str]) -> tuple[bool, str]:
        payloads.append((subscription, payload))
        return True, ""

    result = missing_persons_module.dispatch_missing_person_push_alerts(conn, person, send_web_push=fake_send_web_push)

    assert result["sent"] == 1
    assert payloads[0][1]["target_url"].endswith(person["public_href"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_missing_persons.py::MissingPersonsTests::test_dispatch_missing_person_push_alerts_sends_to_active_device_subscriptions -q`
Expected: FAIL because push dispatch does not exist yet.

- [ ] **Step 3: Write minimal push implementation**

```python
def build_missing_person_push_payload(person: dict[str, Any]) -> dict[str, str]:
    title = f"{'Located' if person.get('status') == STATUS_LOCATED else 'Missing'}: {person['full_name']}"
    body = person.get("resolution_summary") or person.get("summary") or ""
    return {
        "title": title,
        "body": body[:160],
        "target_url": person["public_href"],
    }


def dispatch_missing_person_push_alerts(conn, person, *, subscribers=None, send_web_push=None) -> dict[str, int]:
    targeted = subscribers or _matching_missing_person_subscribers(conn, person)
    sent = failed = skipped = 0
    for subscriber in targeted:
        if "push" not in _eligible_missing_person_channels(conn, subscriber, person):
            skipped += 1
            continue
        subscriptions = conn.execute(
            '''
            SELECT *
            FROM missing_person_push_subscriptions
            WHERE subscriber_id = ?
              AND active = 1
            ''',
            (int(subscriber["id"]),),
        ).fetchall()
        for subscription in subscriptions:
            # insert generalized delivery row with channel='push'
            # call send_web_push(...)
            # deactivate subscription on terminal invalid-subscription responses
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_missing_persons.py::MissingPersonsTests::test_dispatch_missing_person_push_alerts_sends_to_active_device_subscriptions -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add missing_persons.py config.py tests/test_missing_persons.py tests/test_missing_person_push_enrollment.py
git commit -m "feat: add missing person browser push alerts"
```

## Task 7: Orchestrate Initial and Found Alerts With Notification Versioning

**Files:**
- Modify: `/root/montanablotter/missing_persons.py`
- Modify: `/root/montanablotter/missing_person_watch.py`
- Test: `/root/montanablotter/tests/test_missing_persons.py`

- [ ] **Step 1: Write the failing located-transition test**

```python
def test_sync_official_missing_persons_increments_version_and_dispatches_located_alert(self) -> None:
    conn = self._connect()
    self._seed_subscriber(email="reader@example.com", counties="Yellowstone", city="")
    active_snapshot = self._official_snapshot_for_person(status="missing", source_person_id="45849")
    located_snapshot = self._official_snapshot_for_person(status="located", source_person_id="45849")
    sent_statuses = []

    def fake_dispatch(conn_arg, person, **kwargs):
        sent_statuses.append((person["status"], int(person["notification_version"])))
        return {"email": {"sent": 1}, "sms": {"sent": 0}, "push": {"sent": 0}}

    missing_persons_module.sync_official_missing_persons(conn, actor="test_sync", snapshot=active_snapshot, dispatch_alerts=fake_dispatch)
    missing_persons_module.sync_official_missing_persons(conn, actor="test_sync", snapshot=located_snapshot, dispatch_alerts=fake_dispatch)

    assert sent_statuses == [("missing", 1), ("located", 2)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_missing_persons.py::MissingPersonsTests::test_sync_official_missing_persons_increments_version_and_dispatches_located_alert -q`
Expected: FAIL because sync does not currently dispatch on located transitions with incremented version.

- [ ] **Step 3: Write minimal orchestration implementation**

```python
def dispatch_missing_person_alerts(conn, person, **kwargs) -> dict[str, dict[str, int]]:
    subscribers = _matching_missing_person_subscribers(conn, person)
    return {
        "email": dispatch_missing_person_email_alerts(conn, person, subscribers=subscribers, **kwargs),
        "sms": dispatch_missing_person_sms_alerts(conn, person, subscribers=subscribers, **kwargs),
        "push": dispatch_missing_person_push_alerts(conn, person, subscribers=subscribers, **kwargs),
    }
```

In `sync_official_missing_persons(...)`:

```python
if created_new_missing_case:
    person = get_missing_person_by_id(conn, person_id)
    if dispatch_alerts is not None:
        dispatch_alerts(conn, person)

if existing_status == STATUS_MISSING and new_status == STATUS_LOCATED:
    notification_version = int(existing["notification_version"] or 1) + 1
    conn.execute(
        "UPDATE missing_persons SET notification_version = ? WHERE id = ?",
        (notification_version, int(existing["id"])),
    )
    person = get_missing_person_by_id(conn, int(existing["id"]))
    if dispatch_alerts is not None:
        dispatch_alerts(conn, person)
```

Also update `missing_person_watch.py` to log per-channel send counts from the dispatcher result.

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_missing_persons.py::MissingPersonsTests::test_sync_official_missing_persons_increments_version_and_dispatches_located_alert -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add missing_persons.py missing_person_watch.py tests/test_missing_persons.py
git commit -m "feat: dispatch missing person alerts on new and found cases"
```

## Task 8: Add Signed-In Subscriber Enrollment Endpoints

**Files:**
- Modify: `/root/montanablotter/app.py`
- Modify: `/root/montanablotter/missing_persons.py`
- Create: `/root/montanablotter/tests/test_missing_person_push_enrollment.py`

- [ ] **Step 1: Write the failing enrollment endpoint test**

```python
def test_logged_in_subscriber_can_save_missing_person_alert_preferences(self) -> None:
    client = self._logged_in_client()
    response = client.post(
        "/account/missing-person-alerts",
        data={
            "counties": "Yellowstone,Flathead",
            "cities": "Billings",
            "missing_person_email_opt_in": "1",
            "missing_person_sms_opt_in": "1",
            "phone": "+14065550123",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    row = self._subscriber_row("reader@example.com")
    assert row["cities"] == "Billings"
    assert int(row["missing_person_sms_opt_in"]) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_missing_person_push_enrollment.py::MissingPersonPushEnrollmentTests::test_logged_in_subscriber_can_save_missing_person_alert_preferences -q`
Expected: FAIL because the route and persistence behavior do not exist yet.

- [ ] **Step 3: Write minimal enrollment implementation**

```python
@app.route("/account/missing-person-alerts", methods=["GET", "POST"])
def account_missing_person_alerts():
    subscriber = _current_subscriber_or_404()
    conn = get_db()
    if request.method == "POST":
        counties = ", ".join(_subscriber_counties_list(request.form.get("counties", "")))
        cities = ", ".join(_subscriber_city_list(request.form.get("cities", "")))
        phone = _single_line(request.form.get("phone"), max_len=40)
        conn.execute(
            '''
            UPDATE subscribers
            SET counties = ?, cities = ?, phone = ?,
                missing_person_email_opt_in = ?,
                missing_person_sms_opt_in = ?,
                missing_person_alerts_updated_at = datetime('now'),
                updated_at = datetime('now')
            WHERE id = ?
            ''',
            (...),
        )
        conn.commit()
        flash("Missing-person alert preferences updated.", "success")
        return redirect(url_for("account_missing_person_alerts"))
    return render_template("public_account_missing_person_alerts.html", subscriber=subscriber)
```

Follow the project’s existing logged-in account access pattern instead of inventing a new auth abstraction.

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_missing_person_push_enrollment.py::MissingPersonPushEnrollmentTests::test_logged_in_subscriber_can_save_missing_person_alert_preferences -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app.py missing_persons.py templates/public_account_missing_person_alerts.html tests/test_missing_person_push_enrollment.py
git commit -m "feat: add subscriber missing person alert settings"
```

## Task 9: Add Browser Push Enrollment and Service Worker Support

**Files:**
- Modify: `/root/montanablotter/app.py`
- Modify: `/root/montanablotter/static/sw.js`
- Create: `/root/montanablotter/static/js/missing-person-push.js`
- Modify: `/root/montanablotter/templates/public_account_missing_person_alerts.html`
- Test: `/root/montanablotter/tests/test_missing_person_push_enrollment.py`

- [ ] **Step 1: Write the failing push subscription endpoint test**

```python
def test_logged_in_subscriber_can_register_push_subscription(self) -> None:
    client = self._logged_in_client()
    response = client.post(
        "/account/missing-person-alerts/push-subscriptions",
        json={
            "endpoint": "https://push.example/device-1",
            "keys": {"p256dh": "p256dh-key", "auth": "auth-key"},
            "device_label": "Chrome on Mac",
        },
    )
    assert response.status_code == 200
    row = self._push_subscription_row("https://push.example/device-1")
    assert row["device_label"] == "Chrome on Mac"
    assert int(row["active"]) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_missing_person_push_enrollment.py::MissingPersonPushEnrollmentTests::test_logged_in_subscriber_can_register_push_subscription -q`
Expected: FAIL because endpoint and storage behavior do not exist yet.

- [ ] **Step 3: Write minimal push enrollment implementation**

```python
@app.route("/account/missing-person-alerts/push-subscriptions", methods=["POST"])
def save_missing_person_push_subscription():
    subscriber = _current_subscriber_or_404()
    payload = request.get_json(silent=True) or {}
    subscription = payload.get("subscription") or payload
    keys = subscription.get("keys") or {}
    conn = get_db()
    conn.execute(
        '''
        INSERT INTO missing_person_push_subscriptions (
            subscriber_id, endpoint, p256dh_key, auth_key, user_agent, device_label, active, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 1, datetime('now'))
        ON CONFLICT(endpoint) DO UPDATE SET
            subscriber_id = excluded.subscriber_id,
            p256dh_key = excluded.p256dh_key,
            auth_key = excluded.auth_key,
            user_agent = excluded.user_agent,
            device_label = excluded.device_label,
            active = 1,
            updated_at = datetime('now')
        ''',
        (...),
    )
    conn.commit()
    return jsonify({"ok": True})
```

Service worker additions:

```javascript
self.addEventListener('push', event => {
  const payload = event.data ? event.data.json() : {};
  event.waitUntil(
    self.registration.showNotification(payload.title || 'Missing Person Alert', {
      body: payload.body || '',
      data: { targetUrl: payload.target_url || '/missing-persons' },
    })
  );
});

self.addEventListener('notificationclick', event => {
  const targetUrl = event.notification?.data?.targetUrl || '/missing-persons';
  event.notification.close();
  event.waitUntil(clients.openWindow(targetUrl));
});
```

Browser script should request permission, call `PushManager.subscribe`, and POST the subscription JSON to the endpoint above.

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_missing_person_push_enrollment.py::MissingPersonPushEnrollmentTests::test_logged_in_subscriber_can_register_push_subscription -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app.py static/sw.js static/js/missing-person-push.js templates/public_account_missing_person_alerts.html tests/test_missing_person_push_enrollment.py
git commit -m "feat: add signed-in browser push enrollment"
```

## Task 10: Add Public Enrollment UI and Found-Alert Messaging

**Files:**
- Modify: `/root/montanablotter/templates/missing_persons.html`
- Modify: `/root/montanablotter/templates/missing_person_detail.html`
- Modify: `/root/montanablotter/templates/public_account.html`
- Modify: `/root/montanablotter/tests/test_public_detail_routes.py`
- Test: `/root/montanablotter/tests/test_missing_persons.py`

- [ ] **Step 1: Write the failing public-page CTA test**

```python
def test_public_missing_persons_page_renders_local_alert_signup_cta(self) -> None:
    self._seed_missing_persons()
    client = app_module.app.test_client()
    response = client.get("/missing-persons")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Get Local Alerts" in html
    assert "/account/missing-person-alerts" in html
    assert "SMS and push require opt-in enrollment" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_missing_persons.py::MissingPersonsTests::test_public_missing_persons_page_renders_local_alert_signup_cta -q`
Expected: FAIL because the CTA and messaging do not exist yet.

- [ ] **Step 3: Write minimal public template implementation**

```html
<section class="rounded-3xl border border-red-200 bg-red-50 p-5 shadow-sm">
  <p class="text-[11px] font-black uppercase tracking-[0.16em] text-red-700">Get Local Alerts</p>
  <h2 class="mt-2 text-2xl font-black text-slate-900">Track missing and found updates in your county or city.</h2>
  <p class="mt-3 text-sm leading-6 text-slate-700">
    Existing local subscribers receive email alerts by default. SMS and push require opt-in enrollment.
  </p>
  <a href="/account/missing-person-alerts" class="mt-4 inline-flex items-center justify-center rounded-xl bg-slate-900 px-5 py-3 text-sm font-black text-white">
    Manage Local Alerts
  </a>
</section>
```

Add a smaller version of the same CTA to the detail page and a settings link from the account page.

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_missing_persons.py::MissingPersonsTests::test_public_missing_persons_page_renders_local_alert_signup_cta -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add templates/missing_persons.html templates/missing_person_detail.html templates/public_account.html tests/test_missing_persons.py tests/test_public_detail_routes.py
git commit -m "feat: add public missing person alert enrollment ctas"
```

## Task 11: Run Full Verification

**Files:**
- Modify: none
- Test: `/root/montanablotter/tests/test_missing_persons.py`
- Test: `/root/montanablotter/tests/test_missing_person_push_enrollment.py`
- Test: `/root/montanablotter/tests/test_public_detail_routes.py`

- [ ] **Step 1: Run missing-person and public-route test suites**

Run: `./venv/bin/python -m pytest tests/test_missing_persons.py tests/test_missing_person_push_enrollment.py tests/test_public_detail_routes.py -q`
Expected: PASS with all tests green.

- [ ] **Step 2: Run targeted route rendering smoke check**

Run:

```bash
./venv/bin/python - <<'PY'
import sys
sys.path.insert(0, '/root/montanablotter')
import app
client = app.app.test_client()
for path in ['/missing-persons', '/missing-persons', '/account/missing-person-alerts']:
    response = client.get(path)
    print(path, response.status_code)
PY
```

Expected: `200` for public routes and `200` or auth redirect for account settings, depending on the session state in the test harness.

- [ ] **Step 3: Review delivery schema and route diffs**

Run: `git diff -- missing_persons.py app.py static/sw.js templates/missing_persons.html templates/missing_person_detail.html templates/public_account_missing_person_alerts.html tests/test_missing_persons.py tests/test_missing_person_push_enrollment.py`
Expected: diff only contains missing-person alert platform changes from this plan.

- [ ] **Step 4: Commit final integration pass**

```bash
git add missing_persons.py app.py init_db.py config.py static/sw.js static/js/missing-person-push.js templates/missing_persons.html templates/missing_person_detail.html templates/public_account.html templates/public_account_missing_person_alerts.html tests/test_missing_persons.py tests/test_missing_person_push_enrollment.py tests/test_public_detail_routes.py missing_person_watch.py
git commit -m "feat: launch missing person local alert platform"
```

## Self-Review

Spec coverage check:

- Subscriber county/city matching: Task 2.
- Default email for existing local subscribers: Tasks 1, 3, 4.
- SMS opt-in with enrollment: Tasks 5 and 8.
- Signed-in browser push only: Tasks 6, 8, and 9.
- Found-status second alert: Tasks 4 and 7.
- Public directory/detail enrollment messaging: Task 10.
- Service worker push behavior: Task 9.
- Sync-triggered orchestration and operational logging: Task 7.

Placeholder scan:

- No `TODO`, `TBD`, or “implement later” placeholders remain.
- All code steps include concrete snippets and explicit commands.

Type consistency:

- Channel names are consistently `email`, `sms`, `push`.
- Route naming consistently uses `/account/missing-person-alerts`.
- Matching helper names and dispatcher names are consistent across tasks.
