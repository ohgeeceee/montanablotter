# Jail Booking Coverage Expansion — Design Spec
**Date:** 2026-03-22
**Status:** Approved for implementation

---

## Goal

Expand live jail booking ingestion from 2 counties (Yellowstone, Missoula) to up to 7 by activating three existing un-scheduled adapters (Flathead, Jefferson, Sanders) and building two new adapters (Cascade, Gallatin). All new counties poll on a 2-hour cadence, staggered to avoid concurrent DB writes.

---

## Background

`jail_booking_ingest.py` already contains fully coded adapters for Flathead, Jefferson, and Sanders counties. They have never been scheduled and are absent from `TRACKED_SOURCES` (Jefferson, Sanders) or missing cron entries (Flathead). Cascade and Gallatin are in `TRACKED_SOURCES` but have no adapter. Gallatin is currently in `SKIPPED_SOURCES` as unavailable.

The DB schema (`jail_bookings`, `jail_booking_sources`, `jail_booking_runs`) is complete and requires no migration.

---

## Phase 1 — Activate Existing Adapters (Flathead, Jefferson, Sanders)

### Step 1: Test-fetch in dry-run mode

Run each adapter using the existing CLI:

```bash
python jail_booking_ingest.py --county flathead --dry-run
python jail_booking_ingest.py --county jefferson --dry-run
python jail_booking_ingest.py --county sanders --dry-run
```

Inspect stdout and `jail_booking_runs` for HTTP errors, parse failures, or 0-record results. Each must return at least 1 valid `JailBookingRecord` before proceeding.

### Step 2: Fix scraping issues if found

Roster HTML layouts change over time. If a dry-run returns 0 records or raises an exception, diagnose and patch the parser. Common failure modes:
- CSS class or element structure changed (Flathead `<div class="inmate-entry">`)
- Pagination or POST parameter changed (Sanders alpha-search)
- Zuercher API endpoint version bump (Jefferson)

### Step 3: Add Jefferson and Sanders to TRACKED_SOURCES

Both have adapters but are missing from the `TRACKED_SOURCES` dict in `jail_booking_ingest.py`, which seeds `jail_booking_sources` via `_ensure_tracked_sources()`. Add entries matching the structure of existing entries:

```python
"jefferson": {
    "county_name": "Jefferson County",
    "facility_name": "Jefferson County Detention Center",
    "roster_url": "<url from TRACKED_SOURCES or live investigation>",
    "phone": None,           # phone TEXT is nullable in jail_booking_sources
    "coverage_tier": "standard",
    "is_featured": 0,
},
"sanders": { ... same structure ... },
```

`phone` is nullable (`phone TEXT` with no `NOT NULL` constraint in the schema). Use `None` as a placeholder if the number is not known at implementation time — it can be filled in later. Flathead is already in `TRACKED_SOURCES` and requires no change.

### Step 4: Add cron entries

Add to `crontab.txt` using the full `job_runner.py` wrapper form. Template for each county (substitute `<county>` and `<cron_expr>`):

```
# <County> jail roster — poll every 2 hours
<cron_expr>  /root/montanablotter/venv/bin/python3 /root/montanablotter/job_runner.py --name jail_booking_ingest_<county> --log /root/montanablotter/jail_booking_ingest.log --workdir /root/montanablotter -- /root/montanablotter/venv/bin/python3 /root/montanablotter/jail_booking_ingest.py --county <county>
```

Staggered schedule (all 2-hour cadence):

| County | Cron expression | Notes |
|---|---|---|
| Flathead | `05 */2 * * *` | +5 min past even hour |
| Jefferson | `15 */2 * * *` | +15 min |
| Sanders | `35 */2 * * *` | +35 min |
| *(Yellowstone: `20 */2`, Missoula: `50 */2` — unchanged)* | | |

---

## Phase 2 — New Adapters (Cascade, Gallatin)

### Adapter contract

Every adapter must implement:

```python
def fetch_<county>_bookings(source_url: str) -> list[JailBookingRecord]:
    ...
```

Where `JailBookingRecord` is the existing frozen dataclass:

```python
@dataclass(frozen=True)
class JailBookingRecord:
    source_record_id: str          # natural dedup key per county
    person_name: str
    age: int | None                # positional, required — pass None if unavailable
    booking_number: str
    booking_at: str | None         # normalized to YYYY-MM-DD HH:MM:SS
    charges_summary: str
    source_url: str | None = None  # optional deep-link to county detail page
```

The adapter handles HTTP and parsing only. The shared `_sync_records()` / `_run_source()` / `ingest_jail_bookings()` pipeline handles all DB writes.

### Investigation step

Before writing any parser, live-fetch each roster URL to determine page structure. Match to the nearest existing adapter pattern:

| Pattern | Existing example | Reuse factor |
|---|---|---|
| Zuercher JSON API | Jefferson | Near copy-paste |
| ASP.NET ViewState POST | Missoula | Medium |
| HTML table + detail pages | Yellowstone | Medium |
| `<div>` block layout | Flathead | Medium |
| Alpha-search POST + detail pages | Sanders | Medium |

### Register in SUPPORTED_ADAPTERS and TRACKED_SOURCES

After the adapter function is written and dry-run verified, add the county slug to `SUPPORTED_ADAPTERS` (the set that gates dispatch in `_run_source()`) and to `TRACKED_SOURCES`.

### Gallatin contingency

Gallatin is currently in `SKIPPED_SOURCES` with the note *"Official roster portal is currently unavailable or in maintenance mode."* It already has an entry in `TRACKED_SOURCES`. During Phase 2:

**If the portal is live — all four steps are required, in this order:**
1. Remove `"gallatin"` from `SKIPPED_SOURCES` — `_run_source()` checks this set before dispatching; leaving it here causes a silent skip even if the adapter exists
2. Build the `fetch_gallatin_bookings()` adapter
3. Add `"gallatin"` to `SUPPORTED_ADAPTERS` — `_run_source()` checks this set after `SKIPPED_SOURCES`; omitting it also causes a silent skip
4. Add the cron entry (`55 */2 * * *`)

Steps 1 and 3 must both be done or the county lands in the silent-skip branch with no error logged.

**If still down:** Update the existing `TRACKED_SOURCES` entry to set `is_enabled=0` and update the notes field. Do **not** add a duplicate entry. Do not schedule. Revisit in a future session.

### Cron entries for Phase 2

| County | Cron expression |
|---|---|
| Cascade | `45 */2 * * *` |
| Gallatin | `55 */2 * * *` |

---

## Final cron schedule (all counties)

| County | Cron | Offset |
|---|---|---|
| Flathead | `05 */2 * * *` | :05 |
| Jefferson | `15 */2 * * *` | :15 |
| Yellowstone | `20 */2 * * *` | :20 (existing) |
| Sanders | `35 */2 * * *` | :35 |
| Cascade | `45 */2 * * *` | :45 |
| Missoula | `50 */2 * * *` | :50 (existing) |
| Gallatin | `55 */2 * * *` | :55 (if live) |

---

## Success criteria

- Phase 1: all three existing adapters return >0 `JailBookingRecord` objects in dry-run. After scheduling, verify with:
  ```sql
  SELECT county_slug, status, fetched_count FROM jail_booking_runs
  ORDER BY started_at DESC LIMIT 20;
  ```
  Each new county must show `status='success'` (not `'skipped'` or `'failed'`) and `fetched_count > 0` within its first scheduled window.
- Phase 2: Cascade adapter returns >0 `JailBookingRecord` objects in dry-run; same SQL verification after scheduling. Gallatin either passes the same threshold and is scheduled, or is documented in `TRACKED_SOURCES` as `is_enabled=0` with an updated note explaining why.
- No changes to DB schema required.
- No changes to public-facing routes, templates, or admin UI required — `_jail_booking_public_context()` already handles any county in `jail_booking_sources`.

---

## Out of scope

- UI changes
- DB migrations
- Changes to the 56-county roster directory page (`/jail-rosters`)
- Any county beyond Flathead, Jefferson, Sanders, Cascade, Gallatin
