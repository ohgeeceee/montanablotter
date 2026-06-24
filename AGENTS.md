# Montana Blotter — Agent Guide

This file is the primary orientation document for AI coding agents working in the Montana Blotter repository. It describes the project's purpose, architecture, development workflow, testing strategy, and operational conventions. Read this first before making code changes.

---

## Project Overview

Montana Blotter (`montanablotter.com`, alias `fertherecerd.com`) is an open-source public-records aggregation platform for Montana. It ingests, normalizes, and publishes police blotters, jail bookings, court records, missing-persons alerts, warrants, public meetings, and related government transparency data from all 56 Montana counties.

The production deployment runs on a single VPS at `/root/montanablotter`. The repository is a Python Flask monolith with a large ecosystem of background workers, scrapers, AI-assisted content pipelines, a React Native mobile app, and a family of related services (`blotter-host`, `claw3d-office`) that share authentication and operational infrastructure.

### High-level responsibilities

- **Ingestion**: poll email inboxes, scrape sheriff/police websites, fetch APIs, and parse PDFs into a normalized SQLite database.
- **Publishing**: render public web pages, run a daily blog/newsroom pipeline, send email digests, and post to social platforms.
- **Administration**: provide dashboards for manual upload, user management, analytics, sponsor ads, source onboarding, and system health.
- **Privacy/quality**: scan records for PII, rewrite public summaries, and gate sensitive content behind subscriptions or redaction.
- **Operations**: keep cron-scheduled workers healthy via watchdogs, systemd services, and automated backups.

---

## Technology Stack

| Layer | Technology |
|-------|------------|
| Backend framework | Python 3.7+ Flask (WSGI entry: `app:app`) |
| Database | SQLite primary (`blotter.db`), optional Turso/libsql remote sync |
| ORM / DB access | SQLAlchemy models in `db.py`; most code uses raw `sqlite3` via `get_db()` |
| Queue / workers | Redis + RQ (`rq`), plus cron-driven `job_runner.py` wrappers |
| PDF parsing | pdfplumber, pypdfium2, pdf2image, pytesseract, easyocr |
| Web scraping | requests, httpx, Playwright, BeautifulSoup-style parsing |
| AI/LLM | Anthropic Claude (`claude-sonnet-4-6`), optional OpenAI/DeepSeek |
| Authentication | Flask-Login + bcrypt; JWT `blotter_ops_session` for cross-service admin access |
| Web server | Nginx → Gunicorn (3 workers, port 5000) → `app.py` |
| Mobile app | React Native with Expo (Node 22, TypeScript) |
| Secret scanning | gitleaks (`.gitleaks.toml` extends defaults) |
| Testing | pytest + unittest-style `TestCase` classes |

---

## Repository Structure

```
/root/montanablotter/
├── app.py                      # Flask application entry point (~15.7k lines, monolithic)
├── config.py                   # Environment-based configuration; loads `.env`
├── db.py                       # SQLAlchemy models + raw SQLite helpers
├── init_db.py                  # Schema creation and non-destructive migrations
├── requirements.txt            # Python dependencies
├── .env.example                # Documented environment variable template
├── .gitleaks.toml              # Secret scanning config
├── crontab.txt                 # Canonical cron schedule for workers
│
├── blueprints/                 # Flask route modules
│   ├── api.py                  # REST API with token auth
│   ├── auth.py                 # Login/logout/MFA
│   ├── public.py               # Public-facing routes
│   ├── payments.py             # Stripe donations/subscriptions
│   ├── alerts.py               # Alert subscription routes
│   ├── admin/                  # Admin dashboard sub-blueprints
│   ├── detention.py            # Jail bookings / arrestees
│   ├── code_violations.py      # Code violation records
│   ├── license_sanctions.py    # License/sanction records
│   ├── sex_offender.py         # Sex offender registry tracking
│   ├── public_salaries.py      # Public salary records
│   ├── government_spending.py  # Government spending data
│   ├── watchdog.py             # Agency oversight tools
│   └── recovery_ads.py         # Recovery-center advertising
│
├── services/                   # Domain logic organized by subsystem
│   ├── blotter/                # PDF parsing, processing, PII auditing, analytics
│   ├── ingestion/              # Web scrapers, jail bookings, source discovery/onboarding
│   │   └── fetchers/           # County/department-specific fetchers
│   ├── court/                  # Court tracker, case/hearing/filing ingestion
│   ├── persons/                # Missing persons, sex offender imports, FWP violations
│   ├── publishing/             # Newsroom agents, morning briefing, digests
│   ├── summarizer/             # Claude-powered post generation
│   ├── alerts/                 # Email digests, bail bonds, incident notifications
│   ├── monetization/           # Paywall, ads, bail bonds command center
│   ├── agents/                 # Mission control, daily planner, event bus, heartbeat
│   ├── meetings/               # Public meeting tracking and alerts
│   ├── geo/                    # Geocoding and mapping
│   ├── datasets/               # Aggregate data exports
│   ├── admin/                  # Admin-only tools (SQLite agent, charge explainer)
│   ├── api/                    # REST API auth and handlers
│   ├── email/                  # Email delivery service
│   └── ops/                    # Operational utilities
│
├── core/                       # Shared helpers (agency normalization, etc.)
├── scrapers/                   # Legacy / standalone scraper scripts
├── ingestion/                  # Orchestration scripts (e.g. `run_all_scrapers.py`)
├── agendas_scraper/            # Public-meetings agenda scraping
├── meeting_pdf_pipeline/       # Meeting document OCR/extraction
├── crime-extractor/            # Standalone crime-data extraction tool (submodule)
│
├── templates/                  # Jinja2 HTML templates
├── static/                     # CSS, JS, images, fonts, county maps
├── mobile/                     # Expo/React Native client
│
├── tests/                      # pytest suite (100+ test modules)
│   └── fixtures/               # Test fixtures
├── scripts/
│   ├── ops/                    # Health checks, cron helpers, Hermes context scripts
│   ├── maintenance/            # One-off maintenance scripts
│   └── setup/                  # Setup/migration scripts
├── ops/systemd/                # systemd unit files
├── configs/                    # Configuration files for agendas, 3D hub, etc.
├── data/                       # Runtime data files, SQLite DB, civil filings
├── logs/                       # Worker logs (canonical output location)
├── records/                    # Processed record files
├── uploads/                    # Incoming PDFs / attachments
├── backups/                    # Database/config backups
├── agent-queue/                # Draft actions for AI agent fleet (ops/ingest/dev/civic)
└── docs/                       # Architecture docs, runbooks, plans
```

### Related repositories / services in the same VPS

The production VPS hosts a coordinated set of services that share admin authentication:

| Path | Service | Tech |
|------|---------|------|
| `/root/montanablotter` | Montana Blotter flagship | Python Flask + SQLite |
| `/root/blotter-host` | Multi-tenant `blotter.host` router | Node.js / Express |
| `/root/claw3d-office` | Claw3D Office / 3D hub | Separate service |

Shared admin login is implemented via cookies:
- `blotter_ops_session` — JWT signed by `OPS_SESSION_SECRET` (shared between Montana Blotter and Blotter Host).
- `studio_access` — cookie matching `STUDIO_ACCESS_TOKEN` (shared with Claw3D Office).

---

## Build, Test, and Development Commands

All backend work assumes the virtualenv at `/root/montanablotter/venv`.

```bash
# Activate the environment
source /root/montanablotter/venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# edit .env with production or development values

# Initialize / migrate database
python3 init_db.py

# Run the development server
python3 app.py

# Seed an admin user
python3 seed_admin.py <username>
```

### Testing

```bash
# Run the full pytest suite
./venv/bin/python3 -m pytest

# Run a focused test file
./venv/bin/python3 -m pytest tests/test_public_api.py

# Run a single test class or method
./venv/bin/python3 -m pytest tests/test_blotter_auditor.py::TestAuditor::test_redaction
```

Tests use a temporary SQLite database, not production `blotter.db`. `tests/conftest.py` disables the sign-in wall by default so public pages can be tested anonymously.

### Mobile app

```bash
cd mobile
npm install
npm run start        # Expo development server
npm run android      # Build/run Android
npm run ios          # Build/run iOS
npm run web          # Web preview
npm run typecheck    # TypeScript check
npm run ci:verify    # TypeScript + Expo config validation
```

### Local quality checks

```bash
# Secret scan (matches CI)
gitleaks git --config .gitleaks.toml --redact

# Health check (services, sockets, cron freshness)
./venv/bin/python3 script_watchdog.py
./venv/bin/python3 script_watchdog.py --json

# Process a PDF manually
python3 processor.py uploads/example.pdf CountyName

# Test the PDF parser
python3 pdf_parser.py uploads/example.pdf
python3 pdf_parser.py uploads/example.pdf --county Gallatin
python3 pdf_parser.py uploads/example.pdf --ocr

# Run email worker manually
python3 email_worker.py
```

---

## Code Style Guidelines

### Python

- **Indentation**: 4 spaces.
- **Naming**: `snake_case` for functions/variables/modules, `PascalCase` for classes, `UPPER_SNAKE_CASE` for module-level constants.
- **Line length**: follow PEP 8-ish conventions; no strict formatter is enforced.
- **Imports**: group standard library, third-party, and local imports.
- **Error handling**: log exceptions to `logs/` using `logging.getLogger(__name__)`; avoid silent failures in background workers.
- **Database access**: raw SQL via `get_db()` (returns `sqlite3.Connection` with `row_factory = sqlite3.Row`) is the standard pattern. SQLAlchemy is used sparingly for specific features.
- **JSON columns**: always `json.dumps()` / `json.loads()` and validate structure before processing.
- **County slugs**: lowercase hyphenated strings (e.g. `cascade`, `lewis-and-clark`, `gal-gal`). Use `_slugify()` helpers.
- **Agency names**: normalize via `core.agency_normalization.normalize_agency_identity()` before storing/matching.
- **PII**: use `services.blotter.auditor.get_pii_spans()` to detect PII and `_redact_public_pii()` to mask it for public display.

### Tests

- Use `unittest.TestCase` under pytest.
- Name test files `tests/test_*.py` and methods `test_*`.
- Patch `config.DB_PATH`, `init_db.DB_PATH`, and the relevant `app_module.config.DB_PATH` to a temporary DB, then call `init_db.init_database()` and `init_db.migrate()` in `setUp`.
- Add regression coverage for bug fixes and include at least one edge-case assertion.
- Never use production `blotter.db` or real PII in tests.

### JavaScript / TypeScript (mobile)

- 2-space indentation.
- React components in `PascalCase`.
- Variables/functions in `camelCase`.
- Run `npm run typecheck` before pushing mobile changes.

### Templates / frontend

- Jinja2 templates in `templates/`.
- Public pages extend `templates/public_page_base.html`; admin pages extend `templates/admin.html` or a sub-base.
- Main stylesheet: `static/public-redesign.css`.
- Most JS is vanilla; `static/AdSimulator.jsx` is the only React browser component.

### Commit messages

Follow Conventional Commits:

```
feat: add Flathead warrant parser
fix(pdf_parser): handle missing CFS numbers in Helena format
docs: update source coverage table
refactor(services): move charge explainer to services/admin
chore: bump gitleaks config
test: add coverage for bozeman calls fetcher
```

---

## Testing Instructions

1. **Activate the virtualenv**: `source /root/montanablotter/venv/bin/activate`.
2. **Run targeted tests first** when iterating on a specific area, then run the full suite for broad changes.
3. **New code requires tests** for:
   - New parsers or fetchers
   - Database migrations in `init_db.py`
   - New blueprints or API endpoints
   - Source adapters and ingestion workflows
   - Paywall / monetization logic
   - PII redaction / privacy behavior
4. **Mobile changes** must pass `npm run ci:verify`.
5. **Before committing sensitive changes**, run `gitleaks git --config .gitleaks.toml --redact`.

---

## Security Considerations

### Secrets and credentials

- All secrets live in `.env`. Never hardcode credentials, API keys, passwords, or tokens in source files.
- Keep `.env` permissions restrictive: `chmod 600 .env`.
- `.env.example` documents every environment variable without real values. Update it when adding new config.
- The `.gitleaks.toml` config extends gitleaks defaults. CI runs a secret scan on every push/PR to `main`.
- Never commit runtime artifacts: logs, database files (`blotter.db`), backups, or `.secrets/` contents.

### Authentication and authorization

- Flask sessions use `MB_SECRET_KEY`.
- Admin login has brute-force throttling controlled by `MB_ADMIN_LOGIN_MAX_ATTEMPTS`, `MB_ADMIN_LOGIN_WINDOW_MINUTES`, and `MB_ADMIN_LOGIN_LOCKOUT_MINUTES`.
- API keys are managed via `MB_API_ADMIN_SECRET` for `/api/auth/*` endpoints.
- The global sign-in wall (`MB_REQUIRE_SIGNIN=true`) requires a free account for most pages except the homepage and a small allow-list of auth/payment/static routes.
- Warrant access is a separate paid add-on (`warrant_access` plan); `insider` and `professional` plans do **not** include it. Use `user_has_warrant_access()` to gate `/wanted` routes.

### Privacy

- Run PII detection (`services/blotter/auditor.py`) before publishing records.
- Automatic suppression of victim names in domestic violence and sexual assault cases.
- Public summaries are rewritten to omit sensitive details.
- Cross-state name search in `blotter-host` is gated until the PII auditor gives explicit clearance.

### Infrastructure hardening

- Nginx terminates TLS with Let's Encrypt.
- Security headers are configurable via `MB_CONTENT_SECURITY_POLICY`, `MB_REFERRER_POLICY`, `MB_X_FRAME_OPTIONS`, etc.
- systemd unit `agent-events.service` runs with hardening flags (`NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=full`, etc.).

---

## Deployment and Operations

### Production services

```bash
systemctl restart montanablotter   # Flask app via Gunicorn
systemctl restart blotter-host     # Multi-tenant Node router
systemctl restart claw3d-office    # 3D office service
systemctl restart agent-events     # WebSocket events service
systemctl reload nginx             # Reverse proxy
```

### Cron schedule

The canonical schedule is in `crontab.txt`. Key jobs include:

- Email inbox polling every 15 minutes (`email_worker.py`).
- Image-based blotter ingestion (`email_image_blotter.py`).
- Jail roster ingestion every 2–4 hours via `services/ingestion/jail_bookings.py`.
- Court refresh every 6 hours (`services/court/refresh`).
- Public meetings ingestion every 6 hours (`services/meetings/agendas_ingest`).
- Daily blog worker at 05:30 (`daily_blog_worker.py`).
- Morning briefing email at 13:00 UTC / 7:00am MT.
- Newsroom planner/writer/editor every 3 hours.
- Database backup daily at 03:00 (`scripts/ops/backup_db.sh`).
- Healthcheck restart every 3 minutes (`scripts/ops/healthcheck_restart.sh`).

Apply the schedule with:

```bash
crontab crontab.txt
```

### Database migrations

All schema changes go in `init_db.migrate()`. Use non-destructive `ALTER TABLE ADD COLUMN` patterns:

```python
for col, col_type in [('new_column', 'TEXT'), ...]:
    try:
        conn.execute(f'ALTER TABLE some_table ADD COLUMN {col} {col_type}')
    except sqlite3.OperationalError:
        pass  # already exists
```

`migrate()` runs automatically at application startup. Never use destructive migrations on production.

### Backup and recovery

- Daily DB backup at 03:00 via `scripts/ops/backup_db.sh`.
- Keep a rolling 7-day backup chain.
- Manual snapshot: copy `blotter.db` to a `.bak` file.

### Health monitoring

- `script_watchdog.py` checks systemd units, Gunicorn socket, Redis, and cron log freshness.
- `services/agents/heartbeat.py` and related modules monitor agent health.
- Worker logs are written to `logs/` (e.g. `logs/mail.log`, `logs/jail_booking_ingest.log`).

---

## VPS Operations Dashboard

The VPS is the single operations dashboard for `montanablotter.com` and `blotter.host`.

### Entry points

| URL | Surface |
|-----|---------|
| `https://www.montanablotter.com/admin/` | Unified hub (post-login landing) |
| `https://www.montanablotter.com/admin/dashboard` | Legacy Montana Blotter ops dashboard |
| `https://www.montanablotter.com/admin/fleet/` | Blotter Host multi-tenant control panel |
| `https://www.montanablotter.com/admin/office/3d` | 3D ClawHub (Claw3D Office) |
| `https://www.montanablotter.com/hermes/` | Hermes agent workspace |
| `https://www.montanablotter.com/admin/3dhub/status` | 3D print fleet status |

### Shared authentication

One login at `/admin/login` opens every surface. Secrets live in:

- `/root/montanablotter/.env` — `OPS_SESSION_SECRET`, `STUDIO_ACCESS_TOKEN`
- `/root/blotter-host/.env` — `OPS_SESSION_SECRET`
- `/root/claw3d-office/.env` — `STUDIO_ACCESS_TOKEN`

### Agent registry

`/admin/api/agents/registry` returns a unified list of agents from OpenClaw, Montana Blotter, and Hermes profiles. Claw3D proxies the same list at `/api/office/agents/registry`.

---

## Agent-Specific Guidance

When working on a specific subsystem, prefer these ownership boundaries:

| Role | Owns |
|------|------|
| Backend/API | Flask routes, blueprints, `db.connect_db()` usage, `init_db.migrate()` changes, API auth |
| Ingestion | Fetchers in `services/ingestion/fetchers/`, parsers, deduplication, cron entries, watchdog health |
| Content/Privacy | `services/summarizer/`, `services/blotter/auditor.py`, PII checks, SEO metadata, tone safeguards |
| UI | Jinja templates, `static/` CSS/JS, public/admin flows, visual screenshots |
| Mobile | `mobile/src`, API contracts, Expo config, TypeScript validation |
| QA | Regression tests, smoke checks, fixture updates for changed behavior |
| Ops/Security | `.env.example`, `.gitleaks.toml`, service/cron docs, secret hygiene, backups |

### Autonomous-agent boundaries

`MISSION.md` defines a tiered authority model for the agent fleet:

- **Green** — read-only actions: inspect logs, query DB, compute metrics, draft documents.
- **Yellow** — safe recoveries: restart silent workers, re-queue stuck PDFs (after backup), rotate oversized logs, snapshot the DB.
- **Red** — requires explicit human approval: code changes, `git pull`, deploys, `systemctl restart`, DB writes outside the ingest pipeline, outbound email/posts, dependency installs, firewall/nginx changes, deleting PDFs/backups.

Draft all Red-tier proposals in `agent-queue/<profile>/<timestamp>-<short-name>/` with a summary, proposed diff/command, reasoning, and rollback plan.

---

## Useful Reference Commands

```bash
# Query production database (read-only preferred)
sqlite3 blotter.db "SELECT COUNT(*) FROM records;"
sqlite3 blotter.db "SELECT * FROM blotters ORDER BY upload_date DESC LIMIT 5;"

# Check queue depth
python3 -c "from rq import Queue; from redis import Redis; q = Queue(connection=Redis()); print(q.count)"

# Tail live logs
journalctl -u montanablotter -f
tail -f /root/montanablotter/logs/mail.log
tail -f /root/montanablotter/cron_errors.log

# Check services
systemctl status montanablotter.service
systemctl status agent-events.service
systemctl status redis-server
```

---

*Last updated: 2026-06-17. Keep this file in sync with changes to `.env.example`, `crontab.txt`, `requirements.txt`, service architecture, or security practices.*
