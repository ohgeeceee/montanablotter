# Montana Blotter LEA Panel — Executive Summary

## What We're Building

A **free, zero-friction web panel** for every Montana sheriff office, police department, and county detention center to manage and publish their daily police blotters and jail rosters. Everything they submit automatically appears on montanablotter.com with no manual intervention from our team.

---

## The Problem We're Solving

1. **Fragmentation:** Montana's 56 counties publish blotters and rosters via 56 different methods:
   - Some email PDFs to news outlets (not indexed, not searchable).
   - Some post static HTML on old websites (hard to parse, inconsistent formats).
   - Some run proprietary JMS systems (no public API).
   - Some do nothing (data goes dark).

2. **Operational burden on Montana Blotter:**
   - Manual email ingestion from each county.
   - Custom parsers for each unique format.
   - Data often 3–7 days delayed.
   - Deduplication headaches when counties sync data multiple times.

3. **Lost transparency for the public:**
   - People can't search all of Montana's records in one place.
   - Journalists spend hours scraping county websites.
   - No standardized, current, reliable feed.

---

## The Solution

A **multi-tenant SaaS platform** hosted on the Montana Blotter VPS:

- **Agency Dashboard** (`/panel/<county>/`):
  - Officers enter or upload incidents in real-time.
  - Jail rosters sync automatically from county JMS systems via API.
  - One-click publish to montanablotter.com.

- **REST API** (`/api/v1/lea/...`):
  - Existing RMS/CAD systems can POST directly (no UI needed).
  - Programmatic roster syncs (webhook-compatible).
  - Token-based authentication for third-party integrations.

- **Automatic Ingestion**:
  - Cron workers normalize records (MCA code validation, geocoding, PII redaction).
  - Records published to public `/jail-bookings`, `/incidents` within 10 minutes.
  - Full audit trail (CJIS-compliant).

- **Admin Console** (`/admin/lea-management`):
  - Verify agencies (ORI lookup, government email domain check).
  - Monitor submission health across all counties.
  - One-stop dashboard for Montana Blotter ops team.

---

## Revenue & Impact

### Business Value
- **Zero acquisition cost:** Agencies are mandated to publish (it's law). We're offering them an free tool they need.
- **Sticky platform:** Once adopted by a county, they're unlikely to switch (low friction, no competitor).
- **Data moat:** Montana Blotter becomes the single source of truth for all Montana LE records → higher traffic, better ad targeting, premium features (e.g., warrant access, cross-state search).
- **Monetization path:** Premium features (advanced analytics, private case notes, warrant access) for larger agencies.

### Public Impact
- **Daily real-time transparency:** All Montana police blotters + jail rosters updated in one place.
- **Searchable, syndicated:** RSS feeds, APIs, Telegram bots, etc. → information spreads fast.
- **Institutional knowledge:** 10 years of records archived and indexed (right now, county websites delete old data).

---

## Technical Architecture (TL;DR)

```
┌─────────────────────────────────────────────────────────────────────┐
│ Public Internet                                                     │
│ ┌──────────────────┐                                                │
│ │ Agency Officer   │                                                │
│ │ logs into panel  │                                                │
│ │ /panel/cascade/  │                                                │
│ └────────┬─────────┘                                                │
│          │                                                          │
│          ▼                                                          │
│ ┌──────────────────────────────────────────────────────────────┐   │
│ │ LEA Panel (this project)                                     │   │
│ │                                                               │   │
│ │ - Agency Dashboard (submit incidents, upload CSVs)           │   │
│ │ - REST API (/api/v1/lea/...)                                │   │
│ │ - Multi-tenant SQLite tables (lea_agencies, lea_users, ...) │   │
│ │ - Audit logging (CJIS-compliant)                            │   │
│ │                                                               │   │
│ │ Cron Workers:                                                │   │
│ │ - poll_lea_panel (fetch new submissions every 15 min)       │   │
│ │ - normalize_records (validate, geocode, PII-check)          │   │
│ │ - ingest_lea_rosters (sync → jail_bookings)                │   │
│ └────────┬─────────────────────────────────────────────────────┘   │
│          │                                                          │
│          ▼                                                          │
│ ┌──────────────────────────────────────────────────────────────┐   │
│ │ Montana Blotter Public (montanablotter.com)                  │   │
│ │                                                               │   │
│ │ /jail-bookings                                              │   │
│ │ /incidents / search                                          │   │
│ │ RSS feeds                                                    │   │
│ │ JSON API                                                     │   │
│ └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

**Key Design Decisions:**
- **Additive, not disruptive:** New tables + blueprints. Existing public infrastructure unchanged.
- **Offline-first drafts:** Records staged in `lea_blotter_drafts` until approved for publication (prevents accidental publish).
- **Worker-driven publishing:** Cron tasks handle normalization (MCA codes, geocoding), not real-time HTTP (faster, more reliable).
- **Audit-first compliance:** Every action logged immutably (CJIS requirement).
- **Multi-tenant RLS:** Each agency only sees its own records; verified via user.agency_id checks.

---

## Database Schema (Core Tables)

| Table | Purpose |
|-------|---------|
| `lea_agencies` | Agency registry (org name, ORI #, county, verification status) |
| `lea_users` | Users per agency (email, role, MFA support) |
| `lea_invitations` | Pending user invites (email-based onboarding) |
| `lea_blotter_drafts` | Staging area for submitted incidents (draft → approved → published) |
| `lea_roster_snapshots` | Jail roster snapshots with dedup hash |
| `lea_api_tokens` | API keys for programmatic access (hashed, rate-limited) |
| `lea_audit_log` | Immutable action log (CJIS-compliant) |
| `lea_agency_coverages` | Feature flags per agency (blotter tier, roster tier, CAD/RMS support) |

**Integration with existing public tables:**
- Published incidents → `records` (existing public table).
- Published rosters → `jail_bookings` (existing public table).
- No schema changes to production data.

---

## API Endpoints (Public REST Interface)

```
POST /api/v1/lea/auth/token
  → Get JWT access token

POST /api/v1/lea/blotter/publish
  → Submit single incident (JSON)

POST /api/v1/lea/blotter/batch
  → Upload batch (CSV, JSON, or PDF)

GET /api/v1/lea/blotter/batch/<batch_id>/status
  → Poll processing status

POST /api/v1/lea/roster/sync
  → Incremental jail roster update (webhook-compatible)

GET /api/v1/lea/roster/snapshot
  → Export current roster as JSON/CSV

GET /api/v1/lea/audit?action=<action>&days=30
  → Audit log (read-only)
```

All endpoints require:
- **Session auth** (web UI) — Flask session cookie + CSRF token
- **Token auth** (API) — `Authorization: Bearer <token>` header

---

## Rollout Timeline

| Phase | Duration | Output |
|-------|----------|--------|
| 1. Database schema + migrations | 1 week | LEA tables in prod, `init_db.py` updated |
| 2. Auth + user management | 1 week | Password hashing, ORI verification, invitations |
| 3. Dashboard + blotter UI | 1 week | Agency panel routes, incident submission form, CSV upload |
| 4. REST API | 1 week | `/api/v1/lea/` endpoints, token auth, rate limiting |
| 5. Ingestion workers | 1 week | `poll_lea_panel.py`, `normalize_records.py`, `ingest_lea_rosters.py` |
| 6. Admin console | 1 week | `/admin/lea-management`, verification workflow, health dashboard |
| 7. Testing + pilot | 1 week | Pytest coverage, E2E tests, 3–5 agency pilot |
| **Total** | **7 weeks** | **Production-ready LEA panel + ingestion pipeline** |

---

## Success Metrics

**Q1 2026:**
- ✅ 25+ agencies registered
- ✅ 1000+ incidents/week published via panel
- ✅ 500+ jail roster updates/week synced
- ✅ Zero data loss (audit log = 100% accountability)

**Q2 2026:**
- ✅ 40+ agencies (>70% of Montana)
- ✅ 5000+ incidents/week
- ✅ 85% of rosters real-time (vs 60% today)
- ✅ <10 min publication lag (vs 3–7 days today)

**Q3+ 2026:**
- ✅ All 56 Montana counties publishing via panel
- ✅ Third-party integrations (SMS alerts, Telegram bot, etc.)
- ✅ Premium features for large agencies (advanced analytics, warrant access)

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Agencies slow to adopt | Medium | Revenue impact | Free tier, minimal onboarding, phone support |
| ORI verification fails | Low | Spoofing | Require gov email domain (more effective than ORI check) |
| PII leaks in narrative text | Low | Legal liability | PII auditor + manual review before publication |
| Roster dedup fails (duplicates in public view) | Medium | Trust erosion | Hash-based dedup + visual dedupe in UI |
| API token leakage | Low | Unauthorized access | Hash tokens on storage, rate limiting, log all API calls |

---

## Competitive Advantage

**Why Montana Blotter wins with this:**

1. **First-mover advantage:** Only unified LEA publishing platform in Montana.
2. **Zero friction for agencies:** Free (no license), no training, no IT setup.
3. **Network effect:** More agencies → better data → higher public traffic → more ad revenue.
4. **Data moat:** We become the canonical source for Montana LE records (hard for competitors to replicate 10+ years of archives).
5. **Regulatory tailwind:** Transparency initiatives + FOIA pressure push agencies to publish (we're offering the easiest path).

---

## Next Steps

1. **Start Phase 1** (Database schema): 1 week to add all LEA tables to `init_db.py`.
2. **Parallel track:** Reach out to 5–10 largest agencies (Great Falls PD, Billings PD, Helena PD, County Sheriffs) for pilot feedback.
3. **Go live Phase 3 (Dashboard):** After auth is solid, agencies can start using the web UI.
4. **Measure & iterate:** Track adoption, submission volume, publication lag, error rates.

---

## Questions?

- **Can I run this offline?** Yes — all workers are cron-based, no real-time streaming. Works on slow internet.
- **What if an agency runs a proprietary RMS?** We provide REST API + CSV import. If they want webhook, we can build a custom adapter (upsell opportunity).
- **What about juvenile records / sealed cases?** Audit log + PII auditor flag for manual review. We never auto-publish juvenile records.
- **How do we handle corrections?** Officers can edit drafts before publishing. After publish, corrections go through a separate amendment workflow (audited).

---

**Document prepared by:** Montana Blotter Ops
**Date:** 2026-08-02
**Status:** Ready for implementation kickoff
