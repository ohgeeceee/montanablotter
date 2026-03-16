# Agendas Subdomain Blueprint

This blueprint assumes the current Montana Blotter stack:

- Flask
- SQLite
- Gunicorn
- Nginx
- server-rendered templates with light JavaScript

That matches the existing app and deployment shape in this repo.

## 1. Routing Logic

### Recommended deployment shape

Use a dedicated Nginx `server_name` for `agendas.montanablotter.com`, but let it point to the same Gunicorn socket at first.

That gives you:

- clean SEO separation from the police-blotter product
- no path-prefix hacks like `/agendas/...`
- flexibility to split it into its own service later if scraper load grows

### Nginx example

```nginx
server {
    listen 80;
    server_name agendas.montanablotter.com;

    location / {
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_pass http://unix:/tmp/montanablotter.sock;
    }
}
```

### Flask option A: host check

This is the least disruptive option for the current monolith.

```python
from flask import abort, render_template, request


def is_agendas_host() -> bool:
    host = request.host.split(":", 1)[0].lower()
    return host == "agendas.montanablotter.com"


@app.route("/")
def root_router():
    if is_agendas_host():
        return public_meetings_dashboard()
    return index()
```

Use this when:

- you want to keep the current `Flask(__name__)` app initialization
- you do not want to refactor all existing routes around `subdomain_matching`

### Flask option B: subdomain blueprint

This is cleaner if you are ready to formalize the subdomain in code.

```python
app = Flask(__name__, subdomain_matching=True)
app.config["SERVER_NAME"] = "montanablotter.com"

agendas_bp = Blueprint("agendas", __name__, subdomain="agendas")


@agendas_bp.route("/")
def public_meetings_dashboard():
    ...


@agendas_bp.route("/meetings/<slug>")
def public_meeting_detail(slug):
    ...


app.register_blueprint(agendas_bp)
```

Use this when:

- you want explicit subdomain routing in Flask
- you are comfortable testing host-based routing locally

### Recommended route set

For the agendas subdomain:

- `/` → Public Meetings dashboard
- `/meetings` → filtered chronological list
- `/meetings/<meeting_slug>` → single meeting detail
- `/locations/<slug>` → city/county landing page
- `/calendar.ics` → optional export feed later
- `/sitemap.xml` → agendas-only sitemap

## 2. Database Schema

The core design should separate places, governing bodies, meeting events, and documents.

### `locations`

Reuses your Montana geography in a neutral way.

```sql
CREATE TABLE locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    location_type TEXT NOT NULL CHECK (location_type IN ('county', 'city')),
    parent_location_id INTEGER,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    county_name TEXT,
    official_site_url TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (parent_location_id) REFERENCES locations(id) ON DELETE SET NULL
);

CREATE INDEX idx_locations_type_name ON locations(location_type, name);
```

Notes:

- for county rows, `parent_location_id` is `NULL`
- for city rows, `parent_location_id` points to the county row
- `slug` should be stable: `yellowstone-county`, `billings`

### `meeting_bodies`

Represents the actual government body holding meetings.

```sql
CREATE TABLE meeting_bodies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id INTEGER NOT NULL,
    meeting_scope TEXT NOT NULL CHECK (meeting_scope IN ('city', 'county')),
    body_name TEXT NOT NULL,
    body_slug TEXT NOT NULL UNIQUE,
    body_type TEXT NOT NULL DEFAULT 'governing_body',
    official_page_url TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (location_id) REFERENCES locations(id) ON DELETE CASCADE
);

CREATE INDEX idx_meeting_bodies_location ON meeting_bodies(location_id, meeting_scope);
```

Examples:

- `Billings City Council`
- `Missoula County Board of Commissioners`
- `Great Falls City Commission`

### `meeting_sources`

Stores the website/page the scraper should visit.

```sql
CREATE TABLE meeting_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_body_id INTEGER NOT NULL,
    source_url TEXT NOT NULL,
    platform_type TEXT NOT NULL,
    selectors_json TEXT,
    document_type_default TEXT DEFAULT 'agenda',
    scrape_interval_hours INTEGER NOT NULL DEFAULT 12,
    last_success_at TEXT,
    last_error TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (meeting_body_id) REFERENCES meeting_bodies(id) ON DELETE CASCADE
);

CREATE INDEX idx_meeting_sources_body ON meeting_sources(meeting_body_id, is_active);
```

`platform_type` examples:

- `civicplus_agenda_center`
- `legistar`
- `generic_html_links`
- `calendar_with_pdfs`

### `meetings`

One row per meeting occurrence.

```sql
CREATE TABLE meetings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_body_id INTEGER NOT NULL,
    external_uid TEXT,
    meeting_slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    ends_at TEXT,
    meeting_status TEXT NOT NULL DEFAULT 'scheduled'
        CHECK (meeting_status IN ('scheduled', 'cancelled', 'completed', 'rescheduled')),
    location_name TEXT,
    meeting_page_url TEXT,
    source_notes TEXT,
    last_scraped_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (meeting_body_id) REFERENCES meeting_bodies(id) ON DELETE CASCADE
);

CREATE INDEX idx_meetings_body_date ON meetings(meeting_body_id, starts_at DESC);
CREATE INDEX idx_meetings_status_date ON meetings(meeting_status, starts_at DESC);
```

### `meeting_documents`

One meeting can have multiple documents.

```sql
CREATE TABLE meeting_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL,
    document_type TEXT NOT NULL
        CHECK (document_type IN ('agenda', 'minutes', 'packet', 'notice', 'video', 'other')),
    document_url TEXT NOT NULL,
    source_page_url TEXT,
    title TEXT,
    published_at TEXT,
    file_sha256 TEXT,
    mime_type TEXT,
    is_primary INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
);

CREATE INDEX idx_meeting_documents_meeting_type ON meeting_documents(meeting_id, document_type);
CREATE UNIQUE INDEX idx_meeting_documents_unique_url ON meeting_documents(document_url);
```

### `scrape_runs`

Track reliability, not just content.

```sql
CREATE TABLE scrape_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_source_id INTEGER NOT NULL,
    started_at TEXT DEFAULT (datetime('now')),
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'ok', 'error')),
    items_found INTEGER NOT NULL DEFAULT 0,
    items_created INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    FOREIGN KEY (meeting_source_id) REFERENCES meeting_sources(id) ON DELETE CASCADE
);
```

## 3. Scraper Architecture

### Recommendation

Use Python with:

- `requests`
- `beautifulsoup4`
- `dateparser`
- optional `playwright` fallback for JS-heavy pages

Do not hardcode one parser per city. Build a config-driven scraper registry because Montana municipal sites will vary a lot.

### Extraction strategy

1. Load active `meeting_sources`
2. Pick parser strategy from `platform_type`
3. Fetch HTML
4. Extract candidate rows/cards
5. Normalize:
   - title
   - meeting date/time
   - PDF links
   - minutes links
6. Upsert into `meetings`
7. Upsert linked `meeting_documents`
8. Log success/failure into `scrape_runs`

### Config example

```python
from dataclasses import dataclass


@dataclass
class AgendaSourceConfig:
    platform_type: str
    row_selector: str
    title_selector: str
    date_selector: str
    link_selector: str
    minutes_selector: str | None = None
```

Example rows:

- Great Falls often behaves like a CMS page with agenda/minutes links in cards or tables
- Billings commonly exposes agendas through an agenda-center style listing
- Missoula city/county pages may split commission pages, calendars, and PDF links across different templates

That means the parser should not assume:

- one PDF per page
- one date format
- one link label
- one table structure

### Generic scraper pattern

```python
import hashlib
from urllib.parse import urljoin

import dateparser
import requests
from bs4 import BeautifulSoup


def crawl_meeting_source(source_row, config):
    response = requests.get(source_row["source_url"], timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    rows = soup.select(config.row_selector)
    items = []

    for row in rows:
        title_node = row.select_one(config.title_selector)
        date_node = row.select_one(config.date_selector)
        pdf_nodes = row.select(config.link_selector)
        minutes_nodes = row.select(config.minutes_selector) if config.minutes_selector else []

        title = title_node.get_text(" ", strip=True) if title_node else "Public meeting"
        raw_date = date_node.get_text(" ", strip=True) if date_node else ""
        parsed_date = dateparser.parse(raw_date)
        if not parsed_date:
            continue

        document_links = []
        for node in pdf_nodes:
            href = (node.get("href") or "").strip()
            if not href:
                continue
            document_links.append(
                {
                    "document_type": "agenda",
                    "document_url": urljoin(source_row["source_url"], href),
                    "title": node.get_text(" ", strip=True) or "Agenda PDF",
                }
            )

        for node in minutes_nodes:
            href = (node.get("href") or "").strip()
            if not href:
                continue
            document_links.append(
                {
                    "document_type": "minutes",
                    "document_url": urljoin(source_row["source_url"], href),
                    "title": node.get_text(" ", strip=True) or "Minutes PDF",
                }
            )

        items.append(
            {
                "title": title,
                "starts_at": parsed_date.isoformat(),
                "meeting_page_url": source_row["source_url"],
                "documents": document_links,
                "external_uid": hashlib.sha256(
                    f"{source_row['id']}|{title}|{parsed_date.isoformat()}".encode()
                ).hexdigest()[:24],
            }
        )

    return items
```

### Upsert pattern

Use:

- `external_uid` as the primary dedupe key
- `document_url` unique index for document dedupe
- `last_scraped_at` for freshness tracking

If a site is JS-heavy:

- fall back to `playwright`
- wait for a stable selector
- then hand HTML back to the same normalization pipeline

### Source-specific parser strategy

Build parser functions like:

```python
PARSERS = {
    "generic_html_links": crawl_generic_html_links,
    "civicplus_agenda_center": crawl_civicplus_agenda_center,
    "legistar": crawl_legistar,
}
```

That gives you:

- shared DB logic
- shared dedupe logic
- separate DOM handling per CMS family

## 4. Frontend Component

Given the current Flask stack, start with server-rendered filters plus a small amount of client-side polish.

### Dashboard query shape

```python
@app.route("/meetings")
def public_meetings_dashboard():
    county = request.args.get("county", "").strip()
    city = request.args.get("city", "").strip()
    scope = request.args.get("scope", "").strip()  # city | county
    q = request.args.get("q", "").strip()

    sql = """
        SELECT m.id, m.meeting_slug, m.title, m.starts_at, m.meeting_status,
               mb.body_name, mb.meeting_scope,
               loc.name AS location_name,
               loc.county_name,
               md.document_url
        FROM meetings m
        JOIN meeting_bodies mb ON mb.id = m.meeting_body_id
        JOIN locations loc ON loc.id = mb.location_id
        LEFT JOIN meeting_documents md
               ON md.meeting_id = m.id AND md.document_type = 'agenda' AND md.is_primary = 1
        WHERE m.starts_at >= datetime('now', '-30 days')
    """
```

### Filters

Support:

- `County`
- `City`
- `Meeting Scope` (`City` or `County`)
- text search
- `Upcoming only` toggle

### Recommended UI

Top bar:

- search input
- county select
- city select
- scope pills

List:

- chronological cards grouped by date
- body name
- location
- meeting time
- status badge
- primary agenda link
- optional minutes link when available

### Template shape

```html
<form method="GET" action="/" class="meeting-filters">
  <input type="text" name="q" placeholder="Search meetings, places, bodies..." />
  <select name="county">...</select>
  <select name="city">...</select>
  <select name="scope">
    <option value="">All</option>
    <option value="county">County</option>
    <option value="city">City</option>
  </select>
  <button type="submit">Filter</button>
</form>

{% for meeting in meetings %}
<article class="meeting-card">
  <p>{{ meeting.starts_at }}</p>
  <h2><a href="/meetings/{{ meeting.meeting_slug }}">{{ meeting.title }}</a></h2>
  <p>{{ meeting.body_name }} · {{ meeting.location_name }}</p>
  {% if meeting.document_url %}
  <a href="{{ meeting.document_url }}" target="_blank" rel="noopener">Open agenda PDF</a>
  {% endif %}
</article>
{% endfor %}
```

### Recommended detail page sections

On `/meetings/<slug>`:

- meeting title
- date/time
- body name
- city/county metadata
- all documents
- source page link
- "more from this location" links

## 5. Montana-Specific Data Modeling

To keep things consistent with the existing product:

- reuse your county and city slugs where possible
- keep `locations` normalized rather than duplicating county/city text inside meetings
- pre-seed all 56 counties
- then seed Montana cities you care about first:
  - Billings
  - Missoula
  - Great Falls
  - Bozeman
  - Helena
  - Kalispell
  - Whitefish

## 6. Rollout Order

### Phase 1

- add schema
- seed counties/cities/meeting bodies
- build agendas-only dashboard
- support `agenda` PDFs only

### Phase 2

- add `minutes`
- add body detail pages
- add source health monitoring

### Phase 3

- add `packet` documents
- add ICS feeds
- add email alerts for new agendas by county/city

## 7. Recommendation

For this repo, the cleanest first implementation is:

1. keep the Flask monolith
2. use a dedicated `agendas.montanablotter.com` host in Nginx
3. build host-aware Flask routes or a subdomain blueprint
4. add the meeting schema beside your existing blotter schema
5. use a config-driven Python scraper with per-CMS parser families

That gives you a real product quickly without forcing an early rewrite to Next.js.
