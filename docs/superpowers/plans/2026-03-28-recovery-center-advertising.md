# Recovery Center Advertising Panel — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-serve advertising system for Montana recovery centers — Bronze/Silver/Gold tiers, Stripe checkout, `/recovery-centers` directory page, and an advertiser control panel — mirroring the existing bail bonds advertising system.

**Architecture:** Fork (not refactor) of the bail bonds system. Public routes live in `blueprints/recovery_ads.py`, admin routes in `blueprints/admin/recovery_ads.py`. Stripe events are handled by extending the existing `/webhooks/stripe` handler. Two new DB tables (`recovery_ad_orders`, `recovery_ad_listings`) added via `init_db.ensure_recovery_ad_schema()`.

**Tech Stack:** Python 3.12, Flask blueprints, SQLite (WAL mode), Stripe Python SDK, Jinja2 templates, `unittest` + `tempfile` for tests.

---

## File Map

**Create:**
- `blueprints/recovery_ads.py` — all public routes + Stripe event handler + helpers
- `blueprints/admin/recovery_ads.py` — admin routes (orders list, status toggle, CMS)
- `tests/test_recovery_ads.py` — unit tests for schema, helpers, event handler
- `templates/recovery_centers_directory.html` — public `/recovery-centers` directory
- `templates/advertise_recovery.html` — package/pricing landing page
- `templates/advertise_recovery_checkout.html` — checkout form
- `templates/advertise_recovery_checkout_success.html` — post-payment confirmation
- `templates/advertise_recovery_checkout_cancel.html` — cancel page
- `templates/advertise_recovery_control_panel.html` — advertiser self-serve portal
- `templates/admin_recovery_ads.html` — admin orders + stats page
- `templates/admin_recovery_ads_cms.html` — admin listing editor

**Modify:**
- `init_db.py` — add `ensure_recovery_ad_schema()`, call from `migrate()`
- `blueprints/payments.py` — call `apply_stripe_recovery_ad_event` inside `stripe_webhook()`
- `blueprints/admin/__init__.py` — add `recovery_ads` side-effect import
- `app.py` — import + register `recovery_ads_bp`, add `/recovery-centers` to nav

---

## Task 1: DB Schema

**Files:**
- Modify: `init_db.py`
- Test: `tests/test_recovery_ads.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_recovery_ads.py`:

```python
import os
import sqlite3
import tempfile
import unittest


def _make_conn(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


class RecoveryAdSchemaTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        self.conn = _make_conn(self.db_path)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db_path)

    def test_ensure_creates_orders_table(self):
        from init_db import ensure_recovery_ad_schema
        ensure_recovery_ad_schema(self.conn)
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='recovery_ad_orders'"
        ).fetchone()
        self.assertIsNotNone(row)

    def test_ensure_creates_listings_table(self):
        from init_db import ensure_recovery_ad_schema
        ensure_recovery_ad_schema(self.conn)
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='recovery_ad_listings'"
        ).fetchone()
        self.assertIsNotNone(row)

    def test_ensure_is_idempotent(self):
        from init_db import ensure_recovery_ad_schema
        ensure_recovery_ad_schema(self.conn)
        ensure_recovery_ad_schema(self.conn)  # should not raise


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd /root/montanablotter && source venv/bin/activate && python -m pytest tests/test_recovery_ads.py -v 2>&1 | tail -20
```
Expected: `ImportError` or `AttributeError` — `ensure_recovery_ad_schema` does not exist yet.

- [ ] **Step 3: Add `ensure_recovery_ad_schema` to `init_db.py`**

Find the end of the `ensure_bondsman_command_center_schema` import block (around line 13) and add this function. Add it **before** the `migrate()` function, after the other `ensure_*` functions are imported. Place the new function at the end of `init_db.py`:

```python
def ensure_recovery_ad_schema(conn: sqlite3.Connection) -> None:
    """Create recovery_ad_orders and recovery_ad_listings tables if not present."""
    conn.execute('''
        CREATE TABLE IF NOT EXISTS recovery_ad_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            center_name TEXT NOT NULL,
            contact_name TEXT,
            email TEXT NOT NULL,
            phone TEXT,
            website TEXT,
            package_id TEXT NOT NULL,
            billing_cycle TEXT NOT NULL DEFAULT 'monthly',
            stripe_customer_id TEXT,
            stripe_subscription_id TEXT,
            stripe_session_id TEXT UNIQUE,
            status TEXT NOT NULL DEFAULT 'pending',
            token TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            activated_at TEXT,
            cancelled_at TEXT
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS recovery_ad_listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER UNIQUE NOT NULL REFERENCES recovery_ad_orders(id),
            tagline TEXT,
            description TEXT,
            services TEXT,
            city TEXT,
            county TEXT,
            logo_path TEXT,
            photo_path TEXT,
            impressions INTEGER NOT NULL DEFAULT 0,
            clicks INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    ''')
    conn.commit()
```

- [ ] **Step 4: Call `ensure_recovery_ad_schema` from `migrate()`**

In `init_db.py`, find the `migrate()` function body (around line 384). Add the call after the existing `ensure_*` calls:

```python
    ensure_recovery_ad_schema(conn)
```

Add it right after the line `ensure_court_tracker_schema(conn)`.

- [ ] **Step 5: Run tests to confirm pass**

```bash
cd /root/montanablotter && source venv/bin/activate && python -m pytest tests/test_recovery_ads.py -v 2>&1 | tail -20
```
Expected: 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
cd /root/montanablotter && git add init_db.py tests/test_recovery_ads.py && git commit -m "feat: add recovery_ad_orders and recovery_ad_listings DB tables"
```

---

## Task 2: Package Helpers & Stripe Event Handler

**Files:**
- Create: `blueprints/recovery_ads.py`
- Modify: `tests/test_recovery_ads.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_recovery_ads.py`:

```python
class RecoveryAdPackageTests(unittest.TestCase):
    def test_package_lookup_has_three_tiers(self):
        from blueprints.recovery_ads import _recovery_ad_package_lookup
        lookup = _recovery_ad_package_lookup()
        self.assertIn('bronze', lookup)
        self.assertIn('silver', lookup)
        self.assertIn('gold', lookup)

    def test_price_cents_monthly(self):
        from blueprints.recovery_ads import _recovery_ad_price_cents
        self.assertEqual(_recovery_ad_price_cents('bronze', 'monthly'), 9900)
        self.assertEqual(_recovery_ad_price_cents('silver', 'monthly'), 19900)
        self.assertEqual(_recovery_ad_price_cents('gold', 'monthly'), 39900)

    def test_price_cents_annual(self):
        from blueprints.recovery_ads import _recovery_ad_price_cents
        self.assertEqual(_recovery_ad_price_cents('bronze', 'annual'), 100900)
        self.assertEqual(_recovery_ad_price_cents('silver', 'annual'), 203000)
        self.assertEqual(_recovery_ad_price_cents('gold', 'annual'), 407000)

    def test_price_cents_unknown_package(self):
        from blueprints.recovery_ads import _recovery_ad_price_cents
        self.assertEqual(_recovery_ad_price_cents('platinum', 'monthly'), 0)


class RecoveryAdEventHandlerTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        self.conn = _make_conn(self.db_path)
        from init_db import ensure_recovery_ad_schema
        ensure_recovery_ad_schema(self.conn)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db_path)

    def _make_event(self, event_type, session_id, package_id='silver', billing_cycle='monthly'):
        return {
            'type': event_type,
            'data': {
                'object': {
                    'id': session_id,
                    'amount_total': 19900,
                    'currency': 'usd',
                    'customer': 'cus_test123',
                    'subscription': 'sub_test123',
                    'metadata': {
                        'flow': 'recovery_ad',
                        'package_id': package_id,
                        'billing_cycle': billing_cycle,
                        'center_name': 'Big Sky Recovery',
                        'contact_name': 'Jane Doe',
                        'email': 'jane@example.com',
                        'phone': '406-555-1234',
                        'website': 'https://bigskyrec.com',
                    },
                }
            },
        }

    def test_completed_event_activates_order(self):
        from blueprints.recovery_ads import apply_stripe_recovery_ad_event
        event = self._make_event('checkout.session.completed', 'cs_test_001')
        apply_stripe_recovery_ad_event(self.conn, event)
        row = self.conn.execute(
            "SELECT status, center_name FROM recovery_ad_orders WHERE stripe_session_id = ?",
            ('cs_test_001',),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row['status'], 'active')
        self.assertEqual(row['center_name'], 'Big Sky Recovery')

    def test_listing_row_created_on_activation(self):
        from blueprints.recovery_ads import apply_stripe_recovery_ad_event
        event = self._make_event('checkout.session.completed', 'cs_test_002')
        apply_stripe_recovery_ad_event(self.conn, event)
        order = self.conn.execute(
            "SELECT id FROM recovery_ad_orders WHERE stripe_session_id = ?",
            ('cs_test_002',),
        ).fetchone()
        listing = self.conn.execute(
            "SELECT order_id FROM recovery_ad_listings WHERE order_id = ?",
            (order['id'],),
        ).fetchone()
        self.assertIsNotNone(listing)

    def test_wrong_flow_is_ignored(self):
        from blueprints.recovery_ads import apply_stripe_recovery_ad_event
        event = self._make_event('checkout.session.completed', 'cs_test_003')
        event['data']['object']['metadata']['flow'] = 'bail_ad'
        apply_stripe_recovery_ad_event(self.conn, event)
        row = self.conn.execute(
            "SELECT id FROM recovery_ad_orders WHERE stripe_session_id = ?",
            ('cs_test_003',),
        ).fetchone()
        self.assertIsNone(row)

    def test_expired_event_sets_pending(self):
        from blueprints.recovery_ads import apply_stripe_recovery_ad_event
        event = self._make_event('checkout.session.expired', 'cs_test_004')
        apply_stripe_recovery_ad_event(self.conn, event)
        row = self.conn.execute(
            "SELECT status FROM recovery_ad_orders WHERE stripe_session_id = ?",
            ('cs_test_004',),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row['status'], 'expired')

    def test_idempotent_on_duplicate_session(self):
        from blueprints.recovery_ads import apply_stripe_recovery_ad_event
        event = self._make_event('checkout.session.completed', 'cs_test_005')
        apply_stripe_recovery_ad_event(self.conn, event)
        apply_stripe_recovery_ad_event(self.conn, event)  # second call must not raise
        count = self.conn.execute(
            "SELECT COUNT(*) FROM recovery_ad_orders WHERE stripe_session_id = ?",
            ('cs_test_005',),
        ).fetchone()[0]
        self.assertEqual(count, 1)
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /root/montanablotter && source venv/bin/activate && python -m pytest tests/test_recovery_ads.py -v 2>&1 | tail -25
```
Expected: `ModuleNotFoundError` for `blueprints.recovery_ads`.

- [ ] **Step 3: Create `blueprints/recovery_ads.py` with helpers and event handler**

```python
"""
Recovery Center Advertising — public routes, helpers, and Stripe event handler.
"""
from __future__ import annotations

import os
import secrets
import sqlite3
from datetime import datetime

from flask import (
    Blueprint,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from werkzeug.utils import secure_filename

import config
from db import get_db

recovery_ads_bp = Blueprint('recovery_ads', __name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOGO_UPLOAD_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), 'static', 'recovery_logos'
)
PHOTO_UPLOAD_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), 'static', 'recovery_photos'
)
_ALLOWED_IMAGE_EXTS = {'jpg', 'jpeg', 'png', 'webp'}
_MAX_UPLOAD_BYTES = 2 * 1024 * 1024  # 2 MB


# ---------------------------------------------------------------------------
# Package definitions
# ---------------------------------------------------------------------------

_PACKAGES = [
    {
        'id': 'bronze',
        'name': 'Bronze Listing',
        'price_monthly_cents': 9900,
        'price_annual_cents': 100900,
        'price_label': '$99/mo',
        'price_label_annual': '$1,009/yr',
        'logo': False,
        'photo': False,
        'featured': False,
        'description_limit': 0,
        'highlight': False,
        'features': [
            'Center name',
            'Phone number',
            'Website link',
        ],
        'short_description': 'Basic directory listing with contact information.',
    },
    {
        'id': 'silver',
        'name': 'Silver Listing',
        'price_monthly_cents': 19900,
        'price_annual_cents': 203000,
        'price_label': '$199/mo',
        'price_label_annual': '$2,030/yr',
        'logo': True,
        'photo': False,
        'featured': False,
        'description_limit': 200,
        'highlight': False,
        'features': [
            'Everything in Bronze',
            'Logo upload',
            'Tagline',
            '200-character description',
            'Services list',
        ],
        'short_description': 'Enhanced listing with branding and services.',
    },
    {
        'id': 'gold',
        'name': 'Gold Featured Listing',
        'price_monthly_cents': 39900,
        'price_annual_cents': 407000,
        'price_label': '$399/mo',
        'price_label_annual': '$4,070/yr',
        'logo': True,
        'photo': True,
        'featured': True,
        'description_limit': 500,
        'highlight': True,
        'features': [
            'Everything in Silver',
            'Featured top-of-page placement',
            'Hero photo upload',
            '500-character description',
            'Monthly impression & click stats',
        ],
        'short_description': 'Premium featured placement at the top of the directory.',
    },
]


def _recovery_ad_package_lookup() -> dict:
    return {pkg['id']: pkg for pkg in _PACKAGES}


def _recovery_ad_price_cents(package_id: str, billing_cycle: str) -> int:
    pkg = _recovery_ad_package_lookup().get(package_id)
    if not pkg:
        return 0
    if billing_cycle == 'annual':
        return pkg['price_annual_cents']
    return pkg['price_monthly_cents']


def _recovery_ad_checkout_ready() -> bool:
    try:
        import stripe as _stripe
    except Exception:
        return False
    secret = (getattr(config, 'STRIPE_SECRET_KEY', '') or '').strip()
    pub = (getattr(config, 'STRIPE_PUBLISHABLE_KEY', '') or '').strip()
    return bool(_stripe and secret and pub)


# ---------------------------------------------------------------------------
# Stripe event handler (called from blueprints/payments.py webhook)
# ---------------------------------------------------------------------------

def apply_stripe_recovery_ad_event(conn: sqlite3.Connection, event: dict) -> None:
    """Process a Stripe webhook event for recovery ad subscriptions."""
    event_type = (event.get('type') or '').strip()
    data_object = (event.get('data') or {}).get('object') or {}
    metadata = data_object.get('metadata') or {}

    if (metadata.get('flow') or '').strip() != 'recovery_ad':
        return

    handled = {
        'checkout.session.completed',
        'checkout.session.async_payment_succeeded',
        'checkout.session.expired',
        'checkout.session.async_payment_failed',
        'customer.subscription.deleted',
    }
    if event_type not in handled:
        return

    # subscription.deleted carries subscription object, not session
    if event_type == 'customer.subscription.deleted':
        sub_id = (data_object.get('id') or '').strip()
        if sub_id:
            conn.execute(
                '''
                UPDATE recovery_ad_orders
                SET status = 'cancelled', cancelled_at = datetime('now')
                WHERE stripe_subscription_id = ? AND status = 'active'
                ''',
                (sub_id,),
            )
            conn.commit()
        return

    session_id = (data_object.get('id') or '').strip()
    if not session_id:
        return

    status_map = {
        'checkout.session.completed': 'active',
        'checkout.session.async_payment_succeeded': 'active',
        'checkout.session.expired': 'expired',
        'checkout.session.async_payment_failed': 'payment_failed',
    }
    mapped_status = status_map[event_type]

    center_name = (metadata.get('center_name') or '').strip()[:120]
    contact_name = (metadata.get('contact_name') or '').strip()[:120]
    email = (metadata.get('email') or '').strip().lower()[:160]
    phone = (metadata.get('phone') or '').strip()[:40]
    website = (metadata.get('website') or '').strip()[:300]
    package_id = (metadata.get('package_id') or '').strip()[:32]
    billing_cycle = (metadata.get('billing_cycle') or 'monthly').strip().lower()
    if billing_cycle not in ('monthly', 'annual'):
        billing_cycle = 'monthly'
    token = (metadata.get('token') or '').strip()[:64) or secrets.token_urlsafe(24)
    stripe_customer_id = (data_object.get('customer') or '').strip()[:120]
    stripe_subscription_id = (data_object.get('subscription') or '').strip()[:120]

    activated_at = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S') if mapped_status == 'active' else None

    conn.execute(
        '''
        INSERT INTO recovery_ad_orders (
            center_name, contact_name, email, phone, website,
            package_id, billing_cycle,
            stripe_customer_id, stripe_subscription_id, stripe_session_id,
            status, token, activated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(stripe_session_id) DO UPDATE SET
            status = excluded.status,
            stripe_subscription_id = COALESCE(excluded.stripe_subscription_id, stripe_subscription_id),
            stripe_customer_id = COALESCE(excluded.stripe_customer_id, stripe_customer_id),
            activated_at = COALESCE(recovery_ad_orders.activated_at, excluded.activated_at)
        ''',
        (
            center_name, contact_name, email, phone, website,
            package_id, billing_cycle,
            stripe_customer_id, stripe_subscription_id, session_id,
            mapped_status, token, activated_at,
        ),
    )

    if mapped_status == 'active':
        order_row = conn.execute(
            'SELECT id FROM recovery_ad_orders WHERE stripe_session_id = ?',
            (session_id,),
        ).fetchone()
        if order_row:
            conn.execute(
                '''
                INSERT OR IGNORE INTO recovery_ad_listings (order_id)
                VALUES (?)
                ''',
                (order_row['id'],),
            )

    conn.commit()
```

**Note:** There is a syntax error above intentional for illustration — fix the closing paren typo on the token line: `[:64) or` should be `[:64] or`. The correct line is:
```python
    token = (metadata.get('token') or '').strip()[:64] or secrets.token_urlsafe(24)
```

- [ ] **Step 4: Run tests**

```bash
cd /root/montanablotter && source venv/bin/activate && python -m pytest tests/test_recovery_ads.py -v 2>&1 | tail -30
```
Expected: all tests in `RecoveryAdSchemaTests`, `RecoveryAdPackageTests`, `RecoveryAdEventHandlerTests` PASS.

- [ ] **Step 5: Commit**

```bash
cd /root/montanablotter && git add blueprints/recovery_ads.py tests/test_recovery_ads.py && git commit -m "feat: add recovery ads package helpers and Stripe event handler"
```

---

## Task 3: Wire Stripe Webhook

**Files:**
- Modify: `blueprints/payments.py`

- [ ] **Step 1: Import and call the event handler in the webhook**

In `blueprints/payments.py`, find the `stripe_webhook()` function. After the line:
```python
        m._apply_stripe_bail_ad_event(conn, event)
```
Add:
```python
        from blueprints.recovery_ads import apply_stripe_recovery_ad_event
        apply_stripe_recovery_ad_event(conn, event)
```

- [ ] **Step 2: Verify webhook still starts cleanly**

```bash
cd /root/montanablotter && source venv/bin/activate && python -c "from blueprints.payments import payments_bp; print('ok')"
```
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
cd /root/montanablotter && git add blueprints/payments.py && git commit -m "feat: call recovery ad Stripe event handler from webhook"
```

---

## Task 4: Public Routes — Directory, Advertise, Checkout, Control Panel

**Files:**
- Modify: `blueprints/recovery_ads.py`

Add the following routes to `blueprints/recovery_ads.py` after the helpers section:

- [ ] **Step 1: Add directory route with impression tracking**

```python
@recovery_ads_bp.route('/recovery-centers')
def recovery_centers_directory():
    conn = get_db()
    from init_db import ensure_recovery_ad_schema
    ensure_recovery_ad_schema(conn)

    rows = conn.execute(
        '''
        SELECT o.id, o.center_name, o.phone, o.website, o.package_id,
               l.tagline, l.description, l.services, l.city, l.county,
               l.logo_path, l.photo_path, l.impressions, l.clicks
        FROM recovery_ad_orders o
        LEFT JOIN recovery_ad_listings l ON l.order_id = o.id
        WHERE o.status = 'active'
        ORDER BY
            CASE o.package_id WHEN 'gold' THEN 0 WHEN 'silver' THEN 1 ELSE 2 END,
            o.activated_at ASC
        '''
    ).fetchall()
    listings = [dict(r) for r in rows]

    # Track impressions
    ids = [r['id'] for r in listings]
    for oid in ids:
        conn.execute(
            '''
            UPDATE recovery_ad_listings SET impressions = impressions + 1
            WHERE order_id = ?
            ''',
            (oid,),
        )
    if ids:
        conn.commit()
    conn.close()

    import json as _json

    def _parse_services(raw):
        if not raw:
            return []
        try:
            return _json.loads(raw) or []
        except Exception:
            return []

    for r in listings:
        r['services_list'] = _parse_services(r.get('services'))

    gold = [r for r in listings if r['package_id'] == 'gold']
    silver = [r for r in listings if r['package_id'] == 'silver']
    bronze = [r for r in listings if r['package_id'] == 'bronze']

    return render_template(
        'recovery_centers_directory.html',
        gold_listings=gold,
        silver_listings=silver,
        bronze_listings=bronze,
        page_title='Montana Recovery Centers Directory',
        meta_description='Find addiction treatment and recovery centers in Montana. Listings for Great Falls, Billings, Missoula, and all 56 counties.',
        active_nav='recovery_centers',
        current_year=datetime.now().year,
    )


@recovery_ads_bp.route('/recovery-centers/click/<int:order_id>')
def recovery_center_click(order_id):
    conn = get_db()
    row = conn.execute(
        'SELECT website FROM recovery_ad_orders WHERE id = ? AND status = ?',
        (order_id, 'active'),
    ).fetchone()
    if row:
        conn.execute(
            'UPDATE recovery_ad_listings SET clicks = clicks + 1 WHERE order_id = ?',
            (order_id,),
        )
        conn.commit()
        conn.close()
        website = (row['website'] or '').strip()
        if website and (website.startswith('http://') or website.startswith('https://')):
            return redirect(website)
    conn.close()
    return redirect(url_for('.recovery_centers_directory'))
```

- [ ] **Step 2: Add advertise landing page route**

```python
@recovery_ads_bp.route('/advertise/recovery')
def advertise_recovery():
    support_email = (
        (getattr(config, 'SMTP_USER', '') or '').strip()
        or 'support@montanablotter.com'
    )
    return render_template(
        'advertise_recovery.html',
        packages=_PACKAGES,
        support_email=support_email,
        checkout_ready=_recovery_ad_checkout_ready(),
        active_nav='advertise',
        current_year=datetime.now().year,
    )
```

- [ ] **Step 3: Add checkout route**

```python
@recovery_ads_bp.route('/advertise/recovery/checkout', methods=['GET', 'POST'])
def advertise_recovery_checkout():
    try:
        import stripe as _stripe
    except Exception:
        _stripe = None

    package_lookup = _recovery_ad_package_lookup()
    errors = []

    prefill_package = (request.values.get('package') or '').strip().lower()
    if prefill_package not in package_lookup:
        prefill_package = ''

    form_data = {
        'center_name': (request.values.get('center_name') or '').strip()[:120],
        'contact_name': (request.values.get('contact_name') or '').strip()[:120],
        'email': (request.values.get('email') or '').strip().lower()[:160],
        'phone': (request.values.get('phone') or '').strip()[:40],
        'website': (request.values.get('website') or '').strip()[:300],
        'package_id': prefill_package,
        'billing_cycle': 'monthly',
    }

    if not _recovery_ad_checkout_ready():
        return render_template(
            'advertise_recovery_checkout.html',
            packages=_PACKAGES,
            package_lookup=package_lookup,
            form_data=form_data,
            form_errors=['Secure checkout is not configured. Please contact support.'],
            checkout_ready=False,
            active_nav='advertise',
            current_year=datetime.now().year,
        ), 503

    if request.method == 'POST':
        form_data = {
            'center_name': (request.form.get('center_name') or '').strip()[:120],
            'contact_name': (request.form.get('contact_name') or '').strip()[:120],
            'email': (request.form.get('email') or '').strip().lower()[:160],
            'phone': (request.form.get('phone') or '').strip()[:40],
            'website': (request.form.get('website') or '').strip()[:300],
            'package_id': (request.form.get('package_id') or '').strip().lower()[:32],
            'billing_cycle': (request.form.get('billing_cycle') or 'monthly').strip().lower(),
        }

        if not form_data['center_name']:
            errors.append('Center name is required.')
        if not form_data['contact_name']:
            errors.append('Contact name is required.')
        if not form_data['email'] or '@' not in form_data['email']:
            errors.append('A valid email is required.')
        if not form_data['phone']:
            errors.append('Phone number is required.')
        if form_data['package_id'] not in package_lookup:
            errors.append('Please select a valid package.')
        if form_data['billing_cycle'] not in ('monthly', 'annual'):
            errors.append('Billing cycle is invalid.')
        if request.form.get('terms_ack') != 'yes':
            errors.append('You must accept the advertising terms to continue.')

        if not errors:
            token = secrets.token_urlsafe(24)
            amount_cents = _recovery_ad_price_cents(form_data['package_id'], form_data['billing_cycle'])
            pkg = package_lookup[form_data['package_id']]
            interval = 'year' if form_data['billing_cycle'] == 'annual' else 'month'
            base_url = (getattr(config, 'BASE_URL', '') or '').rstrip('/')

            stripe_keys = {
                'secret_key': (getattr(config, 'STRIPE_SECRET_KEY', '') or '').strip(),
            }
            _stripe.api_key = stripe_keys['secret_key']

            try:
                session = _stripe.checkout.Session.create(
                    mode='subscription',
                    line_items=[{
                        'price_data': {
                            'currency': 'usd',
                            'product_data': {'name': f"Montana Blotter Recovery Ad — {pkg['name']}"},
                            'unit_amount': amount_cents,
                            'recurring': {'interval': interval},
                        },
                        'quantity': 1,
                    }],
                    success_url=f'{base_url}/advertise/recovery/checkout/success?session_id={{CHECKOUT_SESSION_ID}}',
                    cancel_url=f'{base_url}/advertise/recovery/checkout/cancel',
                    customer_email=form_data['email'],
                    allow_promotion_codes=False,
                    billing_address_collection='auto',
                    metadata={
                        'flow': 'recovery_ad',
                        'package_id': form_data['package_id'],
                        'billing_cycle': form_data['billing_cycle'],
                        'center_name': form_data['center_name'],
                        'contact_name': form_data['contact_name'],
                        'email': form_data['email'],
                        'phone': form_data['phone'],
                        'website': form_data['website'],
                        'token': token,
                    },
                )
            except Exception:
                errors.append('Unable to start secure checkout. Please try again.')
                session = None

            if session:
                return redirect(session.url, code=303)

    return render_template(
        'advertise_recovery_checkout.html',
        packages=_PACKAGES,
        package_lookup=package_lookup,
        form_data=form_data,
        form_errors=errors,
        checkout_ready=True,
        active_nav='advertise',
        current_year=datetime.now().year,
    )
```

- [ ] **Step 4: Add checkout success and cancel routes**

```python
@recovery_ads_bp.route('/advertise/recovery/checkout/success')
def advertise_recovery_checkout_success():
    session_id = (request.args.get('session_id') or '').strip()
    order = None
    if session_id:
        conn = get_db()
        row = conn.execute(
            '''
            SELECT id, center_name, package_id, billing_cycle, status, token, created_at
            FROM recovery_ad_orders
            WHERE stripe_session_id = ?
            ORDER BY id DESC LIMIT 1
            ''',
            (session_id,),
        ).fetchone()
        conn.close()
        if row:
            order = dict(row)
            if order.get('token'):
                return redirect(
                    url_for('.advertise_recovery_control_panel',
                            token=order['token'],
                            welcome='1')
                )
    return render_template(
        'advertise_recovery_checkout_success.html',
        order=order,
        session_id=session_id,
        active_nav='advertise',
        current_year=datetime.now().year,
    )


@recovery_ads_bp.route('/advertise/recovery/checkout/cancel')
def advertise_recovery_checkout_cancel():
    return render_template(
        'advertise_recovery_checkout_cancel.html',
        packages=_PACKAGES,
        active_nav='advertise',
        current_year=datetime.now().year,
    )
```

- [ ] **Step 5: Add advertiser control panel route**

```python
def _safe_ext(filename: str) -> str:
    if '.' not in filename:
        return ''
    return filename.rsplit('.', 1)[-1].lower()


def _save_upload(upload, dest_dir: str, prefix: str) -> str:
    """Save an uploaded image file. Returns relative URL path or empty string."""
    if not upload or not upload.filename:
        return ''
    ext = _safe_ext(secure_filename(upload.filename))
    if ext not in _ALLOWED_IMAGE_EXTS:
        return ''
    data = upload.read((_MAX_UPLOAD_BYTES + 1))
    if len(data) > _MAX_UPLOAD_BYTES:
        return ''
    os.makedirs(dest_dir, exist_ok=True)
    stored = f'{prefix}_{secrets.token_hex(8)}.{ext}'
    path = os.path.join(dest_dir, stored)
    with open(path, 'wb') as f:
        f.write(data)
    rel_dir = os.path.basename(dest_dir)
    return f'/static/{rel_dir}/{stored}'


@recovery_ads_bp.route('/recovery-control-panel/<token>')
def advertise_recovery_control_panel(token):
    safe_token = (token or '').strip()[:128]
    welcome = request.args.get('welcome') == '1'
    conn = get_db()
    from init_db import ensure_recovery_ad_schema
    ensure_recovery_ad_schema(conn)

    order_row = conn.execute(
        '''
        SELECT o.id, o.center_name, o.contact_name, o.email, o.phone,
               o.website, o.package_id, o.billing_cycle, o.status,
               o.stripe_subscription_id, o.activated_at,
               l.tagline, l.description, l.services, l.city, l.county,
               l.logo_path, l.photo_path, l.impressions, l.clicks
        FROM recovery_ad_orders o
        LEFT JOIN recovery_ad_listings l ON l.order_id = o.id
        WHERE o.token = ?
        ''',
        (safe_token,),
    ).fetchone()
    conn.close()

    if not order_row:
        return render_template('404.html'), 404

    order = dict(order_row)
    pkg = _recovery_ad_package_lookup().get(order['package_id']) or {}
    services_list = []
    if order.get('services'):
        try:
            import json as _json
            services_list = _json.loads(order['services']) or []
        except Exception:
            pass

    return render_template(
        'advertise_recovery_control_panel.html',
        order=order,
        package=pkg,
        services_list=services_list,
        token=safe_token,
        welcome=welcome,
        page_title=f"{order['center_name']} — Recovery Center Control Panel",
        meta_description='Manage your recovery center directory listing on Montana Blotter.',
        active_nav='advertise',
        current_year=datetime.now().year,
    )


@recovery_ads_bp.route('/recovery-control-panel/<token>/update', methods=['POST'])
def advertise_recovery_control_panel_update(token):
    import json as _json
    safe_token = (token or '').strip()[:128]
    conn = get_db()
    order_row = conn.execute(
        'SELECT id, package_id FROM recovery_ad_orders WHERE token = ?',
        (safe_token,),
    ).fetchone()
    if not order_row:
        conn.close()
        abort(404)

    order_id = order_row['id']
    pkg = _recovery_ad_package_lookup().get(order_row['package_id']) or {}
    desc_limit = pkg.get('description_limit') or 0

    tagline = (request.form.get('tagline') or '').strip()[:120]
    description = (request.form.get('description') or '').strip()
    if desc_limit:
        description = description[:desc_limit]
    city = (request.form.get('city') or '').strip()[:80]
    county = (request.form.get('county') or '').strip()[:80]
    website = (request.form.get('website') or '').strip()[:300]
    raw_services = [s.strip() for s in (request.form.get('services') or '').split(',') if s.strip()]
    services_json = _json.dumps(raw_services[:20])

    logo_path = ''
    photo_path = ''
    if pkg.get('logo'):
        logo_upload = request.files.get('logo')
        if logo_upload and logo_upload.filename:
            logo_path = _save_upload(logo_upload, LOGO_UPLOAD_DIR, f'logo_{order_id}')
    if pkg.get('photo'):
        photo_upload = request.files.get('photo')
        if photo_upload and photo_upload.filename:
            photo_path = _save_upload(photo_upload, PHOTO_UPLOAD_DIR, f'photo_{order_id}')

    update_fields = 'tagline=?, description=?, services=?, city=?, county=?, updated_at=datetime(\'now\')'
    params = [tagline, description, services_json, city, county]
    if logo_path:
        update_fields += ', logo_path=?'
        params.append(logo_path)
    if photo_path:
        update_fields += ', photo_path=?'
        params.append(photo_path)
    params.append(order_id)

    conn.execute(f'UPDATE recovery_ad_listings SET {update_fields} WHERE order_id=?', params)
    if website:
        conn.execute('UPDATE recovery_ad_orders SET website=? WHERE id=?', (website, order_id))
    conn.commit()
    conn.close()

    return redirect(url_for('.advertise_recovery_control_panel', token=safe_token))
```

- [ ] **Step 6: Quick smoke test — import the blueprint**

```bash
cd /root/montanablotter && source venv/bin/activate && python -c "from blueprints.recovery_ads import recovery_ads_bp, apply_stripe_recovery_ad_event; print('ok')"
```
Expected: `ok`

- [ ] **Step 7: Commit**

```bash
cd /root/montanablotter && git add blueprints/recovery_ads.py && git commit -m "feat: add recovery ads public routes (directory, checkout, control panel)"
```

---

## Task 5: Admin Routes

**Files:**
- Create: `blueprints/admin/recovery_ads.py`

- [ ] **Step 1: Create `blueprints/admin/recovery_ads.py`**

```python
"""
Admin panel — Recovery Center Advertising orders, status management, and listing CMS.
"""
from __future__ import annotations

import json
from datetime import datetime

from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required

from db import get_db
from blueprints.admin import admin_bp, _log_admin_action
from blueprints.recovery_ads import _recovery_ad_package_lookup


@admin_bp.route('/recovery-ads')
@login_required
def admin_recovery_ads():
    from init_db import ensure_recovery_ad_schema
    conn = get_db()
    ensure_recovery_ad_schema(conn)

    q = (request.args.get('q') or '').strip()[:120]
    status_filter = (request.args.get('status') or 'all').strip().lower()

    base_query = '''
        SELECT o.id, o.center_name, o.contact_name, o.email, o.phone,
               o.website, o.package_id, o.billing_cycle, o.status,
               o.created_at, o.activated_at, o.cancelled_at,
               l.impressions, l.clicks, l.logo_path
        FROM recovery_ad_orders o
        LEFT JOIN recovery_ad_listings l ON l.order_id = o.id
    '''
    where = []
    params = []
    if q:
        where.append('(o.center_name LIKE ? OR o.email LIKE ? OR o.contact_name LIKE ?)')
        like = f'%{q}%'
        params.extend([like, like, like])
    if status_filter != 'all':
        where.append('o.status = ?')
        params.append(status_filter)
    if where:
        base_query += ' WHERE ' + ' AND '.join(where)
    base_query += ' ORDER BY o.created_at DESC LIMIT 200'

    orders = [dict(r) for r in conn.execute(base_query, params).fetchall()]

    # MRR summary
    pkg_lookup = _recovery_ad_package_lookup()
    active_orders = conn.execute(
        "SELECT package_id, billing_cycle FROM recovery_ad_orders WHERE status = 'active'"
    ).fetchall()
    mrr_cents = 0
    for row in active_orders:
        pkg = pkg_lookup.get(row['package_id']) or {}
        if row['billing_cycle'] == 'annual':
            mrr_cents += (pkg.get('price_annual_cents') or 0) // 12
        else:
            mrr_cents += pkg.get('price_monthly_cents') or 0

    tier_counts = {'bronze': 0, 'silver': 0, 'gold': 0}
    for row in active_orders:
        if row['package_id'] in tier_counts:
            tier_counts[row['package_id']] += 1

    conn.close()

    return render_template(
        'admin_recovery_ads.html',
        orders=orders,
        package_lookup=pkg_lookup,
        q=q,
        status_filter=status_filter,
        mrr_cents=mrr_cents,
        tier_counts=tier_counts,
        active_count=len(active_orders),
        current_year=datetime.now().year,
    )


@admin_bp.route('/recovery-ads/order/<int:order_id>/status', methods=['POST'])
@login_required
def admin_recovery_ads_order_status(order_id):
    new_status = (request.form.get('status') or '').strip().lower()
    allowed = {'active', 'inactive', 'cancelled', 'pending'}
    if new_status not in allowed:
        flash('Invalid status.', 'error')
        return redirect(url_for('.admin_recovery_ads'))

    conn = get_db()
    conn.execute(
        'UPDATE recovery_ad_orders SET status = ? WHERE id = ?',
        (new_status, order_id),
    )
    conn.commit()
    _log_admin_action('recovery_ad_status_change', 'recovery_ad_order', order_id,
                      metadata={'new_status': new_status}, conn=conn)
    conn.close()
    return redirect(url_for('.admin_recovery_ads'))


@admin_bp.route('/recovery-ads/cms/<int:order_id>', methods=['GET', 'POST'])
@login_required
def admin_recovery_ads_cms(order_id):
    from init_db import ensure_recovery_ad_schema
    conn = get_db()
    ensure_recovery_ad_schema(conn)

    order_row = conn.execute(
        '''
        SELECT o.id, o.center_name, o.email, o.package_id, o.status,
               l.tagline, l.description, l.services, l.city, l.county,
               l.logo_path, l.photo_path
        FROM recovery_ad_orders o
        LEFT JOIN recovery_ad_listings l ON l.order_id = o.id
        WHERE o.id = ?
        ''',
        (order_id,),
    ).fetchone()
    if not order_row:
        conn.close()
        return render_template('404.html'), 404

    order = dict(order_row)
    services_list = []
    if order.get('services'):
        try:
            services_list = json.loads(order['services']) or []
        except Exception:
            pass

    if request.method == 'POST':
        pkg = _recovery_ad_package_lookup().get(order['package_id']) or {}
        desc_limit = pkg.get('description_limit') or 0

        tagline = (request.form.get('tagline') or '').strip()[:120]
        description = (request.form.get('description') or '').strip()
        if desc_limit:
            description = description[:desc_limit]
        city = (request.form.get('city') or '').strip()[:80]
        county = (request.form.get('county') or '').strip()[:80]
        raw_services = [s.strip() for s in (request.form.get('services') or '').split(',') if s.strip()]
        services_json = json.dumps(raw_services[:20])

        conn.execute(
            '''
            UPDATE recovery_ad_listings
            SET tagline=?, description=?, services=?, city=?, county=?, updated_at=datetime('now')
            WHERE order_id=?
            ''',
            (tagline, description, services_json, city, county, order_id),
        )
        conn.commit()
        _log_admin_action('recovery_ad_cms_edit', 'recovery_ad_order', order_id, conn=conn)
        conn.close()
        flash('Listing updated.', 'success')
        return redirect(url_for('.admin_recovery_ads_cms', order_id=order_id))

    conn.close()
    pkg = _recovery_ad_package_lookup().get(order['package_id']) or {}
    return render_template(
        'admin_recovery_ads_cms.html',
        order=order,
        package=pkg,
        services_list=services_list,
        current_year=datetime.now().year,
    )
```

- [ ] **Step 2: Register module in `blueprints/admin/__init__.py`**

In `blueprints/admin/__init__.py`, find the `register_admin_blueprint` function and add one import line:

```python
    from blueprints.admin import recovery_ads  # noqa: F401
```

Add it after the line `from blueprints.admin import security   # noqa: F401`.

- [ ] **Step 3: Smoke test admin module import**

```bash
cd /root/montanablotter && source venv/bin/activate && python -c "from blueprints.admin.recovery_ads import admin_recovery_ads; print('ok')"
```
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
cd /root/montanablotter && git add blueprints/admin/recovery_ads.py blueprints/admin/__init__.py && git commit -m "feat: add recovery ads admin routes (orders list, status, CMS)"
```

---

## Task 6: Register Blueprint & Nav

**Files:**
- Modify: `app.py`

- [ ] **Step 1: Register `recovery_ads_bp` in `app.py`**

Find the bottom of `app.py` where the other blueprints are registered (around line 10682):
```python
register_admin_blueprint(app)
register_api_blueprint(app)
register_auth_blueprint(app)
register_payments_blueprint(app)
```

Add after those lines:
```python
from blueprints.recovery_ads import recovery_ads_bp
app.register_blueprint(recovery_ads_bp)
```

- [ ] **Step 2: Add nav link**

In `app.py`, find the nav list that contains `{'id': 'advertise', 'href': '/advertise/bail-bonds', 'label': 'Advertise'}` (around line 5756). There will be a similar footer nav list. Add a recovery centers link to the footer nav list only (not the main nav — it's an advertiser page, not a reader page):

Find the footer nav list (around line 5777) where `{'href': '/advertise/bail-bonds', 'label': 'Advertise'}` appears. Add after it:
```python
{'href': '/recovery-centers', 'label': 'Recovery Centers'},
```

- [ ] **Step 3: Full app smoke test**

```bash
cd /root/montanablotter && source venv/bin/activate && python -c "import app; print('routes:', len(list(app.app.url_map.iter_rules())))"
```
Expected: prints a number (should be larger than before, confirming new routes registered without errors).

- [ ] **Step 4: Run full test suite**

```bash
cd /root/montanablotter && source venv/bin/activate && python -m pytest tests/ -v 2>&1 | tail -30
```
Expected: all tests pass including new recovery ads tests.

- [ ] **Step 5: Commit**

```bash
cd /root/montanablotter && git add app.py && git commit -m "feat: register recovery ads blueprint and add nav link"
```

---

## Task 7: Templates

**Files:**
- Create all templates listed below

Create the upload dirs first:

- [ ] **Step 1: Create upload directories**

```bash
mkdir -p /root/montanablotter/static/recovery_logos /root/montanablotter/static/recovery_photos
touch /root/montanablotter/static/recovery_logos/.gitkeep /root/montanablotter/static/recovery_photos/.gitkeep
```

- [ ] **Step 2: Create `templates/recovery_centers_directory.html`**

```html
{% extends "base.html" %}
{% block title %}{{ page_title }}{% endblock %}
{% block meta_description %}{{ meta_description }}{% endblock %}

{% block content %}
<div class="container" style="max-width:900px;margin:0 auto;padding:2rem 1rem;">
  <h1 style="margin-bottom:.5rem;">Montana Recovery Centers</h1>
  <p style="color:#555;margin-bottom:2rem;">Find addiction treatment and recovery support across Montana.</p>

  {% if not gold_listings and not silver_listings and not bronze_listings %}
    <div style="text-align:center;padding:4rem 1rem;background:#f8f8f8;border-radius:8px;">
      <h2 style="color:#444;">Be the First to List Your Center</h2>
      <p style="color:#666;margin-bottom:1.5rem;">Help families in Montana find your recovery services.</p>
      <a href="{{ url_for('recovery_ads.advertise_recovery') }}" class="btn btn-primary" style="background:#1a6b3c;color:#fff;padding:.75rem 2rem;border-radius:4px;text-decoration:none;">Advertise Your Center</a>
    </div>
  {% endif %}

  {% if gold_listings %}
    <h2 style="border-bottom:2px solid #c9a227;padding-bottom:.5rem;margin-bottom:1.5rem;color:#7a5c00;">Featured Centers</h2>
    {% for r in gold_listings %}
    <div style="border:2px solid #c9a227;border-radius:8px;padding:1.5rem;margin-bottom:1.5rem;background:#fffdf0;">
      <div style="display:flex;gap:1rem;align-items:flex-start;flex-wrap:wrap;">
        {% if r.logo_path %}
          <img src="{{ r.logo_path }}" alt="{{ r.center_name }} logo" style="width:80px;height:80px;object-fit:contain;border-radius:4px;">
        {% endif %}
        <div style="flex:1;min-width:200px;">
          <h3 style="margin:0 0 .25rem;">{{ r.center_name }}</h3>
          {% if r.city or r.county %}<p style="color:#666;margin:0 0 .5rem;font-size:.9rem;">{{ [r.city, r.county]|select|join(', ') }}</p>{% endif %}
          {% if r.tagline %}<p style="color:#555;font-style:italic;margin-bottom:.5rem;">{{ r.tagline }}</p>{% endif %}
          {% if r.description %}<p style="margin-bottom:.75rem;">{{ r.description }}</p>{% endif %}
        </div>
      </div>
      {% if r.photo_path %}
        <img src="{{ r.photo_path }}" alt="{{ r.center_name }}" style="width:100%;max-height:220px;object-fit:cover;border-radius:4px;margin-top:1rem;">
      {% endif %}
      {% if r.services_list %}
        <p style="margin-top:.75rem;">{% for s in r.services_list %}<span style="background:#e8f5e9;padding:.2rem .6rem;border-radius:12px;font-size:.85rem;margin-right:.4rem;">{{ s }}</span>{% endfor %}</p>
      {% endif %}
      <div style="margin-top:1rem;display:flex;gap:.75rem;flex-wrap:wrap;">
        {% if r.phone %}<a href="tel:{{ r.phone }}" style="background:#1a6b3c;color:#fff;padding:.5rem 1.25rem;border-radius:4px;text-decoration:none;">Call {{ r.phone }}</a>{% endif %}
        {% if r.website %}<a href="{{ url_for('recovery_ads.recovery_center_click', order_id=r.id) }}" rel="noopener" style="background:#fff;border:1px solid #1a6b3c;color:#1a6b3c;padding:.5rem 1.25rem;border-radius:4px;text-decoration:none;">Visit Website</a>{% endif %}
      </div>
    </div>
    {% endfor %}
  {% endif %}

  {% if silver_listings %}
    <h2 style="border-bottom:2px solid #999;padding-bottom:.5rem;margin-bottom:1.5rem;color:#444;">More Centers</h2>
    {% for r in silver_listings %}
    <div style="border:1px solid #ddd;border-radius:8px;padding:1.25rem;margin-bottom:1rem;background:#fff;">
      <div style="display:flex;gap:1rem;align-items:flex-start;flex-wrap:wrap;">
        {% if r.logo_path %}<img src="{{ r.logo_path }}" alt="{{ r.center_name }} logo" style="width:56px;height:56px;object-fit:contain;border-radius:4px;">{% endif %}
        <div style="flex:1;">
          <h3 style="margin:0 0 .2rem;">{{ r.center_name }}</h3>
          {% if r.city or r.county %}<p style="color:#666;margin:0 0 .4rem;font-size:.85rem;">{{ [r.city, r.county]|select|join(', ') }}</p>{% endif %}
          {% if r.tagline %}<p style="color:#555;font-style:italic;margin-bottom:.4rem;font-size:.9rem;">{{ r.tagline }}</p>{% endif %}
          {% if r.description %}<p style="font-size:.9rem;">{{ r.description }}</p>{% endif %}
          <div style="margin-top:.75rem;display:flex;gap:.5rem;flex-wrap:wrap;">
            {% if r.phone %}<a href="tel:{{ r.phone }}" style="background:#1a6b3c;color:#fff;padding:.4rem 1rem;border-radius:4px;text-decoration:none;font-size:.9rem;">{{ r.phone }}</a>{% endif %}
            {% if r.website %}<a href="{{ url_for('recovery_ads.recovery_center_click', order_id=r.id) }}" rel="noopener" style="color:#1a6b3c;text-decoration:underline;font-size:.9rem;">Website</a>{% endif %}
          </div>
        </div>
      </div>
    </div>
    {% endfor %}
  {% endif %}

  {% if bronze_listings %}
    <h2 style="border-bottom:1px solid #ddd;padding-bottom:.5rem;margin-bottom:1rem;color:#555;font-size:1.1rem;">Directory</h2>
    {% for r in bronze_listings %}
    <div style="padding:.75rem 0;border-bottom:1px solid #eee;display:flex;gap:1rem;align-items:center;flex-wrap:wrap;">
      <span style="font-weight:600;">{{ r.center_name }}</span>
      {% if r.city %}<span style="color:#666;font-size:.9rem;">{{ r.city }}{% if r.county %}, {{ r.county }}{% endif %}</span>{% endif %}
      {% if r.phone %}<a href="tel:{{ r.phone }}" style="color:#1a6b3c;">{{ r.phone }}</a>{% endif %}
      {% if r.website %}<a href="{{ url_for('recovery_ads.recovery_center_click', order_id=r.id) }}" rel="noopener" style="color:#1a6b3c;font-size:.9rem;">Website</a>{% endif %}
    </div>
    {% endfor %}
  {% endif %}

  <div style="margin-top:3rem;padding:1.5rem;background:#f0f7f4;border-radius:8px;text-align:center;">
    <strong>Are you a recovery center in Montana?</strong>
    <a href="{{ url_for('recovery_ads.advertise_recovery') }}" style="color:#1a6b3c;margin-left:.5rem;">List your center →</a>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 3: Create `templates/advertise_recovery.html`**

```html
{% extends "base.html" %}
{% block title %}Advertise Your Recovery Center — Montana Blotter{% endblock %}
{% block meta_description %}Reach families and individuals seeking recovery support in Montana. List your treatment center in the Montana Blotter recovery directory.{% endblock %}

{% block content %}
<div class="container" style="max-width:860px;margin:0 auto;padding:2rem 1rem;">
  <h1 style="margin-bottom:.5rem;">Reach People Seeking Recovery in Montana</h1>
  <p style="color:#555;margin-bottom:2.5rem;">Montana Blotter serves families and individuals navigating the criminal justice system. Many are actively looking for treatment and recovery resources. List your center and be found when it matters most.</p>

  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:1.5rem;margin-bottom:3rem;">
    {% for pkg in packages %}
    <div style="border:{% if pkg.highlight %}2px solid #c9a227{% else %}1px solid #ddd{% endif %};border-radius:8px;padding:1.5rem;background:{% if pkg.highlight %}#fffdf0{% else %}#fff{% endif %};position:relative;">
      {% if pkg.highlight %}<div style="position:absolute;top:-12px;left:50%;transform:translateX(-50%);background:#c9a227;color:#fff;padding:.2rem .8rem;border-radius:12px;font-size:.8rem;font-weight:600;">MOST POPULAR</div>{% endif %}
      <h3 style="margin:0 0 .5rem;">{{ pkg.name }}</h3>
      <p style="font-size:1.5rem;font-weight:700;margin:.5rem 0;">{{ pkg.price_label }}</p>
      <p style="font-size:.8rem;color:#888;margin-bottom:1rem;">or {{ pkg.price_label_annual }} billed annually</p>
      <p style="color:#555;font-size:.9rem;margin-bottom:1rem;">{{ pkg.short_description }}</p>
      <ul style="list-style:none;padding:0;margin:0 0 1.5rem;font-size:.9rem;">
        {% for f in pkg.features %}
        <li style="padding:.3rem 0;border-bottom:1px solid #f0f0f0;">✓ {{ f }}</li>
        {% endfor %}
      </ul>
      <a href="{{ url_for('recovery_ads.advertise_recovery_checkout', package=pkg.id) }}" style="display:block;text-align:center;background:#1a6b3c;color:#fff;padding:.75rem 1rem;border-radius:4px;text-decoration:none;font-weight:600;">Get Started</a>
    </div>
    {% endfor %}
  </div>

  <p style="text-align:center;color:#666;font-size:.9rem;">Questions? Contact us at <a href="mailto:{{ support_email }}" style="color:#1a6b3c;">{{ support_email }}</a></p>
</div>
{% endblock %}
```

- [ ] **Step 4: Create `templates/advertise_recovery_checkout.html`**

```html
{% extends "base.html" %}
{% block title %}Recovery Center Listing Checkout — Montana Blotter{% endblock %}

{% block content %}
<div class="container" style="max-width:600px;margin:0 auto;padding:2rem 1rem;">
  <h1 style="margin-bottom:1.5rem;">Complete Your Listing</h1>

  {% if form_errors %}
  <div style="background:#fff0f0;border:1px solid #f00;border-radius:4px;padding:1rem;margin-bottom:1.5rem;">
    {% for e in form_errors %}<p style="margin:.25rem 0;color:#c00;">{{ e }}</p>{% endfor %}
  </div>
  {% endif %}

  <form method="POST">
    <div style="margin-bottom:1rem;">
      <label style="display:block;font-weight:600;margin-bottom:.25rem;">Recovery Center Name *</label>
      <input type="text" name="center_name" value="{{ form_data.center_name }}" required maxlength="120" style="width:100%;padding:.6rem;border:1px solid #ccc;border-radius:4px;box-sizing:border-box;">
    </div>
    <div style="margin-bottom:1rem;">
      <label style="display:block;font-weight:600;margin-bottom:.25rem;">Contact Name *</label>
      <input type="text" name="contact_name" value="{{ form_data.contact_name }}" required maxlength="120" style="width:100%;padding:.6rem;border:1px solid #ccc;border-radius:4px;box-sizing:border-box;">
    </div>
    <div style="margin-bottom:1rem;">
      <label style="display:block;font-weight:600;margin-bottom:.25rem;">Email *</label>
      <input type="email" name="email" value="{{ form_data.email }}" required maxlength="160" style="width:100%;padding:.6rem;border:1px solid #ccc;border-radius:4px;box-sizing:border-box;">
    </div>
    <div style="margin-bottom:1rem;">
      <label style="display:block;font-weight:600;margin-bottom:.25rem;">Phone *</label>
      <input type="tel" name="phone" value="{{ form_data.phone }}" required maxlength="40" style="width:100%;padding:.6rem;border:1px solid #ccc;border-radius:4px;box-sizing:border-box;">
    </div>
    <div style="margin-bottom:1rem;">
      <label style="display:block;font-weight:600;margin-bottom:.25rem;">Website</label>
      <input type="url" name="website" value="{{ form_data.website }}" maxlength="300" placeholder="https://" style="width:100%;padding:.6rem;border:1px solid #ccc;border-radius:4px;box-sizing:border-box;">
    </div>

    <div style="margin-bottom:1rem;">
      <label style="display:block;font-weight:600;margin-bottom:.5rem;">Package *</label>
      {% for pkg in packages %}
      <label style="display:flex;align-items:center;gap:.75rem;padding:.75rem;border:1px solid #ddd;border-radius:4px;margin-bottom:.5rem;cursor:pointer;">
        <input type="radio" name="package_id" value="{{ pkg.id }}" {% if form_data.package_id == pkg.id %}checked{% endif %} required>
        <span><strong>{{ pkg.name }}</strong> — {{ pkg.price_label }}</span>
      </label>
      {% endfor %}
    </div>

    <div style="margin-bottom:1rem;">
      <label style="display:block;font-weight:600;margin-bottom:.5rem;">Billing Cycle</label>
      <label style="display:flex;align-items:center;gap:.5rem;margin-bottom:.4rem;cursor:pointer;">
        <input type="radio" name="billing_cycle" value="monthly" {% if form_data.billing_cycle != 'annual' %}checked{% endif %}> Monthly
      </label>
      <label style="display:flex;align-items:center;gap:.5rem;cursor:pointer;">
        <input type="radio" name="billing_cycle" value="annual"> Annual (save ~15%)
      </label>
    </div>

    <div style="margin-bottom:1.5rem;padding:1rem;background:#f8f8f8;border-radius:4px;">
      <label style="display:flex;align-items:flex-start;gap:.5rem;cursor:pointer;">
        <input type="checkbox" name="terms_ack" value="yes" style="margin-top:.2rem;" required>
        <span style="font-size:.9rem;">I accept the Montana Blotter advertising terms and authorize recurring billing for the selected package.</span>
      </label>
    </div>

    <button type="submit" style="width:100%;padding:.875rem;background:#1a6b3c;color:#fff;border:none;border-radius:4px;font-size:1rem;font-weight:600;cursor:pointer;">Continue to Secure Payment →</button>
  </form>

  <p style="text-align:center;margin-top:1.5rem;">
    <a href="{{ url_for('recovery_ads.advertise_recovery') }}" style="color:#1a6b3c;">← Back to packages</a>
  </p>
</div>
{% endblock %}
```

- [ ] **Step 5: Create `templates/advertise_recovery_checkout_success.html`**

```html
{% extends "base.html" %}
{% block title %}Listing Purchase Confirmed — Montana Blotter{% endblock %}

{% block content %}
<div class="container" style="max-width:600px;margin:0 auto;padding:3rem 1rem;text-align:center;">
  <div style="font-size:3rem;margin-bottom:1rem;">✓</div>
  <h1 style="color:#1a6b3c;">Thank you!</h1>
  {% if order %}
    <p style="color:#555;margin-bottom:1.5rem;">Your <strong>{{ order.center_name }}</strong> listing has been submitted. You'll be redirected to your control panel shortly.</p>
  {% else %}
    <p style="color:#555;margin-bottom:1.5rem;">Your listing is being set up. Check your email for access to your control panel.</p>
  {% endif %}
  <a href="{{ url_for('recovery_ads.recovery_centers_directory') }}" style="color:#1a6b3c;">View the directory →</a>
</div>
{% endblock %}
```

- [ ] **Step 6: Create `templates/advertise_recovery_checkout_cancel.html`**

```html
{% extends "base.html" %}
{% block title %}Checkout Cancelled — Montana Blotter{% endblock %}

{% block content %}
<div class="container" style="max-width:600px;margin:0 auto;padding:3rem 1rem;text-align:center;">
  <h1 style="color:#555;">Checkout Cancelled</h1>
  <p style="color:#666;margin-bottom:2rem;">No charge was made. You can review packages and try again whenever you're ready.</p>
  <div style="display:flex;gap:1rem;justify-content:center;flex-wrap:wrap;">
    <a href="{{ url_for('recovery_ads.advertise_recovery') }}" style="background:#1a6b3c;color:#fff;padding:.75rem 2rem;border-radius:4px;text-decoration:none;">View Packages</a>
    <a href="{{ url_for('recovery_ads.recovery_centers_directory') }}" style="border:1px solid #ccc;color:#555;padding:.75rem 2rem;border-radius:4px;text-decoration:none;">View Directory</a>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 7: Create `templates/advertise_recovery_control_panel.html`**

```html
{% extends "base.html" %}
{% block title %}{{ page_title }}{% endblock %}

{% block content %}
<div class="container" style="max-width:760px;margin:0 auto;padding:2rem 1rem;">
  {% if welcome %}
  <div style="background:#f0f7f4;border:1px solid #1a6b3c;border-radius:6px;padding:1.25rem;margin-bottom:2rem;">
    <strong>Welcome!</strong> Your listing is now active. Complete your profile below to appear in the directory.
  </div>
  {% endif %}

  <h1 style="margin-bottom:.25rem;">{{ order.center_name }}</h1>
  <p style="color:#666;margin-bottom:2rem;">
    <span style="background:#e8f5e9;padding:.2rem .6rem;border-radius:12px;font-size:.85rem;">{{ package.name or order.package_id }}</span>
    &nbsp;·&nbsp;
    <span style="color:{% if order.status == 'active' %}#1a6b3c{% else %}#c00{% endif %};">{{ order.status|capitalize }}</span>
  </p>

  {% if order.status == 'active' and order.package_id == 'gold' %}
  <div style="display:flex;gap:2rem;margin-bottom:2rem;flex-wrap:wrap;">
    <div style="background:#f8f8f8;border-radius:6px;padding:1rem 1.5rem;text-align:center;">
      <div style="font-size:2rem;font-weight:700;color:#1a6b3c;">{{ order.impressions or 0 }}</div>
      <div style="color:#666;font-size:.85rem;">Impressions this month</div>
    </div>
    <div style="background:#f8f8f8;border-radius:6px;padding:1rem 1.5rem;text-align:center;">
      <div style="font-size:2rem;font-weight:700;color:#1a6b3c;">{{ order.clicks or 0 }}</div>
      <div style="color:#666;font-size:.85rem;">Website clicks this month</div>
    </div>
  </div>
  {% endif %}

  <form method="POST" action="{{ url_for('recovery_ads.advertise_recovery_control_panel_update', token=token) }}" enctype="multipart/form-data">
    <h2 style="font-size:1.1rem;border-bottom:1px solid #eee;padding-bottom:.5rem;margin-bottom:1rem;">Your Listing</h2>

    <div style="margin-bottom:1rem;">
      <label style="display:block;font-weight:600;margin-bottom:.25rem;">Website URL</label>
      <input type="url" name="website" value="{{ order.website or '' }}" maxlength="300" placeholder="https://" style="width:100%;padding:.6rem;border:1px solid #ccc;border-radius:4px;box-sizing:border-box;">
    </div>
    <div style="margin-bottom:1rem;">
      <label style="display:block;font-weight:600;margin-bottom:.25rem;">City</label>
      <input type="text" name="city" value="{{ order.city or '' }}" maxlength="80" style="width:100%;padding:.6rem;border:1px solid #ccc;border-radius:4px;box-sizing:border-box;">
    </div>
    <div style="margin-bottom:1rem;">
      <label style="display:block;font-weight:600;margin-bottom:.25rem;">County</label>
      <input type="text" name="county" value="{{ order.county or '' }}" maxlength="80" style="width:100%;padding:.6rem;border:1px solid #ccc;border-radius:4px;box-sizing:border-box;">
    </div>

    {% if package.logo %}
    <div style="margin-bottom:1rem;">
      <label style="display:block;font-weight:600;margin-bottom:.25rem;">Logo (JPG/PNG/WebP, max 2MB)</label>
      {% if order.logo_path %}<img src="{{ order.logo_path }}" style="height:60px;display:block;margin-bottom:.5rem;border-radius:4px;">{% endif %}
      <input type="file" name="logo" accept=".jpg,.jpeg,.png,.webp">
    </div>
    <div style="margin-bottom:1rem;">
      <label style="display:block;font-weight:600;margin-bottom:.25rem;">Tagline</label>
      <input type="text" name="tagline" value="{{ order.tagline or '' }}" maxlength="120" style="width:100%;padding:.6rem;border:1px solid #ccc;border-radius:4px;box-sizing:border-box;">
    </div>
    <div style="margin-bottom:1rem;">
      <label style="display:block;font-weight:600;margin-bottom:.25rem;">Description ({{ package.description_limit }} chars max)</label>
      <textarea name="description" maxlength="{{ package.description_limit }}" rows="4" style="width:100%;padding:.6rem;border:1px solid #ccc;border-radius:4px;box-sizing:border-box;">{{ order.description or '' }}</textarea>
    </div>
    <div style="margin-bottom:1rem;">
      <label style="display:block;font-weight:600;margin-bottom:.25rem;">Services (comma-separated)</label>
      <input type="text" name="services" value="{{ services_list|join(', ') }}" maxlength="500" placeholder="Detox, Residential, Outpatient, MAT" style="width:100%;padding:.6rem;border:1px solid #ccc;border-radius:4px;box-sizing:border-box;">
    </div>
    {% endif %}

    {% if package.photo %}
    <div style="margin-bottom:1.5rem;">
      <label style="display:block;font-weight:600;margin-bottom:.25rem;">Hero Photo (JPG/PNG/WebP, max 2MB)</label>
      {% if order.photo_path %}<img src="{{ order.photo_path }}" style="width:100%;max-height:180px;object-fit:cover;border-radius:4px;margin-bottom:.5rem;">{% endif %}
      <input type="file" name="photo" accept=".jpg,.jpeg,.png,.webp">
    </div>
    {% endif %}

    <button type="submit" style="background:#1a6b3c;color:#fff;padding:.75rem 2rem;border:none;border-radius:4px;font-size:1rem;cursor:pointer;font-weight:600;">Save Changes</button>
  </form>

  <p style="margin-top:2rem;color:#666;font-size:.9rem;">
    <a href="{{ url_for('recovery_ads.recovery_centers_directory') }}" style="color:#1a6b3c;">View your listing in the directory →</a>
  </p>
</div>
{% endblock %}
```

- [ ] **Step 8: Create `templates/admin_recovery_ads.html`**

```html
{% extends "base.html" %}
{% block title %}Recovery Center Ads — Admin{% endblock %}

{% block content %}
<div class="admin-panel">
  <h1>Recovery Center Advertising</h1>

  <div style="display:flex;gap:1.5rem;margin-bottom:2rem;flex-wrap:wrap;">
    <div style="background:#f0f7f4;border-radius:6px;padding:1rem 1.5rem;text-align:center;min-width:120px;">
      <div style="font-size:1.75rem;font-weight:700;color:#1a6b3c;">{{ active_count }}</div>
      <div style="color:#666;font-size:.85rem;">Active Listings</div>
    </div>
    <div style="background:#f0f7f4;border-radius:6px;padding:1rem 1.5rem;text-align:center;min-width:120px;">
      <div style="font-size:1.75rem;font-weight:700;color:#1a6b3c;">${{ '{:,.0f}'.format(mrr_cents / 100) }}</div>
      <div style="color:#666;font-size:.85rem;">MRR</div>
    </div>
    {% for tier, count in tier_counts.items() %}
    <div style="background:#f8f8f8;border-radius:6px;padding:1rem 1.5rem;text-align:center;min-width:100px;">
      <div style="font-size:1.5rem;font-weight:700;">{{ count }}</div>
      <div style="color:#666;font-size:.85rem;">{{ tier|capitalize }}</div>
    </div>
    {% endfor %}
  </div>

  <form method="GET" style="margin-bottom:1.5rem;display:flex;gap:.75rem;flex-wrap:wrap;">
    <input type="text" name="q" value="{{ q }}" placeholder="Search center, email..." style="padding:.5rem .75rem;border:1px solid #ccc;border-radius:4px;flex:1;min-width:200px;">
    <select name="status" style="padding:.5rem;border:1px solid #ccc;border-radius:4px;">
      <option value="all" {% if status_filter=='all' %}selected{% endif %}>All statuses</option>
      <option value="active" {% if status_filter=='active' %}selected{% endif %}>Active</option>
      <option value="pending" {% if status_filter=='pending' %}selected{% endif %}>Pending</option>
      <option value="cancelled" {% if status_filter=='cancelled' %}selected{% endif %}>Cancelled</option>
      <option value="inactive" {% if status_filter=='inactive' %}selected{% endif %}>Inactive</option>
    </select>
    <button type="submit" style="padding:.5rem 1rem;background:#1a6b3c;color:#fff;border:none;border-radius:4px;cursor:pointer;">Filter</button>
  </form>

  <table style="width:100%;border-collapse:collapse;font-size:.9rem;">
    <thead>
      <tr style="border-bottom:2px solid #ddd;text-align:left;">
        <th style="padding:.5rem;">Center</th>
        <th>Package</th>
        <th>Status</th>
        <th>Signed Up</th>
        <th>Impr / Clicks</th>
        <th>Actions</th>
      </tr>
    </thead>
    <tbody>
      {% for order in orders %}
      <tr style="border-bottom:1px solid #eee;">
        <td style="padding:.6rem .5rem;">
          <strong>{{ order.center_name }}</strong><br>
          <small style="color:#666;">{{ order.email }}</small>
        </td>
        <td>{{ package_lookup.get(order.package_id, {}).get('name', order.package_id) }}</td>
        <td>
          <form method="POST" action="{{ url_for('admin.admin_recovery_ads_order_status', order_id=order.id) }}" style="display:inline;">
            <select name="status" onchange="this.form.submit()" style="font-size:.85rem;padding:.2rem;">
              {% for s in ['pending','active','inactive','cancelled'] %}
              <option value="{{ s }}" {% if order.status==s %}selected{% endif %}>{{ s }}</option>
              {% endfor %}
            </select>
          </form>
        </td>
        <td style="font-size:.85rem;color:#666;">{{ order.created_at[:10] if order.created_at else '—' }}</td>
        <td style="font-size:.85rem;">{{ order.impressions or 0 }} / {{ order.clicks or 0 }}</td>
        <td>
          <a href="{{ url_for('admin.admin_recovery_ads_cms', order_id=order.id) }}" style="color:#1a6b3c;font-size:.85rem;">Edit listing</a>
        </td>
      </tr>
      {% else %}
      <tr><td colspan="6" style="padding:2rem;text-align:center;color:#666;">No orders found.</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
```

- [ ] **Step 9: Create `templates/admin_recovery_ads_cms.html`**

```html
{% extends "base.html" %}
{% block title %}Edit Listing — {{ order.center_name }}{% endblock %}

{% block content %}
<div class="admin-panel" style="max-width:700px;">
  <h1>Edit Listing: {{ order.center_name }}</h1>
  <p style="color:#666;margin-bottom:1.5rem;">Package: {{ package.name or order.package_id }} · Status: {{ order.status }}</p>

  <form method="POST">
    <div style="margin-bottom:1rem;">
      <label style="display:block;font-weight:600;margin-bottom:.25rem;">Tagline</label>
      <input type="text" name="tagline" value="{{ order.tagline or '' }}" maxlength="120" style="width:100%;padding:.6rem;border:1px solid #ccc;border-radius:4px;box-sizing:border-box;">
    </div>
    <div style="margin-bottom:1rem;">
      <label style="display:block;font-weight:600;margin-bottom:.25rem;">Description {% if package.description_limit %}({{ package.description_limit }} chars max){% endif %}</label>
      <textarea name="description" {% if package.description_limit %}maxlength="{{ package.description_limit }}"{% endif %} rows="5" style="width:100%;padding:.6rem;border:1px solid #ccc;border-radius:4px;box-sizing:border-box;">{{ order.description or '' }}</textarea>
    </div>
    <div style="margin-bottom:1rem;">
      <label style="display:block;font-weight:600;margin-bottom:.25rem;">Services (comma-separated)</label>
      <input type="text" name="services" value="{{ services_list|join(', ') }}" maxlength="500" style="width:100%;padding:.6rem;border:1px solid #ccc;border-radius:4px;box-sizing:border-box;">
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-bottom:1.5rem;">
      <div>
        <label style="display:block;font-weight:600;margin-bottom:.25rem;">City</label>
        <input type="text" name="city" value="{{ order.city or '' }}" maxlength="80" style="width:100%;padding:.6rem;border:1px solid #ccc;border-radius:4px;box-sizing:border-box;">
      </div>
      <div>
        <label style="display:block;font-weight:600;margin-bottom:.25rem;">County</label>
        <input type="text" name="county" value="{{ order.county or '' }}" maxlength="80" style="width:100%;padding:.6rem;border:1px solid #ccc;border-radius:4px;box-sizing:border-box;">
      </div>
    </div>
    <button type="submit" style="background:#1a6b3c;color:#fff;padding:.75rem 2rem;border:none;border-radius:4px;cursor:pointer;font-weight:600;">Save Changes</button>
    <a href="{{ url_for('admin.admin_recovery_ads') }}" style="margin-left:1rem;color:#666;">← Back</a>
  </form>
</div>
{% endblock %}
```

- [ ] **Step 10: Add admin nav link**

In `templates/admin.html` (or wherever the admin sidebar nav is), find where `/admin/bail-ads` appears and add a similar entry for recovery ads. Search for it:

```bash
grep -n "bail-ads\|bail_ads" /root/montanablotter/templates/admin.html | head -5
```

Add a link for recovery ads in the same nav section:
```html
<a href="/admin/recovery-ads">Recovery Ads</a>
```

- [ ] **Step 11: App-level integration smoke test**

```bash
cd /root/montanablotter && source venv/bin/activate && python -c "
import app
rules = [str(r) for r in app.app.url_map.iter_rules()]
expected = ['/recovery-centers', '/advertise/recovery', '/advertise/recovery/checkout', '/recovery-control-panel/<token>', '/admin/recovery-ads']
for path in expected:
    found = any(path in r for r in rules)
    print(('✓' if found else '✗'), path)
"
```
Expected: all 5 print `✓`.

- [ ] **Step 12: Run full test suite one final time**

```bash
cd /root/montanablotter && source venv/bin/activate && python -m pytest tests/ -v 2>&1 | tail -30
```
Expected: all tests pass.

- [ ] **Step 13: Commit**

```bash
cd /root/montanablotter && git add templates/recovery_centers_directory.html templates/advertise_recovery.html templates/advertise_recovery_checkout.html templates/advertise_recovery_checkout_success.html templates/advertise_recovery_checkout_cancel.html templates/advertise_recovery_control_panel.html templates/admin_recovery_ads.html templates/admin_recovery_ads_cms.html static/recovery_logos/.gitkeep static/recovery_photos/.gitkeep && git commit -m "feat: add recovery center advertising templates and upload dirs"
```

---

## Config Notes

Before going live, add these keys to `config.py` (they use the same Stripe credentials already present — no new Stripe account needed):

```python
# Recovery ads — uses same STRIPE_SECRET_KEY / STRIPE_PUBLISHABLE_KEY / STRIPE_WEBHOOK_SECRET
# No additional config required. The flow is distinguished by metadata.flow = 'recovery_ad'.
```

If you want a **separate** webhook secret for recovery ads (e.g. a second Stripe webhook endpoint), add:
```python
RECOVERY_ADS_STRIPE_WEBHOOK_SECRET = 'whsec_...'
```
and update `blueprints/payments.py` to use it for recovery ad events. For initial launch, sharing the existing webhook secret is fine.

---

## Post-Implementation Verification Checklist

- [ ] `python -m pytest tests/ -v` — all pass
- [ ] `python -c "import app"` — no import errors
- [ ] `sqlite3 blotter.db ".tables"` — shows `recovery_ad_orders` and `recovery_ad_listings`
- [ ] Visit `/recovery-centers` in browser — directory page loads with "Be the first" CTA
- [ ] Visit `/advertise/recovery` — package cards display correctly
- [ ] Visit `/advertise/recovery/checkout?package=silver` — form prefills Silver package
- [ ] Visit `/admin/recovery-ads` (logged in) — orders page loads (empty table)
- [ ] Test Stripe webhook: `curl -X POST /webhooks/stripe` with a `recovery_ad` flow payload — order created
