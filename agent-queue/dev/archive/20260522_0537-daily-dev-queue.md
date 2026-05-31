---
profile: dev
created: 2026-05-22T05:37:43
tier: green
status: open
priority: high
related_county: ""
related_files: []
---

# Daily Dev Queue

## Priority 1 — Court calendar WAF block (broken since 2026-05-17)

All 100+ MT courts returning 'Request Rejected' on pubcourts.mt.gov.
Root cause: the server is blocking our IP, user-agent, or request pattern.

Investigation steps:
1. Check `services/court/colj_portal_scraper.py` — review `_login()` method.
2. Test if rotating the user-agent header resolves the block.
3. Check if adding Referer header or increasing wait time helps.
4. Draft minimal fix as a red-tier proposal in agent-queue/dev/.

## Priority 2 — Gallatin Zuercher recovery

Gallatin is in SKIPPED_SOURCES at `services/ingestion/jail_bookings.py:61`.
When blotter-scraper confirms the portal returns valid data, draft the 2-line
removal from SKIPPED_SOURCES as a red-tier proposal.

## Priority 3 — 5 broken county adapters

Investigate each; determine if source changed format or feed is dead:
- Lewis and Clark County
- Cascade County
- Carbon County
- Valley County
- Unknown (check for agency normalization issue in blotter parser)
