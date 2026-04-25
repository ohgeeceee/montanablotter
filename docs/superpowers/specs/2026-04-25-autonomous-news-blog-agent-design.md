# Autonomous News Blog Agent Design

Date: 2026-04-25

## Goal

Add an autonomous newsroom pipeline that publishes 2-5 strictly factual Montana crime and local-news blog posts per day to the existing `/blog/` section.

The system must:

- use MontanaBlotter records and approved external Montana public/news sources
- write drafts first
- require a second autonomous manager agent to review and approve before publishing
- auto-publish without human clicks when a draft passes policy and quality checks
- reject uncertain or risky content rather than trying to publish through ambiguity

The system must not:

- publish opinion, commentary, or speculative trend pieces
- publish controversy bait or sensational framing
- create a separate CMS or standalone service for blog publishing

## Recommended Architecture

Implement a two-agent pipeline inside the existing MontanaBlotter stack:

1. `news_planner` gathers and normalizes candidate stories.
2. `news_writer` creates draft blog posts from normalized source packets only.
3. `news_editor` reviews drafts against hard editorial rules and auto-publishes only when all checks pass.

This is preferred over a single all-in-one agent because autonomous publishing needs separation between generation and approval. It is preferred over human review because the stated requirement is autonomous publishing.

## Existing-System Integration

The design reuses the current application structure:

- existing `blog_posts` table and `/blog/` public routes
- existing cron and RQ queue model
- existing agent-events and mission-control infrastructure
- existing admin AI blog draft patterns where useful

No new standalone frontend, CMS, or public publishing surface is introduced.

## Source Inputs

The pipeline may use:

- MontanaBlotter database records and derived internal data
- approved external Montana public records sources
- approved external Montana news/public-source inputs

All external inputs must be explicitly allow-listed in configuration. The planner must not freely browse arbitrary sites. If a source is not allow-listed, it is ignored.

## Pipeline Components

### 1. News Planner

Responsibilities:

- run multiple times per day
- pull candidate story leads from internal records and approved external sources
- normalize source material into structured story packets
- dedupe repeated leads
- score candidates for Montana relevance, recency, and factual completeness

Output:

- persisted candidate rows ready for drafting
- source packet data containing:
  - source URLs
  - source type
  - key facts
  - relevant location and time
  - agencies involved
  - related MontanaBlotter record IDs when available

### 2. News Writer Agent

Responsibilities:

- consume approved story candidates
- create blog drafts only, never publish directly
- write in factual, local-news style with no opinion
- stay grounded to the planner packet; no unsupported claims

Output:

- unpublished rows in `blog_posts`
- attached traceability metadata linking the draft to its source packet

### 3. News Editor Agent

Responsibilities:

- review every draft independently from the writer pass
- check factual grounding, duplication, neutrality, sensitivity, and policy compliance
- publish automatically if the draft passes all checks
- reject or hold drafts that fail any rule, with explicit reasons

Output:

- published `blog_posts` rows when approved
- rejection records when blocked

## Editorial Policy

Every published post must be:

- strictly factual
- local-news style
- neutral in tone
- grounded in cited source material
- time-bounded and materially relevant to Montana

Every published post must avoid:

- opinion
- speculation
- implied guilt beyond the source record
- sensational framing
- controversy-oriented wording
- unsupported causal or trend claims

The manager agent is required to reject uncertain content. The system is allowed to publish fewer than the target volume if safe candidates are not available.

## Safety Gates

The editor agent may publish only if all of the following are true:

- every key claim maps back to the source packet
- required source URLs are present
- draft wording remains neutral
- no unsupported facts were introduced
- the draft is not materially duplicative of a recent blog post
- the content falls within defined sensitivity rules
- the daily publish cap has not been exceeded

The editor agent must reject or hold a draft if any of the following occur:

- missing source attribution
- conflicting facts across sources
- unsupported additions in the written copy
- rumor-like or weak sourcing
- sensitive topics outside policy tolerance
- speculative trend framing

## Data Model

Add the following supporting tables.

### `story_candidates`

Purpose:

- store normalized candidate stories before drafting

Suggested fields:

- `id`
- `candidate_type`
- `source_type`
- `source_url`
- `secondary_source_url`
- `headline_hint`
- `facts_json`
- `location_label`
- `occurred_at`
- `agency_name`
- `source_record_ids_json`
- `dedupe_key`
- `status`
- `score`
- `created_at`
- `updated_at`

Statuses:

- `new`
- `drafted`
- `rejected`
- `published`

### `blog_draft_reviews`

Purpose:

- store editor-agent approval decisions and evidence

Suggested fields:

- `id`
- `blog_post_id`
- `story_candidate_id`
- `decision`
- `reason`
- `evidence_json`
- `reviewed_at`
- `reviewer_agent`

Decisions:

- `approved`
- `rejected`
- `held`

### `blog_post_sources`

Purpose:

- keep per-post traceability for future audit and debugging

Suggested fields:

- `id`
- `blog_post_id`
- `source_url`
- `source_type`
- `source_title`
- `source_published_at`
- `notes`

## Scheduling and Throughput

Recommended cadence:

- planner: every 3 hours
- writer/editor pass: hourly, or immediately after planner completion

Publishing rules:

- hard cap: 5 published posts per day
- soft target: 2 published posts per day
- if only 0-1 safe posts exist on a given day, publish fewer rather than weakening quality

## Queue and Service Layout

Use the existing Redis/RQ model already present in the project.

Recommended queues:

- `ingestion` for external-source collection and candidate normalization
- `parsing` for packet building and drafting
- `publishing` for editor review and publish actions

No separate always-on chatbot sessions are required. The autonomous newsroom is implemented as scheduled jobs plus queue-backed workers.

## Mission Control and Observability

All newsroom stages must emit agent-events so Mission Control can show:

- planner running
- writer drafting
- editor reviewing
- publish approved
- publish rejected

Suggested tracked agent identities:

- `news-planner`
- `news-writer`
- `news-editor`

Each event should include:

- current stage
- candidate or draft ID
- short task description
- result state
- rejection reason when applicable

## Failure Handling

Planner failures:

- leave candidates untouched
- emit critical event
- retry on next schedule

Writer failures:

- do not create partial published content
- mark candidate for retry or rejection
- emit event with failure details

Editor failures:

- default to non-publication
- keep draft unpublished
- record rejection or hold reason

Source failures:

- if external source fetch fails, skip that source for the current cycle
- do not block the whole pipeline unless all sources fail

## Duplicate and Recency Handling

The planner must generate a deterministic `dedupe_key` from normalized facts so repeated source polling does not create duplicate drafts.

The editor must compare candidate drafts against recent published blog posts by:

- title similarity
- source overlap
- subject/location overlap
- recency window

If a draft substantially repeats a recent article, reject it unless materially new facts exist.

## Testing Strategy

Add tests for:

- candidate normalization and dedupe
- writer draft creation into `blog_posts`
- editor approval and rejection behavior
- publish cap enforcement
- duplicate detection
- event emission for Mission Control
- watchdog freshness for the new jobs if log-monitored

Prefer focused unit tests around the planner, writer, and editor policy modules, plus a thin integration test that exercises draft-to-publish flow.

## Rollout Plan

Phase 1:

- add schema for candidates, reviews, and sources
- add planner job and allow-list source configuration
- add writer draft creation path

Phase 2:

- add editor review and auto-publish path
- add mission-control events
- add cron scheduling and watchdog coverage

Phase 3:

- tune scoring, dedupe, and rejection thresholds based on observed output

## Explicit Non-Goals

Out of scope for this project:

- opinion columns
- statewide generic SEO explainers
- manual editor UI redesign
- a separate newsroom web app
- uncontrolled arbitrary web crawling
- publishing unsupported breaking-news claims faster than sources can support

## Recommendation Summary

Build a two-agent autonomous newsroom inside the existing MontanaBlotter pipeline:

- planner finds and normalizes safe Montana story candidates
- writer drafts strictly factual blog posts
- editor manager auto-publishes only after a second-pass policy review

This meets the requirement for autonomous blog output while preserving hard editorial controls and reusing the current site, queues, workers, mission-control view, and blog CMS.
