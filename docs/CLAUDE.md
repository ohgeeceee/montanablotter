# CLAUDE.md — MontanaBlotter.com

## Guiding Principles & "The Law"
- **Data Privacy:** Never display full PII (SSN, specific DOB, DL#) in public views. Always verify `blotter_auditor.py` has run.
- **Tone & Style:** Maintain a "Technical Authority" aesthetic. UI should be high-utility, minimalist, and transparent.
- **Source Truth:** Always link back to the original public record or state jurisdiction where possible.
- **Montana-Centric:** All logic assumes Montana state laws and jurisdictions.

## Development Commands

### Environment & Server
```bash
# Activation
source venv/bin/activate

# Local Dev
python app.py

# Production Management (systemd)
sudo systemctl restart montanablotter
sudo journalctl -u montanablotter
```

## Tests

```bash
# Run all tests
source venv/bin/activate
python -m pytest tests/

# Run a single test file
python -m pytest tests/test_text_blotter_parser.py

# Run a single test case
python -m pytest tests/test_agency_normalization.py::AgencyNormalizationTests::test_normalizes_uppercase_police_department
```

188 tests across 30+ test files in `tests/`. Tests use `unittest` with `pytest` as the runner.

## Database

SQLite at `blotter.db`. Schema migrations run automatically at startup via `init_db.migrate()` (called in `app.py`). To add a migration, append to `migrate()` in `init_db.py` using `ALTER TABLE ... ADD COLUMN` wrapped in `try/except`.

All raw connections should use `db.connect_db()` (sets `row_factory`, foreign keys, busy timeout). `init_db.py` additionally enables WAL mode for long-running write operations.

```bash
sqlite3 blotter.db ".tables"
sqlite3 blotter.db "SELECT * FROM posts ORDER BY created_at DESC LIMIT 5;"
```

## Architecture

### Ingestion Pipeline

`email_worker.py` (every 15 min) → fetches PDFs/text from IONOS IMAP → `processor.py` → `pdf_parser.py` (OCR fallback via pytesseract) → `summarizer.py` (Claude API, `claude-sonnet-4-6`) → `blotter_auditor.py` (PII scan + SEO meta-description) → writes to `blotters`, `records`, `posts` tables.

`pipeline_state.py` tracks ingestion lifecycle: source documents, job status, and pipeline events are logged so failures can be retried and audited.

### Additional Data Sources (fetchers)

Each fetcher runs independently via cron and calls `processor.py` or writes directly to the DB:

- `crimemapping_fetcher.py` — 8 Montana agencies, 6am/6pm daily
- `missoula_public_report_fetcher.py` — hourly, deduped
- `whitefish_blotter_fetcher.py` — every 6 hours, deduped
- `jail_booking_ingest.py` — 6 counties, every 2 hours (Yellowstone, Missoula, Flathead, Jefferson, Sanders, Gallatin)
- `bozeman_police_fetcher.py` — calls hourly, crime every 6 hours
- `court_refresh.py` — court tracker sources every 3 hours
- `agendas_ingest.py` — public meetings every 6 hours via `configs/agendas/montana_live.json`

Deduplication across all sources is handled by `dedupe.py` (`incident_key_set` / `incident_keys`).

### Cron Job Wrapper

All cron jobs run through `job_runner.py`, which wraps any command with structured log output and records job name/status. This is what `script_watchdog.py` reads to verify freshness. See `crontab.txt` for the full schedule.

```bash
# Verify app + scheduled jobs
python script_watchdog.py
python script_watchdog.py --json
```

### Flask Application Structure

`app.py` registers blueprints and contains inline admin routes, sheriff/PD email lists, and public-facing routes. Blueprints are in `blueprints/`:

- `api.py` — public JSON API (`/api/posts`, `/api/counties`, `/api/agencies`)
- `auth.py` — login/logout
- `payments.py` — Stripe donation handling
- `detention.py` — jail booking public pages
- `recovery_ads.py` — recovery center advertising
- `admin/` — modular admin sub-blueprints: `blog.py`, `ingestion.py`, `operations.py`, `audience.py`, `bail_ads.py`, `donations.py`, `security.py`, `recovery_ads.py`

`agency_normalization.py` normalizes sheriff/PD names across posts (e.g. maps filename-derived names to canonical forms). Called in `summarizer.py` and has a standalone backfill function.

### Content Generation Workers

All use Claude API (`claude-sonnet-4-6`):

- `summarizer.py` — per-blotter post drafts (called during ingestion)
- `blotter_auditor.py` — PII scan (SSN/DOB/DL#), tone check, SEO meta-description; CLI: `python blotter_auditor.py --post-id N`
- `morning_briefing.py` — daily subscriber digest email (7am)
- `daily_blog_worker.py` — daily crime/police roundup blog post (7:15am)
- `weekly_county_digest.py` — weekly county-level digest (Monday 7:25am)
- `weekly_safety_report.py` — per-county charge-category trend reports (Monday 7:30am)
- `weekly_snapshot.py` — Cascade County safety snapshot (Monday 7:40am)
- `charge_explainer_worker.py` — generates explanatory pages for new charge types (Monday 7:35am)

### Email

- Inbound blotters: IONOS IMAP (`config.IMAP_SERVER`)
- Outbound to sheriffs/PDs: Gmail SMTP (`config.SMTP_USER` / `config.SMTP_PASSWORD`)
- `resend_bounced.py` scans IONOS inbox for bounces and resends via Gmail

### Facebook

`facebook_worker.py` runs every 15 min (offset from email_worker) but only publishes when auto-publish is enabled in admin settings. `facebook_publisher.py` contains queue management and token handling.

## Config

Credentials live in `config.py` (gitignored). Key vars:
- `EMAIL_USER` / `EMAIL_PASSWORD` — IONOS account (inbound IMAP only)
- `SMTP_USER` / `SMTP_PASSWORD` — Gmail app password (outbound sends)
- `ANTHROPIC_API_KEY` — Claude API
- `DB_PATH`, `UPLOAD_DIR`, `LOG_FILE`
- `MB_ADMIN_ALERT_EMAILS` — admin ingest alert recipients
- `MB_INGEST_ALERT_REPEAT_HOURS` — reminder frequency for unresolved alerts (default 24)

## Secret Scanning

GitHub Actions runs `gitleaks` on PRs and pushes to `main` (config: `.gitleaks.toml`).

```bash
gitleaks git --config .gitleaks.toml --redact
```

## Admin Panel

`/admin/*`, all `@login_required`. Key capabilities: PDF upload, blotter management, bulk email to agencies, blog CMS, analytics, Facebook queue, bail bonds, recovery ads, donations.

`emailed_agencies` table prevents duplicate outreach to sheriff/PD contacts; hardcoded address lists are in `app.py` (`SHERIFFS_EMAILS` + `POLICE_EMAILS` dicts). Daniels County is missing — URL was invalid when collected.

```bash
# Create or reset admin user
MB_ADMIN_BOOTSTRAP_PASSWORD='strong-random-password' python seed_admin.py myusername
```

## Public Pages

- `/` — paginated activity feed with calendar, search, filters
- `/arrests` — filtered arrest log
- `/jail-rosters` — all 56 county jail roster links
- `/laws` — Montana statute reference
- `/blog`, `/blog/<slug>` — CMS blog
- `/missing-persons` — MT missing persons tracker
- `/courts` — court tracker directory and hearing feed
- `/meetings` — public meetings/agendas
