# Code Enforcement Violations Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Add a city/county code enforcement violations module to Montana Blotter — searchable public pages, address-level permalinks, cross-links with jail bookings and arrests, and a real-estate embed widget.

**Architecture:** New `code_violations` blueprint (Flask) + SQLite schema (`code_violations`, `code_violation_sources`, `property_addresses`) + ingestion worker (`code_violation_ingest.py`) + admin panel + public pages + JSON API for embeds. Follows the same patterns as `detention.py` / `jail_bookings`.

**Tech Stack:** Flask, SQLite (via `db.py`), Jinja2, vanilla JS, Python 3.11.

---

## Task 1: Add database schema in `init_db.py`

**Objective:** Create tables and indexes for code enforcement violations.

**Files:**
- Modify: `init_db.py` (append `ensure_code_violation_schema` + call it in `init_database` and `migrate`)

**Step 1: Write the schema function**

Append to `init_db.py`:

```python
def ensure_code_violation_schema(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()

    # Sources — one row per city/county portal or FOIA pipeline
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS code_violation_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_key TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL,
            city TEXT NOT NULL,
            county TEXT,
            source_type TEXT NOT NULL DEFAULT 'portal',
            portal_url TEXT,
            request_email TEXT,
            is_enabled INTEGER NOT NULL DEFAULT 1,
            last_checked_at TEXT,
            last_success_at TEXT,
            latest_error TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    ''')

    # Normalized property addresses (deduped, USPS-normalized)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS property_addresses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            address_slug TEXT UNIQUE NOT NULL,
            street TEXT NOT NULL,
            city TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'MT',
            zip TEXT,
            county TEXT,
            lat REAL,
            lon REAL,
            first_seen_at TEXT DEFAULT (datetime('now')),
            last_seen_at TEXT DEFAULT (datetime('now'))
        )
    ''')

    # Individual violation records
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS code_violations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            property_address_id INTEGER,
            raw_address TEXT NOT NULL,
            violation_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            date_issued TEXT,
            date_resolved TEXT,
            owner_name TEXT,
            description TEXT,
            fine_amount REAL,
            source_record_id TEXT,
            source_url TEXT,
            raw_json TEXT,
            hash_id TEXT,
            first_seen_at TEXT DEFAULT (datetime('now')),
            last_seen_at TEXT DEFAULT (datetime('now')),
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (source_id) REFERENCES code_violation_sources(id) ON DELETE CASCADE,
            FOREIGN KEY (property_address_id) REFERENCES property_addresses(id) ON DELETE SET NULL
        )
    ''')

    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_cv_sources_enabled '
        'ON code_violation_sources(is_enabled, city)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_property_addresses_slug '
        'ON property_addresses(address_slug)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_property_addresses_geo '
        'ON property_addresses(city, county)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_code_violations_lookup '
        'ON code_violations(property_address_id, status, date_issued)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_code_violations_source '
        'ON code_violations(source_id, last_seen_at)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_code_violations_hash '
        'ON code_violations(hash_id)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_code_violations_type '
        'ON code_violations(violation_type, city)'
    )

    # Graceful column additions (idempotent migration style)
    for col, definition in [
        ('county', 'TEXT'),
        ('source_type', "TEXT NOT NULL DEFAULT 'portal'"),
        ('portal_url', 'TEXT'),
        ('request_email', 'TEXT'),
        ('is_enabled', 'INTEGER NOT NULL DEFAULT 1'),
        ('last_checked_at', 'TEXT'),
        ('last_success_at', 'TEXT'),
        ('latest_error', "TEXT DEFAULT ''"),
        ('notes', "TEXT DEFAULT ''"),
        ('created_at', "TEXT DEFAULT (datetime('now'))"),
        ('updated_at', "TEXT DEFAULT (datetime('now'))"),
    ]:
        try:
            cursor.execute(f'ALTER TABLE code_violation_sources ADD COLUMN {col} {definition}')
            print(f'✅ Added code_violation_sources.{col}')
        except sqlite3.OperationalError:
            pass

    for col, definition in [
        ('county', 'TEXT'),
        ('lat', 'REAL'),
        ('lon', 'REAL'),
        ('first_seen_at', "TEXT DEFAULT (datetime('now'))"),
        ('last_seen_at', "TEXT DEFAULT (datetime('now'))"),
    ]:
        try:
            cursor.execute(f'ALTER TABLE property_addresses ADD COLUMN {col} {definition}')
            print(f'✅ Added property_addresses.{col}')
        except sqlite3.OperationalError:
            pass

    for col, definition in [
        ('property_address_id', 'INTEGER'),
        ('raw_address', "TEXT NOT NULL DEFAULT ''"),
        ('status', "TEXT NOT NULL DEFAULT 'open'"),
        ('date_issued', 'TEXT'),
        ('date_resolved', 'TEXT'),
        ('owner_name', 'TEXT'),
        ('description', 'TEXT'),
        ('fine_amount', 'REAL'),
        ('source_record_id', 'TEXT'),
        ('source_url', 'TEXT'),
        ('raw_json', 'TEXT'),
        ('hash_id', 'TEXT'),
        ('first_seen_at', "TEXT DEFAULT (datetime('now'))"),
        ('last_seen_at', "TEXT DEFAULT (datetime('now'))"),
        ('created_at', "TEXT DEFAULT (datetime('now'))"),
        ('updated_at', "TEXT DEFAULT (datetime('now'))"),
    ]:
        try:
            cursor.execute(f'ALTER TABLE code_violations ADD COLUMN {col} {definition}')
            print(f'✅ Added code_violations.{col}')
        except sqlite3.OperationalError:
            pass
```

**Step 2: Wire into `init_database()`**

In `init_database()`, add after `ensure_agent_mission_control_schema(conn)`:

```python
    ensure_code_violation_schema(conn)
```

**Step 3: Wire into `migrate()`**

In `migrate()`, add as the final migration block:

```python
    # 2026-05-11: code enforcement violations
    ensure_code_violation_schema(conn)
```

**Step 4: Verify**

Run: `python -c "from init_db import init_database; init_database()"`
Expected: prints tables created successfully, no errors.

**Step 5: Commit**

```bash
git add init_db.py
git commit -m "feat(code-violations): add schema for violations, sources, and property addresses"
```

---

## Task 2: Create ingestion module `code_violation_ingest.py`

**Objective:** Build a standalone ingestion worker that can parse PDF/Excel/JSON exports and write normalized records.

**Files:**
- Create: `code_violation_ingest.py`
- Create: `tests/test_code_violation_ingest.py`

**Step 1: Write the ingestion module**

```python
"""
Code Enforcement Violation Ingestion Worker

Reads city-exported files (PDF, Excel, CSV, JSON) and writes to
code_violations / property_addresses tables.

Usage:
    python code_violation_ingest.py --source billings --file /path/to/export.pdf
    python code_violation_ingest.py --source missoula --file /path/to/export.xlsx
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from typing import Any

from db import connect_db

DB_PATH = os.getenv('MB_DB_PATH', '/root/montanablotter/blotter.db')


def _slugify_address(street: str, city: str, state: str = 'MT', zip_code: str = '') -> str:
    parts = [street, city, state, zip_code]
    raw = ' '.join(p for p in parts if p)
    slug = raw.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_-]+", "-", slug)
    return slug.strip("-") or 'unknown'


def _hash_record(source_id: int, raw_address: str, violation_type: str, date_issued: str) -> str:
    payload = f"{source_id}|{raw_address}|{violation_type}|{date_issued}"
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _normalize_date(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(value, fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return value[:10] if len(value) >= 10 else value


def _ensure_source(conn: sqlite3.Connection, source_key: str, display_name: str, city: str) -> int:
    row = conn.execute(
        'SELECT id FROM code_violation_sources WHERE source_key = ?',
        (source_key,),
    ).fetchone()
    if row:
        return row['id']
    cur = conn.execute(
        'INSERT INTO code_violation_sources (source_key, display_name, city) VALUES (?, ?, ?)',
        (source_key, display_name, city),
    )
    conn.commit()
    return cur.lastrowid


def _ensure_property_address(conn: sqlite3.Connection, street: str, city: str, state: str = 'MT', zip_code: str = '', county: str = '') -> int:
    slug = _slugify_address(street, city, state, zip_code)
    row = conn.execute(
        'SELECT id FROM property_addresses WHERE address_slug = ?',
        (slug,),
    ).fetchone()
    if row:
        conn.execute(
            'UPDATE property_addresses SET last_seen_at = datetime("now") WHERE id = ?',
            (row['id'],),
        )
        conn.commit()
        return row['id']
    cur = conn.execute(
        '''
        INSERT INTO property_addresses (address_slug, street, city, state, zip, county)
        VALUES (?, ?, ?, ?, ?, ?)
        ''',
        (slug, street, city, state, zip_code, county),
    )
    conn.commit()
    return cur.lastrowid


def ingest_records(
    conn: sqlite3.Connection,
    *,
    source_key: str,
    display_name: str,
    city: str,
    records: list[dict[str, Any]],
) -> dict[str, int]:
    """Insert or update violation records. Returns counts."""
    source_id = _ensure_source(conn, source_key, display_name, city)
    inserted = updated = 0

    for rec in records:
        raw_address = (rec.get('address') or rec.get('property_address') or '').strip()
        violation_type = (rec.get('violation_type') or rec.get('type') or 'Unknown').strip()
        status = (rec.get('status') or 'open').strip().lower()
        date_issued = _normalize_date(rec.get('date_issued') or rec.get('issued_date'))
        date_resolved = _normalize_date(rec.get('date_resolved') or rec.get('resolved_date'))
        owner_name = (rec.get('owner_name') or rec.get('owner') or '').strip() or None
        description = (rec.get('description') or '').strip() or None
        fine_amount = rec.get('fine_amount')
        source_record_id = (rec.get('source_record_id') or '').strip() or None
        source_url = (rec.get('source_url') or '').strip() or None

        # Normalize address into property_addresses
        property_address_id = None
        if raw_address:
            # Naive split: assume "123 Main St, Missoula, MT 59801"
            street = raw_address
            zip_code = ''
            m = re.search(r'\b(\d{5}(?:-\d{4})?)\s*$', raw_address)
            if m:
                zip_code = m.group(1)
                street = raw_address[:m.start()].strip().rstrip(',').strip()
            property_address_id = _ensure_property_address(
                conn, street, city, 'MT', zip_code
            )

        hash_id = _hash_record(source_id, raw_address, violation_type, date_issued or '')

        existing = conn.execute(
            'SELECT id FROM code_violations WHERE hash_id = ?',
            (hash_id,),
        ).fetchone()

        if existing:
            conn.execute(
                '''
                UPDATE code_violations
                SET status = ?, date_resolved = ?, owner_name = ?,
                    description = ?, fine_amount = ?, source_url = ?,
                    last_seen_at = datetime('now'), updated_at = datetime('now')
                WHERE id = ?
                ''',
                (status, date_resolved, owner_name, description, fine_amount, source_url, existing['id']),
            )
            updated += 1
        else:
            conn.execute(
                '''
                INSERT INTO code_violations
                (source_id, property_address_id, raw_address, violation_type, status,
                 date_issued, date_resolved, owner_name, description, fine_amount,
                 source_record_id, source_url, raw_json, hash_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    source_id, property_address_id, raw_address, violation_type, status,
                    date_issued, date_resolved, owner_name, description, fine_amount,
                    source_record_id, source_url, json.dumps(rec), hash_id,
                ),
            )
            inserted += 1

    conn.commit()
    conn.execute(
        'UPDATE code_violation_sources SET last_success_at = datetime("now"), updated_at = datetime("now") WHERE id = ?',
        (source_id,),
    )
    conn.commit()
    return {'inserted': inserted, 'updated': updated, 'source_id': source_id}


def _parse_pdf(file_path: str) -> list[dict[str, Any]]:
    """Placeholder: integrate pdf_parser.py or Kimi extraction here."""
    raise NotImplementedError('PDF parsing not yet implemented — use --json or manual preprocessing.')


def _parse_excel(file_path: str) -> list[dict[str, Any]]:
    try:
        import pandas as pd
    except ImportError:
        raise RuntimeError('pandas required for Excel parsing: pip install pandas openpyxl')
    df = pd.read_excel(file_path)
    return df.fillna('').to_dict(orient='records')


def _parse_csv(file_path: str) -> list[dict[str, Any]]:
    import csv
    with open(file_path, newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def _parse_json(file_path: str) -> list[dict[str, Any]]:
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if isinstance(data, dict) and 'records' in data:
        return data['records']
    return data if isinstance(data, list) else [data]


def main():
    parser = argparse.ArgumentParser(description='Ingest code enforcement violations')
    parser.add_argument('--source', required=True, help='Source key (e.g. billings)')
    parser.add_argument('--display-name', default='', help='Human-readable source name')
    parser.add_argument('--city', required=True, help='City name')
    parser.add_argument('--file', required=True, help='Path to export file')
    parser.add_argument('--format', choices=['pdf', 'excel', 'csv', 'json'], help='File format (auto-detected from extension if omitted)')
    args = parser.parse_args()

    ext = (args.format or os.path.splitext(args.file)[1].lstrip('.').lower())
    if ext in ('xlsx', 'xls'):
        ext = 'excel'

    parsers = {
        'pdf': _parse_pdf,
        'excel': _parse_excel,
        'csv': _parse_csv,
        'json': _parse_json,
    }
    parse_fn = parsers.get(ext)
    if not parse_fn:
        print(f'Unsupported format: {ext}')
        sys.exit(1)

    records = parse_fn(args.file)
    print(f'Parsed {len(records)} records from {args.file}')

    conn = connect_db()
    try:
        result = ingest_records(
            conn,
            source_key=args.source,
            display_name=args.display_name or args.city,
            city=args.city,
            records=records,
        )
        print(f"Inserted: {result['inserted']}, Updated: {result['updated']}")
    finally:
        conn.close()


if __name__ == '__main__':
    main()
```

**Step 2: Write the test**

```python
import sqlite3
import tempfile
import os
import json
import unittest

from init_db import ensure_code_violation_schema
from code_violation_ingest import ingest_records, _slugify_address, _hash_record, _normalize_date


class TestCodeViolationIngest(unittest.TestCase):
    def setUp(self):
        self.db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.conn = sqlite3.connect(self.db.name)
        self.conn.row_factory = sqlite3.Row
        ensure_code_violation_schema(self.conn)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db.name)

    def test_slugify_address(self):
        self.assertEqual(_slugify_address('123 Main St', 'Missoula', 'MT', '59801'), '123-main-st-missoula-mt-59801')

    def test_normalize_date(self):
        self.assertEqual(_normalize_date('05/11/2026'), '2026-05-11')
        self.assertEqual(_normalize_date('2026-05-11'), '2026-05-11')
        self.assertIsNone(_normalize_date(''))

    def test_ingest_creates_source_and_violation(self):
        records = [
            {
                'address': '456 Oak Ave, Billings, MT 59101',
                'violation_type': 'Abandoned Vehicle',
                'status': 'open',
                'date_issued': '2026-04-01',
                'owner_name': 'John Doe',
            }
        ]
        result = ingest_records(
            self.conn,
            source_key='billings',
            display_name='Billings Code Enforcement',
            city='Billings',
            records=records,
        )
        self.assertEqual(result['inserted'], 1)
        self.assertEqual(result['updated'], 0)

        row = self.conn.execute('SELECT * FROM code_violations').fetchone()
        self.assertEqual(row['violation_type'], 'Abandoned Vehicle')
        self.assertEqual(row['status'], 'open')

        addr = self.conn.execute('SELECT * FROM property_addresses').fetchone()
        self.assertEqual(addr['city'], 'Billings')

    def test_idempotent_reingest_updates(self):
        records = [
            {'address': '789 Pine Rd, Helena, MT 59601', 'violation_type': 'Permit Violation', 'status': 'open', 'date_issued': '2026-03-01'}
        ]
        ingest_records(self.conn, source_key='helena', display_name='Helena', city='Helena', records=records)
        result = ingest_records(self.conn, source_key='helena', display_name='Helena', city='Helena', records=records)
        self.assertEqual(result['updated'], 1)
        self.assertEqual(result['inserted'], 0)


if __name__ == '__main__':
    unittest.main()
```

**Step 3: Run tests**

Run: `python -m pytest tests/test_code_violation_ingest.py -v`
Expected: 4 passed.

**Step 4: Commit**

```bash
git add code_violation_ingest.py tests/test_code_violation_ingest.py
git commit -m "feat(code-violations): add ingestion worker with tests"
```

---

## Task 3: Create `blueprints/code_violations.py`

**Objective:** Build the public-facing blueprint with list, search, detail, and property pages.

**Files:**
- Create: `blueprints/code_violations.py`
- Create: `templates/code_violations.html`
- Create: `templates/property_detail.html`

**Step 1: Write the blueprint**

```python
from __future__ import annotations

import re
from datetime import datetime

from flask import Blueprint, abort, jsonify, render_template, request, url_for


code_violations_bp = Blueprint('code_violations', __name__)

_get_db = None


def register_code_violations_blueprint(app, *, get_db):
    global _get_db
    _get_db = get_db
    app.register_blueprint(code_violations_bp)


def _slugify(text: str) -> str:
    slug = text.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_-]+", "-", slug)
    return slug.strip("-") or 'unknown'


def _load_violations_context(
    *,
    city: str = '',
    violation_type: str = '',
    status: str = '',
    q: str = '',
    page: int = 1,
    per_page: int = 50,
):
    conn = _get_db()
    try:
        where_clauses = ['1=1']
        params: list = []

        if city:
            where_clauses.append('pa.city = ?')
            params.append(city)
        if violation_type:
            where_clauses.append('cv.violation_type = ?')
            params.append(violation_type)
        if status:
            where_clauses.append('cv.status = ?')
            params.append(status)
        if q:
            where_clauses.append('(pa.street LIKE ? OR cv.violation_type LIKE ? OR cv.owner_name LIKE ?)')
            like = f'%{q}%'
            params.extend([like, like, like])

        where_sql = ' AND '.join(where_clauses)

        count_row = conn.execute(
            f'''
            SELECT COUNT(*) AS total
            FROM code_violations cv
            LEFT JOIN property_addresses pa ON cv.property_address_id = pa.id
            WHERE {where_sql}
            ''',
            params,
        ).fetchone()
        total = count_row['total'] if count_row else 0

        rows = conn.execute(
            f'''
            SELECT
                cv.id,
                cv.violation_type,
                cv.status,
                cv.date_issued,
                cv.date_resolved,
                cv.owner_name,
                cv.fine_amount,
                cv.raw_address,
                pa.address_slug,
                pa.street,
                pa.city,
                pa.state,
                pa.zip,
                pa.county,
                cvs.display_name AS source_name
            FROM code_violations cv
            LEFT JOIN property_addresses pa ON cv.property_address_id = pa.id
            LEFT JOIN code_violation_sources cvs ON cv.source_id = cvs.id
            WHERE {where_sql}
            ORDER BY cv.date_issued DESC, cv.id DESC
            LIMIT ? OFFSET ?
            ''',
            params + [per_page, (page - 1) * per_page],
        ).fetchall()

        # Facets
        cities = [r['city'] for r in conn.execute(
            'SELECT DISTINCT city FROM property_addresses ORDER BY city'
        ).fetchall() if r['city']]
        types = [r['violation_type'] for r in conn.execute(
            'SELECT DISTINCT violation_type FROM code_violations ORDER BY violation_type'
        ).fetchall() if r['violation_type']]

        return {
            'rows': rows,
            'total': total,
            'page': page,
            'per_page': per_page,
            'pages': (total + per_page - 1) // per_page,
            'cities': cities,
            'violation_types': types,
            'city_filter': city,
            'type_filter': violation_type,
            'status_filter': status,
            'q': q,
        }
    finally:
        conn.close()


@code_violations_bp.route('/code-violations')
def code_violations_index():
    context = _load_violations_context(
        city=request.args.get('city', ''),
        violation_type=request.args.get('type', ''),
        status=request.args.get('status', ''),
        q=request.args.get('q', ''),
        page=int(request.args.get('page', 1)),
    )
    return render_template('code_violations.html', **context)


@code_violations_bp.route('/property/<address_slug>')
def property_detail(address_slug):
    conn = _get_db()
    try:
        prop = conn.execute(
            'SELECT * FROM property_addresses WHERE address_slug = ?',
            (address_slug,),
        ).fetchone()
        if not prop:
            abort(404)

        violations = conn.execute(
            '''
            SELECT
                cv.*,
                cvs.display_name AS source_name
            FROM code_violations cv
            LEFT JOIN code_violation_sources cvs ON cv.source_id = cvs.id
            WHERE cv.property_address_id = ?
            ORDER BY cv.date_issued DESC
            ''',
            (prop['id'],),
        ).fetchall()

        # Cross-link: recent jail bookings at same address (naive text match)
        bookings = conn.execute(
            '''
            SELECT id, person_name, county_name, booking_at, charges_summary
            FROM jail_bookings
            WHERE raw_json LIKE ? OR person_name LIKE ?
            ORDER BY booking_at DESC
            LIMIT 10
            ''',
            (f'%"address": "{prop["street"]}%', f'%{prop["street"]}%'),
        ).fetchall()

        # Cross-link: recent records (arrests/incidents) mentioning the street
        records = conn.execute(
            '''
            SELECT id, incident, location, date, county
            FROM records
            WHERE location LIKE ?
            ORDER BY date DESC
            LIMIT 10
            ''',
            (f'%{prop["street"]}%',),
        ).fetchall()

        return render_template(
            'property_detail.html',
            prop=prop,
            violations=violations,
            bookings=bookings,
            records=records,
            page_title=f"{prop['street']}, {prop['city']}, {prop['state']} — Property Violations",
            meta_description=f"Code enforcement violations for {prop['street']}, {prop['city']}, {prop['state']}. View violation history, cross-linked jail bookings, and incident records.",
        )
    finally:
        conn.close()


@code_violations_bp.route('/api/code-violations')
def api_code_violations():
    context = _load_violations_context(
        city=request.args.get('city', ''),
        violation_type=request.args.get('type', ''),
        status=request.args.get('status', ''),
        q=request.args.get('q', ''),
        page=int(request.args.get('page', 1)),
        per_page=min(int(request.args.get('per_page', 50)), 100),
    )
    return jsonify({
        'violations': [dict(r) for r in context['rows']],
        'total': context['total'],
        'page': context['page'],
        'pages': context['pages'],
        'filters': {
            'city': context['city_filter'] or None,
            'type': context['type_filter'] or None,
            'status': context['status_filter'] or None,
            'q': context['q'] or None,
        },
    })


@code_violations_bp.route('/api/property/<address_slug>')
def api_property_detail(address_slug):
    conn = _get_db()
    try:
        prop = conn.execute(
            'SELECT * FROM property_addresses WHERE address_slug = ?',
            (address_slug,),
        ).fetchone()
        if not prop:
            return jsonify({'error': 'Not found'}), 404

        violations = conn.execute(
            '''
            SELECT cv.*, cvs.display_name AS source_name
            FROM code_violations cv
            LEFT JOIN code_violation_sources cvs ON cv.source_id = cvs.id
            WHERE cv.property_address_id = ?
            ORDER BY cv.date_issued DESC
            ''',
            (prop['id'],),
        ).fetchall()

        return jsonify({
            'property': dict(prop),
            'violations': [dict(v) for v in violations],
        })
    finally:
        conn.close()
```

**Step 2: Write `templates/code_violations.html`**

Extend `public_page_base.html` or `base.html`. Include:
- Search input (q)
- Filters: city `<select>`, type `<select>`, status `<select>`
- Table of violations with columns: Address, City, Type, Status, Date Issued, Owner
- Each address links to `/property/<address_slug>`
- Pagination

Keep styling consistent with existing public pages (minimal, high-utility).

**Step 3: Write `templates/property_detail.html`**

Extend `public_page_base.html`. Include:
- Property header: street, city, state, zip
- Violation history table
- Cross-link sections: "Recent Jail Bookings" and "Recent Incidents" (only show if data exists)
- SEO: `page_title`, `meta_description`, structured data (`PropertyValue` or `Place` JSON-LD)

**Step 4: Register blueprint in `app.py`**

Near the top imports, add:

```python
from blueprints.code_violations import register_code_violations_blueprint
```

After other blueprint registrations (~line 11685), add:

```python
register_code_violations_blueprint(app, get_db=get_db)
```

**Step 5: Verify**

Run: `python app.py` (local dev)
Visit: `http://localhost:5000/code-violations`
Expected: page loads, empty state shown gracefully.

**Step 6: Commit**

```bash
git add blueprints/code_violations.py templates/code_violations.html templates/property_detail.html app.py
git commit -m "feat(code-violations): add public blueprint, list, and property detail pages"
```

---

## Task 4: Add admin panel for code violations

**Objective:** Allow admins to view sources, toggle them, and see violation counts.

**Files:**
- Create: `blueprints/admin/code_violations.py`
- Modify: `blueprints/admin/__init__.py` (import and register)

**Step 1: Write admin blueprint module**

```python
from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_login import login_required

admin_code_violations_bp = Blueprint('admin_code_violations', __name__)


def register_admin_code_violations_blueprint(admin_bp, *, get_db):
    admin_bp.register_blueprint(admin_code_violations_bp)


@admin_code_violations_bp.route('/code-violations')
@login_required
def admin_code_violations_dashboard():
    conn = get_db()
    try:
        sources = conn.execute('SELECT * FROM code_violation_sources ORDER BY city').fetchall()
        counts = conn.execute('''
            SELECT source_id, COUNT(*) AS total,
                   SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open_count
            FROM code_violations
            GROUP BY source_id
        ''').fetchall()
        count_map = {r['source_id']: {'total': r['total'], 'open': r['open_count']} for r in counts}
        return render_template('admin_code_violations.html', sources=sources, count_map=count_map)
    finally:
        conn.close()
```

**Step 2: Create `templates/admin_code_violations.html`**

Extend `admin.html` or `base.html`. Show:
- Sources table: name, city, enabled, last success, total violations, open violations
- Toggle enabled button
- Link to `/admin/code-violations/sources/<id>` for detail

**Step 3: Wire into `blueprints/admin/__init__.py`**

Import and call `register_admin_code_violations_blueprint(admin_bp, get_db=get_db)`.

**Step 4: Commit**

```bash
git add blueprints/admin/code_violations.py templates/admin_code_violations.html blueprints/admin/__init__.py
git commit -m "feat(code-violations): add admin dashboard for sources and counts"
```

---

## Task 5: Add embed widget API

**Objective:** Provide a JSON endpoint real estate agents can call to show violations for a given address.

**Files:**
- Modify: `blueprints/code_violations.py`

**Step 1: Add embed endpoint**

```python
@code_violations_bp.route('/api/embed/violations')
def api_embed_violations():
    address_slug = request.args.get('address')
    if not address_slug:
        return jsonify({'error': 'address required'}), 400

    conn = _get_db()
    try:
        prop = conn.execute(
            'SELECT * FROM property_addresses WHERE address_slug = ?',
            (address_slug,),
        ).fetchone()
        if not prop:
            return jsonify({'violations': [], 'property': None})

        violations = conn.execute(
            '''
            SELECT violation_type, status, date_issued, date_resolved, description
            FROM code_violations
            WHERE property_address_id = ?
            ORDER BY date_issued DESC
            ''',
            (prop['id'],),
        ).fetchall()

        return jsonify({
            'property': {
                'street': prop['street'],
                'city': prop['city'],
                'state': prop['state'],
                'zip': prop['zip'],
            },
            'violations': [dict(v) for v in violations],
            'count': len(violations),
            'open_count': sum(1 for v in violations if v['status'] == 'open'),
        })
    finally:
        conn.close()
```

**Step 2: Add CORS header support (if not already global)**

If the site doesn't already set `Access-Control-Allow-Origin` globally, add:

```python
from flask import after_this_request

@code_violations_bp.after_request
def _add_cors(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response
```

**Step 3: Commit**

```bash
git add blueprints/code_violations.py
git commit -m "feat(code-violations): add embed API endpoint for real estate widget"
```

---

## Task 6: Add navigation links and sitemap entries

**Objective:** Surface the new pages to users and search engines.

**Files:**
- Modify: `templates/index.html` (or nav include)
- Modify: `app.py` (sitemap route if present)

**Step 1: Add nav link**

In the public nav (likely in `templates/includes/nav.html` or `public_page_base.html`), add:

```html
<a href="/code-violations">Code Violations</a>
```

**Step 2: Add sitemap entries**

If `app.py` has a `/sitemap.xml` or `/sitemap-*.xml` builder, append `/code-violations` and a sample of `/property/<slug>` URLs (top 100 properties by violation count).

**Step 3: Commit**

```bash
git add templates/ app.py
git commit -m "feat(code-violations): add nav links and sitemap entries"
```

---

## Task 7: Seed initial sources

**Objective:** Pre-populate the five target cities so admins don't have to create them manually.

**Files:**
- Modify: `init_db.py` (add seed function)
- Modify: `init_db.py` (call seed in `init_database`)

**Step 1: Write seed function**

```python
def seed_code_violation_sources(conn: sqlite3.Connection) -> None:
    sources = [
        ('billings', 'Billings Code Enforcement', 'Billings', 'Yellowstone'),
        ('missoula', 'Missoula Code Enforcement', 'Missoula', 'Missoula'),
        ('great_falls', 'Great Falls Code Enforcement', 'Great Falls', 'Cascade'),
        ('bozeman', 'Bozeman Code Enforcement', 'Bozeman', 'Gallatin'),
        ('helena', 'Helena Code Enforcement', 'Helena', 'Lewis and Clark'),
    ]
    for key, name, city, county in sources:
        conn.execute(
            '''
            INSERT OR IGNORE INTO code_violation_sources (source_key, display_name, city, county)
            VALUES (?, ?, ?, ?)
            ''',
            (key, name, city, county),
        )
    conn.commit()
```

**Step 2: Call in `init_database()`**

After `ensure_code_violation_schema(conn)`:

```python
    seed_code_violation_sources(conn)
```

**Step 3: Commit**

```bash
git add init_db.py
git commit -m "feat(code-violations): seed initial city sources"
```

---

## Task 8: Run full test suite and verify

**Objective:** Ensure nothing is broken.

**Step 1: Run tests**

```bash
source venv/bin/activate
python -m pytest tests/test_code_violation_ingest.py -v
python -m pytest tests/ -v --tb=short
```

Expected: all pass.

**Step 2: Run lint / type check (if configured)**

```bash
python -m py_compile blueprints/code_violations.py blueprints/admin/code_violations.py code_violation_ingest.py
```

Expected: no syntax errors.

**Step 3: Commit**

```bash
git commit -m "test(code-violations): verify suite passes"
```

---

## Summary of New Files

| File | Purpose |
|------|---------|
| `code_violation_ingest.py` | Ingestion worker (PDF/Excel/CSV/JSON) |
| `blueprints/code_violations.py` | Public routes: `/code-violations`, `/property/<slug>`, API |
| `blueprints/admin/code_violations.py` | Admin dashboard for sources |
| `templates/code_violations.html` | Searchable violation list |
| `templates/property_detail.html` | Address permalink with cross-links |
| `templates/admin_code_violations.html` | Admin source management |
| `tests/test_code_violation_ingest.py` | Unit tests for ingestion |

## Modified Files

| File | Change |
|------|--------|
| `init_db.py` | Schema + seed sources |
| `app.py` | Register blueprint |
| `blueprints/admin/__init__.py` | Register admin blueprint |
| `templates/` nav / sitemap | Add links |
