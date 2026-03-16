# Case Journey Homepage Spec

## Objective

Turn Montana Blotter's existing strength in source transparency into a homepage feature that no generic crime-map or blotter site offers: a public, source-linked "what happened next" journey for a single incident.

The homepage module should not be another trends card, county directory, or arrest teaser. It should prove that Montana Blotter can follow one record through the public-record lifecycle and clearly label how certain each link is.

## What The Codebase Already Supports

### Current homepage behavior

- The homepage route in `app.py` builds around `posts`, not entity-level cases.
- It already assembles multiple homepage sections: county cards, trend cards, city pages, weekly digest, weekly snapshot, then the feed with filters.
- The homepage template is already section-based, so a new module can be inserted cleanly without restructuring the whole page.

Relevant files:

- `app.py`
- `templates/index.html`
- `templates/includes/homepage_masthead.html`

### Current record behavior

- Individual records already expose:
  - `cfs_number`
  - command logs
  - source confidence
  - parse quality
  - duplicate checks
  - source PDF
  - link back to the generated daily report
- This is the right foundation for a journey product because provenance is already a first-class concept in the UI.

Relevant files:

- `app.py`
- `templates/record_detail.html`

### Current schema behavior

The current schema has strong ingest and provenance support, but no persisted case-linking layer.

Existing core tables:

- `blotters`
- `records`
- `command_logs`
- `posts`
- `source_documents`
- `ingestion_jobs`
- `pipeline_events`

Missing today:

- No `case_journeys` table
- No cross-source event table
- No public match-confidence model
- No jail booking snapshots
- No court case snapshots
- No manual review queue for cross-source links

Relevant file:

- `init_db.py`

## Live Data Constraints As Of 2026-03-14

From the current `blotter.db`:

- `records`: 3109
- `records` with `cfs_number`: 2882
- `records` with non-empty county: 3007
- `posts`: 97
- `command_logs`: 246 across 28 records
- obvious arrest-like records by text match: 42

Implication:

- You have enough dispatch-level structure to start matching.
- You do not have enough internal follow-up data to fake a full lifecycle product from current tables alone.
- Command logs are valuable on detail pages, but too sparse to be the homepage module's core data source.

## Product Definition

### Public name

Use reader-facing language on the homepage:

- Eyebrow: `What Happened Next`
- Headline: `Follow an incident from dispatch to follow-up records`

Use `Case Journey` as the internal feature name and URL slug.

### Core promise

For a small set of incidents, show a verified timeline of public-record steps such as:

1. Dispatch or blotter incident
2. Arrest or booking, if confirmed
3. Jail status or detention lookup, if confirmed
4. Court or warrant follow-up, if confirmed
5. Final visible disposition, if confirmed

Every step must show:

- source type
- event date/time
- match confidence
- link to the original Montana Blotter record or external official source

## Homepage Module Spec

### Placement

Insert the module on the homepage immediately after the hero/subscribe card and before `Explore By Record Type`.

Reason:

- That is the highest-leverage slot for communicating a unique value proposition.
- The current homepage starts with SEO hubs and directories; none of those explain why Montana Blotter is different.
- The new module should lead the page before the county/city/jail/warrant navigation blocks.

### Module name

Internal include:

- `templates/includes/homepage_case_journeys.html`

Suggested render position:

- in `templates/index.html`, after the hero section and before the `Explore By Record Type` section

### Module layout

Desktop:

- 1 explanatory intro block on the left
- 3 featured journey cards on the right or below in a 3-column grid

Mobile:

- stacked intro block
- cards in a single column

### Intro copy

- Eyebrow: `What Happened Next`
- Headline: `Public records that follow the story past the first blotter entry`
- Body: `Montana Blotter highlights incidents where later booking, jail, warrant, or court records can be connected to the original dispatch entry. Every connection is labeled by confidence and source.`
- CTA 1: `Browse all case journeys`
- CTA 2: `How journey matching works`

### Journey card fields

Each homepage card should show:

- primary incident label
- county
- first event date
- latest event date
- current journey status
- number of confirmed steps
- highest-confidence link badge
- one-sentence "latest known step"
- CTA: `Open journey`

Example card structure:

- `Gallatin County`
- `Burglary report`
- `Started Mar 12, 2026`
- `Latest: booked into Gallatin County jail`
- `3 confirmed steps`
- `Link confidence: high`

### Card badges

Keep two different confidence concepts separate:

- `Source confidence`
  - reuse the provenance logic pattern already used on record pages
- `Link confidence`
  - new
  - how certain Montana Blotter is that two records refer to the same underlying incident or person

Do not collapse these into one score.

### Card statuses

Use a public status enum:

- `open`
- `arrested`
- `in custody`
- `court follow-up`
- `resolved`
- `monitoring`

Do not reuse `posts.case_status` for this. That field is homepage-feed/editorial state, not lifecycle state.

## Journey Detail Page Spec

### Route

- `GET /case-journeys/<journey_slug>`

### Template

- `templates/case_journey_detail.html`

### Page sections

1. Hero summary
2. Timeline of journey events
3. Match-confidence explainer
4. Source evidence panel
5. Related Montana Blotter records
6. County follow-up resources

### Hero summary fields

- incident title
- county
- started date
- last updated date
- current status
- journey confidence summary
- caution/disclaimer copy

### Timeline event fields

Each event row should show:

- event type
- timestamp
- source label
- summary text
- confidence chip
- direct link
- optional note on why the step was linked

Supported event types for v1 schema:

- `dispatch_record`
- `blotter_report`
- `arrest_record`
- `jail_booking`
- `jail_release`
- `warrant_reference`
- `court_case`
- `court_hearing`
- `court_disposition`
- `editor_note`

## Data Model

Add new tables through `init_db.py` migration logic.

### `case_journeys`

Purpose:

- one public-facing journey per linked incident lifecycle

Fields:

- `id`
- `slug` unique
- `title`
- `county`
- `primary_record_id` nullable FK to `records`
- `current_status`
- `summary`
- `started_at`
- `last_event_at`
- `link_confidence_label`
- `link_confidence_score`
- `is_featured_homepage` default `0`
- `is_published` default `0`
- `created_at`
- `updated_at`

Indexes:

- `slug`
- `county`
- `is_featured_homepage`
- `is_published`
- `last_event_at`

### `case_journey_events`

Purpose:

- normalized timeline rows inside a journey

Fields:

- `id`
- `journey_id` FK
- `event_type`
- `event_at`
- `sort_order`
- `source_kind`
- `source_table`
- `source_row_id`
- `source_url`
- `title`
- `summary`
- `raw_reference`
- `match_confidence_label`
- `match_confidence_score`
- `match_reason`
- `is_public` default `1`
- `created_at`

Indexes:

- `(journey_id, sort_order)`
- `(journey_id, event_at)`
- `(source_table, source_row_id)`

### `case_journey_links`

Purpose:

- audit trail for how two records were linked into one journey

Fields:

- `id`
- `journey_id` FK
- `left_source_table`
- `left_source_row_id`
- `right_source_table`
- `right_source_row_id`
- `link_type`
- `confidence_label`
- `confidence_score`
- `reason_json`
- `review_status`
- `reviewed_by_user_id` nullable
- `reviewed_at`
- `created_at`

### `external_record_snapshots`

Purpose:

- optional generic store for jail/court/warrant snapshots before full source-specific modeling

Fields:

- `id`
- `source_type`
- `county`
- `external_id`
- `person_name`
- `event_type`
- `event_at`
- `payload_json`
- `source_url`
- `content_sha256`
- `created_at`

This table keeps v1 flexible. If a source becomes important enough, split it later into dedicated tables.

## Matching Rules

### Public confidence labels

- `exact`
- `strong`
- `probable`
- `manual`

Only `exact`, `strong`, and `probable` should appear publicly in v1.

### Exact match examples

- same county and same external incident number
- same county and same booking number
- same county and same court case number

### Strong match examples

- same county
- normalized full name
- event time within 48 hours
- charge/offense overlap

### Probable match examples

- same county
- normalized name similarity
- event window within 72 hours
- no conflicting age or location data

### Guardrails

- Never create a public journey from name-only matching with no secondary signal.
- Never show a booking, warrant, or court row as linked unless the linkage can be explained in `match_reason`.
- If confidence is below public threshold, keep it internal only.

## Homepage Ranking Logic

Show only journeys that are both `is_published = 1` and `is_featured_homepage = 1`.

Rank by:

1. journey has at least 3 public events
2. latest event within last 14 days
3. `link_confidence_score`
4. journey has at least 2 distinct source kinds
5. county diversity on the homepage

Hard requirements for homepage eligibility:

- at least 2 public events
- at least 1 Montana Blotter source event
- latest step summary exists
- no event with unresolved review status

## UX Guardrails

- Every card and detail page must say this is a public-record linkage product, not a definitive legal record.
- Show exact dates, not relative dates.
- Distinguish:
  - `record confidence`
  - `link confidence`
  - `current status`
- If a journey has no confirmed downstream event, do not include it in this module.

## Implementation Plan

### Phase 1: Ship the homepage shell and detail page with manual curation

Goal:

- prove the user-facing concept before building large source integrations

Build:

- new schema tables
- helper to load featured journeys on homepage
- detail route and template
- admin-only seed workflow via direct DB inserts or lightweight admin tooling

Do not build yet:

- automated statewide jail or court ingestion

Why:

- You can launch the differentiated concept with a handful of manually curated journeys.
- This avoids pretending the current DB can automatically generate a lifecycle product it does not yet support.

### Phase 2: Automated journey generation from existing Montana Blotter data

Goal:

- auto-create initial journeys when multiple records or command-log evidence support a valid thread

Build:

- journey candidate generator using `records`, `command_logs`, `posts`, and `source_documents`
- matching helper centered on county + `cfs_number` + time proximity
- internal review output for acceptance before publication

Expected result:

- more dispatch-centric journeys
- still limited true downstream coverage

### Phase 3: Add external follow-up sources

Priority order:

1. jail roster snapshots for counties with stable public rosters
2. warrant pages where machine-readable
3. court/case lookups only where public, stable, and legally appropriate

Build:

- source-specific fetchers into `external_record_snapshots`
- matcher that creates `case_journey_events`
- review tooling for low-confidence links

## Concrete Code Changes

### `app.py`

Add:

- `_featured_case_journeys(conn, limit=3)`
- `_case_journey_detail(conn, slug)`
- `@app.route('/case-journeys')`
- `@app.route('/case-journeys/<journey_slug>')`

Update:

- homepage route to load `featured_case_journeys`

### `init_db.py`

Add additive migrations for:

- `case_journeys`
- `case_journey_events`
- `case_journey_links`
- `external_record_snapshots`

### `templates/index.html`

Add:

- `{% include "includes/homepage_case_journeys.html" %}`

Placement:

- after the hero/subscribe block
- before `Explore By Record Type`

### New templates

- `templates/includes/homepage_case_journeys.html`
- `templates/case_journey_detail.html`
- optionally `templates/case_journey_index.html`

## Acceptance Criteria

### Homepage

- Homepage shows the module only when at least one featured journey exists.
- Module renders cleanly on mobile and desktop.
- Module does not shift or break the existing feed/sidebar layout.
- Each card links to a real journey detail page.

### Detail page

- Page shows a chronological timeline.
- Every event has a visible source label and date.
- Every linked step has a visible confidence label.
- Page includes a clear disclaimer and update timestamp.

### Data integrity

- No journey can be published without at least two public events.
- No public event may exist without either `source_url` or `source_table/source_row_id`.
- Confidence labels must be deterministic from stored scores.

## Recommendation

Build this in two tracks:

1. ship the homepage module and detail page with manually curated journeys first
2. build automated linking and external-source ingestion second

That is the pragmatic path for this codebase. The existing product already excels at source transparency. The winning move is to extend that strength into lifecycle transparency, not to add another directory or trend card.
