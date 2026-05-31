# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Last Updated:** 2026-05-29

## Essential Commands

Always activate the virtualenv first:
```bash
source /root/montanablotter/venv/bin/activate
```

**Dev server:** `python3 app.py`
**Production restart:** `systemctl restart montanablotter`
**Tests:** `./venv/bin/python3 -m pytest`
**Single test file:** `./venv/bin/python3 -m pytest tests/test_public_api.py`
**DB init/migrate:** `python3 init_db.py`
**Health check:** `./venv/bin/python3 script_watchdog.py`
**Secret scan:** `gitleaks git --config .gitleaks.toml --redact`
**Seed admin:** `python3 seed_admin.py <username>`

## Architecture Overview

### Request Path (Web)
`nginx → gunicorn (3 workers, port 5000) → app.py`

`app.py` is the monolithic Flask entrypoint (~12,763 lines). It imports and registers blueprints from `blueprints/` for modular route groups:
- `blueprints/api/` — REST API with token auth
- `blueprints/auth/` — Login/logout, MFA
- `blueprints/payments/` — Stripe integration for donations
- `blueprints/detention/` — Jail bookings and arrestee tracking
- `blueprints/code_violations/` — Code violation records
- `blueprints/license_sanctions/` — License/sanction records
- `blueprints/sex_offender/` — Sex offender registry tracking
- `blueprints/public_salaries/` — Public salary records
- `blueprints/government_spending/` — Government spending data
- `blueprints/watchdog/` — Agency oversight tools
- `blueprints/recovery_ads/` — Recovery center advertising

Public routes live directly in `app.py`; admin routes are split across blueprints and admin sub-modules.

**Database access** is done inline via `get_db()` (returns a raw `sqlite3.Connection` with `row_factory = sqlite3.Row`). SQLAlchemy models in `db.py` are used only for specific features — most of the codebase uses raw SQL.

### Ingestion Pipeline (Background)
All background jobs run via `job_runner.py`, which wraps any command with logging, locking, and timeout. The crontab at `crontab.txt` is the canonical job schedule.

**Data flow for new blotter content:**
1. **Email** (`email_worker.py`) fetches IMAP attachments → queues PDFs
2. **PDF parser** (`core/pdf_parser.py` or county-specific parsers in `services/blotter/`) extracts records
3. **Processor** (`processor.py` / `services/blotter/processor.py`) normalizes and inserts into `records` + `blotters` tables
4. **Summarizer** (`services/summarizer/engine.py`) generates AI post drafts with historical context
5. **Auditor** (`services/blotter/auditor.py`) scans for PII, rewrites public summary, generates SEO metadata

**Scrapers and integrations:**
- Web scrapers (Missoula, Bozeman, CrimeMapping, etc.) live in `services/ingestion/fetchers/`
- Jail booking scrapers in `services/ingestion/jail_bookings.py` with `--county` flag per county
- Source discovery/onboarding in `services/ingestion/source_scout.py`, `source_reviewer.py`, `source_onboarder.py`
- Civil filings ingest via `services/ingestion/civil_filings.py`
- Code violations via `services/ingestion/code_violations.py`
- Court case outcome scraper: `services/court/outcome_scraper.py`

### Services Layer
`services/` is organized by domain:

**Core data ingestion:**
- `services/blotter/` — PDF parsing (`parser.py`), record processing (`processor.py`), PII auditing (`auditor.py`), analytics
- `services/ingestion/` — web scraper orchestration, jail booking integrations, source discovery/onboarding, civil filings, code violations
- `services/ingestion/fetchers/` — county-specific scrapers (Bozeman, Missoula, CrimeMapping, etc.)
- `services/court/` — court tracker schema, case/hearing/filing ingestion, outcome scraper, source adapters
- `services/persons/` — missing persons tracking (`missing.py`), sex offender import/delta, FWP violation scraping

**Publishing & alerts:**
- `services/publishing/` — autonomous newsroom agents (planner → writer → editor), morning briefing, daily arrests digest
- `services/summarizer/` — Claude-powered post generation with historical context
- `services/alerts/` — email digests, bail bonds alerts, incident notifications, missing person watches, ingestion health alerts

**Monetization & operations:**
- `services/monetization/` — recovery center ads, bail ad orders/creatives/slots, bondsman command center, paywall preview tracking
- `services/agents/` — mission control, daily planner, event bus, heartbeat, OpenClaw fleet management
- `services/meetings/` — public meeting tracking and alerts
- `services/geo/` — geocoding and mapping
- `services/datasets/` — aggregate data export
- `services/ops/` — operational utilities
- `services/email/` — email delivery service
- `services/api/` — REST API auth and request handlers
- `services/admin/` — admin-only tools (SQLite agent, charge explainer, case journeys)

### Database Schema (SQLite)
**Key tables** (selected from 80+ total):
- `records` — Police blotter incidents (CFS#, date, time, location, incident type)
- `blotters` — PDF/text blotter batches with ingestion metadata
- `posts` — Blotter digest posts (summary, SEO title/slug/description)
- `jail_bookings` — Current booking records with charges and release info
- `court_cases` — Court case tracker with criminal outcome fields (added 2026-05-29)
  - **New criminal outcome columns:** defendant_name, is_criminal, charges_text, charges_json, plea, disposition, sentence_text, sentence_date, sentencing_judge, outcome_scraped_at
- `court_events` — Hearings, trials, appeals
- `court_filings` — Motion filings
- `sex_offenders` — Registry with address, conviction history, photo URL
- `missing_persons` — Missing persons alerts with photo, last seen location
- `civil_filings` — Civil case records
- `code_violations` — Property code violations
- `blog_posts` — Published news articles
- `story_candidates` — Content candidates awaiting review
- `subscribers` — Email digest subscribers by county preference
- `donations` — Payment records (Stripe)
- `bail_ad_orders` — Recovery center advertising orders
- `page_views`, `pattern_clicks`, `subscribe_events`, `donation_events` — Analytics

**Migrations:**
All schema changes go in `init_db.migrate()` in `init_db.py`. Pattern:
```python
for col, col_type in [('new_column', 'TEXT'), ...]:
    try:
        conn.execute(f'ALTER TABLE some_table ADD COLUMN {col} {col_type}')
    except sqlite3.OperationalError:
        pass  # already exists
```
`migrate()` is called automatically at app startup. Never use destructive migrations.

**New migration (2026-05-29):** Court cases now track criminal outcomes. Run `python3 init_db.py` to apply.

### AI / Claude Integration
`config.py` holds `ANTHROPIC_API_KEY`. The active model is `claude-sonnet-4-6`. Claude is called from:

**Content pipeline:**
- `services/blotter/auditor.py` — PII detection (SSNs, addresses, DOBs, DL#s, phone), public summary rewrite, SEO metadata generation (title/slug/description), tone validation
- `services/summarizer/engine.py` — Post generation with historical context (prior incidents, trends)
- `services/summarizer/historical_context.py` — Context builder for related cases

**Newsroom agents:**
- `services/publishing/news_planner.py` — Story candidate scoring and ingestion
- `services/publishing/news_writer.py` — Draft article generation from candidate facts
- `services/publishing/news_editor.py` — Final review, sensitivity check, publication approval

**Admin tools:**
- `services/admin/sqlite_agent.py` — Natural language SQL querying (admin only)
- `services/admin/charge_explainer.py` — Charge/code definition explanation
- `services/agents/mission_control.py` — OpenClaw agent orchestration

**Graceful degradation:** If Claude API is unavailable, `services/blotter/auditor.py` falls back to regex-only mode. Newsroom agents queue tasks for retry.

### Autonomous Agents
**OpenClaw fleet** (deprecated, being replaced):
- Three agents (reporter → clerk → publisher) ran via `openclaw_launcher.py --once`
- Used OpenClaw MCP framework with `agent-queue/` communication
- Daily task queue generated at 6:45am MT

**Current newsroom pipeline:**
- `services/agents/daily_planner.py` — Task queue builder (6:45am MT), story prioritization
- `services/agents/mission_control.py` — Agent orchestration and mission tracking
- `services/agents/events_bus.py` — Event publishing for agent progress
- `services/agents/status.py` — Agent health/status dashboard
- `services/agents/heartbeat.py` — Periodic agent health monitoring
- `services/agents/kanban_push.py` — Kanban board sync for task tracking

**Publishing pipeline:**
- `services/publishing/morning_briefing.py` — 7am daily digest of new incidents
- `services/publishing/daily_arrests_blotter.py` — "What's Happening in Montana" post (7:10am)
- `services/publishing/weekly_digest.py` — Weekly roundup
- `services/publishing/weekly_top_calls.py` — Top incidents by county

### Log Files
All worker logs are in `logs/` (not the project root). Named by job: `logs/mail.log`, `logs/jail_booking_ingest.log`, `logs/gunicorn.log`, etc. The `script_watchdog.py` checks freshness of these files.

## Key Conventions

**Templates & Frontend:**
- Jinja2 in `templates/`. Public pages extend `public_page_base.html`; admin pages extend `admin.html` or a sub-base.
- `static/public-redesign.css` is the main stylesheet. JS is mostly vanilla.
- `static/AdSimulator.jsx` is the only React component (bail ad preview).

**Data normalization:**
- **County slugs:** Lowercase hyphenated strings (e.g. `cascade`, `lewis-and-clark`, `gal-gal`). Use `_slugify()` helper or `services.court.tracker._slugify()` to normalize.
- **Agency names:** Normalized via `core/agency_normalization.py`. Always call `normalize_agency_identity()` when storing/matching.
- **PII redaction:** `services.blotter.auditor.get_pii_spans()` detects PII; `_redact_public_pii()` masks it for public display.

**Database:**
- All secrets in `.env` (loaded via `config.py`). Never hardcode credentials.
- Raw SQL via `get_db()` is standard. SQLAlchemy used sparingly.
- JSON columns use `json.dumps()` / `json.loads()`. Always validate structure before processing.

**Code & Git:**
- **Commit style:** Conventional Commits — `feat:`, `fix(scope):`, `chore:`, `refactor:`.
- **Error handling:** Log all exceptions to `logs/` directory. Use `logging.getLogger(__name__)` for module-level logger.
- **Security:** PII scanning before publication (blotter_auditor). No real phone/SSN/address in test data.

## Testing Pattern

Tests use a temporary SQLite DB, not the production `blotter.db`. Fixtures are in `tests/fixtures/`. Each test module patches `config.DB_PATH`, `init_db.DB_PATH`, and `app_module.config.DB_PATH` to point at the temp file, then calls `init_db.init_database()` and `init_db.migrate()` in `setUp`.

Run targeted tests before the full suite when iterating on a specific area.
