# Missing Persons Directory Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing MontanaBlotter missing-person system so it ingests the Montana DOJ directory fields, deduplicates by case number, refreshes safely from cron, and renders a tighter technical-authority public directory.

**Architecture:** Keep `missing_persons.py` as the source of truth for schema, parsing, sync, and public-context shaping. Add tests first around normalization and dedupe, then extend the existing sync pipeline and Jinja templates without introducing a second persistence path or a browser-first scraper.

**Tech Stack:** Python, Flask, Jinja2, SQLite, requests/httpx-style HTTP fetching, pytest/unittest.

---

## File Structure

- Modify: `missing_persons.py`
  Responsibility: schema migration helpers, row parsing, sync/upsert flow, public directory context, async refresh entry point.
- Modify: `templates/missing_persons.html`
  Responsibility: directory index presentation for the new compact fields and technical-authority layout.
- Modify: `templates/missing_person_detail.html`
  Responsibility: detail-page field presentation and graceful missing-photo fallback.
- Modify: `tests/test_missing_persons.py`
  Responsibility: parser, malformed-date, schema, and dedupe regression coverage.
- Modify: `tests/test_public_detail_routes.py`
  Responsibility: public route assertions for the directory and detail rendering.
- Create: `missing_person_refresh.py`
  Responsibility: cron-safe async wrapper around the missing-person directory refresh service.

### Task 1: Lock in schema and normalization behavior with tests

**Files:**
- Modify: `tests/test_missing_persons.py`
- Modify: `missing_persons.py`

- [ ] **Step 1: Write the failing schema and normalization tests**

```python
    def test_ensure_missing_person_schema_adds_directory_columns(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        conn.execute('DROP TABLE IF EXISTS missing_persons')
        conn.execute(
            '''
            CREATE TABLE missing_persons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL,
                full_name TEXT DEFAULT ''
            )
            '''
        )
        conn.commit()

        missing_persons_module.ensure_missing_person_schema(conn)

        columns = {
            row['name']
            for row in conn.execute("PRAGMA table_info('missing_persons')").fetchall()
        }

        assert 'case_number' in columns
        assert 'missing_from' in columns
        assert 'height_weight' in columns
        assert 'last_synced' in columns
        assert 'is_active' in columns

    def test_normalize_directory_fields_tolerates_bad_dates(self) -> None:
        normalized = missing_persons_module._normalize_directory_record(
            {
                'full_name': '  Jane Example  ',
                'age': '19',
                'missing_from': '  Billings / Yellowstone  ',
                'date_last_seen': 'not-a-date',
                'height_weight': ' 5-08 / 120 ',
                'case_number': ' MP-42 ',
                'status': 'missing',
            }
        )

        assert normalized['full_name'] == 'Jane Example'
        assert normalized['age'] == 19
        assert normalized['missing_from'] == 'Billings / Yellowstone'
        assert normalized['date_last_seen'] == ''
        assert normalized['height_weight'] == '5-08 / 120'
        assert normalized['case_number'] == 'MP-42'
        assert normalized['is_active'] is True
```

- [ ] **Step 2: Run the targeted tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_missing_persons.py -k "directory_columns or bad_dates" -q`
Expected: FAIL with missing `case_number`/directory columns and undefined `_normalize_directory_record`.

- [ ] **Step 3: Add the minimal schema and normalization implementation**

```python
def _normalize_directory_record(raw: dict[str, Any]) -> dict[str, Any]:
    case_number = _single_line(raw.get('case_number'), max_len=64)
    date_last_seen = ''
    raw_date = _single_line(raw.get('date_last_seen'), max_len=40)
    if raw_date:
        try:
            date_last_seen = _normalize_datetime(raw_date)
        except ValueError:
            date_last_seen = ''

    age_value = _normalize_optional_int(raw.get('age'), label='Age', maximum=150)
    status = _normalize_status(raw.get('status'))

    return {
        'full_name': _single_line(raw.get('full_name')),
        'age': age_value,
        'missing_from': _single_line(raw.get('missing_from')),
        'date_last_seen': date_last_seen,
        'height_weight': _single_line(raw.get('height_weight')),
        'case_number': case_number,
        'is_active': status == STATUS_MISSING,
        'status': status,
    }


for column_name, definition in [
    ('case_number', "TEXT DEFAULT ''"),
    ('missing_from', "TEXT DEFAULT ''"),
    ('height_weight', "TEXT DEFAULT ''"),
    ('last_synced', "TEXT DEFAULT ''"),
    ('is_active', 'INTEGER NOT NULL DEFAULT 1'),
]:
    if column_name not in existing_columns:
        conn.execute(f'ALTER TABLE missing_persons ADD COLUMN {column_name} {definition}')
```

- [ ] **Step 4: Run the targeted tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_missing_persons.py -k "directory_columns or bad_dates" -q`
Expected: PASS

- [ ] **Step 5: Commit the schema and normalization baseline**

```bash
git add tests/test_missing_persons.py missing_persons.py
git commit -m "test: lock missing person directory schema rules"
```

### Task 2: Add parser and case-number dedupe coverage, then extend sync

**Files:**
- Modify: `tests/test_missing_persons.py`
- Modify: `missing_persons.py`

- [ ] **Step 1: Write the failing parser and dedupe tests**

```python
    def test_parse_missing_person_result_row_extracts_directory_fields(self) -> None:
        html = '''
        <tr>
          <td><a href="detail.php?id=100">Jane Example</a></td>
          <td>19</td>
          <td>Billings / Yellowstone</td>
          <td>04/20/2026</td>
          <td>5\'08" / 120 lbs</td>
          <td>MP-100</td>
        </tr>
        '''

        record = missing_persons_module.parse_missing_person_result_row(html)

        assert record['full_name'] == 'Jane Example'
        assert record['age'] == 19
        assert record['missing_from'] == 'Billings / Yellowstone'
        assert record['case_number'] == 'MP-100'

    def test_upsert_missing_person_uses_case_number_for_dedupe(self) -> None:
        conn = app_module.get_db()
        missing_persons_module.ensure_missing_person_schema(conn)
        conn.execute(
            '''
            INSERT INTO missing_persons (
                slug, full_name, case_number, status, is_active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                'jane-example',
                'Jane Example',
                'MP-100',
                'missing',
                1,
                '2026-04-25 08:00:00',
                '2026-04-25 08:00:00',
            ),
        )
        conn.commit()

        result = missing_persons_module._upsert_directory_record(
            conn,
            {
                'slug': 'jane-example',
                'full_name': 'Jane Example Updated',
                'case_number': 'MP-100',
                'status': 'located',
                'is_active': False,
                'missing_from': 'Billings / Yellowstone',
                'height_weight': '5\'08" / 120 lbs',
                'date_last_seen': '2026-04-20 00:00:00',
            },
        )

        row = conn.execute(
            'SELECT full_name, status, is_active FROM missing_persons WHERE case_number = ?',
            ('MP-100',),
        ).fetchone()

        assert result == 'updated'
        assert row['full_name'] == 'Jane Example Updated'
        assert row['status'] == 'located'
        assert row['is_active'] == 0
```

- [ ] **Step 2: Run the targeted tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_missing_persons.py -k "extracts_directory_fields or case_number_for_dedupe" -q`
Expected: FAIL with missing parser and upsert helpers.

- [ ] **Step 3: Implement parser and case-number upsert helpers**

```python
def parse_missing_person_result_row(row_html: str) -> dict[str, Any]:
    cells = re.findall(r'<td[^>]*>(.*?)</td>', row_html, flags=re.IGNORECASE | re.DOTALL)
    if len(cells) < 6:
        raise ValueError('Result row did not contain the required directory fields.')

    return _normalize_directory_record(
        {
            'full_name': _html_to_text(cells[0]),
            'age': _html_to_text(cells[1]),
            'missing_from': _html_to_text(cells[2]),
            'date_last_seen': _html_to_text(cells[3]),
            'height_weight': _html_to_text(cells[4]),
            'case_number': _html_to_text(cells[5]),
            'status': STATUS_MISSING,
        }
    )


def _upsert_directory_record(conn: sqlite3.Connection, record: dict[str, Any]) -> str:
    existing = conn.execute(
        '''
        SELECT id
        FROM missing_persons
        WHERE TRIM(COALESCE(case_number, '')) = ?
        LIMIT 1
        ''',
        (record['case_number'],),
    ).fetchone()
    if existing:
        conn.execute(
            '''
            UPDATE missing_persons
            SET full_name = ?, status = ?, is_active = ?, missing_from = ?,
                height_weight = ?, last_seen_at = ?, last_synced = datetime('now'),
                updated_at = datetime('now')
            WHERE id = ?
            ''',
            (
                record['full_name'],
                record['status'],
                int(record['is_active']),
                record['missing_from'],
                record['height_weight'],
                record['date_last_seen'],
                existing['id'],
            ),
        )
        return 'updated'
    conn.execute(...)
    return 'inserted'
```

- [ ] **Step 4: Extend `sync_official_missing_persons()` to use the new helpers**

```python
for raw_row in fetched_rows:
    try:
        record = parse_missing_person_result_row(raw_row)
    except ValueError:
        skipped_count += 1
        continue

    if not record['case_number']:
        skipped_count += 1
        continue

    outcome = _upsert_directory_record(conn, record)
    if outcome == 'inserted':
        inserted_count += 1
    else:
        updated_count += 1
```

- [ ] **Step 5: Run the focused missing-person suite**

Run: `./venv/bin/python -m pytest tests/test_missing_persons.py -q`
Expected: PASS

- [ ] **Step 6: Commit the sync and dedupe work**

```bash
git add tests/test_missing_persons.py missing_persons.py
git commit -m "feat: dedupe missing persons by case number"
```

### Task 3: Add the async refresh wrapper and cron-facing behavior

**Files:**
- Modify: `tests/test_missing_persons.py`
- Modify: `missing_persons.py`
- Create: `missing_person_refresh.py`

- [ ] **Step 1: Write the failing async refresh and wrapper tests**

```python
    def test_refresh_missing_persons_directory_returns_count_summary(self) -> None:
        conn = app_module.get_db()

        async def run_refresh() -> dict[str, int]:
            return await missing_persons_module.refresh_missing_persons_directory(
                conn,
                fetch_rows=lambda: [
                    {
                        'full_name': 'Jane Example',
                        'age': 19,
                        'missing_from': 'Billings / Yellowstone',
                        'date_last_seen': '2026-04-20 00:00:00',
                        'height_weight': '5\'08" / 120 lbs',
                        'case_number': 'MP-100',
                        'status': 'missing',
                    }
                ],
            )

        result = asyncio.run(run_refresh())

        assert result == {
            'fetched': 1,
            'inserted': 1,
            'updated': 0,
            'skipped': 0,
            'failed': 0,
        }
```

```python
def test_missing_person_refresh_main_exits_zero_on_success(monkeypatch):
    monkeypatch.setattr(
        'missing_person_refresh.run_refresh',
        lambda: {'fetched': 1, 'inserted': 1, 'updated': 0, 'skipped': 0, 'failed': 0},
    )
    assert missing_person_refresh.main() == 0
```

- [ ] **Step 2: Run the targeted tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_missing_persons.py -k "count_summary or exits_zero" -q`
Expected: FAIL with undefined refresh service and wrapper module.

- [ ] **Step 3: Implement the async service and cron wrapper**

```python
async def refresh_missing_persons_directory(
    conn: sqlite3.Connection,
    *,
    fetch_rows: Callable[[], list[dict[str, Any]]] | None = None,
) -> dict[str, int]:
    ensure_missing_person_schema(conn)
    rows = fetch_rows() if fetch_rows is not None else fetch_official_missing_person_rows()

    result = {'fetched': len(rows), 'inserted': 0, 'updated': 0, 'skipped': 0, 'failed': 0}
    for raw in rows:
        try:
            record = _normalize_directory_record(raw)
            if not record['case_number']:
                result['skipped'] += 1
                continue
            outcome = _upsert_directory_record(conn, record)
            result[outcome] += 1
        except Exception:
            result['failed'] += 1
    conn.commit()
    return result
```

```python
# missing_person_refresh.py
import asyncio
import sqlite3

import config
from missing_persons import refresh_missing_persons_directory


def run_refresh() -> dict[str, int]:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return asyncio.run(refresh_missing_persons_directory(conn))
    finally:
        conn.close()


def main() -> int:
    result = run_refresh()
    print(result)
    return 0 if result['failed'] < result['fetched'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
```

- [ ] **Step 4: Run the focused test suite**

Run: `./venv/bin/python -m pytest tests/test_missing_persons.py -q`
Expected: PASS

- [ ] **Step 5: Commit the refresh service**

```bash
git add tests/test_missing_persons.py missing_persons.py missing_person_refresh.py
git commit -m "feat: add missing person refresh service"
```

### Task 4: Refresh the directory index and detail view presentation

**Files:**
- Modify: `tests/test_public_detail_routes.py`
- Modify: `templates/missing_persons.html`
- Modify: `templates/missing_person_detail.html`
- Modify: `missing_persons.py`

- [ ] **Step 1: Write the failing route-render tests**

```python
def test_missing_persons_index_shows_directory_fields(client):
    response = client.get('/missing-persons')
    html = response.get_data(as_text=True)

    assert 'Case Number' in html
    assert 'Missing From' in html
    assert 'Height / Weight' in html
    assert 'Roboto Mono' in html or 'SF Mono' in html


def test_missing_person_detail_handles_missing_photo(client):
    response = client.get('/missing-persons/rich-person-1')
    html = response.get_data(as_text=True)

    assert 'Record Dossier' in html
    assert 'Case Number' in html
    assert 'Official Source' in html
```

- [ ] **Step 2: Run the targeted route tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_public_detail_routes.py -k "directory_fields or missing_photo" -q`
Expected: FAIL because the templates do not yet expose the new directory fields.

- [ ] **Step 3: Extend the public context and templates**

```python
item['case_number'] = _single_line(row['case_number'], max_len=64)
item['missing_from'] = _single_line(
    row['missing_from'] or row['last_seen_location'] or f"{row['city']} / {row['county']}",
    max_len=240,
)
item['height_weight_label'] = _single_line(
    row['height_weight'] or _build_height_weight_label(row['height_raw'], row['weight_lbs']),
    max_len=120,
)
item['last_synced_label'] = _display_datetime(row['last_synced'])
```

```html
<div class="font-mono text-[11px] uppercase tracking-[0.18em] text-slate-500">Case Number</div>
<div class="mt-1 text-sm font-semibold text-slate-900">{{ person.case_number or 'Not provided' }}</div>

<div class="font-mono text-[11px] uppercase tracking-[0.18em] text-slate-500">Missing From</div>
<div class="mt-1 text-sm font-semibold text-slate-900">{{ person.missing_from or 'Not provided' }}</div>

<div class="font-mono text-[11px] uppercase tracking-[0.18em] text-slate-500">Height / Weight</div>
<div class="mt-1 text-sm font-semibold text-slate-900">{{ person.height_weight_label or 'Not provided' }}</div>
```

- [ ] **Step 4: Add graceful photo fallback on the detail page**

```html
{% if person.photo_gallery %}
  ...existing gallery...
{% elif person.photo_url %}
  <figure class="overflow-hidden rounded-2xl border border-slate-200 bg-slate-50">
    <img src="{{ person.photo_url }}" alt="{{ person.full_name }}" class="h-64 w-full object-cover">
  </figure>
{% else %}
  <div class="rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-5 py-10 text-center text-sm text-slate-500">
    No official photo is available in the current public record.
  </div>
{% endif %}
```

- [ ] **Step 5: Run the public route suite**

Run: `./venv/bin/python -m pytest tests/test_public_detail_routes.py tests/test_missing_persons.py -q`
Expected: PASS

- [ ] **Step 6: Commit the public directory refresh**

```bash
git add tests/test_public_detail_routes.py templates/missing_persons.html templates/missing_person_detail.html missing_persons.py
git commit -m "feat: refresh missing persons public directory"
```

### Task 5: Verify end-to-end behavior and document the cron command

**Files:**
- Modify: `tests/test_missing_persons.py`
- Modify: `docs/superpowers/plans/2026-04-25-missing-person-directory-module.md`

- [ ] **Step 1: Add one end-to-end refresh regression test**

```python
    def test_refresh_directory_skips_missing_case_number_without_failing_run(self) -> None:
        conn = app_module.get_db()

        async def run_refresh() -> dict[str, int]:
            return await missing_persons_module.refresh_missing_persons_directory(
                conn,
                fetch_rows=lambda: [
                    {
                        'full_name': 'No Case Number',
                        'age': 18,
                        'missing_from': 'Helena / Lewis and Clark',
                        'date_last_seen': '2026-04-20 00:00:00',
                        'height_weight': '5\'04" / 110 lbs',
                        'case_number': '',
                        'status': 'missing',
                    }
                ],
            )

        result = asyncio.run(run_refresh())
        count = conn.execute('SELECT COUNT(*) FROM missing_persons').fetchone()[0]

        assert result['skipped'] == 1
        assert result['failed'] == 0
        assert count == 0
```

- [ ] **Step 2: Run the full focused suite**

Run: `./venv/bin/python -m pytest tests/test_missing_persons.py tests/test_public_detail_routes.py -q`
Expected: PASS

- [ ] **Step 3: Verify the cron wrapper manually**

Run: `./venv/bin/python missing_person_refresh.py`
Expected: prints a dict like `{'fetched': 0, 'inserted': 0, 'updated': 0, 'skipped': 0, 'failed': 0}` or live refresh counts without a traceback.

- [ ] **Step 4: Add the production cron command to the plan for handoff**

```text
*/30 * * * * cd /root/montanablotter && ./venv/bin/python missing_person_refresh.py >> /var/log/montanablotter-missing-persons.log 2>&1
```

- [ ] **Step 5: Commit the verification pass**

```bash
git add tests/test_missing_persons.py docs/superpowers/plans/2026-04-25-missing-person-directory-module.md
git commit -m "test: verify missing person directory refresh flow"
```
