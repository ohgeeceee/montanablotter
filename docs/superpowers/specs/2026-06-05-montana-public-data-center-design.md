# Montana Public Data Center Design

Date: 2026-06-05  
Project: MontanaBlotter (`montanablotter.com`)  
Scope: a hybrid public data center built on top of existing Montana Blotter ingestion, records, and admin workflows.

## Goals

- Turn Montana Blotter into a broader public data center that serves three equal audiences:
  - public search and transparency
  - reporter / researcher workflow
  - internal operations and ingestion control
- Start with a focused core set of Montana datasets:
  - jail bookings
  - warrants
  - arrests
  - public meetings / agendas
  - police calls / calls-for-service
- Keep one canonical data model and one ingestion backbone so new datasets can be added without building a separate app for each source.
- Make the platform useful immediately as a public search portal, while preserving strong admin visibility into ingestion quality, source coverage, and publish status.

## Non-Goals

- Rebuilding the entire site into a brand-new stack.
- National coverage.
- Public user accounts, subscriptions, or personalization as a first release.
- Real-time streaming or websocket-based updates.
- A full open-data warehouse with arbitrary CSV export on day one.
- Replacing the existing admin and ingestion patterns that already work.

## Recommended Approach

Build a **hybrid public data center** on the existing Montana Blotter codebase.

The platform should keep one shared ingestion and normalization layer, one canonical record model, and one set of dataset definitions. The public site, researcher-oriented views, and admin ops tools should all read from the same underlying data and differ mainly in presentation and access controls.

This avoids splitting the project into disconnected products:

- the public site gets fast search and useful dataset pages
- reporters and researchers get a consistent way to browse, filter, and cross-reference records
- operators get better observability, dedupe, and publish control without maintaining a second system

## Product Shape

### Public Surface

The public-facing side should present the data center as a directory of datasets with shared navigation and consistent record detail pages.

Primary public entry points:

- a top-level data center landing page
- dataset landing pages for each supported dataset
- searchable record explorers with shared filters and dataset-specific defaults
- record detail pages that expose source links, timestamps, and methodology where available

### Researcher Surface

The researcher workflow is not a separate app. It should be a richer version of the public search experience:

- stronger filtering
- cross-dataset browsing
- stable permalinks
- better source attribution
- easy transitions from summary views into raw records

The first release should prioritize speed, clarity, and repeatability over deep analysis tooling.

### Operations Surface

Internal operators need visibility into:

- what was ingested
- what failed
- what was deduped
- what was published
- which sources are stale
- which datasets have incomplete coverage

The admin side should keep the existing ingestion controls and add data-center-specific health and coverage views rather than introducing a separate operations stack.

## Initial Dataset Scope

The first release should focus on the following Montana public records:

- jail bookings
- warrants
- arrests
- public meetings / agendas
- police calls / calls-for-service

These should be treated as the core “v1 coverage set.” Other datasets can be added later, but the architecture should assume more will come.

## Information Architecture

### Public Routes

The first public release should include:

- `GET /datacenter`
  - landing page for the Montana public data center
  - highlights the core datasets
  - shows recent updates and source coverage

- `GET /datasets`
  - directory of supported datasets
  - one card per dataset with freshness, coverage, and record counts

- `GET /datasets/<slug>`
  - dataset landing page
  - summary metrics
  - methodology
  - filters and browse entry points

- `GET /datasets/<slug>/records`
  - dataset-scoped explorer
  - reusable filters and pagination

- `GET /records/<id>` or equivalent existing detail routes
  - canonical record detail page
  - source attribution
  - timestamps and related metadata

### Internal Routes

The admin surface should expose operational views for:

- ingestion status by source
- dataset freshness
- dedupe and normalization failures
- publish queue status
- source coverage gaps

## Canonical Data Model

The data center should not invent a separate record schema for every dataset.

Instead, use a canonical model with these concepts:

- `dataset`
  - the public-facing category, such as jail bookings or warrants
- `source`
  - the upstream agency, email feed, PDF, DOCX, API, or scraper
- `record`
  - a normalized public record row
- `document`
  - raw source file or attachment when the source is file-based
- `ingestion_run`
  - metadata about when and how the source was processed
- `publish_event`
  - what was successfully exposed to the public site or downstream outputs

The implementation should keep the current record tables where practical, but the design should make the canonical concept explicit so future datasets can map into it without new one-off behavior.

## Data Flow

### Ingestion

1. A source fetcher pulls or receives upstream material.
2. The source is normalized into a dataset-specific record shape.
3. Records are deduped against prior ingests using stable source identifiers where available.
4. Valid records are written into the canonical store.
5. Any dataset-specific side effects are triggered, such as public index refresh or downstream notifications.

### Publication

1. The public site reads only from the canonical store and cached aggregates.
2. Dataset landing pages show precomputed counts, freshness, and coverage summaries.
3. Record explorers query the canonical store with dataset filters.
4. Record detail pages show the record plus source metadata and method notes.

### Operations

1. Each ingestion run emits structured logs.
2. Failures are tagged by source and dataset.
3. Admin tools show which sources are stale or broken.
4. The same ingestion output powers both public pages and operational dashboards.

## Dataset Definitions

### Jail Bookings

- source types may include emails, DOCX attachments, PDFs, or agency-specific feeds
- record detail should preserve booking time, booking county, source citation, and any charge/person fields already supported
- explore by county, date, and source agency

### Warrants

- expose warrant-specific fields where available
- make source identity and issue date obvious
- support filtering by county and current status if the source provides it

### Arrests

- arrests should be a first-class dataset, not just a derived page
- when the platform already has arrest-like source material, it should publish into the canonical arrest dataset rather than only appearing in a booking list
- support search by county, date, and source

### Public Meetings / Agendas

- preserve meeting date, agency, location, and agenda/source links
- allow browsing by city, county, and meeting type where known

### Police Calls / Calls-for-Service

- preserve incident time, location, and incident category when present
- support date-range browsing and source-based filtering

## Aggregates And Indexes

Dataset landing pages should not compute everything on the fly.

Add a lightweight aggregation layer for:

- last updated timestamps
- 1-day / 7-day / 30-day counts
- coverage summaries by county, city, or agency
- trend snippets for simple charts
- top categories when a source has stable categorization

The explorer pages should still query the canonical records directly so the aggregate cache does not become a second source of truth.

## Search And Research UX

The public search experience should emphasize:

- fast keyword search
- stable filters
- dataset scoping
- source links
- exact timestamps and locations where available

For researcher usefulness, the system should also support:

- consistent field names across datasets
- query URLs that can be shared
- a visible note explaining whether a result is raw source text, normalized text, or inferred metadata

## Operations And Governance

The data center should keep the existing admin controls, but add explicit operations around:

- source freshness
- dataset completeness
- dedupe failures
- publish failures
- manual reingest actions

Governance requirements:

- public records should remain factual and source-grounded
- no implication of guilt from arrests or bookings
- methodology must be visible on dataset pages
- records with partial extraction should show that status rather than pretending to be complete

## Rollout Plan

### Phase 1: Shared Core

- define the dataset registry for the five core datasets
- add dataset landing pages and shared explorers
- expose the canonical public landing page
- add aggregate metrics for freshness and counts

### Phase 2: Ingestion Unification

- map existing ingestors into the dataset registry
- standardize source identifiers and dedupe keys
- surface ingestion health in admin views

### Phase 3: Researcher Utility

- improve cross-dataset filters
- add stable permalinks and richer source attribution
- refine summary metrics and coverage displays

### Phase 4: Expansion

- add more Montana datasets without changing the core architecture
- extend dashboards and operational monitoring as new source types arrive

## Testing Strategy

Add tests that prove:

- each core dataset resolves to the correct dataset slug and browse view
- dataset pages only include records that belong to that dataset
- aggregate counts are stable and reflect the canonical store
- record detail pages show source attribution and methodology notes
- ingestion failures do not break public pages
- admin freshness views report stale versus current sources correctly

Prefer tests that operate through the existing Flask routes and ingestion helpers rather than only isolated unit tests, so the data center behavior is verified end to end.

## Acceptance Criteria

- A public landing page exists for the Montana public data center.
- The five core datasets are first-class and independently browsable.
- Public pages, researcher views, and admin ops all read from the same canonical data foundation.
- New source types can be added without building a separate data system.
- The platform still fits the existing Montana Blotter codebase and deployment model.

