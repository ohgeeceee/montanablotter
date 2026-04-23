# Jail Booking Coverage Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand live jail booking ingestion from 2 counties to up to 7 by activating three existing un-scheduled adapters (Flathead, Jefferson, Sanders) and building two new adapters (Cascade, Gallatin).

**Architecture:** All work is confined to `jail_booking_ingest.py` and `crontab.txt`. The shared sync/DB pipeline (`_sync_records`, `_run_source`, `ingest_jail_bookings`) is untouched. New adapters follow the existing `fetch_<county>_bookings(source_url) -> list[JailBookingRecord]` contract. Gallatin uses the same Zuercher JSON API as Jefferson — `fetch_gallatin_bookings` is written as a standalone function (DRY refactor is a future task). Cascade requires a live-fetch investigation before an adapter can be written.

**Tech Stack:** Python 3.12, `requests`, `sqlite3`, `unittest` (existing test suite at `tests/test_ingestion_sources.py`). Always `cd /root/montanablotter && source venv/bin/activate` before running anything.

---

## Important context before starting

**Jefferson and Sanders are already wired up in `_run_source` — they just aren't in the DB yet.**

`SUPPORTED_ADAPTERS` (line 40) already includes `"flathead"`, `"jefferson"`, and `"sanders"`. `_run_source` (lines 1228–1241) already has dispatch branches for all three. The only missing piece is that Jefferson and Sanders have no entries in `TRACKED_SOURCES`, so `_ensure_tracked_sources()` never inserts them into `jail_booking_sources`, and `ingest_jail_bookings()` never queries them. **Task 1 fixes this. No dispatch changes are needed for Phase 1 counties.**

---

## File Map

| File | Change |
|---|---|
| `jail_booking_ingest.py` | Add Jefferson + Sanders to `TRACKED_SOURCES`; add `fetch_gallatin_bookings()`; add `fetch_cascade_bookings()`; add dispatch branches for `cascade` and `gallatin` in `_run_source()`; remove `"gallatin"` from `SKIPPED_SOURCES` if live |
| `crontab.txt` | Add up to 5 new cron entries (Flathead, Jefferson, Sanders, Cascade, Gallatin) |
| `tests/test_ingestion_sources.py` | Add unit tests for Gallatin and Cascade parser logic |

---

## Task 1: Add Jefferson and Sanders to TRACKED_SOURCES

**Files:**
- Modify: `jail_booking_ingest.py:45-86` (the `TRACKED_SOURCES` dict)

`TRACKED_SOURCES` seeds the `jail_booking_sources` table via `_ensure_tracked_sources()`. Jefferson and Sanders have adapters but no TRACKED_SOURCES entries — adding them here is the only change needed to make the CLI find and run them. Use the short county name form (`"Jefferson"`, `"Sanders"`) consistent with all other entries in the dict.

- [ ] **Step 1: Find Jefferson's roster URL**

```bash
cd /root/montanablotter && source venv/bin/activate
python3 -c "
import requests
r = requests.post(
    'https://jefferson-so-mt.zuercherportal.com/api/portal/inmates/load',
    json={'name':'','race':'all','sex':'all','cell_block':'all','held_for_agency':'any',
          'in_custody':'','paging':{'start':0,'count':1},
          'sorting':{'sort_by_column_tag':'name','sort_descending':False}},
    headers={'User-Agent':'MontanaBlotter/1.0',
             'Accept':'application/json,*/*',
             'Referer':'https://jefferson-so-mt.zuercherportal.com/#/inmates'},
    timeout=15
)
print(r.status_code, r.text[:300])
"
```

If this returns JSON with a `records` key → `https://jefferson-so-mt.zuercherportal.com/#/inmates` is correct. If 404, find the current URL at `https://www.jeffersoncountymt.gov` (look for a jail or detention link).

- [ ] **Step 2: Find Sanders' roster URL**

```bash
cd /root/montanablotter && source venv/bin/activate
python3 -c "
import requests, urllib3
urllib3.disable_warnings()
r = requests.post(
    'https://www.sanderscountymt.gov/jms_public/functions/search.php',
    data={'nx':'','last':'A','first':'','jkt':'','submit':'Search'},
    verify=False, timeout=15,
    headers={'User-Agent':'MontanaBlotter/1.0'}
)
print(r.status_code, r.text[:300])
"
```

If this returns HTML with `<tr bgcolor=` rows → Sanders' base URL is `https://www.sanderscountymt.gov`. If 404, find the current URL at the Sanders County official website.

- [ ] **Step 3: Add entries to TRACKED_SOURCES in `jail_booking_ingest.py`**

Add after the `"cascade"` entry (around line 85), before the closing `}`:

```python
    "jefferson": {
        "county_name": "Jefferson",
        "facility_name": "Jefferson County Detention Center",
        "roster_url": "<confirmed URL from Step 1>",
        "phone": None,
        "coverage_tier": "standard",
        "is_featured": 0,
    },
    "sanders": {
        "county_name": "Sanders",
        "facility_name": "Sanders County Jail",
        "roster_url": "<confirmed URL from Step 2>",
        "phone": None,
        "coverage_tier": "standard",
        "is_featured": 0,
    },
```

`phone` is nullable (`phone TEXT` with no `NOT NULL` constraint). Use `None` as a placeholder — fill in later if needed.

- [ ] **Step 4: Verify `_ensure_tracked_sources` inserts both counties**

```bash
cd /root/montanablotter && source venv/bin/activate
python3 -c "
import sqlite3, jail_booking_ingest
conn = sqlite3.connect('blotter.db')
conn.row_factory = sqlite3.Row
jail_booking_ingest._ensure_tracked_sources(conn)
rows = conn.execute(
    'SELECT county_slug, roster_url FROM jail_booking_sources ORDER BY county_slug'
).fetchall()
for r in rows:
    print(r['county_slug'], r['roster_url'])
conn.close()
"
```

Expected: both `jefferson` and `sanders` appear in the output.

- [ ] **Step 5: Commit**

```bash
cd /root/montanablotter
git add jail_booking_ingest.py
git commit -m "feat: add Jefferson and Sanders to TRACKED_SOURCES

Registers both counties in the source registry so their existing
adapters (already in SUPPORTED_ADAPTERS and _run_source) can be
discovered and scheduled.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 2: Dry-run Flathead and fix any issues

**Files:**
- Modify: `jail_booking_ingest.py:751-753` (fetch_flathead_bookings) — only if fixes are needed

Flathead is already in `TRACKED_SOURCES`. Its adapter (`fetch_flathead_bookings`, line 751) parses `<div class="inmate-entry">` blocks from `apps.flathead.mt.gov/jailroster/`.

- [ ] **Step 1: Run dry-run**

```bash
cd /root/montanablotter && source venv/bin/activate
python3 jail_booking_ingest.py --county flathead --dry-run
```

Expected: `flathead: fetched=N new=N updated=0 missing=0` where N > 0. If this passes, skip to Step 4.

- [ ] **Step 2: If fetched=0 or exception — diagnose**

```bash
cd /root/montanablotter && source venv/bin/activate
python3 -c "
import jail_booking_ingest, logging
logging.basicConfig(level=logging.DEBUG)
records = jail_booking_ingest.fetch_flathead_bookings(
    'https://apps.flathead.mt.gov/jailroster/'
)
print(f'Got {len(records)} records')
if records:
    print(records[0])
"
```

Common failures and fixes:
- **`<div class="inmate-entry">` not found** → Fetch live HTML: `requests.get('https://apps.flathead.mt.gov/jailroster/?report=inmates&sort=lastname')`. Inspect the current structure and update `_parse_flathead_roster` to match.
- **HTTP 4xx/5xx** → The URL may have moved. Check `flathead.mt.gov` for the current jail roster link.
- **0 records, no exception** → Add `print(page_html[:2000])` inside `fetch_flathead_bookings` to inspect what the parser sees.

- [ ] **Step 3: If parser was fixed — write a unit test**

In `tests/test_ingestion_sources.py`, add inside `IngestionSourceTests`:

```python
def test_flathead_parser_extracts_booking(self) -> None:
    # Paste a representative <div class="inmate-entry"> block from the live site
    sample_html = """
    <div class="inmate-entry">
      <img src="/mugshots/12345.jpg" alt="Inmate photo">
      <p><strong>SMITH, JOHN A</strong></p>
      <p>Age: 34</p>
      <p>Booking #: 2026-001234</p>
      <p class="disposition-description">THEFT | ASSAULT</p>
    </div>
    """
    records = jail_booking_ingest._parse_flathead_roster(
        sample_html, "https://apps.flathead.mt.gov/jailroster/"
    )
    self.assertGreater(len(records), 0)
    self.assertIn("Smith", records[0].person_name)
```

Adjust the HTML fixture to match whatever the live site actually emits.

Run: `python3 -m pytest tests/test_ingestion_sources.py -v`
Expected: all tests PASS.

- [ ] **Step 4: Commit (only if code was changed)**

```bash
cd /root/montanablotter
git add jail_booking_ingest.py tests/test_ingestion_sources.py
git commit -m "fix: update Flathead roster parser for current site structure

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 3: Dry-run Jefferson and fix any issues

**Files:**
- Modify: `jail_booking_ingest.py:770-838` (fetch_jefferson_bookings) — only if fixes are needed

Jefferson uses the Zuercher JSON API. The adapter POSTs paginated JSON to `/api/portal/inmates/load`.

- [ ] **Step 1: Run dry-run**

```bash
cd /root/montanablotter && source venv/bin/activate
python3 jail_booking_ingest.py --county jefferson --dry-run
```

Expected: `jefferson: fetched=N new=N updated=0 missing=0` where N > 0. If this passes, skip to Step 4.

- [ ] **Step 2: If fetched=0 or exception — diagnose**

```bash
cd /root/montanablotter && source venv/bin/activate
python3 -c "
import jail_booking_ingest, logging
logging.basicConfig(level=logging.DEBUG)
records = jail_booking_ingest.fetch_jefferson_bookings(
    '<URL confirmed in Task 1>'
)
print(f'Got {len(records)} records')
if records:
    print(records[0])
"
```

Common Zuercher failures:
- **API endpoint moved** → The current path (`/api/portal/inmates/load`) may have changed. Inspect the Zuercher portal's browser network requests to find the current API path.
- **`in_custody` format changed** → Try `"in_custody": "today"` as a string, or an empty string.
- **`records` key missing in response** → Print `payload.keys()` to see the current response shape.

- [ ] **Step 3: Re-run and confirm fetched > 0**

```bash
cd /root/montanablotter && source venv/bin/activate
python3 jail_booking_ingest.py --county jefferson --dry-run
```

- [ ] **Step 4: Commit if fixes were made**

```bash
cd /root/montanablotter
git add jail_booking_ingest.py
git commit -m "fix: update Jefferson Zuercher adapter for current API

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 4: Dry-run Sanders and fix any issues

**Files:**
- Modify: `jail_booking_ingest.py:895-961` (fetch_sanders_bookings) — only if fixes are needed

Sanders uses alpha-search POSTs (A–Z) plus per-inmate detail page fetches. `session.verify = False` is intentional — Sanders County's TLS certificate is expired.

- [ ] **Step 1: Run dry-run**

```bash
cd /root/montanablotter && source venv/bin/activate
python3 jail_booking_ingest.py --county sanders --dry-run
```

Expected: `sanders: fetched=N new=N updated=0 missing=0` where N > 0. If this passes, skip to Step 4.

- [ ] **Step 2: If fetched=0 or exception — diagnose**

```bash
cd /root/montanablotter && source venv/bin/activate
python3 -c "
import jail_booking_ingest, logging
logging.basicConfig(level=logging.DEBUG)
records = jail_booking_ingest.fetch_sanders_bookings(
    '<URL confirmed in Task 1>'
)
print(f'Got {len(records)} records')
if records:
    print(records[0])
"
```

Common failures:
- **`functions/search.php` path changed** → Inspect the live site's HTML form action attribute.
- **`<tr bgcolor=` pattern changed** → Fetch a search result page and inspect the current table structure in `_parse_sanders_search_results`.
- **`viewbkg.php?bkg=N` detail URL changed** → Inspect a result row's link href.

- [ ] **Step 3: Re-run and confirm**

```bash
cd /root/montanablotter && source venv/bin/activate
python3 jail_booking_ingest.py --county sanders --dry-run
```

- [ ] **Step 4: Commit if fixes were made**

```bash
cd /root/montanablotter
git add jail_booking_ingest.py
git commit -m "fix: update Sanders roster parser for current site structure

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 5: Add Phase 1 cron entries (Flathead, Jefferson, Sanders)

**Files:**
- Modify: `crontab.txt`

- [ ] **Step 1: Add three entries to `crontab.txt`**

Find the existing jail booking section (around line 66). Add after the last existing jail booking entry:

```
# Flathead County jail roster — poll every 2 hours
05 */2 * * * /root/montanablotter/venv/bin/python3 /root/montanablotter/job_runner.py --name jail_booking_ingest_flathead --log /root/montanablotter/jail_booking_ingest.log --workdir /root/montanablotter -- /root/montanablotter/venv/bin/python3 /root/montanablotter/jail_booking_ingest.py --county flathead

# Jefferson County jail roster — poll every 2 hours
15 */2 * * * /root/montanablotter/venv/bin/python3 /root/montanablotter/job_runner.py --name jail_booking_ingest_jefferson --log /root/montanablotter/jail_booking_ingest.log --workdir /root/montanablotter -- /root/montanablotter/venv/bin/python3 /root/montanablotter/jail_booking_ingest.py --county jefferson

# Sanders County jail roster — poll every 2 hours
35 */2 * * * /root/montanablotter/venv/bin/python3 /root/montanablotter/job_runner.py --name jail_booking_ingest_sanders --log /root/montanablotter/jail_booking_ingest.log --workdir /root/montanablotter -- /root/montanablotter/venv/bin/python3 /root/montanablotter/jail_booking_ingest.py --county sanders
```

- [ ] **Step 2: Install the updated crontab**

```bash
crontab /root/montanablotter/crontab.txt
crontab -l | grep jail_booking
```

Expected: all 5 jail_booking entries visible (yellowstone, missoula, flathead, jefferson, sanders).

- [ ] **Step 3: Verify after first scheduled window**

Wait for the :05, :15, or :35 mark of the next even hour, then run:

```bash
sqlite3 /root/montanablotter/blotter.db "
SELECT county_slug, status, fetched_count, started_at
FROM jail_booking_runs
ORDER BY started_at DESC
LIMIT 20;
"
```

Each new county must show `status='success'` (not `'skipped'` or `'failed'`) **and** `fetched_count > 0`.

- [ ] **Step 4: Commit**

```bash
cd /root/montanablotter
git add crontab.txt
git commit -m "feat: schedule Flathead, Jefferson, Sanders jail booking ingest

2-hour cadence at :05, :15, :35 — staggered to avoid concurrent
DB writes with existing Yellowstone (:20) and Missoula (:50) jobs.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 6: Build Gallatin adapter (or disable if portal still down)

**Files:**
- Modify: `jail_booking_ingest.py` — `SKIPPED_SOURCES`, `SUPPORTED_ADAPTERS`, add `fetch_gallatin_bookings()`, add dispatch branch in `_run_source()`

Gallatin's roster URL (`https://gallatin-so-mt.zuercherportal.com/#/inmates`) uses the same Zuercher JSON API as Jefferson. `fetch_gallatin_bookings` is a new standalone function — no refactor of the existing Jefferson code.

- [ ] **Step 1: Check if Gallatin portal is live**

```bash
cd /root/montanablotter && source venv/bin/activate
python3 -c "
import requests
r = requests.post(
    'https://gallatin-so-mt.zuercherportal.com/api/portal/inmates/load',
    json={'name':'','race':'all','sex':'all','cell_block':'all','held_for_agency':'any',
          'in_custody':'','paging':{'start':0,'count':1},
          'sorting':{'sort_by_column_tag':'name','sort_descending':False}},
    headers={'User-Agent':'MontanaBlotter/1.0',
             'Accept':'application/json,*/*',
             'Referer':'https://gallatin-so-mt.zuercherportal.com/#/inmates'},
    timeout=15
)
print(r.status_code, r.text[:300])
"
```

**HTTP 200 with JSON → LIVE PATH (Steps 2–9).**
**Connection error or non-200 → DOWN PATH (Step 10 only).**

- [ ] **Step 2: [LIVE PATH] Remove Gallatin from SKIPPED_SOURCES first**

This must happen before adding the adapter — if `"gallatin"` stays in `SKIPPED_SOURCES`, `_run_source()` silently skips it even if a dispatch branch exists:

```python
SKIPPED_SOURCES = {
    "broadwater": "Official roster host is timing out from the ingest machine.",
    # "gallatin" removed — portal confirmed live
}
```

- [ ] **Step 3: [LIVE PATH] Add `fetch_gallatin_bookings` to `jail_booking_ingest.py`**

Add after `fetch_jefferson_bookings` (around line 838). This is a standalone function — do not modify `fetch_jefferson_bookings`:

```python
def fetch_gallatin_bookings(source_url: str) -> list[JailBookingRecord]:
    """Fetch current Gallatin County jail roster via Zuercher portal JSON API."""
    api_base = source_url.rstrip("/")
    if api_base.endswith("#/inmates"):
        api_base = api_base[:-9].rstrip("/")

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://montanablotter.com)",
            "Accept": "application/json,text/plain,*/*",
            "Referer": f"{api_base}/#/inmates",
        }
    )

    records: list[JailBookingRecord] = []
    offset = 0
    page_size = 50
    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00.000Z")

    while True:
        response = session.post(
            f"{api_base}/api/portal/inmates/load",
            json={
                "name": "",
                "race": "all",
                "sex": "all",
                "cell_block": "all",
                "held_for_agency": "any",
                "in_custody": today,
                "paging": {"start": offset, "count": page_size},
                "sorting": {"sort_by_column_tag": "name", "sort_descending": False},
            },
            timeout=45,
        )
        response.raise_for_status()
        payload = response.json() or {}
        rows = payload.get("records") or []

        for row in rows:
            raw_hold = (row.get("hold_reasons") or "").strip()
            if raw_hold:
                parts = [
                    _text_from_html(fragment)
                    for fragment in re.split(r"<br\s*/?>", raw_hold, flags=re.IGNORECASE)
                    if _text_from_html(fragment)
                ]
                charges_summary = "; ".join(parts[:4]) if parts else (
                    "Charge details available on the official Gallatin County inmate portal."
                )
            else:
                charges_summary = "Charge details available on the official Gallatin County inmate portal."

            arrest_date = _normalize_datetime(f"{row.get('arrest_date', '')} 00:00")
            identity_parts = [
                (row.get("name") or "").strip().upper(),
                (row.get("held_for_agency") or "").strip().upper(),
                (row.get("sex") or "").strip().upper(),
                (row.get("arrest_date") or "").strip(),
                charges_summary,
            ]
            source_record_id = hashlib.sha1("|".join(identity_parts).encode("utf-8")).hexdigest()[:20]
            records.append(
                JailBookingRecord(
                    source_record_id=source_record_id,
                    person_name=(row.get("name") or "").title(),
                    age=None,
                    booking_number=source_record_id[:12],
                    booking_at=arrest_date,
                    charges_summary=charges_summary,
                    source_url=f"{api_base}/#/inmates",
                )
            )

        offset += len(rows)
        total = int(payload.get("total_record_count") or 0)
        if not rows or offset >= total:
            break

    return records
```

- [ ] **Step 4: [LIVE PATH] Add `"gallatin"` to `SUPPORTED_ADAPTERS`**

```python
SUPPORTED_ADAPTERS = {"broadwater", "flathead", "gallatin", "jefferson", "missoula", "sanders", "yellowstone"}
```

Both Steps 2 and 4 must be complete before a live run — if either is missing, `_run_source` silently records `status='skipped'` with no error.

- [ ] **Step 5: [LIVE PATH] Add gallatin dispatch branch to `_run_source`**

In `_run_source` (around line 1230), add after the `elif county_slug == "flathead":` branch:

```python
    elif county_slug == "gallatin":
        records = fetch_gallatin_bookings(roster_url)
```

- [ ] **Step 6: [LIVE PATH] Write unit test for `fetch_gallatin_bookings`**

In `tests/test_ingestion_sources.py`, add inside `IngestionSourceTests`:

```python
def test_fetch_gallatin_bookings_parses_zuercher_response(self) -> None:
    from unittest import mock
    fake_payload = {
        "total_record_count": 1,
        "records": [{
            "name": "DOE, JANE",
            "held_for_agency": "Gallatin County SO",
            "sex": "F",
            "arrest_date": "2026-03-01",
            "hold_reasons": "THEFT<br/>ASSAULT",
        }],
    }
    empty_payload = {"total_record_count": 1, "records": []}

    def make_response(payload):
        r = mock.MagicMock()
        r.raise_for_status = mock.MagicMock()
        r.json.return_value = payload
        return r

    with mock.patch("requests.Session") as mock_session_cls:
        mock_session = mock.MagicMock()
        mock_session_cls.return_value = mock_session
        mock_session.headers = {}
        mock_session.post.side_effect = [
            make_response(fake_payload),
            make_response(empty_payload),
        ]
        records = jail_booking_ingest.fetch_gallatin_bookings(
            "https://gallatin-so-mt.zuercherportal.com/#/inmates"
        )

    self.assertEqual(len(records), 1)
    self.assertEqual(records[0].person_name, "Doe, Jane")
    self.assertIn("THEFT", records[0].charges_summary)

def test_fetch_gallatin_bookings_uses_fallback_when_no_charges(self) -> None:
    from unittest import mock
    fake_payload = {
        "total_record_count": 1,
        "records": [{
            "name": "DOE, JOHN",
            "held_for_agency": "Gallatin County SO",
            "sex": "M",
            "arrest_date": "2026-03-01",
            "hold_reasons": "",
        }],
    }
    empty_payload = {"total_record_count": 1, "records": []}

    def make_response(payload):
        r = mock.MagicMock()
        r.raise_for_status = mock.MagicMock()
        r.json.return_value = payload
        return r

    with mock.patch("requests.Session") as mock_session_cls:
        mock_session = mock.MagicMock()
        mock_session_cls.return_value = mock_session
        mock_session.headers = {}
        mock_session.post.side_effect = [
            make_response(fake_payload),
            make_response(empty_payload),
        ]
        records = jail_booking_ingest.fetch_gallatin_bookings(
            "https://gallatin-so-mt.zuercherportal.com/#/inmates"
        )

    self.assertIn("Gallatin County", records[0].charges_summary)
```

Run: `python3 -m pytest tests/test_ingestion_sources.py -v`
Expected: all tests PASS.

- [ ] **Step 7: [LIVE PATH] Dry-run Gallatin**

```bash
cd /root/montanablotter && source venv/bin/activate
python3 jail_booking_ingest.py --county gallatin --dry-run
```

Expected: `gallatin: fetched=N new=N updated=0 missing=0` where N > 0.

- [ ] **Step 8: [LIVE PATH] Commit**

```bash
cd /root/montanablotter
git add jail_booking_ingest.py tests/test_ingestion_sources.py
git commit -m "feat: add Gallatin County jail booking adapter

Gallatin uses Zuercher JSON API, same pattern as Jefferson.
Removes Gallatin from SKIPPED_SOURCES; adds to SUPPORTED_ADAPTERS.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

- [ ] **Step 10: [DOWN PATH — only if Gallatin portal is still unavailable]**

`_ensure_tracked_sources()` uses `INSERT OR IGNORE` — Gallatin is already in `jail_booking_sources` from a prior run, so the dict cannot be used to set `is_enabled`. Run a direct DB update instead:

```bash
sqlite3 /root/montanablotter/blotter.db "
UPDATE jail_booking_sources
SET is_enabled = 0,
    notes = 'Portal unavailable as of 2026-03-22 — Zuercher instance at gallatin-so-mt.zuercherportal.com not responding. Re-check next session.',
    updated_at = datetime('now')
WHERE county_slug = 'gallatin';
"
```

Verify:
```bash
sqlite3 /root/montanablotter/blotter.db \
  "SELECT county_slug, is_enabled, notes FROM jail_booking_sources WHERE county_slug='gallatin';"
```

Do NOT add Gallatin to `SUPPORTED_ADAPTERS`. Do NOT add a cron entry. Do NOT remove from `SKIPPED_SOURCES`. Skip the Gallatin entry in Task 8.

---

## Task 7: Investigate Cascade roster page and build adapter

**Files:**
- Modify: `jail_booking_ingest.py` — add `fetch_cascade_bookings()`, update `SUPPORTED_ADAPTERS`, add dispatch branch in `_run_source()`

Cascade County's roster URL is `https://www.cascadecountymt.gov/314/Inmate-Roster`. This is a county government page — the actual roster data format is unknown until investigated.

- [ ] **Step 1: Fetch and inspect the Cascade roster page**

```bash
cd /root/montanablotter && source venv/bin/activate
python3 -c "
import requests
r = requests.get(
    'https://www.cascadecountymt.gov/314/Inmate-Roster',
    headers={'User-Agent':'MontanaBlotter/1.0'},
    timeout=15,
    allow_redirects=True
)
print('Status:', r.status_code)
print('Final URL:', r.url)
print('Content-Type:', r.headers.get('content-type',''))
print()
print(r.text[:4000])
"
```

Identify which case applies:

| What you see | Action |
|---|---|
| Zuercher portal URL in iframe or redirect | Use same pattern as `fetch_gallatin_bookings` |
| HTML `<table>` with inmate rows | Use `fetch_yellowstone_bookings` / `_parse_yellowstone_roster` as reference |
| `<div class="inmate-entry">` blocks | Use `fetch_flathead_bookings` / `_parse_flathead_roster` as reference |
| JMS public form (`functions/search.php`) | Use `fetch_sanders_bookings` as reference |
| Link to downloadable PDF or CSV | Fetch the file; parse with existing PDF utils or csv module |
| **Login/auth wall** | See Step 2 (skip path) |
| **Page 404 or connection error** | See Step 2 (skip path) |

- [ ] **Step 2: Skip path — if page is dead, auth-gated, or returns no parseable roster data**

If Cascade's page requires a login, returns a 404/5xx, or contains no machine-readable inmate data:

```bash
sqlite3 /root/montanablotter/blotter.db "
UPDATE jail_booking_sources
SET is_enabled = 0,
    notes = 'Cascade roster URL unscrapable as of 2026-03-22 — <describe what you found>. Re-investigate next session.',
    updated_at = datetime('now')
WHERE county_slug = 'cascade';
"
```

Do NOT add `"cascade"` to `SUPPORTED_ADAPTERS`. Do NOT add a dispatch branch. Do NOT add a cron entry. Skip the remaining steps in this task.

- [ ] **Step 3: [If parseable] Write `fetch_cascade_bookings()`**

Add after `fetch_flathead_bookings` in `jail_booking_ingest.py`. Exact implementation depends on Step 1 findings. All records must:
- Call `_normalize_datetime()` for `booking_at`
- Use a stable `source_record_id` (booking number if available; otherwise SHA-1 of `name|date|charges`)

Skeleton:

```python
def fetch_cascade_bookings(source_url: str) -> list[JailBookingRecord]:
    """Fetch current Cascade County jail roster.

    Roster served at <URL / format found in Step 1>.
    """
    # Implementation based on investigation
    ...
```

- [ ] **Step 4: Write a unit test for the Cascade parser**

In `tests/test_ingestion_sources.py`, add a test using a minimal fixture matching the live structure:

```python
def test_cascade_parser_extracts_booking(self) -> None:
    sample = """<paste a representative snippet from the live roster>"""
    records = jail_booking_ingest._parse_cascade_roster(
        sample, "https://..."
    )
    self.assertGreater(len(records), 0)
    self.assertTrue(records[0].person_name)
    self.assertTrue(records[0].source_record_id)
```

Run: `python3 -m pytest tests/test_ingestion_sources.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Add `"cascade"` to `SUPPORTED_ADAPTERS`**

```python
SUPPORTED_ADAPTERS = {"broadwater", "cascade", "flathead", "gallatin", "jefferson", "missoula", "sanders", "yellowstone"}
```

(Remove `"gallatin"` if it ended up in the DOWN PATH in Task 6.)

- [ ] **Step 6: Add `cascade` dispatch branch to `_run_source`**

Add after the `elif county_slug == "broadwater":` branch:

```python
    elif county_slug == "cascade":
        records = fetch_cascade_bookings(roster_url)
```

- [ ] **Step 7: Dry-run Cascade**

```bash
cd /root/montanablotter && source venv/bin/activate
python3 jail_booking_ingest.py --county cascade --dry-run
```

Expected: `cascade: fetched=N new=N updated=0 missing=0` where N > 0.

- [ ] **Step 8: Commit**

```bash
cd /root/montanablotter
git add jail_booking_ingest.py tests/test_ingestion_sources.py
git commit -m "feat: add Cascade County jail booking adapter

Cascade roster format: <brief description of what you found>.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 8: Add Phase 2 cron entries (Cascade and/or Gallatin)

**Files:**
- Modify: `crontab.txt`

Only add entries for counties that passed dry-run in Tasks 6 and 7. Skip any county that ended in a DOWN PATH.

- [ ] **Step 1: Add cron entries for each live county**

```
# Cascade County jail roster — poll every 2 hours
45 */2 * * * /root/montanablotter/venv/bin/python3 /root/montanablotter/job_runner.py --name jail_booking_ingest_cascade --log /root/montanablotter/jail_booking_ingest.log --workdir /root/montanablotter -- /root/montanablotter/venv/bin/python3 /root/montanablotter/jail_booking_ingest.py --county cascade

# Gallatin County jail roster — poll every 2 hours
55 */2 * * * /root/montanablotter/venv/bin/python3 /root/montanablotter/job_runner.py --name jail_booking_ingest_gallatin --log /root/montanablotter/jail_booking_ingest.log --workdir /root/montanablotter -- /root/montanablotter/venv/bin/python3 /root/montanablotter/jail_booking_ingest.py --county gallatin
```

- [ ] **Step 2: Install and verify**

```bash
crontab /root/montanablotter/crontab.txt
crontab -l | grep jail_booking
```

Expected: up to 7 entries depending on how many counties made it through.

- [ ] **Step 3: Verify after first scheduled window**

```bash
sqlite3 /root/montanablotter/blotter.db "
SELECT county_slug, status, fetched_count, started_at
FROM jail_booking_runs
ORDER BY started_at DESC
LIMIT 20;
"
```

All newly-scheduled counties must show `status='success'` and `fetched_count > 0`. Any county in a DOWN PATH should show no new run (or `status='skipped'` if it was queried).

- [ ] **Step 4: Commit**

```bash
cd /root/montanablotter
git add crontab.txt
git commit -m "feat: schedule Cascade and Gallatin jail booking ingest

Completes 2-hour staggered schedule. Final slot assignments:
:45 Cascade, :55 Gallatin.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Final verification

```bash
sqlite3 /root/montanablotter/blotter.db "
SELECT
  s.county_slug,
  s.is_enabled,
  s.last_success_at,
  r.status,
  r.fetched_count
FROM jail_booking_sources s
LEFT JOIN jail_booking_runs r ON r.source_id = s.id
  AND r.started_at = (
    SELECT MAX(started_at) FROM jail_booking_runs WHERE source_id = s.id
  )
ORDER BY s.county_slug;
"
```

Expected: All `is_enabled=1` counties show `status='success'` and `fetched_count > 0`. Any `is_enabled=0` county (Gallatin or Cascade if down-pathed) shows no recent run.
