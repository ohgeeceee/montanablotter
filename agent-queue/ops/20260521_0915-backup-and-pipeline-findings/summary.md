---
profile: ops
status: open
tier: yellow
created: 2026-05-21T09:15:00-06:00
related_files:
  - /root/montanablotter/backup_db.sh
  - /root/montanablotter/crontab.txt
  - /root/montanablotter/db_backups/
  - /root/montanablotter/logs/backup.log
---

# Summary

Investigated the "stale backup chain" and "73 stuck PDFs" alerts from the 2026-05-21 daily digest. Most issues are explainable; one requires a code change (Red-tier proposal).

## Findings

### 1. Backup is actively running — NOT stale

- A backup started at **07:26 MT** today and is currently ~65% complete (8.4 GB of ~13 GB copied).
- The Python backup process (PID 1280144) is running at 85% CPU, state `R`, writing to `db_backups/blotter_20260521_072631.db`.
- Previous backup history:
  - **May 14**: failed due to missing AWS S3 credentials (exit 1)
  - **May 17**: timed out after 12h (cron `--timeout 43200`); the 13 GB DB copy at idle I/O priority exceeds 12h under load
  - **May 18**: correctly skipped because May 17 lock was still held
  - **May 20–21**: multiple restarts (possible manual or cron overlap)
- The digest reports "latest: 2026-05-17 (3.2d old)" because it looks for `.db.gz` or `.bak` files; the in-flight temp `.db` is not counted.

### 2. Backup timeout risk — Red-tier proposal queued

The 12h cron timeout is insufficient for a 13 GB DB at `ionice -c3 nice -n 19`. Proposal:
- Increase `pages` from `8192` to `32768` (fewer sleep batches)
- Reduce `sleep` from `0.5` to `0.25`
- Or increase cron `--timeout` from `43200` to `64800` (18h)
- Or run backup during lower-traffic hours

Draft proposal: `agent-queue/dev/20260521_0915-backup-timeout-fix/`

### 3. SSH brute-force is elevated but contained

- 11,394 failed-password/invalid-user attempts in 24h
- `fail2ban` is active; no successful breaches detected
- This is noise, not an active incident
- Optional hardening: key-based auth only (Red-tier infrastructure change)

### 4. Gallatin scraper — upstream maintenance, NOT a code bug

- `jail_bookings.py --dry-run` for Gallatin returns: `fetched=0 new=0 updated=0 missing=0`
- `latest_error` in DB: **"Zuercher portal is in maintenance mode as of 2026-05-21."**
- The old "JSON parse error at char 0" from May 11 was the maintenance page being served as HTML; the scraper now correctly identifies maintenance mode.
- **No code fix needed.** Monitor for portal restoration.

### 5. Court calendars — Montana court portal intermittent outage

- `services.court.refresh` logs show:
  - `ERR_CONNECTION_RESET` at `dcportal.pubcourts.mt.gov`
  - `ERR_CONNECTION_RESET` at `coljportal.pubcourts.mt.gov`
  - Manual `curl` also fails with exit code 56 (connection reset)
- `court_source_alerts` shows last successes as recently as **03:48 today** for COLJ and **15:35 yesterday** for District Court, confirming intermittent connectivity.
- **No code fix needed.** This is an external infrastructure issue on the Montana court system's side.
- Both open alerts are in `court_source_alerts` (IDs 36, 37).

### 6. "73 stuck PDFs" is misleading — actual pipeline is healthy

- `uploads/` contains **157 PDF files** total, but **147 are tracked** in `source_documents` with completed `ingestion_jobs`.
- Only **11 untracked PDFs** existed, all from **February 2026** (orphaned from before current pipeline or manual uploads).
- **Action taken (Yellow):** Archived the 11 untracked February PDFs to `uploads/archive/`.
- `uploads/` now holds **62 top-level PDFs** plus subdirectories, most already processed.
- The daily digest's "73 stuck PDFs" count appears to be a simple filesystem scan (`find uploads/ -mtime +0.25`) without correlating against `source_documents`. This inflates the metric and creates false urgency.
- **Red-tier proposal:** Update `blotter-ingest`'s stuck-PDF detection to query `source_documents` and `ingestion_jobs` instead of raw filesystem age. Queue: `agent-queue/dev/20260521_0915-stuck-pdf-misreport/`

### 7. No new blotters since May 20 19:10 — source silence, NOT pipeline failure

- `email_worker.py` logs show cron firing every 15 minutes and completing successfully.
- Each run reports: `queued_count=0`, `No new emails found for queue scan`.
- `source_documents` shows 3 new docs created on May 21 (rosebud), 8 on May 20.
- The pipeline is working; the upstream email sources simply haven't sent new blotters in ~14 hours.
- This is within normal variance for some sources (weekends, holidays, source maintenance).

### 8. 8 orphaned source_documents lack ingestion_jobs

- 8 `source_documents` rows have no corresponding `ingestion_jobs` row.
- Most are from May 6–7 (Bozeman arcgis, email PDF, Missoula scrape) and one from April 14 (email PDF).
- These documents have `raw_text IS NULL`, meaning extraction never occurred.
- **Red-tier proposal:** Script to re-create ingestion_jobs for these 8 docs and trigger re-extraction. Queue: `agent-queue/dev/20260521_0915-orphaned-source-documents/`

## Recommended next actions (for Jon)

1. **Wait for current backup to finish** (~1–2h remaining). Verify `blotter_20260521_072631.db.gz` appears in `db_backups/`.
2. **Review Red-tier proposals** in dev queue:
   - Backup timeout/optimization
   - Stuck-PDF detection fix
   - Orphaned source_document recovery
3. **No action needed** for Gallatin or court calendars — monitor only.
4. **Approve or discard** the civic outreach draft `20260519_0840-outreach-no-adapter-counties`.
5. **Review `_roster.yaml`** created in `agent-queue/civic/` — populate contact emails and names for counties with known PIOs/records officers.
