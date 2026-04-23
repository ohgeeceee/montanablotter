# Personalized Reader Feed — Design Spec
**Date:** 2026-03-22
**Project:** Montana Blotter

---

## Overview

Add relevance-scored personalization to the Montana Blotter public feed. Logged-in `public_users` get a `/for-you` page with a fully personalized post list. The homepage `/` gets soft-ranked based on the visitor's detected or stated county interest. Anonymous visitors are geo-detected silently via MaxMind GeoLite2.

---

## Goals

- Surface locally relevant blotter posts without requiring an account
- Reward logged-in users with a richer, county- and incident-type-aware feed
- Track reading history to improve recommendations over time
- Introduce no new external services beyond the MaxMind GeoLite2 free database

---

## Non-Goals

- Machine learning / embeddings
- Claude API calls on the critical path
- Push notifications or email personalization (separate feature)
- Admin-facing recommendation controls

---

## Architecture

### New module: `recommender.py`

Single-responsibility module. Public interface:

```python
def get_ranked_posts(
    conn: sqlite3.Connection,
    county_set: set[str],            # user's counties (from prefs, geo, or clicks)
    type_affinities: dict[str, int], # incident_type → click count
    limit: int = 20,
) -> list[sqlite3.Row]:
    # candidate pool size is an internal constant, not a caller parameter
    ...

def get_user_signals(
    conn: sqlite3.Connection,
    session_id: str | None,
    public_user_id: int | None,
) -> tuple[set[str], dict[str, int]]:
    """Return (county_set, type_affinities) for a visitor."""
    ...

def detect_geo_county(ip: str) -> str | None:
    """Return a Montana county name from IP, or None on failure."""
    ...
```

### New DB table: `post_clicks`

```sql
CREATE TABLE IF NOT EXISTS post_clicks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT,
    public_user_id  INTEGER,
    post_id         INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    incident_type   TEXT,
    county          TEXT,
    clicked_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_post_clicks_session  ON post_clicks(session_id, clicked_at);
CREATE INDEX IF NOT EXISTS idx_post_clicks_user     ON post_clicks(public_user_id, clicked_at);
CREATE INDEX IF NOT EXISTS idx_post_clicks_post     ON post_clicks(post_id);
```

Migration added to `init_db.migrate()` via `ALTER TABLE`-style `CREATE TABLE IF NOT EXISTS` block.

**Pruning:** `post_clicks` has no TTL enforcement in the DB. The `get_user_signals()` query already filters to 30 days, so stale rows are ignored but not deleted. A future cron job should periodically `DELETE FROM post_clicks WHERE clicked_at < datetime('now', '-90 days')` to prevent unbounded growth.

---

## Scoring Algorithm

For each candidate post:

```
score = (county_score + type_score) * recency_factor
```

| Signal | Role | Value |
|---|---|---|
| County match (per matching county) | Additive | +10 |
| Incident type affinity (per known type) | Additive | +5 |
| Recency decay | **Multiplier** applied to the sum above | `1 / (1 + days_old * 0.1)` |

Recency is a **multiplicative decay**, not an additive term. The county and type scores are summed first, then multiplied by the decay factor:

- Post from today, county match + type match: `(10 + 5) * 1.0 = 15`
- Same post from 10 days ago: `(10 + 5) * 0.5 = 7.5`
- Post with no signal match: `(0 + 0) * recency = 0` — falls to bottom regardless of age

Posts with `score == 0` are appended in recency order after scored posts, so the feed never goes empty.

---

## Signal Resolution

### Logged-in `public_users`
1. `county_set` ← `subscription_counties` (comma-separated field, split matches existing `PublicUser` logic in `app.py`)
2. `type_affinities` ← `post_clicks` for this `public_user_id`, last 30 days, grouped by `incident_type`

### Anonymous visitors
1. `county_set` ← geo-IP result cached in `session['geo_county']` as a single-element set
2. `type_affinities` ← `post_clicks` for this `session_id`, last 30 days
3. Geo-IP failure → empty `county_set`, feed shows unranked recent posts (graceful fallback)

---

## Geo-IP Detection

- Library: `geoip2` (pip install geoip2)
- Database: MaxMind GeoLite2-City.mmdb (free, ~60MB, downloaded separately, path in `config.py` as `GEOIP_DB_PATH`)
- Detection runs once per session in a `before_request` hook, result stored in `session['geo_county']`
- Client IP resolved via the existing `_client_ip()` helper in `app.py` (reads `X-Forwarded-For` correctly behind gunicorn/nginx) — do NOT use `request.remote_addr` directly
- **County extraction:** MaxMind GeoLite2 returns a city name (e.g. "Great Falls"), not a county name. `detect_geo_county()` must map city → county using a hardcoded Montana city-to-county dict (e.g. `{"Great Falls": "Cascade", "Billings": "Yellowstone", ...}`). The function returns `None` for unmapped city names. This is acceptable — the fallback (unranked feed) handles it gracefully.
- If DB file is missing, lookup fails, or city is not in the mapping dict, detection returns `None` silently — no error surfaced to user

---

## Routes

### `GET /` (modified)
- Calls `get_user_signals()` then `get_ranked_posts()` before passing posts to template
- Candidate pool: 50 most recent published posts
- Returns top 20 by score

### `GET /for-you` (new, requires public_user login)
- Calls `get_user_signals()` with `public_user_id` set
- Candidate pool: 100 most recent published posts
- Returns top 30 by score
- Template shows: county preference strip ("Your counties: Cascade, Gallatin · Edit") + county tag on each post card

### `POST /api/track-click` (new, no auth required)
- Accepts JSON `{"post_id": N}`
- Looks up `posts.county` and `posts.incident_type` for that post
- Inserts into `post_clicks` with `session_id` (from cookie) and `public_user_id` (if logged in)
- Returns `{"ok": true}` — errors swallowed silently
- Called via `fetch()` from post detail template (fire-and-forget)

---

## Session ID for Anonymous Tracking

- A random UUID is generated on first visit and stored in Flask's signed `session` dict as `session['mb_sid']`
- Generated and checked in the `before_request` hook: `if 'mb_sid' not in session: session['mb_sid'] = str(uuid.uuid4())`
- Flask's session cookie infrastructure writes it automatically — no separate `response.set_cookie()` or `after_request` hook needed
- Used as `session_id` in `post_clicks` rows

---

## Template Changes

- **Post list partial** (used by `/` and `/for-you`): no change needed — county is already shown
- **`/for-you` template**: new, extends base layout, reuses post list partial, adds county strip header
- **`templates/post_detail.html`**: add one `<script>` block with `fetch('/api/track-click', ...)` on page load

---

## Dependencies

| Package | Purpose | Already installed? |
|---|---|---|
| `geoip2` | MaxMind GeoLite2 reader | No — add to requirements |
| MaxMind GeoLite2-City.mmdb | IP→county DB file | No — download separately |

Config addition:
```python
# config.py
GEOIP_DB_PATH = '/root/montanablotter/GeoLite2-City.mmdb'
```

---

## Error Handling & Fallbacks

| Failure | Behavior |
|---|---|
| Geo-IP DB missing | Skip detection; `geo_county` = None; homepage shows unranked feed |
| Geo-IP lookup error | Same as above |
| `post_clicks` write fails | Log warning; do not surface to user |
| No signal data for user | Score = 0 for all posts; feed shows recency-sorted posts |
| `public_user` not logged in on `/for-you` | `redirect(url_for('public_login', next='/for-you'))` — matches existing convention |

---

## Files Changed / Created

| File | Change |
|---|---|
| `recommender.py` | **New** — scoring engine + geo-IP + signal resolution |
| `init_db.py` | Add `post_clicks` table creation in `migrate()` |
| `app.py` | Add `before_request` hook, modify `/`, add `/for-you` + `/api/track-click` routes; add `/api/track-click` to the `track_page_view` exclusion list to prevent polluting the `page_views` analytics table |
| `templates/for_you.html` | **New** — personalized feed page |
| `templates/post_detail.html` | Add `fetch()` click tracking script |
| `config.py` | Add `GEOIP_DB_PATH` |
| `requirements.txt` | Add `geoip2` |
