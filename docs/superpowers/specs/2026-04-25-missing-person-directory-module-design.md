# Missing Persons Directory Module Design

Date: 2026-04-25
Status: Draft for review

## Goal

Extend the existing MontanaBlotter missing-person system so it can:

- sync the Montana DOJ Missing Persons public portal through the current Python ingestion path,
- capture and normalize the core public directory fields,
- deduplicate records by case number during sync,
- expose a tighter directory-oriented public presentation,
- run as a cron-safe refresh service on the existing Linux VPS.

## Non-Goals

- Replacing the current MontanaBlotter missing-person subsystem with a standalone service.
- Moving persistence from the current SQLite-backed application flow to PostgreSQL as part of this phase.
- Rebuilding the site around Rust or Diesel.
- Adding anonymous scraping infrastructure, queues, or distributed workers.

## Existing System

The repository already contains:

- official Montana DOJ sync logic in [missing_persons.py](/root/montanablotter/missing_persons.py:1),
- public missing-person routes in [app.py](/root/montanablotter/app.py:7876),
- public templates in [missing_persons.html](/root/montanablotter/templates/missing_persons.html:1) and [missing_person_detail.html](/root/montanablotter/templates/missing_person_detail.html:1),
- schema evolution helpers for `missing_persons`,
- status and notification lifecycle handling for imported cases.

This work should extend those paths, not create a second source of truth.

## Recommended Approach

Use direct HTTP fetching as the primary ingestion method and keep browser automation as a future fallback only if the DOJ portal stops serving parseable HTML.

Reasoning:

- the current system already uses lightweight HTML fetch-and-parse patterns,
- cron execution on the VPS benefits from low memory and startup cost,
- adding Playwright immediately would increase complexity without clear need,
- a fetch abstraction can leave room for a browser fallback later.

## Scraping Design

### Fetch Strategy

Add a focused fetch layer inside the existing missing-person sync module:

- `fetch_official_missing_person_rows(...)`
- `parse_missing_person_result_row(...)`
- `fetch_missing_person_detail(...)` if detail-page enrichment is required

The fetcher should:

- use `httpx` or the project’s existing HTTP style for direct requests,
- reuse one client/session per refresh run,
- request the DOJ list view first and only pull detail pages when list data is incomplete,
- tolerate missing images, empty text nodes, and partial records.

### Captured Fields

Each normalized record should include:

- `full_name`
- `age`
- `missing_from`
- `date_last_seen`
- `height_weight`
- `case_number`

Where available from the current portal structure, preserve existing fields already supported by the app, including status, photo URLs, agency data, and source timestamps.

### Normalization Rules

- `case_number` is required for a durable import; rows without one are skipped and counted as parse failures.
- `full_name`, `missing_from`, and `height_weight` are trimmed to single-line display values.
- `age` is parsed as an optional integer.
- `date_last_seen` is parsed through a small accepted-format chain and stored empty when malformed rather than raising.
- missing images resolve to an empty string or placeholder-safe value, never a hard failure.

## Persistence Design

### Source of Truth

Keep the existing `missing_persons` table as the canonical store for directory rendering and alert lifecycle logic.

### Table Extensions

Extend the existing schema with directory-specific columns where they do not already exist:

- `case_number TEXT`
- `missing_from TEXT`
- `height_weight TEXT`
- `is_active INTEGER NOT NULL DEFAULT 1`
- `last_synced TEXT DEFAULT ''`

If the current table already stores equivalent structured fields such as city, county, height, or weight separately, the sync should populate both the structured values and the new compact directory display fields only when that improves route/template simplicity.

### Deduplication and Upsert Rules

During refresh:

- first look up existing records by `case_number`,
- secondarily use existing source identity fields if needed for legacy continuity,
- insert only when no matching record exists,
- otherwise update the existing row in place,
- change `is_active` based on the imported status,
- update `last_synced` for every touched row.

Routine metadata changes should not create duplicate rows.

## Service Architecture

### Async Refresh Entry Point

Expose an async service boundary such as:

```python
async def refresh_missing_persons_directory(...) -> dict[str, int]:
    ...
```

Responsibilities:

- fetch official rows,
- normalize the payload,
- open a single database transaction,
- upsert records with case-number dedupe,
- return counts for fetched, inserted, updated, skipped, and failed rows.

Internally, DB writes can remain serialized and synchronous if that best matches the current app architecture. The async boundary exists so cron wrappers and future orchestration can call one clear service entry point.

### Cron Integration

The VPS cron job should trigger a small wrapper that:

- opens the app database connection,
- runs the refresh service,
- logs counts and parse failures,
- exits non-zero only on refresh-level failures, not on a single malformed row.

This keeps the refresh reliable and observable without introducing a queue system.

## Frontend Design

### Visual Direction

The directory should follow a zero-bloat technical-authority presentation:

- monochrome or near-monochrome palette,
- compact grid with strong dividers,
- mono-first font stack such as `"Roboto Mono", "SFMono-Regular", "SF Mono", monospace`,
- dense metadata blocks with uppercase labels,
- minimal ornament and no soft-card marketing aesthetic.

### Directory Index

Extend [missing_persons.html](/root/montanablotter/templates/missing_persons.html:1) to render each row or card with:

- full name as the primary identifier,
- age,
- missing from,
- date last seen,
- height / weight,
- case number,
- status emphasis for active versus located cases.

Active cases should be visually dominant. Located or resolved cases should remain visible but quieter.

### Detail Page

Extend [missing_person_detail.html](/root/montanablotter/templates/missing_person_detail.html:1) to preserve the same forensic layout, promote case identity and official-source context, and degrade gracefully when a photo or a field is missing.

## Error Handling

- malformed dates are recorded as empty or raw-safe values and included in failure metrics,
- missing required identifiers such as `case_number` cause the row to be skipped,
- missing images do not break rendering or sync,
- request failures should surface enough context for logs while allowing safe retry on the next cron run.

## Testing

Add targeted coverage for:

- HTML parsing of representative DOJ result rows,
- malformed-date normalization,
- dedupe-by-case-number upserts,
- inactive versus active status mapping,
- directory template rendering with missing image and missing field cases.

## Open Compatibility Note

The original prompt mentioned Rust/Diesel or PostgreSQL models. This repository is currently centered on Python, Flask, Jinja, and SQLite-backed persistence for missing persons. The implementation should therefore treat the Python schema and service path as authoritative. If a Rust or PostgreSQL integration becomes necessary later, it should be generated from the stabilized Python-side field contract rather than introduced prematurely here.
