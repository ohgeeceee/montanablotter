# Memory Bank — Shared Agent Memory

This folder is the **living memory** for every AI agent that works on Montana Blotter
(Hermes, Codex, Kimi Code, OpenClaw, Claude, and human-driven chat sessions).
`AGENTS.md` tells you how the project is *supposed* to work; this folder records what
is *actually* happening, what was decided, and what hurt.

## Read protocol (session start)

1. `AGENTS.md` — project orientation, stack, conventions.
2. `MISSION.md` — tiered authority model (Green / Yellow / Red). Respect it.
3. `memory-bank/active-context.md` — current deployment reality and focus.
4. `memory-bank/gotchas.md` — skim before touching infra, DB, or deploys.
5. `memory-bank/roadmap.md` — what to work on; `progress.md` for what's done.
6. Latest entries in `memory-bank/session-log/` if your task continues prior work.

## Write protocol (before ending a session)

- **State changed?** Update `active-context.md` (deployments, ports, services, focus).
- **Made a non-obvious choice?** Append a dated entry to `decisions.md` with the why.
- **Hit something that cost time?** Append to `gotchas.md` so no agent repeats it.
- **Finished or paused work?** Drop a handoff in `session-log/` using `TEMPLATE.md`,
  named `YYYY-MM-DD-<agent>-<slug>.md`.
- **Shipped a roadmap item?** Move it from `roadmap.md` to `progress.md` with a date.

## Rules

- **This repo is PUBLIC.** Never write secrets, tokens, private keys, real user PII,
  or unreleased business-sensitive numbers here. gitleaks runs in CI.
- **Date everything** (`YYYY-MM-DD`). Undated "current state" rots silently.
- **Append, don't rewrite.** History is the point. Strike through (`~~`) obsolete
  lines with a dated note instead of deleting them.
- **Keep it tight.** Each file should stay scannable (< ~300 lines). Archive old
  session logs rather than letting files bloat.
- **Contradictions:** if reality contradicts `AGENTS.md`/`docs/`, record the
  discrepancy in `active-context.md` and fix the stale doc when you touch it —
  don't silently pick one.
- **Authority still applies.** Updating this folder is Green-tier. Code changes,
  deploys, and restarts stay Red-tier per `MISSION.md`.
