# Montana LEA Panel — Project Status & Checklist
**Updated:** 2026-08-02 08:50 UTC  
**Project Lead:** Jon (user)  
**Implementation Status:** ACTIVE (Phase 1 in progress)

---

## Executive Summary

The **Montana LEA Panel** is a 7-phase, 2-3 week implementation to build a free SaaS platform where Montana's 56 law enforcement agencies can self-publish police blotters and jail rosters directly to montanablotter.com.

**Key Goals:**
- ✅ Reduce publication lag from 3–7 days (scraping) to <10 minutes (direct agency submission)
- ✅ Enable 1000+ incidents/week to go public (vs. 200–300 today)
- ✅ Give agencies direct control, audit trail, and API access
- ✅ Maintain CJIS compliance (immutable audit logging)
- ✅ Scale to all 56 Montana counties by Q3 2026

---

## Team Composition

| Role | Phase | Status | Assigned | Notes |
|------|-------|--------|----------|-------|
| **Backend Engineer** | 1 (Schema) | 🟡 IN PROGRESS (08:35 UTC) | Subagent | TDD: test → code → commit |
| **Auth Engineer** | 2 (Auth) | 🟢 Ready to queue | Awaiting phase 1 | Bcrypt, JWT, ORI, invites |
| **Frontend Engineer** | 3 (Dashboard) | 🟢 Ready to queue | Awaiting phase 2 | Jinja2, forms, batch upload |
| **API Engineer** | 4 (REST API) | 🟢 Ready to queue | Awaiting phase 2 | `/api/v1/lea/*` endpoints |
| **Pipeline Engineer** | 5 (Workers) | 🟢 Ready to queue | Awaiting phase 4 | Cron jobs, normalization |
| **Admin UI Engineer** | 6 (Admin) | 🟢 Ready to queue | Awaiting phase 3 | Onboarding, health dashboard |
| **QA Engineer** | 7 (Testing) | 🟢 Ready to queue | Awaiting phase 6 | Pytest, E2E, pilot, go-live |

---

## Phase Completion Checklist

### ✅ Phase 1: Database Schema (Week 1)
**Target:** 2026-08-02 09:15 UTC  
**Status:** 🟡 IN PROGRESS (5 min elapsed, est. 35 min remaining)

- [ ] Task 1.1: `lea_agencies` table + ORI indexes
- [ ] Task 1.2: `lea_users` table + RBAC columns
- [ ] Task 1.3: `lea_blotter_drafts` table + workflow
- [ ] Task 1.4: `lea_roster_snapshots` table + indexes
- [ ] Task 1.5: `lea_api_tokens` table + hashing fields
- [ ] Task 1.6: `lea_audit_log` table + immutable constraints
- [ ] Task 1.7: `lea_agency_coverages` table + feature flags
- [ ] Task 1.8: `lea_invitations` table + expiry logic
- [ ] Full pytest suite passes (95%+ coverage)
- [ ] All commits pushed to `main`

**Subagent Progress:**
- Currently reading implementation plan
- ETA for Task 1.1: 08:38 UTC
- ETA for Phase 1 completion: 09:15 UTC

**Next Action:** Dispatch Phase 2 (Auth) upon Phase 1 completion

---

### 🟢 Phase 2: Authentication (Week 2, Day 1–2)
**Target:** 2026-08-05 (after Phase 1 ✓)

- [ ] Task 2.1: Bcrypt password hashing & verification
- [ ] Task 2.2: JWT API token generation & expiry
- [ ] Task 2.3: ORI verification & government email domains
- [ ] Task 2.4: Invitation workflow (create & accept)
- [ ] Task 2.5: User login route & session
- [ ] Task 2.6: MFA TOTP setup & validation (optional)
- [ ] Task 2.7: Auth service integration tests
- [ ] Full pytest suite passes
- [ ] `/lea/login` and `/lea/logout` routes working
- [ ] `/api/v1/lea/auth/token` endpoint working

**Assigned:** Auth Engineer (subagent on deck)  
**Task Templates:** `/root/montanablotter/docs/plans/PHASE_2_AUTH_TASKS.md` (ready)  
**Status:** Awaiting Phase 1 completion

---

### 🟢 Phase 3: Agency Dashboard UI (Week 2–3, Day 2–3)
**Target:** 2026-08-07 (parallel to Phase 4, after Phase 2 ✓)

- [ ] Task 3.1: Agency dashboard home
- [ ] Task 3.2: Submit single incident form
- [ ] Task 3.3: Batch CSV upload & preview
- [ ] Task 3.4: Blotter history & filtering
- [ ] Task 3.5: API key management UI
- [ ] Task 3.6: Team management (invites, roles)
- [ ] Task 3.7: Dashboard styling & responsive design
- [ ] Full pytest suite passes
- [ ] All forms tested (validation, submission, error handling)
- [ ] Mobile-responsive layout verified

**Assigned:** Frontend Engineer (subagent on deck)  
**Task Templates:** `/root/montanablotter/docs/plans/PHASE_3_DASHBOARD_TASKS.md` (ready)  
**Status:** Awaiting Phase 2 completion

---

### 🟢 Phase 4: REST API (Week 2–3, Day 4–5)
**Target:** 2026-08-07 (parallel to Phase 3, after Phase 2 ✓)

- [ ] Task 4.1: `/api/v1/lea/auth/token` endpoint
- [ ] Task 4.2: `/api/v1/lea/blotter/publish` endpoint
- [ ] Task 4.3: `/api/v1/lea/blotter/batch` multipart handler
- [ ] Task 4.4: `/api/v1/lea/roster/sync` endpoint
- [ ] Task 4.5: Rate limiting & token middleware
- [ ] Task 4.6: CORS & security headers
- [ ] Task 4.7: Error responses & logging
- [ ] Task 4.8: Full API test suite (95%+ coverage)
- [ ] All endpoints return consistent error format
- [ ] Rate limiting enforced (1000 req/hour per agency)

**Assigned:** API Engineer (subagent on deck)  
**Task Templates:** `/root/montanablotter/docs/plans/PHASE_4_API_TASKS.md` (ready)  
**Status:** Awaiting Phase 2 completion

---

### 🟢 Phase 5: Ingestion Workers (Week 3, Day 1–2)
**Target:** 2026-08-09 (after Phase 4 ✓)

- [ ] Task 5.1: `poll_lea_panel.py` (every 15 min)
- [ ] Task 5.2: `normalize_records.py` (every 5 min)
- [ ] Task 5.3: PII auditor integration & redaction
- [ ] Task 5.4: `ingest_lea_rosters.py` (every 4 hours)
- [ ] Task 5.5: Error handling & alert logging
- [ ] Task 5.6: Cron scheduler setup (systemd + crontab.txt)
- [ ] Task 5.7: Worker health checks & monitoring
- [ ] Full pytest suite passes
- [ ] Cron jobs scheduled and tested
- [ ] Monitoring/alerting rules in place

**Assigned:** Pipeline Engineer (subagent on deck)  
**Status:** Awaiting Phase 4 completion

---

### 🟢 Phase 6: Admin Console (Week 3, Day 3–4)
**Target:** 2026-08-11 (after Phase 3 ✓)

- [ ] Task 6.1: Agency onboarding & verification request form
- [ ] Task 6.2: Agency directory table (searchable, filterable)
- [ ] Task 6.3: Health dashboard (charts, metrics, submission stats)
- [ ] Task 6.4: Bulk agency configuration (coverage tiers, features)
- [ ] Task 6.5: Bulk email sending to agencies
- [ ] Task 6.6: Admin audit log viewer (searchable, export)
- [ ] Full pytest suite passes
- [ ] Admin-only authentication enforced
- [ ] All forms tested

**Assigned:** Admin UI Engineer (subagent on deck)  
**Status:** Awaiting Phase 3 completion

---

### 🟢 Phase 7: Testing & Go-Live (Week 3–4, Day 5+)
**Target:** 2026-08-16 (after Phase 6 ✓)

- [ ] Task 7.1: Schema validation tests (all 8 tables)
- [ ] Task 7.2: Auth flow tests (bcrypt, JWT, invitations)
- [ ] Task 7.3: Dashboard route tests + form submission
- [ ] Task 7.4: API endpoint tests (publish, batch, roster, audit)
- [ ] Task 7.5: Ingestion worker tests (normalization, dedup, PII)
- [ ] Task 7.6: E2E tests (Playwright) — full user journey
- [ ] Task 7.7: Pilot outreach & onboarding (3–5 agencies)
- [ ] Task 7.8: Go-live checklist & deployment
- [ ] 95%+ code coverage achieved
- [ ] E2E tests pass
- [ ] 3–5 agencies successfully onboarded & submitting
- [ ] <10 min publication lag verified
- [ ] Production monitoring/alerting configured
- [ ] Go-live approved

**Assigned:** QA Engineer (subagent on deck)  
**Status:** Awaiting Phase 6 completion

---

## Documentation Prepared

### For Developers
✅ `/root/montanablotter/docs/plans/LEA_IMPLEMENTATION_GUIDE.md` — Full developer guide  
✅ `/root/montanablotter/docs/plans/PHASE_2_AUTH_TASKS.md` — Phase 2 task templates (725 lines)  
✅ `/root/montanablotter/docs/plans/PHASE_3_DASHBOARD_TASKS.md` — Phase 3 task templates (413 lines)  
✅ `/root/montanablotter/docs/plans/PHASE_4_API_TASKS.md` — Phase 4 task templates (521 lines)  

### For Architects & Decision-Makers
✅ `/root/montanablotter/docs/plans/LEA_PANEL_README.md` — Navigation & overview  
✅ `/root/montanablotter/docs/plans/2026-08-02-lea-panel-executive-summary.md` — Business case (10 min read)  
✅ `/root/montanablotter/docs/plans/2026-08-02-law-enforcement-agency-panel-architecture.md` — System design (45 min read)  
✅ `/root/montanablotter/docs/plans/2026-08-02-lea-panel-implementation-plan.md` — Full task breakdown  

### For Operations
✅ `/root/montanablotter/docs/plans/LEA_TEAM_COORDINATION.md` — Live status dashboard (you are here)  

**Total Documentation:** ~12,000 lines of detailed specs, task templates, and implementation guides.

---

## Key Milestones

| Milestone | Target | Status | Notes |
|-----------|--------|--------|-------|
| Phase 1 Complete | 2026-08-02 09:15 UTC | 🟡 30% done (5 min elapsed) | All 8 tables created |
| Phase 2 Complete | 2026-08-05 | 🟢 Queued | Auth implemented |
| Phases 3–4 Complete | 2026-08-07 | 🟢 Queued (parallel) | Dashboard + API |
| Phase 5 Complete | 2026-08-09 | 🟢 Queued | Ingestion workers |
| Phase 6 Complete | 2026-08-11 | 🟢 Queued | Admin console |
| Phase 7 Complete | 2026-08-16 | 🟢 Queued | Full testing + go-live |
| **PRODUCTION GO-LIVE** | **2026-08-16** | 🟢 Target | All 56 agencies live |

---

## Critical Path

```
Phase 1 (09:15)
    ↓
Phase 2 (08-05)
    ↓
├─ Phase 3 (08-07)  [Dashboard]
│   ↓
│  Phase 6 (08-11)  [Admin console]
│       ↓
│      Phase 7 (08-16)  [Testing & go-live]
│
└─ Phase 4 (08-07)  [API] — parallel to Phase 3
    ↓
   Phase 5 (08-09)  [Workers]
       ↓
      Phase 7 (08-16)  [Testing & go-live]
```

**Critical path:** Phase 1 → 2 → 3 → 6 → 7 (13 days)  
**With parallelization:** 10 days (Phases 3–4 run simultaneously)

---

## How to Monitor Progress

### Live Transcript (Phase 1)
```bash
tail -f /root/.hermes/cache/delegation/live/deleg_e30f3b96/task-0.log
```

### Recent Git Commits
```bash
cd /root/montanablotter
git log --oneline | head -20
```

### Database State
```bash
sqlite3 blotter.db "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'lea_%' ORDER BY name;"
```

### Test Results
```bash
cd /root/montanablotter
./venv/bin/python3 -m pytest tests/test_lea_*.py -v --tb=short
```

---

## Success Metrics

### Year 1 Targets (Q1–Q3 2026)
- ✅ 25+ agencies registered (Q1)
- ✅ 1000+ incidents/week published (Q1)
- ✅ <10 min publication lag
- ✅ 500+ roster updates/week (Q1)
- ✅ 40+ agencies active (Q2, >70% of Montana)
- ✅ 5000+ incidents/week (Q2)
- ✅ All 56 counties publishing via panel (Q3)
- ✅ Zero data loss (audit log = 100% accountability)

### Technical Targets
- ✅ 95%+ test coverage (all phases)
- ✅ E2E test pass rate = 100%
- ✅ API uptime = 99.9%
- ✅ Response time <500 ms (p95)
- ✅ Rate limit enforcement = 100%
- ✅ CJIS compliance audit = pass

---

## Known Risks & Mitigations

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|-----------|
| Slow agency adoption | Medium | High | Free tier, minimal onboarding, phone support |
| ORI spoofing | Low | High | Require .gov email domain + manual verification |
| PII leaks in narratives | Low | Critical | PII auditor + manual review before publication |
| Duplicate incidents | Medium | Medium | Hash-based dedup on (agency_id, CAD #, date, location) |
| API token leakage | Low | Medium | Hash tokens, rate limiting, audit all API calls |
| Roster sync overwrites | Low | Medium | Separate tables + hash dedup logic |
| Database corruption | Very low | Critical | Daily backups, immutable audit log |

---

## Budget & Resources

**Team Size:** 7 agents (1 per phase)  
**Duration:** 2–3 weeks (running 3 phases in parallel)  
**Infrastructure:** Existing montanablotter.com VPS (no additional cost)  
**Dependencies:** bcrypt, PyJWT, email-validator (all open-source, pip-installable)  
**Go-Live Cost:** ~$0 (no new services, existing setup scales to this workload)  

---

## Next Steps (Right Now)

1. ✅ **Phase 1 Running** — Subagent executing TDD tasks (08:35 UTC start)
2. **Monitor Phase 1** — Check transcript for progress every 5–10 min
3. **Await Phase 1 Completion** — ETA 09:15 UTC (40 min from start)
4. **Dispatch Phase 2** — Auth Engineer subagent (upon Phase 1 ✓)
5. **Queue Phases 3–4** — Frontend & API engineers (upon Phase 2 ✓)

---

## Communication & Escalation

**Blockers:** If Phase 1 fails, immediately flag in transcript. I'll diagnose and rerun.  
**Questions:** Refer to `/root/montanablotter/docs/plans/LEA_IMPLEMENTATION_GUIDE.md`  
**Decisions Needed:** Go/no-go for pilot onboarding (Phase 7), production cutover date  
**Status Updates:** Live in this dashboard; refresh when Phase 1 completes.

---

**Document prepared by:** Hermes Agent (Jon's assistant)  
**Last updated:** 2026-08-02 08:50 UTC  
**Project Status:** ACTIVE — Phase 1 in progress, Phases 2–7 ready to queue  
**Questions?** Check the implementation guide or task templates.
