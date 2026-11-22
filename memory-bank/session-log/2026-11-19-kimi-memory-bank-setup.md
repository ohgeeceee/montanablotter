# Session: Memory bank setup

- **Date:** 2026-11-19
- **Agent:** kimi (chat session, via GitHub MCP)
- **Scope:** `memory-bank/` (new), `AGENTS.md`

## Goal
Create a shared, persistent memory bank so the agent fleet (Hermes, Codex, Kimi Code,
OpenClaw) stops starting cold, plus a roadmap for building out the site.

## Done
- Created `memory-bank/` with README (protocol), active-context, decisions, gotchas,
  progress, roadmap, and session-log template.
- Added a "Memory Bank" section to `AGENTS.md` with read/write instructions and a
  doc-drift notice (`/root` → `/opt/montanablotter`).

## Not done / blockers
- Repo-wide path reconciliation (`AGENTS.md` body, `docs/notes.md`,
  `HANDOFF-paywall.md` still reference `/root/montanablotter`) — flagged, not fixed.
- Paywall implementation status unverified — audit against `HANDOFF-paywall.md`.

## State changes
- None (docs-only commit; `git_sync.sh` will pull and restart `mtblotter` normally).

## Handoff notes
- Owner to confirm: old fine-grained PAT revoked? `/alerts` import bug fixed?
- Agents: read `memory-bank/README.md` before your first session using this system.
