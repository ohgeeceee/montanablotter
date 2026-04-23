# Bail Bond Agency Portal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each paying bail bond advertiser a private branded portal for managing their ad creative, subscription, and public profile — plus a public-facing agency page on Montana Blotter.

**Architecture:** A new `bail_agency_accounts` table stores portal credentials linked to `bail_ad_orders`. A standalone Flask Blueprint (`bail_agency_portal.py`) owns all portal routes with its own session key, keeping it isolated from both the admin auth (`users` table) and public subscriber auth (`public_users` table). Public agency pages live at `/bail-bonds/agencies/<slug>` in `app.py`.

**Tech Stack:** Python 3.12, Flask, Flask-Login (second LoginManager instance for agency sessions), SQLite, Stripe (customer portal for billing), bcrypt, Jinja2 templates.

---

## Scope

This plan covers three independent but connected subsystems. Each is implemented in order since later tasks depend on earlier ones.

| Subsystem | Tasks |
|-----------|-------|
| 1. Agency Auth & Accounts | 1–3 |
| 2. Portal Dashboard & Management | 4–7 |
| 3. Public Agency Page & Admin Provisioning | 8–9 |

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `init_db.py` | Modify | Add `bail_agency_accounts` table migration |
| `bail_agency_portal.py` | **Create** | Blueprint: login, logout, dashboard, profile, creative, subscription, analytics, leads |
| `app.py` | Modify | Register blueprint; add public `/bail-bonds/agencies/<slug>` route; auto-provision account on order activation |
| `templates/portal_bail_agency_login.html` | **Create** | Agency login page |
| `templates/portal_bail_agency_dashboard.html` | **Create** | Portal home: subscription status, quick stats |
| `templates/portal_bail_agency_profile.html` | **Create** | Edit business info, logo, description |
| `templates/portal_bail_agency_creative.html` | **Create** | Edit ad headline, body, CTA, URL |
| `templates/portal_bail_agency_subscription.html` | **Create** | View plan, launch Stripe billing portal, cancel |
| `templates/portal_bail_agency_analytics.html` | **Create** | Impressions, clicks, leads chart |
| `templates/bail_bonds_agency_page.html` | **Create** | Public-facing agency profile page |
| `templates/admin_bail_agency_accounts.html` | **Create** | Admin view: list accounts, provision, reset password |
| `tests/test_bail_agency_portal.py` | **Create** | Tests for auth, CRUD routes, public page |

---

## Task 1: DB Schema — `bail_agency_accounts`

**Files:**
- Modify: `init_db.py` (append to `migrate()`)
- Test: `tests/test_bail_agency_portal.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bail_agency_portal.py
import os, tempfile, sqlite3, unittest
import config, init_db

class BailAgencyPortalTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        self.prev_db = config.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        init_db.migrate()

    def tearDown(self):
        config.DB_PATH = self.prev_db
        init_db.DB_PATH = self.prev_db
        os.unlink(self.db_path)

    def _conn(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    def test_bail_agency_accounts_table_exists(self):
        conn = self._conn()
        cols = {r[1] for r in conn.execute("PRAGMA table_info('bail_agency_accounts')").fetchall()}
        conn.close()
        for expected in ('id', 'order_id', 'email', 'password_hash', 'business_name',
                         'slug', 'contact_name', 'phone', 'website_url', 'license_number',
                         'counties_served', 'about_text', 'logo_path', 'profile_status',
                         'reset_token', 'reset_token_expires_at', 'last_login_at',
                         'created_at', 'updated_at'):
            self.assertIn(expected, cols, f"Missing column: {expected}")
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd /root/montanablotter && source venv/bin/activate
python -m pytest tests/test_bail_agency_portal.py::BailAgencyPortalTests::test_bail_agency_accounts_table_exists -v
```
Expected: FAIL — table doesn't exist yet.

- [ ] **Step 3: Add migration to `init_db.py`**

In `migrate()`, after the `ensure_incident_notification_schema(conn)` call (which is right after subscribers is created), add:

```python
# Bail agency portal accounts
try:
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bail_agency_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER UNIQUE REFERENCES bail_ad_orders(id) ON DELETE CASCADE,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            business_name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            contact_name TEXT,
            phone TEXT,
            website_url TEXT,
            license_number TEXT,
            counties_served TEXT,
            about_text TEXT,
            logo_path TEXT,
            profile_status TEXT NOT NULL DEFAULT 'active',
            reset_token TEXT,
            reset_token_expires_at TEXT,
            last_login_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_bail_agency_accounts_email '
        'ON bail_agency_accounts(email)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_bail_agency_accounts_slug '
        'ON bail_agency_accounts(slug)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_bail_agency_accounts_order '
        'ON bail_agency_accounts(order_id)'
    )
except sqlite3.OperationalError:
    pass
```

Also add the same block inside `init_database()` after `ensure_incident_notification_schema(conn)`.

- [ ] **Step 4: Run test — expect PASS**

```bash
python -m pytest tests/test_bail_agency_portal.py::BailAgencyPortalTests::test_bail_agency_accounts_table_exists -v
```

- [ ] **Step 5: Run full suite — no regressions**

```bash
python -m pytest tests/ -q
```
Expected: same pass/fail count as before.

- [ ] **Step 6: Commit**

```bash
git add init_db.py tests/test_bail_agency_portal.py
git commit -m "feat: add bail_agency_accounts table for agency portal"
```

---

## Task 2: Agency Portal Blueprint — Auth (Login / Logout / Password Reset)

**Files:**
- Create: `bail_agency_portal.py`
- Test: `tests/test_bail_agency_portal.py`

- [ ] **Step 1: Write failing tests**

```python
# In tests/test_bail_agency_portal.py, add to BailAgencyPortalTests:

import app as app_module

class BailAgencyAuthTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        init_db.migrate()
        self.app = app_module.app
        self.app.config['TESTING'] = True
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.client = self.app.test_client()

    def tearDown(self):
        os.unlink(self.db_path)

    def _seed_agency(self, email='test@agency.com', password='Secure123!'):
        from werkzeug.security import generate_password_hash
        import secrets, re
        slug = re.sub(r'[^a-z0-9]+', '-', email.split('@')[0].lower())
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            '''INSERT INTO bail_agency_accounts
               (email, password_hash, business_name, slug, profile_status)
               VALUES (?, ?, ?, ?, 'active')''',
            (email, generate_password_hash(password), 'Test Bail Co', slug)
        )
        conn.commit()
        conn.close()
        return email, password

    def test_login_page_renders(self):
        r = self.client.get('/portal/bail-bond/login')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'login', r.data.lower())

    def test_login_with_valid_credentials_redirects_to_dashboard(self):
        email, pw = self._seed_agency()
        r = self.client.post('/portal/bail-bond/login',
                             data={'email': email, 'password': pw},
                             follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertIn('/portal/bail-bond/dashboard', r.headers['Location'])

    def test_login_with_bad_password_returns_error(self):
        email, _ = self._seed_agency()
        r = self.client.post('/portal/bail-bond/login',
                             data={'email': email, 'password': 'wrong'},
                             follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Invalid', r.data)

    def test_dashboard_requires_login(self):
        r = self.client.get('/portal/bail-bond/dashboard', follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertIn('/portal/bail-bond/login', r.headers['Location'])

    def test_logout_clears_session(self):
        email, pw = self._seed_agency()
        self.client.post('/portal/bail-bond/login', data={'email': email, 'password': pw})
        r = self.client.get('/portal/bail-bond/logout', follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        r2 = self.client.get('/portal/bail-bond/dashboard', follow_redirects=False)
        self.assertEqual(r2.status_code, 302)
```

- [ ] **Step 2: Run to confirm they fail**

```bash
python -m pytest tests/test_bail_agency_portal.py::BailAgencyAuthTests -v
```
Expected: FAIL (routes don't exist).

- [ ] **Step 3: Create `bail_agency_portal.py`**

```python
"""
Bail Agency Portal — private portal for paying bail bond advertisers.

Routes are prefixed /portal/bail-bond/ and use a dedicated session key
('_bail_agency_id') so they are fully isolated from admin and public user auth.
"""
from __future__ import annotations

import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (
    Blueprint, flash, g, redirect, render_template,
    request, session, url_for
)
from werkzeug.security import check_password_hash, generate_password_hash

import config

portal = Blueprint('bail_agency_portal', __name__, url_prefix='/portal/bail-bond')

SESSION_KEY = '_bail_agency_id'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def _current_agency():
    """Return the logged-in agency row, or None."""
    agency_id = session.get(SESSION_KEY)
    if not agency_id:
        return None
    conn = _get_db()
    row = conn.execute(
        "SELECT * FROM bail_agency_accounts WHERE id = ? AND profile_status = 'active'",
        (agency_id,),
    ).fetchone()
    conn.close()
    return row


def agency_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get(SESSION_KEY):
            return redirect(url_for('bail_agency_portal.login', next=request.path))
        return f(*args, **kwargs)
    return decorated


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    return text[:60].strip('-')


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@portal.route('/login', methods=['GET', 'POST'])
def login():
    if session.get(SESSION_KEY):
        return redirect(url_for('bail_agency_portal.dashboard'))

    error = None
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        password = request.form.get('password') or ''
        conn = _get_db()
        row = conn.execute(
            "SELECT * FROM bail_agency_accounts WHERE email = ? AND profile_status = 'active'",
            (email,),
        ).fetchone()
        if row and check_password_hash(row['password_hash'], password):
            conn.execute(
                "UPDATE bail_agency_accounts SET last_login_at = datetime('now') WHERE id = ?",
                (row['id'],),
            )
            conn.commit()
            conn.close()
            session[SESSION_KEY] = row['id']
            next_url = request.args.get('next') or url_for('bail_agency_portal.dashboard')
            return redirect(next_url)
        conn.close()
        error = 'Invalid email or password.'

    return render_template('portal_bail_agency_login.html', error=error)


@portal.route('/logout')
def logout():
    session.pop(SESSION_KEY, None)
    return redirect(url_for('bail_agency_portal.login'))


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@portal.route('/dashboard')
@agency_login_required
def dashboard():
    agency = _current_agency()
    conn = _get_db()
    # Active slots
    slots = conn.execute(
        "SELECT * FROM bail_ad_slots WHERE order_id = (SELECT order_id FROM bail_agency_accounts WHERE id = ?)",
        (agency['id'],),
    ).fetchall()
    # 30-day event counts
    counts = conn.execute(
        """SELECT event_type, COUNT(*) as n
           FROM bail_ad_events
           WHERE order_id = (SELECT order_id FROM bail_agency_accounts WHERE id = ?)
             AND created_at >= datetime('now', '-30 days')
           GROUP BY event_type""",
        (agency['id'],),
    ).fetchall()
    conn.close()
    stats = {r['event_type']: r['n'] for r in counts}
    return render_template('portal_bail_agency_dashboard.html',
                           agency=agency, slots=slots, stats=stats)
```

Continue in the same file — add the remaining route stubs (profile, creative, subscription, analytics, leads). Each returns `render_template(...)` with a placeholder template for now.

```python
@portal.route('/profile', methods=['GET', 'POST'])
@agency_login_required
def profile():
    agency = _current_agency()
    if request.method == 'POST':
        fields = {
            'business_name': (request.form.get('business_name') or '').strip(),
            'contact_name':  (request.form.get('contact_name') or '').strip(),
            'phone':         (request.form.get('phone') or '').strip(),
            'website_url':   (request.form.get('website_url') or '').strip(),
            'about_text':    (request.form.get('about_text') or '').strip(),
            'counties_served': (request.form.get('counties_served') or '').strip(),
        }
        conn = _get_db()
        conn.execute(
            """UPDATE bail_agency_accounts
               SET business_name=?, contact_name=?, phone=?, website_url=?,
                   about_text=?, counties_served=?, updated_at=datetime('now')
               WHERE id=?""",
            (*fields.values(), agency['id']),
        )
        conn.commit()
        conn.close()
        flash('Profile updated.', 'success')
        return redirect(url_for('bail_agency_portal.profile'))
    return render_template('portal_bail_agency_profile.html', agency=agency)


@portal.route('/creative', methods=['GET', 'POST'])
@agency_login_required
def creative():
    agency = _current_agency()
    conn = _get_db()
    creative_row = conn.execute(
        "SELECT * FROM bail_ad_creatives WHERE order_id = ?", (agency['order_id'],)
    ).fetchone() if agency['order_id'] else None

    if request.method == 'POST' and creative_row:
        conn.execute(
            """UPDATE bail_ad_creatives
               SET headline=?, body_copy=?, cta_text=?, target_url=?,
                   status='pending', updated_at=datetime('now')
               WHERE id=?""",
            (
                (request.form.get('headline') or '').strip(),
                (request.form.get('body_copy') or '').strip(),
                (request.form.get('cta_text') or '').strip(),
                (request.form.get('target_url') or '').strip(),
                creative_row['id'],
            ),
        )
        conn.commit()
        flash('Ad creative submitted for review.', 'success')
        return redirect(url_for('bail_agency_portal.creative'))

    conn.close()
    return render_template('portal_bail_agency_creative.html',
                           agency=agency, creative=creative_row)


@portal.route('/subscription')
@agency_login_required
def subscription():
    agency = _current_agency()
    conn = _get_db()
    order = conn.execute(
        "SELECT * FROM bail_ad_orders WHERE id = ?", (agency['order_id'],)
    ).fetchone() if agency['order_id'] else None
    conn.close()

    stripe_portal_url = None
    if order and order['provider_customer_id']:
        try:
            import stripe as _stripe
            _stripe.api_key = getattr(config, 'STRIPE_SECRET_KEY', '')
            portal_session = _stripe.billing_portal.Session.create(
                customer=order['provider_customer_id'],
                return_url=url_for('bail_agency_portal.subscription', _external=True),
            )
            stripe_portal_url = portal_session.url
        except Exception:
            pass

    return render_template('portal_bail_agency_subscription.html',
                           agency=agency, order=order,
                           stripe_portal_url=stripe_portal_url)


@portal.route('/analytics')
@agency_login_required
def analytics():
    agency = _current_agency()
    conn = _get_db()
    events = conn.execute(
        """SELECT date(created_at) as day, event_type, COUNT(*) as n
           FROM bail_ad_events
           WHERE order_id = ?
             AND created_at >= datetime('now', '-30 days')
           GROUP BY day, event_type
           ORDER BY day""",
        (agency['order_id'],),
    ).fetchall() if agency['order_id'] else []
    leads = conn.execute(
        """SELECT id, full_name, county, status, created_at
           FROM bail_consumer_leads
           WHERE ',' || routed_order_ids || ',' LIKE ?
           ORDER BY created_at DESC LIMIT 50""",
        (f'%,{agency["order_id"]},%',),
    ).fetchall() if agency['order_id'] else []
    conn.close()
    return render_template('portal_bail_agency_analytics.html',
                           agency=agency, events=list(events), leads=list(leads))
```

- [ ] **Step 4: Register blueprint in `app.py`**

Near the top of `app.py` where other blueprints or route files are imported, add:

```python
from bail_agency_portal import portal as bail_agency_portal_blueprint
app.register_blueprint(bail_agency_portal_blueprint)
```

- [ ] **Step 5: Run auth tests — expect PASS**

```bash
python -m pytest tests/test_bail_agency_portal.py::BailAgencyAuthTests -v
```

- [ ] **Step 6: Commit**

```bash
git add bail_agency_portal.py app.py tests/test_bail_agency_portal.py
git commit -m "feat: bail agency portal blueprint with auth routes"
```

---

## Task 3: Login Template

**Files:**
- Create: `templates/portal_bail_agency_login.html`

- [ ] **Step 1: Create the template**

```html
{% extends "base.html" %}
{% block title %}Agency Login – Montana Blotter{% endblock %}
{% block content %}
<div class="max-w-md mx-auto mt-20 p-8 bg-white rounded-xl shadow">
  <h1 class="text-2xl font-bold mb-2">Agency Portal Login</h1>
  <p class="text-gray-500 mb-6 text-sm">Sign in to manage your Montana Blotter listing.</p>
  {% if error %}
    <div class="bg-red-50 text-red-700 border border-red-200 rounded p-3 mb-4 text-sm">{{ error }}</div>
  {% endif %}
  <form method="POST">
    <label class="block text-sm font-medium mb-1">Email</label>
    <input type="email" name="email" required autocomplete="email"
           class="w-full border rounded px-3 py-2 mb-4 text-sm">
    <label class="block text-sm font-medium mb-1">Password</label>
    <input type="password" name="password" required autocomplete="current-password"
           class="w-full border rounded px-3 py-2 mb-6 text-sm">
    <button type="submit"
            class="w-full bg-blue-600 text-white rounded py-2 font-semibold hover:bg-blue-700">
      Sign In
    </button>
  </form>
  <p class="text-center text-xs text-gray-400 mt-4">
    Issues? Contact <a href="mailto:support@montanablotter.com" class="underline">support</a>.
  </p>
</div>
{% endblock %}
```

- [ ] **Step 2: Smoke test — login page renders without 500**

```bash
source venv/bin/activate && python -m pytest tests/test_bail_agency_portal.py::BailAgencyAuthTests::test_login_page_renders -v
```

- [ ] **Step 3: Commit**

```bash
git add templates/portal_bail_agency_login.html
git commit -m "feat: agency portal login template"
```

---

## Task 4: Dashboard Template

**Files:**
- Create: `templates/portal_bail_agency_dashboard.html`

- [ ] **Step 1: Write test**

```python
# In BailAgencyAuthTests:
def test_dashboard_renders_after_login(self):
    email, pw = self._seed_agency()
    self.client.post('/portal/bail-bond/login', data={'email': email, 'password': pw})
    r = self.client.get('/portal/bail-bond/dashboard')
    self.assertEqual(r.status_code, 200)
    self.assertIn(b'Test Bail Co', r.data)
```

- [ ] **Step 2: Run — expect FAIL (template doesn't exist)**

```bash
python -m pytest tests/test_bail_agency_portal.py::BailAgencyAuthTests::test_dashboard_renders_after_login -v
```

- [ ] **Step 3: Create template**

```html
{% extends "base.html" %}
{% block title %}Portal Dashboard – {{ agency.business_name }}{% endblock %}
{% block content %}
<div class="max-w-4xl mx-auto py-10 px-4">
  <div class="flex items-center justify-between mb-8">
    <div>
      <h1 class="text-2xl font-bold">{{ agency.business_name }}</h1>
      <p class="text-gray-500 text-sm">Agency Portal</p>
    </div>
    <a href="{{ url_for('bail_agency_portal.logout') }}"
       class="text-sm text-gray-500 hover:text-red-600">Sign out</a>
  </div>

  {# Subscription status banner #}
  {% if not agency.order_id %}
  <div class="bg-yellow-50 border border-yellow-200 rounded p-4 mb-6 text-sm text-yellow-800">
    No active advertising order linked. Contact support to activate your listing.
  </div>
  {% endif %}

  {# Quick stats #}
  <div class="grid grid-cols-3 gap-4 mb-8">
    <div class="bg-white rounded-xl shadow p-5 text-center">
      <div class="text-3xl font-bold text-blue-600">{{ stats.get('impression', 0) }}</div>
      <div class="text-xs text-gray-500 mt-1">Impressions (30d)</div>
    </div>
    <div class="bg-white rounded-xl shadow p-5 text-center">
      <div class="text-3xl font-bold text-green-600">{{ stats.get('click', 0) }}</div>
      <div class="text-xs text-gray-500 mt-1">Clicks (30d)</div>
    </div>
    <div class="bg-white rounded-xl shadow p-5 text-center">
      <div class="text-3xl font-bold text-purple-600">{{ stats.get('lead_view', 0) }}</div>
      <div class="text-xs text-gray-500 mt-1">Lead Views (30d)</div>
    </div>
  </div>

  {# Nav cards #}
  <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
    {% for label, icon, endpoint in [
        ('Profile', '🏢', 'bail_agency_portal.profile'),
        ('Ad Creative', '✏️', 'bail_agency_portal.creative'),
        ('Subscription', '💳', 'bail_agency_portal.subscription'),
        ('Analytics', '📊', 'bail_agency_portal.analytics'),
    ] %}
    <a href="{{ url_for(endpoint) }}"
       class="bg-white rounded-xl shadow p-5 text-center hover:shadow-md transition">
      <div class="text-3xl mb-2">{{ icon }}</div>
      <div class="text-sm font-semibold text-gray-700">{{ label }}</div>
    </a>
    {% endfor %}
  </div>

  {# Active counties #}
  {% if slots %}
  <div class="mt-8 bg-white rounded-xl shadow p-5">
    <h2 class="font-semibold text-gray-700 mb-3">Active Counties</h2>
    <div class="flex flex-wrap gap-2">
      {% for slot in slots %}
      <span class="bg-blue-50 text-blue-700 text-xs px-3 py-1 rounded-full">
        {{ slot.county }}
        {% if slot.status != 'active' %}<span class="text-gray-400"> ({{ slot.status }})</span>{% endif %}
      </span>
      {% endfor %}
    </div>
  </div>
  {% endif %}
</div>
{% endblock %}
```

- [ ] **Step 4: Run test — expect PASS**

```bash
python -m pytest tests/test_bail_agency_portal.py::BailAgencyAuthTests::test_dashboard_renders_after_login -v
```

- [ ] **Step 5: Commit**

```bash
git add templates/portal_bail_agency_dashboard.html
git commit -m "feat: agency portal dashboard template"
```

---

## Task 5: Profile, Creative & Subscription Templates

**Files:**
- Create: `templates/portal_bail_agency_profile.html`
- Create: `templates/portal_bail_agency_creative.html`
- Create: `templates/portal_bail_agency_subscription.html`

- [ ] **Step 1: Write tests**

```python
def test_profile_page_renders(self):
    email, pw = self._seed_agency()
    self.client.post('/portal/bail-bond/login', data={'email': email, 'password': pw})
    r = self.client.get('/portal/bail-bond/profile')
    self.assertEqual(r.status_code, 200)

def test_profile_update_saves_fields(self):
    email, pw = self._seed_agency()
    self.client.post('/portal/bail-bond/login', data={'email': email, 'password': pw})
    r = self.client.post('/portal/bail-bond/profile', data={
        'business_name': 'Updated Bail Co',
        'contact_name': 'Jane Doe',
        'phone': '406-555-0100',
        'website_url': 'https://example.com',
        'about_text': 'We serve Cascade County.',
        'counties_served': 'Cascade, Cascade',
    }, follow_redirects=True)
    self.assertEqual(r.status_code, 200)
    conn = sqlite3.connect(self.db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM bail_agency_accounts WHERE email=?", (email,)).fetchone()
    conn.close()
    self.assertEqual(row['business_name'], 'Updated Bail Co')
    self.assertEqual(row['contact_name'], 'Jane Doe')

def test_creative_page_renders(self):
    email, pw = self._seed_agency()
    self.client.post('/portal/bail-bond/login', data={'email': email, 'password': pw})
    r = self.client.get('/portal/bail-bond/creative')
    self.assertEqual(r.status_code, 200)

def test_subscription_page_renders(self):
    email, pw = self._seed_agency()
    self.client.post('/portal/bail-bond/login', data={'email': email, 'password': pw})
    r = self.client.get('/portal/bail-bond/subscription')
    self.assertEqual(r.status_code, 200)
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
python -m pytest tests/test_bail_agency_portal.py -k "profile or creative or subscription" -v
```

- [ ] **Step 3: Create `templates/portal_bail_agency_profile.html`**

```html
{% extends "base.html" %}
{% block title %}Agency Profile – Montana Blotter Portal{% endblock %}
{% block content %}
<div class="max-w-2xl mx-auto py-10 px-4">
  <div class="flex items-center gap-4 mb-6">
    <a href="{{ url_for('bail_agency_portal.dashboard') }}" class="text-blue-600 text-sm">← Dashboard</a>
    <h1 class="text-xl font-bold">Business Profile</h1>
  </div>
  {% with messages = get_flashed_messages(with_categories=true) %}
    {% for cat, msg in messages %}
      <div class="bg-green-50 text-green-700 border border-green-200 rounded p-3 mb-4 text-sm">{{ msg }}</div>
    {% endfor %}
  {% endwith %}
  <form method="POST" class="bg-white rounded-xl shadow p-6 space-y-4">
    <div>
      <label class="block text-sm font-medium mb-1">Business Name *</label>
      <input type="text" name="business_name" required value="{{ agency.business_name or '' }}"
             class="w-full border rounded px-3 py-2 text-sm">
    </div>
    <div class="grid grid-cols-2 gap-4">
      <div>
        <label class="block text-sm font-medium mb-1">Contact Name</label>
        <input type="text" name="contact_name" value="{{ agency.contact_name or '' }}"
               class="w-full border rounded px-3 py-2 text-sm">
      </div>
      <div>
        <label class="block text-sm font-medium mb-1">Phone</label>
        <input type="tel" name="phone" value="{{ agency.phone or '' }}"
               class="w-full border rounded px-3 py-2 text-sm">
      </div>
    </div>
    <div>
      <label class="block text-sm font-medium mb-1">Website URL</label>
      <input type="url" name="website_url" value="{{ agency.website_url or '' }}"
             class="w-full border rounded px-3 py-2 text-sm">
    </div>
    <div>
      <label class="block text-sm font-medium mb-1">Counties Served</label>
      <input type="text" name="counties_served" value="{{ agency.counties_served or '' }}"
             placeholder="e.g. Cascade, Lewis and Clark, Yellowstone"
             class="w-full border rounded px-3 py-2 text-sm">
    </div>
    <div>
      <label class="block text-sm font-medium mb-1">About Your Agency</label>
      <textarea name="about_text" rows="4"
                class="w-full border rounded px-3 py-2 text-sm">{{ agency.about_text or '' }}</textarea>
    </div>
    <button type="submit"
            class="bg-blue-600 text-white rounded px-6 py-2 text-sm font-semibold hover:bg-blue-700">
      Save Profile
    </button>
  </form>

  <p class="text-xs text-gray-400 mt-4">
    Your public agency page:
    <a href="{{ url_for('bail_bonds_agency_page', slug=agency.slug) }}"
       class="underline text-blue-500" target="_blank">
      montanablotter.com/bail-bonds/agencies/{{ agency.slug }}
    </a>
  </p>
</div>
{% endblock %}
```

- [ ] **Step 4: Create `templates/portal_bail_agency_creative.html`**

```html
{% extends "base.html" %}
{% block title %}Ad Creative – Montana Blotter Portal{% endblock %}
{% block content %}
<div class="max-w-2xl mx-auto py-10 px-4">
  <div class="flex items-center gap-4 mb-6">
    <a href="{{ url_for('bail_agency_portal.dashboard') }}" class="text-blue-600 text-sm">← Dashboard</a>
    <h1 class="text-xl font-bold">Ad Creative</h1>
  </div>
  {% if not creative %}
    <div class="bg-yellow-50 border border-yellow-200 rounded p-4 text-sm text-yellow-800">
      No ad creative found. Contact support if you believe this is an error.
    </div>
  {% else %}
  {% with messages = get_flashed_messages(with_categories=true) %}
    {% for cat, msg in messages %}
      <div class="bg-green-50 text-green-700 border border-green-200 rounded p-3 mb-4 text-sm">{{ msg }}</div>
    {% endfor %}
  {% endwith %}
  {% if creative.status == 'pending' %}
    <div class="bg-blue-50 text-blue-700 border border-blue-200 rounded p-3 mb-4 text-sm">
      Your creative is under review. Changes will be re-submitted for approval.
    </div>
  {% endif %}
  <form method="POST" class="bg-white rounded-xl shadow p-6 space-y-4">
    <div>
      <label class="block text-sm font-medium mb-1">Headline *</label>
      <input type="text" name="headline" required maxlength="80"
             value="{{ creative.headline or '' }}"
             class="w-full border rounded px-3 py-2 text-sm">
      <p class="text-xs text-gray-400 mt-1">Max 80 characters</p>
    </div>
    <div>
      <label class="block text-sm font-medium mb-1">Body Copy *</label>
      <textarea name="body_copy" required rows="4" maxlength="400"
                class="w-full border rounded px-3 py-2 text-sm">{{ creative.body_copy or '' }}</textarea>
    </div>
    <div class="grid grid-cols-2 gap-4">
      <div>
        <label class="block text-sm font-medium mb-1">Call to Action Text</label>
        <input type="text" name="cta_text" maxlength="40"
               value="{{ creative.cta_text or '' }}"
               placeholder="e.g. Call Now"
               class="w-full border rounded px-3 py-2 text-sm">
      </div>
      <div>
        <label class="block text-sm font-medium mb-1">Target URL *</label>
        <input type="url" name="target_url" required
               value="{{ creative.target_url or '' }}"
               class="w-full border rounded px-3 py-2 text-sm">
      </div>
    </div>
    <button type="submit"
            class="bg-blue-600 text-white rounded px-6 py-2 text-sm font-semibold hover:bg-blue-700">
      Submit for Review
    </button>
  </form>
  {% endif %}
</div>
{% endblock %}
```

- [ ] **Step 5: Create `templates/portal_bail_agency_subscription.html`**

```html
{% extends "base.html" %}
{% block title %}Subscription – Montana Blotter Portal{% endblock %}
{% block content %}
<div class="max-w-2xl mx-auto py-10 px-4">
  <div class="flex items-center gap-4 mb-6">
    <a href="{{ url_for('bail_agency_portal.dashboard') }}" class="text-blue-600 text-sm">← Dashboard</a>
    <h1 class="text-xl font-bold">Subscription & Billing</h1>
  </div>
  <div class="bg-white rounded-xl shadow p-6">
    {% if order %}
      <dl class="divide-y">
        <div class="flex justify-between py-3 text-sm">
          <dt class="text-gray-500">Plan</dt>
          <dd class="font-medium">{{ order.package_id | replace('_', ' ') | title }}</dd>
        </div>
        <div class="flex justify-between py-3 text-sm">
          <dt class="text-gray-500">Status</dt>
          <dd>
            <span class="px-2 py-0.5 rounded-full text-xs font-semibold
              {% if order.status == 'active' %}bg-green-100 text-green-700
              {% else %}bg-yellow-100 text-yellow-700{% endif %}">
              {{ order.status }}
            </span>
          </dd>
        </div>
        <div class="flex justify-between py-3 text-sm">
          <dt class="text-gray-500">Billing</dt>
          <dd class="font-medium">${{ (order.amount_cents / 100) | int }} / {{ order.billing_cycle }}</dd>
        </div>
        {% if order.paid_at %}
        <div class="flex justify-between py-3 text-sm">
          <dt class="text-gray-500">Last Payment</dt>
          <dd>{{ order.paid_at[:10] }}</dd>
        </div>
        {% endif %}
      </dl>
      {% if stripe_portal_url %}
      <div class="mt-6">
        <a href="{{ stripe_portal_url }}" target="_blank"
           class="inline-block bg-blue-600 text-white text-sm font-semibold px-6 py-2 rounded hover:bg-blue-700">
          Manage Billing (Stripe Portal) ↗
        </a>
        <p class="text-xs text-gray-400 mt-2">Update payment method, download invoices, or cancel.</p>
      </div>
      {% endif %}
    {% else %}
      <p class="text-gray-500 text-sm">No active subscription found. Contact support for assistance.</p>
    {% endif %}
  </div>
</div>
{% endblock %}
```

- [ ] **Step 6: Run all tests — expect PASS**

```bash
python -m pytest tests/test_bail_agency_portal.py -v
```

- [ ] **Step 7: Commit**

```bash
git add templates/portal_bail_agency_profile.html templates/portal_bail_agency_creative.html templates/portal_bail_agency_subscription.html
git commit -m "feat: agency portal management templates (profile, creative, subscription)"
```

---

## Task 6: Analytics Template

**Files:**
- Create: `templates/portal_bail_agency_analytics.html`

- [ ] **Step 1: Write test**

```python
def test_analytics_page_renders(self):
    email, pw = self._seed_agency()
    self.client.post('/portal/bail-bond/login', data={'email': email, 'password': pw})
    r = self.client.get('/portal/bail-bond/analytics')
    self.assertEqual(r.status_code, 200)
    self.assertIn(b'Analytics', r.data)
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Create template**

```html
{% extends "base.html" %}
{% block title %}Analytics – Montana Blotter Portal{% endblock %}
{% block content %}
<div class="max-w-4xl mx-auto py-10 px-4">
  <div class="flex items-center gap-4 mb-6">
    <a href="{{ url_for('bail_agency_portal.dashboard') }}" class="text-blue-600 text-sm">← Dashboard</a>
    <h1 class="text-xl font-bold">Analytics (Last 30 Days)</h1>
  </div>

  {# Event summary table #}
  <div class="bg-white rounded-xl shadow p-6 mb-6">
    <h2 class="font-semibold text-gray-700 mb-4">Ad Events</h2>
    {% if events %}
    <table class="w-full text-sm">
      <thead>
        <tr class="border-b text-left text-gray-500">
          <th class="pb-2">Date</th>
          <th class="pb-2">Event</th>
          <th class="pb-2 text-right">Count</th>
        </tr>
      </thead>
      <tbody class="divide-y">
        {% for e in events %}
        <tr>
          <td class="py-2 text-gray-600">{{ e.day }}</td>
          <td class="py-2 font-medium">{{ e.event_type | replace('_', ' ') | title }}</td>
          <td class="py-2 text-right">{{ e.n }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    {% else %}
      <p class="text-gray-400 text-sm">No ad events in the last 30 days.</p>
    {% endif %}
  </div>

  {# Lead inbox #}
  <div class="bg-white rounded-xl shadow p-6">
    <h2 class="font-semibold text-gray-700 mb-4">Routed Leads</h2>
    {% if leads %}
    <table class="w-full text-sm">
      <thead>
        <tr class="border-b text-left text-gray-500">
          <th class="pb-2">Name</th>
          <th class="pb-2">County</th>
          <th class="pb-2">Status</th>
          <th class="pb-2">Received</th>
        </tr>
      </thead>
      <tbody class="divide-y">
        {% for lead in leads %}
        <tr>
          <td class="py-2 font-medium">{{ lead.full_name }}</td>
          <td class="py-2 text-gray-600">{{ lead.county }}</td>
          <td class="py-2">
            <span class="text-xs px-2 py-0.5 rounded-full
              {% if lead.status == 'new' %}bg-blue-50 text-blue-700
              {% else %}bg-gray-100 text-gray-500{% endif %}">
              {{ lead.status }}
            </span>
          </td>
          <td class="py-2 text-gray-400 text-xs">{{ lead.created_at[:10] }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    {% else %}
      <p class="text-gray-400 text-sm">No leads routed to your account yet.</p>
    {% endif %}
  </div>
</div>
{% endblock %}
```

- [ ] **Step 4: Run — expect PASS**

```bash
python -m pytest tests/test_bail_agency_portal.py::BailAgencyAuthTests::test_analytics_page_renders -v
```

- [ ] **Step 5: Commit**

```bash
git add templates/portal_bail_agency_analytics.html
git commit -m "feat: agency portal analytics template"
```

---

## Task 7: Public Agency Page

**Files:**
- Create: `templates/bail_bonds_agency_page.html`
- Modify: `app.py` (add route `/bail-bonds/agencies/<slug>`)

- [ ] **Step 1: Write test**

```python
def test_public_agency_page_returns_200_for_active_agency(self):
    # Seed an agency directly
    conn = sqlite3.connect(self.db_path)
    conn.execute(
        '''INSERT INTO bail_agency_accounts
           (email, password_hash, business_name, slug, profile_status)
           VALUES ('pub@test.com', 'x', 'Public Bail Co', 'public-bail-co', 'active')'''
    )
    conn.commit()
    conn.close()
    r = self.client.get('/bail-bonds/agencies/public-bail-co')
    self.assertEqual(r.status_code, 200)
    self.assertIn(b'Public Bail Co', r.data)

def test_public_agency_page_returns_404_for_unknown_slug(self):
    r = self.client.get('/bail-bonds/agencies/does-not-exist')
    self.assertEqual(r.status_code, 404)
```

- [ ] **Step 2: Run — expect FAIL**

```bash
python -m pytest tests/test_bail_agency_portal.py -k "public_agency_page" -v
```

- [ ] **Step 3: Add route to `app.py`**

Find the bail-bonds directory routes section in `app.py` (around line 11258 near `/bail-bonds`). Add after the existing directory routes:

```python
@app.route('/bail-bonds/agencies/<slug>')
def bail_bonds_agency_page(slug):
    conn = get_db()
    agency = conn.execute(
        "SELECT * FROM bail_agency_accounts WHERE slug = ? AND profile_status = 'active'",
        (slug,),
    ).fetchone()
    if not agency:
        conn.close()
        abort(404)
    creative = None
    if agency['order_id']:
        creative = conn.execute(
            "SELECT * FROM bail_ad_creatives WHERE order_id = ? AND status = 'approved'",
            (agency['order_id'],),
        ).fetchone()
    conn.close()
    return render_template('bail_bonds_agency_page.html', agency=agency, creative=creative)
```

- [ ] **Step 4: Create `templates/bail_bonds_agency_page.html`**

```html
{% extends "base.html" %}
{% block title %}{{ agency.business_name }} – Bail Bond Agency | Montana Blotter{% endblock %}
{% block meta_description %}{{ agency.business_name }} serves {{ agency.counties_served or 'Montana' }}.
Licensed bail bond agency. {{ (agency.about_text or '')[:120] }}{% endblock %}
{% block content %}
<div class="max-w-3xl mx-auto py-10 px-4">
  <nav class="text-sm text-gray-500 mb-6">
    <a href="/bail-bonds" class="hover:underline">Bail Bonds</a> › {{ agency.business_name }}
  </nav>

  <div class="bg-white rounded-2xl shadow-sm border p-8">
    {# Header #}
    <div class="flex items-start gap-6 mb-6">
      {% if agency.logo_path %}
      <img src="/{{ agency.logo_path }}" alt="{{ agency.business_name }} logo"
           class="w-20 h-20 object-contain rounded-xl border">
      {% else %}
      <div class="w-20 h-20 rounded-xl bg-blue-50 flex items-center justify-center text-3xl">🏛️</div>
      {% endif %}
      <div>
        <h1 class="text-2xl font-bold text-gray-900">{{ agency.business_name }}</h1>
        {% if agency.counties_served %}
        <p class="text-sm text-gray-500 mt-1">Serving: {{ agency.counties_served }}</p>
        {% endif %}
        {% if agency.license_number %}
        <p class="text-xs text-gray-400 mt-0.5">License #{{ agency.license_number }}</p>
        {% endif %}
      </div>
    </div>

    {# About #}
    {% if agency.about_text %}
    <div class="prose prose-sm max-w-none mb-6 text-gray-700">
      {{ agency.about_text }}
    </div>
    {% endif %}

    {# Contact #}
    <div class="grid grid-cols-2 gap-4 mb-8">
      {% if agency.phone %}
      <a href="tel:{{ agency.phone }}"
         class="flex items-center gap-2 bg-blue-600 text-white rounded-lg px-4 py-3 text-sm font-semibold hover:bg-blue-700">
        📞 {{ agency.phone }}
      </a>
      {% endif %}
      {% if agency.website_url %}
      <a href="{{ agency.website_url }}" target="_blank" rel="noopener noreferrer"
         class="flex items-center gap-2 border rounded-lg px-4 py-3 text-sm font-semibold hover:bg-gray-50">
        🌐 Visit Website ↗
      </a>
      {% endif %}
    </div>

    {# Ad creative (if approved) #}
    {% if creative %}
    <div class="bg-blue-50 border border-blue-100 rounded-xl p-5">
      <p class="font-bold text-gray-900">{{ creative.headline }}</p>
      <p class="text-sm text-gray-600 mt-1">{{ creative.body_copy }}</p>
      {% if creative.cta_text and creative.target_url %}
      <a href="{{ creative.target_url }}" target="_blank" rel="noopener noreferrer"
         class="inline-block mt-3 bg-blue-600 text-white text-xs font-semibold px-4 py-2 rounded hover:bg-blue-700">
        {{ creative.cta_text }} →
      </a>
      {% endif %}
    </div>
    {% endif %}
  </div>

  {# Intake CTA #}
  <div class="mt-6 bg-gray-50 rounded-xl p-6 text-center">
    <h2 class="font-semibold text-gray-800 mb-1">Need a bail bond?</h2>
    <p class="text-sm text-gray-500 mb-3">Submit a quick request and get connected fast.</p>
    <a href="/bail-bonds/intake"
       class="inline-block bg-green-600 text-white text-sm font-semibold px-6 py-2 rounded-lg hover:bg-green-700">
      Get Help Now
    </a>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
python -m pytest tests/test_bail_agency_portal.py -k "public_agency_page" -v
```

- [ ] **Step 6: Commit**

```bash
git add app.py templates/bail_bonds_agency_page.html
git commit -m "feat: public bail bond agency profile page at /bail-bonds/agencies/<slug>"
```

---

## Task 8: Admin Provisioning — Create & Link Accounts

**Files:**
- Modify: `app.py` (admin route to provision portal accounts)
- Create: `templates/admin_bail_agency_accounts.html`

Admins need a way to create portal accounts for approved orders and reset passwords.

- [ ] **Step 1: Write test**

```python
def _seed_admin(self):
    from werkzeug.security import generate_password_hash
    conn = sqlite3.connect(self.db_path)
    conn.execute(
        "INSERT INTO users (username, password, role, is_active) VALUES (?, ?, ?, 1)",
        ('admin', generate_password_hash('adminpass'), 'super_admin'),
    )
    conn.commit()
    conn.close()

def test_admin_bail_agency_accounts_page_requires_login(self):
    r = self.client.get('/admin/bail-ads/agency-accounts', follow_redirects=False)
    self.assertEqual(r.status_code, 302)

def test_admin_can_provision_agency_account(self):
    self._seed_admin()
    self.client.post('/admin/login', data={'username': 'admin', 'password': 'adminpass'})
    r = self.client.post('/admin/bail-ads/agency-accounts/create', data={
        'email': 'newagency@test.com',
        'business_name': 'New Bail Agency',
        'password': 'TempPass123!',
    }, follow_redirects=True)
    self.assertEqual(r.status_code, 200)
    conn = sqlite3.connect(self.db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM bail_agency_accounts WHERE email=?", ('newagency@test.com',)).fetchone()
    conn.close()
    self.assertIsNotNone(row)
    self.assertEqual(row['business_name'], 'New Bail Agency')
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Add admin routes to `app.py`**

Find the admin bail-ads section (~line 15320). Add:

```python
@app.route('/admin/bail-ads/agency-accounts')
@login_required
def admin_bail_agency_accounts():
    conn = get_db()
    accounts = conn.execute(
        """SELECT a.*, o.package_id, o.status as order_status
           FROM bail_agency_accounts a
           LEFT JOIN bail_ad_orders o ON o.id = a.order_id
           ORDER BY a.created_at DESC"""
    ).fetchall()
    conn.close()
    return render_template('admin_bail_agency_accounts.html', accounts=accounts)


@app.route('/admin/bail-ads/agency-accounts/create', methods=['POST'])
@login_required
def admin_bail_agency_accounts_create():
    import re as _re
    email = (request.form.get('email') or '').strip().lower()
    business_name = (request.form.get('business_name') or '').strip()
    password = request.form.get('password') or ''
    order_id = request.form.get('order_id') or None

    if not email or not business_name or not password:
        flash('Email, business name, and password are required.', 'error')
        return redirect(url_for('admin_bail_agency_accounts'))

    slug_base = _re.sub(r'[^a-z0-9]+', '-', business_name.lower())[:50].strip('-')
    slug = slug_base
    conn = get_db()
    # Ensure slug uniqueness
    i = 2
    while conn.execute("SELECT 1 FROM bail_agency_accounts WHERE slug=?", (slug,)).fetchone():
        slug = f"{slug_base}-{i}"
        i += 1

    try:
        conn.execute(
            """INSERT INTO bail_agency_accounts
               (email, password_hash, business_name, slug, order_id, profile_status)
               VALUES (?, ?, ?, ?, ?, 'active')""",
            (email, bcrypt.generate_password_hash(password).decode('utf-8'),
             business_name, slug, order_id),
        )
        conn.commit()
        flash(f'Portal account created for {email}. They can log in at /portal/bail-bond/login', 'success')
    except Exception as e:
        flash(f'Error: {e}', 'error')
    finally:
        conn.close()

    return redirect(url_for('admin_bail_agency_accounts'))


@app.route('/admin/bail-ads/agency-accounts/<int:account_id>/reset-password', methods=['POST'])
@login_required
def admin_bail_agency_reset_password(account_id):
    new_pw = request.form.get('new_password') or ''
    if len(new_pw) < 8:
        flash('Password must be at least 8 characters.', 'error')
        return redirect(url_for('admin_bail_agency_accounts'))
    conn = get_db()
    conn.execute(
        "UPDATE bail_agency_accounts SET password_hash=?, updated_at=datetime('now') WHERE id=?",
        (bcrypt.generate_password_hash(new_pw).decode('utf-8'), account_id),
    )
    conn.commit()
    conn.close()
    flash('Password reset.', 'success')
    return redirect(url_for('admin_bail_agency_accounts'))
```

- [ ] **Step 4: Create `templates/admin_bail_agency_accounts.html`**

```html
{% extends "admin_base.html" %}
{% block title %}Agency Portal Accounts{% endblock %}
{% block content %}
<div class="p-6">
  <div class="flex justify-between items-center mb-6">
    <h1 class="text-xl font-bold">Agency Portal Accounts</h1>
    <button onclick="document.getElementById('create-modal').classList.remove('hidden')"
            class="bg-blue-600 text-white text-sm px-4 py-2 rounded hover:bg-blue-700">
      + Provision Account
    </button>
  </div>

  <table class="w-full text-sm bg-white rounded-xl shadow">
    <thead class="bg-gray-50">
      <tr>
        <th class="text-left p-3">Business</th>
        <th class="text-left p-3">Email</th>
        <th class="text-left p-3">Slug</th>
        <th class="text-left p-3">Order</th>
        <th class="text-left p-3">Status</th>
        <th class="text-left p-3">Last Login</th>
        <th class="text-left p-3">Actions</th>
      </tr>
    </thead>
    <tbody class="divide-y">
      {% for a in accounts %}
      <tr>
        <td class="p-3 font-medium">{{ a.business_name }}</td>
        <td class="p-3 text-gray-500">{{ a.email }}</td>
        <td class="p-3">
          <a href="/bail-bonds/agencies/{{ a.slug }}" target="_blank" class="text-blue-600 underline text-xs">
            /bail-bonds/agencies/{{ a.slug }}
          </a>
        </td>
        <td class="p-3 text-gray-500">{{ a.order_id or '—' }}</td>
        <td class="p-3">
          <span class="px-2 py-0.5 rounded-full text-xs font-semibold
            {% if a.profile_status == 'active' %}bg-green-100 text-green-700
            {% else %}bg-red-100 text-red-700{% endif %}">
            {{ a.profile_status }}
          </span>
        </td>
        <td class="p-3 text-gray-400 text-xs">{{ (a.last_login_at or 'Never')[:16] }}</td>
        <td class="p-3">
          <form method="POST"
                action="/admin/bail-ads/agency-accounts/{{ a.id }}/reset-password"
                class="inline-flex gap-2">
            <input type="password" name="new_password" placeholder="New password" minlength="8"
                   class="border rounded px-2 py-1 text-xs w-28">
            <button type="submit" class="text-xs bg-yellow-100 text-yellow-800 px-2 py-1 rounded hover:bg-yellow-200">
              Reset PW
            </button>
          </form>
        </td>
      </tr>
      {% else %}
      <tr><td colspan="7" class="p-6 text-center text-gray-400">No portal accounts yet.</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>

{# Create account modal #}
<div id="create-modal" class="hidden fixed inset-0 bg-black/50 flex items-center justify-center z-50">
  <div class="bg-white rounded-xl shadow-xl p-6 w-full max-w-md">
    <h2 class="font-bold text-lg mb-4">Provision Portal Account</h2>
    <form method="POST" action="/admin/bail-ads/agency-accounts/create" class="space-y-3">
      <div>
        <label class="block text-sm font-medium mb-1">Business Name *</label>
        <input type="text" name="business_name" required class="w-full border rounded px-3 py-2 text-sm">
      </div>
      <div>
        <label class="block text-sm font-medium mb-1">Email *</label>
        <input type="email" name="email" required class="w-full border rounded px-3 py-2 text-sm">
      </div>
      <div>
        <label class="block text-sm font-medium mb-1">Temporary Password *</label>
        <input type="password" name="password" required minlength="8"
               class="w-full border rounded px-3 py-2 text-sm">
      </div>
      <div>
        <label class="block text-sm font-medium mb-1">Link to Order ID (optional)</label>
        <input type="number" name="order_id" class="w-full border rounded px-3 py-2 text-sm">
      </div>
      <div class="flex gap-3 pt-2">
        <button type="submit"
                class="flex-1 bg-blue-600 text-white text-sm font-semibold py-2 rounded hover:bg-blue-700">
          Create Account
        </button>
        <button type="button"
                onclick="document.getElementById('create-modal').classList.add('hidden')"
                class="flex-1 border text-sm py-2 rounded hover:bg-gray-50">
          Cancel
        </button>
      </div>
    </form>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 5: Add link in admin nav**

In `templates/admin.html` (or whichever template has the admin sidebar), add:
```html
<a href="{{ url_for('admin_bail_agency_accounts') }}">Agency Portal Accounts</a>
```
near the other bail-ads admin links.

- [ ] **Step 6: Run all tests**

```bash
python -m pytest tests/ -q
```

- [ ] **Step 7: Commit**

```bash
git add app.py templates/admin_bail_agency_accounts.html
git commit -m "feat: admin provisioning for bail agency portal accounts"
```

---

## Task 9: Wire-up — Auto-Provision on Order Activation (Optional Enhancement)

**Goal:** When an admin marks a bail_ad_order as `active`, automatically create the portal account if one doesn't exist, and email the agency their login link.

This is additive — implement only after Tasks 1–8 are complete and verified.

**Files:**
- Modify: `app.py` (in the order status update route, add auto-provision logic)

- [ ] **Step 1: Write test**

```python
def test_order_activation_provisions_portal_account(self):
    self._seed_admin()
    self.client.post('/admin/login', data={'username': 'admin', 'password': 'adminpass'})
    # Create a bail_ad_order
    conn = sqlite3.connect(self.db_path)
    conn.execute(
        """INSERT INTO bail_ad_orders
           (business_name, email, package_id, billing_cycle, amount_cents, status)
           VALUES ('Auto Agency', 'auto@test.com', 'county_feature', 'monthly', 9900, 'checkout_pending')"""
    )
    order_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    # Mark order as active
    r = self.client.post(f'/admin/bail-ads/orders/{order_id}/status',
                         data={'status': 'active'}, follow_redirects=True)
    self.assertEqual(r.status_code, 200)
    conn = sqlite3.connect(self.db_path)
    conn.row_factory = sqlite3.Row
    account = conn.execute(
        "SELECT * FROM bail_agency_accounts WHERE order_id=?", (order_id,)
    ).fetchone()
    conn.close()
    self.assertIsNotNone(account)
    self.assertEqual(account['email'], 'auto@test.com')
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Add auto-provision logic**

In `app.py`, find `admin_bail_ad_order_status` (the route at `/admin/bail-ads/orders/<int:order_id>/status`). After the status is updated to `'active'`, add:

```python
if new_status == 'active':
    # Auto-provision agency portal account if not yet created
    existing_account = conn.execute(
        "SELECT id FROM bail_agency_accounts WHERE order_id=?", (order_id,)
    ).fetchone()
    if not existing_account:
        order_row = conn.execute(
            "SELECT * FROM bail_ad_orders WHERE id=?", (order_id,)
        ).fetchone()
        if order_row and order_row['email']:
            import re as _re
            slug_base = _re.sub(r'[^a-z0-9]+', '-', (order_row['business_name'] or 'agency').lower())[:50].strip('-')
            slug = slug_base
            i = 2
            while conn.execute("SELECT 1 FROM bail_agency_accounts WHERE slug=?", (slug,)).fetchone():
                slug = f"{slug_base}-{i}"
                i += 1
            temp_password = secrets.token_urlsafe(12)
            try:
                conn.execute(
                    """INSERT INTO bail_agency_accounts
                       (order_id, email, password_hash, business_name, slug,
                        website_url, license_number, profile_status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'active')""",
                    (
                        order_id,
                        order_row['email'].strip().lower(),
                        bcrypt.generate_password_hash(temp_password).decode('utf-8'),
                        order_row['business_name'] or 'Agency',
                        slug,
                        order_row['website_url'] or '',
                        order_row['license_number'] or '',
                    ),
                )
                # TODO: send welcome email with login link and temp_password
                logging.info(f"Auto-provisioned portal for order {order_id}: {order_row['email']}")
            except Exception as _e:
                logging.error(f"Failed to auto-provision portal account for order {order_id}: {_e}")
```

- [ ] **Step 4: Run all tests**

```bash
python -m pytest tests/ -q
```

- [ ] **Step 5: Final commit**

```bash
git add app.py tests/test_bail_agency_portal.py
git commit -m "feat: auto-provision agency portal account on order activation"
```

---

## Testing Checklist (Manual QA)

After all tasks complete:

- [ ] Create a portal account via admin at `/admin/bail-ads/agency-accounts`
- [ ] Log in at `/portal/bail-bond/login`
- [ ] Update profile → verify at `/bail-bonds/agencies/<slug>`
- [ ] Submit creative → verify admin sees it as 'pending' in `/admin/bail-ads`
- [ ] Approve creative in admin → verify it appears on public agency page
- [ ] Visit Stripe billing portal from subscription page (needs real Stripe config in config.py)
- [ ] Activate an order in admin → verify portal account auto-provisioned

---

## Notes for Implementer

1. **`bcrypt` is already imported in `app.py`** — use `bcrypt.generate_password_hash()` in routes added to `app.py`. In `bail_agency_portal.py`, use `werkzeug.security.check_password_hash` directly (no bcrypt dependency needed for verification).

2. **Logo upload not included** in this plan — it can be added as a follow-on task using the existing file-upload pattern in `app.py` (`_save_uploaded_file()` or similar).

3. **Welcome email** when auto-provisioning (Task 9 step 3 TODO) should use the existing `EmailWorker.send_email()` pattern from `email_worker.py`.

4. **The `abort(404)` in the public agency page route** requires `from flask import abort` — check if it's already imported in `app.py` before adding.

5. **Session isolation**: the portal uses `session[SESSION_KEY]` (a plain Flask session key) rather than Flask-Login. This means `current_user` in templates will still refer to the admin user (or AnonymousUser). Do NOT use `@login_required` on portal routes — use `@agency_login_required` from the blueprint instead.
