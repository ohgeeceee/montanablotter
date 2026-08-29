---
title: "County Coverage Expansion Tracker — 2026-08-11"
date: 2026-08-11
status: active
phase: "Foundation infrastructure complete; Implementing parsers per county"
---

# Montana Blotter County Coverage Expansion Tracker

## Objective
Expand jail roster and police blotter coverage from 35 → 56 Montana counties, with 180-day historical depth for each source.

## Completed Infrastructure (Shipping)

| Item | Status | Location | Notes |
|---|---|---|---|
| **Warrant ingestion** | ✅ Active | `warrant_ingest.py` + cron | 6793 warrants, runs every 6h (0, 6, 12, 18 UTC) |
| **Police backfill runner** | ✅ Ready | `scripts/backfill_police_blotters.py` | Supports `--days`, `--source`, `--dry-run` |
| **6 jail adapters (stub)** | ✅ Wired | `services/ingestion/fetchers/{dawson,granite,mineral,phillips,pondera,powell}_inmate.py` | All 6 callable, registered in dispatcher |
| **3 police blotter stubs** | ✅ Ready | `services/ingestion/fetchers/{billings,helena,great_falls}.py` | Awaiting endpoint discovery |
| **Slug-drift cleanup** | ✅ Done | `jail_booking_sources` | Lewis-and-Clark + Silver Bow ghosts disabled |
| **Bozeman 180d backfill** | ✅ Live | Records table | +138 net new, Feb 12—Aug 11 span |

## Current County Roster Status (35 counties tracked)

### Tier 1: Major counties (Actively ingesting)
- Flathead, Missoula, Yellowstone, Lewis-and-Clark, Gallatin, Hill (email), Silver Bow

### Tier 2: Standard (Mostly working)
- Jefferson, Ravalli, Madison, Stillwater, Meagher, Wheatland, Roosevelt, Beaverhead, Big Horn, Fallon, Fergus, Glacier, Park, Lincoln, Rosebud, Custer

### Tier 3: New adapters (Stub → needs parser)
- **Dawson**, **Granite**, **Mineral**, **Phillips**, **Pondera**, **Powell**

### Tier 4: Stale/Blocked (Requires recovery)
- **Sanders** (DNS/SSL, stale since 2026-06-14)
- **Cascade** (SharePoint auth wall, last 2026-06-23)
- **Broadwater** (Timeout, last 2026-07-29)
- **Lake** (Disabled, disabled since 2026-08-10)

### Tier 5: Not in system (21 counties)
- Blaine, Daniels, Deer Lodge, Garfield, Golden Valley, Judith Basin, Liberty, McCone, Musselshell, Petroleum, Powder River, Prairie, Richland, Sheridan, Sweet Grass, Teton, Toole, Treasure, Wibaux

---

## Research Tracker — 6 New Adapters

Awaiting roster URL discovery. Update status as endpoints are found.

| County | Current URL | Status | Roster Type | Parser Note |
|---|---|---|---|---|
| Dawson | https://www.co.dawson.mt.us | 🔍 Unknown | — | — |
| Granite | https://www.co.granite.mt.us | 🔍 Unknown | — | — |
| Mineral | https://www.co.mineral.mt.us | 🔍 Unknown | — | — |
| Phillips | https://www.phillipscosheriff.com | 🔍 Unknown | — | — |
| Pondera | https://www.co.pondera.mt.us | 🔍 Unknown | — | — |
| Powell | https://www.co.powell.mt.us | 🔍 Unknown | — | — |

**Legend**: 
- 🔍 Unknown = awaiting discovery
- ✅ Found = roster located, ready for parser
- 🔨 Parser Draft = implementation in progress
- ✅ Live = parsing + ingesting

---

## Police Blotter Expansion — 3 Cities

| City | Current Source | Endpoint | Status | Notes |
|---|---|---|---|---|
| Billings | Granicus portal | https://www.billingsmt.gov | 🔍 Discover | Test `/Police/Calls-for-Service` + `/api/v1/police/calls` patterns |
| Helena | Granicus/CivicPlus | https://www.helenamt.gov/police | 🔍 Discover | Helena PD has public police section; find dispatch/incident feed |
| Great Falls | Granicus portal | https://www.greatfallsmt.gov | 🔍 Discover | Test Granicus endpoint patterns |

**Backfill candidates** (if APIs support date ranges):
- Missoula: 135 docs (already active; extend to 180d)
- Whitefish: 151 docs (already active; extend to 90d if archive available)

---

## Stale Major County Recovery Plan

| County | Issue | Last Success | Blocker | Recovery Path |
|---|---|---|---|---|
| Sanders | DNS/SSL timeout | 2026-06-14 (58 days) | Host offline or blocking | Probe URL; contact sheriff if cert expired |
| Cascade | SharePoint auth | 2026-06-23 (49 days) | PDF behind sign-in | Use daily-email workaround (`scripts/ops/cascade_roster_email.py`) |
| Broadwater | Network timeout | 2026-07-29 (13 days) | Host latency | Retry with longer timeout (30s → 60s) |
| Lake | Disabled | 2026-08-10 | Policy unknown | Re-enable and test; may be stale by design |

---

## Next Immediate Actions (Priority Order)

### P0 — This week
1. **Research 6 new adapters** — Probe sheriff websites for public roster endpoints (HTML, PDF, API)
2. **Implement 2–3 parsers** — Start with highest-probability roster URLs
3. **Stale recovery** — Retry Broadwater + attempt Sanders/Cascade
4. **Warrant monitoring** — Confirm cron is healthy (already active)

### P1 — Next 2 weeks
1. **Billings/Helena/Great Falls** — Identify Granicus endpoints
2. **Full 180d backfill** — Missoula + Whitefish
3. **Wire stale-county recovery** into daily/weekly health-check cron

### P2 — Backlog
1. **Tier 5 counties** (21 counties) — Research if public rosters exist; many small counties may have none
2. **Court case backfill** — Currently 399 records; expand via `services/court/refresh.py`
3. **Civil filings** — 6 records; establish source onboarding
4. **Warrant depth** — 6,793 current; backfill historical if vendors support it

---

## Data Volume Metrics (Current)

| Source | Records | County Coverage | Depth | Status |
|---|---|---|---|---|
| Blotter incidents | 51,085 | 16 active sources | 7 days (free) / 12 months (Plus) / full (Pro) | ✅ Live |
| Jail bookings | 10,628 | 35 registered, ~21 active | Current + 90d historical | ⚠️ Mixed (stale counties exist) |
| Warrants | 6,793 | Statewide (30+ counties) | Recent PDFs only | ✅ Cron active |
| Court cases | 399 | Statewide | Limited | ❌ Backlog |
| Civil filings | 6 | 1–2 counties | Sample only | ❌ Backlog |
| Public meetings | 478 | Statewide | Recent | ✅ Cron active |

---

## Git Status (Pending Commit)

**Modified:**
- `services/ingestion/jail_bookings.py` (+6 adapters, +6 TRACKED_SOURCES, +6 dispatch branches)
- `crontab.txt` (no changes, warrants already cron'd)

**New files:**
- `services/ingestion/fetchers/{dawson,granite,mineral,phillips,pondera,powell}_inmate.py` (6 stub adapters)
- `services/ingestion/fetchers/{billings,helena,great_falls}.py` (3 police blotter stubs)
- `scripts/backfill_police_blotters.py` (180d backfill runner)

---

## Validation Checklist

- [x] 6 adapters registered in `SUPPORTED_ADAPTERS`
- [x] 6 adapters live in `TRACKED_SOURCES`
- [x] 6 adapters dispatched in `_fetch_records_for_source()`
- [x] `--county <slug> --dry-run` callable for all 6
- [x] Syntax check: all new `.py` files parse
- [x] Warrant cron active + ingesting
- [x] Bozeman backfill verified (+138 records)
- [x] Slug-drift ghosts disabled
- [ ] Stale-county recovery dry-runs complete
- [ ] Docs published (this file)

---

## References

- **Jail-bookings skill**: `/root/.hermes/skills/devops/jail-bookings-fetchers/SKILL.md`
- **Source-coverage skill**: `/root/.hermes/skills/devops/montanablotter-source-coverage-analyst/SKILL.md`
- **Backfill pattern**: `scripts/backfill_police_blotters.py` (180d template)
- **Cascade workaround**: `scripts/ops/cascade_roster_email.py` (daily email fallback)
- **Crontab**: `crontab.txt` (line 55 = warrant_ingest every 6h)
- **Status snapshot**: `docs/montana_jail_roster_gap_contacts.csv` (agency research log)

---

Last updated: 2026-08-11 20:30 UTC
Next review: 2026-08-18 (one week)
Curator: Hermes Agent
