# New Source Expansion Design
**Date:** 2026-04-16  
**Scope:** Jail roster adapters for 5 new Montana counties + Helena PD police incident feed

---

## Overview

Montana Blotter currently ingests jail rosters from 6 counties (Yellowstone, Missoula, Flathead, Jefferson, Sanders, Gallatin-tracked-only). This spec adds 5 new jail roster adapters and one new police incident source (Helena PD), expanding automated coverage to approximately 360,000 additional Montana residents.

---

## 1. Jail Roster Expansion

All five counties are added to `jail_booking_ingest.py`. Each requires:
- An entry in `TRACKED_SOURCES`
- Addition to `SUPPORTED_ADAPTERS`
- A county-specific fetch function

Three scraping patterns cover all five counties:

### 1a. Zuercher Portal — Gallatin County

**Population:** ~120,000 (Bozeman)  
**Source:** `https://gallatin-so-mt.zuercherportal.com/#/inmates`  
**Already in:** `TRACKED_SOURCES` (not yet in `SUPPORTED_ADAPTERS`)

Gallatin uses the same Zuercher portal software as Jefferson County. The existing Jefferson adapter (`_fetch_jefferson()`) uses a JSON API endpoint on the portal. Gallatin should use the same endpoint shape with the `gallatin-so-mt` subdomain. Implementation is primarily configuration — add to `SUPPORTED_ADAPTERS` and implement `_fetch_gallatin()` that mirrors `_fetch_jefferson()` with the Gallatin subdomain.

### 1b. HTML Scraper — Cascade County

**Population:** ~87,000 (Great Falls)  
**Source:** `https://www.cascadecountymt.gov/314/Inmate-Roster`  
**Already in:** `TRACKED_SOURCES` (not yet in `SUPPORTED_ADAPTERS`)

The page serves an HTML table updated every 4 hours. New `_fetch_cascade()` function parses the table using the existing `_RosterTextExtractor` / HTML parsing approach similar to Missoula. Fields: name, age, booking date, charges, bond amount.

### 1c. PDF Parser — Lewis & Clark County + Silver Bow County

**Lewis & Clark population:** ~70,000 (Helena — state capital)  
**Lewis & Clark source:** PDF linked from `https://www.lccountymt.gov/Sheriff/Detention-Center`  
- The PDF URL includes a version number that changes with each update; the detention page must be scraped first to discover the current PDF link before downloading and parsing.

**Silver Bow population:** ~35,000 (Butte)  
**Silver Bow source:** PDF roster at `https://co.silverbow.mt.us/3274/Detention-Center`  
- More stable URL pattern than Lewis & Clark.

Both counties publish standard Montana jail roster PDFs with the same columnar format used by Whitefish (already handled by `pdf_parser.py`). A shared `_parse_mt_jail_pdf()` helper handles both. Each county gets its own fetch function (`_fetch_lewisclark()`, `_fetch_silverbow()`) that retrieves the PDF and passes it to the shared parser.

### 1d. Web Portal Scraper — Ravalli County

**Population:** ~43,000 (Hamilton)  
**Source:** `https://ravallicounty.gov/239/Adult-Detention-Center`

The detention center page has an online inmate lookup. The exact mechanism (form POST vs. server-rendered HTML list) must be confirmed against the live page during implementation. Implement `_fetch_ravalli()` accordingly. If the portal requires JavaScript rendering, fall back to requesting the page with standard `requests` and parsing whatever static content is available, or contact the county for a data feed.

---

## 2. Helena PD Police Source

**New file:** `helena_police_fetcher.py`  
**Modeled after:** `bozeman_police_fetcher.py`

Helena PD publishes Calls for Service (CFS) data through their Support Services records page at `https://www.helenamt.gov/Departments/Police-Department/Support-Services-Records`. The exact format (downloadable dataset, portal, or paginated HTML) must be confirmed during implementation.

**Data destination:** `records` table with `source = "helena_pd"` — same destination as CrimeMapping data. Does **not** go through the full blotter summarizer pipeline (`processor.py` → `summarizer.py`). Surfaces on the public activity feed and `/arrests` page.

**Deduplication:** By incident ID (or a hash of incident date + address + type if no stable ID is available), matching the pattern in `missoula_public_report_fetcher.py`.

**Scope boundary:** Summarization into digest posts is out of scope for this iteration. Revisit if Helena incident volume warrants it.

---

## 3. Cron Schedule

Five new entries added to `crontab.txt`, all wrapped in `job_runner.py`. Minutes are staggered to avoid pile-ups with existing jail ingest jobs (which run at :05, :15, :20, :35, :40, :50).

**Note:** Gallatin County already has a cron entry at `:40 */2` — it just lacks a working adapter. No new cron line needed for Gallatin; implementing the adapter is sufficient.

```
# Cascade County jail roster — every 2 hours
25 */2 * * *   jail_booking_ingest.py --county cascade

# Lewis & Clark County jail roster — every 2 hours
30 */2 * * *   jail_booking_ingest.py --county lewisclark

# Silver Bow County jail roster — every 2 hours
45 */2 * * *   jail_booking_ingest.py --county silverbow

# Ravalli County jail roster — every 2 hours
55 */2 * * *   jail_booking_ingest.py --county ravalli

# Helena PD calls for service — hourly
15 * * * *     helena_police_fetcher.py
```

`script_watchdog.py` picks up all new jobs automatically via log freshness checks — no watchdog changes required.

---

## 4. Alert Integration

New jail counties inherit the existing felony booking alert system automatically. `dispatch_felony_booking_alerts()` and `dispatch_telegram_booking_alerts()` are called at the end of every successful sync in `sync_county()`, so Gallatin, Cascade, Lewis & Clark, Silver Bow, and Ravalli bookings will trigger bail bond alerts and Telegram notifications without any additional wiring.

---

## 5. Error Handling

Each new adapter follows the existing pattern:
- HTTP errors → log warning, return empty list (sync skips gracefully)
- Parse errors → log per-record, continue with remaining records
- PDF discovery failure (Lewis & Clark) → log error, return empty list
- Counties added to `SKIPPED_SOURCES` if their endpoint is persistently unavailable (existing mechanism)

---

## 6. Out of Scope

- Butte PD / Silver Bow PD incident reports (jail roster only for Silver Bow)
- Summarizer pipeline integration for Helena PD
- Lake County jail roster (lower population, lower priority — add in a follow-on)
- Any county requiring authenticated access or formal records requests
