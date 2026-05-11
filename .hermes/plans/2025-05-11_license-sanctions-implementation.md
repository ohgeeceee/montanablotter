# Professional License Sanctions — Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Build a complete professional license sanctions feature for Montana Blotter — scrape disciplinary actions from ~12 MT licensing boards, store structured records, expose public browse/filter pages and per-person profile pages, and cross-link with jail bookings / arrest records.

**Architecture:** New SQLite tables + ingestion worker (Kimi PDF/HTML extraction) + Flask blueprint (public pages + API) + Jinja templates + admin review page + sitemap + nav integration. Follows the exact pattern used by `code_violations` and `detention` blueprints.

**Tech Stack:** Flask, SQLite, Jinja2, Tailwind CSS (CDN), Kimi API for extraction, cron via `job_runner.py`.

---

## Task 1: Add database schema to init_db.py

**Objective:** Create three new tables and register the schema helper in `migrate()`.

**Files:**
- Modify: `init_db.py`

**Step 1: Add `ensure_license_sanction_schema()` function**

Insert near the other `ensure_*` functions (e.g., after `ensure_code_violation_schema`):

```python
def ensure_license_sanction_schema(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS license_sanction_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            board_key TEXT NOT NULL UNIQUE,
            board_name TEXT NOT NULL,
            board_url TEXT NOT NULL,
            source_type TEXT NOT NULL DEFAULT 'html',
            last_fetched_at TEXT,
            last_status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS license_sanctions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            name_slug TEXT NOT NULL,
            license_number TEXT,
            board TEXT NOT NULL,
            violation_type TEXT,
            action_taken TEXT,
            effective_date TEXT,
            county TEXT,
            description TEXT,
            source_url TEXT,
            source_document_url TEXT,
            raw_extraction_id INTEGER,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (source_id) REFERENCES license_sanction_sources(id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS license_sanction_raw_extractions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            fetched_at TEXT NOT NULL,
            raw_html TEXT,
            raw_pdf_path TEXT,
            kimi_response_json TEXT,
            extraction_status TEXT NOT NULL DEFAULT 'pending',
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (source_id) REFERENCES license_sanction_sources(id) ON DELETE CASCADE
        )
    ''')
    # Indexes
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_ls_name_slug ON license_sanctions(name_slug)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_ls_board ON license_sanctions(board)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_ls_county ON license_sanctions(county)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_ls_effective_date ON license_sanctions(effective_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_ls_action ON license_sanctions(action_taken)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_ls_active ON license_sanctions(is_active)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_ls_source ON license_sanctions(source_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_ls_raw_extraction ON license_sanctions(raw_extraction_id)')
    conn.commit()
```

**Step 2: Call it in `migrate()`**

Add `ensure_license_sanction_schema(conn)` inside `migrate()` after `ensure_code_violation_schema(conn)`.

**Step 3: Verify**

Run: `python -c "from init_db import migrate; migrate(); print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add init_db.py
git commit -m "feat(license-sanctions): add schema for license sanctions, sources, and raw extractions"
```

---

## Task 2: Seed board sources

**Objective:** Insert the ~12 MT licensing boards into `license_sanction_sources`.

**Files:**
- Create: `license_sanction_sources.py`

**Step 1: Write source registry module**

```python
# license_sanction_sources.py
BOARD_SOURCES = [
    {
        'board_key': 'mt_medical_examiners',
        'board_name': 'Montana Board of Medical Examiners',
        'board_url': 'https://boards.bsd.dli.mt.gov/verify/LicenseLookup.aspx?BID=1',
        'source_type': 'html',
    },
    {
        'board_key': 'mt_dental_examiners',
        'board_name': 'Montana Board of Dental Examiners',
        'board_url': 'https://boards.bsd.dli.mt.gov/verify/LicenseLookup.aspx?BID=2',
        'source_type': 'html',
    },
    {
        'board_key': 'mt_nursing',
        'board_name': 'Montana Board of Nursing',
        'board_url': 'https://boards.bsd.dli.mt.gov/nur',
        'source_type': 'html',
    },
    {
        'board_key': 'mt_bar',
        'board_name': 'Montana State Bar',
        'board_url': 'https://montanabar.org/disciplinary-actions',
        'source_type': 'html',
    },
    {
        'board_key': 'mt_real_estate',
        'board_name': 'Montana Real Estate Commission',
        'board_url': 'https://boards.bsd.dli.mt.gov/rre',
        'source_type': 'html',
    },
    {
        'board_key': 'mt_contractors',
        'board_name': 'Montana Contractor Registration',
        'board_url': 'https://licensing.mt.gov',
        'source_type': 'html',
    },
    {
        'board_key': 'mt_accountancy',
        'board_name': 'Montana Board of Public Accountants',
        'board_url': 'https://boards.bsd.dli.mt.gov/verify/LicenseLookup.aspx?BID=3',
        'source_type': 'html',
    },
    {
        'board_key': 'mt_pharmacy',
        'board_name': 'Montana Board of Pharmacy',
        'board_url': 'https://boards.bsd.dli.mt.gov/verify/LicenseLookup.aspx?BID=4',
        'source_type': 'html',
    },
    {
        'board_key': 'mt_chiropractic',
        'board_name': 'Montana Board of Chiropractors',
        'board_url': 'https://boards.bsd.dli.mt.gov/verify/LicenseLookup.aspx?BID=5',
        'source_type': 'html',
    },
    {
        'board_key': 'mt_veterinary',
        'board_name': 'Montana Board of Veterinary Medicine',
        'board_url': 'https://boards.bsd.dli.mt.gov/verify/LicenseLookup.aspx?BID=6',
        'source_type': 'html',
    },
    {
        'board_key': 'mt_social_work',
        'board_name': 'Montana Board of Social Work Examiners',
        'board_url': 'https://boards.bsd.dli.mt.gov/verify/LicenseLookup.aspx?BID=7',
        'source_type': 'html',
    },
    {
        'board_key': 'mt_psychology',
        'board_name': 'Montana Board of Psychologists',
        'board_url': 'https://boards.bsd.dli.mt.gov/verify/LicenseLookup.aspx?BID=8',
        'source_type': 'html',
    },
]


def seed_license_sanction_sources(db_path: str = 'blotter.db') -> None:
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        for source in BOARD_SOURCES:
            conn.execute(
                '''
                INSERT OR IGNORE INTO license_sanction_sources
                (board_key, board_name, board_url, source_type)
                VALUES (?, ?, ?, ?)
                ''',
                (source['board_key'], source['board_name'], source['board_url'], source['source_type']),
            )
        conn.commit()
        print(f"Seeded {len(BOARD_SOURCES)} license sanction sources")
    finally:
        conn.close()


if __name__ == '__main__':
    seed_license_sanction_sources()
```

**Step 2: Run it**

```bash
source venv/bin/activate
python license_sanction_sources.py
```

Expected: `Seeded 12 license sanction sources`

**Step 3: Commit**

```bash
git add license_sanction_sources.py
git commit -m "feat(license-sanctions): seed 12 MT board sources"
```

---

## Task 3: Build ingestion worker

**Objective:** Create `license_sanction_ingest.py` that fetches each board page, calls Kimi for extraction, parses JSON, and writes records.

**Files:**
- Create: `license_sanction_ingest.py`

**Step 1: Write the ingestion module**

```python
"""
license_sanction_ingest.py

Weekly cron job: fetch each MT licensing board discipline page,
extract structured records via Kimi API, and write to license_sanctions.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from db import connect_db
from license_sanction_sources import BOARD_SOURCES

DB_PATH = os.getenv('MB_DB_PATH', 'blotter.db')
KIMI_API_KEY = os.getenv('KIMI_API_KEY', '')
KIMI_API_BASE = os.getenv('KIMI_API_BASE', 'https://api.kimi.com/coding')
KIMI_MODEL = os.getenv('KIMI_MODEL', 'kimi-k2.6')

_EXTRACTION_PROMPT = """
You are a data extraction assistant. The following HTML or text comes from a Montana professional licensing board disciplinary actions page.

Extract every disciplinary action as a JSON array of objects with these fields:
- name: full name of the sanctioned person (string, required)
- license_number: license or permit number if shown (string, optional)
- board: name of the licensing board (string, required)
- violation_type: type of violation or misconduct (string, optional)
- action_taken: disciplinary action taken (e.g., suspension, revocation, fine, probation) (string, required)
- effective_date: date the action took effect in ISO 8601 format YYYY-MM-DD if shown, else null (string, optional)
- county_if_known: Montana county if mentioned, else null (string, optional)
- description: brief summary of the action (string, optional)
- source_url: the URL of the specific document or page section if available (string, optional)

Return ONLY a valid JSON array. No markdown, no explanation, no preamble.
If no actions are found, return an empty array [].
"""


def _slugify_name(name: str) -> str:
    slug = (name or 'unknown').lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_-]+", "-", slug)
    return slug.strip("-") or 'unknown'


def _fetch_page(url: str, timeout: int = 30) -> str:
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        ),
    }
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def _call_kimi_extraction(html: str, board_name: str) -> list[dict[str, Any]]:
    if not KIMI_API_KEY:
        raise RuntimeError('KIMI_API_KEY not set')

    messages = [
        {'role': 'system', 'content': _EXTRACTION_PROMPT},
        {'role': 'user', 'content': f"Board: {board_name}\n\n{html[:80000]}"},
    ]

    resp = requests.post(
        f"{KIMI_API_BASE}/v1/chat/completions",
        headers={'Authorization': f'Bearer {KIMI_API_KEY}', 'Content-Type': 'application/json'},
        json={'model': KIMI_MODEL, 'messages': messages, 'temperature': 0.1},
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    content = data['choices'][0]['message']['content']

    # Strip markdown fences if present
    content = content.strip()
    if content.startswith('```json'):
        content = content[7:]
    if content.startswith('```'):
        content = content[3:]
    if content.endswith('```'):
        content = content[:-3]
    content = content.strip()

    parsed = json.loads(content)
    if isinstance(parsed, dict) and 'actions' in parsed:
        parsed = parsed['actions']
    if not isinstance(parsed, list):
        raise ValueError(f'Expected JSON array, got {type(parsed).__name__}')
    return parsed


def _insert_raw_extraction(conn: sqlite3.Connection, source_id: int, html: str, status: str, error: str | None = None) -> int:
    cursor = conn.execute(
        '''
        INSERT INTO license_sanction_raw_extractions
        (source_id, fetched_at, raw_html, extraction_status, error_message)
        VALUES (?, ?, ?, ?, ?)
        ''',
        (source_id, datetime.now(timezone.utc).isoformat(), html, status, error),
    )
    conn.commit()
    return cursor.lastrowid


def _upsert_sanction(conn: sqlite3.Connection, source_id: int, raw_id: int, record: dict[str, Any]) -> None:
    name = (record.get('name') or '').strip()
    if not name:
        return
    name_slug = _slugify_name(name)
    board = (record.get('board') or '').strip()
    license_number = (record.get('license_number') or '').strip() or None
    violation_type = (record.get('violation_type') or '').strip() or None
    action_taken = (record.get('action_taken') or '').strip() or None
    effective_date = (record.get('effective_date') or '').strip() or None
    county = (record.get('county_if_known') or '').strip() or None
    description = (record.get('description') or '').strip() or None
    source_url = (record.get('source_url') or '').strip() or None

    # Deduplicate by name + board + effective_date + action_taken
    existing = conn.execute(
        '''
        SELECT id FROM license_sanctions
        WHERE name_slug = ? AND board = ? AND effective_date = ? AND action_taken = ?
        LIMIT 1
        ''',
        (name_slug, board, effective_date, action_taken),
    ).fetchone()

    if existing:
        conn.execute(
            '''
            UPDATE license_sanctions SET
                license_number = COALESCE(?, license_number),
                violation_type = COALESCE(?, violation_type),
                action_taken = ?,
                description = COALESCE(?, description),
                source_url = COALESCE(?, source_url),
                raw_extraction_id = ?,
                updated_at = ?,
                is_active = 1
            WHERE id = ?
            ''',
            (license_number, violation_type, action_taken, description, source_url, raw_id, datetime.now(timezone.utc).isoformat(), existing['id']),
        )
    else:
        conn.execute(
            '''
            INSERT INTO license_sanctions
            (source_id, name, name_slug, license_number, board, violation_type, action_taken, effective_date, county, description, source_url, raw_extraction_id, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            ''',
            (source_id, name, name_slug, license_number, board, violation_type, action_taken, effective_date, county, description, source_url, raw_id, datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()),
        )
    conn.commit()


def ingest_board(source: dict[str, Any], conn: sqlite3.Connection) -> dict[str, Any]:
    source_id = source['id']
    board_name = source['board_name']
    url = source['board_url']

    print(f"[ingest] {board_name} — {url}")
    try:
        html = _fetch_page(url)
    except Exception as exc:
        error = f"Fetch failed: {exc}"
        print(f"[ingest] ERROR {board_name}: {error}")
        _insert_raw_extraction(conn, source_id, '', 'fetch_failed', error)
        conn.execute(
            "UPDATE license_sanction_sources SET last_fetched_at = ?, last_status = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), 'fetch_failed', source_id),
        )
        conn.commit()
        return {'board': board_name, 'status': 'fetch_failed', 'error': error, 'count': 0}

    try:
        records = _call_kimi_extraction(html, board_name)
    except Exception as exc:
        error = f"Extraction failed: {exc}"
        print(f"[ingest] ERROR {board_name}: {error}")
        raw_id = _insert_raw_extraction(conn, source_id, html, 'extraction_failed', error)
        conn.execute(
            "UPDATE license_sanction_sources SET last_fetched_at = ?, last_status = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), 'extraction_failed', source_id),
        )
        conn.commit()
        return {'board': board_name, 'status': 'extraction_failed', 'error': error, 'count': 0}

    raw_id = _insert_raw_extraction(conn, source_id, html, 'success')
    count = 0
    for record in records:
        try:
            _upsert_sanction(conn, source_id, raw_id, record)
            count += 1
        except Exception as exc:
            print(f"[ingest] WARN skipping record for {board_name}: {exc}")

    conn.execute(
        "UPDATE license_sanction_sources SET last_fetched_at = ?, last_status = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), 'success', source_id),
    )
    conn.commit()
    print(f"[ingest] {board_name} — {count} records")
    return {'board': board_name, 'status': 'success', 'count': count}


def run_ingestion(db_path: str = DB_PATH) -> list[dict[str, Any]]:
    conn = connect_db()
    try:
        rows = conn.execute('SELECT * FROM license_sanction_sources ORDER BY board_name').fetchall()
        sources = [dict(r) for r in rows]
        results = []
        for source in sources:
            result = ingest_board(source, conn)
            results.append(result)
        return results
    finally:
        conn.close()


if __name__ == '__main__':
    results = run_ingestion()
    total = sum(r['count'] for r in results if r['status'] == 'success')
    print(f"\nDone. {total} total sanctions ingested.")
    for r in results:
        print(f"  {r['board']}: {r['status']} ({r.get('count', 0)} records)")
```

**Step 2: Verify syntax**

```bash
python -m py_compile license_sanction_ingest.py
```

Expected: no output (success)

**Step 3: Commit**

```bash
git add license_sanction_ingest.py
git commit -m "feat(license-sanctions): add ingestion worker with Kimi extraction"
```

---

## Task 4: Create Flask blueprint

**Objective:** Build `blueprints/license_sanctions.py` with index, detail, and API routes.

**Files:**
- Create: `blueprints/license_sanctions.py`

**Step 1: Write blueprint**

```python
from __future__ import annotations

import re
from datetime import datetime

from flask import Blueprint, abort, jsonify, render_template, request, url_for


license_sanctions_bp = Blueprint('license_sanctions', __name__)

_get_db = None


def register_license_sanctions_blueprint(app, *, get_db):
    global _get_db
    _get_db = get_db
    app.register_blueprint(license_sanctions_bp)


def _slugify(text: str) -> str:
    slug = text.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_-]+", "-", slug)
    return slug.strip("-") or 'unknown'


def _load_sanctions_context(
    *,
    board: str = '',
    county: str = '',
    action: str = '',
    q: str = '',
    date_from: str = '',
    date_to: str = '',
    page: int = 1,
    per_page: int = 50,
):
    conn = _get_db()
    try:
        where_clauses = ['is_active = 1']
        params: list = []

        if board:
            where_clauses.append('board = ?')
            params.append(board)
        if county:
            where_clauses.append('county = ?')
            params.append(county)
        if action:
            where_clauses.append('action_taken = ?')
            params.append(action)
        if date_from:
            where_clauses.append('effective_date >= ?')
            params.append(date_from)
        if date_to:
            where_clauses.append('effective_date <= ?')
            params.append(date_to)
        if q:
            where_clauses.append('(name LIKE ? OR license_number LIKE ? OR violation_type LIKE ?)')
            like = f'%{q}%'
            params.extend([like, like, like])

        where_sql = ' AND '.join(where_clauses)

        count_row = conn.execute(
            f'SELECT COUNT(*) AS total FROM license_sanctions WHERE {where_sql}',
            params,
        ).fetchone()
        total = count_row['total'] if count_row else 0

        rows = conn.execute(
            f'''
            SELECT
                id, name, name_slug, license_number, board,
                violation_type, action_taken, effective_date, county,
                description, source_url, created_at
            FROM license_sanctions
            WHERE {where_sql}
            ORDER BY effective_date DESC, id DESC
            LIMIT ? OFFSET ?
            ''',
            params + [per_page, (page - 1) * per_page],
        ).fetchall()

        # Facets
        boards = [r['board'] for r in conn.execute(
            'SELECT DISTINCT board FROM license_sanctions WHERE is_active = 1 ORDER BY board'
        ).fetchall() if r['board']]
        counties = [r['county'] for r in conn.execute(
            'SELECT DISTINCT county FROM license_sanctions WHERE is_active = 1 AND county IS NOT NULL ORDER BY county'
        ).fetchall() if r['county']]
        actions = [r['action_taken'] for r in conn.execute(
            'SELECT DISTINCT action_taken FROM license_sanctions WHERE is_active = 1 AND action_taken IS NOT NULL ORDER BY action_taken'
        ).fetchall() if r['action_taken']]

        return {
            'rows': rows,
            'total': total,
            'page': page,
            'per_page': per_page,
            'pages': (total + per_page - 1) // per_page,
            'boards': boards,
            'counties': counties,
            'actions': actions,
            'board_filter': board,
            'county_filter': county,
            'action_filter': action,
            'date_from': date_from,
            'date_to': date_to,
            'q': q,
        }
    finally:
        conn.close()


@license_sanctions_bp.route('/license-sanctions')
def license_sanctions_index():
    context = _load_sanctions_context(
        board=request.args.get('board', ''),
        county=request.args.get('county', ''),
        action=request.args.get('action', ''),
        q=request.args.get('q', ''),
        date_from=request.args.get('date_from', ''),
        date_to=request.args.get('date_to', ''),
        page=int(request.args.get('page', 1)),
    )
    return render_template('license_sanctions.html', **context)


@license_sanctions_bp.route('/license-sanctions/<slug>')
def license_sanction_detail(slug):
    conn = _get_db()
    try:
        row = conn.execute(
            '''
            SELECT
                ls.*,
                lss.board_name AS source_board_name,
                lss.board_url AS source_board_url
            FROM license_sanctions ls
            LEFT JOIN license_sanction_sources lss ON ls.source_id = lss.id
            WHERE ls.name_slug = ? AND ls.is_active = 1
            ORDER BY ls.effective_date DESC
            LIMIT 1
            ''',
            (slug,),
        ).fetchone()
        if not row:
            abort(404)

        # All sanctions for this person
        all_sanctions = conn.execute(
            '''
            SELECT * FROM license_sanctions
            WHERE name_slug = ? AND is_active = 1
            ORDER BY effective_date DESC
            ''',
            (slug,),
        ).fetchall()

        # Cross-link: jail bookings by name similarity
        bookings = conn.execute(
            '''
            SELECT id, person_name, county_name, booking_at, charges_summary
            FROM jail_bookings
            WHERE person_name LIKE ?
            ORDER BY booking_at DESC
            LIMIT 10
            ''',
            (f'%{row["name"]}%',),
        ).fetchall()

        # Cross-link: arrest records by name similarity
        records = conn.execute(
            '''
            SELECT id, incident, location, date, county
            FROM records
            WHERE (incident LIKE ? OR location LIKE ?)
            ORDER BY date DESC
            LIMIT 10
            ''',
            (f'%{row["name"]}%', f'%{row["name"]}%'),
        ).fetchall()

        page_title = f"{row['name']} — Montana License Sanctions"
        meta_description = (
            f"{row['name']} disciplinary actions in Montana. "
            f"Board: {row['board']}. Action: {row['action_taken'] or 'Unknown'}. "
            f"View sanctions, violations, and cross-linked records."
        )

        return render_template(
            'license_sanction_detail.html',
            sanction=row,
            all_sanctions=all_sanctions,
            bookings=bookings,
            records=records,
            page_title=page_title,
            meta_description=meta_description,
            canonical_url=url_for('license_sanctions.license_sanction_detail', slug=slug, _external=True),
        )
    finally:
        conn.close()


@license_sanctions_bp.route('/api/license-sanctions')
def api_license_sanctions():
    context = _load_sanctions_context(
        board=request.args.get('board', ''),
        county=request.args.get('county', ''),
        action=request.args.get('action', ''),
        q=request.args.get('q', ''),
        date_from=request.args.get('date_from', ''),
        date_to=request.args.get('date_to', ''),
        page=int(request.args.get('page', 1)),
        per_page=min(int(request.args.get('per_page', 50)), 100),
    )
    return jsonify({
        'sanctions': [dict(r) for r in context['rows']],
        'total': context['total'],
        'page': context['page'],
        'pages': context['pages'],
        'filters': {
            'board': context['board_filter'] or None,
            'county': context['county_filter'] or None,
            'action': context['action_filter'] or None,
            'date_from': context['date_from'] or None,
            'date_to': context['date_to'] or None,
            'q': context['q'] or None,
        },
    })


@license_sanctions_bp.route('/api/license-sanctions/<slug>')
def api_license_sanction_detail(slug):
    conn = _get_db()
    try:
        row = conn.execute(
            'SELECT * FROM license_sanctions WHERE name_slug = ? AND is_active = 1 ORDER BY effective_date DESC LIMIT 1',
            (slug,),
        ).fetchone()
        if not row:
            return jsonify({'error': 'Not found'}), 404
        all_sanctions = conn.execute(
            'SELECT * FROM license_sanctions WHERE name_slug = ? AND is_active = 1 ORDER BY effective_date DESC',
            (slug,),
        ).fetchall()
        return jsonify({
            'sanction': dict(row),
            'all_sanctions': [dict(r) for r in all_sanctions],
        })
    finally:
        conn.close()
```

**Step 2: Verify syntax**

```bash
python -m py_compile blueprints/license_sanctions.py
```

**Step 3: Commit**

```bash
git add blueprints/license_sanctions.py
git commit -m "feat(license-sanctions): add public blueprint with index, detail, and API routes"
```

---

## Task 5: Create public templates

**Objective:** Build `license_sanctions.html` (index) and `license_sanction_detail.html` (profile).

**Files:**
- Create: `templates/license_sanctions.html`
- Create: `templates/license_sanction_detail.html`

**Step 1: Write index template**

```html
{% extends "public_page_base.html" %}

{% block breadcrumb %}
<span class="public-breadcrumb__sep">/</span>
<a href="/license-sanctions">License Sanctions</a>
{% endblock %}

{% block body_class %}license-sanctions-page{% endblock %}

{% block content %}
<section class="max-w-7xl mx-auto px-4 sm:px-6 py-8">
    <div class="mb-6">
        <h1 class="text-2xl font-bold text-slate-900">Montana Professional License Sanctions</h1>
        <p class="text-sm text-slate-600 mt-1">Disciplinary actions from Montana licensing boards — medical, dental, nursing, legal, real estate, contractors, and more.</p>
    </div>

    <form method="get" action="/license-sanctions" class="flex flex-wrap gap-3 mb-6">
        <input type="text" name="q" value="{{ q }}" placeholder="Search name, license, or violation…" class="flex-1 min-w-[200px] rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-400">
        <select name="board" class="rounded-lg border border-slate-300 px-3 py-2 text-sm bg-white">
            <option value="">All Boards</option>
            {% for b in boards %}
            <option value="{{ b }}" {% if board_filter == b %}selected{% endif %}>{{ b }}</option>
            {% endfor %}
        </select>
        <select name="county" class="rounded-lg border border-slate-300 px-3 py-2 text-sm bg-white">
            <option value="">All Counties</option>
            {% for c in counties %}
            <option value="{{ c }}" {% if county_filter == c %}selected{% endif %}>{{ c }}</option>
            {% endfor %}
        </select>
        <select name="action" class="rounded-lg border border-slate-300 px-3 py-2 text-sm bg-white">
            <option value="">All Actions</option>
            {% for a in actions %}
            <option value="{{ a }}" {% if action_filter == a %}selected{% endif %}>{{ a }}</option>
            {% endfor %}
        </select>
        <input type="date" name="date_from" value="{{ date_from }}" class="rounded-lg border border-slate-300 px-3 py-2 text-sm" title="From date">
        <input type="date" name="date_to" value="{{ date_to }}" class="rounded-lg border border-slate-300 px-3 py-2 text-sm" title="To date">
        <button type="submit" class="rounded-lg bg-slate-900 text-white px-4 py-2 text-sm font-medium hover:bg-slate-800">Search</button>
        {% if q or board_filter or county_filter or action_filter or date_from or date_to %}
        <a href="/license-sanctions" class="rounded-lg border border-slate-300 px-4 py-2 text-sm text-slate-700 hover:bg-slate-50">Clear</a>
        {% endif %}
    </form>

    {% if rows %}
    <div class="overflow-x-auto rounded-xl border border-slate-200">
        <table class="min-w-full text-sm">
            <thead class="bg-slate-50 text-slate-700 font-semibold">
                <tr>
                    <th class="px-4 py-3 text-left">Name</th>
                    <th class="px-4 py-3 text-left">Board</th>
                    <th class="px-4 py-3 text-left">Action</th>
                    <th class="px-4 py-3 text-left">Violation</th>
                    <th class="px-4 py-3 text-left">Effective</th>
                    <th class="px-4 py-3 text-left">County</th>
                </tr>
            </thead>
            <tbody class="divide-y divide-slate-100">
                {% for row in rows %}
                <tr class="hover:bg-slate-50">
                    <td class="px-4 py-3">
                        <a href="/license-sanctions/{{ row.name_slug }}" class="text-slate-900 font-medium hover:underline">{{ row.name }}</a>
                        {% if row.license_number %}
                        <span class="text-xs text-slate-500 block">License {{ row.license_number }}</span>
                        {% endif %}
                    </td>
                    <td class="px-4 py-3 text-slate-700">{{ row.board }}</td>
                    <td class="px-4 py-3">
                        <span class="inline-flex items-center rounded-full bg-rose-50 px-2 py-0.5 text-xs font-medium text-rose-700">{{ row.action_taken or 'Unknown' }}</span>
                    </td>
                    <td class="px-4 py-3 text-slate-600">{{ row.violation_type or '—' }}</td>
                    <td class="px-4 py-3 text-slate-600">{{ row.effective_date or '—' }}</td>
                    <td class="px-4 py-3 text-slate-600">{{ row.county or '—' }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>

    {% if pages > 1 %}
    <div class="mt-6 flex items-center justify-between">
        <p class="text-sm text-slate-600">Page {{ page }} of {{ pages }} — {{ total }} total</p>
        <div class="flex gap-2">
            {% if page > 1 %}
            <a href="?page={{ page - 1 }}{% if q %}&q={{ q | urlencode }}{% endif %}{% if board_filter %}&board={{ board_filter | urlencode }}{% endif %}{% if county_filter %}&county={{ county_filter | urlencode }}{% endif %}{% if action_filter %}&action={{ action_filter | urlencode }}{% endif %}{% if date_from %}&date_from={{ date_from }}{% endif %}{% if date_to %}&date_to={{ date_to }}{% endif %}" class="rounded-lg border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50">Previous</a>
            {% endif %}
            {% if page < pages %}
            <a href="?page={{ page + 1 }}{% if q %}&q={{ q | urlencode }}{% endif %}{% if board_filter %}&board={{ board_filter | urlencode }}{% endif %}{% if county_filter %}&county={{ county_filter | urlencode }}{% endif %}{% if action_filter %}&action={{ action_filter | urlencode }}{% endif %}{% if date_from %}&date_from={{ date_from }}{% endif %}{% if date_to %}&date_to={{ date_to }}{% endif %}" class="rounded-lg border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50">Next</a>
            {% endif %}
        </div>
    </div>
    {% endif %}

    {% else %}
    <div class="rounded-xl border border-slate-200 bg-slate-50 p-8 text-center">
        <p class="text-slate-600">No sanctions found matching your filters.</p>
    </div>
    {% endif %}
</section>
{% endblock %}
```

**Step 2: Write detail template**

```html
{% extends "public_page_base.html" %}

{% block breadcrumb %}
<span class="public-breadcrumb__sep">/</span>
<a href="/license-sanctions">License Sanctions</a>
<span class="public-breadcrumb__sep">/</span>
<span>{{ sanction.name }}</span>
{% endblock %}

{% block body_class %}license-sanction-detail-page{% endblock %}

{% block content %}
<section class="max-w-7xl mx-auto px-4 sm:px-6 py-8">
    <div class="mb-8">
        <h1 class="text-2xl font-bold text-slate-900">{{ sanction.name }}</h1>
        <p class="text-sm text-slate-600 mt-1">Montana professional license disciplinary record</p>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div class="lg:col-span-2 space-y-6">
            {% for s in all_sanctions %}
            <div class="rounded-xl border border-slate-200 p-5">
                <div class="flex items-start justify-between mb-3">
                    <div>
                        <h2 class="text-lg font-semibold text-slate-900">{{ s.board }}</h2>
                        <p class="text-sm text-slate-500">{{ s.effective_date or 'Date unknown' }}</p>
                    </div>
                    <span class="inline-flex items-center rounded-full bg-rose-50 px-3 py-1 text-sm font-medium text-rose-700">{{ s.action_taken or 'Action unknown' }}</span>
                </div>
                {% if s.violation_type %}
                <p class="text-sm text-slate-700 mb-2"><strong>Violation:</strong> {{ s.violation_type }}</p>
                {% endif %}
                {% if s.description %}
                <p class="text-sm text-slate-600 mb-3">{{ s.description }}</p>
                {% endif %}
                {% if s.license_number %}
                <p class="text-sm text-slate-500">License: {{ s.license_number }}</p>
                {% endif %}
                {% if s.county %}
                <p class="text-sm text-slate-500">County: {{ s.county }}</p>
                {% endif %}
                {% if s.source_url %}
                <a href="{{ s.source_url }}" target="_blank" rel="noopener" class="text-sm text-blue-600 hover:underline mt-2 inline-block">View source document →</a>
                {% endif %}
            </div>
            {% endfor %}
        </div>

        <aside class="space-y-6">
            {% if bookings %}
            <div class="rounded-xl border border-slate-200 p-5">
                <h3 class="text-sm font-semibold text-slate-900 uppercase tracking-wide mb-3">Jail Bookings</h3>
                <p class="text-xs text-slate-500 mb-3">This name also appears in jail booking records.</p>
                <ul class="space-y-3">
                    {% for b in bookings %}
                    <li>
                        <a href="/booking/{{ b.id }}" class="block text-sm text-slate-900 hover:underline font-medium">{{ b.person_name }}</a>
                        <p class="text-xs text-slate-500">{{ b.county_name }} — {{ b.booking_at[:10] if b.booking_at else 'Unknown date' }}</p>
                        {% if b.charges_summary %}
                        <p class="text-xs text-slate-600">{{ b.charges_summary[:80] }}{% if b.charges_summary|length > 80 %}…{% endif %}</p>
                        {% endif %}
                    </li>
                    {% endfor %}
                </ul>
            </div>
            {% endif %}

            {% if records %}
            <div class="rounded-xl border border-slate-200 p-5">
                <h3 class="text-sm font-semibold text-slate-900 uppercase tracking-wide mb-3">Arrest Records</h3>
                <p class="text-xs text-slate-500 mb-3">This name also appears in incident records.</p>
                <ul class="space-y-3">
                    {% for r in records %}
                    <li>
                        <a href="/record/{{ r.id }}" class="block text-sm text-slate-900 hover:underline font-medium">{{ r.incident or 'Incident' }}</a>
                        <p class="text-xs text-slate-500">{{ r.county or 'Unknown county' }} — {{ r.date or 'Unknown date' }}</p>
                        {% if r.location %}
                        <p class="text-xs text-slate-600">{{ r.location[:80] }}{% if r.location|length > 80 %}…{% endif %}</p>
                        {% endif %}
                    </li>
                    {% endfor %}
                </ul>
            </div>
            {% endif %}
        </aside>
    </div>
</section>
{% endblock %}
```

**Step 3: Commit**

```bash
git add templates/license_sanctions.html templates/license_sanction_detail.html
git commit -m "feat(license-sanctions): add public index and detail templates"
```

---

## Task 6: Wire into app.py

**Objective:** Register blueprint, add nav items, and add sitemap entries.

**Files:**
- Modify: `app.py`

**Step 1: Import and register blueprint**

Near the other blueprint imports (~line 33-38), add:

```python
from blueprints.license_sanctions import register_license_sanctions_blueprint
```

Near the other registrations (~line 11732-11737), add:

```python
register_license_sanctions_blueprint(app, get_db=get_db)
```

**Step 2: Add nav items**

In `inject_public_nav()` (~line 6212), add to `public_primary_nav_items`:

```python
{'id': 'license_sanctions', 'href': '/license-sanctions', 'label': 'License Sanctions', 'menu_label': 'Sanctions'},
```

Add to `public_footer_items` (~line 6261):

```python
{'href': '/license-sanctions', 'label': 'License Sanctions'},
```

**Step 3: Add sitemap support**

In `_sitemap_static_urls()` (~line 7992), add:

```python
{'loc': f"{BASE_URL}/license-sanctions", 'changefreq': 'weekly', 'priority': '0.7'},
```

Add a new sitemap route for sanctions profiles. After `sitemap_charges()` (~line 8248), add:

```python
@app.route('/sitemap-license-sanctions.xml')
def sitemap_license_sanctions():
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT name_slug, updated_at FROM license_sanctions WHERE is_active = 1 GROUP BY name_slug"
        ).fetchall()
        urls = []
        for row in rows:
            urls.append({
                'loc': f"{BASE_URL}/license-sanctions/{row['name_slug']}",
                'lastmod': (row['updated_at'] or '')[:10],
                'changefreq': 'weekly',
                'priority': '0.6',
            })
        return _render_sitemap(urls)
    finally:
        conn.close()
```

Then update `sitemap_index()` to include the new sitemap in the index list.

**Step 4: Verify app starts**

```bash
source venv/bin/activate
python -c "from app import app; print('App loads OK')"
```

**Step 5: Commit**

```bash
git add app.py
git commit -m "feat(license-sanctions): register blueprint, nav, footer, and sitemap"
```

---

## Task 7: Build admin review page

**Objective:** Add admin blueprint module for reviewing sanctions and raw extractions.

**Files:**
- Create: `blueprints/admin/license_sanctions.py`
- Modify: `blueprints/admin/__init__.py`

**Step 1: Write admin module**

```python
from __future__ import annotations

from flask import render_template

from blueprints.admin import admin_bp, require_role
from db import get_db


@admin_bp.route('/license-sanctions')
@require_role('super_admin', 'admin')
def admin_license_sanctions():
    conn = get_db()
    try:
        rows = conn.execute(
            '''
            SELECT
                ls.id, ls.name, ls.board, ls.action_taken, ls.effective_date,
                ls.is_active, ls.created_at, ls.updated_at,
                lss.board_name, lss.last_status
            FROM license_sanctions ls
            LEFT JOIN license_sanction_sources lss ON ls.source_id = lss.id
            ORDER BY ls.updated_at DESC
            LIMIT 500
            '''
        ).fetchall()
        sources = conn.execute('SELECT * FROM license_sanction_sources ORDER BY board_name').fetchall()
        return render_template('admin_license_sanctions.html', rows=rows, sources=sources)
    finally:
        conn.close()
```

**Step 2: Register in admin __init__.py**

Add to the import list in `register_admin_blueprint()`:

```python
from blueprints.admin import license_sanctions  # noqa: F401
```

**Step 3: Create admin template**

Create `templates/admin_license_sanctions.html` extending `base.html` with a table of sanctions and source health.

**Step 4: Commit**

```bash
git add blueprints/admin/license_sanctions.py blueprints/admin/__init__.py templates/admin_license_sanctions.html
git commit -m "feat(license-sanctions): add admin review page"
```

---

## Task 8: Add cron job and test end-to-end

**Objective:** Schedule weekly ingestion and verify the full pipeline.

**Files:**
- Modify: `crontab.txt`

**Step 1: Add cron entry**

```
# Professional license sanctions — weekly ingestion (Mondays 3:00 AM)
0 3 * * 1 cd /root/montanablotter && source venv/bin/activate && python job_runner.py -- python license_sanction_ingest.py >> cron.log 2>&1
```

**Step 2: Run a test ingestion**

```bash
source venv/bin/activate
export KIMI_API_KEY=<your-key>
python license_sanction_ingest.py
```

Verify records appear:

```bash
sqlite3 blotter.db "SELECT COUNT(*) FROM license_sanctions;"
sqlite3 blotter.db "SELECT name, board, action_taken FROM license_sanctions LIMIT 5;"
```

**Step 3: Test public pages**

```bash
python app.py
```

Visit:
- `http://localhost:5000/license-sanctions`
- `http://localhost:5000/license-sanctions/<slug-from-db>`
- `http://localhost:5000/api/license-sanctions`

**Step 4: Commit**

```bash
git add crontab.txt
git commit -m "feat(license-sanctions): add weekly cron job"
```

---

## Post-Implementation Checklist

- [ ] `init_db.migrate()` runs cleanly
- [ ] `license_sanction_sources.py` seeds 12 boards
- [ ] `license_sanction_ingest.py` runs and populates records
- [ ] `/license-sanctions` renders with filters
- [ ] `/license-sanctions/<slug>` renders with cross-links
- [ ] API endpoints return JSON
- [ ] Sitemap includes sanctions URLs
- [ ] Admin page shows records and source health
- [ ] Nav and footer link to the new section
- [ ] Cron job is active

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Board websites change structure | Store raw HTML in `raw_extractions`; re-run with updated prompt |
| Kimi extraction returns malformed JSON | Wrap `json.loads` in try/except; log error and skip record |
| Duplicate records across boards | Deduplicate by `name_slug + board + effective_date + action_taken` |
| Name matching for cross-links is naive | Future: use fuzzy string matching or entity resolution |
| No Kimi API key in prod | Feature gracefully degrades (ingestion logs error, public pages still work) |
