# Montana LEA Panel — Team Coordination Dashboard
**Status:** ACTIVE IMPLEMENTATION  
**Started:** 2026-08-02 08:35 UTC  
**Target Completion:** 2026-08-16 (2 weeks, 7 phases)  

---

## Team Roster

| Phase | Lead Role | Focus | Status | Assigned To |
|-------|-----------|-------|--------|-------------|
| Phase 1 | Backend Engineer | Database schema (8 tables, migrations) | 🟡 IN PROGRESS | Subagent-driven-development |
| Phase 2 | Auth Engineer | ORI verification, JWT tokens, invitations | 🟢 QUEUED | Ready to dispatch |
| Phase 3 | Frontend Engineer | Agency dashboard UI (forms, uploads, history) | 🟢 QUEUED | Ready to dispatch |
| Phase 4 | API Engineer | REST API (`/api/v1/lea/...`), rate limiting | 🟢 QUEUED | Ready to dispatch |
| Phase 5 | Pipeline Engineer | Cron workers (poll, normalize, ingest) | 🟢 QUEUED | Ready to dispatch |
| Phase 6 | Admin UI Engineer | Admin console, onboarding, health dashboard | 🟢 QUEUED | Ready to dispatch |
| Phase 7 | QA/Testing Engineer | Pytest, E2E tests (Playwright), pilot coordination | 🟢 QUEUED | Ready to dispatch |

---

## Phase 1: Database Schema & Migrations (Week 1)
**Target:** All 8 LEA tables created, indexed, migrated, tested  
**Current:** Live subagent executing Tasks 1.1–1.8  

### Tasks Breakdown

| Task | Objective | Input Commit | Output Commit | Status |
|------|-----------|--------------|---------------|--------|
| 1.1 | `lea_agencies` table + ORI indexes | — | `feat(lea): add lea_agencies table` | 🟡 Running |
| 1.2 | `lea_users` table + RBAC columns | 1.1 ✓ | `feat(lea): add lea_users table with roles` | 🟣 Pending |
| 1.3 | `lea_blotter_drafts` table + workflow | 1.2 ✓ | `feat(lea): add lea_blotter_drafts with status flow` | 🟣 Pending |
| 1.4 | `lea_roster_snapshots` table + indexes | 1.3 ✓ | `feat(lea): add lea_roster_snapshots table` | 🟣 Pending |
| 1.5 | `lea_api_tokens` table + hashing fields | 1.4 ✓ | `feat(lea): add lea_api_tokens with security` | 🟣 Pending |
| 1.6 | `lea_audit_log` table + immutable constraints | 1.5 ✓ | `feat(lea): add immutable lea_audit_log table` | 🟣 Pending |
| 1.7 | `lea_agency_coverages` table + feature flags | 1.6 ✓ | `feat(lea): add lea_agency_coverages feature flags` | 🟣 Pending |
| 1.8 | `lea_invitations` table + expiry logic | 1.7 ✓ | `feat(lea): add lea_invitations with TTL support` | 🟣 Pending |

**Commit Status:**
- Phase 1 completion expected: 08:50–09:15 UTC (est. 15 min per task)
- All 8 commits will be pushed to `main` branch

---

## Phase 2: Authentication (Week 2, Day 1)
**Target:** Bcrypt password hashing, JWT tokens, ORI verification, invite accept flow  
**Prerequisites:** Phase 1 ✓

### Tasks 2.1–2.7 (Ready to queue)
- 2.1: Bcrypt password hashing + verify functions
- 2.2: JWT token generation & expiry logic
- 2.3: ORI lookup + agency verification service
- 2.4: Invitation creation & acceptance flow
- 2.5: User session + login route
- 2.6: MFA TOTP setup & validation (optional)
- 2.7: Auth service integration tests

**Dispatch:** After Phase 1 completes (~09:20 UTC)

---

## Phase 3: Agency Dashboard (Week 2, Day 2–3)
**Target:** Web UI for incident submission, batch upload, history view  
**Prerequisites:** Phase 1 ✓, Phase 2 ✓

### Tasks 3.1–3.7 (Ready to queue)
- 3.1: Agency home dashboard template
- 3.2: Submit single incident form (Jinja2 + vanilla JS)
- 3.3: Batch CSV upload handler + parser
- 3.4: Blotter history table + filtering
- 3.5: API key management UI
- 3.6: Team management UI (invite, roles, deactivate)
- 3.7: Dashboard styling + responsive design

**Dispatch:** After Phase 2 completes

---

## Phase 4: REST API (Week 2, Day 4–5)
**Target:** Full `/api/v1/lea/...` endpoint suite  
**Prerequisites:** Phase 1 ✓, Phase 2 ✓

### Tasks 4.1–4.8 (Ready to queue)
- 4.1: `/api/v1/lea/auth/token` endpoint (username/password)
- 4.2: `/api/v1/lea/blotter/publish` endpoint
- 4.3: `/api/v1/lea/blotter/batch` multipart form handler
- 4.4: `/api/v1/lea/roster/sync` endpoint
- 4.5: Rate limiting + token middleware
- 4.6: CORS + security headers
- 4.7: API error responses + logging
- 4.8: API test suite (pytest + mocking)

**Dispatch:** After Phase 2 completes

---

## Phase 5: Ingestion Workers (Week 3, Day 1–2)
**Target:** Cron workers: `poll_lea_panel.py`, `normalize_records.py`, `ingest_lea_rosters.py`  
**Prerequisites:** Phase 1 ✓, Phase 4 ✓

### Tasks 5.1–5.7 (Ready to queue)
- 5.1: `poll_lea_panel.py` — fetch approved drafts every 15 min
- 5.2: `normalize_records.py` — MCA validation + geocoding
- 5.3: PII auditor integration + redaction
- 5.4: `ingest_lea_rosters.py` — dedup + jail_bookings insertion
- 5.5: Error handling + alert logging
- 5.6: Cron scheduler setup (systemd timer + crontab.txt)
- 5.7: Worker health checks + monitoring

**Dispatch:** After Phase 4 completes

---

## Phase 6: Admin Console (Week 3, Day 3–4)
**Target:** `/admin/lea-management` — agency onboarding, verification, health dashboard  
**Prerequisites:** Phase 1 ✓, Phase 2 ✓, Phase 3 ✓

### Tasks 6.1–6.6 (Ready to queue)
- 6.1: Agency onboarding + verification request form
- 6.2: Agency directory table (name, county, type, status, users)
- 6.3: Health dashboard (charts, metrics, submission/roster stats)
- 6.4: Bulk agency configuration (coverage tiers, feature flags)
- 6.5: Bulk email sending to agencies
- 6.6: Admin audit log viewer

**Dispatch:** After Phase 3 completes

---

## Phase 7: Testing & Go-Live (Week 3, Day 5 → Week 4)
**Target:** Full pytest suite, E2E tests, pilot with 3–5 agencies  
**Prerequisites:** All phases 1–6 ✓

### Tasks 7.1–7.8 (Ready to queue)
- 7.1: Schema validation tests (all 8 tables)
- 7.2: Auth flow tests (bcrypt, JWT, invitations)
- 7.3: Dashboard route tests + form submission
- 7.4: API endpoint tests (publish, batch, roster, audit)
- 7.5: Ingestion worker tests (normalization, dedup, PII audit)
- 7.6: E2E tests (Playwright) — full user journey
- 7.7: Pilot outreach + onboarding (3–5 agencies)
- 7.8: Go-live checklist + deployment

**Dispatch:** After Phase 6 completes

---

## Real-Time Progress Log

### 2026-08-02 08:35 UTC
- **Phase 1 Kicked Off** — Subagent dispatched to execute Tasks 1.1–1.8
- Transcript: `/root/.hermes/cache/delegation/live/deleg_e30f3b96/task-0.log`
- Expected completion: ~09:15 UTC (40 min for 8 tasks)

### Checkpoints (Live Updates)
- [ ] Phase 1 Task 1.1 ✓ (08:45 est)
- [ ] Phase 1 Task 1.2 ✓ (08:50 est)
- [ ] Phase 1 Task 1.3 ✓ (08:55 est)
- [ ] Phase 1 Task 1.4 ✓ (09:00 est)
- [ ] Phase 1 Task 1.5 ✓ (09:05 est)
- [ ] Phase 1 Task 1.6 ✓ (09:10 est)
- [ ] Phase 1 Task 1.7 ✓ (09:12 est)
- [ ] Phase 1 Task 1.8 ✓ (09:15 est)
- [ ] Phase 1 Pytest Full Suite ✓ (09:18 est)
- [ ] Phase 1 Complete → Dispatch Phase 2 (09:20 est)

---

## Key Milestones

| Milestone | Target Date | Status |
|-----------|-------------|--------|
| **Phase 1 Complete** | 2026-08-02 09:15 | 🟡 In progress |
| **Phase 2 Complete** | 2026-08-05 | 🟢 Ready |
| **Phases 3–4 Complete** | 2026-08-07 | 🟢 Ready |
| **Phase 5–6 Complete** | 2026-08-09 | 🟢 Ready |
| **Phase 7 (Testing)** | 2026-08-11 | 🟢 Ready |
| **Pilot with 3–5 agencies** | 2026-08-13 | 🟢 Ready |
| **Go-Live** | 2026-08-16 | 🟢 Ready |

---

## Communication Channels

**Status Updates:**
- Each phase completion → message to this dashboard
- Task failures → logged immediately with rollback plan
- Blockers → escalate to Jon

**Monitoring:**
- Live transcripts: `/root/.hermes/cache/delegation/live/deleg_*/task-*.log`
- Git commits: `git log --oneline | head -20`
- Test results: `pytest --tb=short` (final phase 7)

---

## Rollback Plan

If any task fails:
1. **Immediate:** Log error + context to `/root/montanablotter/logs/lea_impl.log`
2. **Diagnosis:** Read transcript, identify root cause
3. **Repair:** Create patch or reimplement task
4. **Retry:** Re-run task from failed step
5. **Escalate:** If 2+ retries fail, flag for Jon review

---

## Next Steps

1. ✅ **Phase 1 Running** — Subagent is executing now
2. **Await Phase 1 Completion** → All 8 tables created, indexed, tested
3. **Dispatch Phase 2** → Immediately after Phase 1 ✓
4. **Parallel Phases?** → If needed, can run Phases 3–4 in parallel after Phase 2

---

**Document Updated:** 2026-08-02 08:35 UTC  
**Prepared by:** Hermes Agent (Jon's assistant)  
**Next Review:** When Phase 1 completes
