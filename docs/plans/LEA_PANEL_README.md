# Montana Blotter LEA Panel — Complete Project Documentation

## Overview

This project adds a **free, zero-friction web panel and REST API** for Montana's 56 law enforcement agencies (sheriff offices, police departments, detention centers) to publish police blotters and jail rosters directly to montanablotter.com.

**Goal:** Transform Montana Blotter from a scraper-dependent aggregator into a **direct publishing platform** where agencies push their own data in real-time, with automatic normalization and public distribution.

---

## Documents in This Series

### 1. **Executive Summary** (`2026-08-02-lea-panel-executive-summary.md`)
- **Audience:** Stakeholders, decision-makers, non-technical team members.
- **Length:** ~10 min read.
- **Contains:**
  - The problem we're solving (fragmented county publishing).
  - Business value (revenue, traffic, market positioning).
  - Technical architecture overview (high-level diagram, not code).
  - Rollout timeline (7-week phased rollout).
  - Success metrics and risks.
- **Start here if:** You want to understand the "why" and business impact.

### 2. **System Architecture** (`2026-08-02-law-enforcement-agency-panel-architecture.md`)
- **Audience:** Architects, senior engineers, system designers.
- **Length:** ~45 min read (deep dive).
- **Contains:**
  - Complete system architecture diagram (ASCII).
  - Full database schema (8 new tables, all columns documented).
  - REST API endpoint specification (request/response payloads).
  - Agency dashboard UI/UX flow (5 main sections).
  - Admin console layout and features.
  - Ingestion pipeline pseudocode (cron workers).
  - Security & compliance considerations (PII, RBAC, audit logging).
  - Implementation risks and mitigations.
  - Glossary and references.
- **Start here if:** You're going to implement this or review the architecture.

### 3. **Implementation Plan** (`2026-08-02-lea-panel-implementation-plan.md`)
- **Audience:** Developers, QA engineers, project managers.
- **Length:** ~30 min read (quick reference during implementation).
- **Contains:**
  - 7 phases, each ~1 week.
  - Bite-sized tasks (2–5 min focused work each).
  - Each task includes:
    - Failing test (TDD pattern).
    - Minimal implementation.
    - Passing test verification.
    - Git commit message.
  - Example: Phase 1, Task 1.1 — Create `lea_agencies` table with full test suite.
  - Can be delegated task-by-task to subagents via `subagent-driven-development`.
- **Start here if:** You're implementing the system and need bite-sized tasks.

---

## Quick Navigation

### I want to...

**...understand the business case**
→ Read: Executive Summary (Section: "The Problem", "The Solution", "Revenue & Impact")

**...see the technical architecture**
→ Read: System Architecture (Sections: "System Architecture Overview", "Database Schema", "REST API")

**...start implementing**
→ Read: Implementation Plan (Phase 1, Tasks 1.1–1.8), then delegate tasks to subagents

**...verify security/compliance**
→ Read: System Architecture (Section: "Security & Compliance Considerations")

**...design the UI/UX**
→ Read: System Architecture (Section: "Agency Dashboard UI/UX Flow")

**...understand the data flow**
→ Read: System Architecture (Section: "Data Ingestion Pipeline")

---

## Key Concepts

### Multi-Tenant Architecture
- Each agency is a separate "tenant" with its own users, records, and audit trail.
- Row-level security (RLS) enforced: users can only view/edit their own agency's data.
- Shared database (SQLite) but complete data isolation.

### Draft → Approved → Published Flow
```
Officer submits incident
    ↓
Saved as "draft" in lea_blotter_drafts
    ↓
Officer or supervisor clicks "Publish"
    ↓
Status changes to "approved"
    ↓
Cron worker (normalize_records.py) runs every 5 min
    ↓
Validates MCA codes, geocodes location, checks for PII
    ↓
If valid: inserts into public "records" table
    ↓
Incident appears on /jail-bookings, RSS feeds, JSON API within 10 min
```

### RBAC (Role-Based Access Control)
- **Agency Admin:** Can manage users, API keys, settings, view all audit logs.
- **Public Information Officer (PIO):** Can submit/publish incidents, view own submissions.
- **Records Officer:** Can submit incidents, but cannot publish (requires PIO approval).

### Audit Logging (CJIS-Compliant)
- Every action logged immutably in `lea_audit_log` table.
- Includes: timestamp, actor (user), action, resource, IP address, before/after state.
- Immutable: no deletion, no modification (enforcement via database constraints).
- Retention: 7 years (compliance requirement).

---

## Technology Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python 3.12 + Flask |
| Database | SQLite (existing; no migration to Postgres) |
| Auth | Bcrypt (password hashing) + JWT (API tokens) |
| API | REST over HTTPS, Bearer token auth |
| Cron | Python + systemd timers (existing pattern) |
| Testing | pytest + unittest |
| Frontend | Jinja2 templates + vanilla JS + CSS |
| Mobile | React Native (future phase) |

---

## New Database Tables

### Core Tables (8 total)

| Table | Rows | Purpose |
|-------|------|---------|
| `lea_agencies` | ~56 | Agency registry (org name, ORI, county, verification status) |
| `lea_users` | ~500–1000 | Users per agency (email, role, MFA) |
| `lea_invitations` | ~100 (ephemeral) | Pending user invites (expires after 7 days) |
| `lea_blotter_drafts` | ~10K/year | Staging for submitted incidents (draft → approved → published) |
| `lea_roster_snapshots` | ~2K/year | Jail roster snapshots (one per sync) |
| `lea_api_tokens` | ~200 | API keys for programmatic access (hashed) |
| `lea_audit_log` | ~100K+/year | Immutable action log (CJIS-compliant) |
| `lea_agency_coverages` | ~56 | Feature flags per agency (blotter tier, roster tier) |

**Integration with existing tables:**
- Published incidents → inserted into `records` (existing public table).
- Published rosters → inserted into `jail_bookings` (existing public table).
- No modifications to existing schemas.

---

## REST API Endpoints

### Authentication
```
POST /api/v1/lea/auth/token
  ← grant_type, username, password
  → access_token, token_type, expires_in, refresh_token
```

### Blotter Publishing
```
POST /api/v1/lea/blotter/publish
  ← incident JSON (date, time, CAD #, charges, location, narrative)
  → draft_id, status, review_url

POST /api/v1/lea/blotter/batch
  ← multipart form (CSV/JSON file)
  → batch_id, status, records_queued, status_url

GET /api/v1/lea/blotter/batch/<batch_id>/status
  → processed, succeeded, failed, failures[]
```

### Jail Roster Management
```
POST /api/v1/lea/roster/sync
  ← sync_type, updates[] (incremental roster changes)
  → sync_id, status, records_received, status_url

GET /api/v1/lea/roster/snapshot?format=json&status=current
  → facility, snapshot_date, total_inmates, inmates[]
```

### Audit & Admin
```
GET /api/v1/lea/audit?action=<action>&days=30
  → logs[] with timestamp, user, action, resource_id, change_summary
```

---

## Cron Workers (Ingestion Pipeline)

### `poll_lea_panel.py` (every 15 min)
Fetch all new blotter drafts (status='approved') and roster snapshots (status='staged') from `lea_*` tables. Queue them for normalization.

### `normalize_records.py` (every 5 min)
Process queued records:
1. Validate MCA codes (e.g., "45-5-202" → "Assault").
2. Geocode location string (e.g., "300 BLK CENTRAL AVE" → lat/long).
3. Audit for PII (flag victim names, DOBs).
4. If valid: insert into public `records` table.
5. Mark draft as "published".

### `ingest_lea_rosters.py` (every 4 hours)
Convert `lea_roster_snapshots` (agency-submitted) into `jail_bookings` (public table).
- Dedup logic: hash-based (SHA256 of roster JSON).
- Only new/updated records inserted.
- Update release dates for released inmates.

---

## Agency Dashboard Features

### For Officers
- **Submit Single Incident:** Date, time, CAD #, charge (MCA lookup), location, narrative.
- **Upload Batch:** CSV/JSON/PDF (parsed to extract incident data).
- **View History:** Submitted incidents with status, filters, edit/delete.
- **Sync Roster:** Manual sync button (if enabled by admin).

### For Agency Admin
- **Team Management:** Invite users, assign roles, deactivate.
- **API Keys:** Create tokens, set scope & expiry, revoke.
- **Settings:** Org name, ORI, contact info, timezone, enable/disable features.
- **Audit Log:** View all actions (immutable, downloadable).

### For Records Officer
- (Same as Officers, but cannot "Publish" — only supervisors can approve.)

---

## Admin Console (`/admin/lea-management`)

For Montana Blotter ops staff:

1. **Agency Onboarding:**
   - List pending agencies (awaiting verification).
   - Verify button (ORI check, email confirmation).
   - Reject button (with reason).

2. **Agency Directory:**
   - Table: name, county, type, status, users, last activity.
   - Click for details: full contact, user roster, submission stats.

3. **Health Dashboard:**
   - Total agencies, verification rate, active users.
   - Submissions per week (line chart).
   - Roster syncs per week.
   - Failed submissions (alerts).

4. **Bulk Configuration:**
   - Set coverage tiers (blotter, roster).
   - Enable/disable features.
   - Send bulk emails.

---

## Security Model

### Authentication
- **Web UI:** Flask session + bcrypt hashed passwords.
- **API:** JWT bearer tokens (30-day default expiry), hashed on storage.
- **MFA:** Optional TOTP (email OTP as fallback).

### Authorization
- **Row-Level Security (RLS):** Users only see their own agency's data.
- **RBAC:** admin → pio → records_officer hierarchy.
- **Audit Logging:** Every action logged with user, IP, timestamp, before/after state.

### Privacy (PII Handling)
- **PII Auditor:** Scans narratives for victim names, DOBs, SSNs.
- **Redaction:** Automatic masking for public display (or flag for manual review).
- **Juvenile Records:** Never auto-published; require explicit admin approval.

### API Security
- **Rate Limiting:** 1000 req/hour per agency.
- **Token Hashing:** SHA256 on storage; token shown only on creation.
- **CORS:** Restricted to agency-owned domains (configurable).

---

## Compliance & Auditability

### CJIS Compliance
- **Immutable Audit Log:** Every action recorded (no deletion, no modification).
- **Retention:** 7 years (configurable).
- **Tamper-Proof:** Database constraints prevent updates/deletes on `lea_audit_log`.

### GDPR / Privacy
- **Data Deletion:** Users can request deletion; audit trail preserved.
- **Data Minimization:** Only store necessary fields (no SSNs, no juvenile records unless explicit).
- **Encryption:** API tokens hashed; PII masked in logs.

### Operational Security
- **Secret Scanning:** gitleaks extended to flag ORI numbers, API keys.
- **Rate Limiting:** Prevent brute-force (login attempts, API calls).
- **TLS:** HTTPS-only; no plaintext transmission.

---

## Rollout Timeline

| Phase | Week | Deliverables |
|-------|------|--------------|
| 1 | W1 | Database schema (8 tables), migrations integrated |
| 2 | W2 | Auth (bcrypt, ORI verification, invitations, JWT tokens) |
| 3 | W3 | Agency dashboard (incident form, batch upload, history) |
| 4 | W4 | REST API (`/api/v1/lea/...`), token auth, rate limiting |
| 5 | W5 | Ingestion workers (poll, normalize, ingest) |
| 6 | W6 | Admin console, agency onboarding, health dashboard |
| 7 | W7 | Testing (pytest, E2E), pilot with 3–5 agencies, go live |

---

## Success Metrics (First Year)

- ✅ 25+ agencies registered (Q1 2026)
- ✅ 1000+ incidents/week published (Q1 2026)
- ✅ <10 min publication lag (vs 3–7 days today)
- ✅ 500+ roster updates/week (Q1 2026)
- ✅ 40+ agencies active (Q2 2026, >70% of Montana)
- ✅ 5000+ incidents/week (Q2 2026)
- ✅ All 56 counties publishing via panel (Q3 2026)
- ✅ Zero data loss (audit log = 100% accountability)

---

## Known Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Slow agency adoption | Free tier, minimal onboarding, phone support |
| ORI spoofing | Require government email domain (.gov) |
| PII leaks in narratives | PII auditor + manual review before publication |
| Duplicate incidents | Hash-based dedup on (agency_id, CAD #, date, location) |
| API token leakage | Hash tokens, rate limiting, audit all API calls |
| Roster sync overwrite incidents | Separate tables + hash dedup logic |

---

## How to Use These Documents

### For Decision-Makers
1. Read **Executive Summary** (10 min).
2. Check **Success Metrics** and **Timeline** sections above.
3. Approve budget/resources.

### For Architects/System Designers
1. Read **Executive Summary** (overview).
2. Read **System Architecture** (deep dive, all sections).
3. Review **Database Schema** and **REST API** in detail.
4. Identify technical dependencies and integration points.

### For Developers (Implementation Phase)
1. Skim **Executive Summary** and **System Architecture** (context).
2. Read **Implementation Plan** (Phase 1–7 overview).
3. Start with Phase 1, Task 1.1 (create `lea_agencies` table).
4. Follow TDD pattern: write test → implement → verify → commit.
5. Can be parallelized via subagent-driven-development.

### For QA/Testing
1. Read **Implementation Plan** (test patterns).
2. Each task includes pytest test suite (copy-pasteable).
3. Phase 7 includes E2E tests (Playwright).

### For Operations/DevOps
1. Read **Cron Workers** section (ingestion pipeline).
2. Add `poll_lea_panel.py`, `normalize_records.py`, `ingest_lea_rosters.py` to `crontab.txt`.
3. Monitor logs for errors.
4. Alert if agency submission failures exceed 10%.

---

## File Structure (Post-Implementation)

```
/root/montanablotter/
├── blueprints/
│   ├── lea_panel.py          # Agency dashboard routes
│   └── api_lea.py            # REST API routes (/api/v1/lea/...)
│
├── blueprints/admin/
│   └── lea_management.py      # Admin console routes
│
├── services/
│   ├── lea_auth/
│   │   ├── __init__.py
│   │   ├── user_auth.py       # Password hashing, session mgmt
│   │   ├── agency_verification.py  # ORI lookup, email domain check
│   │   ├── api_tokens.py      # Token generation & hashing
│   │   └── invitations.py     # Invitation acceptance flow
│   │
│   ├── ingestion/
│   │   ├── poll_lea_panel.py           # Cron: fetch new submissions
│   │   ├── normalize_lea_records.py    # Cron: validate & publish
│   │   └── ingest_lea_rosters.py       # Cron: roster → jail_bookings
│   │
│   └── api/
│       └── lea_auth.py        # API middleware (token validation)
│
├── templates/
│   ├── lea/
│   │   ├── dashboard.html          # Agency home
│   │   ├── submit_incident.html    # Single incident form
│   │   ├── batch_upload.html       # CSV upload
│   │   └── blotter_history.html    # Submission history
│   │
│   └── admin/
│       └── lea_management.html     # Admin console
│
├── tests/
│   ├── test_lea_agencies.py        # Schema tests
│   ├── test_lea_auth.py            # Auth tests
│   ├── test_lea_panel_routes.py    # Dashboard routes
│   ├── test_api_lea.py             # REST API tests
│   ├── test_poll_lea_panel.py      # Ingestion worker tests
│   ├── test_normalize_lea_records.py
│   └── test_ingest_lea_rosters.py
│
├── docs/plans/
│   ├── 2026-08-02-lea-panel-executive-summary.md
│   ├── 2026-08-02-law-enforcement-agency-panel-architecture.md
│   └── 2026-08-02-lea-panel-implementation-plan.md
│
└── init_db.py
    ├── ensure_lea_schema()    # Create all LEA tables
    └── (called from migrate())
```

---

## What's NOT Included (Out of Scope)

- ❌ Mobile app (phase 2, post-MVP).
- ❌ Advanced analytics / dashboards.
- ❌ SMS alerts or webhook integrations (phase 2).
- ❌ Cross-state record search (requires PII review per state).
- ❌ Prosecution case management (separate system).
- ❌ Prisoner booking photo storage (storage cost concern).

---

## References & Links

- **AGENTS.md** — Montana Blotter agent ownership model.
- **CLAUDE.md** — Developer workflow & coding conventions.
- **crontab.txt** — Existing cron schedule (add LEA workers here).
- **requirements.txt** — Add `bcrypt`, `pyjwt` for auth.
- **Existing tables:** `records`, `blotters`, `jail_bookings`, `users`, `audit_logs`.
- **Existing workers:** `email_worker.py`, `jail_booking_ingest.py`, `daily_blog_worker.py`.

---

## Questions During Implementation?

Refer to the **System Architecture** document (Sections 9–10: Risks, Deployment, Testing).

For example:
- "How do we handle X?"  → Check Section 9 (Risks & Mitigations).
- "What's the deployment process?" → Check Section 10 (Deployment & Operations).
- "How do we test Y?" → Check System Architecture, Section 7 (Testing Strategy).

---

**Document prepared by:** Montana Blotter Architecture Team
**Date:** 2026-08-02
**Status:** Ready for implementation
**Next step:** Begin Phase 1 (Database schema) with subagent-driven-development
