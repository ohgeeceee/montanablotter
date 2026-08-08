# Law Enforcement Agency Self-Service Panel — Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Build the LEA panel multi-tenant web application and ingestion workers so Montana law enforcement agencies can self-publish blotters and jail rosters to montanablotter.com.

**Architecture:** Additive layer on the existing Flask monolith. New multi-tenant database tables, new blueprints, new REST API endpoints, new admin dashboard, and cron workers that feed published records into the public-facing tables.

**Tech Stack:** Python 3.12 + Flask + SQLite (existing monolith), RBAC via user roles, JWT API tokens, immutable audit logging, row-level security.

---

## Phase 1: Database Schema & Migrations (Week 1)

### Task 1.1: Create `lea_agencies` table + indexes
**Objective:** Establish the agency registry with ORI verification and coverage tiers.

**Files:**
- Modify: `init_db.py:ensure_lea_schema()` (create new function)
- Test: `tests/test_lea_agencies.py`

**Step 1: Write failing test**

```python
# tests/test_lea_agencies.py
import os, sqlite3, tempfile, unittest
import app as app_module
import config
import init_db

class TestLEAAgenciesSchema(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-lea-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        
        conn = sqlite3.connect(self.db_path)
        init_db.ensure_lea_schema(conn)
        self.conn = conn

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_lea_agencies_table_exists(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='lea_agencies'")
        self.assertIsNotNone(cursor.fetchone())

    def test_lea_agencies_has_required_columns(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute("PRAGMA table_info(lea_agencies)")
        columns = {row[1] for row in cursor.fetchall()}
        required = {'id', 'org_name', 'agency_type', 'county_slug', 'ori_number', 'verification_status'}
        self.assertTrue(required.issubset(columns))

    def test_lea_agencies_org_name_unique(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO lea_agencies (org_name, agency_type, county_slug, county_name, primary_contact_email) VALUES (?, ?, ?, ?, ?)",
            ("Great Falls PD", "police", "cascade", "Cascade", "officer@gfpd.gov")
        )
        self.conn.commit()
        
        with self.assertRaises(sqlite3.IntegrityError):
            cursor.execute(
                "INSERT INTO lea_agencies (org_name, agency_type, county_slug, county_name, primary_contact_email) VALUES (?, ?, ?, ?, ?)",
                ("Great Falls PD", "police", "cascade", "Cascade", "other@gfpd.gov")
            )
```

**Step 2: Run test to verify failure**

Run: `cd /root/montanablotter && ./venv/bin/python3 -m pytest tests/test_lea_agencies.py::TestLEAAgenciesSchema::test_lea_agencies_table_exists -v`

Expected: FAIL — `NameError: name 'ensure_lea_schema' is not defined`

**Step 3: Write minimal implementation**

Add to `init_db.py` (at the end, before `if __name__ == ...`):

```python
def ensure_lea_schema(conn: sqlite3.Connection) -> None:
    """Create LEA panel tables for multi-tenant agency self-service."""
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS lea_agencies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            org_name TEXT NOT NULL UNIQUE,
            agency_type TEXT NOT NULL,
            county_slug TEXT NOT NULL,
            county_name TEXT NOT NULL,
            ori_number TEXT UNIQUE,
            primary_contact_name TEXT,
            primary_contact_email TEXT NOT NULL,
            primary_contact_phone TEXT,
            agency_website_url TEXT,
            verification_status TEXT DEFAULT 'pending',
            verified_by_user_id INTEGER,
            verified_at TEXT,
            timezone TEXT DEFAULT 'America/Denver',
            enable_blotter_publishing INTEGER DEFAULT 1,
            enable_roster_publishing INTEGER DEFAULT 0,
            enable_api_access INTEGER DEFAULT 0,
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (verified_by_user_id) REFERENCES users(id) ON DELETE SET NULL
        )
    ''')
    
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_lea_agencies_slug ON lea_agencies(county_slug)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_lea_agencies_ori ON lea_agencies(ori_number)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_lea_agencies_status ON lea_agencies(verification_status)')
    
    conn.commit()
```

**Step 4: Run test to verify pass**

Run: `cd /root/montanablotter && ./venv/bin/python3 -m pytest tests/test_lea_agencies.py -v`

Expected: PASS (all 3 tests)

**Step 5: Commit**

```bash
cd /root/montanablotter
git add tests/test_lea_agencies.py init_db.py
git commit -m "feat(lea): add lea_agencies table with ORI verification support"
```

---

### Task 1.2: Create `lea_users` table + RBAC columns
**Objective:** Support per-agency users with role-based access (admin, pio, records_officer).

**Files:**
- Modify: `init_db.py:ensure_lea_schema()`
- Test: `tests/test_lea_agencies.py`

**Step 1: Write failing test**

```python
def test_lea_users_table_exists(self) -> None:
    cursor = self.conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='lea_users'")
    self.assertIsNotNone(cursor.fetchone())

def test_lea_users_role_enforcement(self) -> None:
    cursor = self.conn.cursor()
    # Insert agency first
    cursor.execute(
        "INSERT INTO lea_agencies (org_name, agency_type, county_slug, county_name, primary_contact_email) VALUES (?, ?, ?, ?, ?)",
        ("Test PD", "police", "test", "Test", "admin@test.gov")
    )
    agency_id = cursor.lastrowid
    self.conn.commit()
    
    # Insert user with admin role
    cursor.execute(
        "INSERT INTO lea_users (agency_id, username, email, full_name, password_hash, role) VALUES (?, ?, ?, ?, ?, ?)",
        (agency_id, "admin1", "admin@test.gov", "Admin User", "hashed_pwd", "admin")
    )
    self.conn.commit()
    
    # Verify role saved correctly
    row = cursor.execute("SELECT role FROM lea_users WHERE username = ?", ("admin1",)).fetchone()
    self.assertEqual(row[0], "admin")
```

**Step 2: Run test to verify failure**

Run: `./venv/bin/python3 -m pytest tests/test_lea_agencies.py::TestLEAAgenciesSchema::test_lea_users_table_exists -v`

Expected: FAIL — table does not exist

**Step 3: Write minimal implementation**

Add to `ensure_lea_schema()` in `init_db.py`:

```python
cursor.execute('''
    CREATE TABLE IF NOT EXISTS lea_users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agency_id INTEGER NOT NULL,
        username TEXT NOT NULL,
        email TEXT NOT NULL,
        full_name TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'records_officer',
        is_active INTEGER DEFAULT 1,
        last_login_at TEXT,
        last_login_ip TEXT,
        mfa_enabled INTEGER DEFAULT 0,
        mfa_secret TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now')),
        UNIQUE (agency_id, email),
        FOREIGN KEY (agency_id) REFERENCES lea_agencies(id) ON DELETE CASCADE
    )
''')

cursor.execute('CREATE INDEX IF NOT EXISTS idx_lea_users_agency ON lea_users(agency_id)')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_lea_users_active ON lea_users(agency_id, is_active)')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_lea_users_email ON lea_users(email)')
```

**Step 4: Run tests to verify pass**

Run: `./venv/bin/python3 -m pytest tests/test_lea_agencies.py -v`

Expected: PASS (all 5+ tests)

**Step 5: Commit**

```bash
git add tests/test_lea_agencies.py init_db.py
git commit -m "feat(lea): add lea_users table with role-based access control"
```

---

### Task 1.3: Create `lea_invitations` table
**Objective:** Support pending user invitations (email-based onboarding).

**Files:**
- Modify: `init_db.py:ensure_lea_schema()`
- Test: `tests/test_lea_agencies.py`

**Step 1: Write failing test**

```python
def test_lea_invitations_table_exists(self) -> None:
    cursor = self.conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='lea_invitations'")
    self.assertIsNotNone(cursor.fetchone())

def test_lea_invitations_unique_token(self) -> None:
    import secrets
    cursor = self.conn.cursor()
    
    # Setup: create agency
    cursor.execute(
        "INSERT INTO lea_agencies (org_name, agency_type, county_slug, county_name, primary_contact_email) VALUES (?, ?, ?, ?, ?)",
        ("Test PD", "police", "test", "Test", "admin@test.gov")
    )
    agency_id = cursor.lastrowid
    self.conn.commit()
    
    # Create two invitations with same email but different tokens
    token1 = secrets.token_urlsafe(32)
    token2 = secrets.token_urlsafe(32)
    
    cursor.execute(
        "INSERT INTO lea_invitations (agency_id, email, role, token, expires_at) VALUES (?, ?, ?, ?, ?)",
        (agency_id, "newuser@test.gov", "records_officer", token1, "2026-09-02T00:00:00Z")
    )
    self.conn.commit()
    
    # Both should be allowed (email not unique, token is)
    cursor.execute(
        "INSERT INTO lea_invitations (agency_id, email, role, token, expires_at) VALUES (?, ?, ?, ?, ?)",
        (agency_id, "newuser@test.gov", "pio", token2, "2026-09-02T00:00:00Z")
    )
    self.conn.commit()
    
    # Duplicate token should fail
    with self.assertRaises(sqlite3.IntegrityError):
        cursor.execute(
            "INSERT INTO lea_invitations (agency_id, email, role, token, expires_at) VALUES (?, ?, ?, ?, ?)",
            (agency_id, "another@test.gov", "records_officer", token1, "2026-09-02T00:00:00Z")
        )
```

**Step 2: Run test to verify failure**

Run: `./venv/bin/python3 -m pytest tests/test_lea_agencies.py::TestLEAAgenciesSchema::test_lea_invitations_table_exists -v`

Expected: FAIL

**Step 3: Write minimal implementation**

Add to `ensure_lea_schema()`:

```python
cursor.execute('''
    CREATE TABLE IF NOT EXISTS lea_invitations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agency_id INTEGER NOT NULL,
        email TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'records_officer',
        token TEXT NOT NULL UNIQUE,
        expires_at TEXT NOT NULL,
        accepted_at TEXT,
        invited_by_user_id INTEGER,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (agency_id) REFERENCES lea_agencies(id) ON DELETE CASCADE,
        FOREIGN KEY (invited_by_user_id) REFERENCES lea_users(id) ON DELETE SET NULL
    )
''')

cursor.execute('CREATE INDEX IF NOT EXISTS idx_lea_invitations_token ON lea_invitations(token)')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_lea_invitations_email ON lea_invitations(email, expires_at)')
```

**Step 4: Run tests to verify pass**

Run: `./venv/bin/python3 -m pytest tests/test_lea_agencies.py -v`

Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_lea_agencies.py init_db.py
git commit -m "feat(lea): add lea_invitations table for email-based user onboarding"
```

---

### Task 1.4: Create `lea_blotter_drafts` table + indexing
**Objective:** Staging table for submitted incidents before normalization & publication.

**Files:**
- Modify: `init_db.py:ensure_lea_schema()`
- Test: `tests/test_lea_agencies.py`

(Follow same pattern as 1.1–1.3; write test, run to fail, implement, pass, commit.)

---

### Task 1.5: Create `lea_roster_snapshots` table
**Objective:** Store jail roster snapshots with dedup hash.

(Same pattern; see architecture doc for schema.)

---

### Task 1.6: Create `lea_api_tokens` table
**Objective:** API token storage (hashed) for programmatic integrations.

(Same pattern.)

---

### Task 1.7: Create `lea_audit_log` table
**Objective:** Immutable audit trail for CJIS compliance.

(Same pattern.)

---

### Task 1.8: Call `ensure_lea_schema()` from `migrate()`
**Objective:** Wire LEA schema into the standard database initialization flow.

**Files:**
- Modify: `init_db.py:migrate()`

**Step 1: Write test**

```python
def test_migrate_calls_ensure_lea_schema(self) -> None:
    """Verify that init_db.migrate() creates LEA tables."""
    conn = sqlite3.connect(self.db_path)
    init_db.init_database()
    init_db.migrate()
    conn = sqlite3.connect(self.db_path)
    
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    table_names = {row[0] for row in cursor.fetchall()}
    
    expected_lea_tables = {
        'lea_agencies', 'lea_users', 'lea_invitations', 'lea_blotter_drafts',
        'lea_roster_snapshots', 'lea_api_tokens', 'lea_audit_log', 'lea_agency_coverages'
    }
    self.assertTrue(expected_lea_tables.issubset(table_names))
```

**Step 2: Write implementation**

In `init_db.migrate()`, add before the final `conn.commit()`:

```python
ensure_lea_schema(conn)
```

**Step 3: Run test to verify pass**

Run: `./venv/bin/python3 -m pytest tests/test_lea_agencies.py::TestLEAAgenciesSchema::test_migrate_calls_ensure_lea_schema -v`

Expected: PASS

**Step 4: Commit**

```bash
git add tests/test_lea_agencies.py init_db.py
git commit -m "feat(lea): integrate lea schema into migrate() initialization"
```

---

## Phase 2: Authentication & User Management (Week 2)

### Task 2.1: Create LEA user authentication helpers (password hashing, session)
**Objective:** Implement bcrypt-based password hashing and LEA session management.

**Files:**
- Create: `services/lea_auth/user_auth.py`
- Test: `tests/test_lea_auth.py`

**Step 1: Write failing test**

```python
import unittest
from services.lea_auth.user_auth import hash_password, verify_password

class TestLEAUserAuth(unittest.TestCase):
    def test_hash_password(self) -> None:
        password = "SecurePass123!"
        hashed = hash_password(password)
        self.assertNotEqual(hashed, password)
        self.assertEqual(len(hashed), 60)  # bcrypt hash length

    def test_verify_password_correct(self) -> None:
        password = "SecurePass123!"
        hashed = hash_password(password)
        self.assertTrue(verify_password(password, hashed))

    def test_verify_password_incorrect(self) -> None:
        password = "SecurePass123!"
        hashed = hash_password(password)
        self.assertFalse(verify_password("WrongPassword", hashed))
```

**Step 2: Run test to verify failure**

Run: `./venv/bin/python3 -m pytest tests/test_lea_auth.py::TestLEAUserAuth::test_hash_password -v`

Expected: FAIL — module does not exist

**Step 3: Write minimal implementation**

Create `services/lea_auth/__init__.py` (empty file) and `services/lea_auth/user_auth.py`:

```python
"""LEA user authentication helpers."""

import bcrypt


def hash_password(password: str) -> str:
    """Hash a password using bcrypt (cost=12)."""
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')


def verify_password(password: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
    except Exception:
        return False
```

**Step 4: Run test to verify pass**

Run: `./venv/bin/python3 -m pytest tests/test_lea_auth.py -v`

Expected: PASS (all 3 tests)

**Step 5: Commit**

```bash
git add services/lea_auth/__init__.py services/lea_auth/user_auth.py tests/test_lea_auth.py
git commit -m "feat(lea_auth): add bcrypt password hashing utilities"
```

---

### Task 2.2: Agency verification workflow (ORI lookup + email domain check)
**Objective:** Implement secure agency registration with government domain verification.

**Files:**
- Create: `services/lea_auth/agency_verification.py`
- Test: `tests/test_lea_auth.py`

**Step 1: Write failing test**

```python
from services.lea_auth.agency_verification import (
    verify_email_domain, verify_ori_number
)

def test_verify_email_domain_gov(self) -> None:
    """Verify that .gov emails are allowed."""
    # Whitelist common MT government domains
    self.assertTrue(verify_email_domain("officer@gfpd.gov"))
    self.assertTrue(verify_email_domain("sheriff@cascadecountymt.gov"))
    
def test_verify_email_domain_non_gov_rejected(self) -> None:
    """Verify that non-.gov emails are rejected."""
    self.assertFalse(verify_email_domain("officer@gmail.com"))
    self.assertFalse(verify_email_domain("officer@gfpd.com"))

def test_verify_ori_number_format(self) -> None:
    """ORI numbers should be 9 chars alphanumeric."""
    self.assertTrue(verify_ori_number("MT0120100"))  # Valid format
    self.assertFalse(verify_ori_number("MT012"))     # Too short
    self.assertFalse(verify_ori_number("INVALID!"))  # Invalid chars
```

**Step 2: Run test to verify failure**

Run: `./venv/bin/python3 -m pytest tests/test_lea_auth.py::TestLEAUserAuth::test_verify_email_domain_gov -v`

Expected: FAIL — function not defined

**Step 3: Write minimal implementation**

Create `services/lea_auth/agency_verification.py`:

```python
"""LEA agency verification utilities."""

import re


# Whitelist of Montana government domains
MT_GOVERNMENT_DOMAINS = {
    'gfpd.gov', 'cascadecountymt.gov', 'msoutnews.org',
    'montanaagencyname.gov', 'montanaagency.gov',
    # Add more as discovered during onboarding
}


def verify_email_domain(email: str) -> bool:
    """
    Verify that the email is from a government domain.
    
    Currently supports:
    - *.gov domains (assumed government)
    - Whitelisted Montana agency-specific domains
    
    Returns True if domain is trusted, False otherwise.
    """
    if '@' not in email:
        return False
    
    domain = email.split('@')[1].lower()
    
    # Check whitelist first
    if domain in MT_GOVERNMENT_DOMAINS:
        return True
    
    # Allow any .gov domain (conservative but effective)
    if domain.endswith('.gov'):
        return True
    
    return False


def verify_ori_number(ori: str) -> bool:
    """
    Verify ORI number format (9 chars: 2-char state code + 7 digits).
    
    Format: SSXXXXX (S = state, X = digits)
    Example: MT0120100
    
    Note: We do NOT validate against FBI CJIS database in MVP.
    That requires registration and API credentials.
    """
    if not ori or len(ori) != 9:
        return False
    
    # Must match pattern: 2 letters + 7 digits
    pattern = r'^[A-Z]{2}\d{7}$'
    return bool(re.match(pattern, ori))
```

**Step 4: Run test to verify pass**

Run: `./venv/bin/python3 -m pytest tests/test_lea_auth.py -v`

Expected: PASS

**Step 5: Commit**

```bash
git add services/lea_auth/agency_verification.py tests/test_lea_auth.py
git commit -m "feat(lea_auth): add agency verification (ORI + email domain)"
```

---

### Task 2.3: API token generation & hashing
**Objective:** Generate secure, non-expiring API tokens with scopes.

**Files:**
- Create: `services/lea_auth/api_tokens.py`
- Test: `tests/test_lea_auth.py`

(Follow same pattern; tokens = secrets.token_urlsafe(32), hashed on storage, scopes as JSON.)

---

### Task 2.4: Implement invitation acceptance flow
**Objective:** Users accept email invitations, set password, join agency.

**Files:**
- Create: `services/lea_auth/invitations.py`
- Test: `tests/test_lea_auth.py`

(Pattern: generate token, email link with token, on accept: verify token not expired, create user, delete invitation.)

---

## Phase 3: LEA Panel Routes & Dashboard (Week 3)

### Task 3.1: Create `blueprints/lea_panel.py` with basic routing
**Objective:** Setup the Flask blueprint for agency dashboard.

**Files:**
- Create: `blueprints/lea_panel.py`
- Create: `templates/lea/dashboard.html`
- Test: `tests/test_lea_panel_routes.py`

**Step 1: Write failing test**

```python
import os, sqlite3, tempfile, unittest
import app as app_module
import config, init_db

class TestLEAPanelRoutes(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-lea-panel-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        
        conn = sqlite3.connect(self.db_path)
        init_db.init_database()
        init_db.migrate()
        conn.close()
        
        app_module.app.config['TESTING'] = True
        self.client = app_module.app.test_client()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_lea_dashboard_route_exists(self) -> None:
        """GET /panel/cascade/ should return 200 (if logged in)."""
        response = self.client.get('/panel/cascade/')
        # Not logged in yet, should redirect to login
        self.assertIn(response.status_code, [302, 401])
```

**Step 2: Run test to verify failure**

Run: `./venv/bin/python3 -m pytest tests/test_lea_panel_routes.py::TestLEAPanelRoutes::test_lea_dashboard_route_exists -v`

Expected: FAIL — 404

**Step 3: Write minimal implementation**

Create `blueprints/lea_panel.py`:

```python
"""LEA (Law Enforcement Agency) self-service panel blueprint."""

from flask import Blueprint, render_template, session, redirect, url_for

lea_panel_bp = Blueprint('lea_panel', __name__, url_prefix='/panel')


def register_lea_panel(app) -> None:
    """Register the LEA panel blueprint."""
    app.register_blueprint(lea_panel_bp)


@lea_panel_bp.route('/<county_slug>/')
def dashboard(county_slug):
    """Agency dashboard home."""
    # TODO: Verify user is logged in and belongs to this county's agency
    return render_template('lea/dashboard.html', county_slug=county_slug)
```

Create `templates/lea/dashboard.html`:

```html
{% extends "public_page_base.html" %}

{% block title %}Agency Dashboard — Montana Blotter{% endblock %}

{% block content %}
<div class="lea-dashboard">
    <h1>Agency Dashboard</h1>
    <p>County: {{ county_slug }}</p>
</div>
{% endblock %}
```

In `app.py`, add near the bottom (before `if __name__ == ...`):

```python
from blueprints.lea_panel import register_lea_panel
register_lea_panel(app)
```

**Step 4: Run test to verify pass**

Run: `./venv/bin/python3 -m pytest tests/test_lea_panel_routes.py -v`

Expected: PASS

**Step 5: Commit**

```bash
git add blueprints/lea_panel.py templates/lea/dashboard.html tests/test_lea_panel_routes.py
git commit -m "feat(lea_panel): add basic agency dashboard blueprint and routing"
```

---

### Task 3.2: Implement incident submission form (single entry)
**Objective:** Form for officers to submit one incident at a time.

**Files:**
- Modify: `templates/lea/dashboard.html`
- Create: `templates/lea/submit_incident.html`
- Modify: `blueprints/lea_panel.py`
- Test: `tests/test_lea_panel_routes.py`

(Pattern: GET shows form, POST validates and inserts into lea_blotter_drafts, redirect to confirmation.)

---

### Task 3.3: Implement batch CSV upload
**Objective:** Upload multiple incidents via CSV file.

**Files:**
- Modify: `blueprints/lea_panel.py`
- Create: `services/lea_ingestion/csv_parser.py`
- Test: `tests/test_lea_panel_routes.py`, `tests/test_lea_ingestion.py`

(Pattern: multipart form, parse CSV, validate rows, bulk insert into lea_blotter_drafts, return summary.)

---

### Task 3.4: Implement incident history & filtering
**Objective:** Show submitted incidents with search, filter, edit, delete.

**Files:**
- Modify: `templates/lea/dashboard.html`
- Create: `templates/lea/blotter_history.html`
- Modify: `blueprints/lea_panel.py`
- Test: `tests/test_lea_panel_routes.py`

---

## Phase 4: REST API (`/api/v1/lea/...`)

### Task 4.1: Create API blueprint + authentication
**Objective:** Setup `/api/v1/lea/` endpoints with token-based auth.

**Files:**
- Create: `blueprints/api_lea.py`
- Create: `services/api/lea_auth.py`
- Test: `tests/test_api_lea.py`

(Pattern: middleware that validates `Authorization: Bearer <token>` header, loads user from token_hash, enforces agency RLS.)

---

### Task 4.2: Implement `POST /api/v1/lea/blotter/publish`
**Objective:** Single incident submission via API.

(See Phase 3, Task 3.2; adapt to JSON request/response.)

---

### Task 4.3: Implement `POST /api/v1/lea/blotter/batch`
**Objective:** Batch upload via API (JSON format).

---

### Task 4.4: Implement `POST /api/v1/lea/roster/sync`
**Objective:** Jail roster sync endpoint.

---

### Task 4.5: Implement `GET /api/v1/lea/audit`
**Objective:** Read-only audit log retrieval.

---

## Phase 5: Ingestion Workers (Cron)

### Task 5.1: Implement `poll_lea_panel.py` worker
**Objective:** Fetch new drafts/rosters every 15 minutes, queue for normalization.

**Files:**
- Create: `services/ingestion/poll_lea_panel.py`
- Create: `tests/test_poll_lea_panel.py`

---

### Task 5.2: Implement `normalize_records.py` worker
**Objective:** Validate, geocode, PII-check, then publish to `records` table.

**Files:**
- Create: `services/ingestion/normalize_lea_records.py`
- Create: `tests/test_normalize_lea_records.py`

---

### Task 5.3: Implement `ingest_lea_rosters.py` worker
**Objective:** Convert roster snapshots to jail_bookings.

**Files:**
- Create: `services/ingestion/ingest_lea_rosters.py`
- Create: `tests/test_ingest_lea_rosters.py`

---

## Phase 6: Admin Console

### Task 6.1: Create `/admin/lea-management` dashboard
**Objective:** Admin overview of all agencies.

**Files:**
- Create: `blueprints/admin/lea_management.py`
- Create: `templates/admin/lea_management.html`
- Test: `tests/test_admin_lea.py`

---

### Task 6.2: Agency onboarding workflow
**Objective:** Verify pending agencies via ORI + email.

(Pattern: list pending, verify button triggers ORI check, email confirmation.)

---

## Phase 7: Testing & Deployment (Week 7)

### Task 7.1: Full pytest coverage
**Objective:** >80% coverage on all LEA modules.

---

### Task 7.2: E2E smoke tests (Playwright)
**Objective:** Agency signup → submission → public publication.

---

### Task 7.3: Pilot with 3–5 agencies
**Objective:** Real-world testing before launch.

---

### Task 7.4: Deploy to production
**Objective:** Add to crontab, test integration, go live.

---

## Execution Notes

- Each task should take 1–2 hours (2–5 minute focused work steps).
- Always run the full test suite after each task to catch regressions.
- Commit frequently (after each task).
- Use subagent-driven-development for parallel task execution (Phase 2 tasks can run in parallel).

---

**Status:** DRAFT — Ready for Phase 1 task-by-task implementation.

**Last updated:** 2026-08-02
