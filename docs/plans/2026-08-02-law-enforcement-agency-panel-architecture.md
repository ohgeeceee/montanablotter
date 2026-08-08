# Montana Blotter — Law Enforcement Agency Self-Service Panel (LEA Panel)

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Provide all Montana sheriff offices, police departments, and county detention facilities with a zero-cost, frictionless web panel to manage and publish daily police blotters and jail rosters, which automatically feed into montanablotter.com.

**Architecture:** Additive layer on the existing Flask monolith. New multi-tenant database tables (`lea_agencies`, `lea_users`, `lea_invitations`), new blueprints (`blueprints/lea_panel.py`), new REST API endpoints (`/api/v1/lea/*`), new admin dashboard (`templates/admin/lea_management.html`), and new public ingestion workers that poll the agency panel for new records.

**Tech Stack:** Python 3.12 + Flask + SQLite (existing monolith), RBAC via user roles, secure agency verification (ORI number + gov domain email), JWT API tokens for programmatic integrations, immutable audit logging, row-level security on all agency data.

---

## System Architecture Overview

```
┌────────────────────────────────────────────────────────────────────┐
│                      MontanaBlotter.com (Public)                   │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ Public-facing pages:                                         │  │
│  │  - /jail-rosters / /jail-bookings/<county_slug>             │  │
│  │  - /blotters / /incidents / search & feed APIs              │  │
│  │  - RSS/JSON feeds for media syndication                     │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                ▲                                     │
│                                │ (consumes published records)        │
└────────────────────────────────┼─────────────────────────────────────┘
                                 │
                    ┌────────────┴──────────────┐
                    │   Ingestion Workers       │
                    │  (cron-scheduled)         │
                    │                           │
                    │ - poll_lea_panel.py       │
                    │ - normalize_records.py    │
                    │ - dedup_and_stage.py      │
                    └────────────┬──────────────┘
                                 │
                    ┌────────────▼──────────────┐
                    │   LEA Panel Backend       │
                    │  (Flask app, this file)   │
                    │                           │
│  ┌────────────────────────────────────────────┬────────────────────┐ │
│  │  Admin Console                             │  Agency Dashboard  │ │
│  │  (/admin/lea-management)                   │  (/panel/<org_id>) │ │
│  │                                             │                    │ │
│  │ - Agency onboarding & verification         │  - Manual entry    │ │
│  │ - User + role management                   │  - Batch upload    │ │
│  │ - Source health dashboard                  │  - Report gen      │ │
│  │ - Audit log viewer                         │  - Roster sync     │ │
│  │ - Bulk configuration                       │  - API keys        │ │
│  └────────────────────────────────────────────┴────────────────────┘ │
│                                                                       │
│  REST API Layer                                                      │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ /api/v1/lea/auth/              (token generation)            │  │
│  │ /api/v1/lea/blotter/           (publish single incident)     │  │
│  │ /api/v1/lea/blotter/batch      (batch upload CSV/JSON)       │  │
│  │ /api/v1/lea/roster/            (sync jail roster updates)    │  │
│  │ /api/v1/lea/roster/snapshot    (full roster export)          │  │
│  │ /api/v1/lea/audit/             (read-only audit log)         │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                       │
│  Database                                                            │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ lea_agencies         - org metadata, ORI, contact info       │  │
│  │ lea_users            - roles, email, agency affiliation      │  │
│  │ lea_invitations      - pending user on-boarding              │  │
│  │ lea_api_tokens       - programmatic access (expired)         │  │
│  │ lea_blotter_drafts   - staging area for submitted records    │  │
│  │ lea_roster_snapshots - historical jail roster states         │  │
│  │ lea_audit_log        - immutable action log (CJIS compliant) │  │
│  └──────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────┘
```

---

## 1. Database Schema

### Core Tables

#### `lea_agencies`
Represents a single registered agency (sheriff, police dept, detention center).

```sql
CREATE TABLE IF NOT EXISTS lea_agencies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- Agency identity
    org_name TEXT NOT NULL UNIQUE,              -- "Great Falls Police Department"
    agency_type TEXT NOT NULL,                  -- "police", "sheriff", "detention"
    county_slug TEXT NOT NULL,                  -- "cascade", "lewis-and-clark"
    county_name TEXT NOT NULL,                  -- "Cascade", "Lewis and Clark"
    ori_number TEXT UNIQUE,                     -- Optional ORI; verified via FBI CJIS
    
    -- Contact & location
    primary_contact_name TEXT,
    primary_contact_email TEXT NOT NULL,        -- Gov domain required
    primary_contact_phone TEXT,
    agency_website_url TEXT,
    
    -- Verification & onboarding
    verification_status TEXT DEFAULT 'pending', -- pending, verified, suspended, inactive
    verified_by_user_id INTEGER,                -- Admin user who approved
    verified_at TEXT,                           -- Timestamp of verification
    
    -- Configuration
    timezone TEXT DEFAULT 'America/Denver',
    enable_blotter_publishing INTEGER DEFAULT 1,
    enable_roster_publishing INTEGER DEFAULT 0,
    enable_api_access INTEGER DEFAULT 0,
    
    -- Metadata
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    
    FOREIGN KEY (verified_by_user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_lea_agencies_slug ON lea_agencies(county_slug);
CREATE INDEX IF NOT EXISTS idx_lea_agencies_ori ON lea_agencies(ori_number);
CREATE INDEX IF NOT EXISTS idx_lea_agencies_status ON lea_agencies(verification_status);
```

#### `lea_users`
Users within an agency with role-based access control.

```sql
CREATE TABLE IF NOT EXISTS lea_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- Links to agency
    agency_id INTEGER NOT NULL,
    
    -- User identity
    username TEXT NOT NULL,
    email TEXT NOT NULL,
    full_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    
    -- Role-based access
    role TEXT NOT NULL DEFAULT 'records_officer',  -- admin, pio (public info), records_officer
    
    -- Session management
    is_active INTEGER DEFAULT 1,
    last_login_at TEXT,
    last_login_ip TEXT,
    
    -- MFA (optional)
    mfa_enabled INTEGER DEFAULT 0,
    mfa_secret TEXT,
    
    -- Metadata
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    
    UNIQUE (agency_id, email),
    FOREIGN KEY (agency_id) REFERENCES lea_agencies(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_lea_users_agency ON lea_users(agency_id);
CREATE INDEX IF NOT EXISTS idx_lea_users_active ON lea_users(agency_id, is_active);
CREATE INDEX IF NOT EXISTS idx_lea_users_email ON lea_users(email);
```

#### `lea_invitations`
Pending invitations for new users.

```sql
CREATE TABLE IF NOT EXISTS lea_invitations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    agency_id INTEGER NOT NULL,
    email TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'records_officer',
    
    -- Invitation token & expiry
    token TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    accepted_at TEXT,
    
    -- Metadata
    invited_by_user_id INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    
    FOREIGN KEY (agency_id) REFERENCES lea_agencies(id) ON DELETE CASCADE,
    FOREIGN KEY (invited_by_user_id) REFERENCES lea_users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_lea_invitations_token ON lea_invitations(token);
CREATE INDEX IF NOT EXISTS idx_lea_invitations_email ON lea_invitations(email, expires_at);
```

#### `lea_api_tokens`
For programmatic access (CAD/RMS integrations, third-party APIs).

```sql
CREATE TABLE IF NOT EXISTS lea_api_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    agency_id INTEGER NOT NULL,
    user_id INTEGER,
    
    -- Token identity
    token_name TEXT NOT NULL,                   -- "CAD Sync", "RMS Exporter"
    token_hash TEXT NOT NULL UNIQUE,            -- SHA256(token)
    token_created_from_ip TEXT,
    
    -- Scope & permissions
    scopes TEXT NOT NULL,                       -- JSON: ["blotter.publish", "roster.read"]
    
    -- Lifecycle
    last_used_at TEXT,
    expires_at TEXT,
    is_revoked INTEGER DEFAULT 0,
    
    -- Metadata
    created_at TEXT DEFAULT (datetime('now')),
    
    FOREIGN KEY (agency_id) REFERENCES lea_agencies(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES lea_users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_lea_api_tokens_hash ON lea_api_tokens(token_hash);
CREATE INDEX IF NOT EXISTS idx_lea_api_tokens_agency ON lea_api_tokens(agency_id, is_revoked);
```

#### `lea_blotter_drafts`
Staging area for submitted blotter entries before ingestion normalization.

```sql
CREATE TABLE IF NOT EXISTS lea_blotter_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- Agency & source
    agency_id INTEGER NOT NULL,
    submitted_by_user_id INTEGER NOT NULL,
    
    -- Incident metadata
    incident_date TEXT NOT NULL,               -- YYYY-MM-DD
    incident_time TEXT,                        -- HH:MM:SS
    cad_number TEXT,
    case_number TEXT,
    
    -- Incident details
    primary_offense_mca TEXT,                   -- "45-5-202" (Assault)
    charges_json TEXT,                          -- [{"mca": "45-5-202", "degree": "felony", "description": ""}]
    incident_location_block TEXT,               -- "300 BLK CENTRAL AVE"
    incident_location_latitude REAL,
    incident_location_longitude REAL,
    
    -- Narrative & summary
    public_narrative TEXT,
    arresting_agency TEXT,
    responding_officer TEXT,
    
    -- Lifecycle & visibility
    submission_status TEXT DEFAULT 'draft',    -- draft, submitted, approved, rejected, published
    published_at TEXT,
    
    -- Audit trail
    raw_json TEXT,                              -- Full submitted JSON payload
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    
    FOREIGN KEY (agency_id) REFERENCES lea_agencies(id) ON DELETE CASCADE,
    FOREIGN KEY (submitted_by_user_id) REFERENCES lea_users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_lea_blotter_incident_date ON lea_blotter_drafts(agency_id, incident_date);
CREATE INDEX IF NOT EXISTS idx_lea_blotter_status ON lea_blotter_drafts(agency_id, submission_status);
```

#### `lea_roster_snapshots`
Snapshot history for jail rosters (supports dedup & change tracking).

```sql
CREATE TABLE IF NOT EXISTS lea_roster_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- Source
    agency_id INTEGER NOT NULL,
    submitted_by_user_id INTEGER,
    
    -- Snapshot metadata
    snapshot_date TEXT NOT NULL,                -- Date the roster was taken
    sync_type TEXT DEFAULT 'incremental',       -- full or incremental
    
    -- Roster state
    roster_json TEXT NOT NULL,                  -- Array of inmate records
    total_inmates INTEGER DEFAULT 0,
    hash_checksum TEXT,                         -- SHA256 of roster_json for dedup
    
    -- Processing
    ingestion_status TEXT DEFAULT 'staged',    -- staged, processing, published, rejected
    ingestion_error TEXT,
    published_at TEXT,
    
    -- Metadata
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    
    FOREIGN KEY (agency_id) REFERENCES lea_agencies(id) ON DELETE CASCADE,
    FOREIGN KEY (submitted_by_user_id) REFERENCES lea_users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_lea_roster_agency_date ON lea_roster_snapshots(agency_id, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_lea_roster_hash ON lea_roster_snapshots(hash_checksum);
CREATE INDEX IF NOT EXISTS idx_lea_roster_status ON lea_roster_snapshots(agency_id, ingestion_status);
```

#### `lea_audit_log`
Immutable action log for compliance (CJIS/LE audits).

```sql
CREATE TABLE IF NOT EXISTS lea_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- Actor
    agency_id INTEGER NOT NULL,
    user_id INTEGER,
    actor_ip TEXT,
    
    -- Action
    action TEXT NOT NULL,                      -- "blotter.submit", "roster.sync", "api_token.create", etc.
    resource_type TEXT,                        -- "blotter", "roster", "user", "agency"
    resource_id TEXT,
    
    -- Details
    change_summary TEXT,                        -- Human-readable: "Updated incident narrative"
    previous_state_json TEXT,
    new_state_json TEXT,
    
    -- Metadata
    timestamp TEXT DEFAULT (datetime('now')),
    
    FOREIGN KEY (agency_id) REFERENCES lea_agencies(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES lea_users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_lea_audit_timestamp ON lea_audit_log(agency_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_lea_audit_action ON lea_audit_log(action);
CREATE INDEX IF NOT EXISTS idx_lea_audit_resource ON lea_audit_log(resource_type, resource_id);
```

#### `lea_agency_coverages`
Tracks coverage options and capabilities per agency (for admin dashboard).

```sql
CREATE TABLE IF NOT EXISTS lea_agency_coverages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    agency_id INTEGER NOT NULL UNIQUE,
    
    -- Coverage tiers
    blotter_coverage_tier TEXT DEFAULT 'standard',  -- off, standard, premium
    roster_coverage_tier TEXT DEFAULT 'off',        -- off, daily, realtime
    
    -- Feature flags
    supports_cad_export INTEGER DEFAULT 0,
    supports_rms_export INTEGER DEFAULT 0,
    supports_api_batch_upload INTEGER DEFAULT 0,
    
    -- Metadata
    updated_at TEXT DEFAULT (datetime('now')),
    
    FOREIGN KEY (agency_id) REFERENCES lea_agencies(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_lea_agency_coverages_tier ON lea_agency_coverages(blotter_coverage_tier);
```

---

## 2. REST API Specification

### Authentication

All authenticated endpoints require either:
1. **Session-based** (web UI): Flask session cookie with `_user_id` + `_csrf_token`.
2. **Token-based** (programmatic): `Authorization: Bearer <api_token>` header.

#### `POST /api/v1/lea/auth/token`
Generate a short-lived JWT for programmatic access.

**Request:**
```json
{
  "grant_type": "password",
  "username": "officer@greatfallspd.gov",
  "password": "secure_password"
}
```

**Response:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

---

### Blotter Publishing

#### `POST /api/v1/lea/blotter/publish`
Submit a single incident for publication.

**Request:**
```json
{
  "incident_date": "2026-08-02",
  "incident_time": "14:35",
  "cad_number": "2026-001234",
  "case_number": "GF-2026-45678",
  "primary_offense_mca": "45-5-202",
  "charges": [
    {
      "mca": "45-5-202",
      "statute_name": "Assault on another",
      "degree": "felony",
      "description": "Assault with a weapon"
    }
  ],
  "incident_location_block": "300 BLK CENTRAL AVE",
  "incident_location_coordinates": {
    "latitude": 47.4975,
    "longitude": -111.2965
  },
  "public_narrative": "Officers responded to a report of an assault. Upon arrival, they located a suspect and made an arrest.",
  "arresting_agency": "Great Falls Police Department",
  "responding_officer": "Officer Smith"
}
```

**Response (201 Created):**
```json
{
  "draft_id": 12345,
  "status": "draft",
  "message": "Incident saved to drafts. Await publication.",
  "review_url": "https://montanablotter.com/panel/cascade/blotter/12345"
}
```

---

#### `POST /api/v1/lea/blotter/batch`
Upload a batch of incidents (CSV, JSON, or PDF-extracted).

**Request (Multipart Form):**
```
POST /api/v1/lea/blotter/batch
Content-Type: multipart/form-data

- file: <uploaded_file.csv>
- format: "csv" | "json" | "pdf"
- date_submitted: "2026-08-02"
```

**CSV Format:**
```
incident_date,incident_time,cad_number,primary_offense_mca,incident_location_block,public_narrative,arresting_agency
2026-08-02,14:35,2026-001234,45-5-202,300 BLK CENTRAL AVE,Officers responded to assault report...,Great Falls Police Department
2026-08-02,15:00,2026-001235,43-5-1005,RIVER RD W,Traffic violation stop...,Great Falls Police Department
```

**Response (202 Accepted):**
```json
{
  "batch_id": "batch_98765",
  "status": "processing",
  "records_queued": 42,
  "message": "Batch received. Processing will complete in ~5 minutes.",
  "status_url": "https://montanablotter.com/api/v1/lea/blotter/batch/batch_98765/status"
}
```

---

#### `GET /api/v1/lea/blotter/batch/<batch_id>/status`
Poll for batch processing status.

**Response:**
```json
{
  "batch_id": "batch_98765",
  "status": "processing",
  "total_records": 42,
  "processed": 35,
  "succeeded": 33,
  "failed": 2,
  "failures": [
    {
      "row_index": 15,
      "error": "primary_offense_mca '99-99-999' not found in MCA code list"
    }
  ],
  "completed_at": null
}
```

---

### Jail Roster Management

#### `POST /api/v1/lea/roster/sync`
Sync incremental jail roster updates (WebHook-friendly).

**Request:**
```json
{
  "sync_type": "incremental",
  "updates": [
    {
      "booking_id": "HCC-202600123",
      "inmate_name": "SMITH, JOHN MICHAEL",
      "dob": "1985-06-15",
      "booking_date": "2026-08-01T14:35:00Z",
      "release_date": null,
      "charges": ["45-5-202"],
      "bail_amount": 5000.00,
      "facility": "Hill County Detention Center",
      "status": "current"
    },
    {
      "booking_id": "HCC-202600122",
      "inmate_name": "JONES, MARY ANNE",
      "dob": "1990-03-22",
      "booking_date": "2026-07-31T09:15:00Z",
      "release_date": "2026-08-02T08:00:00Z",
      "charges": ["61-8-412"],
      "bail_amount": 1000.00,
      "facility": "Hill County Detention Center",
      "status": "released"
    }
  ]
}
```

**Response (202 Accepted):**
```json
{
  "sync_id": "sync_54321",
  "status": "queued",
  "records_received": 2,
  "estimated_process_time_seconds": 60,
  "status_url": "https://montanablotter.com/api/v1/lea/roster/sync/sync_54321/status"
}
```

---

#### `GET /api/v1/lea/roster/snapshot`
Export the current jail roster as JSON or CSV.

**Request:**
```
GET /api/v1/lea/roster/snapshot?format=json&status=current
```

**Response:**
```json
{
  "facility": "Hill County Detention Center",
  "snapshot_date": "2026-08-02T10:30:00Z",
  "total_inmates": 47,
  "inmates": [
    {
      "booking_id": "HCC-202600123",
      "name": "SMITH, JOHN MICHAEL",
      "dob": "1985-06-15",
      "booking_at": "2026-08-01T14:35:00Z",
      "charges": ["45-5-202"],
      "status": "current"
    }
  ]
}
```

---

### Audit & Administrative

#### `GET /api/v1/lea/audit?action=<action>&days=30`
Retrieve audit log (read-only).

**Response:**
```json
{
  "agency_id": 12,
  "logs": [
    {
      "id": 456,
      "timestamp": "2026-08-02T14:35:00Z",
      "user_email": "officer@greatfallspd.gov",
      "action": "blotter.submit",
      "resource_type": "blotter",
      "resource_id": "12345",
      "change_summary": "Submitted incident GF-2026-45678"
    }
  ]
}
```

---

## 3. Agency Dashboard UI/UX Flow

### Dashboard Sections

#### 1. **Home / Quick Stats**
- Agency name, verification status
- "Today's Blotter" counter
- "Current Inmates" counter (if roster enabled)
- Action buttons: "Submit Incident", "Upload Batch", "Sync Roster", "View Reports"

#### 2. **Blotter Management**
- **New Incident** form (auto-fills agency, user, timestamp)
  - Date/time picker
  - CAD # / Case # input
  - Charge lookup (autocomplete MCA codes)
  - Location block input (auto-geocode on blur)
  - Narrative text area
  - **Save Draft** / **Publish** buttons
  
- **Batch Upload**
  - Drag-and-drop zone for CSV/JSON/PDF
  - Format preview (shows column mapping)
  - "Upload & Process" button
  - Real-time progress bar during processing
  
- **Submission History** table
  - Date, Status, CAD #, Offense, Action links
  - Inline **Edit**, **Delete**, **Republish** buttons
  - Filters: Date range, Status (draft, submitted, rejected, published)

#### 3. **Jail Roster Management** (if enabled)
- **Sync Status**
  - Last successful sync, record count, errors
  
- **Full Roster View**
  - Searchable table: Name, Booking ID, DOB, Charges, Status
  - Export as CSV
  
- **Snapshot History**
  - Previous roster snapshots (with timestamps, record counts)
  - Download each snapshot
  
- **Manual Entry** (for corrections/emergency updates)
  - Quick-add inmate form
  - Edit existing record
  - Mark as released

#### 4. **Administration** (for Agency Admin role only)
- **Team Management**
  - User list with roles
  - **Invite User** button (email + role)
  - Edit / Deactivate user
  
- **API Keys & Integrations**
  - List active API tokens (name, last used, scope)
  - **Create Token** button (copy token, set expiry, scope)
  - **Revoke** button
  
- **Agency Settings**
  - Organization name, ORI number
  - Contact info
  - Timezone, preferred formats
  - Enable/disable blotter/roster publishing

#### 5. **Reports & Compliance**
- **Activity Dashboard**
  - Submissions per day (line chart)
  - Roster syncs per day (line chart)
  
- **Audit Log**
  - Immutable action history
  - Filters: Action, User, Date range
  - Export to CSV for compliance audits

---

## 4. Admin Console (`/admin/lea-management`)

For Montana Blotter ops staff to oversee all agencies.

### Sections

#### 1. **Agency Onboarding**
- List of pending agencies (awaiting verification)
- Verify button (checks ORI, approves)
- Reject button (with reason)

#### 2. **Agency Directory**
- Table: Agency name, County, Type, Status, Users, Last activity
- Search, sort by name/county
- Click to view agency details:
  - Full contact info
  - User roster
  - Submission stats
  - Audit log preview

#### 3. **Health Dashboard**
- Total agencies, verification rate, active users
- Submissions per week (chart)
- Roster syncs per week (chart)
- Failed submissions (chart)
- Alert for agencies with errors

#### 4. **Bulk Configuration**
- Set coverage tiers (blotter_coverage_tier, roster_coverage_tier)
- Enable/disable features (CAD export, RMS export)
- Send bulk emails (new features, policy updates)

---

## 5. Data Ingestion Pipeline (Cron Workers)

These workers run on the existing Montana Blotter cron schedule and ingest published records into the public tables.

### `poll_lea_panel.py`
**Runs every 15 minutes.**

```python
def run():
    """Poll all enabled LEA agencies for new blotter drafts and roster snapshots."""
    conn = get_db()
    
    # Fetch all agencies with enable_blotter_publishing or enable_roster_publishing = 1
    agencies = conn.execute(
        'SELECT id, org_name FROM lea_agencies WHERE verification_status = ? AND (enable_blotter_publishing OR enable_roster_publishing)',
        ('verified',)
    ).fetchall()
    
    for agency in agencies:
        try:
            # Fetch new blotter drafts (submission_status = 'approved')
            blotters = conn.execute(
                'SELECT * FROM lea_blotter_drafts WHERE agency_id = ? AND submission_status = ? AND published_at IS NULL',
                (agency['id'], 'approved')
            ).fetchall()
            
            for blotter in blotters:
                # Pass to normalize_records.py for MCA lookup, geocoding, etc.
                queue_for_normalization(blotter)
            
            # Fetch new roster snapshots (ingestion_status = 'staged')
            rosters = conn.execute(
                'SELECT * FROM lea_roster_snapshots WHERE agency_id = ? AND ingestion_status = ?',
                (agency['id'], 'staged')
            ).fetchall()
            
            for roster in rosters:
                queue_for_roster_ingestion(roster)
        
        except Exception as e:
            log_error(f"Agency {agency['org_name']} poll failed: {e}")
            conn.execute(
                'INSERT INTO lea_audit_log (agency_id, action, change_summary) VALUES (?, ?, ?)',
                (agency['id'], 'ingestion.error', str(e))
            )
    
    conn.commit()
    conn.close()
```

### `normalize_records.py`
**Runs every 5 minutes (high frequency for real-time publishing).**

```python
def run():
    """Normalize and validate LEA blotter drafts."""
    conn = get_db()
    
    # Fetch queued blotters
    queued = conn.execute(
        'SELECT * FROM lea_blotter_drafts WHERE submission_status = ?',
        ('queued_for_normalization',)
    ).fetchall()
    
    for blotter in queued:
        try:
            # 1. Validate MCA codes
            mca_lookup = normalize_mca_code(blotter['primary_offense_mca'])
            if not mca_lookup:
                raise ValueError(f"MCA code {blotter['primary_offense_mca']} not found")
            
            # 2. Geocode location
            coords = geocode_location(blotter['incident_location_block'], county=blotter['county_name'])
            
            # 3. PII audit (flag if victim names present in narrative)
            pii_spans = get_pii_spans(blotter['public_narrative'])
            
            # 4. Create normalized record in blotter_incidents (main public table)
            incident_id = conn.execute(
                '''INSERT INTO records (
                    blotter_id, cfs_number, date, time, incident, incident_type,
                    location, details, county, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))''',
                (
                    blotter['id'],
                    blotter['cad_number'],
                    blotter['incident_date'],
                    blotter['incident_time'],
                    blotter['primary_offense_mca'],
                    mca_lookup['statute_name'],
                    blotter['incident_location_block'],
                    blotter['public_narrative'],
                    blotter['county_name'],
                )
            ).lastrowid
            
            # 5. Update blotter_draft to published
            conn.execute(
                'UPDATE lea_blotter_drafts SET submission_status = ?, published_at = datetime("now") WHERE id = ?',
                ('published', blotter['id'])
            )
            
            # 6. Audit log
            conn.execute(
                'INSERT INTO lea_audit_log (agency_id, action, resource_type, resource_id, change_summary) VALUES (?, ?, ?, ?, ?)',
                (blotter['agency_id'], 'incident.published', 'blotter', blotter['id'], f"Published to incident {incident_id}")
            )
        
        except Exception as e:
            conn.execute(
                'UPDATE lea_blotter_drafts SET submission_status = ?, submission_error = ? WHERE id = ?',
                ('error', str(e), blotter['id'])
            )
    
    conn.commit()
    conn.close()
```

### `ingest_lea_rosters.py`
**Runs every 4 hours (or on webhook trigger).**

Converts `lea_roster_snapshots` → `jail_bookings` (existing public table).

---

## 6. Implementation Roadmap

### **Phase 1: Database & Core Schema** (Week 1)
1. Add all LEA tables to `init_db.py`
2. Create migration helper functions
3. Write bootstrap fixtures for testing

### **Phase 2: Authentication & Authorization** (Week 2)
1. Implement LEA user authentication (password hashing, session management)
2. Agency verification workflow (ORI lookup, email domain check)
3. Role-based access control (admin, pio, records_officer)
4. API token generation & validation

### **Phase 3: Blotter Dashboard & API** (Week 3)
1. Create `blueprints/lea_panel.py` with blotter routes
2. Implement incident submission form (single + batch)
3. Create batch processing workers
4. Build blotter history view

### **Phase 4: Jail Roster Management** (Week 4)
1. Roster sync API & webhook handling
2. Snapshot dedup (hash-based)
3. Historical roster view

### **Phase 5: Admin Console & Compliance** (Week 5)
1. `/admin/lea-management` dashboard
2. Audit log viewer
3. Agency bulk configuration

### **Phase 6: Ingestion Workers & Public Integration** (Week 6)
1. `poll_lea_panel.py` (fetch & queue)
2. `normalize_records.py` (validate, geocode, PII check)
3. `ingest_lea_rosters.py` (roster → jail_bookings)

### **Phase 7: Testing & Go-Live** (Week 7)
1. Full pytest coverage
2. E2E smoke tests
3. Pilot with 3–5 agencies
4. Public launch

---

## 7. Security & Compliance Considerations

### Data Privacy
- **Row-level security:** Users can only view/edit their own agency's records.
- **PII auditing:** All narrative text is checked for victim/juvenile identifiers before publication.
- **Audit logging:** Immutable log of every action (compliant with CJIS requirements).

### Authentication
- Passwords hashed with bcrypt (cost=12).
- MFA support (optional, via TOTP).
- Session timeout: 30 minutes inactivity.
- API tokens: 30-day default expiry, revocable.

### API Rate Limiting
- Per-agency rate limit: 1000 requests/hour.
- Batch upload: max 500 records per file.
- Roster sync: max 5000 records per sync.

### CSRF Protection
- All POST/PUT/DELETE protected via Flask-WTF CSRF tokens.
- API tokens use Bearer scheme (immune to CSRF).

### Secret Scanning
- `.gitleaks.toml` extended to flag API keys, JWT secrets, ORI numbers.
- Never log full API tokens or passwords.

---

## 8. Example User Flows

### Flow 1: Police Department Officer Submits a Blotter Incident

1. Officer logs into `/panel/cascade/` with agency credentials.
2. Clicks **"Submit New Incident"**.
3. Fills form: Date, CAD #, Charge (auto-completes MCA codes), Location, Narrative.
4. Clicks **"Save Draft"** → incident saved, officer can edit later.
5. Officer or supervisor clicks **"Publish"** → moves to "approved" queue.
6. `normalize_records.py` worker (runs every 5 min):
   - Validates MCA codes, geocodes location, checks for PII.
   - Inserts into public `records` table.
   - Marks draft as "published".
7. Incident appears on `/jail-bookings` and in RSS feed within 10 minutes.
8. Audit log records: "officer@greatfallspd.gov published incident GF-2026-45678".

### Flow 2: Detention Center Syncs Jail Roster

1. Hill County Detention Center runs nightly cron job (10 PM).
2. Cron pulls live inmate list from internal JMS.
3. Makes `POST /api/v1/lea/roster/sync` with incremental updates.
4. API returns `sync_id` and queues roster for ingestion.
5. `ingest_lea_rosters.py` worker (runs every 4 hours):
   - Deduplicates against existing `jail_bookings` (via booking_number + facility + booking_date).
   - Updates release_at for released inmates.
   - Marks new bookings as `is_current=1`.
   - Updates jail_booking_sources.last_success_at.
6. `/jail-bookings/hill` page updates within 30 minutes.
7. Public users see current Hill County roster; RSS feeds out new bookings.

### Flow 3: Admin Onboards a New Agency

1. Sheriff office at `/panel/signup` fills: Org name, ORI, contact email, county.
2. Email verification sent to `contact@<county>.us` domain.
3. Sheriff clicks verification link → agency verification_status = "pending".
4. Montana Blotter admin at `/admin/lea-management` sees pending agency.
5. Admin clicks **"Verify"** → system checks ORI against FBI CJIS database.
6. ORI valid → verification_status = "verified", agency enabled for publishing.
7. Ops team sends welcome email with onboarding guide + API docs.
8. Sheriff office admin invites team members via `/panel/cascade/settings/team`.
9. Users accept invitations, set passwords, start submitting records.

---

## 9. MVP Scope (Minimum Viable Product)

**Launch with these features:**

1. ✅ Agency registration + email verification.
2. ✅ Single-incident blotter submission form.
3. ✅ Batch CSV upload (blotters only).
4. ✅ Incident history & search.
5. ✅ API token generation (for RMS/CAD integrations).
6. ✅ Basic audit log.
7. ✅ Jail roster sync API (`POST /api/v1/lea/roster/sync`).
8. ✅ Admin agency directory + verification.
9. ✅ Ingestion workers that feed records to public tables.

**Post-MVP (future):**
- Multi-agency (state-wide) dashboard.
- Report generation (PDF blotters for agency distribution).
- Slack/Teams webhook integrations.
- Mobile app native blotter submission.
- Advanced analytics (crime heatmaps, trend reports).

---

## 10. Deployment & Operations

### Environment Variables (add to `.env`)
```
LEA_PANEL_ENABLED=true
LEA_MAX_BATCH_SIZE=500
LEA_MAX_ROSTER_SYNC_SIZE=5000
LEA_API_TOKEN_EXPIRY_DAYS=30
LEA_MFA_ENABLED=false
LEA_VERIFICATION_REQUIRES_ORI=true
```

### Cron Entries (add to `crontab.txt`)
```
*/15 * * * * cd /root/montanablotter && python3 services/ingestion/poll_lea_panel.py >> logs/lea_panel.log 2>&1
*/5 * * * * cd /root/montanablotter && python3 services/ingestion/normalize_records.py >> logs/lea_normalize.log 2>&1
0 */4 * * * cd /root/montanablotter && python3 services/ingestion/ingest_lea_rosters.py >> logs/lea_roster.log 2>&1
```

### Monitoring
- Watch logs for `poll_lea_panel.py`, `normalize_records.py` errors.
- Alert if any agency has >10% submission failures.
- Daily report: total incidents published, roster syncs, new agencies.

---

## 11. Testing Strategy

### Unit Tests
- MCA code lookup validation.
- Location geocoding edge cases.
- PII detection (victim names, DOBs in narratives).
- Token generation & expiry.
- Audit log immutability.

### Integration Tests
- Full blotter submission flow (form → draft → published).
- Batch CSV upload with validation.
- Roster sync dedup logic.
- API token authentication.
- RBAC enforcement (user can't view another agency's data).

### E2E Tests (Playwright)
- Agency signup + email verification.
- Officer dashboard tour + incident submission.
- Admin console agency verification.
- Incident appears on public `/jail-bookings` within 10 min.

### Load Tests
- 100 agencies simultaneously submitting 10 incidents/day.
- Roster sync with 5000 inmate records.
- API rate limiting enforcement.

---

## 12. Known Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| ORI verification fails / spoofing | Verify against FBI CJIS database; require gov email domain. |
| Duplicate incident submissions | Hash-based dedup on (agency_id, cad_number, incident_date, location). |
| Malicious narrative text (PII, profanity) | PII auditor + content filter; flag for manual review before publication. |
| Roster flooding (syncs overwrite new incidents) | Separate `jail_bookings` from `lea_roster_snapshots`; use hash dedup. |
| Token leakage / unauthorized API access | Hash token on storage; enforce rate limiting; log all API calls. |
| GDPR / privacy compliance | Audit log retention = 7 years; user can request data deletion (audit trail preserved). |

---

## Glossary

- **ORI:** Originating Agency Identifier (FBI UCR standard for law enforcement agencies).
- **MCA:** Montana Code Annotated (state criminal statutes).
- **CJIS:** Criminal Justice Information Services (FBI compliance standard).
- **RMS:** Records Management System (agency internal database, e.g., Spillman, Tyler).
- **CAD:** Computer-Aided Dispatch (agency incident tracking system).
- **PII:** Personally Identifiable Information (names, DOBs, SSNs, victim info).
- **RBAC:** Role-Based Access Control (admin, pio, records_officer).
- **JWT:** JSON Web Token (stateless, signed token for API authentication).

---

## References & Related Documentation

- **AGENTS.md** — Montana Blotter agent boundaries & ownership model.
- **Security Considerations** — CJIS compliance, PII redaction, audit logging.
- **Existing tables** — `records`, `blotters`, `jail_bookings`, `agency_contacts`, `audit_logs`.
- **Existing workers** — `email_worker.py`, `jail_booking_ingest.py`, `daily_blog_worker.py`.
- **Public API** — `/api/v1/public/rosters`, `/api/v1/public/incidents`, RSS feeds.

---

## Appendix: SQL Migration Script

```python
# In init_db.py, add this to migrate() function:

def ensure_lea_schema(conn):
    """Create LEA panel tables."""
    cursor = conn.cursor()
    
    # Create all tables above (see Section 1)
    cursor.execute('''CREATE TABLE IF NOT EXISTS lea_agencies (...)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS lea_users (...)''')
    # ... etc
    
    conn.commit()

# In app.py initialization:
from init_db import ensure_lea_schema
ensure_lea_schema(get_db())
```

---

**Status:** DRAFT — Ready for Phase 1 implementation via subagent-driven-development.

**Last updated:** 2026-08-02

**Owner:** Montana Blotter Ops Team

---

End of Architecture Document. Let me write a concise implementation plan document next.
