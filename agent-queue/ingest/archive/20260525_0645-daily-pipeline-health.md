---
profile: ingest
created: 2026-05-25T06:45:01
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

**62 PDFs stuck >6h in uploads/:**

- `2-26 media log.pdf` (2103.3h old)
- `Calls_in_Whitehall_area_2026-03-02_08.00.10.pdf` (1987.3h old)
- `0304 log.pdf` (1961.3h old)
- `0309 log.pdf` (1841.7h old)
- `Calls_in_Whitehall_area_2026-03-09_08.00.12.pdf` (1840.5h old)
- ...and 57 more

For each: run diagnosis in report-only mode.
If parse succeeds in dry-run: back up to uploads/retry/ then re-queue (Yellow tier).
If parse fails: write diagnosis to agent-queue/ingest/ and escalate to blotter-dev.

## Jail roster freshness

All jail rosters current. ✓

## RQ ingestion errors (last 24h)

- `sqlite3.IntegrityError: UNIQUE constraint failed: ingestion_jobs.source_document_id`
- `12:45:04 Worker 986c23afb2cd433ea099c8850b8894c8: job 83bfeddf-bb20-432e-8126-d1cfa5b75e5b: exception raised while executing (tasks.process_incoming_email_item)`
- `sqlite3.IntegrityError: UNIQUE constraint failed: ingestion_jobs.source_document_id`
- `2026-05-07 12:45:04,349 - ERROR - Worker 986c23afb2cd433ea099c8850b8894c8: job 83bfeddf-bb20-432e-8126-d1cfa5b75e5b: exception raised while executing (tasks.process_incoming_email_item)`
- `sqlite3.IntegrityError: UNIQUE constraint failed: ingestion_jobs.source_document_id`

## Known issues

- Gallatin Zuercher portal: in SKIPPED_SOURCES since 2026-05-11. Verify with blotter-scraper.
- 5 broken county adapters: Lewis and Clark, Cascade, Carbon, Valley, Unknown.
- No new blotters since 2026-05-20 19:10 — confirm email worker is receiving source emails.
