# Montana LEA Panel — Complete Implementation Guide

**Status:** ACTIVE (Phase 1 in progress as of 2026-08-02 08:35 UTC)

**Target:** Go-live Q3 2026 with all 56 Montana law enforcement agencies able to publish police blotters and jail rosters directly to montanablotter.com.

---

## Quick Start

### For Decision-Makers
1. Read the **Executive Summary** (10 min): `/root/montanablotter/docs/plans/2026-08-02-lea-panel-executive-summary.md`
2. Check **Success Metrics** and **Timeline** sections
3. Approve budget/resources

### For Architects
1. Read the **System Architecture** (45 min): `/root/montanablotter/docs/plans/2026-08-02-law-enforcement-agency-panel-architecture.md`
2. Review **Database Schema** and **REST API** specs in detail
3. Identify technical dependencies and integration points

### For Developers (Implementation)
1. Read the **Implementation Plan** overview: `/root/montanablotter/docs/plans/2026-08-02-lea-panel-implementation-plan.md`
2. Clone/pull the repo and activate venv: `source /root/montanablotter/venv/bin/activate`
3. Run the test suite: `./venv/bin/python3 -m pytest tests/test_lea_*.py -v`
4. Start with **Phase 1, Task 1.1** (database schema)
5. Follow TDD: write test → implement → verify → commit

### For QA/Testing
1. Read the **Implementation Plan** (test section)
2. Each task includes pytest test suite (copy-pasteable)
3. Phase 7 includes E2E tests (Playwright)

### For Operations/DevOps
1. Read **Cron Workers** section (ingestion pipeline)
2. Add `poll_lea_panel.py`, `normalize_records.py`, `ingest_lea_rosters.py` to `crontab.txt`
3. Monitor logs for errors
4. Alert if agency submission failures exceed 10%

---

## Project Structure

```
/root/montanablotter/
├── docs/plans/
│   ├── LEA_PANEL_README.md                          ← Overview & navigation
│   ├── 2026-08-02-lea-panel-executive-summary.md    ← Business case (10 min read)
│   ├── 2026-08-02-law-enforcement-agency-panel-architecture.md ← Deep dive (45 min)
│   ├── 2026-08-02-lea-panel-implementation-plan.md  ← Task breakdown (all phases)
│   │
│   ├── PHASE_2_AUTH_TASKS.md                        ← Auth: bcrypt, JWT, ORI, invites
│   ├── PHASE_3_DASHBOARD_TASKS.md                   ← Dashboard: UI forms, batch upload
│   ├── PHASE_4_API_TASKS.md                         ← REST API: /api/v1/lea/...
│   │
│   ├── LEA_TEAM_COORDINATION.md                     ← Live status dashboard
│   └── LEA_IMPLEMENTATION_GUIDE.md                  ← This file
│
├── blueprints/
│   ├── lea_panel.py                   (Phase 3)     Dashboard routes
│   └── api_lea.py                     (Phase 4)     REST API endpoints
│
├── services/lea_auth/
│   ├── __init__.py
│   ├── user_auth.py                   (Phase 2)     Bcrypt hashing
│   ├── api_tokens.py                  (Phase 2)     JWT generation
│   ├── agency_verification.py          (Phase 2)     ORI lookup
│   └── invitations.py                  (Phase 2)     Invite workflow
│
├── services/ingestion/
│   ├── poll_lea_panel.py              (Phase 5)     Fetch approved drafts (15 min)
│   ├── normalize_lea_records.py        (Phase 5)     MCA validation, geocoding, PII
│   └── ingest_lea_rosters.py          (Phase 5)     Roster → jail_bookings
│
├── templates/lea/
│   ├── base.html
│   ├── login.html                     (Phase 2)
│   ├── dashboard.html                 (Phase 3)
│   ├── submit_incident.html           (Phase 3)
│   ├── batch_upload.html              (Phase 3)
│   ├── blotter_history.html           (Phase 3)
│   ├── api_keys.html                  (Phase 3)
│   ├── team_management.html           (Phase 3)
│   └── admin/lea_management.html      (Phase 6)
│
├── static/lea/
│   └── dashboard.css                  (Phase 3)
│
├── tests/
│   ├── test_lea_agencies.py           (Phase 1)
│   ├── test_lea_auth.py               (Phase 2)
│   ├── test_lea_panel_routes.py       (Phase 3)
│   ├── test_api_lea.py                (Phase 4)
│   ├── test_poll_lea_panel.py         (Phase 5)
│   ├── test_normalize_lea_records.py  (Phase 5)
│   ├── test_ingest_lea_rosters.py     (Phase 5)
│   ├── test_lea_admin_console.py      (Phase 6)
│   └── test_lea_e2e_playwright.py     (Phase 7)
│
└── init_db.py                         Updated with ensure_lea_schema()
```

---

## 7-Phase Roadmap

| Phase | Week | Focus | Status | Lead Role |
|-------|------|-------|--------|-----------|
| **1** | W1 | Database schema (8 tables) | 🟡 IN PROGRESS (08:35 UTC) | Backend Engineer |
| **2** | W2 | Authentication (bcrypt, JWT, ORI, invites) | 🟢 Ready to queue | Auth Engineer |
| **3** | W2–W3 | Agency dashboard UI (forms, uploads, history) | 🟢 Ready to queue | Frontend Engineer |
| **4** | W2–W3 | REST API (`/api/v1/lea/...`) | 🟢 Ready to queue | API Engineer |
| **5** | W3 | Ingestion workers (poll, normalize, ingest) | 🟢 Ready to queue | Pipeline Engineer |
| **6** | W3 | Admin console (onboarding, verification, health) | 🟢 Ready to queue | Admin UI Engineer |
| **7** | W3–W4 | Testing, E2E, pilot, go-live | 🟢 Ready to queue | QA Engineer |

---

## What Each Phase Builds

### Phase 1: Database Schema (Week 1)
8 new tables in SQLite, fully indexed and migrated:
- `lea_agencies` — agency registry (56 rows, one per county/type)
- `lea_users` — per-agency users (500–1000 total)
- `lea_blotter_drafts` — staged incidents (draft → approved → published)
- `lea_roster_snapshots` — jail roster snapshots
- `lea_api_tokens` — hashed API keys
- `lea_audit_log` — immutable action log (CJIS-compliant)
- `lea_agency_coverages` — feature flags per agency
- `lea_invitations` — pending user invites (7-day TTL)

**Output:** All tables created, indexed, tested, migrated via `init_db.ensure_lea_schema()`

---

### Phase 2: Authentication (Week 2, Day 1–2)
User identity verification and API access:
- **Bcrypt password hashing** (secure, random salt, verification)
- **JWT API tokens** (30-day expiry, refresh flow)
- **ORI verification** (lookup vs. Montana DOJ registry)
- **Government email domain checks** (.gov, .mt.us)
- **User invitation workflow** (invite → 7-day link → accept → create account)
- **Session management** (Flask-Login + LEA session cookies)

**Output:** `services/lea_auth/` module, `/lea/login` and `/lea/logout` routes, API token endpoints

---

### Phase 3: Agency Dashboard UI (Week 2–3, Day 2–3)
Web interface for officers to submit incidents and manage agency:
- **Agency dashboard home** (stats, recent submissions, quick actions)
- **Submit single incident** (date, time, CAD #, location, charges, narrative form)
- **Batch CSV upload** (upload multiple incidents, parse, preview, confirm)
- **Blotter history** (search, filter, status, edit/delete/publish)
- **API key management** (create, revoke, show once)
- **Team management** (invite users, assign roles, deactivate)
- **Responsive design** (mobile-first, dark/light mode support)

**Output:** `blueprints/lea_panel.py`, Jinja2 templates, CSS, all tested

---

### Phase 4: REST API (Week 2–3, Day 4–5)
Programmatic access for automated ingestion systems:
- **`POST /api/v1/lea/auth/token`** — exchange username/password or refresh token for JWT
- **`POST /api/v1/lea/blotter/publish`** — submit single incident via API
- **`POST /api/v1/lea/blotter/batch`** — submit 10–1000 incidents in one request
- **`POST /api/v1/lea/roster/sync`** — submit jail roster snapshot (full or incremental)
- **`GET /api/v1/lea/blotter/batch/<batch_id>/status`** — poll batch processing progress
- **`GET /api/v1/lea/audit?action=<action>&days=30`** — fetch audit log (agency's own actions)
- **Rate limiting** (1000 req/hour per agency)
- **Bearer token auth** (JWT in Authorization header)
- **Security headers** (CORS, X-Frame-Options, etc.)

**Output:** `blueprints/api_lea.py`, rate limiter, error handling, full test coverage

---

### Phase 5: Ingestion Workers (Week 3, Day 1–2)
Background jobs that normalize and publish agency submissions:
- **`poll_lea_panel.py`** (runs every 15 min) — fetch new approved drafts and queue for processing
- **`normalize_lea_records.py`** (runs every 5 min) — validate MCA codes, geocode locations, audit PII, insert into public `records` table
- **`ingest_lea_rosters.py`** (runs every 4 hours) — convert agency roster snapshots into public `jail_bookings` table, dedup by booking number + hash
- **Error handling & alerts** — log failures, alert on >10% failure rate
- **Cron scheduling** — add to `crontab.txt` and systemd timers

**Output:** Fully tested workers, `crontab.txt` updated, monitoring/alerting rules

---

### Phase 6: Admin Console (Week 3, Day 3–4)
Montana Blotter staff dashboard for managing agencies:
- **Agency onboarding** (list pending → verify email domain → check ORI → approve/reject)
- **Agency directory** (table: name, county, type, status, users count, last activity)
- **Health dashboard** (line charts: submissions/week, roster syncs/week, failure rate)
- **Bulk configuration** (set coverage tiers, enable/disable features, bulk email)
- **Audit log viewer** (search, filter, export)
- **User management** (deactivate users, reset passwords)

**Output:** `blueprints/admin/lea_management.py`, templates, admin routes

---

### Phase 7: Testing & Go-Live (Week 3–4, Day 5+)
Full test suite, E2E validation, and production launch:
- **Unit tests** (95%+ coverage for all phases)
- **Integration tests** (end-to-end workflows: invite → login → submit → publish)
- **E2E tests** (Playwright: user journey through dashboard and API)
- **Pilot** (recruit 3–5 Montana agencies, real blotter submissions)
- **Load testing** (rate limits, concurrent requests)
- **Security testing** (token validation, SQL injection, XSS)
- **Go-live checklist** (monitoring, alerting, runbook, backup strategy)

**Output:** Full pytest suite, E2E test scripts, pilot report, production launch

---

## How to Work on This

### 1. Activate Environment
```bash
cd /root/montanablotter
source venv/bin/activate
pip install -r requirements.txt  # Ensure bcrypt, pyjwt are installed
```

### 2. Run Tests
```bash
# Full suite
./venv/bin/python3 -m pytest tests/test_lea_*.py -v

# Single phase
./venv/bin/python3 -m pytest tests/test_lea_agencies.py -v

# Single test
./venv/bin/python3 -m pytest tests/test_lea_agencies.py::TestLEAAgenciesSchema::test_lea_agencies_table_exists -v
```

### 3. Development Workflow (per task)
1. **Read the task spec** in `PHASE_X_Y_TASKS.md`
2. **Write a failing test** (copy from template)
3. **Run test** to confirm it fails: `pytest tests/test_*.py::TestClass::test_method -v`
4. **Implement minimal code** to make test pass
5. **Run test** again to confirm it passes
6. **Commit** with conventional commit message: `git commit -m "feat(lea): ..."`
7. **Move to next task**

### 4. Check Git History
```bash
git log --oneline | head -20  # See recent commits
git status                     # Current changes
git diff HEAD~3               # Last 3 commits' changes
```

### 5. Database Inspection
```bash
sqlite3 blotter.db "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;" | grep lea
sqlite3 blotter.db "PRAGMA table_info(lea_agencies);"
```

---

## Key Dependencies to Install

```bash
pip install bcrypt==4.0.1        # Password hashing
pip install PyJWT==2.8.1         # JWT tokens
pip install email-validator==2.1 # Email validation
```

These are already in `requirements.txt` — just ensure they're installed.

---

## Success Criteria

### Phase 1 ✅
- [ ] All 8 tables created and indexed
- [ ] Migration logic in `init_db.ensure_lea_schema()`
- [ ] All tests pass (`pytest tests/test_lea_agencies.py -v`)
- [ ] Commits pushed to `main`

### Phase 2 ✅
- [ ] Bcrypt hashing tested and working
- [ ] JWT token generation + validation
- [ ] ORI verification logic
- [ ] Invitation workflow (create, validate, accept)
- [ ] All tests pass
- [ ] `/lea/login` and `/lea/logout` routes working
- [ ] `/api/v1/lea/auth/token` endpoint working

### Phase 3 ✅
- [ ] Dashboard accessible to logged-in users
- [ ] Form submission (single incident) saves to drafts
- [ ] CSV batch upload works
- [ ] History view with filters
- [ ] API key creation/revocation
- [ ] Team invite flow
- [ ] Responsive design (mobile + desktop)
- [ ] All tests pass

### Phase 4 ✅
- [ ] All `/api/v1/lea/*` endpoints implemented
- [ ] Bearer token auth on all endpoints
- [ ] Rate limiting enforced
- [ ] Error responses standardized
- [ ] Audit logging on all API calls
- [ ] All tests pass (95%+ coverage)

### Phase 5 ✅
- [ ] `poll_lea_panel.py` fetches approved drafts
- [ ] `normalize_lea_records.py` validates + publishes
- [ ] `ingest_lea_rosters.py` syncs rosters
- [ ] Cron jobs scheduled and monitoring
- [ ] All tests pass

### Phase 6 ✅
- [ ] Admin console routes operational
- [ ] Agency onboarding workflow
- [ ] Health dashboard showing real data
- [ ] Bulk config + email sending
- [ ] Audit log searchable
- [ ] All tests pass

### Phase 7 ✅
- [ ] 95%+ test coverage
- [ ] E2E tests pass
- [ ] 3–5 agencies successfully onboarded + submitting
- [ ] <10 min publication lag verified
- [ ] Production deployment checklist complete
- [ ] Go-live approved

---

## Timeline Estimates

| Phase | Tasks | Est. Time | Parallel? |
|-------|-------|-----------|-----------|
| 1 | 1.1–1.8 (schema) | 1 week | No (sequential) |
| 2 | 2.1–2.7 (auth) | 3 days | No (depends on Phase 1) |
| 3 | 3.1–3.7 (dashboard) | 3 days | Yes (after Phase 2) |
| 4 | 4.1–4.8 (API) | 3 days | Yes (after Phase 2, parallel to Phase 3) |
| 5 | 5.1–5.7 (workers) | 2 days | No (depends on Phase 4) |
| 6 | 6.1–6.6 (admin) | 2 days | After Phase 3 |
| 7 | 7.1–7.8 (testing) | 3 days | After Phase 6 |

**Total:** 2–3 weeks (running Phases 3–4 in parallel shaves 3 days)

---

## Common Issues & Fixes

### Issue: "No module named 'services.lea_auth'"
**Fix:** Ensure `services/lea_auth/__init__.py` exists (can be empty).

### Issue: Tests fail with "table does not exist"
**Fix:** Ensure `init_db.ensure_lea_schema()` is called in test `setUp()` before any test runs.

### Issue: JWT token validation always fails
**Fix:** Check `LEA_JWT_SECRET` environment variable. In dev, it defaults to `dev-lea-jwt-secret-change-in-prod`. In prod, set via `.env` or `config.py`.

### Issue: Rate limiter not working
**Fix:** Ensure `lea_audit_log` table exists and is populated. Rate limiter queries this table for request counts.

### Issue: Invitation tokens not matching
**Fix:** Invitation tokens are **hashed** when stored. When accepting, verify against the hash (not the plaintext token). Use `user_auth.verify_password(invite_token, stored_hash)`.

---

## Resources & References

- **AGENTS.md** — Montana Blotter agent ownership model and conventions
- **CLAUDE.md** — Developer workflow, coding style, commit patterns
- **crontab.txt** — Existing cron schedule (add LEA workers here)
- **requirements.txt** — Dependencies (bcrypt, PyJWT, etc.)
- **Existing tables:** `records`, `blotters`, `jail_bookings`, `users`, `audit_logs`
- **Existing workers:** `email_worker.py`, `jail_booking_ingest.py`, `daily_blog_worker.py`

---

## Next Steps

**Now:** Phase 1 is running (subagent, ETA 09:15 UTC).  
**After Phase 1:** Dispatch Phase 2 (Auth) immediately.  
**Parallel:** Phases 3–4 can run simultaneously after Phase 2.  
**Go-Live:** Target Q3 2026 (8–16 weeks from kickoff).

---

## Questions?

Refer to the **System Architecture** document for deep dives:
- "How do we handle X?" → Check Section 9 (Risks & Mitigations)
- "What's the deployment process?" → Check Section 10 (Operations)
- "How do we test Y?" → Check each phase's task spec (includes test templates)

---

**Document prepared by:** Hermes Agent (Jon's assistant)  
**Last updated:** 2026-08-02 08:50 UTC  
**Status:** Active development (Phase 1 in progress)
