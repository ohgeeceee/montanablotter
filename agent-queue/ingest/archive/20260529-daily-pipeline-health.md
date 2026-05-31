---
profile: ingest
created: 2026-05-29T06:45:01
tier: green
status: open
priority: high
related_county: ""
related_files: []
---

# Daily Pipeline Health Report

## County ingest gaps

All counties within 72h window. ✓

## Stuck PDFs

**65 PDFs stuck >6h in uploads/:**

- `2-26 media log.pdf` (2199.3h old)
- `Calls_in_Whitehall_area_2026-03-02_08.00.10.pdf` (2083.3h old)
- `0304 log.pdf` (2057.2h old)
- `0309 log.pdf` (1937.7h old)
- `Calls_in_Whitehall_area_2026-03-09_08.00.12.pdf` (1936.5h old)
- ...and 60 more

For each: run diagnosis in report-only mode.
If parse succeeds in dry-run: back up to uploads/retry/ then re-queue (Yellow tier).
If parse fails: write diagnosis to agent-queue/ingest/ and escalate to blotter-dev.

## Jail roster freshness

All jail rosters current. ✓

## Known issues

- Gallatin Zuercher portal: in SKIPPED_SOURCES since 2026-05-11. Verify with blotter-scraper.
- 5 broken county adapters: Lewis and Clark, Cascade, Carbon, Valley, Unknown.
- No new blotters since 2026-05-20 19:10 — confirm email worker is receiving source emails.
