# Civil Filings And Code Violations Design

## Goal

Add a landlord- and property-intelligence data layer to Montana Blotter by:

- introducing statewide civil filing ingestion focused on evictions, restraining orders, liens, and civil judgments
- hardening the existing code-violations ingestion path into a source-driven pipeline
- linking both datasets to the shared `property_addresses` table so address-level property pages can aggregate risk signals

This work should extend the existing Montana Blotter ingestion, court, and property surfaces rather than create a parallel subsystem.

## Scope

This design covers:

- new civil filing schema and ingestion services
- a phased ingestion strategy that supports file imports first and live `iCourtCase.mt.gov` harvesting second
- shared address normalization and linking into `property_addresses`
- a new public `/eviction-records` page
- extensions to `/property/<address-slug>` for civil filing and code-violation aggregation
- operational tracking and tests

This design does not cover:

- billing, subscriptions, or paywall logic
- a tenant-facing screening workflow
- a real estate embed widget
- cached scoring models or durable “risk score” tables

## Current Project Context

The current codebase already contains the critical foundations:

- `services/ingestion/code_violations.py` provides an ingestion pattern for source registration, idempotent upsert behavior, and linking into `property_addresses`
- `/code-violations` and `/property/<address-slug>` already exist as public surfaces
- the court tracker already models source metadata, refresh workflows, and admin visibility for public court data

Because those patterns already exist, the recommended architecture is to add a civil-filings domain that reuses the same ingestion and property-linking model.

## Recommended Approach

Use a phased ingestion architecture.

Phase 1 introduces a generic civil-filings schema and importer that accepts normalized JSON or CSV records. This allows product and data-model work to ship without blocking on the riskiest dependency.

Phase 2 adds an `iCourtCase.mt.gov` adapter behind the same ingestion interface. The adapter becomes only one producer of normalized filing records, not the center of the feature.

This approach is preferred over a scraper-first build because:

- the scraper is the highest-risk part of the project due to session handling, pagination, and throttling
- the product value comes from the stored, searchable filing dataset and address links, not from the transport itself
- the same normalized schema can later accept city exports, FOIA batches, or manual curation without downstream rewrites

## Architecture

The work is split into three bounded modules:

1. Civil filings domain
2. Source adapters and ingestion flow
3. Property-facing public surfaces

### 1. Civil Filings Domain

Add a new service area for civil filings with schema and ingestion helpers.

Primary tables:

- `civil_filing_sources`
- `civil_filings`
- `civil_filing_parties` if party rows need to be stored separately

`civil_filing_sources` stores source identity and health metadata:

- `id`
- `source_key` unique
- `display_name`
- `adapter_type` such as `import_json`, `import_csv`, `icourtcase`
- `jurisdiction` and optional `county`
- `source_url`
- `last_success_at`
- `last_error`
- `last_run_count`
- `created_at`
- `updated_at`

`civil_filings` stores the normalized filing record:

- `id`
- `source_id`
- `property_address_id` nullable foreign key to `property_addresses`
- `county`
- `city` nullable
- `case_number`
- `case_type_code` such as `UD`, `DV`, `CC`
- `case_type_label` if present from source
- `filing_class` constrained to `eviction`, `lien`, `restraining_order`, `civil_judgment`, `other`
- `caption`
- `plaintiff_name`
- `defendant_name`
- `raw_address`
- `filing_date`
- `case_status`
- `source_record_id` nullable
- `source_url`
- `raw_json`
- `hash_id` unique for idempotency
- `first_seen_at`
- `last_seen_at`
- `created_at`
- `updated_at`

Optional `civil_filing_parties` is only needed if one filing regularly contains multiple plaintiffs or defendants and the UI needs separate searchable party rows. If the source usually yields one plaintiff and one defendant, start with the inline text columns on `civil_filings` and defer party normalization.

### Classification Strategy

Classification must be stored as data, not inferred only at query time.

Initial classification should use deterministic rules:

- `UD` -> `eviction`
- `DV` -> `restraining_order`
- `CC` -> `civil_judgment` by default unless stronger cues map it elsewhere
- filing titles or captions containing lien-related language -> `lien`
- everything else -> `other`

Hermes-based classification can be added later as an enrichment pass that rewrites or confirms `filing_class` and extracts plaintiff, defendant, address, and status. The schema should not depend on Hermes being available during the first release.

## Ingestion Design

### Common Pipeline

Create a new ingestion module modeled after `services/ingestion/code_violations.py`.

Responsibilities:

- ensure or create the source row
- normalize dates and strings
- derive deterministic classification from case code and text
- normalize and link any address into `property_addresses`
- generate a stable `hash_id` for idempotent upsert behavior
- update `last_seen_at` on repeated records
- record source success or failure metadata

The civil filing ingester accepts a list of normalized records shaped like:

```json
{
  "county": "Yellowstone",
  "city": "Billings",
  "case_number": "UD-26-1234",
  "case_type_code": "UD",
  "case_type_label": "Unlawful Detainer",
  "caption": "ABC Properties LLC v. Jane Doe",
  "plaintiff_name": "ABC Properties LLC",
  "defendant_name": "Jane Doe",
  "address": "123 Main St, Billings, MT 59101",
  "filing_date": "2026-05-11",
  "case_status": "Open",
  "source_record_id": "UD-26-1234",
  "source_url": "https://example.com/case/UD-26-1234"
}
```

### Phase 1 Adapter: Import Files

The first adapter should support JSON and CSV imports.

Reasons:

- low implementation risk
- allows seeding and validation before the live scraper exists
- enables intake from manual exports or future public-record batches

This adapter should be usable from a CLI entry point similar to existing ingestion scripts.

### Phase 2 Adapter: `iCourtCase.mt.gov`

Add a dedicated adapter that harvests public civil cases from `iCourtCase.mt.gov` and emits normalized records into the same ingestion service.

The adapter should:

- run county-by-county instead of statewide in one undifferentiated session
- filter for target case codes such as `UD`, `DV`, and selected `CC` categories
- enforce rate limiting between requests and pages
- reuse session state within a county run
- checkpoint progress by county and time window
- resume from partial progress after a failure
- persist source-level error details without rolling back previously ingested counties

The adapter should not write directly to presentation tables. It should only produce normalized filing records and call the shared civil-filings ingester.

### Code Violations Hardening

Do not redesign code violations as a separate feature. Extend the existing implementation to match the new source-driven pattern.

Required improvements:

- keep using `code_violation_sources` as the source registry
- formalize parser boundaries for JSON, CSV, Excel, and PDF
- normalize addresses before insert so property linking is more reliable
- record source refresh metadata consistently with civil filing sources

This keeps both datasets converging on `property_addresses`.

## Address Linking

`property_addresses` remains the central address identity table.

Both civil filings and code violations should normalize into that table. The normalization path should:

- derive a stable `address_slug`
- preserve the original `raw_address`
- store parsed address parts when possible
- tolerate partial addresses by leaving `property_address_id` null if the source cannot support a confident match

The first release should use pragmatic, deterministic normalization rather than an LLM dependency. If a stronger normalizer is later added through Hermes, it should enrich or repair address matches, not block ingestion.

## Public Product Surfaces

### `/eviction-records`

Add a new public route and template for civil filings relevant to landlords and property managers.

Initial filters:

- query text
- county
- city
- filing class
- filing date range or recent-period preset

Initial result columns:

- filing date
- county
- city
- filing class
- case number
- plaintiff
- defendant
- address if present
- case status

The page should be backed by `civil_filings` and start focused on the commercially valuable classes:

- `eviction`
- `restraining_order`

If desired, the route can later expand into a broader civil-filings index without changing the underlying tables.

### `/property/<address-slug>`

Extend the property detail page so it aggregates:

- linked code violations
- linked civil filings
- existing arrest-related or blotter-linked signals already keyed by address, where available

Add a compact server-side summary block such as:

- `3 civil filings`
- `2 code violations`
- `2 arrest-linked records`

This should be computed at request time from linked tables in the first release. Do not introduce a separate denormalized score table yet. The model is still changing, and premature caching would add invalidation work without enough benefit.

## Operations And Safety

Each source should expose enough metadata for admin and watchdog use:

- last successful run time
- last error
- records seen in the last run
- adapter type
- source URL or import identity

The civil filing source registry should integrate with the existing operations mindset used in the court tracker so stale or failing feeds can be surfaced without inventing a second admin philosophy.

Public presentation must use neutral language, especially for restraining-order data:

- identify items as public civil filings
- avoid implying guilt, validity, or outcome beyond the visible source status
- preserve source attribution where available
- avoid editorial summaries that make legal claims not present in the record

## Testing Strategy

Add tests in the same style as existing ingestion and route coverage.

Required coverage:

- schema creation tests for civil filing tables
- deterministic classification tests for `UD`, `DV`, `CC`, and text-driven lien detection
- ingestion tests for insert and idempotent reingest
- address-linking tests against `property_addresses`
- route tests for `/eviction-records`
- property detail tests that verify linked civil filings and summary rollups render
- parser and state-machine tests for the `iCourtCase` adapter using captured fixtures

The live scraper should not rely on network tests in CI. Test parser behavior and checkpoint logic with fixtures.

## Implementation Order

1. Add civil filing schema and schema tests
2. Add common civil filing ingestion service and unit tests
3. Add import-file adapter for JSON and CSV
4. Add `/eviction-records` route and template tests
5. Extend `/property/<address-slug>` to show linked civil filings and summary rollups
6. Harden code-violations ingestion around parser boundaries and source metadata consistency
7. Add `iCourtCase` adapter with county-scoped checkpoints and fixture-based tests

## Success Criteria

The first release is successful when:

- civil filings can be imported into a normalized table with idempotent updates
- eviction and restraining-order records are searchable on `/eviction-records`
- addresses from civil filings and code violations link into `property_addresses`
- property detail pages show linked filing and violation history
- the source model captures operational health data for future automation

The second release is successful when:

- `iCourtCase.mt.gov` harvesting can run per county with rate limiting and resumable checkpoints
- statewide daily refreshes can complete incrementally without full-run restarts
- scraper failures surface operationally without corrupting existing filing data
