# Jail Bookings

> Jail roster ingestion, release monitoring, and public exposure for Montana county bookings.

## Overview

The jail-bookings subsystem fetches roster data from county jails and detention centers, normalizes it, and writes it into the `jail_bookings` table. Public pages under `/detention/` expose county rosters, search, and per-person profiles. The pipeline also flags release states and tracks source health in `jail_booking_sources` and `jail_booking_runs`.

## Module Map

```
services/ingestion/
├── jail_bookings.py           # Main orchestrator: CLI entry point, source registry, db helpers,
│                              # dedup/upsert, run recording, release monitoring, stats.
├── models.py                  # `JailBookingRecord` dataclass used by fetchers.
└── fetchers/
    ├── havre_inmate.py        # Hill County / Havre PD: DOCX email attachment parser.
    ├── flathead.py            # Flathead County online roster.
    ├── missoula.py            # Missoula County online roster.
    ├── yellowstone.py         # Yellowstone County online roster.
    ├── ravalli.py             # Ravalli County Zuercher portal.
    ├── jefferson.py           # Jefferson County roster.
    ├── sanders.py             # Sanders County roster.
    └── bozeman.py             # City of Bozeman arrest/call logs (related records, not jail).

services/detention/
└── (directory exists as placeholder; no active modules were found)

blueprints/
└── detention.py               # Flask blueprint for /detention/ routes and admin endpoints.

app.py                         # Source sync, public context builders, sitemap, and
                               # registration of the detention blueprint.

scripts/ops/
├── disposition_watcher.py     # Links new jail bookings to court cases every 15 min.
└── backup_db.sh               # Daily SQLite backup.

reports/
└── jail_booking_source_status.md  # Human-readable county coverage and freshness report.
```

## County Coverage

Source registry is seeded from `app.COUNTY_DIRECTORY` via `_ensure_tracked_sources()` in `services/ingestion/jail_bookings.py`. Major counties (`MAJOR_JAIL_BOOKING_COUNTIES`) receive `coverage_tier='major'`, `is_featured=1`, and more frequent polling.

**Major / featured counties**

- `yellowstone`
- `missoula`

**Active online roster counties**

- `flathead`
- `jefferson`
- `ravalli` (Zuercher portal)
- `sanders`
- `yellowstone`
- `missoula`

**Email/DOCX-only**

- `hill` (Havre / Hill County Sheriff's Office daily DOCX) — no public online roster; data arrives via `email_worker.py`.

**Remaining Zuercher portal counties** (batched every 4 hours when no `--county` flag is passed):

- Gallatin, Madison, Carbon, Stillwater, Meagher, Wheatland, Valley, Roosevelt, Broadwater.

## Data Flow

### 1. Fetch

Each cron entry invokes `services/ingestion/jail_bookings.py` with a `--county <slug>` argument or runs the full remaining-Zuercher batch:

```bash
python services/ingestion/jail_bookings.py --county flathead
```

The orchestrator (`_run_county`) looks up the `CountyFetcher` adapter registered in `_COUNTY_FETCHERS`. Each adapter returns a list of `JailBookingRecord` objects:

```python
@dataclass
class JailBookingRecord:
    source_record_id: str
    person_name: str
    age: int | None
    booking_number: str
    booking_at: str | None
    charges_summary: str
    source_url: str | None = None
```

Adapters are responsible for:

- HTTP/portal scraping or email-DOCX parsing.
- Normalizing `booking_at` to `YYYY-MM-DD HH:MM:SS`.
- Building a stable `source_record_id` (required for idempotent dedup).

### 2. Dedup & upsert

`_sync_records(conn, source_id, county_slug, records)` performs the core merge:

1. Compute `hash_id` for each record from normalized name + booking number + booking date.
2. If a row with the same `source_record_id` already exists, update `last_seen_at` and mutable fields (charges, status) and count it as `updated`.
3. If no row exists, insert it, set `first_seen_at = now`, `is_current = 1`, and count it as `new`.
4. Records not seen in the current fetch are marked `is_current = 0` and `booking_status = 'released'`, counted as `missing`.

Long-term state fields:

- `first_seen_at` — when this person/booking first appeared.
- `last_seen_at` — most recent time this row was observed in a roster.
- `is_current` — `1` if still present in latest fetch, `0` if missing.
- `booking_status` — `'current'` or `'released'`.

### 3. Run recording

Every completed fetch inserts a `jail_booking_runs` row with:

```
source_id, run_type='scheduled', status, fetched_count, new_count, updated_count, missing_count
started_at, completed_at, notes
```

`jail_booking_sources.last_checked_at`, `last_success_at`, and `latest_error` are also updated.

### 4. Public exposure

`blueprints/detention.py` exposes JSON and HTML endpoints:

| Route | Purpose |
|---|---|
| `/detention/` | Statewide roster index / admin dashboard. |
| `/detention/<county_slug>` | County roster page. |
| `/detention/api/bookings` | API: county-filtered, search, status filter, paginated booking list. |
| `/detention/api/stats` | API: per-county counts. |
| `/detention/api/sources` | API: source list with freshness. |
| `/detention/api/sync` | Admin: trigger an on-demand sync. |

`app.py` provides the legacy public HTML views (`/jail-bookings`, `/county/<slug>/jail-bookings`) through helper functions `_jail_booking_public_context()` and `_jail_booking_admin_context()`. These join `jail_bookings` with `jail_booking_sources` and compute `current_bookings`, `new_24h`, `featured_sources`, and recent run history.

## Cron Schedule

From `crontab.txt` (re-enabled 2026-06-14, all wrapped with `nice -n 19 ionice -c 3`):

```
# Major / explicit county rosters — every 2 hours, staggered
 5 */2 * * * jail_bookings.py --county flathead
15 */2 * * * jail_bookings.py --county jefferson
20 */2 * * * jail_bookings.py --county yellowstone
35 */2 * * * jail_bookings.py --county sanders
45 */2 * * * jail_bookings.py --county ravalli
50 */2 * * * jail_bookings.py --county missoula

# Remaining Zuercher portal counties — every 4 hours
0 */4 * * * jail_bookings.py
```

Email worker (every 15 minutes):

```
*/15 * * * * ... email_worker.py --mode queue
```

The email worker routes Hill County/Havre DOCX attachments to `services/ingestion/fetchers/havre_inmate.py`.

## Database Tables

### `jail_booking_sources`

```
id, county_slug, county_name, facility_name, roster_url, phone
is_enabled, is_featured, coverage_tier
last_checked_at, last_success_at, latest_error, notes, created_at, updated_at
```

Seeded on first read from `app.COUNTY_DIRECTORY` via `_sync_jail_booking_sources()` / `_ensure_tracked_sources()`.

### `jail_bookings`

```
id, source_id, county_slug, county_name, facility_name
person_name, age, booking_number, booking_at, release_at
charges_summary, charges_json, arresting_agency
source_url, source_record_id
booking_status, is_current
first_seen_at, last_seen_at, notes
hash_id, name_slug, raw_json, created_at, updated_at
```

### `jail_booking_runs`

```
id, source_id, run_type, status, fetched_count, new_count, updated_count, missing_count
started_at, completed_at, notes
```

### Indexes (selected)

- `idx_jail_bookings_lookup` — `(county_slug, is_current, booking_at, first_seen_at)` for roster pages.
- `idx_jail_bookings_hash_id` — dedup.
- `idx_jail_bookings_name_slug` — person profile joins.
- `idx_jail_bookings_source_record_id` — source-side idempotency.
- `idx_jail_bookings_source` — source freshness analytics.
- `idx_jail_booking_runs_source` — run history.
- `idx_jail_booking_sources_featured` — source listings.

## Public Pages

| Route | Source | Purpose |
|---|---|---|
| `/jail-bookings` | `app.py` `_jail_booking_public_context()` | Statewide roster with featured (major) counties. |
| `/county/<slug>/jail-bookings` | `app.py` | County-specific roster, source card, recent runs. |
| `/people` | `services.persons.profiles` | Aggregated person directory across bookings. |
| `/person/<name_slug>` | `services.persons.profiles` | Person profile with booking history + related court cases. |
| `/detention/<county_slug>` | `blueprints/detention.py` | County roster page and admin hooks. |

## Deduplication & Release Monitoring

- **Dedup key**: `source_record_id` is the primary idempotency key. A secondary `hash_id` is computed from normalized name + booking number + booking date for additional merge confidence.
- **Name normalization**: `person_name` is title-cased; `name_slug` is derived by stripping punctuation/spaces and lowercasing.
- **Release detection**: On every fetch, rows for the source that were not present in the returned roster get `is_current = 0`, `booking_status = 'released'`, and `release_at` may be set to the fetch time.
- **Re-appearance**: If a released person re-appears with the same `source_record_id`, the row is revived (`is_current = 1`, `booking_status = 'current'`); a new booking is only created if the dedup key differs.
- **Havre/Hill DOCX**: The same filename is reused daily, so `source_record_id` is prefixed with `havre-docx:{roster_date}` using the email `Date:` header; otherwise, re-ingest would silently no-op.

## Failure Modes

| Symptom | Likely Cause | Mitigation |
|---|---|---|
| `database is locked` during Havre ingest | Concurrent email worker + image blotter both saw the same DOCX. | `havre_inmate.py` uses an `fcntl` cross-process lock (`montanablotter_havre_ingest.lock`) with a 10-minute timeout. |
| County shows no new bookings for days | Portal UI changed or source disabled. | `latest_error` + `last_success_at` on `jail_booking_sources`; stale sources surface on `/jail-bookings` source cards. |
| `source_record_id` collision | Adapter uses non-unique row index or static filename. | Fix adapter to include booking number, date, or email date in the record ID. |
| High `missing_count` on a single run | Portal temporarily dropped rows or fetch timed out mid-page. | Run still completes and writes a run record; next cron window reconciles. |
| Zuercher portal returns stale roster | Session/cookie expiry. | Generic retry + source error logging; manual adapter review may be required. |
| Person profile mis-merge | `name_slug` collision on common names. | Profile builder groups by slug; edge cases are manually reconciled. |

## Related Documents

- `reports/jail_booking_source_status.md` — current coverage, freshness, and known blockers per county.
- `docs/CODEMAPS/court-tracking.md` — disposition watcher links bookings to court cases.
- `docs/civil_filings_county_imports.md` and `docs/icourtcase_playwright_capture.md` — related iCourtCase ingestion docs.
