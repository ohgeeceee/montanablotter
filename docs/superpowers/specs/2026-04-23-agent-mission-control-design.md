# Agent Mission Control — Design Spec
**Date:** 2026-04-23
**Status:** Approved for planning

## Overview

Add a private admin-only "Mission Control" page to the existing Montana Blotter Flask application that shows the real OpenClaw/Codex agents currently running on the VPS in a 2D office layout. The office is a visualization of actual runtime state, not a simulation. If the system does not know what an agent is doing, the UI must say so clearly rather than inventing activity.

The primary goal for v1 is truthful visibility into what each real agent is doing right now. The visual office is a rendering layer over live telemetry. Decorative motion, fictional workflows, and write-back controls are out of scope.

---

## Goals

- Show each real tracked agent's current live state with 1-2 second freshness.
- Show what problem or task an agent is working on right now, when that data is available.
- Display agent movement through a 2D office based on real runtime state changes.
- Keep the entire interface behind the existing admin login.
- Reuse the current Flask admin surface rather than creating a separate service or frontend stack.

## Non-Goals

- No public or shareable view.
- No agent chat, message sending, approval actions, or remote control in v1.
- No fake movement or idle animation unrelated to real state.
- No requirement for websocket infrastructure in v1.
- No dependence on log parsing as the primary truth source.

---

## Recommended Approach

Use a hybrid telemetry model:

1. A lightweight heartbeat/status emitter is the canonical source of current agent state.
2. Process inspection and log ingestion are secondary sources for enrichment and fallback.
3. The dashboard polls the Flask backend every 1-2 seconds for the current office snapshot and recent event history.

This design is recommended because logs are good at describing what happened, but they are poor at answering "what is this agent doing right now?" A heartbeat layer solves that directly while preserving logs for context and recovery.

---

## User Experience

### Entry Point

- Add a new admin page at `GET /admin/mission-control`.
- It is available only to authenticated admin users through the existing login flow.

### Main Layout

The page is split into three functional regions:

1. Top operations bar
Shows host-level health: OpenClaw runtime status, number of live agents, stale agent count, last successful snapshot time, and global warning banners.

2. Office canvas
A simple top-down 2D office floor with fixed state zones. Each agent appears as a labeled avatar/card that moves between zones based on real current state.

3. Details rail
A side panel and recent-events rail show exact current-task details, recent state changes, last tool/action, last heartbeat time, and raw-source evidence.

### State Zones

The office uses fixed zones mapped to operational states:

- `ready` -> Ready lounge
- `working` -> Working desks
- `tool_run` -> Tool bench
- `waiting` -> Review table
- `blocked` -> Incident bay
- `done` -> Archive shelf
- `offline` -> Offline dock

Agents move only when their real state changes. There is no random walking or ambient motion.

### Agent Card

Each agent avatar/card shows:

- display name
- runtime type (`openclaw`, `codex`, or other supported runtime)
- current state
- current task summary
- elapsed time in current state
- freshness indicator
- confidence/source badge (`heartbeat`, `observed-only`, `stale`)

Clicking an agent opens detailed information in the side panel.

### Agent Detail Panel

Selecting an agent shows:

- agent ID and display name
- PID and/or session identifier when available
- current problem ID
- current step label
- full current task text
- last tool/action
- state started time
- last heartbeat time
- recent event timeline
- raw source snippets used to derive state

### Recent Events Rail

The events rail shows the newest state changes and operational events across all tracked agents, newest first. It is intended to answer "what just changed?" without requiring the operator to click each agent.

---

## Truth Model

### Canonical Source

The system of record for current state is a lightweight status emitter that runs alongside real agents and periodically reports:

- current state
- current task text
- current problem ID
- step label
- last tool/action
- timestamps

This emitter must be attached to actual running OpenClaw/Codex workflows on the VPS.

### Secondary Sources

Fallback and enrichment sources:

- OpenClaw logs
- process inspection
- tmux/session metadata if available

These sources may populate context or mark an uninstrumented agent as observed, but they must not override heartbeat truth when heartbeat data exists.

### Confidence Labels

Every agent record must expose how trustworthy the current status is:

- `heartbeat`: direct live telemetry from instrumented runtime
- `observed-only`: inferred from logs/process/session evidence
- `stale`: heartbeat previously existed but is older than threshold
- `offline`: no active runtime evidence

The UI must surface this clearly.

---

## Data Model

### Live Agent Record

Each tracked agent has one live record with at least:

- `agent_id`
- `display_name`
- `runtime`
- `pid`
- `session_id`
- `state`
- `current_task`
- `problem_id`
- `step_label`
- `last_tool`
- `detail_text`
- `source_kind`
- `confidence`
- `last_heartbeat_at`
- `state_started_at`
- `updated_at`
- `stale`

### Event Record

Recent events are stored separately for timeline/history use:

- `id`
- `agent_id`
- `event_type`
- `state`
- `message`
- `problem_id`
- `tool_name`
- `source_kind`
- `created_at`
- `raw_excerpt`

### Storage

- Current live state may be kept in memory for fast reads.
- Recent events must also be persisted in SQLite so page reloads and app restarts retain short-term history.
- SQLite retention for v1 can be bounded to a rolling recent window such as the latest 1,000-5,000 events.

---

## State Machine

### Supported States

- `ready`
- `working`
- `tool_run`
- `waiting`
- `blocked`
- `done`
- `offline`

### State Rules

- `ready`: agent is alive but not currently assigned meaningful work
- `working`: agent is actively reasoning or progressing a task
- `tool_run`: agent is executing a command, tool, or external operation
- `waiting`: agent is waiting for input, review, dependency, or external completion
- `blocked`: agent hit an error or cannot proceed
- `done`: agent completed the current problem or work item and has not yet started another
- `offline`: no credible current activity from heartbeat or observer sources

### Freshness Rules

- Heartbeat cadence target: every 1-2 seconds
- Mark as `stale` if heartbeat age exceeds configured threshold, for example 5 seconds
- Promote to `offline` after a longer threshold, for example 20-30 seconds without credible evidence

Thresholds should be configurable, but the UI copy should remain simple.

---

## Backend Architecture

### Application Placement

Implement Mission Control inside the existing Flask app and admin blueprint structure. Do not create a separate Node service or SPA for v1.

### Components

1. Agent status registry
A Python service module that maintains live in-memory agent state and writes recent events to SQLite.

2. Heartbeat ingestion endpoint or local ingestion path
A local-only ingestion mechanism used by instrumented agent wrappers to update current status.

3. Observer adapters
Read-only adapters for OpenClaw logs, process inspection, and optional session metadata. These enrich the registry and cover uninstrumented runtimes.

4. Snapshot API
Returns the full current office state used by the polling UI.

5. Events API
Returns recent events and per-agent history for side panels and timeline views.

### API Surface

Expected v1 routes:

- `GET /admin/mission-control`
- `GET /admin/api/mission-control/snapshot`
- `GET /admin/api/mission-control/events`

Optional internal/local-only ingestion routes if needed:

- `POST /internal/mission-control/heartbeat`

If heartbeat updates can be written directly from local Python wrappers without HTTP, that is preferred for simplicity.

---

## Frontend Architecture

### Rendering Model

- Server-render the initial admin page via Jinja.
- Use a focused client-side script for polling and DOM updates.
- Poll snapshot/events endpoints every 1-2 seconds.

### Visual Style

- Use a clear top-down office map, not an isometric game.
- Optimize for legibility first, atmosphere second.
- Use purposeful zones, labels, and motion with restrained styling.
- Preserve the existing admin design language where necessary, but the Mission Control page may have a more distinctive operational look.

### Motion Rules

- Reposition an agent only when the state zone changes.
- Use short transitions to show movement between zones.
- Avoid constant micro-animation.
- If status is stale or offline, freeze movement and visually downgrade the card.

### Failure Display

- If the snapshot endpoint fails, show a visible stale-data banner.
- If an agent becomes stale, the UI must mark it rather than leaving the prior state looking fresh.
- If an agent is observed-only, badge it accordingly.

---

## Instrumentation Strategy

### Preferred Instrumentation

Wrap real agent launches with a small local reporter that can emit:

- lifecycle start/stop
- state changes
- current task text
- current problem ID
- step label
- tool execution start/end
- heartbeat timestamps

This wrapper is responsible for feeding the registry with authoritative current-state data.

### Instrumentation Constraint

Mission Control is only useful if it observes the real running agents. Therefore, integration work must target the actual OpenClaw/Codex launch paths on this VPS rather than a parallel mock runner.

### Fallback Strategy

Where direct instrumentation is not yet possible:

- detect agent process presence
- infer partial status from logs/process/session evidence
- mark the result as lower confidence

---

## Security

- Every Mission Control page and API route requires the existing admin login.
- No public endpoints.
- No browser-triggered shell execution.
- No write actions from the UI in v1.
- Internal heartbeat ingestion must not be internet-exposed.
- Raw excerpts shown in the UI must be escaped and rendered as text.

---

## Testing Strategy

### Backend

- unit tests for state-transition logic
- unit tests for stale/offline thresholds
- unit tests for fallback observer parsing
- route tests for admin auth protection
- route tests for snapshot and events payload shape

### Frontend

- smoke tests for state-to-zone rendering
- polling/update behavior tests where practical
- stale/offline visual state tests

### Integration

- test that an instrumented fake agent can register, transition through states, emit events, and disappear cleanly
- test that observer-only agents appear with lower confidence

---

## Out of Scope

- controlling agents from the dashboard
- chat with agents
- approving or rejecting tasks
- multi-user collaboration
- historical analytics beyond recent operational history
- public dashboards
- rich replay of past office sessions
- audio, game mechanics, or decorative simulation systems

---

## Open Decisions For Planning

These are implementation decisions, not product-definition blockers:

- whether heartbeat ingestion is in-process Python calls, SQLite writes, or localhost HTTP posts
- exact SQLite schema and retention limit
- exact mapping from OpenClaw/Codex runtime signals to state transitions
- whether Mission Control replaces the older `/admin/agents` page or coexists with it

None of these change the product direction established in this spec.

---

## Recommendation Summary

Build Mission Control as an authenticated Flask admin page backed by a hybrid telemetry registry. Treat heartbeat/status emission from the real agent runtime as the source of truth for current state, enrich with logs and process observation, poll every 1-2 seconds, and render the result as a 2D office where each agent's position corresponds to its real live workflow state.
