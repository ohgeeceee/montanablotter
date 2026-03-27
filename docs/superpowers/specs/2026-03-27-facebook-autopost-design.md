# Facebook Auto-Post: Blotter Posts + City Meetings

**Date:** 2026-03-27
**Status:** Approved

---

## Overview

Two features:
1. **Blotter posts → Facebook** — wire the existing (but unwired) `auto_queue_post_if_enabled()` into `processor.py` so new blotter posts are automatically queued for Facebook.
2. **City meetings → Facebook** — new module posting upcoming city meetings: on discovery, day-before reminder, and a weekly digest every Monday.

---

## Architecture

### Part 1: Blotter post auto-queue (processor.py hookup)

The `facebook_publisher.auto_queue_post_if_enabled()` function already exists and is gated by `facebook_enabled` + `facebook_auto_enqueue_enabled` settings. It just isn't called.

**Change:** In `processor.py`, inside `_publish_blotter_outputs()`, after `summarizer.generate_posts()` succeeds, fetch all post IDs for the blotter and call `auto_queue_post_if_enabled(post_id)` for each. Wrapped in try/except so a failure doesn't break the pipeline. The existing `facebook_worker` cron (runs at :05/:20/:35/:50) picks them up and publishes if `facebook_auto_publish_enabled` is on.

No schema changes. No new settings.

---

### Part 2: Meeting Facebook publisher

#### New module: `meeting_facebook_publisher.py`

Mirrors `facebook_publisher.py` structure. Handles queue management and publishing for meeting posts.

**New DB table: `facebook_meeting_queue`**

```sql
CREATE TABLE facebook_meeting_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL,
    trigger_type TEXT NOT NULL,         -- 'discovery' or 'day_before'
    dedupe_key TEXT NOT NULL UNIQUE,    -- 'fb_meeting:{meeting_id}:{trigger_type}'
    status TEXT NOT NULL DEFAULT 'queued', -- queued | processing | posted | failed | skipped
    facebook_post_id TEXT,
    scheduled_for TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    posted_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
```

**Settings** (stored in `app_settings`, reusing existing keys where possible):
- `facebook_enabled` — master on/off (existing)
- `facebook_page_id` — (existing)
- `facebook_page_access_token` — (existing)
- `facebook_meetings_enabled` — new; gates all meeting posting
- `facebook_meetings_digest_enabled` — new; gates weekly digest specifically

**Key functions:**
- `auto_queue_meeting_if_enabled(meeting_id, trigger_type)` — checks settings, dedupes on `meeting_id + trigger_type`, inserts into queue
- `run_meeting_facebook_queue(max_items)` — processes queued items; renders message; calls Graph API
- `_render_meeting_message(meeting_row)` — builds the Facebook post text with per-city hashtags

**Message format:**

```
📅 {body_name} — {meeting_date_human} at {meeting_time}

{location_name}

Agenda: {agenda_url}

#GreatFalls #GreatFallsMT #CityCouncil #Montana #MontanaBlotter #PublicMeetings
```

**Hashtag generation:**
- City tag: `#` + city_name with spaces removed (e.g. `Great Falls` → `#GreatFalls`)
- State+city tag: `#` + city_name_no_spaces + `MT` (e.g. `#GreatFallsMT`)
- Body tag: derived from `body_name` — `Council` → `#CityCouncil`, `Commission` → `#CityCommission`, `Board` → `#CityBoard`, default `#CityMeeting`
- Fixed: `#Montana #MontanaBlotter #PublicMeetings`

---

### Part 3: Discovery hook

**Change:** Extend `sync_scraped_meetings()` in `public_meetings.py` to also return `new_meeting_ids: list[int]` — IDs of meetings that were inserted (not updated) in this run. In `agendas_ingest.py`, after `sync_scraped_meetings()` returns, call `auto_queue_meeting_if_enabled(meeting_id, 'discovery')` for each ID in `new_meeting_ids`. Wrapped in try/except.

Only newly created meetings get a discovery post. The dedupe key (`fb_meeting:{id}:discovery`) prevents re-posting if somehow called twice.

---

### Part 4: `meeting_facebook_worker.py`

CLI worker with two modes:

- `--mode queue` — find meetings where `meeting_date = tomorrow` and `status = 'upcoming'`; call `auto_queue_meeting_if_enabled(meeting_id, 'day_before')` for each. Safe to run daily — dedupe key prevents duplicate queue entries.
- `--mode publish` — call `run_meeting_facebook_queue()`. Processes up to N queued items (default 5).

---

### Part 5: `meeting_digest_worker.py`

Runs Monday mornings. Queries all `upcoming` meetings for the current week (Monday through Sunday). Groups by city. Renders a single Facebook post. Posts directly to Graph API (no queue — one post per week).

**Post format:**

```
📋 This week's public meetings in Montana — week of {Mon date}

🏛 Great Falls
  • City Commission — Tue Mar 31 at 7:00pm
  • Planning Board — Thu Apr 2 at 6:00pm

🏛 Missoula
  • City Council — Mon Mar 30 at 7:00pm

Full schedule: https://montanablotter.com/meetings

#Montana #MontanaBlotter #PublicMeetings #CityHall #Montana
```

Falls back gracefully if no meetings are found (skips posting).

---

## Data Flow

```
agendas_ingest.py
  └─ sync_scraped_meetings() → new meeting IDs
       └─ auto_queue_meeting_if_enabled(id, 'discovery')
            └─ facebook_meeting_queue (status=queued)

meeting_facebook_worker.py --mode queue  [daily 8am]
  └─ finds meetings where meeting_date = tomorrow
       └─ auto_queue_meeting_if_enabled(id, 'day_before')
            └─ facebook_meeting_queue (status=queued)

meeting_facebook_worker.py --mode publish  [every 15 min]
  └─ run_meeting_facebook_queue()
       └─ Graph API → facebook_meeting_queue (status=posted)

meeting_digest_worker.py  [Monday 8:05am]
  └─ queries upcoming meetings for week
       └─ renders digest post → Graph API directly
```

---

## Cron Additions (crontab.txt)

```
# Meeting Facebook — queue day-before reminders daily at 8am
0 8 * * * /root/montanablotter/venv/bin/python3 /root/montanablotter/job_runner.py --name meeting_fb_queue --log /root/montanablotter/meeting_facebook.log --workdir /root/montanablotter -- /root/montanablotter/venv/bin/python3 /root/montanablotter/meeting_facebook_worker.py --mode queue

# Meeting Facebook — publish queue every 15 min (offset from blotter worker)
10,25,40,55 * * * * /root/montanablotter/venv/bin/python3 /root/montanablotter/job_runner.py --name meeting_fb_publish --log /root/montanablotter/meeting_facebook.log --workdir /root/montanablotter -- /root/montanablotter/venv/bin/python3 /root/montanablotter/meeting_facebook_worker.py --mode publish

# Weekly meetings digest — Monday 8:05am
5 8 * * 1 /root/montanablotter/venv/bin/python3 /root/montanablotter/job_runner.py --name meeting_digest --log /root/montanablotter/meeting_digest.log --workdir /root/montanablotter -- /root/montanablotter/venv/bin/python3 /root/montanablotter/meeting_digest_worker.py
```

---

## Error Handling

- All hooks (in processor.py and agendas_ingest.py) are wrapped in try/except — a Facebook failure never breaks ingestion.
- `run_meeting_facebook_queue()` marks failed items in DB; they do not retry automatically (same pattern as blotter publisher).
- If `facebook_meetings_enabled` is off, all meeting queue calls are no-ops.
- Digest worker skips posting if no meetings found for the week.

---

## Files Changed / Created

| File | Change |
|------|--------|
| `processor.py` | Add auto-queue call after post generation |
| `public_meetings.py` | Extend `sync_scraped_meetings()` to return `new_meeting_ids` |
| `agendas_ingest.py` | Add auto-queue call for newly created meetings |
| `meeting_facebook_publisher.py` | **New** — queue + publish engine for meetings |
| `meeting_facebook_worker.py` | **New** — CLI worker (--mode queue / --mode publish) |
| `meeting_digest_worker.py` | **New** — weekly digest poster |
| `init_db.py` | Add `facebook_meeting_queue` table migration |
| `crontab.txt` | Add 3 new cron entries |
