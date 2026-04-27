# Montana Blotter - Montana Public Records Initiative

A free, open-source platform for aggregating and publishing Montana's public police blotters, jail bookings, court records, and missing persons alerts from all 56 counties.

**Live Site**: [montanablotter.com](https://montanablotter.com)  
**Alias**: fertherecerd.com

## 🎯 Project Overview

Montana Blotter is the tech-minimalist flagship of the Montana Public Records Initiative. It provides centralized access to:

- **Police Blotters** — "For the Record" incident summaries from sheriff offices
- **Jail Bookings** — Real-time arrest and detention records
- **Court Records** — Court dockets, hearings, and case tracking
- **Missing Persons** — Active alerts and directory with photo profiles
- **Warrant Lookup** — County-level warrant searches
- **Public Meetings** — Government meeting agendas and minutes

## 🏗️ System Architecture

### Technology Stack
- **Backend**: Python Flask + SQLAlchemy
- **Database**: SQLite (local) / Turso (remote sync)
- **PDF Processing**: pdfplumber + OCR fallback (pytesseract)
- **Queue System**: Redis + RQ (Redis Queue)
- **Authentication**: Flask-Login + Bcrypt
- **Web Server**: Nginx + Gunicorn
- **Email**: IMAP/SMTP (IONOS)
- **Mobile**: React Native (Expo)

### Core Components

1. **PDF Parser** (`pdf_parser.py`)
   - Multi-county format support: GCSO, Helena, Whitefish, Havre, Jefferson (JeffCo)
   - CFS number extraction, date normalization, location parsing
   - OCR fallback for image-based PDFs
   - County auto-detection with regex patterns

2. **Ingestion Pipeline** (`processor.py`, `email_worker.py`)
   - Email IMAP fetching (every 15 minutes via cron)
   - Automated PDF attachment extraction
   - Queue-based processing with Redis
   - Pipeline state tracking and retry logic

3. **Data Fetchers**
   - `crimemapping_fetcher.py` — CrimeMapping.com API (Billings, Great Falls)
   - `missoula_public_report_fetcher.py` — Missoula PD daily reports
   - `whitefish_blotter_fetcher.py` — Whitefish PD incident logs
   - `bozeman_police_fetcher.py` — Bozeman calls for service & crime reports
   - `jail_booking_ingest.py` — Multi-county jail roster scraping

4. **Flask Application** (`app.py` + blueprints)
   - Public pages: blotter browser, county/city pages, trends
   - Admin dashboard: manual upload, user management, analytics
   - Missing persons directory with profile pages
   - Blog/CMS for editorial content
   - SEO-optimized with OpenGraph, structured data

5. **AI/ML Layer**
   - `summarizer.py` — Incident summarization and entity extraction
   - `blotter_auditor.py` — Privacy/PII scanning and redaction
   - `news_writer_agent.py` — Automated editorial content generation
   - `charge_explainer_worker.py` — Legal charge explanation pages

6. **Database** (`init_db.py`, `db.py`)
   - SQLite with libsql/Turso sync for remote replication
   - Tables: users, blotters, records, posts, jail_bookings, missing_persons, courts, warrants, sponsors

## 📊 Database Schema

```
users              — Authentication and membership
blotters           — PDF batch tracking with county and source_type
records            — Individual incidents (CFS, location, type, details)
posts              — Public-facing editorial content with SEO metadata
jail_bookings      — Arrest records with mugshots and charges
missing_persons    — Active alerts with photos, physical descriptions, last_seen
courts             — Court dockets, hearings, and case tracking
warrants           — Warrant listings by county
sponsors           — Advertiser/sponsor management with geo-targeting
source_documents   — Raw ingestion logs with content hashes
pipeline_state     — Ingestion job tracking and retry counters
```

## 🚀 Installation & Setup

### Prerequisites
- Python 3.7+
- pip3
- Nginx
- Root access to VPS

### Quick Install

```bash
# 1. Upload files to your VPS
cd /root/montanablotter
# Upload all .py files

# 2. Install dependencies
pip3 install -r requirements.txt

# 3. Run automated setup
python3 setup.py

# 4. Configure your credentials
cp .env.example .env
nano .env
# Set MB_SECRET_KEY, MB_EMAIL_PASSWORD, MB_SMTP_PASSWORD, API keys

# 5. Test the system
python3 pdf_parser.py uploads/your_file.pdf
python3 app.py
```

### Manual Setup

See `DEPLOYMENT_GUIDE.py` for detailed step-by-step instructions.

## 🔧 Configuration

Edit environment variables (recommended via `.env`) to customize:

```bash
# Core Application
MB_SECRET_KEY=change-me-in-production
MB_BASE_URL=https://montanablotter.com
MB_DEBUG=false

# Email (IONOS)
MB_EMAIL_USER=records@montanablotter.com
MB_EMAIL_PASSWORD=***
MB_SMTP_PASSWORD=***

# Admin & Alerts
MB_ADMIN_ALERT_EMAILS=you@example.com,ops@example.com
MB_INGEST_ALERT_REPEAT_HOURS=24
MB_ADMIN_BOOTSTRAP_PASSWORD=***

# Security Headers
MB_CONTENT_SECURITY_POLICY=default-src 'self'; ...
MB_REFERRER_POLICY=strict-origin-when-cross-origin

# Login Throttling
MB_ADMIN_LOGIN_MAX_ATTEMPTS=5
MB_ADMIN_LOGIN_WINDOW_SECONDS=300
MB_ADMIN_LOGIN_LOCKOUT_SECONDS=3600

# Turso/libsql (Remote DB Sync)
MB_TURSO_URL=libsql://your-db.turso.io
MB_TURSO_AUTH_TOKEN=***

# Redis (Queue System)
MB_REDIS_URL=redis://localhost:6379/0

# AI/ML Services
MB_OPENAI_API_KEY=***
MB_ANTHROPIC_API_KEY=***
MB_DEEPSEEK_API_KEY=***

# Sponsor/Ads
MB_SPONSOR_ENABLED=true
```

### Admin Ingest Alerts

- `MB_ADMIN_ALERT_EMAILS` sends stale/failing source alerts to one or more admin inboxes.
- `MB_INGEST_ALERT_REPEAT_HOURS` controls reminder frequency for unresolved alerts. Default: `24`.

### Database Sync

The app supports local SQLite with optional Turso (libsql) remote sync:

- Set `MB_TURSO_URL` and `MB_TURSO_AUTH_TOKEN` to enable remote replication
- Local SQLite remains the primary database for performance
- Sync is triggered on write operations when Turso is configured

## 🔐 Secret Scanning

GitHub Actions runs a `Secret Scan` workflow on pull requests, pushes to `main`, and manual dispatches. It uses `gitleaks` with the repo config in `.gitleaks.toml` to scan git history for accidentally committed credentials before they ship.

If you have `gitleaks` installed locally, run:

```bash
gitleaks git --config .gitleaks.toml --redact
```

## 📧 Email Processing

The system automatically fetches PDFs from your email:

1. Sheriff offices send blotters to your email
2. Email worker (cron job) checks inbox every 15 minutes
3. PDFs are extracted and saved
4. Processor parses and inserts into database
5. Processed emails moved to "Processed" folder

### Setting up Email Automation

```bash
# Add cron job
crontab -e

# Add this line (runs every 15 minutes)
*/15 * * * * cd /root/montanablotter && /usr/bin/python3 email_worker.py >> /root/montanablotter/cron.log 2>&1
```

## ✅ Operations Checks

Use the watchdog to verify the app and scheduled jobs are still healthy.

```bash
cd /root/montanablotter
./venv/bin/python3 script_watchdog.py
./venv/bin/python3 script_watchdog.py --json
```

What it checks:

- `montanablotter.service` is active and running under systemd
- `agent-events.service` is active (WebSocket events service)
- the Gunicorn unix socket at `/tmp/montanablotter.sock` responds with HTTP
- all scheduled jobs have written to their expected log files within the allowed freshness window
- Redis server is running and responsive

Exit codes:

- `0` = all checks passed
- `1` = one or more services or jobs are missing, stale, or failing

The watchdog is also scheduled in [crontab.txt](/root/montanablotter/crontab.txt) to run daily and append output to `cron_errors.log`.

## 🤖 Hermes Workflows

Hermes is configured as an AI operations layer on top of existing cron jobs.

Setup:

```bash
cd /root/montanablotter
./scripts/ops/setup_hermes_workflows.sh
```

Manual context scripts (for quick debugging):

```bash
./scripts/ops/hermes_context_health.py
./scripts/ops/hermes_context_ingestion.py
./scripts/ops/hermes_context_growth.py
```

Useful Hermes commands:

```bash
/root/.local/bin/hermes cron list
/root/.local/bin/hermes cron status
/root/.local/bin/hermes gateway status
```

## 📱 Mobile App

A React Native mobile app is available in the `mobile/` directory, built with Expo.

### Features
- Browse recent blotters by county
- Search incidents
- Missing persons alerts
- Push notifications (planned)
- Offline mode (planned)

### Development

```bash
cd mobile
npm install
npx expo start
```

## 🎨 Dashboard Features

### Public Pages
- **Homepage**: Live incident feed, search, county directory, trending stories
- **Blotter Browser**: Filter by county, city, date range, incident type
- **County Pages**: County-specific blotter feeds with SEO metadata
- **City Pages**: City-specific feeds (e.g., Bozeman, Missoula, Helena)
- **Missing Persons**: Active alerts directory with photo profiles
- **Warrant Lookup**: County-level warrant search interface
- **Court Records**: Docket browser and hearing calendar
- **Jail Bookings**: Recent arrest gallery with charge details
- **Blog/Editorial**: AI-assisted public-interest journalism

### Admin Dashboard
- **Manual Upload**: Drag-and-drop PDF processing
- **User Management**: Role-based access control
- **Analytics**: Traffic, engagement, and content performance
- **Sponsor Management**: Geo-targeted ad placement
- **Content Moderation**: AI-assisted PII redaction review
- **System Health**: Pipeline status, queue depth, error logs

## 📝 Usage Examples

### Process a PDF Manually
```bash
python3 processor.py uploads/gallatin_blotter.pdf Gallatin
```

### Test PDF Parser
```bash
python3 pdf_parser.py uploads/your_file.pdf
```

### Run Email Worker
```bash
python3 email_worker.py
```

### Fetch Web Sources
```bash
# CrimeMapping (Billings/Great Falls)
python3 crimemapping_fetcher.py

# Missoula PD reports
python3 missoula_public_report_fetcher.py

# Whitefish blotter
python3 whitefish_blotter_fetcher.py

# Bozeman calls for service
python3 bozeman_police_fetcher.py
```

### Ingest Jail Bookings
```bash
python3 jail_booking_ingest.py
```

### AI Content Generation
```bash
# Summarize recent incidents
python3 summarizer.py

# Generate editorial content
python3 news_writer_agent.py

# Create charge explanation pages
python3 charge_explainer_worker.py
```

### Create Admin User
```bash
MB_ADMIN_BOOTSTRAP_PASSWORD='strong...word' python3 seed_admin.py myusername
# or run interactively:
python3 seed_admin.py myusername
```

### Query Database
```bash
sqlite3 blotter.db "SELECT COUNT(*) FROM records;"
sqlite3 blotter.db "SELECT * FROM blotters ORDER BY upload_date DESC LIMIT 5;"
sqlite3 blotter.db "SELECT * FROM jail_bookings ORDER BY booking_date DESC LIMIT 10;"
```

## 🔍 Data Source Support

### PDF Formats (Sheriff Offices)
| County | Format | Status | Method |
|--------|--------|--------|--------|
| Gallatin | GCSO | Active | Email (PDF) |
| Helena/Lewis & Clark | Helena | Active | Email (PDF) |
| Jefferson | JeffCo | Active | Email (PDF) |
| Whitefish | Whitefish | Active | Web scrape |
| Havre | Havre | Active | Web scrape |
| Flathead | — | Planned | Email (PDF) |
| Yellowstone | — | Planned | Email (PDF) |

### Web/API Sources (Police Departments)
| Department | Source | Status |
|------------|--------|--------|
| Billings PD | CrimeMapping.com | Active |
| Great Falls PD | CrimeMapping.com | Active |
| Missoula PD | Public reports | Active |
| Bozeman PD | City website | Active |
| Whitefish PD | City website | Active |

### Jail Booking Sources
| County | Status | URL Pattern |
|--------|--------|-------------|
| Gallatin | Active | gallatin.mt.gov |
| Jefferson | Active | jefferson.mt.gov |
| Flathead | In dev | flathead.mt.gov |
| Missoula | In dev | missoula.mt.gov |

### Adding New Formats

1. Identify the data source (PDF, web, API)
2. For PDF: add county-specific parser in `pdf_parser.py`
3. For web: create fetcher script (see `whitefish_blotter_fetcher.py` as example)
4. For API: create client wrapper (see `crimemapping_fetcher.py` as example)
5. Register in `processor.py` pipeline
6. Add tests and update this README

## 🛠️ Troubleshooting

### Database Locked
```bash
# Find process using database
ps aux | grep python
kill <pid>

# Or use lsof
lsof blotter.db
```

### PDF Parsing Issues
```bash
# Test parser with debug output
python3 pdf_parser.py path/to/pdf.pdf

# Test with specific county
python3 pdf_parser.py path/to/pdf.pdf --county Gallatin

# Check OCR fallback
python3 pdf_parser.py path/to/pdf.pdf --ocr
```

### Email Worker Not Running
```bash
# Test manually
python3 email_worker.py

# Check logs
tail -f worker.log

# Verify IMAP connection
python3 -c "from email_worker import test_connection; test_connection()"
```

### Queue Worker Issues
```bash
# Check Redis connection
redis-cli ping

# Check queue depth
python3 -c "from rq import Queue; from redis import Redis; q = Queue(connection=Redis()); print(q.count)"

# Restart RQ worker
systemctl restart rq-worker
```

### Verify The Whole Stack
```bash
cd /root/montanablotter
./venv/bin/python3 script_watchdog.py
```

If this reports a failure:

- check `systemctl status montanablotter.service`
- check `systemctl status agent-events.service`
- check `systemctl status redis-server`
- check `tail -n 100 /root/montanablotter/cron_errors.log`
- inspect the specific log file named in the watchdog output

### Can't Login
```bash
# Reset admin password
MB_ADMIN_BOOTSTRAP_PASSWORD='new-st...word' python3 seed_admin.py admin
```

### Agent Events WebSocket Not Connecting
```bash
# Check service status
systemctl status agent-events.service

# Check if port is listening
ss -tlnp | grep 18789

# Restart service
systemctl restart agent-events.service
```

## 📂 File Structure

```
/root/montanablotter/
├── app.py                          # Flask application entry point
├── config.py                       # Environment-based configuration
├── db.py                           # SQLAlchemy database models
├── init_db.py                      # Database initialization
├── pdf_parser.py                   # Multi-county PDF parsing engine
├── processor.py                    # Ingestion pipeline orchestrator
├── email_worker.py                 # IMAP email fetching
├── jail_booking_ingest.py          # Multi-county jail roster scraper
├── crimemapping_fetcher.py         # CrimeMapping.com API client
├── missoula_public_report_fetcher.py  # Missoula PD report fetcher
├── whitefish_blotter_fetcher.py    # Whitefish PD incident fetcher
├── bozeman_police_fetcher.py       # Bozeman PD data fetcher
├── summarizer.py                   # AI incident summarization
├── blotter_auditor.py              # Privacy/PII scanning
├── news_writer_agent.py            # Automated editorial content
├── charge_explainer_worker.py      # Legal charge explanation generator
├── seed_admin.py                   # Admin user bootstrap
├── setup.py                        # Automated environment setup
├── DEPLOYMENT_GUIDE.py             # Detailed deployment instructions
├── requirements.txt                # Python dependencies
├── .env.example                    # Environment variable template
├── .gitleaks.toml                  # Secret scanning configuration
├── blotter.db                      # SQLite database
├── uploads/                        # Incoming PDFs
├── records/                        # Processed record files
├── templates/                      # Jinja2 HTML templates
├── static/                         # CSS, JS, images, fonts
│   ├── css/                        # Tailwind + custom styles
│   ├── js/                         # React components (browser-rendered)
│   └── images/                     # Logos, icons, county maps
├── scripts/                        # Operational scripts
│   ├── ops/                        # Health checks, context scripts
│   └── deploy/                     # Deployment helpers
├── mobile/                         # React Native (Expo) app
└── tests/                          # Test suite
```

## 🔐 Security & Privacy

### Application Security
1. **Set `MB_SECRET_KEY`** in `.env` (or environment)
2. **Use environment variables** for credentials — never commit secrets
3. **Set file permissions**: `chmod 600 .env`
4. **Enable login throttling** with `MB_ADMIN_LOGIN_*` settings
5. **Set security headers** (`MB_CONTENT_SECURITY_POLICY`, `MB_REFERRER_POLICY`)
6. **Use HTTPS** (Let's Encrypt)
7. **Regular backups** of database
8. **Update dependencies** regularly

### Secret Scanning

GitHub Actions runs a `Secret Scan` workflow on pull requests, pushes to `main`, and manual dispatches. It uses `gitleaks` with the repo config in `.gitleaks.toml` to scan git history for accidentally committed credentials before they ship.

If you have `gitleaks` installed locally, run:

```bash
gitleaks git --config .gitleaks.toml --redact
```

### Privacy & Redaction

The platform includes an AI-assisted privacy layer:

- **`blotter_auditor.py`** — Scans incidents for PII (names, DOBs, SSNs, addresses) and flags for redaction
- **`summarizer.py`** — Generates public-safe summaries that omit sensitive details
- **Manual review queue** — Admin dashboard for reviewing flagged content before publication
- **Victim name suppression** — Automatic suppression of victim names in domestic violence and sexual assault cases

### Data Retention

- Raw PDFs: Retained for 90 days, then purged
- Processed records: Retained indefinitely (public records)
- Source documents: Content-hashed for deduplication
- User data: Minimal collection, no tracking cookies

## 📊 Monitoring & Operations

### Systemd Services

```bash
# Main application
systemctl status montanablotter.service

# Agent events WebSocket service
systemctl status agent-events.service

# Redis server
systemctl status redis-server
```

### Log Files

```bash
# Application logs
journalctl -u montanablotter -f

# Email worker logs
tail -f worker.log

# Nginx logs
tail -f /var/log/nginx/error.log

# Cron job logs
tail -f cron.log
tail -f cron_errors.log

# Queue worker logs
tail -f /var/log/rq-worker.log
```

### Health Checks

```bash
# Full stack verification
./venv/bin/python3 script_watchdog.py

# JSON output for monitoring systems
./venv/bin/python3 script_watchdog.py --json

# Manual context scripts
./scripts/ops/hermes_context_health.py
./scripts/ops/hermes_context_ingestion.py
./scripts/ops/hermes_context_growth.py
```

## 🤝 Contributing

This is an open-source project. Contributions welcome!

### Areas for improvement:
- Additional county format parsers (Flathead, Missoula, Yellowstone)
- Advanced search features (geospatial, temporal)
- Data visualization dashboard (crime heatmaps, trend analysis)
- Mobile app enhancements (push notifications, offline mode)
- Public API endpoints (GraphQL or REST)
- Export functionality (CSV, PDF, JSON)
- User registration and subscription system
- Multi-language support
- Accessibility improvements (WCAG 2.1 AA)

### Development Setup

```bash
# 1. Clone the repo
git clone https://github.com/yourusername/montanablotter.git
cd montanablotter

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy environment template
cp .env.example .env
# Edit .env with your settings

# 5. Initialize database
python3 init_db.py

# 6. Seed admin user
python3 seed_admin.py devadmin

# 7. Run development server
python3 app.py
```

## 📞 Contact

- **Email**: juan@fertherecerd.com
- **Website**: [www.fertherecerd.com](https://www.fertherecerd.com)
- **Location**: Gibson Flats, MT

## 🗺️ Montana Counties Supported

All 56 Montana counties can be supported. Currently active sources:

### Sheriff Offices (PDF/email)
- **Gallatin County** (GCSO format)
- **Helena/Lewis & Clark County**
- **Jefferson County** (JeffCo format)
- **Whitefish PD** (web scraping)
- **Havre PD** (web scraping)

### Police Departments (API/web)
- **Billings PD** (CrimeMapping.com)
- **Great Falls PD** (CrimeMapping.com)
- **Missoula PD** (public reports)
- **Bozeman PD** (calls for service + crime reports)

### Jail Bookings
- **Gallatin County** (active)
- **Jefferson County** (active)
- **Flathead County** (in development)
- **Missoula County** (in development)

### Generic Format
- Fallback parser for unsupported counties
- Community contributions welcome

## 🎯 Roadmap

### Completed
- [x] Multi-county PDF parsing (GCSO, Helena, JeffCo, Whitefish, Havre)
- [x] Web scraping pipeline (CrimeMapping, Missoula, Bozeman, Whitefish)
- [x] Jail booking ingestion system
- [x] Missing persons directory with photo profiles
- [x] AI-assisted content generation (summarizer, news writer)
- [x] Privacy/PII redaction pipeline
- [x] SEO-optimized public pages (county, city, trends)
- [x] Sponsor/advertiser management
- [x] Queue-based processing with Redis/RQ
- [x] Turso/libsql remote database sync
- [x] React Native mobile app (Expo)
- [x] Agent events WebSocket service
- [x] GitHub Actions secret scanning

### In Progress
- [ ] Flathead County parser
- [ ] Yellowstone County parser
- [ ] Missoula County jail bookings
- [ ] Court records expansion (more counties)
- [ ] Public meetings aggregator

### Planned
- [ ] Public REST API
- [ ] GraphQL endpoint
- [ ] Crime heatmap visualization
- [ ] Trend analysis dashboard
- [ ] Push notifications (mobile)
- [ ] User registration and subscriptions
- [ ] Export to CSV/PDF/JSON
- [ ] Multi-language support
- [ ] Accessibility audit (WCAG 2.1 AA)
- [ ] Automated testing suite (pytest)

---

**Version**: 3.0  
**Last Updated**: April 2026  
**Status**: Production Ready — Active Development
