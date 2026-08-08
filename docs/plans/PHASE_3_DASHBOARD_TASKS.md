# Phase 3: Agency Dashboard UI (Week 2, Day 2–3)

**Objective:** Build the web interface for agencies to submit incidents, upload batch CSVs, view history, and manage API keys.

**Prerequisites:** Phase 1 ✓ (schema), Phase 2 ✓ (auth)

**Tech Stack:**
- Jinja2 templates
- Vanilla JavaScript (no build tool)
- CSS (extend `static/public-redesign.css`)
- Flask blueprint routing

**Files to Create/Modify:**
- `blueprints/lea_panel.py` — Agency dashboard routes (new blueprint)
- `templates/lea/dashboard.html` — Agency home
- `templates/lea/submit_incident.html` — Single incident form
- `templates/lea/batch_upload.html` — CSV upload + preview
- `templates/lea/blotter_history.html` — Submission history with filters
- `templates/lea/api_keys.html` — API key management
- `templates/lea/team_management.html` — User invite/role management
- `static/lea/dashboard.css` — Dashboard styling
- `tests/test_lea_panel_routes.py` — Full route test suite

---

## Task 3.1: Agency Dashboard Home

**Objective:** Create the main dashboard landing page for logged-in users.

### Step 1: Write Failing Test

```python
# tests/test_lea_panel_routes.py
import os, sqlite3, tempfile, unittest
from flask import Flask
import app as app_module
import config, init_db
from services.lea_auth import user_auth, agency_verification

class TestLEAPanelRoutes(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-lea-panel-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        
        conn = sqlite3.connect(self.db_path)
        init_db.init_database(conn)
        init_db.ensure_lea_schema(conn)
        self.conn = conn
        
        # Create Flask test client
        app_module.app.config['TESTING'] = True
        self.client = app_module.app.test_client()
        
        # Insert test agency and user
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO lea_agencies (org_name, agency_type, county_slug, county_name, primary_contact_email, verification_status) VALUES (?, ?, ?, ?, ?, ?)",
            ("Test PD", "police", "test", "Test", "admin@testpd.gov", "verified")
        )
        self.agency_id = cursor.lastrowid
        
        pwd_hash = user_auth.hash_password("TestPass123!")
        cursor.execute(
            "INSERT INTO lea_users (agency_id, username, email, full_name, password_hash, role) VALUES (?, ?, ?, ?, ?, ?)",
            (self.agency_id, "testuser", "admin@testpd.gov", "Test User", pwd_hash, "admin")
        )
        self.user_id = cursor.lastrowid
        conn.commit()
    
    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
    
    def test_dashboard_unauthenticated_redirects(self) -> None:
        """Test unauthenticated access redirects to login."""
        response = self.client.get('/lea/dashboard')
        self.assertEqual(response.status_code, 302)  # Redirect
    
    def test_dashboard_authenticated_loads(self) -> None:
        """Test authenticated dashboard loads successfully."""
        # Login first
        self.client.post('/lea/login', data={
            'username': 'testuser',
            'password': 'TestPass123!'
        }, follow_redirects=True)
        
        # Access dashboard
        response = self.client.get('/lea/dashboard')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Test PD', response.data)  # Agency name visible
```

### Step 2–5: Implementation

Create `blueprints/lea_panel.py`:

```python
"""LEA Agency Panel — dashboard, incident submission, batch upload."""
from flask import Blueprint, render_template, request, redirect, session, jsonify, current_app
from functools import wraps
import sqlite3
from services.lea_auth import user_auth

lea_panel = Blueprint('lea_panel', __name__, url_prefix='/lea')


def login_required_lea(f):
    """Decorator to require LEA user login."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'lea_user_id' not in session:
            return redirect('/lea/login')
        return f(*args, **kwargs)
    return decorated_function


@lea_panel.route('/login', methods=['GET', 'POST'])
def login():
    """Login page and handler."""
    if request.method == 'GET':
        return render_template('lea/login.html')
    
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    
    conn = sqlite3.connect(current_app.config['DATABASE'])
    cursor = conn.cursor()
    
    # Look up user
    row = cursor.execute(
        'SELECT id, agency_id, password_hash FROM lea_users WHERE username = ? AND is_active = 1',
        (username,)
    ).fetchone()
    
    if not row:
        return render_template('lea/login.html', error='Invalid username or password'), 401
    
    user_id, agency_id, pwd_hash = row
    
    if not user_auth.verify_password(password, pwd_hash):
        return render_template('lea/login.html', error='Invalid username or password'), 401
    
    # Update last login
    conn.execute(
        'UPDATE lea_users SET last_login_at = datetime(now) WHERE id = ?',
        (user_id,)
    )
    conn.commit()
    conn.close()
    
    # Set session
    session['lea_user_id'] = user_id
    session['lea_agency_id'] = agency_id
    
    return redirect('/lea/dashboard')


@lea_panel.route('/logout')
def logout():
    """Logout handler."""
    session.pop('lea_user_id', None)
    session.pop('lea_agency_id', None)
    return redirect('/lea/login')


@lea_panel.route('/dashboard')
@login_required_lea
def dashboard():
    """Main dashboard landing page."""
    agency_id = session.get('lea_agency_id')
    user_id = session.get('lea_user_id')
    
    conn = sqlite3.connect(current_app.config['DATABASE'])
    
    # Get agency info
    agency = conn.execute(
        'SELECT org_name, agency_type, county_name FROM lea_agencies WHERE id = ?',
        (agency_id,)
    ).fetchone()
    
    # Get user info
    user = conn.execute(
        'SELECT full_name, role, email FROM lea_users WHERE id = ?',
        (user_id,)
    ).fetchone()
    
    # Get submission stats
    submission_count = conn.execute(
        'SELECT COUNT(*) FROM lea_blotter_drafts WHERE agency_id = ?',
        (agency_id,)
    ).fetchone()[0]
    
    # Get recent submissions
    recent = conn.execute('''
        SELECT id, incident_date, location, status, created_at
        FROM lea_blotter_drafts
        WHERE agency_id = ?
        ORDER BY created_at DESC
        LIMIT 5
    ''', (agency_id,)).fetchall()
    
    conn.close()
    
    return render_template('lea/dashboard.html', 
        agency=agency,
        user=user,
        submission_count=submission_count,
        recent_submissions=recent
    )
```

Create `templates/lea/dashboard.html`:

```html
{% extends "lea/base.html" %}

{% block title %}Dashboard — {{ agency[0] }}{% endblock %}

{% block content %}
<div class="lea-dashboard">
    <header class="dashboard-header">
        <div class="header-content">
            <h1>{{ agency[0] }}</h1>
            <p class="badge badge-{{ agency[1] }}">{{ agency[1] | upper }}</p>
        </div>
        <nav class="dashboard-nav">
            <a href="/lea/submit" class="btn btn-primary">Submit Incident</a>
            <a href="/lea/batch-upload" class="btn btn-secondary">Batch Upload</a>
            <a href="/lea/api-keys" class="btn btn-secondary">API Keys</a>
            <a href="/lea/team" class="btn btn-secondary">Team</a>
        </nav>
    </header>
    
    <section class="dashboard-stats">
        <div class="stat-card">
            <h3>{{ submission_count }}</h3>
            <p>Total Submissions</p>
        </div>
        <div class="stat-card">
            <h3>{{ recent_submissions | length }}</h3>
            <p>This Month</p>
        </div>
    </section>
    
    <section class="dashboard-recent">
        <h2>Recent Submissions</h2>
        <table class="submissions-table">
            <thead>
                <tr>
                    <th>Date</th>
                    <th>Location</th>
                    <th>Status</th>
                    <th>Submitted</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody>
                {% for sub in recent_submissions %}
                <tr>
                    <td>{{ sub[1] }}</td>
                    <td>{{ sub[2] }}</td>
                    <td><span class="badge badge-{{ sub[3] }}">{{ sub[3] }}</span></td>
                    <td>{{ sub[4] | strftime('%Y-%m-%d %H:%M') }}</td>
                    <td>
                        <a href="/lea/submission/{{ sub[0] }}" class="link">View</a>
                        {% if sub[3] == 'draft' %}
                        <a href="/lea/submission/{{ sub[0] }}/edit" class="link">Edit</a>
                        {% endif %}
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </section>
</div>
{% endblock %}
```

### Commit

```bash
git add blueprints/lea_panel.py templates/lea/ tests/test_lea_panel_routes.py
git commit -m "feat(lea): add agency dashboard with login and home view"
```

---

## Task 3.2: Submit Single Incident Form

**Objective:** Create form for officers to submit one incident at a time.

### Implementation Outline

1. **Route:** `/lea/submit` (GET = form, POST = save draft)
2. **Form Fields:**
   - Incident date (date picker)
   - Incident time (time picker)
   - CAD # (free text)
   - Location (address autocomplete → geocoding API)
   - Charges (multi-select, MCA code lookup)
   - Narrative (free text, 5000 char limit)
   - Suspect name (optional)
   - Suspect DOB (optional, for internal use)
3. **Validation:**
   - Required fields present
   - Date not in future
   - Narrative under 5000 chars
   - At least one charge selected
4. **On Submit:**
   - Save to `lea_blotter_drafts` with `status='draft'`
   - Show confirmation + link to view/edit
5. **Test:** Form loads, validation passes/fails, draft saves to DB

---

## Task 3.3: Batch CSV Upload & Preview

**Objective:** Upload CSV/JSON with multiple incidents, preview before publish.

### Implementation Outline

1. **Route:** `/lea/batch-upload` (GET = form, POST = parse + preview)
2. **File Format (CSV):**
   ```
   incident_date,incident_time,cad_number,location,charges,narrative
   2026-08-02,14:30,2026-1234,300 BLK MAIN ST,"45-5-202,45-5-206","Assault, disorderly conduct"
   ```
3. **Processing:**
   - Parse CSV/JSON (detect format)
   - Validate each row
   - Show preview table (date, location, charges, errors)
   - Show "Publish All" or "Discard"
4. **Test:** CSV parsing, validation errors, batch insert to drafts

---

## Task 3.4: Blotter History & Filtering

**Objective:** Show all submissions (past and present) with filters.

### Implementation Outline

1. **Route:** `/lea/history` (GET with optional filters)
2. **Columns:** ID, Date, Location, Status (draft/approved/published), Submitted, Actions
3. **Filters:**
   - Date range
   - Status (all, draft, approved, published)
   - Location (free text search)
4. **Actions:** View, Edit (if draft), Delete (if draft), Publish (if approved)
5. **Test:** Filter logic, pagination, deletion

---

## Task 3.5: API Key Management

**Objective:** Create, view, revoke API tokens.

### Implementation Outline

1. **Route:** `/lea/api-keys` (GET, POST create, POST revoke)
2. **Display:** Table of active tokens (masked, show only first 10 chars)
3. **Create:** Generate token, show once (can't retrieve later), copy-to-clipboard
4. **Revoke:** Mark inactive immediately
5. **Test:** Token generation, revocation, auth middleware

---

## Task 3.6: Team Management

**Objective:** Invite users, assign roles, deactivate accounts.

### Implementation Outline

1. **Route:** `/lea/team` (GET list, POST invite)
2. **List:** Current users, their roles, last login, deactivate button
3. **Invite:** Email, role (admin/pio/records_officer), send email with link
4. **Email Link:** `/lea/accept-invite?token=<token>` → creates user account
5. **Test:** Invite creation, link validation, expiry

---

## Task 3.7: Dashboard Styling & Responsive Design

**Objective:** CSS for all dashboard pages (mobile-first, Tailwind-adjacent).

### Implementation Outline

1. **File:** `static/lea/dashboard.css`
2. **Requirements:**
   - Mobile-first responsive layout
   - Dark/light mode support (if montanablotter supports)
   - Form input styling (consistent with public site)
   - Table styling (alternating rows, hover effects)
   - Status badges (color-coded: draft=blue, approved=yellow, published=green)
3. **Test:** Visual regression (screenshot comparison is optional; focus on layout)

---

## Ready for Phase 3 Dispatch

Once Phase 2 finishes, the **Frontend Engineer subagent** will receive:
- All task specs + test templates
- Jinja2 template stubs
- CSS baseline
- Expected pytest output

Each task: **TEST FAIL → IMPLEMENT → TEST PASS → COMMIT**
