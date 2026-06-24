# Persons Tracking codemap

Tracks three overlapping person-data streams: **missing-person alerts**, **Montana sex-offender registry sync**, and general person/profile helpers used by the rest of the site.

---

## Module map

| Path | Purpose | Key entry points |
|------|---------|-----------------|
| `services/persons/missing.py` | Missing-person alert platform: schema, ingest, CRUD, dispatch, public context, Facebook-poster support. | `ensure_missing_person_schema()` (~L324), `sync_official_missing_persons()`, `create_missing_person()`, `update_missing_person()`, `update_missing_person_status()`, `dispatch_missing_person_alerts()`, `missing_person_public_context()`, `missing_person_detail_context()` |
| `facebook_missing_poster.py` | Generates the Facebook "missing person" shareable poster image. | `build_missing_person_poster_image()` and helpers |
| `services/persons/sex_offender_scraper.py` | Fetches the Montana DOJ SVOR public site (search, results, and detail pages) and parses raw offender/address HTML. | CLI `main()`, ingest helpers |
| `services/persons/sex_offender_import.py` | Normalizes scraped data and upserts it into the registry tables. | `import_offender_records()`, schema-ensure helpers |
| `services/persons/sex_offender_delta.py` | Compares newly scraped records against the existing DB using a canonical signature hash and emits add/update/delete changes. | `compute_delta()`, `apply_delta()` |
| `services/persons/sex_offender_arcgis_sync.py` | Pushes address/geometric changes to the ArcGIS feature layer used by the public map. | `sync_arcgis_features()` |
| `services/persons/profiles.py` | Shared person-profile normalization/search helpers. | profile builders |
| `services/persons/watch.py` | Lightweight monitoring/watch helpers used by the persons subsystem. | watch utilities |
| `blueprints/sex_offender.py` | Public sex-offender directory/detail pages. | county listing, offender detail, map pages |
| `blueprints/admin/sex_offender.py` | Admin review queue for registry updates. | `/admin/sex-offenders/*` |
| `blueprints/admin/operations.py` | Admin CRUD, sync, and dispatch for missing-person alerts. | `/admin/operations/missing-persons/*` (~L507+) |
| `blueprints/public.py` | Public blueprint root; anchors entry-point routes. | various landing routes |

---

## Data sources

| Domain | Source | Notes |
|--------|--------|-------|
| Missing persons | Montana DOJ/Missing Persons pages, MMIP dashboards, official police feeds where available | Fetched as HTML/JSON, normalized into `missing_persons`. Source attribution stored in `source_name`/`source_url`. |
| Sex offender registry | Montana DOJ Sexual/Violent Offender Registry (public search/detail pages) | Scraped, canonicalized, and compared against local records. |
| Generics/person profiles | Internal `jail_bookings`, court records, and subscriber data | Reused by watch/profile helpers. |

---

## Data model

### Missing persons
Tables are created by `services/persons/missing.py` (`ensure_missing_person_schema()`), not in `init_db.py`.

- `missing_persons` — one row per missing/located person.
  - Key columns: `id`, `slug` (unique), `full_name`, `age`, `city`, `county`, `last_seen_at`, `last_seen_location`, `summary`, `physical_description`, `contact_info`, `source_name`, `source_url`, `photo_url`, `status` (`missing` or `resolved`), `resolution_summary`, `resolved_at`, `last_alerted_at`, `alert_delivery_count`, `notification_version`, audit columns.
  - Unique partial index on `source_person_id` for official-source records.
- `missing_person_alert_deliveries` — one row per subscriber/channel delivery attempt.
  - Key columns: `missing_person_id`, `notification_version`, `recipient_email`, `subscriber_id`, `channel`, `delivery_status`, `provider_message_id`, `error_message`.
- `missing_person_push_subscriptions` — WebPush subscription rows tied to `subscriber_id`.
  - `endpoint`, `p256dh_key`, `auth_key`, `last_seen_county`, `last_seen_city`, `active`.
- `missing_person_source_stats` — singleton stats table (`id` primary key `CHECK id = 1`) tracking last official sync state.
- `public_users` / `subscribers` opt-in flags (defined in `init_db.py` migrations): `missing_person_email_opt_in`, `missing_person_sms_opt_in`, `missing_person_push_opt_in`.

### Sex offender registry
Tables are managed by the scraper/import modules (e.g., `sex_offender_import.py`). Common names seen in the code:

- `sex_offender_records` — normalized offender identity and DOJ metadata.
- `sex_offender_addresses` — known addresses for each offender.
- `sex_offender_geometry` / ArcGIS feature data — geometric point data synced by `sex_offender_arcgis_sync.py`.
- Sync log tables used for audit/delta bookkeeping.

---

## Data flow

### Missing-person alerts

1. **Ingest** — an admin clicks **Sync** on `/admin/operations/missing-persons` (POST `/operations/missing-persons/sync`) or a source fetcher runs.
2. `services/persons/missing.py::sync_official_missing_persons()` fetches the configured official sources, parses each record, and upserts into `missing_persons`.
3. **State change detection** — `create_missing_person()` / `update_missing_person()` / `update_missing_person_status()` compare old vs. new values and raise a `should_notify` flag when the case is new or transitions in a way that warrants an alert.
4. **Dispatch** — `dispatch_missing_person_alerts()` loads the alert, builds HTML content, generates the Facebook poster (`facebook_missing_poster.py` if enabled), and sends to opted-in subscribers (email today; SMS/Push supported in the schema).
5. **Delivery log** — each attempt inserts into `missing_person_alert_deliveries`; on success, `missing_persons.last_alerted_at`, `alert_delivery_count`, and `notification_version` are updated.
6. **Audit** — admin actions are logged with `target_type='missing_person'`.

### Sex-offender registry sync

1. **Scrape** — `sex_offender_scraper.py` fetches the DOJ SVOR search forms and result/detail pages.
2. **Normalize** — `sex_offender_import.py` parses names, addresses, tiers, DOB, and images into `sex_offender_records` + `sex_offender_addresses`.
3. **Delta** — `sex_offender_delta.py` hashes a canonical set of fields per offender and per address, comparing against the DB to identify additions, updates (including moves), and removals.
4. **Geometry sync** — `sex_offender_arcgis_sync.py` reconciles changed addresses with the ArcGIS feature layer geocoder, updating point geometry.
5. **Approval/Publication** — changed records are surfaced in `/admin/sex-offenders` for review; approved records appear on the public directory pages.

---

## Cron schedule

The manual wrapper `scripts/ops/sex_offender_daily.sh` exists, but `crontab.txt` does **not** currently include a dedicated cron entry for missing-persons or sex-offender sync. Both pipelines are primarily triggered from the admin UI. If automation is desired, add scheduled invocations of:

- `sync_official_missing_persons(conn)` (missing)
- `scripts/ops/sex_offender_daily.sh` (registry → import → delta → ArcGIS)

Watch `crontab.txt` for the quarterly ad-health check, which is unrelated to persons data.

---

## Public pages

- Missing-person directory and detail (routes/templates managed in `app.py` and the missing templates).
- Sex-offender directory (`blueprints/sex_offender.py`):
  - county-level listings
  - offender detail pages
  - map view backed by the ArcGIS feature layer

All public pages enforce the usual content policies and include source attribution where applicable.

---

## Admin UIs

| URL base | File | What it does |
|----------|------|--------------|
| `/admin/operations/missing-persons` | `blueprints/admin/operations.py` | List, add/edit, sync, change status, and manually dispatch missing-person alerts. |
| `/admin/sex-offenders` | `blueprints/admin/sex_offender.py` | Review queue for registry updates and geometry sync status. |

---

## Gotchas / important notes

- **Schema lives in the service layer**: `missing_persons` tables are created by `services/persons/missing.py`, not inside `init_db.py`. Upgrades need to propagate through `ensure_missing_person_schema()`.
- **No automated schedule**: cron does not run these syncs; they rely on an admin or future scheduled job unless `scripts/ops/sex_offender_daily.sh` is added to `crontab.txt`.
- **Photo handling**: `photo_url` may be an external URL. The Facebook poster generator downloads/renders it locally; failures return a fallback layout rather than crashing dispatch.
- **Notification version**: Alerts are idempotent by `(missing_person_id, notification_version, recipient_email)`, enforced by `idx_missing_person_alert_delivery_unique`.
- **Sex-offender delta hashing**: Changes are detected by canonical signatures, not raw DB diffs. Updating the hash logic will cause a one-time full rewrite.
- **ArcGIS is optional**: `sex_offender_arcgis_sync.py` only runs when credentials are configured; map pages degrade gracefully.
- **Sensitive data**: Offender detail pages should not expose raw address geometry beyond the public-facing map point; verify template output before shipping map changes.
- **Supporter opt-ins**: Missing-person alert subscriptions are gated by the three opt-in columns on subscribers; only email dispatch is known to be wired end-to-end at this time.

---

## Related docs

- `docs/superpowers/plans/2026-04-25-missing-person-alert-platform.md` — product/architecture plan for the missing-person platform.
