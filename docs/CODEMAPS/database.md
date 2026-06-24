# Database Architecture — Montana Blotter

Single-source overview of the SQLite-backed data layer: schema init/migrations, the 80+ table surface, conventions, indexes, and the split-off `page_views.db`.

## Module Map

| File / Module | Responsibility |
|---------------|----------------|
| `init_db.py` | Schema initialization (`init_database()`) and additive migrations (`migrate()`). Calls per-subsystem `ensure_*_schema()` helpers. |
| `db.py` | Runtime connection factory `connect_db()`/`get_db()`, Turso/libsql replica support, `page_views.db` connection helper, `timed_db_transaction()` lock observability. |
| `config.py` | `DB_PATH`, `DB_TIMEOUT_SECONDS`, `DB_BUSY_TIMEOUT_MS`, plus Turso envs used by `db.py`. |
| `services/court/tracker.py` | Court/case schema (`court_sources`, `courts`, `court_cases`, `court_events`, `court_filings`, ...). |
| `services/ingestion/warrants/models.py` | `warrants` schema and model. |
| `services/api/auth.py` | API client/request-log schema (`api_clients`, `api_request_logs`). |
| `services/persons/missing.py` | Missing-person schema and alert tables. |
| `services/meetings/public.py` | Public meeting / agenda (`meeting_locations`, ...) schema. |
| `services/admin/case_journeys.py`, `services/agents/mission_control.py`, `services/monetization/bondsman.py`, etc. | Modular schema helpers for their features. |

## Schema Philosophy

Additive migrations only. `init_database()` is intended for fresh installs; `migrate()` is idempotent and safe against populated databases:

- All table creation uses `CREATE TABLE IF NOT EXISTS`.
- Column additions use `try/except sqlite3.OperationalError` to ignore "duplicate column" errors.
- `init_db.py` exposes a private `_safe_add_column()` that re-raises real failures.
- Old schema changes are backfilled with `UPDATE` statements gated on `NULL` to keep re-runs safe.
- `posts` had a non-NULL `record_id` constraint removed by renaming the legacy table, recreating the table with `record_id NULL`, and migrating common columns.

## Conventions

- **WAL mode** (`PRAGMA journal_mode = WAL; PRAGMA synchronous = NORMAL`) in `init_db.py` and `db.py` when `enable_wal=True`, plus `PRAGMA foreign_keys = ON`.
- **Busy timeout**: `MB_DB_BUSY_TIMEOUT_MS` (default 30000 ms).
- **Row factory**: `sqlite3.Row` by default; `db.py` adds a `_DictRow` wrapper for libsql/Turso compatibility.
- **Text timestamps**: most tables use `TEXT` ISO-8601-ish strings with `DEFAULT (datetime('now'))`.
- **JSON columns**: stored as `TEXT` and deserialized in Python (`charges_json`, `facts_json`, `metrics_json`, etc.).
- **Boolean flags**: stored as `INTEGER` (`0`/`1`).
- **Names/slugs**: normalized slugs (lower-case, punctuation stripped, spaces → hyphens) for person names and court defendants.

## Core Tables

### Auth / Admin

- `users` — Flask-Login admin users (`username`, `password`, `email`, `role`, `is_active`, `mfa_secret`, `mfa_enabled`).
- `auth_login_attempts` — Failed/successful admin login attempts for brute-force throttling.
- `audit_logs` — Admin action audit trail (`action`, `target_type`, `target_id`, `metadata_json`).
- `app_settings` — Key/value runtime settings.

### Source Documents / Ingestion

- `source_documents` — Deduped raw source files by `content_sha256`, captures email metadata, storage path, extracted text, extraction warnings.
- `source_registry` — Adapter registry for tracked sources (`adapter_name`, `poll_interval_seconds`, `is_enabled`).
- `source_artifacts` — Cached fetched artifacts tied to registry/documents.
- `ingestion_jobs` — Pipeline job tracking per source document (`status`, `retry_count`, `last_error`).
- `pipeline_events` — Per-job `stage`/`status` event log.

### Blotter Core

- `blotters` — PDF/source batch container (`filename`, `county`, `upload_date`, `incident_count`, `status`, `file_path`, `source_type`, `source_document_id`).
- `records` — Individual incidents (`date`, `time`, `incident`, `incident_type`, `location`, `county`, `officer`, `cfs_number`, `charge_category`).
- `command_logs` — Detailed narrative log lines bound to `record_id`.
- `posts` — Public blotter-level digests (`record_id` nullable, `blotter_id`, `title`, `summary`, `county`, `agency_type`, `incident_date`, plus audit/SEO columns).
- `blog_posts` / `story_candidates` / `blog_draft_reviews` / `blog_post_sources` — Editorial/blog pipeline.

### Jail & Court

- `jail_booking_sources` / `jail_bookings` / `jail_booking_runs` — Inmate roster ingestion, per-source tracking, and run stats.
- `court_sources` / `courts` / `court_cases` / `court_events` / `court_filings` / `court_case_snapshots` / `court_alerts` / `court_alert_events` — Court tracker schemas maintained in `services/court/tracker.py`.
- `booking_case_links` — Disposition watcher joins jail bookings ↔ court cases.

### Public Users / Engagement

- `public_users` — Site account holders (`email`, `password_hash`, `display_name`, `subscription_counties`, `subscribe_digest`, `facebook_id`).
- `public_comments` — Moderated comments on records/posts (status `pending`).
- `password_reset_tokens` — Hashed reset tokens with `expires_at`.
- `subscribers` — Anonymous/cookie digest subscribers by email.
- `alert_subscriptions` — Immediate county alerts (`email`, `county`, `alert_types`, `token`).
- `name_watches` — Name-based appearance alerts.
- `push_subscriptions` — Web push endpoints.

### Monetization

- `donations` / `donation_events` / `payment_webhook_events` — Donation and webhook audit trail.
- `bail_ad_inquiries` / `bail_ad_orders` / `bail_ad_creatives` / `bail_ad_slots` / `bail_ad_events` — Bail-bond ad program.
- `bail_consumer_leads` / `bail_consumer_lead_events` / `bail_agency_outreach` / `bail_agency_email_logs` — Lead routing and agency outreach.
- `recovery_ad_orders` / `recovery_ad_listings` — Recovery-center ad listings.
- `attorney_referrals` / `attorney_sponsored_claims` — Attorney directory and sponsored placement requests.
- `ad_unlock_grants` — Ad-watched warrant-page unlock grants.

### Premium / Alerting

- `user_alert_profiles` — Granular user alert filters (counties, cities, radius, severity, frequency, channels).
- `notification_queue` — Outbound notification queue with status/delivery tracking.

### Analytics / Mapping / SEO

- `incident_geocodes` — Geocoded `record_id` locations with lat/lng/confidence.
- `safety_scorecards` — Cached neighborhood/city scorecards.
- `page_views` — Lightweight visitor analytics (`path`, `ip_hash`, `referrer`, `created_at`).
- `pattern_clicks` / `subscribe_events` — Interaction telemetry.
- `case_status_searches` / `sponsored_digests` / `case_status_*` — Case-status lookup + sponsorship.
- `charge_explainers` — Evergreen charge-info pages.
- `unsplash_image_cache` — Local image cache keyed by slug.

### Other Important Subsystem Tables

- `warrants` — Public warrant records (`source_record_id`, `person_name`, `charges_text`, `status`).
- `missing_persons`, `missing_person_alert_deliveries`, `missing_person_push_subscriptions`, `missing_person_source_stats`.
- `code_violation_sources`, `property_addresses`, `code_violations`, `license_sanction_*`, `civil_filing_sources`, `civil_filings`.
- `crash_incidents`, `zip_geocode_cache`.
- `sex_offenders`, `sex_offender_snapshots`, `sex_offender_changes`, `sex_offender_alert_subscriptions`.
- `fwp_violations` from `services/persons/fwp_violations_scraper.py`.
- `agency_contacts`.
- `api_data_tokens` / `api_data_token_hits` / `api_token_deliveries` — B2B data API tokens (see `api-auth.md`).
- `api_clients` / `api_request_logs` — Public API key gating (see `api-auth.md`).

## Key Indexes

- Records lookup: `idx_records_county`, `idx_records_date`, `idx_records_blotter`, `idx_records_cfs`, `idx_records_county_date_time`, `idx_records_charge_category`.
- Posts lookup: `idx_posts_county`, `idx_posts_city`, `idx_posts_agency_type`, `idx_posts_incident_date`, `idx_posts_audit_status`, `idx_posts_case_status`.
- Jail: `idx_jail_bookings_lookup`, `idx_jail_bookings_source`, `idx_jail_bookings_person`, `idx_jail_bookings_hash_id`, `idx_jail_bookings_name_slug`.
- Warrants: `idx_warrants_county_status`, `idx_warrants_person`, `idx_warrants_source_id`.
- Court: `idx_court_cases_court`, `idx_court_cases_case_type`, `idx_court_cases_criminal`, `idx_court_cases_defendant_slug`, `idx_court_cases_defendant_last_first`.
- Tokens/logs: `idx_adt_token_hash`, `idx_api_clients_key_hash`, `idx_api_logs_client_time`.

## page_views.db Separation

The `page_views` table lives in a separate local SQLite file (`page_views.db`, env `MB_PAGE_VIEWS_DB_PATH`) by default. Rationale:

- Keeps high-volume analytics writes out of the main `blotter.db`.
- Avoids counting high-churn analytics data against a Turso cloud storage quota when `db.py` is using an embedded replica.
- Created on-demand by `db.connect_page_views()` with same `page_views` schema as a fallback.

## Backup / Ops Notes

- `init_database()` backs up an existing `blotter.db` to `blotter.db.backup.<timestamp>` before writing.
- `migrate()` does **not** auto-backup; run DB backups before deployment.
- Long-running ingestion uses `db.timed_db_transaction()` to surface lock contention/slow transactions.
- When Turso is enabled (`MB_TURSO_ENABLED`, `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`), `connect_db()` uses a per-PID embedded replica and falls back to local SQLite on 403/forbidden or connection errors.
- Avoid sharing WAL files between gunicorn workers; `db.py` uses per-PID replica paths for Turso and SQLite files should not be shared across container/filesystem boundaries without caution.

## Gotchas

- **Two API-token systems**: `services/api/auth.py` (`api_clients`) for public/website keys, and `api_data_tokens` (`init_db.py`) for paid B2B data exports. Do not mix them up.
- **Posts `record_id` nullable**: A migration made `record_id` optional because posts can be blotter-level digests.
- **Migrations are additive, not reversible**: There is no rollback script; test schema changes on a copy.
- **JSON is just TEXT**: No native JSON columns; code must call `json.loads/dumps` explicitly.
- **`page_views` exists in two places**: `init_db.py` creates it inside `blotter.db` for legacy callers, but runtime writes should route through `db.connect_page_views()`.
- **`init_database()` and `migrate()` duplicate some create calls intentionally** (e.g., `subscribers`) to remain safe if one function is called without the other.
- **Court defendant name indexes**: `defendant_last`/`defendant_first` are computed in Python during migrate to get the true last word, not a SQL substring trick.

