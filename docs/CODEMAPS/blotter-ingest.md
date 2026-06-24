# Code Map: Police Blotter Ingestion Pipeline

Path: `/root/montanablotter`  
Concise reference for how police-blotter PDFs, emails, and narrative text flow from IMAP into the SQLite database.

---

## Module Map

| File | Responsibility |
|------|----------------|
| `email_worker.py` | Top-level IMAP intake. Connects to the configured IMAP server, enumerates unread messages, downloads attachments (PDF/DOCX/eml), and dispatches each source document to `services/blotter/processor.py`. Tracks work in `source_documents` + `ingestion_jobs`. |
| `services/blotter/processor.py` | Document-processing orchestrator (`DocumentProcessor`). Detects source type from sender/subject/filename, picks the right parser adapter, runs parser → normalizer → dedupe → auditor → persistence. Updates `ingestion_jobs` status and writes `pipeline_events`. |
| `services/blotter/parser.py` | Extractors and adapters for PDF, DOCX, plain email text, and image-based attachments. Returns raw strings and normalized record dicts. |
| `services/blotter/auditor.py` | Post-parse validation: anomaly detection, required-field checks, sensitive-pattern/PII flags, and per-job audit logging. |
| `services/summarizer/engine.py` | LLM-backed summarization used during processing to produce blotter-level `posts.summary` when a batch of records is condensed. |
| `core/dedupe.py` | Hash + fuzzy duplicate suppression for raw records and source documents before persistence. |
| `core/pipeline_state.py` | Centralizes `ingestion_jobs` / `source_documents` / `pipeline_events` state transitions and retry bookkeeping. |
| `core/agency_normalization.py` | Maps raw agency strings (e.g. "GFPD", "Havre Police Dispatch") to canonical names and county hints. |
| `init_db.py` | Database bootstrap, migrations, and schema convergence for all ingestion tables. |
| `crontab.txt` | Production cron schedule; `job_runner.py` wraps workers for logging and lock semantics. |

---

## Data Flow (High Level)

```text
IMAP account ──▶ email_worker.py
                        │
                        ▼
              source_documents (raw eml/PDF, SHA256)
                        │
              ingestion_jobs (status='pending')
                        │
                        ▼
              services/blotter/processor.py
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
   parser.py     agency_normalization   dedupe.py
        │               │               │
        ▼               ▼               ▼
   auditor.py  ──▶  summarizer/engine  ──▶  records / blotters / posts
                        │
                 pipeline_events + ingestion_jobs updated
```

1. **Acquisition** — `email_worker.py` logs in to the configured IMAP host, scans for unread mail, and classifies each message by sender/subject. PDF, DOCX, image and `.eml` payloads are written to disk and hashed.
2. **Source registration** — a row is inserted in `source_documents` keyed by `(source_type, content_sha256)`. `init_db.py` also defines `source_registry` / `source_artifacts` for generic polled adapters.
3. **Job creation** — `core/pipeline_state` creates/updates `ingestion_jobs` per `source_document_id` with `status = pending|running|completed|failed` and `retry_count`.
4. **Parsing** — `processor.py` selects a parser from `services/blotter/parser.py` based on source type and extracts structured records (`cfs_number`, `date`, `time`, `incident_type`, `location`, `county`, etc.).
5. **Agency normalization** — `core/agency_normalization.py` canonicalizes raw agency strings using known aliases and county hints.
6. **Deduplication** — `core/dedupe.py` drops records whose hash/fingerprint already exists in `records` for the same county/date window.
7. **Auditing** — `services/blotter/auditor.py` flags malformed rows, missing counties, and sensitive/PII patterns; failures update `ingestion_jobs.last_error` and write to `pipeline_events`.
8. **Summarization** — when needed, `services/summarizer/engine.py` condenses a batch into a `posts` row with `title`/`summary`.
9. **Persistence** — clean records go to `records`; batch metadata goes to `blotters`; blotter-level digests go to `posts`.

---

## Key Tables

| Table | Purpose | Foreign Keys / Notes |
|-------|---------|----------------------|
| `source_documents` | Raw incoming source artifacts (eml, PDF, text, etc.) | `content_sha256` UNIQUE per `(source_type, content_sha256)` |
| `source_registry` | Catalog of known external sources/agencies + poll intervals | `source_key` UNIQUE |
| `source_artifacts` | Poll artifacts linked to registry/documents | FKs to `source_registry` and `source_documents` |
| `ingestion_jobs` | Per-document processing jobs and retries | `source_document_id` FK, UNIQUE, `status`, `last_error` |
| `pipeline_events` | Fine-grained stage/status audit trail | `ingestion_job_id` FK |
| `blotters` | Batch-level metadata for each PDF/doc processed | `county`, `filename`, `status` |
| `records` | Individual parsed incidents | `blotter_id` FK, indexed on `(county, date, time)` |
| `posts` | Blotter-level human-readable digests (record_id nullable) | `blotter_id` FK |
| `command_logs` | Verbatim log entries when raw logs are split into records | `record_id` FK |

### `source_documents` (selected columns)

- `source_type`, `source_message_id`, `source_sender`, `source_subject`
- `source_received_at`, `filename`, `content_sha256`, `storage_path`
- `raw_text`, `extraction_method`, `extraction_warnings`

### `ingestion_jobs` (selected columns)

- `id`, `source_document_id`, `status`, `retry_count`, `last_error`
- `started_at`, `finished_at`
- Unique on `source_document_id` so each raw document gets one job.

### `records` (selected columns)

- `id`, `blotter_id`, `cfs_number`, `date`, `time`, `incident`, `incident_type`
- `location`, `details`, `county`, `officer`

### `posts` (selected columns)

- `id`, `record_id`, `blotter_id`, `title`, `summary`, `city`, `county`
- `agency_type`, `agency_name`, `incident_date`, `incident_type`

---

## Cron Schedule (from `crontab.txt`)

| Job | Schedule | Notes |
|-----|----------|-------|
| `email_worker.py --mode queue` | `*/15 * * * *` | Main IMAP poll for records@[REDACTED] inbox via `job_runner.py` |
| `email_image_blotter.py` | `8-59/15 * * * *` | 14-day image/PDF catch-up window |
| `healthcheck_restart.sh` | `*/3 * * * *` | Web liveness probe |
| `run_all_scrapers.py` | `20 */6 * * *` | Non-email source ingestion |
| `backup_db.sh` | `0 3 * * *` | Daily SQLite backup |
| Generic public-meeting/agenda workers | hourly / 6-hourly | Not part of blotter ingest, left in same schedule |

> All cron entries are run inside `/root/montanablotter` with `BASH_ENV=/root/montanablotter/.env`. Credential-like values are omitted from docs.

---

## CLI Commands

### `email_worker.py`

```bash
cd /root/montanablotter
python email_worker.py --mode queue                     # poll IMAP for unread messages
python email_worker.py --mode queue --dry-run             # read only, no write
python email_worker.py --county cascade --days 3          # restrict to county/date window
python email_worker.py --all --mark-read                  # backfill; mark processed read
python email_worker.py --imap-host imap.example.com
```

### `services/blotter/processor.py` (module CLI)

Run as module or imported; supports processing a single `source_document_id` / file path:

```bash
python -m services.blotter.processor --source-id <id>
python -m services.blotter.processor --file /path/to/blotter.pdf --county cascade
```

### `init_db.py`

```bash
python init_db.py          # backup existing DB, run migrations, create missing tables
python init_db.py --fresh  # (if supported) drop and recreate; verify before using
```

---

## Ops / Security Notes

- **Secrets**: IMAP password, SMTP/Anthropic API keys, DB path are read from `.env`. Never hard-code or log them.
- **PII**: `auditor.py` inspects parsed text for sensitive patterns (SSN, DOB, addresses). Do not disable these flags globally.
- **Retry safety**: `core/pipeline_state.py` manages `retry_count` and `status`; jobs exceeding retries are marked `failed` and require manual triage.
- **Concurrency**: SQLite `journal_mode = WAL` and `busy_timeout = 30000` are set in `init_db.py._configure_sqlite`.
- **Idempotency**: `source_documents` is keyed by `(source_type, content_sha256)`; re-running the same PDF just re-associates the same source document and skips inserts if already processed.
- **Backups**: `init_db.py` auto-backs up `blotter.db` before migrating. `scripts/ops/backup_db.sh` runs nightly from cron.
- **Monitoring**: Check `logs/mail.log`, `logs/email_image_blotter.log`, and `pipeline_events` for stage-level diagnostics.

---

## Gotchas

1. **Processor/source-type matching is fragile** — a mis-classified sender/subject picks the wrong parser adapter and produces garbage records. Keep `parser.py` registration list aligned with cron sources.
2. **One job per source document** — the UNIQUE constraint on `ingestion_jobs(source_document_id)` means a re-process must update the existing row rather than insert a duplicate job.
3. **`records.date` is TEXT** — downstream analytics rely on `YYYY-MM-DD` when casting; parsers must normalize MM/DD/YY to ISO.
4. **`posts` is a blotter-level digest now** — `record_id` is nullable. Do not assume every post maps to one record.
5. **`source_documents.raw_text` can be large** — very large PDFs may exceed SQLite cell limits if full raw bytes are stored; verify extractor writes files to `storage_path` and stores only extracted text in `raw_text`.
6. **Retry loops can hammer IMAP** — `email_worker.py --all` should not be cronned; use it manually for backfill.
7. **`pipeline_events` grows quickly** — consider periodic archival or retention policy for unbounded event rows.
