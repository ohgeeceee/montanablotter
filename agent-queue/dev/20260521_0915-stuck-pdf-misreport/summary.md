---
profile: dev
created: 2026-05-21T09:15:00-06:00
tier: red
status: open
priority: medium
related_files:
  - /root/montanablotter/services/ops/ingestion_monitor.py
  - /root/montanablotter/agent-queue/digests/raw/ingest/
---

# Summary

The daily digest reports "Stuck PDFs (>6h in uploads/)" by scanning the filesystem, which counts already-processed PDFs as "stuck." This creates false urgency.

## Problem

- 2026-05-21 digest: **73 stuck PDFs**
- Reality: **147 of 157 PDFs** are tracked in `source_documents` with completed `ingestion_jobs`
- Only **11 untracked PDFs** existed, all from February 2026 (archived to `uploads/archive/` today)
- The filesystem-age metric does not distinguish processed vs. unprocessed files

## Proposed change

Update the stuck-PDF detection logic in `ingestion_monitor.py` (or wherever the agent computes this metric) to query the database instead of raw filesystem age:

```sql
SELECT COUNT(*) FROM source_documents sd
LEFT JOIN ingestion_jobs ij ON ij.source_document_id = sd.id
WHERE sd.storage_path LIKE '%uploads%'
  AND (ij.id IS NULL OR ij.status NOT IN ('published', 'failed'))
  AND sd.created_at <= datetime('now', '-6 hours');
```

Alternatively, if the metric is computed by the agent's own prompt logic, update the agent skill to use the DB query.

## Rollback

Revert the file or prompt change.

## Verification

Next daily digest should show 0 stuck PDFs (or only genuinely untracked ones).
