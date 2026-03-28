# Dynamic Crisis Banner Design

**Date:** 2026-03-28
**Status:** Approved

## Overview

Each morning, after the daily briefing runs, Claude scans yesterday's blotter posts and generates a relevant top-of-site banner message. If an active public safety crisis is detected (wildfire, flood, storm, search/rescue, major incident), the banner highlights it with a crisis-specific headline and body. If no crisis is found, the banner shows a generic evergreen "support our journalism" message. The banner is always enabled.

## Architecture

All changes live in `morning_briefing.py`. No new scripts, no new cron entries. The function runs at the end of `run_briefing()`, using the posts already loaded for the admin briefing.

Settings are written to the `app_settings` table via `_save_app_setting` from `utils/app_settings.py`. The banner reads those settings at request time via `_winter_storm_banner_config()` in `app.py` — no changes needed there.

## Components

### `_update_crisis_banner(posts)` (new function in `morning_briefing.py`)

**Input:** List of yesterday's post rows (title, summary, agency_name, county).

**Flow:**
1. Build a compact text blob from post titles and summaries (capped at ~3000 chars to stay within token budget).
2. Call Claude API (`claude-sonnet-4-6`) with a structured prompt requesting JSON output with fields: `crisis_detected` (bool), `crisis_type` (str or null), `headline` (≤80 chars), `body` (≤160 chars).
3. Parse the JSON response. If `crisis_detected` is true, use the crisis headline/body. If false, use the evergreen fallback.
4. Write three settings to `app_settings`:
   - `winter_storm_banner_enabled` → `"1"` (always on)
   - `winter_storm_banner_headline` → generated or evergreen headline
   - `winter_storm_banner_body` → generated or evergreen body

**Evergreen fallback text:**
- Headline: `"Support Montana public safety journalism"`
- Body: `"Help fund ongoing dispatch monitoring, records coverage, and county-by-county reporting across Montana."`

**Error handling:**
- If Claude API call fails or returns unparseable JSON: log the error, leave existing banner settings unchanged, return without raising.
- If `posts` is empty: write evergreen text (no posts = no crisis to detect).

### Label change

`WINTER_STORM_SUPPORT_BANNER_DEFAULTS['label']` in `app.py` changes from `"Winter Storm Support"` → `"Public Safety Alert"` so it reads correctly year-round regardless of crisis type.

## Data Flow

```
run_briefing()
  └── get_posts_for_date(yesterday)  [already called]
  └── [send admin + subscriber emails]
  └── _update_crisis_banner(posts)   [NEW — runs last]
        └── Claude API call
        └── _save_app_setting(conn, 'winter_storm_banner_enabled', '1')
        └── _save_app_setting(conn, 'winter_storm_banner_headline', headline)
        └── _save_app_setting(conn, 'winter_storm_banner_body', body)
```

## Claude Prompt

```
You are an editor for Montana Blotter, a Montana public safety news site.

Review these law enforcement blotter summaries from yesterday and determine if there is an active public safety crisis that readers should know about. Crises include: wildfires, floods, winter storms, major search-and-rescue operations, missing persons, or other significant public safety emergencies affecting Montana communities.

Respond ONLY with valid JSON in this exact format:
{
  "crisis_detected": true or false,
  "crisis_type": "brief crisis type or null",
  "headline": "Banner headline, max 80 characters",
  "body": "Banner body, max 160 characters"
}

If no crisis is detected, set crisis_detected to false and write an evergreen message encouraging readers to support Montana public safety coverage.

Blotter summaries:
<SUMMARIES>
```

## Testing

- Run `python morning_briefing.py` manually with blotter data that includes a storm/fire incident — verify banner settings update in DB.
- Run with no posts — verify evergreen text is written.
- Simulate API failure (bad key) — verify existing settings are unchanged.
- Check live site banner after a run.

## Out of Scope

- Admin UI for the label field (label stays hardcoded default).
- Banner auto-disable on no-crisis days (always enabled per user decision).
- Per-blotter banner updates (daily cadence only).
