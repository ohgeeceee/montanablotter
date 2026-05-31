# Montana Blotter — Full Source Coverage Audit Baseline Report
**Date:** 2026-05-21  
**Auditor:** blotter-ops  
**Scope:** jail rosters, police blotters, calls-for-service, PDF ingest, email ingest, civil filings, agendas, CrimeMapping  
**Classification:** Working / Broken / Configured but disabled / Not configured

---

## Executive Summary

| Category | Working | Broken | Configured but disabled | Not configured |
|---|---|---|---|---|
| Jail Rosters | 6 | 11 | 9 | 24+ |
| Police Blotters / Calls | 5 | 3 | 4+ | Many |
| Civil / Courts / Agendas | 2 | 2 | 0 | — |
| Auxiliary (Sex Offender, MHP, etc.) | 2 | 0 | 0 | — |

**Critical gaps:**
1. CrimeMapping fetcher is completely dead for all 8 Montana agencies (global endpoint failure).
2. Rosebud PDF fetcher crashes every run (`ModuleNotFoundError: config`) — trivial PYTHONPATH fix.
3. `run_all_scrapers.py` and `email_blotter_ingest.py` are absent from the crontab, leaving `mt_blotter.py` sources and legacy email text ingest dormant.
4. Anthropic API credit exhaustion is degrading Bozeman digest quality (fallback mode).
5. 11 Zuercher-portal counties are enabled but return API 404; 9 counties are explicitly disabled in DB.

---

## 1. Jail Roster Sources

### 1.1 DB State (`jail_booking_sources`)

32 counties tracked. 6 major tier, 26 standard tier.

**Working (recent successful fetch with >0 bookings)**
| County | Tier | Last Success | Bookings (7d) | Notes |
|---|---|---|---|---|
| yellowstone | major | 2026-05-21 12:21 | 706 | ASP form parser; healthy |
| missoula | major | 2026-05-21 12:52 | 78 | HTML table; healthy |
| flathead | major | 2026-05-21 12:05 | 45 | HTML table; healthy |
| jefferson | major | 2026-05-21 12:15 | 4 | Zuercher API; healthy |
| sanders | major | 2026-05-21 12:35 | — | publiclogs.com; healthy |
| cascade | standard | 2026-05-21 12:04 | 0 | CivicPlus page parsed but **no PDF link found** every run |

**Broken (enabled=1, in SUPPORTED_ADAPTERS, but fetching 0 or erroring)**
| County | Tier | Issue | Evidence |
|---|---|---|---|
| gallatin | major | Zuercher portal maintenance mode | `latest_error`: "Zuercher portal is in maintenance mode as of 2026-05-21." |
| broadwater | standard | Host timeout from VPS | `latest_error`: "Official roster host is timing out from the ingest machine." |
| carbon | standard | Zuercher API 404 | `latest_error`: "Zuercher API endpoint not found" |
| madison | standard | Zuercher API 404 | `latest_error`: "GET /api/public/inmate/criteria returns 404" |
| meagher | standard | Zuercher API 404 | Same as Madison |
| ravalli | standard | Zuercher API 404 / 0 fetched | `latest_error` says API not found; last run fetched 0 |
| roosevelt | standard | Zuercher API 404 | Same pattern |
| rosebud | standard | Zuercher API 404 | Same pattern |
| stillwater | standard | Zuercher API 404 | Same pattern |
| valley | standard | Zuercher API 404 | Same pattern |
| wheatland | standard | Zuercher API 404 | Same pattern |

**Configured but disabled (`is_enabled=0`)**
| County | Tier | Last Checked | Notes |
|---|---|---|---|
| custer | standard | 2026-03-18 | Quiet since March; no URL configured |
| hill | standard | 2026-03-18 | Quiet since March; no URL configured |
| lincoln | standard | 2026-03-18 | Quiet since March; no URL configured |
| madison | standard | 2026-05-21 09:27 | Disabled but still probed by `jail_booking_ingest_all`? No — `is_enabled=0` skips them |
| meagher | standard | 2026-05-21 09:27 | Same |
| roosevelt | standard | 2026-05-21 09:27 | Same |
| rosebud | standard | 2026-05-21 09:27 | Same |
| stillwater | standard | 2026-05-21 09:27 | Same |
| wheatland | standard | 2026-05-21 09:27 | Same |

Wait — correction: the `jail_booking_ingest_all` run at 12:04 processed beaverhead, big-horn, broadwater, carbon, cascade, dawson, fergus, glacier, granite, lewis-and-clark, mineral, park, phillips, pondera, powell, ravalli, silver-bow, valley. It did **not** process custer/hill/lincoln because those are `is_enabled=0` with NULL URLs. The 9 counties above with `is_enabled=0` and recent `last_checked_at` appear to have been probed anyway — possibly by `source_discovery` or an older run. The DB shows mixed state.

**Not configured (no row in `jail_booking_sources`)**
Lake, Judith Basin, Golden Valley, Deer Lodge, Silver Bow (as jail roster; silver-bow has a URL but no adapter), Granite (URL present but no adapter), Park, Phillips, Pondera, Powell, Dawson, Fergus, Glacier, Beaverhead, Big Horn, Lewis and Clark, Mineral — these have DB rows with `is_enabled=1` but are **not in `SUPPORTED_ADAPTERS`**, so they are technically "configured but not supported" rather than "not configured."

### 1.2 Code State (`SUPPORTED_ADAPTERS`)

```python
SUPPORTED_ADAPTERS = {
    "broadwater", "cascade", "carbon", "flathead", "gallatin",
    "jefferson", "madison", "meagher", "missoula", "ravalli",
    "roosevelt", "rosebud", "sanders", "stillwater", "valley",
    "wheatland", "yellowstone"
}
```

17 counties claim adapter support. Of those:
- 5 are actually fetching data (flathead, jefferson, missoula, sanders, yellowstone)
- 1 fetches 0 because the source has no PDF link (cascade)
- 1 is skipped due to maintenance (gallatin, via `SKIPPED_SOURCES`)
- 1 is skipped due to timeout (broadwater, via `SKIPPED_SOURCES`)
- 9 return Zuercher 404 (carbon, madison, meagher, ravalli, roosevelt, rosebud, stillwater, valley, wheatland)

### 1.3 Crontab Coverage

| County | Schedule | Status |
|---|---|---|
| yellowstone | every 2h | ✅ scheduled |
| missoula | every 2h | ✅ scheduled |
| flathead | every 2h | ✅ scheduled |
| jefferson | every 2h | ✅ scheduled |
| sanders | every 2h | ✅ scheduled |
| ravalli | every 2h | ✅ scheduled |
| all remaining (all.py) | every 4h | ✅ scheduled |

**Crontab is complete** for jail sources. No gaps.

### 1.4 Recent Booking Volume (7 days)

```
yellowstone  | 706
missoula     |  78
ravalli      |  53
flathead     |  45
jefferson    |   4
```

Ravalli shows 53 bookings in the 7-day SQL but 0 in the most recent single run. This suggests bookings were loaded earlier in the week and the Zuercher API stopped responding more recently (latest run fetched 0).

---

## 2. Police Blotter / Calls-for-Service Sources

### 2.1 Working

| Source | Type | Schedule | Last Activity | Notes |
|---|---|---|---|---|
| bozeman_calls | ArcGIS REST | every hour | 2026-05-21 12:40 | 175 rows today; **Anthropic billing error** causes fallback digest |
| bozeman_crime | ArcGIS REST | every 6h | 2026-05-21 12:55 | 49 rows today; healthy ingest |
| missoula_public_report | HTML table | every hour | 2026-05-21 13:10 | 145 rows; healthy |
| whitefish_pdf | PDF scrape | every 6h | 2026-05-21 12:25 | Skipping published docs; no new PDFs |
| email_worker | IMAP queue | every 15 min | ongoing | polls for image attachments |
| email_image_blotter | IMAP images | :07,:22,:37,:52 | ongoing | processes image attachments |

### 2.2 Broken

| Source | Type | Issue | Evidence |
|---|---|---|---|
| crimemapping_fetcher | JSON API | **All 8 agencies fail** with Non-JSON response | Carbon County, Red Lodge PD, Bridger PD, Chouteau County Sheriff, etc. all return `Expecting value: line 1 column 1` — endpoint changed or blocked |
| roasebud_pdf_fetcher | PDF scrape | `ModuleNotFoundError: No module named 'config'` | Every run at 08:30 crashes with exit_code=1. Script lacks `PYTHONPATH=/root/montanablotter` |
| run_all_scrapers.py | Unified scraper | **Not in crontab** | File exists at `ingestion/run_all_scrapers.py` but never scheduled. `scraped_records` table empty for last 7 days. |
| email_blotter_ingest.py | IMAP text | **Not in crontab** | File exists but `email_worker` and `email_image_blotter` are the only email jobs scheduled. Legacy text-based email ingest is dormant. |

### 2.3 Configured but disabled (`mt_blotter.py`)

From `services/ingestion/fetchers/mt_blotter.py`:

```
gallatin_jail    → enabled=False (JS-rendered, needs Playwright)
yellowstone_jail → enabled=False (ASP form-based, needs POST)
cascade_jail     → enabled=False (No public roster found yet)
lewis_clark_jail → enabled=False (No public roster found yet)
```

Additionally, numerous city PD blotters inside `mt_blotter.py` are marked `enabled=False` because they need custom parsers. The scraper framework exists but `run_all_scrapers.py` is not scheduled, so even the enabled sources never run.

### 2.4 Blotter Activity (last 30 days)

```
County              | Blotters | Last Upload
--------------------|----------|----------------
Gallatin            |   42     | 2026-05-21 12:40
Flathead            |   32     | 2026-05-20 19:10
Missoula            |   30     | 2026-05-21 10:10
Yellowstone         |   16     | 2026-05-20 18:00
Hill                |   13     | 2026-05-21 10:11
Jefferson           |    6     | 2026-05-18 14:07
Lewis and Clark     |    6     | 2026-05-14 16:22
Unknown             |    3     | 2026-05-11 20:00
Cascade             |    2     | 2026-04-26 18:00
Rosebud             |    1     | 2026-05-21 07:00
```

Only **10 counties** produced blotters in the last 30 days. The `records` table (legacy) shows only Flathead and Gallatin activity in the same window, confirming the pipeline has shifted to `blotters` → `posts` and `records` is stale.

---

## 3. Civil Filings & Courts

| Source | Schedule | Status | Evidence |
|---|---|---|---|
| court_refresh | every 3h | ⚠️ degraded | `pubcourts.mt.gov` portals hit `ERR_CONNECTION_RESET` (IP-level block) — see Pattern C5 |
| icourtcase_civil_ingest | daily 05:20 | ⚠️ degraded | Same WAF block pattern |
| civil_filing_source_alerts | :11,:41 | ✅ scheduled | checks staleness; may alert on the above |
| license_sanction_ingest | Mon 03:00 | ✅ scheduled | low volume; no errors noted |

---

## 4. Auxiliary Sources

| Source | Schedule | Status | Evidence |
|---|---|---|---|
| sex_offender_daily | 04:35,16:35 | ✅ working | no errors in recent logs |
| sex_offender_source_alerts | every 6h | ✅ working | no errors |
| mhp_crashes | daily 08:00 | ✅ scheduled | not probed this session |
| agendas_ingest | every 6h | ✅ scheduled | municipal agendas; no errors noted |
| meeting_source_alerts | :14,:44 | ✅ scheduled | no errors noted |
| missing_person_watch | hourly | ✅ scheduled | no errors noted |

---

## 5. OpenClaw Agents

| Agent | Schedule | Status | Evidence |
|---|---|---|---|
| openclaw_reporter | hourly | ⚠️ degraded | Discord 404 errors in log; may be failing to deliver. Agent finishes exit_code=0 but output routing broken. |
| openclaw_clerk | hourly | ✅ scheduled | not probed this session |
| openclaw_publisher | hourly | ✅ scheduled | not probed this session |

---

## 6. Data Quality & Infrastructure Risks

### 6.1 Anthropic API Billing Exhaustion
- **Impact:** Bozeman calls fallback digest (low quality), Claude audit fails.
- **Evidence:** `HTTP/1.1 400 Bad Request` — `Your credit balance is too low to access the Anthropic API`
- **Remediation:** Add credits at console.anthropic.com or switch model to a provider with balance.

### 6.2 SQLite Write-Lock / DB Health
- `PRAGMA journal_mode` not checked this session, but no `database is locked` errors seen in recent logs.
- DB size: ~12 GB. No rapid growth signals.

### 6.3 Backup Health
- `backup_db` scheduled daily at 02:00 with 18-hour timeout. No timeouts noted in this session.

---

## 7. Exact Remediation Commands

### 7.1 Fix Rosebud PDF fetcher (Yellow tier — do then report)
```bash
# Backup crontab first
crontab -l > /root/montanablotter/crontab.txt.bak.$(date +%Y%m%d_%H%M%S)

# Fix roasebud_pdf_fetcher cron entry — add PYTHONPATH
crontab -l | sed 's|/root/montanablotter/venv/bin/python3 /root/montanablotter/services/ingestion/fetchers/roasebud_inmate.py|/usr/bin/env PYTHONPATH=/root/montanablotter /root/montanablotter/venv/bin/python3 /root/montanablotter/services/ingestion/fetchers/roasebud_inmate.py|' | crontab -
# Then mirror to crontab.txt
```

### 7.2 Re-enable email_blotter_ingest.py (Yellow tier — do then report)
```bash
# Add to crontab.txt on its own 15-minute tick
echo "0,15,30,45 * * * * /root/montanablotter/venv/bin/python3 /root/montanablotter/job_runner.py --name email_blotter_ingest --log /root/montanablotter/logs/email_blotter_ingest.log --workdir /root/montanablotter -- /root/montanablotter/venv/bin/python3 /root/montanablotter/email_blotter_ingest.py" >> /root/montanablotter/crontab.txt
crontab /root/montanablotter/crontab.txt
```

### 7.3 Add run_all_scrapers.py to crontab (Yellow tier — do then report)
```bash
# Add once-daily run of the unified scraper
echo "30 6 * * * cd /root/montanablotter && /root/montanablotter/venv/bin/python3 ingestion/run_all_scrapers.py" >> /root/montanablotter/crontab.txt
crontab /root/montanablotter/crontab.txt
```

### 7.4 Disable broken Zuercher counties in DB (Red tier — **requires explicit approval**)
These 9 counties burn cycles every 4 hours producing 404s:
`carbon`, `madison`, `meagher`, `ravalli`, `roosevelt`, `rosebud`, `stillwater`, `valley`, `wheatland`

```sql
-- Requires human approval before running
UPDATE jail_booking_sources
SET is_enabled = 0, notes = 'Zuercher API returns 404 as of 2026-05-21 audit'
WHERE county_slug IN ('carbon','madison','meagher','ravalli','roosevelt','rosebud','stillwater','valley','wheatland');
```

### 7.5 Fix Anthropic billing (Human action required)
Visit https://console.anthropic.com/ → Plans & Billing → purchase credits.

---

## 8. Source Classification Matrix

### Jail Rosters

| County | Adapter | DB Enabled | Crontab | Last Success | Class |
|---|---|---|---|---|---|
| beaverhead | ❌ no | ✅ | via all | never | Not configured |
| big-horn | ❌ no | ✅ | via all | never | Not configured |
| broadwater | ✅ yes | ✅ | via all | never | Broken (timeout) |
| carbon | ✅ yes | ✅ | via all | never | Broken (404) |
| cascade | ✅ yes | ✅ | via all | 2026-05-21 | Broken (0 PDF links) |
| custer | ❌ no | ❌ | no | never | Configured but disabled |
| dawson | ❌ no | ✅ | via all | never | Not configured |
| fergus | ❌ no | ✅ | via all | never | Not configured |
| flathead | ✅ yes | ✅ | every 2h | 2026-05-21 | **Working** |
| gallatin | ✅ yes | ✅ | no (skipped) | never | Broken (maintenance) |
| glacier | ❌ no | ✅ | via all | never | Not configured |
| granite | ❌ no | ✅ | via all | never | Not configured |
| hill | ❌ no | ❌ | no | never | Configured but disabled |
| jefferson | ✅ yes | ✅ | every 2h | 2026-05-21 | **Working** |
| lewis-and-clark | ❌ no | ✅ | via all | never | Not configured |
| lincoln | ❌ no | ❌ | no | never | Configured but disabled |
| madison | ✅ yes | ❌ | no | never | Configured but disabled |
| meagher | ✅ yes | ❌ | no | never | Configured but disabled |
| mineral | ❌ no | ✅ | via all | never | Not configured |
| missoula | ✅ yes | ✅ | every 2h | 2026-05-21 | **Working** |
| park | ❌ no | ✅ | via all | never | Not configured |
| phillips | ❌ no | ✅ | via all | never | Not configured |
| pondera | ❌ no | ✅ | via all | never | Not configured |
| powell | ❌ no | ✅ | via all | never | Not configured |
| ravalli | ✅ yes | ❌ | every 2h | 2026-05-21 09:30 | Broken (404 / 0 fetched) |
| roosevelt | ✅ yes | ❌ | no | never | Configured but disabled |
| rosebud | ✅ yes | ❌ | no | never | Configured but disabled |
| sanders | ✅ yes | ✅ | every 2h | 2026-05-21 | **Working** |
| silver-bow | ❌ no | ✅ | via all | never | Not configured |
| stillwater | ✅ yes | ❌ | no | never | Configured but disabled |
| valley | ✅ yes | ✅ | via all | never | Broken (404) |
| wheatland | ✅ yes | ❌ | no | never | Configured but disabled |
| yellowstone | ✅ yes | ✅ | every 2h | 2026-05-21 | **Working** |

### Police Blotters / Calls

| Source | Adapter | Crontab | Last Activity | Class |
|---|---|---|---|---|
| bozeman_calls | ✅ | every hour | 2026-05-21 | **Working** (degraded by Anthropic billing) |
| bozeman_crime | ✅ | every 6h | 2026-05-21 | **Working** |
| missoula_public_report | ✅ | every hour | 2026-05-21 | **Working** |
| whitefish_pdf | ✅ | every 6h | 2026-05-21 | **Working** (no new docs) |
| crimemapping | ✅ | every 12h | 2026-05-21 | **Broken** (all agencies Non-JSON) |
| roasebud_pdf | ✅ | daily 08:30 | 2026-05-21 | **Broken** (PYTHONPATH crash) |
| run_all_scrapers | ✅ file exists | **missing** | never | **Broken** (not scheduled) |
| email_blotter_ingest | ✅ file exists | **missing** | never | **Broken** (not scheduled) |
| mt_blotter city PDs | ✅ framework | via run_all | never | Configured but disabled |

---

## 9. Recommendations (Priority Order)

1. **P0 — Fix Anthropic billing.** Bozeman digest quality is in fallback mode; every call-for-service batch gets a low-quality summary.
2. **P1 — Fix roasebud_pdf_fetcher.** One-line `PYTHONPATH` fix in crontab.
3. **P1 — Add `email_blotter_ingest.py` to crontab.** 15-minute tick separate from `email_worker`.
4. **P1 — Add `run_all_scrapers.py` to crontab.** Daily at 06:30 to populate `scraped_records` and enabled `mt_blotter` sources.
5. **P2 — Disable or fix 9 broken Zuercher counties.** Either `UPDATE is_enabled=0` (Red tier, needs approval) or verify if the API endpoint changed from `GET /api/public/inmate/criteria` to `POST /api/portal/inmates/load`.
6. **P2 — Investigate CrimeMapping global failure.** All 8 agencies returning Non-JSON suggests a platform-level change, not per-agency drift.
7. **P3 — Expand `SUPPORTED_ADAPTERS` for standard-tier counties with working HTML rosters.** e.g. Lewis and Clark, Park, Phillips if parsers can be written.

---

*Report generated by blotter-ops • Task t_b9dbeed2 • Refer to pipeline-ops skill for remediation playbooks*
