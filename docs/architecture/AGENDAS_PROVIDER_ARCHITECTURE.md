# Agendas Provider Architecture

This package adds a config-driven Playwright scraper architecture for
`agendas.montanablotter.com`.

Live Montana starter config:

- `configs/agendas/montana_live.json`
  - Billings City Council via Destiny Hosted
  - Great Falls City Commission via CivicPlus AgendaCenter

## Structure

- `agendas_scraper/base.py`
  - abstract `MontanaScraper`
  - shared page loading
  - PDF vs nested-page link detection
  - nested meeting-page hydration
- `agendas_scraper/providers.py`
  - `GranicusProvider`
  - `MuniCodeProvider`
  - `CustomHTMLProvider`
- `agendas_scraper/factory.py`
  - provider registry from `provider` string to subclass
- `agendas_scraper/config.py`
  - JSON config loader
- `agendas_scraper/runner.py`
  - CLI entrypoint

## New City Workflow

For a new city or county, add one JSON object to
`configs/agendas/cities.example.json` with:

- `slug`
- `name`
- `provider`
- `url`
- optional `selectors`
- optional `metadata`

Useful `metadata` keys for messy legacy pages:

- `legacy_row_keywords`
  - list of row-level keywords for old HTML tables
  - example: `["agenda", "minutes", "packet", "commission"]`
- `meeting_scope`
  - `city` or `county`
- `location_slug`
  - stable location key used by the meetings database
- `location_name`
  - public label shown in the dashboard
- `city_name`
  - optional city filter value
- `county_name`
  - optional county filter value

No Python changes should be required when the source fits an existing provider family.

## Link Detection

Agenda links are normalized into one of:

- `pdf`
- `nested_page`
- `unknown`

Detection uses:

1. URL heuristics
2. label heuristics
3. a Playwright `HEAD` probe when the URL alone is ambiguous

That lets the scraper decide whether to store the agenda link directly or visit a nested meeting page and harvest PDF links from there.

## Persisting Results

Scraped meetings can now be stored in SQLite and rendered by the Flask app:

```bash
cd /root/montanablotter
./venv/bin/python agendas_ingest.py configs/agendas/cities.example.json
./venv/bin/python agendas_ingest.py configs/agendas/cities.example.json --city great-falls-city-commission --json
```

This writes to:

- `meeting_locations`
- `meeting_sources`
- `public_meetings`
- `meeting_documents`

The public dashboard is available at `/meetings` on the main site and `/` on `agendas.montanablotter.com`.

## Run

```bash
cd /root/montanablotter
./venv/bin/python -m agendas_scraper.runner configs/agendas/cities.example.json
./venv/bin/python -m agendas_scraper.runner configs/agendas/cities.example.json --city great-falls-city-commission
./venv/bin/python agendas_ingest.py configs/agendas/montana_live.json
```

If Playwright browsers are not installed, the runner will use `MB_PLAYWRIGHT_EXECUTABLE_PATH`
or `/usr/bin/google-chrome` when present.
