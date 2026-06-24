# Court Tracking

> Court case discovery, hearing alerts, and outcome scraping for Montana state and appellate courts.

## Overview

The court-tracking pipeline gathers public docket, hearing, filing, and outcome data from a mix of web portals and email-driven sources. It stores cases, hearings, and filings in the SQLite database, exposes them through public pages, and links criminal outcomes back to jail bookings via the disposition watcher.

## Module Map

```
services/court/
├── tracker.py                 # Core DB helpers and orchestration: upsert case/court/source, event management,
│                              # schema guard, admin/public listing context, person/profile joins.
├── outcome_scraper.py         # Generic outcome scraper used for District Court portal details.
├── supreme_court_outcome_scraper.py # Supreme Court opinions + docket outcomes.
├── refresh.py                 # CLI/cron entry point: refresh court data from all registered sources.
├── source_adapters.py         # Adapter layer that selects the right fetch strategy per source slug
│                              # (iCourtCase, portal scrapers, calendar-only sources, etc.).
├── district_portal_scraper.py # MT District Court calendar portal (fullcourtweb) — Playwright-based.
├── watercourt_scraper.py      # Montana Water Court docket and e-filing scrapers.
├── taxappeal_scraper.py       # MT Department of Revenue tax appeal board docket scraper.
├── colj_portal_scraper.py     # Court on the Judiciary portal scraper.
└── ingest.py                  # One-off CSV/JSON court ingestion helper.

services/alerts/
└── court.py                   # Hearing-alert engine: builds per-case alerts and sends notifications
                               # when a tracked defendant gets a new court hearing.

services/disposition/
├── lookup.py                  # person/case matching utilities, slug normalization.
└── watcher.py                 # Cron-side disposition watcher; links jail bookings to court cases
                               # and detects outcome changes.

services/ingestion/
├── icourtcase_civil.py        # iCourtCase integration for civil filings.
└── icourtcase_civil_runner.py # CLI runner for the civil-filing import.

blueprints/
└── (court routes are in app.py, but the data lifecycle lives in services/court)

app.py                         # Public routes: /court-sources, /court-case/<slug>, /charges,
                               # disposition API landing/checkout, sitemap entries for court_cases.
```

## Data Flow

### 1. Discovery & refresh

`services/court/refresh.py` is the cron entry point:

```python
python -m services.court.refresh
```

It iterates over every row in `court_sources` and uses `services/court/source_adapters.py` to pick a fetcher:

| Source provider type | Used by | What it does |
|---|---|---|
| `icourtcase` | iCourtCase civil/criminal feeds, county-level lookups | Authenticated JSON/RSS scraping via `services/ingestion/icourtcase_civil.py` plus portal URLs. |
| `court_calendar` | `district_portal_scraper.py` | Logs into the District Court calendar portal and scrapes weekly hearing calendars. |
| `watercourt` | `watercourt_scraper.py` | Downloads Water Court docket reports and e-filings. |
| `tax_appeal` | `taxappeal_scraper.py` | Docket and opinion search for MT DOR tax appeals. |
| `supreme_court` | `supreme_court_outcome_scraper.py` | Supreme Court opinions + case details. |
| `colj` | `colj_portal_scraper.py` | Court on the Judiciary public cases / hearings. |

Results are normalized and written through helpers in `services/court/tracker.py`:

- `ensure_court_tracker_schema(conn)` — idempotent schema guard.
- `upsert_court_source(...)` — source registry.
- `upsert_court(...)` — individual court/facility.
- `upsert_court_case(...)` — case header (number, caption, status, etc.).
- `add_court_event(...)` / `add_court_filing(...)` — hearings and filings.

### 2. Hearing alerts

`services/alerts/court.py` evaluates new/updated court events and sends alerts for tracked defendants or followed cases. It runs as part of the alert worker lifecycle triggered by `run_all_scrapers.py` and the court refresh.

Key concepts:

- Alerts are generated when a known `court_cases` row receives a new `court_events` row (hearing date/time/courtroom).
- The engine de-duplicates using the case number + event date + event title tuple.
- Output format and routing align with the rest of `services.alerts.*`.

### 3. Outcome scraping

Criminal outcomes are populated from several parallel paths:

1. **District portal detail pages** (`district_portal_scraper.py`) — when the calendar event links to a case detail page, the scraper extracts case type, status, caption, and, for criminal cases, defendant name, charges, plea, disposition, sentence text, sentence date, and sentencing judge. The helper `_extract_docket_outcome()` in `outcome_scraper.py` handles the generic disposition parsing.
2. **Supreme Court** (`supreme_court_outcome_scraper.py`) — parses published opinions for affirmed/reversed/remanded dispositions and maps them back to `court_cases.disposition`.
3. **Source adapters** return structured outcome fields; `tracker.py` applies them with an `OUTCOME UPDATE` SQL block keyed on `court_id + case_number`.

Outcome values normalized in `court_cases`:

| Field | Meaning |
|---|---|
| `is_criminal` | `1` when the case type or portal entry indicates a criminal matter. |
| `defendant_name` | Full defendant name as found on the portal/opinion. |
| `charges_text` | Flattened charge summary. |
| `charges_json` | Structured charge list (JSON). |
| `plea` | Guilty / not guilty / no contest / etc. |
| `disposition` | Affirmed, reversed, reversed_and_remanded, remanded, dismissed, etc. |
| `sentence_text` | Free-form sentence description. |
| `sentence_date` | Sentencing date. |
| `sentencing_judge` | Judge name. |
| `outcome_scraped_at` | Last successful outcome scrape timestamp. |

The court alert engine can also surface disposition changes as additional notifications.

### 4. Disposition → jail booking linkage

`scripts/ops/disposition_watcher.py` runs every 15 minutes:

```
*/15 * * * * /root/montanablotter/venv/bin/python3 /root/montanablotter/scripts/ops/disposition_watcher.py >> /root/montanablotter/logs/disposition_watcher.log 2>&1
```

It calls `services/disposition/watcher.py:run_all(conn)` to:

1. Link recent `jail_bookings` rows to `court_cases` on defendant slug / last-name/first-name and county.
2. Detect outcome changes for previously linked rows.
3. Log changes without mutating core court or booking data (used by downstream alerts and the disposition API).

## External Sources

| Source | URL pattern | Access |
|---|---|---|
| iCourtCase (Justice Systems) | County-specific subdomains | Authenticated; credentials loaded from env (`ICOORTCASE_*`) and rotated/retried. |
| MT District Court portal | `https://fullcourtweb.mtcourt.gov/...` | Public Playwright session; court selector dropdown, weekly calendar. |
| MT Supreme Court | State opinions page + case search | Public HTTP + HTML parsing. |
| MT Water Court | Docket search / e-filing portal | Public HTTP. |
| MT Tax Appeals | DOR appeal search | Public HTTP. |
| Court on the Judiciary | Public discipline/hearing portal | Public HTTP. |
| Havre/Hill County DOCX | Emails from Hill County Sheriff's Office | Email attachment; routed through `email_worker.py` → `services/ingestion/fetchers/havre_inmate.py`. |

> Credential references in source are replaced with `[REDACTED]` where applicable.

## Cron Schedule

Defined in `crontab.txt`:

```
# Court refresh — pulls new court cases and dispositions every 6h at :50
50 */6 * * * /usr/bin/nice -n 19 ... /root/montanablotter/venv/bin/python3 -m services.court.refresh

# Disposition watcher — link bookings to cases + detect outcome changes
*/15 * * * * ... /root/montanablotter/venv/bin/python3 /root/montanablotter/scripts/ops/disposition_watcher.py

# General scraper bundle (includes court-alert engine)
20 */6 * * * ... /root/montanablotter/venv/bin/python3 /root/montanablotter/ingestion/run_all_scrapers.py
```

Both court refresh and the all-scrapers run are wrapped with `nice -n 19 ionice -c 3` to limit impact on the web workload.

## Database Tables

### `court_sources`

Registry of ingested data sources.

```
id, slug, name, provider_type, source_url, status
last_scraped_at, last_success_at, last_error, created_at, updated_at
```

### `courts`

Individual courts/facilities.

```
id, source_id, slug, name, court_type, county, portal_url, created_at, updated_at
```

### `court_cases`

Core case header plus criminal outcome fields.

```
id, court_id, case_number, slug, caption, status, filed_date, case_type, judge, source_url
is_criminal, defendant_name, charges_text, charges_json, plea, disposition, sentence_text
sentence_date, sentencing_judge, outcome_scraped_at, original_court, original_case_number
defendant_slug, defendant_last, defendant_first, created_at, updated_at
```

### `court_events`

Hearings and calendar events.

```
id, case_id, event_type, event_date, event_time, event_title, judge, notes, source_url, created_at, updated_at
```

### `court_filings`

Documents / docket entries.

```
id, case_id, filing_date, filing_title, filing_url, summary, created_at, updated_at
```

### Indexes (selected)

- `idx_court_cases_slug`, `idx_court_cases_case_number` — public lookups.
- `idx_court_cases_criminal` — `(is_criminal, outcome_scraped_at, status)` for outcome sweep.
- `idx_court_cases_defendant_slug`, `idx_court_cases_defendant_last_first` — disposition joins.
- `idx_court_events_case_id_date` — hearing alert queries.

## Public Pages

| Route | Source function | Purpose |
|---|---|---|
| `/court-sources` | `app.public_court_sources()` | Transparency page listing every source with sync status, court count, and case count. |
| `/court-case/<slug>` | `app.public_court_case_detail()` | Case detail with hearings, filings, and outcomes. |
| `/charges` | `app.public_charges_index()` | Charge-category index across records + court cases. |
| `/disposition-api` | `app.disposition_api_landing()` | Paid/member API landing. |

## Failure Modes

| Symptom | Likely Cause | Mitigation |
|---|---|---|
| `522` / `forbidden` / `connection reset` in `court_sources.last_error` | Portal rate-limit or WAF block. | Marked as blocked on `/court-sources`; autoretried next cron window with back-off. |
| Playwright timeout on `fullcourtweb` | Portal session/cookie churn or heavy load. | Scraper captures the error, updates `last_error`, and moves to next court. |
| iCourtCase auth failure | Credential expiry or account lockout. | iCourtCase adapter retries and logs; manual credential rotation required if retry budget exhausted. |
| Stale cases with `outcome_scraped_at IS NULL` | Outcome scraper did not reach those case types. | Outcome scrapers prioritize criminal cases; non-criminal and sealed records stay empty. |
| Duplicate cases | Same case reachable via multiple sources. | Upsert keyed on `court_id + case_number`, but cross-source duplication can occur if portal IDs differ. |
| Disposition watcher false links | Common names across counties. | Join gated by county + first/last name + slug; edge cases reviewed manually. |
| DOCX roster filename collision (Havre) | HCSO re-uses the same filename daily. | `havre_inmate.py` scopes `source_record_id` by email `Date:` header (`roster_date`) and writes dated disk copies. |
