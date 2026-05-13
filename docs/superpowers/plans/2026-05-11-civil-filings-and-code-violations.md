# Civil Filings And Code Violations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a civil-filings ingestion and public search surface for evictions and related filings, then link those filings into the existing property and code-violations views.

**Architecture:** Extend the existing `code_violations` and `property_addresses` stack instead of adding a parallel subsystem. Phase 1 adds normalized schema, import-based ingestion, `/eviction-records`, and property aggregation; phase 2 adds an `iCourtCase` adapter that feeds the same civil-filings ingester with county-scoped checkpointing.

**Tech Stack:** Flask, SQLite, Jinja templates, existing `init_db.py` migrations, Python unittest, existing ingestion service patterns in `services/ingestion/`.

---

## File Structure

### Existing files to modify

- `init_db.py`
  Add civil filing tables, indexes, additive migration columns, and source seeding helpers in the same style as `ensure_code_violation_schema`.

- `blueprints/code_violations.py`
  Keep property-detail ownership here. Add `/eviction-records`, civil-filing query helpers, property-level civil filing queries, and summary-rollup context.

- `templates/property_detail.html`
  Extend the existing property page with civil filings and a compact risk summary.

- `services/ingestion/code_violations.py`
  Reuse or extract address normalization logic so both code violations and civil filings converge on the same `property_addresses` behavior. Add source run metadata consistency.

- `tests/test_code_violation_ingest.py`
  Expand around shared address linking and source metadata updates after the helper extraction.

### New files to create

- `services/ingestion/property_addresses.py`
  Shared helpers for address slugging, best-effort parsing, and `property_addresses` upsert logic.

- `services/ingestion/civil_filings.py`
  Main normalized civil-filings ingester: classification, hashing, source upsert, address linking, and idempotent insert/update behavior.

- `services/ingestion/civil_filings_import.py`
  JSON/CSV import CLI that loads normalized records and passes them to the shared ingester.

- `services/ingestion/icourtcase_civil.py`
  Phase 2 adapter with county-scoped checkpointing and normalization into the shared ingester.

- `templates/eviction_records.html`
  Public search page for eviction and restraining-order filings.

- `tests/test_civil_filing_ingest.py`
  Schema, classification, idempotency, and import-adapter behavior.

- `tests/test_property_intelligence_routes.py`
  Route coverage for `/eviction-records` and `/property/<address_slug>` with linked civil filings and summary counts.

- `tests/fixtures/icourtcase_civil_search.html`
  Captured fixture for parser behavior in the phase 2 adapter.

## Task 1: Add Civil Filing Schema

**Files:**
- Modify: `init_db.py`
- Test: `tests/test_civil_filing_ingest.py`

- [ ] **Step 1: Write the failing schema test**

```python
import os
import sqlite3
import tempfile
import unittest

from init_db import ensure_code_violation_schema, ensure_civil_filing_schema


class TestCivilFilingSchema(unittest.TestCase):
    def setUp(self):
        self.db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.conn = sqlite3.connect(self.db.name)
        self.conn.row_factory = sqlite3.Row

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db.name)

    def test_ensure_civil_filing_schema_creates_tables(self):
        ensure_code_violation_schema(self.conn)
        ensure_civil_filing_schema(self.conn)

        tables = {
            row["name"]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

        self.assertIn("civil_filing_sources", tables)
        self.assertIn("civil_filings", tables)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest /root/montanablotter/tests/test_civil_filing_ingest.py::TestCivilFilingSchema::test_ensure_civil_filing_schema_creates_tables -v`
Expected: FAIL with `ImportError` or `AttributeError` because `ensure_civil_filing_schema` does not exist yet.

- [ ] **Step 3: Write minimal schema implementation**

```python
def ensure_civil_filing_schema(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS civil_filing_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_key TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL,
            adapter_type TEXT NOT NULL DEFAULT 'import_json',
            jurisdiction TEXT NOT NULL DEFAULT 'Montana',
            county TEXT,
            source_url TEXT,
            is_enabled INTEGER NOT NULL DEFAULT 1,
            last_success_at TEXT,
            last_error TEXT DEFAULT '',
            last_run_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        '''
    )

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS civil_filings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            property_address_id INTEGER,
            county TEXT NOT NULL,
            city TEXT,
            case_number TEXT NOT NULL,
            case_type_code TEXT,
            case_type_label TEXT,
            filing_class TEXT NOT NULL DEFAULT 'other',
            caption TEXT,
            plaintiff_name TEXT,
            defendant_name TEXT,
            raw_address TEXT DEFAULT '',
            filing_date TEXT,
            case_status TEXT,
            source_record_id TEXT,
            source_url TEXT,
            raw_json TEXT,
            hash_id TEXT UNIQUE,
            first_seen_at TEXT DEFAULT (datetime('now')),
            last_seen_at TEXT DEFAULT (datetime('now')),
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (source_id) REFERENCES civil_filing_sources(id) ON DELETE CASCADE,
            FOREIGN KEY (property_address_id) REFERENCES property_addresses(id) ON DELETE SET NULL
        )
        '''
    )

    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_civil_filings_lookup '
        'ON civil_filings(property_address_id, filing_class, filing_date)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_civil_filings_case '
        'ON civil_filings(county, case_number)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_civil_filings_hash '
        'ON civil_filings(hash_id)'
    )
```

- [ ] **Step 4: Wire schema into the existing database bootstrap**

```python
def init_database():
    conn = sqlite3.connect(DB_PATH)
    try:
        _create_core_tables(conn.cursor())
        ensure_code_violation_schema(conn)
        ensure_civil_filing_schema(conn)
        seed_code_violation_sources(conn)
        seed_civil_filing_sources(conn)
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 5: Run tests to verify schema passes**

Run: `pytest /root/montanablotter/tests/test_civil_filing_ingest.py::TestCivilFilingSchema::test_ensure_civil_filing_schema_creates_tables -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git -C /root/montanablotter add init_db.py tests/test_civil_filing_ingest.py
git -C /root/montanablotter commit -m "feat: add civil filing schema"
```

## Task 2: Add Shared Property Address Helpers

**Files:**
- Create: `services/ingestion/property_addresses.py`
- Modify: `services/ingestion/code_violations.py`
- Test: `tests/test_code_violation_ingest.py`

- [ ] **Step 1: Write the failing helper extraction test**

```python
from services.ingestion.property_addresses import slugify_address, parse_address_parts


def test_parse_address_parts_extracts_zip():
    parsed = parse_address_parts("456 Oak Ave, Billings, MT 59101", fallback_city="Billings")
    assert parsed["street"] == "456 Oak Ave, Billings, MT"
    assert parsed["zip_code"] == "59101"
    assert parsed["city"] == "Billings"


def test_slugify_address_matches_existing_behavior():
    assert slugify_address("123 Main St", "Missoula", "MT", "59801") == "123-main-st-missoula-mt-59801"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest /root/montanablotter/tests/test_code_violation_ingest.py -k "slugify or parse_address_parts" -v`
Expected: FAIL because `services.ingestion.property_addresses` does not exist.

- [ ] **Step 3: Create the shared helper module**

```python
import re
import sqlite3


def slugify_address(street: str, city: str, state: str = 'MT', zip_code: str = '') -> str:
    parts = [street, city, state, zip_code]
    raw = ' '.join(p for p in parts if p)
    slug = raw.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_-]+", "-", slug)
    return slug.strip("-") or 'unknown'


def parse_address_parts(raw_address: str, fallback_city: str = '', fallback_state: str = 'MT') -> dict[str, str]:
    value = (raw_address or '').strip()
    zip_code = ''
    match = re.search(r'\b(\d{5}(?:-\d{4})?)\s*$', value)
    if match:
        zip_code = match.group(1)
        value = value[:match.start()].strip().rstrip(',').strip()
    return {
        'street': value,
        'city': fallback_city,
        'state': fallback_state,
        'zip_code': zip_code,
    }


def ensure_property_address(
    conn: sqlite3.Connection,
    *,
    street: str,
    city: str,
    state: str = 'MT',
    zip_code: str = '',
    county: str = '',
) -> int:
    slug = slugify_address(street, city, state, zip_code)
    row = conn.execute(
        'SELECT id FROM property_addresses WHERE address_slug = ?',
        (slug,),
    ).fetchone()
    if row:
        conn.execute(
            'UPDATE property_addresses SET last_seen_at = datetime("now") WHERE id = ?',
            (row['id'],),
        )
        return row['id']
    cur = conn.execute(
        '''
        INSERT INTO property_addresses (address_slug, street, city, state, zip, county)
        VALUES (?, ?, ?, ?, ?, ?)
        ''',
        (slug, street, city, state, zip_code, county),
    )
    return cur.lastrowid
```

- [ ] **Step 4: Refactor code violations to use the shared helper**

```python
from services.ingestion.property_addresses import ensure_property_address, parse_address_parts, slugify_address


parts = parse_address_parts(raw_address, fallback_city=city)
property_address_id = ensure_property_address(
    conn,
    street=parts['street'],
    city=parts['city'],
    state=parts['state'],
    zip_code=parts['zip_code'],
)
```

- [ ] **Step 5: Run code-violation tests**

Run: `pytest /root/montanablotter/tests/test_code_violation_ingest.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git -C /root/montanablotter add services/ingestion/property_addresses.py services/ingestion/code_violations.py tests/test_code_violation_ingest.py
git -C /root/montanablotter commit -m "refactor: share property address ingestion helpers"
```

## Task 3: Build Civil Filing Ingestion

**Files:**
- Create: `services/ingestion/civil_filings.py`
- Test: `tests/test_civil_filing_ingest.py`

- [ ] **Step 1: Write failing ingestion and classification tests**

```python
from services.ingestion.civil_filings import classify_civil_filing, ingest_civil_filings


def test_classify_civil_filing_maps_known_case_codes():
    assert classify_civil_filing(case_type_code='UD', caption='ABC v. Doe') == 'eviction'
    assert classify_civil_filing(case_type_code='DV', caption='Order of Protection') == 'restraining_order'
    assert classify_civil_filing(case_type_code='CC', caption='Money judgment') == 'civil_judgment'
    assert classify_civil_filing(case_type_code='', caption='Construction Lien Notice') == 'lien'


def test_ingest_civil_filings_creates_rows_and_property_link(self):
    records = [{
        'county': 'Yellowstone',
        'city': 'Billings',
        'case_number': 'UD-26-1234',
        'case_type_code': 'UD',
        'caption': 'ABC Properties LLC v. Jane Doe',
        'plaintiff_name': 'ABC Properties LLC',
        'defendant_name': 'Jane Doe',
        'address': '123 Main St, Billings, MT 59101',
        'filing_date': '2026-05-11',
        'case_status': 'Open',
    }]
    result = ingest_civil_filings(
        self.conn,
        source_key='import-yellowstone',
        display_name='Yellowstone Import',
        adapter_type='import_json',
        county='Yellowstone',
        records=records,
    )
    self.assertEqual(result['inserted'], 1)
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest /root/montanablotter/tests/test_civil_filing_ingest.py -k "classify or ingest" -v`
Expected: FAIL because the ingester does not exist.

- [ ] **Step 3: Write the minimal ingester**

```python
def classify_civil_filing(*, case_type_code: str = '', caption: str = '', case_type_label: str = '') -> str:
    code = (case_type_code or '').strip().upper()
    haystack = ' '.join([(caption or ''), (case_type_label or '')]).lower()
    if code == 'UD':
        return 'eviction'
    if code == 'DV':
        return 'restraining_order'
    if 'lien' in haystack:
        return 'lien'
    if code == 'CC':
        return 'civil_judgment'
    return 'other'


def civil_filing_hash(source_id: int, case_number: str, filing_date: str, filing_class: str) -> str:
    payload = f'{source_id}|{case_number}|{filing_date}|{filing_class}'
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def ingest_civil_filings(conn, *, source_key, display_name, adapter_type, county, records, source_url=''):
    source_id = ensure_civil_filing_source(
        conn,
        source_key=source_key,
        display_name=display_name,
        adapter_type=adapter_type,
        county=county,
        source_url=source_url,
    )
    inserted = updated = 0
    for rec in records:
        filing_class = classify_civil_filing(
            case_type_code=rec.get('case_type_code', ''),
            caption=rec.get('caption', ''),
            case_type_label=rec.get('case_type_label', ''),
        )
        raw_address = (rec.get('address') or '').strip()
        property_address_id = None
        if raw_address:
            parts = parse_address_parts(raw_address, fallback_city=(rec.get('city') or '').strip())
            property_address_id = ensure_property_address(
                conn,
                street=parts['street'],
                city=parts['city'],
                state=parts['state'],
                zip_code=parts['zip_code'],
                county=(rec.get('county') or county or '').strip(),
            )
        hash_id = civil_filing_hash(source_id, rec['case_number'], rec.get('filing_date') or '', filing_class)
        existing = conn.execute('SELECT id FROM civil_filings WHERE hash_id = ?', (hash_id,)).fetchone()
        if existing:
            conn.execute(
                '''
                UPDATE civil_filings
                SET property_address_id = ?, case_status = ?, raw_address = ?, last_seen_at = datetime('now'), updated_at = datetime('now')
                WHERE id = ?
                ''',
                (property_address_id, rec.get('case_status'), raw_address, existing['id']),
            )
            updated += 1
        else:
            conn.execute(
                '''
                INSERT INTO civil_filings (
                    source_id, property_address_id, county, city, case_number, case_type_code, case_type_label,
                    filing_class, caption, plaintiff_name, defendant_name, raw_address, filing_date,
                    case_status, source_record_id, source_url, raw_json, hash_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    source_id, property_address_id, rec.get('county') or county, rec.get('city'),
                    rec['case_number'], rec.get('case_type_code'), rec.get('case_type_label'),
                    filing_class, rec.get('caption'), rec.get('plaintiff_name'), rec.get('defendant_name'),
                    raw_address, rec.get('filing_date'), rec.get('case_status'), rec.get('source_record_id'),
                    rec.get('source_url'), json.dumps(rec), hash_id,
                ),
            )
            inserted += 1
    conn.execute(
        'UPDATE civil_filing_sources SET last_success_at = datetime("now"), last_run_count = ?, updated_at = datetime("now") WHERE id = ?',
        (len(records), source_id),
    )
    conn.commit()
    return {'inserted': inserted, 'updated': updated, 'source_id': source_id}
```

- [ ] **Step 4: Add idempotency and last-run metadata tests**

```python
def test_reingest_updates_instead_of_duplicate_insert(self):
    records = [{
        'county': 'Lewis and Clark',
        'city': 'Helena',
        'case_number': 'DV-26-11',
        'case_type_code': 'DV',
        'caption': 'State v. Doe',
        'filing_date': '2026-05-10',
        'case_status': 'Open',
    }]
    ingest_civil_filings(self.conn, source_key='helena', display_name='Helena', adapter_type='import_json', county='Lewis and Clark', records=records)
    result = ingest_civil_filings(self.conn, source_key='helena', display_name='Helena', adapter_type='import_json', county='Lewis and Clark', records=records)
    self.assertEqual(result['updated'], 1)
    self.assertEqual(result['inserted'], 0)
```

- [ ] **Step 5: Run the ingestion test file**

Run: `pytest /root/montanablotter/tests/test_civil_filing_ingest.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git -C /root/montanablotter add services/ingestion/civil_filings.py tests/test_civil_filing_ingest.py
git -C /root/montanablotter commit -m "feat: add civil filing ingestion service"
```

## Task 4: Add JSON/CSV Civil Filing Import CLI

**Files:**
- Create: `services/ingestion/civil_filings_import.py`
- Test: `tests/test_civil_filing_ingest.py`

- [ ] **Step 1: Write the failing import-parser tests**

```python
from services.ingestion.civil_filings_import import parse_import_file


def test_parse_import_file_reads_json_records(tmp_path):
    payload = tmp_path / 'civil.json'
    payload.write_text('[{"county":"Yellowstone","case_number":"UD-1"}]', encoding='utf-8')
    rows = parse_import_file(str(payload), file_format='json')
    assert rows[0]['case_number'] == 'UD-1'


def test_parse_import_file_reads_csv_records(tmp_path):
    payload = tmp_path / 'civil.csv'
    payload.write_text('county,case_number\\nYellowstone,UD-1\\n', encoding='utf-8')
    rows = parse_import_file(str(payload), file_format='csv')
    assert rows[0]['county'] == 'Yellowstone'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest /root/montanablotter/tests/test_civil_filing_ingest.py -k "parse_import_file" -v`
Expected: FAIL because the import CLI module does not exist.

- [ ] **Step 3: Implement the import helper and CLI**

```python
def parse_import_file(file_path: str, *, file_format: str | None = None) -> list[dict[str, Any]]:
    ext = (file_format or os.path.splitext(file_path)[1].lstrip('.').lower())
    if ext == 'json':
        with open(file_path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        return data['records'] if isinstance(data, dict) and 'records' in data else data
    if ext == 'csv':
        with open(file_path, newline='', encoding='utf-8') as handle:
            return list(csv.DictReader(handle))
    raise ValueError(f'Unsupported format: {ext}')


def main() -> None:
    parser = argparse.ArgumentParser(description='Import civil filing records')
    parser.add_argument('--source', required=True)
    parser.add_argument('--display-name', required=True)
    parser.add_argument('--county', required=True)
    parser.add_argument('--file', required=True)
    parser.add_argument('--format', choices=['json', 'csv'])
    args = parser.parse_args()

    rows = parse_import_file(args.file, file_format=args.format)
    conn = connect_db()
    try:
        result = ingest_civil_filings(
            conn,
            source_key=args.source,
            display_name=args.display_name,
            adapter_type=f'import_{args.format or "json"}',
            county=args.county,
            records=rows,
        )
        print(f"Inserted: {result['inserted']}, Updated: {result['updated']}")
    finally:
        conn.close()
```

- [ ] **Step 4: Add a smoke test for the CLI helper path**

```python
def test_parse_import_file_rejects_unknown_format(tmp_path):
    payload = tmp_path / 'civil.txt'
    payload.write_text('nope', encoding='utf-8')
    with pytest.raises(ValueError):
        parse_import_file(str(payload), file_format='txt')
```

- [ ] **Step 5: Run the import-related tests**

Run: `pytest /root/montanablotter/tests/test_civil_filing_ingest.py -k "parse_import_file" -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git -C /root/montanablotter add services/ingestion/civil_filings_import.py tests/test_civil_filing_ingest.py
git -C /root/montanablotter commit -m "feat: add civil filing import cli"
```

## Task 5: Add `/eviction-records` Route And Template

**Files:**
- Modify: `blueprints/code_violations.py`
- Create: `templates/eviction_records.html`
- Test: `tests/test_property_intelligence_routes.py`

- [ ] **Step 1: Write the failing route test**

```python
def test_eviction_records_route_renders_seeded_filing(self):
    conn = app_module.get_db()
    source_id = conn.execute(
        "INSERT INTO civil_filing_sources (source_key, display_name, adapter_type) VALUES (?, ?, ?)",
        ('yellowstone-import', 'Yellowstone Import', 'import_json'),
    ).lastrowid
    conn.execute(
        '''
        INSERT INTO civil_filings (
            source_id, county, city, case_number, case_type_code, filing_class, caption,
            plaintiff_name, defendant_name, raw_address, filing_date, case_status, hash_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            source_id, 'Yellowstone', 'Billings', 'UD-26-1234', 'UD', 'eviction',
            'ABC Properties LLC v. Jane Doe', 'ABC Properties LLC', 'Jane Doe',
            '123 Main St, Billings, MT 59101', '2026-05-11', 'Open', 'hash-1',
        ),
    )
    conn.commit()
    conn.close()

    response = app_module.app.test_client().get('/eviction-records')
    html = response.get_data(as_text=True)
    self.assertEqual(response.status_code, 200)
    self.assertIn('Eviction Records', html)
    self.assertIn('UD-26-1234', html)
    self.assertIn('ABC Properties LLC', html)
```

- [ ] **Step 2: Run the route test to verify failure**

Run: `pytest /root/montanablotter/tests/test_property_intelligence_routes.py::PropertyIntelligenceRouteTests::test_eviction_records_route_renders_seeded_filing -v`
Expected: FAIL with 404 because `/eviction-records` does not exist yet.

- [ ] **Step 3: Add the query helper and route**

```python
def _load_civil_filings_context(*, county: str = '', city: str = '', filing_class: str = '', q: str = '', page: int = 1, per_page: int = 50):
    conn = _get_db()
    try:
        where_clauses = ["cf.filing_class IN ('eviction', 'restraining_order')"]
        params: list[str] = []
        if county:
            where_clauses.append('cf.county = ?')
            params.append(county)
        if city:
            where_clauses.append('COALESCE(cf.city, pa.city) = ?')
            params.append(city)
        if filing_class:
            where_clauses.append('cf.filing_class = ?')
            params.append(filing_class)
        if q:
            like = f'%{q}%'
            where_clauses.append('(cf.case_number LIKE ? OR cf.caption LIKE ? OR cf.plaintiff_name LIKE ? OR cf.defendant_name LIKE ?)')
            params.extend([like, like, like, like])

        rows = conn.execute(
            f'''
            SELECT cf.*, pa.address_slug, pa.street, pa.city AS property_city
            FROM civil_filings cf
            LEFT JOIN property_addresses pa ON cf.property_address_id = pa.id
            WHERE {' AND '.join(where_clauses)}
            ORDER BY cf.filing_date DESC, cf.id DESC
            LIMIT ? OFFSET ?
            ''',
            params + [per_page, (page - 1) * per_page],
        ).fetchall()
        return {'rows': rows, 'q': q, 'county_filter': county, 'city_filter': city, 'class_filter': filing_class, 'page': page}
    finally:
        conn.close()


@code_violations_bp.route('/eviction-records')
def eviction_records_index():
    context = _load_civil_filings_context(
        county=request.args.get('county', ''),
        city=request.args.get('city', ''),
        filing_class=request.args.get('class', ''),
        q=request.args.get('q', ''),
        page=int(request.args.get('page', 1)),
    )
    return render_template('eviction_records.html', **context)
```

- [ ] **Step 4: Create the initial template**

```html
{% extends "public_page_base.html" %}
{% block content %}
<section class="max-w-7xl mx-auto px-4 sm:px-6 py-8">
  <div class="mb-6">
    <h1 class="text-3xl font-bold text-slate-900">Eviction Records</h1>
    <p class="text-sm text-slate-600 mt-2">Search public Montana civil filings linked to evictions and protection-order activity.</p>
  </div>
  <div class="space-y-4">
    {% for row in rows %}
    <article class="rounded-xl border border-slate-200 p-5 bg-white">
      <div class="flex items-start justify-between gap-4">
        <div>
          <p class="text-xs uppercase tracking-wide text-slate-500">{{ row.filing_class.replace('_', ' ') }}</p>
          <h2 class="text-lg font-semibold text-slate-900">{{ row.case_number }}</h2>
          <p class="text-sm text-slate-700">{{ row.plaintiff_name or 'Unknown plaintiff' }} v. {{ row.defendant_name or 'Unknown defendant' }}</p>
          {% if row.address_slug %}
          <p class="text-sm mt-2"><a href="/property/{{ row.address_slug }}" class="text-slate-900 hover:underline">{{ row.street or row.raw_address }}</a></p>
          {% elif row.raw_address %}
          <p class="text-sm mt-2 text-slate-700">{{ row.raw_address }}</p>
          {% endif %}
        </div>
        <div class="text-right text-sm text-slate-500">
          <p>{{ row.filing_date or '' }}</p>
          <p>{{ row.county }}</p>
          <p>{{ row.case_status or '' }}</p>
        </div>
      </div>
    </article>
    {% else %}
    <p class="text-sm text-slate-600">No matching civil filings found.</p>
    {% endfor %}
  </div>
</section>
{% endblock %}
```

- [ ] **Step 5: Run the new route test**

Run: `pytest /root/montanablotter/tests/test_property_intelligence_routes.py::PropertyIntelligenceRouteTests::test_eviction_records_route_renders_seeded_filing -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git -C /root/montanablotter add blueprints/code_violations.py templates/eviction_records.html tests/test_property_intelligence_routes.py
git -C /root/montanablotter commit -m "feat: add eviction records public page"
```

## Task 6: Extend Property Detail With Civil Filings And Summary Rollups

**Files:**
- Modify: `blueprints/code_violations.py`
- Modify: `templates/property_detail.html`
- Test: `tests/test_property_intelligence_routes.py`

- [ ] **Step 1: Write the failing property-detail test**

```python
def test_property_detail_shows_civil_filings_and_rollup(self):
    conn = app_module.get_db()
    property_id = conn.execute(
        "INSERT INTO property_addresses (address_slug, street, city, state, zip, county) VALUES (?, ?, ?, ?, ?, ?)",
        ('123-main-st-billings-mt-59101', '123 Main St', 'Billings', 'MT', '59101', 'Yellowstone'),
    ).lastrowid
    source_id = conn.execute(
        "INSERT INTO civil_filing_sources (source_key, display_name, adapter_type) VALUES (?, ?, ?)",
        ('yellowstone-import', 'Yellowstone Import', 'import_json'),
    ).lastrowid
    conn.execute(
        '''
        INSERT INTO civil_filings (
            source_id, property_address_id, county, city, case_number, filing_class,
            plaintiff_name, defendant_name, raw_address, filing_date, case_status, hash_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            source_id, property_id, 'Yellowstone', 'Billings', 'UD-26-1234', 'eviction',
            'ABC Properties LLC', 'Jane Doe', '123 Main St, Billings, MT 59101',
            '2026-05-11', 'Open', 'civil-hash-1',
        ),
    )
    conn.commit()
    conn.close()

    response = app_module.app.test_client().get('/property/123-main-st-billings-mt-59101')
    html = response.get_data(as_text=True)
    self.assertEqual(response.status_code, 200)
    self.assertIn('Civil Filings', html)
    self.assertIn('3 civil filings', html.replace('\\n', ' '))
    self.assertIn('ABC Properties LLC', html)
```

- [ ] **Step 2: Run the property-detail test to verify failure**

Run: `pytest /root/montanablotter/tests/test_property_intelligence_routes.py::PropertyIntelligenceRouteTests::test_property_detail_shows_civil_filings_and_rollup -v`
Expected: FAIL because the property page does not query or render civil filings yet.

- [ ] **Step 3: Add property-level civil filing queries and summary counts**

```python
civil_filings = conn.execute(
    '''
    SELECT cf.*, cfs.display_name AS source_name
    FROM civil_filings cf
    LEFT JOIN civil_filing_sources cfs ON cf.source_id = cfs.id
    WHERE cf.property_address_id = ?
    ORDER BY cf.filing_date DESC, cf.id DESC
    ''',
    (prop['id'],),
).fetchall()

summary = {
    'civil_filings': len(civil_filings),
    'code_violations': len(violations),
    'arrest_linked_records': len(records),
}

return render_template(
    'property_detail.html',
    prop=prop,
    violations=violations,
    civil_filings=civil_filings,
    bookings=bookings,
    records=records,
    summary=summary,
    page_title=f"{prop['street']}, {prop['city']}, {prop['state']} — Property Intelligence",
    meta_description=f"Property intelligence for {prop['street']}, {prop['city']}, {prop['state']} including code violations, civil filings, jail bookings, and incident records.",
)
```

- [ ] **Step 4: Render the summary and civil filings block**

```html
<div class="rounded-xl border border-slate-200 p-5">
  <h3 class="text-sm font-semibold text-slate-900 uppercase tracking-wide">Property Summary</h3>
  <div class="mt-3 grid grid-cols-1 gap-2 text-sm">
    <div class="flex justify-between"><span class="text-slate-500">Civil filings</span><span class="text-slate-900">{{ summary.civil_filings }}</span></div>
    <div class="flex justify-between"><span class="text-slate-500">Code violations</span><span class="text-slate-900">{{ summary.code_violations }}</span></div>
    <div class="flex justify-between"><span class="text-slate-500">Arrest-linked records</span><span class="text-slate-900">{{ summary.arrest_linked_records }}</span></div>
  </div>
</div>

<div class="rounded-xl border border-slate-200 p-5">
  <h2 class="text-lg font-semibold text-slate-900 mb-3">Civil Filings</h2>
  {% if civil_filings %}
  <div class="space-y-3">
    {% for row in civil_filings %}
    <div class="rounded-lg border border-slate-100 bg-slate-50 px-4 py-3">
      <p class="text-sm font-medium text-slate-900">{{ row.case_number }} · {{ row.filing_class.replace('_', ' ') }}</p>
      <p class="text-xs text-slate-600 mt-1">{{ row.plaintiff_name or 'Unknown plaintiff' }} v. {{ row.defendant_name or 'Unknown defendant' }}</p>
      <p class="text-xs text-slate-500 mt-1">{{ row.filing_date or '' }} · {{ row.case_status or '' }} · {{ row.source_name or 'Unknown source' }}</p>
    </div>
    {% endfor %}
  </div>
  {% else %}
  <p class="text-sm text-slate-600">No civil filings on record for this address.</p>
  {% endif %}
</div>
```

- [ ] **Step 5: Run the route test file**

Run: `pytest /root/montanablotter/tests/test_property_intelligence_routes.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git -C /root/montanablotter add blueprints/code_violations.py templates/property_detail.html tests/test_property_intelligence_routes.py
git -C /root/montanablotter commit -m "feat: link civil filings into property detail"
```

## Task 7: Harden Code Violation Source Metadata

**Files:**
- Modify: `services/ingestion/code_violations.py`
- Modify: `tests/test_code_violation_ingest.py`

- [ ] **Step 1: Write the failing metadata test**

```python
def test_ingest_updates_source_run_metadata(self):
    records = [{
        'address': '456 Oak Ave, Billings, MT 59101',
        'violation_type': 'Abandoned Vehicle',
        'status': 'open',
        'date_issued': '2026-04-01',
    }]
    ingest_records(
        self.conn,
        source_key='billings',
        display_name='Billings Code Enforcement',
        city='Billings',
        records=records,
    )
    source = self.conn.execute(
        'SELECT last_success_at, latest_error FROM code_violation_sources WHERE source_key = ?',
        ('billings',),
    ).fetchone()
    self.assertIsNotNone(source['last_success_at'])
    self.assertEqual(source['latest_error'], '')
```

- [ ] **Step 2: Run the source metadata test**

Run: `pytest /root/montanablotter/tests/test_code_violation_ingest.py::TestCodeViolationIngest::test_ingest_updates_source_run_metadata -v`
Expected: FAIL if source metadata is not reset or updated consistently.

- [ ] **Step 3: Update ingestion metadata handling**

```python
conn.execute(
    '''
    UPDATE code_violation_sources
    SET last_success_at = datetime("now"),
        latest_error = '',
        updated_at = datetime("now")
    WHERE id = ?
    ''',
    (source_id,),
)
```

- [ ] **Step 4: Add an explicit failure-path helper for later parser integrations**

```python
def mark_source_failure(conn: sqlite3.Connection, source_id: int, error_message: str) -> None:
    conn.execute(
        '''
        UPDATE code_violation_sources
        SET latest_error = ?, updated_at = datetime("now")
        WHERE id = ?
        ''',
        (error_message[:500], source_id),
    )
    conn.commit()
```

- [ ] **Step 5: Run the full code-violation ingestion tests**

Run: `pytest /root/montanablotter/tests/test_code_violation_ingest.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git -C /root/montanablotter add services/ingestion/code_violations.py tests/test_code_violation_ingest.py
git -C /root/montanablotter commit -m "refactor: normalize code violation source metadata"
```

## Task 8: Add `iCourtCase` Adapter Skeleton With Fixtures

**Files:**
- Create: `services/ingestion/icourtcase_civil.py`
- Create: `tests/fixtures/icourtcase_civil_search.html`
- Modify: `tests/test_civil_filing_ingest.py`

- [ ] **Step 1: Write the failing parser and checkpoint tests**

```python
from pathlib import Path

from services.ingestion.icourtcase_civil import parse_icourtcase_search_results, default_checkpoint


def test_parse_icourtcase_search_results_extracts_case_rows():
    html = Path('/root/montanablotter/tests/fixtures/icourtcase_civil_search.html').read_text(encoding='utf-8')
    rows = parse_icourtcase_search_results(html, county='Yellowstone')
    assert rows[0]['case_number'] == 'UD-26-1234'
    assert rows[0]['case_type_code'] == 'UD'
    assert rows[0]['county'] == 'Yellowstone'


def test_default_checkpoint_is_county_scoped():
    checkpoint = default_checkpoint(county='Yellowstone', date_from='2026-05-01', page=3)
    assert checkpoint == {
        'county': 'Yellowstone',
        'date_from': '2026-05-01',
        'page': 3,
    }
```

- [ ] **Step 2: Run the adapter tests to verify failure**

Run: `pytest /root/montanablotter/tests/test_civil_filing_ingest.py -k "icourtcase or checkpoint" -v`
Expected: FAIL because the adapter module and fixture do not exist.

- [ ] **Step 3: Add a captured fixture and parser**

```python
def parse_icourtcase_search_results(html_text: str, *, county: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html_text, 'html.parser')
    rows = []
    for tr in soup.select('table tr[data-case-number]'):
        rows.append({
            'county': county,
            'city': tr.get('data-city', ''),
            'case_number': tr.get('data-case-number', ''),
            'case_type_code': tr.get('data-case-type', ''),
            'case_type_label': tr.get('data-case-type-label', ''),
            'caption': tr.get('data-caption', ''),
            'filing_date': tr.get('data-filing-date', ''),
            'case_status': tr.get('data-status', ''),
            'address': tr.get('data-address', ''),
            'source_url': tr.get('data-url', ''),
        })
    return rows


def default_checkpoint(*, county: str, date_from: str, page: int = 1) -> dict[str, str | int]:
    return {'county': county, 'date_from': date_from, 'page': page}
```

- [ ] **Step 4: Add the adapter runner boundary**

```python
def run_county_import(conn, *, county: str, date_from: str, html_text: str, page: int = 1) -> dict[str, int]:
    checkpoint = default_checkpoint(county=county, date_from=date_from, page=page)
    records = parse_icourtcase_search_results(html_text, county=county)
    result = ingest_civil_filings(
        conn,
        source_key=f'icourtcase-{county.lower().replace(" ", "-")}',
        display_name=f'iCourtCase {county}',
        adapter_type='icourtcase',
        county=county,
        records=records,
        source_url='https://icourtcase.mt.gov/',
    )
    result['checkpoint_page'] = checkpoint['page']
    return result
```

- [ ] **Step 5: Run the phase 2 adapter tests**

Run: `pytest /root/montanablotter/tests/test_civil_filing_ingest.py -k "icourtcase or checkpoint" -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git -C /root/montanablotter add services/ingestion/icourtcase_civil.py tests/fixtures/icourtcase_civil_search.html tests/test_civil_filing_ingest.py
git -C /root/montanablotter commit -m "feat: add icourtcase civil adapter skeleton"
```

## Verification Checklist

- Run: `pytest /root/montanablotter/tests/test_civil_filing_ingest.py -v`
  Expected: all civil filing schema, ingestion, import, and adapter tests pass.

- Run: `pytest /root/montanablotter/tests/test_code_violation_ingest.py -v`
  Expected: shared property helper and code-violation ingestion tests pass.

- Run: `pytest /root/montanablotter/tests/test_property_intelligence_routes.py -v`
  Expected: `/eviction-records` and property-detail integration tests pass.

- Run: `pytest /root/montanablotter/tests/test_public_detail_routes.py -v`
  Expected: no regressions in public detail route rendering patterns.

## Spec Coverage Check

- Civil filing schema and source metadata: Task 1
- Shared address normalization and linking: Task 2
- Deterministic classification and idempotent ingestion: Task 3
- File import adapter for phase 1: Task 4
- `/eviction-records` public surface: Task 5
- `/property/<address-slug>` aggregation and summary rollups: Task 6
- Code-violations hardening and source metadata consistency: Task 7
- `iCourtCase.mt.gov` phase 2 adapter and county-scoped checkpointing: Task 8
