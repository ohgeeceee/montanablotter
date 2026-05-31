# Datasets Pages + Daily Cron (Design)

Date: 2026-05-19  
Project: MontanaBlotter (`montanablotter.com`)  
Scope: Montana-only datasets directory + per-dataset pages + record explorer links + daily system cron refresh.

## Goals

- Add a top-level datasets directory page: `/datasets`.
- Add per-dataset pages: `/datasets/<slug>`, starting with:
  - Jail Bookings
  - Public Meetings / Agendas
  - Police Calls / Calls-for-Service
- Each dataset page provides:
  - “At a glance” summary (counts + simple trends)
  - A “Browse records” explorer (filterable list + existing record detail pages)
  - Sources + methodology + disclaimers
- Add a daily **system cron** job to update ingestion + refresh dataset aggregates + “last updated” timestamps.
- Keep v1 **view-only** (no public CSV/JSON downloads yet).

## Non-Goals (v1)

- National datasets (Montana-only for now).
- Public bulk downloads / API endpoints.
- A brand-new record schema per dataset (v1 favors curated views on existing records).
- Auto-posting daily roundups to social/email (leave hooks, but not required for v1).

## Guiding Approach

Prefer **curated dataset views on existing `records` + existing ingestion outputs**, plus a small aggregate cache for fast dataset landing pages.

Rationale:
- Fastest to ship with minimal schema risk.
- Keeps the “explorer” consistent with existing record UX.
- Allows future migration to dedicated dataset tables if needed without breaking URLs.

## Information Architecture

### New Routes

- `GET /datasets`
  - Directory: tiles for each dataset, “Last updated”, and city/county coverage hints (Great Falls, Missoula, Billings as prominent chips).
- `GET /datasets/<slug>`
  - Landing page: dataset summary + trends + “Browse records” CTA + methodology.
- `GET /datasets/<slug>/records`
  - Explorer: dataset-scoped view backed by existing records list/search, with dataset-specific filters applied by default.

### Dataset Slugs (v1)

- `jail-bookings`
- `public-meetings`
- `police-calls`

(Future slugs can be added without changing the structure.)

## Dataset Definitions (v1)

### Jail Bookings

**Definition:** Jail booking records from supported MT agencies/counties.  
**Explorer behavior:** show only records tagged/typed as jail bookings; default sort newest first; filter by county/city when available.  
**Landing summary:** last 24h count, last 7d count, last 30d count; a 30-day daily bar trend; top charge categories if present; “unknown charge” bucket if not.

### Public Meetings / Agendas

**Definition:** Public meeting events + agendas scraped/ingested for MT entities, prioritized:
- Great Falls
- Missoula
- Billings

**Explorer behavior:** show meeting records/events (agenda items optional) and allow filtering by city/agency.  
**Landing summary:** upcoming 14 days count, last 30 days count; “next meeting” callout per priority city if available; source coverage list.

### Police Calls / Calls-for-Service

**Definition:** Official calls-for-service / call logs where available.  
**Explorer behavior:** show CFS/call log records; filters for city, incident type/category, and time window.  
**Landing summary:** last 24h / 7d / 30d counts; top incident categories (if present); 30-day time series.

## Page Content Requirements

### Shared components (directory + dataset pages)

- Title + short description
- Coverage chips (Great Falls / Missoula / Billings)
- “Last updated” timestamp
- “Sources” list (official URLs where possible)
- Disclaimers:
  - Public records: factual, no implication of guilt
  - Data completeness/coverage varies by agency and date
  - Times/dates in UTC vs local must be labeled consistently

### Dataset landing page blocks (recommended order)

1. Header: dataset name + one-line “what it is”
2. “At a glance” stats
3. Trend chart (30 days)
4. Explorer CTA: “Browse records”
5. Coverage + sources
6. Methodology + known limitations

### Explorer page

- Reuse existing list/detail UX patterns.
- Apply dataset filter by default, but allow user to adjust additional filters.
- Keep a prominent “back to dataset” link.

## Data & Aggregates

### Aggregate cache (recommended)

Add a small table to store computed aggregates for each dataset page:

- `dataset_metrics`
  - `dataset_slug` (PK)
  - `updated_at` (ISO timestamp)
  - `window_1d_count`
  - `window_7d_count`
  - `window_30d_count`
  - `trend_30d_json` (JSON array of `{date, count}`)
  - `top_categories_json` (JSON array of `{label, count}`) (optional per dataset)
  - `coverage_json` (JSON list of cities/counties covered)

Notes:
- This is an optimization layer. The explorer should still query source-of-truth records/events tables.
- If metrics are missing (first run), pages should render with graceful “metrics pending” states.

### Record tagging / dataset membership

Each dataset page needs a deterministic “membership rule” for which rows appear in the explorer.

Implementation can be one of:
- A stable `records.dataset` / `records.record_type` value
- A stable `records.source` + `incident_type` mapping
- A materialized view of record IDs per dataset

Design requirement: choose one rule per dataset that is:
- explainable to users (methodology section)
- stable over time
- testable (unit tests for inclusion/exclusion)

## Daily System Cron (“Automizer”)

### Cron behavior

Run once per day (time chosen for lowest load; e.g. early morning local).

Steps:
1. Run ingestion updates for the supported datasets/sources.
2. Recompute `dataset_metrics` for v1 slugs.
3. Update per-dataset “last updated” timestamps used by `/datasets` tiles.
4. Write logs and alert on failures (non-zero exit).

### Operational requirements

- Single entrypoint script (idempotent) that:
  - logs start/end
  - records per-step timing
  - exits non-zero on failure
- Log files written under an existing logs location/pattern.
- Safe concurrency: if a run is still active, next run should not overlap (lock file).

### Failure modes / UX

- If ingestion fails: keep old metrics, show “Last updated” remains the previous successful timestamp.
- If metrics refresh fails but ingestion succeeded: keep old metrics, but record a metrics-refresh failure in logs.

## Rollout Plan (Design-level)

1. Add routes/templates for `/datasets`, `/datasets/<slug>`, `/datasets/<slug>/records`.
2. Implement dataset membership rules for the 3 v1 datasets.
3. Add `dataset_metrics` cache + refresh job.
4. Add cron wiring + lock + logging.
5. Soft launch: link “Datasets” in main nav/footer; monitor logs for a week.

## Future Enhancements (post-v1)

- Add view-only pages for additional datasets:
  - Court filings / dockets
  - Code violations
  - License sanctions
  - Sex offender registry
  - Missing persons
- Add “Download snapshot” links (CSV/JSON) with rate limiting.
- Add a public JSON API with strict pagination and caching.
- Add daily “What changed” posts per city/county (optional publishing pipeline).
