# Site Operations Team — mtblotter.com

Companion to `MISSION.md` and `docs/CODEMAPS/*`. Defines the agent fleet that runs the public site.

## Roles (from MISSION.md)

| Role | Profile | Responsibility |
|------|---------|----------------|
| Ops | blotter-ops | Uptime, disk, certs, backups, systemd/nginx health |
| Ingest | blotter-ingest | Pipeline flow, stuck PDFs, format drift, county coverage |
| Dev | blotter-dev | Draft parser patches, new county scaffolds, dependency notes, tests |
| Civic | blotter-civic | Outreach drafts, contact roster, press mentions, legal notes |

## Tiers (from MISSION.md)
- Green: observe, draft, report — no approval needed.
- Yellow: recoveries (restart workers, re-queue PDFs, rotate logs, snapshot DB) — report within 1h.
- Red: code changes, deploys, DB writes, outbound comms — Jon approval required, draft to agent-queue/.

## Daily/weekly cadence
- Daily 07:00 MT: ops health + pipeline health digest.
- Weekly Sunday evening: dev queue + civic queue digest.
- Real-time alerts: site down >15m, disk >90%, cert <14d, backup broken, unit failed after 3 Yellow restarts.

## Backlog (initial)
1. Create agent-queue subdirs (ops/, ingest/, dev/, civic/, new-counties/) with .gitkeep.
2. Wire digest delivery channel (email/Telegram) before schedules go live.
3. Confirm all Yellow-tier recoveries idempotent.
4. Promote reliable workflows Green/Yellow via deliberate MISSION.md edits.
5. Add disk/cert/backup monitoring scripts if missing.

## Working rules
- Agents never autonomously change code, deploy, or send outbound comms.
- All Red-tier actions drafted to agent-queue/<profile>/<timestamp>-<name>/.
- Digests never longer than one screen; silence ≠ healthy.
