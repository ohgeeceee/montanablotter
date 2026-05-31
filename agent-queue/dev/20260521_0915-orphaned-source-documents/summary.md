---
profile: dev
created: 2026-05-21T09:15:00-06:00
tier: red
status: open
priority: low
related_files:
  - /root/montanablotter/blotter.db
  - /root/montanablotter/email_worker.py
  - /root/montanablotter/services/ingestion/
---

# Summary

8 `source_documents` rows have no corresponding `ingestion_jobs`, meaning they were never extracted or published. Most are from May 6–7, 2026.

## Affected documents

| id | source_type | filename | created_at | extraction_method |
|----|-------------|----------|------------|-------------------|
| 370 | bozeman_daily_case_reports | crime-20260507T125502Z.json | 2026-05-07 12:55 | arcgis_query |
| 368 | bozeman_calls_for_service | calls-20260507T124002Z.json | 2026-05-07 12:40 | arcgis_query |
| 366 | bozeman_daily_case_reports | crime-20260506T185502Z.json | 2026-05-06 18:55 | arcgis_query |
| 364 | bozeman_daily_case_reports | crime-20260506T125502Z.json | 2026-05-06 12:55 | arcgis_query |
| 363 | email | media(1).pdf | 2026-05-06 12:45 | pdf_attachment |
| 362 | bozeman_calls_for_service | calls-20260506T124002Z.json | 2026-05-06 12:40 | arcgis_query |
| 361 | missoula_public_report | (no filename) | 2026-05-06 07:10 | html_scrape |
| 247 | imap_pdf | 4-14 media log.pdf | 2026-04-14 15:45 | pdf_attachment |

All have `raw_text IS NULL`, confirming extraction never ran.

## Proposed action

1. Investigate why ingestion_jobs were not created (possible race condition in `email_worker.py` or ingestion pipeline).
2. Create missing `ingestion_jobs` rows for these 8 docs.
3. Re-run extraction for each.
4. If source data is no longer available (e.g., ArcGIS query expired), mark as `failed` with appropriate error.

## Rollback

Delete the newly created ingestion_jobs rows if they cause issues.

## Verification

All 8 docs should have `ingestion_jobs` rows with status `published` or `failed`, and `raw_text` should no longer be NULL.
