# Sex Offender Registry Changes Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Add a Montana Sex Offender Registry module that scrapes svor.doj.mt.gov weekly, diffs for changes, classifies them, and surfaces updates via public pages, a Leaflet map, and a proximity-based parent alert product.

**Architecture:** New `sex_offender` blueprint + SQLite schema (`sex_offenders`, `sex_offender_snapshots`, `sex_offender_changes`) + scraper worker (`sex_offender_scraper.py`) + delta engine + public pages with Leaflet.js + radius alert subscriptions + admin panel. Follows the same patterns as `code_violations` and `detention`.

**Tech Stack:** Flask, SQLite, Jinja2, Leaflet.js, vanilla JS, Python 3.11.

---

## Task 1: Add database schema in `init_db.py`

**Objective:** Create tables and indexes for sex offender registry tracking.

**Files:**
- Modify: `init_db.py`

**Step 1: Write the schema function**

Append to `init_db.py`:

```python
def ensure_sex_offender_schema(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()

    # Core registrant table — one row per current registrant
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sex_offenders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            registry_id TEXT UNIQUE NOT NULL,
            full_name TEXT NOT NULL,
            first_name TEXT,
            last_name TEXT,
            date_of_birth TEXT,
            tier TEXT,
            risk_level TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            address_street TEXT,
            address_city TEXT,
            address_county TEXT,
            address_state TEXT DEFAULT 'MT',
            address_zip TEXT,
            lat REAL,
            lon REAL,
            employer_name TEXT,
            employer_address TEXT,
            school_name TEXT,
            school_address TEXT,
            offense_description TEXT,
            conviction_date TEXT,
            conviction_state TEXT,
            conviction_county TEXT,
            photo_url TEXT,
            source_url TEXT,
            raw_json TEXT,
            first_seen_at TEXT DEFAULT (datetime('now')),
            last_seen_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    ''')

    # Weekly snapshot tracking
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sex_offender_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT NOT NULL,
            total_count INTEGER NOT NULL DEFAULT 0,
            new_count INTEGER NOT NULL DEFAULT 0,
            removed_count INTEGER NOT NULL DEFAULT 0,
            changed_count INTEGER NOT NULL DEFAULT 0,
            scrape_duration_seconds INTEGER,
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        )
    ''')

    # Individual change events (delta)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sex_offender_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            offender_id INTEGER NOT NULL,
            snapshot_id INTEGER NOT NULL,
            change_type TEXT NOT NULL,
            change_note TEXT,
            old_value_json TEXT,
            new_value_json TEXT,
            classified_by TEXT DEFAULT 'hermes',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (offender_id) REFERENCES sex_offenders(id) ON DELETE CASCADE,
            FOREIGN KEY (snapshot_id) REFERENCES sex_offender_snapshots(id) ON DELETE CASCADE
        )
    ''')

    # Proximity alert subscriptions
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sex_offender_alert_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            radius_miles REAL NOT NULL DEFAULT 5.0,
            counties TEXT DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1,
            last_sent_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    ''')

    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_sex_offenders_registry_id ON sex_offenders(registry_id)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_sex_offenders_county ON sex_offenders(address_county)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_sex_offenders_city ON sex_offenders(address_city)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_sex_offenders_geo ON sex_offenders(lat, lon)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_sex_offenders_status ON sex_offenders(status)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_sex_offender_changes_offender ON sex_offender_changes(offender_id)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_sex_offender_changes_snapshot ON sex_offender_changes(snapshot_id)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_sex_offender_changes_type ON sex_offender_changes(change_type)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_sex_offender_snapshots_date ON sex_offender_snapshots(snapshot_date)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_so_alert_subs_active ON sex_offender_alert_subscriptions(is_active)'
    )

    # Graceful column additions
    for col, definition in [
        ('registry_id', 'TEXT UNIQUE NOT NULL'),
        ('tier', 'TEXT'),
        ('risk_level', 'TEXT'),
        ('status', "TEXT NOT NULL DEFAULT 'active'"),
        ('address_street', 'TEXT'),
        ('address_city', 'TEXT'),
        ('address_county', 'TEXT'),
        ('address_state', "TEXT DEFAULT 'MT'"),
        ('address_zip', 'TEXT'),
        ('lat', 'REAL'),
        ('lon', 'REAL'),
        ('employer_name', 'TEXT'),
        ('employer_address', 'TEXT'),
        ('school_name', 'TEXT'),
        ('school_address', 'TEXT'),
        ('offense_description', 'TEXT'),
        ('conviction_date', 'TEXT'),
        ('conviction_state', 'TEXT'),
        ('conviction_county', 'TEXT'),
        ('photo_url', 'TEXT'),
        ('source_url', 'TEXT'),
        ('raw_json', 'TEXT'),
        ('first_seen_at', "TEXT DEFAULT (datetime('now'))"),
        ('last_seen_at', "TEXT DEFAULT (datetime('now'))"),
        ('updated_at', "TEXT DEFAULT (datetime('now'))"),
    ]:
        try:
            cursor.execute(f'ALTER TABLE sex_offenders ADD COLUMN {col} {definition}')
            print(f'✅ Added sex_offenders.{col}')
        except sqlite3.OperationalError:
            pass

    for col, definition in [
        ('snapshot_date', 'TEXT NOT NULL'),
        ('total_count', 'INTEGER NOT NULL DEFAULT 0'),
        ('new_count', 'INTEGER NOT NULL DEFAULT 0'),
        ('removed_count', 'INTEGER NOT NULL DEFAULT 0'),
        ('changed_count', 'INTEGER NOT NULL DEFAULT 0'),
        ('scrape_duration_seconds', 'INTEGER'),
        ('notes', "TEXT DEFAULT ''"),
        ('created_at', "TEXT DEFAULT (datetime('now'))"),
    ]:
        try:
            cursor.execute(f'ALTER TABLE sex_offender_snapshots ADD COLUMN {col} {definition}')
            print(f'✅ Added sex_offender_snapshots.{col}')
        except sqlite3.OperationalError:
            pass

    for col, definition in [
        ('offender_id', 'INTEGER NOT NULL'),
        ('snapshot_id', 'INTEGER NOT NULL'),
        ('change_type', 'TEXT NOT NULL'),
        ('change_note', 'TEXT'),
        ('old_value_json', 'TEXT'),
        ('new_value_json', 'TEXT'),
        ('classified_by', "TEXT DEFAULT 'hermes'"),
        ('created_at', "TEXT DEFAULT (datetime('now'))"),
    ]:
        try:
            cursor.execute(f'ALTER TABLE sex_offender_changes ADD COLUMN {col} {definition}')
            print(f'✅ Added sex_offender_changes.{col}')
        except sqlite3.OperationalError:
            pass

    for col, definition in [
        ('email', 'TEXT NOT NULL'),
        ('lat', 'REAL NOT NULL'),
        ('lon', 'REAL NOT NULL'),
        ('radius_miles', 'REAL NOT NULL DEFAULT 5.0'),
        ('counties', "TEXT DEFAULT ''"),
        ('is_active', 'INTEGER NOT NULL DEFAULT 1'),
        ('last_sent_at', 'TEXT'),
        ('created_at', "TEXT DEFAULT (datetime('now'))"),
    ]:
        try:
            cursor.execute(f'ALTER TABLE sex_offender_alert_subscriptions ADD COLUMN {col} {definition}')
            print(f'✅ Added sex_offender_alert_subscriptions.{col}')
        except sqlite3.OperationalError:
            pass
```

**Step 2: Wire into `init_database()` and `migrate()`**

In `init_database()`, add after `ensure_code_violation_schema(conn)`:

```python
    ensure_sex_offender_schema(conn)
```

In `migrate()`, add as the final migration block:

```python
    # 2026-05-11: sex offender registry
    ensure_sex_offender_schema(conn)
```

**Step 3: Verify**

Run: `source venv/bin/activate && python -c "from init_db import migrate; migrate()"`
Expected: prints "Migration complete", no errors.

**Step 4: Commit**

```bash
git add init_db.py
git commit -m "feat(sex-offender): add schema for offenders, snapshots, changes, and alert subscriptions"
```

---

## Task 2: Create scraper module `sex_offender_scraper.py`

**Objective:** Build a scraper that fetches the MT Sex Offender Registry and writes to the database.

**Files:**
- Create: `sex_offender_scraper.py`
- Create: `tests/test_sex_offender_scraper.py`

**Step 1: Write the scraper**

```python
"""
Montana Sex Offender Registry Scraper

Fetches registrant data from svor.doj.mt.gov and writes to sex_offenders.
Designed to run weekly via cron.

Usage:
    python sex_offender_scraper.py --dry-run
    python sex_offender_scraper.py --county Yellowstone
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime
from typing import Any

import requests
from db import connect_db

BASE_URL = 'https://svor.doj.mt.gov'
SEARCH_URL = f'{BASE_URL}/search'
DETAIL_URL = f'{BASE_URL}/detail'
DB_PATH = os.getenv('MB_DB_PATH', '/root/montanablotter/blotter.db')
REQUEST_DELAY = 1.5  # seconds between requests


def _normalize_name(name: str) -> str:
    return ' '.join(name.split()).title()


def _normalize_date(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y'):
        try:
            return datetime.strptime(value, fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return value[:10] if len(value) >= 10 else value


def _geocode_address(street: str, city: str, state: str = 'MT', zip_code: str = '') -> tuple[float | None, float | None]:
    """Simple geocoding via Nominatim (OpenStreetMap)."""
    try:
        query = f"{street}, {city}, {state} {zip_code}"
        resp = requests.get(
            'https://nominatim.openstreetmap.org/search',
            params={'q': query, 'format': 'json', 'limit': 1},
            headers={'User-Agent': 'MontanaBlotter/1.0'},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data:
            return float(data[0]['lat']), float(data[0]['lon'])
    except Exception:
        pass
    return None, None


def _fetch_county_list() -> list[dict[str, str]]:
    """Fetch list of counties from the registry search page."""
    try:
        resp = requests.get(SEARCH_URL, timeout=30, headers={'User-Agent': 'Mozilla/5.0'})
        resp.raise_for_status()
        # Parse county options from HTML
        counties = []
        for match in re.finditer(r'<option[^>]*value="([^"]+)"[^>]*>([^<]+)</option>', resp.text):
            val = match.group(1).strip()
            label = match.group(2).strip()
            if val and label.lower() not in ('select county', 'all counties'):
                counties.append({'value': val, 'label': label})
        return counties
    except Exception as exc:
        print(f'Failed to fetch county list: {exc}')
        return []


def _fetch_registrants_for_county(county_value: str) -> list[dict[str, Any]]:
    """Fetch all registrants for a given county."""
    registrants = []
    page = 1
    while True:
        try:
            resp = requests.post(
                SEARCH_URL,
                data={'county': county_value, 'page': page},
                timeout=30,
                headers={'User-Agent': 'Mozilla/5.0', 'Referer': SEARCH_URL},
            )
            resp.raise_for_status()
            # Parse registrant links from results table
            ids = re.findall(r'href="/detail/([^"]+)"', resp.text)
            if not ids:
                break
            for rid in ids:
                registrants.append({'registry_id': rid, 'county_value': county_value})
            if 'Next' not in resp.text and 'next' not in resp.text.lower():
                break
            page += 1
            time.sleep(REQUEST_DELAY)
        except Exception as exc:
            print(f'Error fetching county {county_value} page {page}: {exc}')
            break
    return registrants


def _fetch_registrant_detail(registry_id: str) -> dict[str, Any] | None:
    """Fetch detailed record for a single registrant."""
    try:
        resp = requests.get(
            f'{DETAIL_URL}/{registry_id}',
            timeout=30,
            headers={'User-Agent': 'Mozilla/5.0'},
        )
        resp.raise_for_status()
        html = resp.text

        def _extract(label: str) -> str:
            pattern = re.compile(rf'<[^>]*>\s*{re.escape(label)}\s*</[^>]*>\s*<[^>]*>([^<]+)</', re.IGNORECASE)
            m = pattern.search(html)
            return m.group(1).strip() if m else ''

        record = {
            'registry_id': registry_id,
            'full_name': _normalize_name(_extract('Name') or _extract('Full Name')),
            'date_of_birth': _normalize_date(_extract('Date of Birth') or _extract('DOB')),
            'tier': _extract('Tier') or '',
            'risk_level': _extract('Risk Level') or '',
            'status': 'active',
            'address_street': _extract('Address') or _extract('Street Address'),
            'address_city': _extract('City'),
            'address_county': _extract('County'),
            'address_zip': _extract('Zip') or _extract('ZIP'),
            'employer_name': _extract('Employer'),
            'employer_address': _extract('Employer Address'),
            'school_name': _extract('School'),
            'school_address': _extract('School Address'),
            'offense_description': _extract('Offense') or _extract('Conviction Offense'),
            'conviction_date': _normalize_date(_extract('Conviction Date')),
            'conviction_state': _extract('Conviction State') or 'MT',
            'conviction_county': _extract('Conviction County'),
            'photo_url': '',
            'source_url': f'{DETAIL_URL}/{registry_id}',
            'raw_json': json.dumps({'html_sample': html[:5000]}),
        }

        # Attempt to extract photo URL
        photo_match = re.search(r'<img[^>]*src="(/photos/[^"]+)"', html)
        if photo_match:
            record['photo_url'] = BASE_URL + photo_match.group(1)

        # Geocode if we have address
        if record['address_street'] and record['address_city']:
            lat, lon = _geocode_address(record['address_street'], record['address_city'], 'MT', record['address_zip'])
            record['lat'] = lat
            record['lon'] = lon
            time.sleep(0.5)  # Nominatim rate limit

        return record
    except Exception as exc:
        print(f'Error fetching detail for {registry_id}: {exc}')
        return None


def _upsert_offender(conn: sqlite3.Connection, record: dict[str, Any]) -> tuple[int, bool]:
    """Insert or update a registrant. Returns (id, is_new)."""
    existing = conn.execute(
        'SELECT id, raw_json FROM sex_offenders WHERE registry_id = ?',
        (record['registry_id'],),
    ).fetchone()

    if existing:
        # Update existing
        conn.execute(
            '''
            UPDATE sex_offenders SET
                full_name = ?, date_of_birth = ?, tier = ?, risk_level = ?,
                status = ?, address_street = ?, address_city = ?, address_county = ?,
                address_zip = ?, lat = ?, lon = ?, employer_name = ?, employer_address = ?,
                school_name = ?, school_address = ?, offense_description = ?,
                conviction_date = ?, conviction_state = ?, conviction_county = ?,
                photo_url = ?, source_url = ?, raw_json = ?, last_seen_at = datetime('now'),
                updated_at = datetime('now')
            WHERE id = ?
            ''',
            (
                record['full_name'], record['date_of_birth'], record['tier'], record['risk_level'],
                record['status'], record['address_street'], record['address_city'], record['address_county'],
                record['address_zip'], record.get('lat'), record.get('lon'), record['employer_name'],
                record['employer_address'], record['school_name'], record['school_address'],
                record['offense_description'], record['conviction_date'], record['conviction_state'],
                record['conviction_county'], record['photo_url'], record['source_url'], record['raw_json'],
                existing['id'],
            ),
        )
        conn.commit()
        return existing['id'], False
    else:
        # Insert new
        cur = conn.execute(
            '''
            INSERT INTO sex_offenders
            (registry_id, full_name, date_of_birth, tier, risk_level, status,
             address_street, address_city, address_county, address_zip, lat, lon,
             employer_name, employer_address, school_name, school_address,
             offense_description, conviction_date, conviction_state, conviction_county,
             photo_url, source_url, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                record['registry_id'], record['full_name'], record['date_of_birth'], record['tier'],
                record['risk_level'], record['status'], record['address_street'], record['address_city'],
                record['address_county'], record['address_zip'], record.get('lat'), record.get('lon'),
                record['employer_name'], record['employer_address'], record['school_name'],
                record['school_address'], record['offense_description'], record['conviction_date'],
                record['conviction_state'], record['conviction_county'], record['photo_url'],
                record['source_url'], record['raw_json'],
            ),
        )
        conn.commit()
        return cur.lastrowid, True


def run_scrape(*, dry_run: bool = False, county_filter: str = '') -> dict[str, Any]:
    """Run full registry scrape. Returns summary stats."""
    start_time = time.time()
    conn = connect_db()
    try:
        counties = _fetch_county_list()
        if county_filter:
            counties = [c for c in counties if county_filter.lower() in c['label'].lower()]

        total_new = 0
        total_updated = 0
        total_errors = 0
        all_registry_ids: set[str] = set()

        for county in counties:
            print(f"Scraping {county['label']}...")
            registrants = _fetch_registrants_for_county(county['value'])
            for reg in registrants:
                all_registry_ids.add(reg['registry_id'])
                detail = _fetch_registrant_detail(reg['registry_id'])
                if not detail:
                    total_errors += 1
                    continue
                if dry_run:
                    print(f"  [DRY] {detail['full_name']} — {detail['address_city']}")
                    continue
                offender_id, is_new = _upsert_offender(conn, detail)
                if is_new:
                    total_new += 1
                else:
                    total_updated += 1
                time.sleep(REQUEST_DELAY)

        if not dry_run:
            # Mark missing registrants as removed
            conn.execute(
                "UPDATE sex_offenders SET status = 'removed', updated_at = datetime('now') WHERE registry_id NOT IN ({}) AND status = 'active'".format(
                    ','.join('?' * len(all_registry_ids)) if all_registry_ids else "''"
                ),
                list(all_registry_ids) if all_registry_ids else [],
            )
            conn.commit()

            # Record snapshot
            removed_count = conn.execute(
                "SELECT COUNT(*) FROM sex_offenders WHERE status = 'removed' AND updated_at > datetime('now', '-1 day')"
            ).fetchone()[0]
            total_active = conn.execute(
                "SELECT COUNT(*) FROM sex_offenders WHERE status = 'active'"
            ).fetchone()[0]

            conn.execute(
                '''
                INSERT INTO sex_offender_snapshots
                (snapshot_date, total_count, new_count, removed_count, changed_count, scrape_duration_seconds, notes)
                VALUES (datetime('now'), ?, ?, ?, ?, ?, ?)
                ''',
                (total_active, total_new, removed_count, total_updated, int(time.time() - start_time), f"Counties: {len(counties)}"),
            )
            conn.commit()

        return {
            'counties': len(counties),
            'new': total_new,
            'updated': total_updated,
            'errors': total_errors,
            'duration': int(time.time() - start_time),
        }
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description='Scrape MT Sex Offender Registry')
    parser.add_argument('--dry-run', action='store_true', help='Do not write to database')
    parser.add_argument('--county', default='', help='Filter to specific county')
    args = parser.parse_args()

    result = run_scrape(dry_run=args.dry_run, county_filter=args.county)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
```

**Step 2: Write the test**

```python
import sqlite3
import tempfile
import os
import unittest

from init_db import ensure_sex_offender_schema
from sex_offender_scraper import _normalize_name, _normalize_date, _upsert_offender


class TestSexOffenderScraper(unittest.TestCase):
    def setUp(self):
        self.db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.conn = sqlite3.connect(self.db.name)
        self.conn.row_factory = sqlite3.Row
        ensure_sex_offender_schema(self.conn)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db.name)

    def test_normalize_name(self):
        self.assertEqual(_normalize_name('  john   doe  '), 'John Doe')

    def test_normalize_date(self):
        self.assertEqual(_normalize_date('05/11/1985'), '1985-05-11')
        self.assertIsNone(_normalize_date(''))

    def test_upsert_creates_and_updates(self):
        record = {
            'registry_id': 'MT12345',
            'full_name': 'John Doe',
            'date_of_birth': '1985-05-11',
            'tier': 'II',
            'risk_level': 'Moderate',
            'status': 'active',
            'address_street': '123 Main St',
            'address_city': 'Billings',
            'address_county': 'Yellowstone',
            'address_zip': '59101',
            'employer_name': '',
            'employer_address': '',
            'school_name': '',
            'school_address': '',
            'offense_description': 'Sexual Assault',
            'conviction_date': '2010-01-15',
            'conviction_state': 'MT',
            'conviction_county': 'Yellowstone',
            'photo_url': '',
            'source_url': 'https://svor.doj.mt.gov/detail/MT12345',
            'raw_json': '{}',
        }
        oid, is_new = _upsert_offender(self.conn, record)
        self.assertTrue(is_new)

        record['tier'] = 'III'
        oid2, is_new2 = _upsert_offender(self.conn, record)
        self.assertFalse(is_new2)
        self.assertEqual(oid, oid2)

        row = self.conn.execute('SELECT tier FROM sex_offenders WHERE id = ?', (oid,)).fetchone()
        self.assertEqual(row['tier'], 'III')


if __name__ == '__main__':
    unittest.main()
```

**Step 3: Run tests**

```bash
source venv/bin/activate
python -m pytest tests/test_sex_offender_scraper.py -v
```

Expected: 3 passed.

**Step 4: Commit**

```bash
git add sex_offender_scraper.py tests/test_sex_offender_scraper.py
git commit -m "feat(sex-offender): add registry scraper with tests"
```

---

## Task 3: Build delta engine `sex_offender_delta.py`

**Objective:** Compare weekly snapshots, classify changes, and write change records.

**Files:**
- Create: `sex_offender_delta.py`
- Create: `tests/test_sex_offender_delta.py`

**Step 1: Write the delta engine**

```python
"""
Sex Offender Registry Delta Engine

Compares current state against previous snapshot, classifies changes,
and writes sex_offender_changes records.

Usage:
    python sex_offender_delta.py
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from db import connect_db


def _classify_change(old: dict[str, Any] | None, new: dict[str, Any] | None) -> tuple[str, str]:
    """Classify change type and generate a factual note."""
    if old is None and new is not None:
        return 'new_registration', f"New registrant: {new.get('full_name', 'Unknown')} registered in {new.get('address_county', 'Unknown')} County."
    if new is None and old is not None:
        return 'removed', f"Registrant removed: {old.get('full_name', 'Unknown')} no longer listed in {old.get('address_county', 'Unknown')} County."

    # Address change
    old_addr = f"{old.get('address_street', '')}, {old.get('address_city', '')}"
    new_addr = f"{new.get('address_street', '')}, {new.get('address_city', '')}"
    if old_addr.strip(', ') != new_addr.strip(', '):
        return 'address_change', f"Address change: {old.get('full_name', 'Unknown')} moved from {old_addr} to {new_addr}."

    # Compliance / status changes
    if old.get('status') != new.get('status'):
        return 'compliance_violation', f"Status change: {new.get('full_name', 'Unknown')} status changed from {old.get('status')} to {new.get('status')}."

    # Risk level change
    if old.get('risk_level') != new.get('risk_level'):
        return 'compliance_violation', f"Risk level change: {new.get('full_name', 'Unknown')} risk level changed from {old.get('risk_level')} to {new.get('risk_level')}."

    # Generic change
    return 'updated', f"Record updated: {new.get('full_name', 'Unknown')} information was modified."


def compute_delta(conn: sqlite3.Connection, snapshot_id: int) -> list[dict[str, Any]]:
    """Compute delta since last snapshot and write change records."""
    # Get previous snapshot
    prev = conn.execute(
        'SELECT id FROM sex_offender_snapshots WHERE id < ? ORDER BY id DESC LIMIT 1',
        (snapshot_id,),
    ).fetchone()

    changes = []

    if prev:
        prev_id = prev['id']
        # Find offenders that existed in previous snapshot
        prev_offenders = {
            r['registry_id']: dict(r)
            for r in conn.execute('SELECT * FROM sex_offenders WHERE last_seen_at <= (SELECT snapshot_date FROM sex_offender_snapshots WHERE id = ?)', (prev_id,)).fetchall()
        }
    else:
        prev_offenders = {}

    current_offenders = {
        r['registry_id']: dict(r)
        for r in conn.execute('SELECT * FROM sex_offenders WHERE status = \'active\'').fetchall()
    }

    all_ids = set(prev_offenders.keys()) | set(current_offenders.keys())

    for rid in all_ids:
        old = prev_offenders.get(rid)
        new = current_offenders.get(rid)

        if old == new:
            continue

        change_type, note = _classify_change(old, new)
        offender = new or old
        offender_id = offender['id']

        conn.execute(
            '''
            INSERT INTO sex_offender_changes
            (offender_id, snapshot_id, change_type, change_note, old_value_json, new_value_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ''',
            (
                offender_id,
                snapshot_id,
                change_type,
                note,
                json.dumps(old) if old else None,
                json.dumps(new) if new else None,
            ),
        )
        changes.append({
            'offender_id': offender_id,
            'change_type': change_type,
            'note': note,
        })

    conn.commit()

    # Update snapshot counts
    new_count = sum(1 for c in changes if c['change_type'] == 'new_registration')
    removed_count = sum(1 for c in changes if c['change_type'] == 'removed')
    changed_count = len(changes) - new_count - removed_count

    conn.execute(
        'UPDATE sex_offender_snapshots SET new_count = ?, removed_count = ?, changed_count = ? WHERE id = ?',
        (new_count, removed_count, changed_count, snapshot_id),
    )
    conn.commit()

    return changes


def main():
    conn = connect_db()
    try:
        latest = conn.execute('SELECT id FROM sex_offender_snapshots ORDER BY id DESC LIMIT 1').fetchone()
        if not latest:
            print('No snapshots found. Run scraper first.')
            return
        changes = compute_delta(conn, latest['id'])
        print(f'Computed {len(changes)} changes for snapshot {latest["id"]}.')
        for c in changes[:10]:
            print(f"  [{c['change_type']}] {c['note']}")
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
import unittest

from init_db import ensure_sex_offender_schema
from sex_offender_delta import _classify_change, compute_delta


class TestSexOffenderDelta(unittest.TestCase):
    def setUp(self):
        self.db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.conn = sqlite3.connect(self.db.name)
        self.conn.row_factory = sqlite3.Row
        ensure_sex_offender_schema(self.conn)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db.name)

    def test_classify_new_registration(self):
        new = {'full_name': 'John Doe', 'address_county': 'Yellowstone'}
        ctype, note = _classify_change(None, new)
        self.assertEqual(ctype, 'new_registration')
        self.assertIn('John Doe', note)

    def test_classify_removed(self):
        old = {'full_name': 'Jane Doe', 'address_county': 'Missoula'}
        ctype, note = _classify_change(old, None)
        self.assertEqual(ctype, 'removed')
        self.assertIn('Jane Doe', note)

    def test_classify_address_change(self):
        old = {'full_name': 'Bob Smith', 'address_street': '123 A St', 'address_city': 'Billings'}
        new = {'full_name': 'Bob Smith', 'address_street': '456 B St', 'address_city': 'Billings'}
        ctype, note = _classify_change(old, new)
        self.assertEqual(ctype, 'address_change')

    def test_compute_delta(self):
        # Insert snapshot
        self.conn.execute("INSERT INTO sex_offender_snapshots (snapshot_date, total_count) VALUES (datetime('now'), 0)")
        self.conn.commit()
        sid = self.conn.execute('SELECT id FROM sex_offender_snapshots').fetchone()['id']

        # Insert offender
        self.conn.execute(
            '''INSERT INTO sex_offenders (registry_id, full_name, status, address_county, raw_json)
               VALUES (?, ?, ?, ?, ?)''',
            ('MT001', 'Alice', 'active', 'Yellowstone', '{}'),
        )
        self.conn.commit()

        changes = compute_delta(self.conn, sid)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]['change_type'], 'new_registration')


if __name__ == '__main__':
    unittest.main()
```

**Step 3: Run tests**

```bash
python -m pytest tests/test_sex_offender_delta.py -v
```

Expected: 4 passed.

**Step 4: Commit**

```bash
git add sex_offender_delta.py tests/test_sex_offender_delta.py
git commit -m "feat(sex-offender): add delta engine with change classifier and tests"
```

---

## Task 4: Create `blueprints/sex_offender.py`

**Objective:** Build public-facing routes for updates, county pages, and maps.

**Files:**
- Create: `blueprints/sex_offender.py`
- Create: `templates/sex_offender_updates.html`
- Create: `templates/sex_offender_county.html`

**Step 1: Write the blueprint**

```python
from __future__ import annotations

from flask import Blueprint, abort, jsonify, render_template, request

sex_offender_bp = Blueprint('sex_offender', __name__)
_get_db = None


def register_sex_offender_blueprint(app, *, get_db):
    global _get_db
    _get_db = get_db
    app.register_blueprint(sex_offender_bp)


def _load_updates_context(
    *,
    county: str = '',
    city: str = '',
    change_type: str = '',
    page: int = 1,
    per_page: int = 50,
):
    conn = _get_db()
    try:
        where_clauses = ['1=1']
        params: list = []

        if county:
            where_clauses.append('so.address_county = ?')
            params.append(county)
        if city:
            where_clauses.append('so.address_city = ?')
            params.append(city)
        if change_type:
            where_clauses.append('soc.change_type = ?')
            params.append(change_type)

        where_sql = ' AND '.join(where_clauses)

        count_row = conn.execute(
            f'''
            SELECT COUNT(*) AS total
            FROM sex_offender_changes soc
            JOIN sex_offenders so ON soc.offender_id = so.id
            WHERE {where_sql}
            ''',
            params,
        ).fetchone()
        total = count_row['total'] if count_row else 0

        rows = conn.execute(
            f'''
            SELECT
                soc.id,
                soc.change_type,
                soc.change_note,
                soc.created_at,
                so.registry_id,
                so.full_name,
                so.address_street,
                so.address_city,
                so.address_county,
                so.lat,
                so.lon,
                so.photo_url
            FROM sex_offender_changes soc
            JOIN sex_offenders so ON soc.offender_id = so.id
            WHERE {where_sql}
            ORDER BY soc.created_at DESC, soc.id DESC
            LIMIT ? OFFSET ?
            ''',
            params + [per_page, (page - 1) * per_page],
        ).fetchall()

        counties = [r['address_county'] for r in conn.execute(
            'SELECT DISTINCT address_county FROM sex_offenders WHERE status = \'active\' ORDER BY address_county'
        ).fetchall() if r['address_county']]

        types = [r['change_type'] for r in conn.execute(
            'SELECT DISTINCT change_type FROM sex_offender_changes ORDER BY change_type'
        ).fetchall() if r['change_type']]

        return {
            'rows': rows,
            'total': total,
            'page': page,
            'per_page': per_page,
            'pages': (total + per_page - 1) // per_page,
            'counties': counties,
            'change_types': types,
            'county_filter': county,
            'city_filter': city,
            'type_filter': change_type,
        }
    finally:
        conn.close()


@sex_offender_bp.route('/sex-offender-updates')
def sex_offender_updates():
    context = _load_updates_context(
        county=request.args.get('county', ''),
        city=request.args.get('city', ''),
        change_type=request.args.get('type', ''),
        page=int(request.args.get('page', 1)),
    )
    return render_template('sex_offender_updates.html', **context)


@sex_offender_bp.route('/sex-offender-updates/<county_slug>')
def sex_offender_county(county_slug):
    conn = _get_db()
    try:
        county = county_slug.replace('-', ' ').title()
        offenders = conn.execute(
            '''
            SELECT * FROM sex_offenders
            WHERE address_county = ? AND status = 'active'
            ORDER BY address_city, full_name
            ''',
            (county,),
        ).fetchall()

        cities = sorted({r['address_city'] for r in offenders if r['address_city']})

        return render_template(
            'sex_offender_county.html',
            county=county,
            offenders=offenders,
            cities=cities,
            page_title=f'{county} County Sex Offender Registry — Montana Blotter',
            meta_description=f'Current sex offender registrants in {county} County, Montana. View address-level map and recent changes.',
        )
    finally:
        conn.close()


@sex_offender_bp.route('/api/sex-offender-updates')
def api_sex_offender_updates():
    context = _load_updates_context(
        county=request.args.get('county', ''),
        city=request.args.get('city', ''),
        change_type=request.args.get('type', ''),
        page=int(request.args.get('page', 1)),
        per_page=min(int(request.args.get('per_page', 50)), 100),
    )
    return jsonify({
        'changes': [dict(r) for r in context['rows']],
        'total': context['total'],
        'page': context['page'],
        'pages': context['pages'],
        'filters': {
            'county': context['county_filter'] or None,
            'city': context['city_filter'] or None,
            'type': context['type_filter'] or None,
        },
    })


@sex_offender_bp.route('/api/sex-offenders/geojson')
def api_sex_offenders_geojson():
    """Return GeoJSON for Leaflet map."""
    conn = _get_db()
    try:
        county = request.args.get('county', '')
        params = []
        where = "status = 'active' AND lat IS NOT NULL AND lon IS NOT NULL"
        if county:
            where += ' AND address_county = ?'
            params.append(county)

        rows = conn.execute(
            f'SELECT * FROM sex_offenders WHERE {where}',
            params,
        ).fetchall()

        features = []
        for r in rows:
            features.append({
                'type': 'Feature',
                'geometry': {
                    'type': 'Point',
                    'coordinates': [r['lon'], r['lat']],
                },
                'properties': {
                    'name': r['full_name'],
                    'address': f"{r['address_street']}, {r['address_city']}, MT {r['address_zip'] or ''}",
                    'tier': r['tier'],
                    'risk_level': r['risk_level'],
                    'offense': r['offense_description'],
                },
            })

        return jsonify({'type': 'FeatureCollection', 'features': features})
    finally:
        conn.close()
```

**Step 2: Write `templates/sex_offender_updates.html`**

Extend `public_page_base.html`. Include:
- Filters: county `<select>`, change type `<select>`
- Table of changes with columns: Date, Type, Name, County, Note
- Pagination
- Link to county pages

**Step 3: Write `templates/sex_offender_county.html`**

Extend `public_page_base.html`. Include:
- County header
- Leaflet.js map (CDN) with GeoJSON layer from `/api/sex-offenders/geojson?county={{ county }}`
- City filter sidebar
- List of registrants with addresses, tiers, risk levels
- SEO: `page_title`, `meta_description`

**Step 4: Register blueprint in `app.py`**

Import and register:

```python
from blueprints.sex_offender import register_sex_offender_blueprint
```

After other blueprint registrations:

```python
register_sex_offender_blueprint(app, get_db=get_db)
```

**Step 5: Add nav link**

In `app.py` `public_primary_nav_items`, add:

```python
{'id': 'sex_offender_updates', 'href': '/sex-offender-updates', 'label': 'Sex Offender Updates', 'menu_label': 'Registry'},
```

**Step 6: Commit**

```bash
git add blueprints/sex_offender.py templates/sex_offender_updates.html templates/sex_offender_county.html app.py
git commit -m "feat(sex-offender): add public pages, county map with Leaflet, and API"
```

---

## Task 5: Build alert subscription system

**Objective:** Allow parents to subscribe to proximity alerts for new registrants.

**Files:**
- Create: `sex_offender_alerts.py`
- Modify: `blueprints/sex_offender.py`
- Create: `templates/sex_offender_alerts.html`

**Step 1: Write alert engine**

```python
"""
Sex Offender Proximity Alert Engine

Checks new registrations against alert subscriptions and sends emails.

Usage:
    python sex_offender_alerts.py --dry-run
"""
from __future__ import annotations

import argparse
import math
import os
import sqlite3
from typing import Any

from db import connect_db


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in miles between two lat/lon points."""
    R = 3958.8  # Earth radius in miles
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def check_and_notify(conn: sqlite3.Connection, *, dry_run: bool = False) -> list[dict[str, Any]]:
    """Check new changes against subscriptions and return notifications sent."""
    # Find changes from the last 7 days that haven't been notified
    changes = conn.execute(
        '''
        SELECT soc.*, so.full_name, so.address_street, so.address_city, so.address_county, so.lat, so.lon
        FROM sex_offender_changes soc
        JOIN sex_offenders so ON soc.offender_id = so.id
        WHERE soc.created_at > datetime('now', '-7 days')
          AND soc.change_type IN ('new_registration', 'address_change')
        ORDER BY soc.created_at DESC
        '''
    ).fetchall()

    subscriptions = conn.execute(
        'SELECT * FROM sex_offender_alert_subscriptions WHERE is_active = 1'
    ).fetchall()

    notifications = []
    for sub in subscriptions:
        matched = []
        for change in changes:
            if change['lat'] is None or change['lon'] is None:
                continue
            dist = haversine(sub['lat'], sub['lon'], change['lat'], change['lon'])
            if dist <= sub['radius_miles']:
                matched.append({
                    'change': dict(change),
                    'distance_miles': round(dist, 1),
                })

        if matched:
            notification = {
                'email': sub['email'],
                'subscription_id': sub['id'],
                'matches': matched,
            }
            notifications.append(notification)

            if not dry_run:
                # TODO: integrate with existing email system (morning_briefing.py pattern)
                print(f"Would notify {sub['email']} about {len(matched)} changes within {sub['radius_miles']} miles")
                conn.execute(
                    'UPDATE sex_offender_alert_subscriptions SET last_sent_at = datetime("now") WHERE id = ?',
                    (sub['id'],),
                )

    if not dry_run:
        conn.commit()

    return notifications


def main():
    parser = argparse.ArgumentParser(description='Check and send sex offender proximity alerts')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    conn = connect_db()
    try:
        notifications = check_and_notify(conn, dry_run=args.dry_run)
        print(f'Notifications: {len(notifications)}')
        for n in notifications:
            print(f"  {n['email']}: {len(n['matches'])} matches")
    finally:
        conn.close()


if __name__ == '__main__':
    main()
```

**Step 2: Add subscription route**

In `blueprints/sex_offender.py`, add:

```python
@sex_offender_bp.route('/sex-offender-alerts', methods=['GET', 'POST'])
def sex_offender_alerts():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        address = request.form.get('address', '').strip()
        radius = float(request.form.get('radius', 5))

        # Geocode address
        lat, lon = _geocode_address(address, '', 'MT')
        if lat is None:
            flash('Could not geocode address. Please try again.', 'error')
            return redirect(url_for('sex_offender.sex_offender_alerts'))

        conn = _get_db()
        try:
            conn.execute(
                '''
                INSERT INTO sex_offender_alert_subscriptions (email, lat, lon, radius_miles)
                VALUES (?, ?, ?, ?)
                ''',
                (email, lat, lon, radius),
            )
            conn.commit()
            flash('Alert subscription created successfully.', 'success')
        finally:
            conn.close()
        return redirect(url_for('sex_offender.sex_offender_alerts'))

    return render_template('sex_offender_alerts.html')
```

**Step 3: Create `templates/sex_offender_alerts.html`**

Simple form: email, address input, radius slider (1-25 miles), submit. Extend `public_page_base.html`.

**Step 4: Commit**

```bash
git add sex_offender_alerts.py blueprints/sex_offender.py templates/sex_offender_alerts.html
git commit -m "feat(sex-offender): add proximity alert subscriptions and notification engine"
```

---

## Task 6: Add admin panel

**Objective:** Admin view of snapshots and subscriber counts.

**Files:**
- Create: `blueprints/admin/sex_offender.py`
- Modify: `blueprints/admin/__init__.py`

**Step 1: Write admin module**

```python
from flask import Blueprint, render_template
from flask_login import login_required

from db import get_db

admin_sex_offender_bp = Blueprint('admin_sex_offender', __name__)


@admin_sex_offender_bp.route('/sex-offender')
@login_required
def admin_sex_offender_dashboard():
    conn = get_db()
    try:
        snapshots = conn.execute(
            'SELECT * FROM sex_offender_snapshots ORDER BY snapshot_date DESC LIMIT 20'
        ).fetchall()
        total_active = conn.execute(
            "SELECT COUNT(*) AS c FROM sex_offenders WHERE status = 'active'"
        ).fetchone()['c']
        total_subscribers = conn.execute(
            'SELECT COUNT(*) AS c FROM sex_offender_alert_subscriptions WHERE is_active = 1'
        ).fetchone()['c']
        return render_template(
            'admin_sex_offender.html',
            snapshots=snapshots,
            total_active=total_active,
            total_subscribers=total_subscribers,
        )
    finally:
        conn.close()
```

**Step 2: Create `templates/admin_sex_offender.html`**

Extend `admin.html`. Show snapshot table, active count, subscriber count.

**Step 3: Wire into `blueprints/admin/__init__.py`**

Add import:

```python
from blueprints.admin import sex_offender  # noqa: F401
```

**Step 4: Commit**

```bash
git add blueprints/admin/sex_offender.py templates/admin_sex_offender.html blueprints/admin/__init__.py
git commit -m "feat(sex-offender): add admin dashboard for snapshots and subscribers"
```

---

## Task 7: Run full test suite and verify

**Step 1: Run tests**

```bash
source venv/bin/activate
python -m pytest tests/test_sex_offender_scraper.py tests/test_sex_offender_delta.py -v
python -m py_compile blueprints/sex_offender.py blueprints/admin/sex_offender.py sex_offender_alerts.py
```

Expected: all pass, no syntax errors.

**Step 2: Commit**

```bash
git commit -m "test(sex-offender): verify scraper, delta, and route syntax"
```

---

## Summary of New Files

| File | Purpose |
|------|---------|
| `sex_offender_scraper.py` | Weekly registry scraper |
| `sex_offender_delta.py` | Delta engine + change classifier |
| `sex_offender_alerts.py` | Proximity alert notification engine |
| `blueprints/sex_offender.py` | Public routes + API |
| `blueprints/admin/sex_offender.py` | Admin dashboard |
| `templates/sex_offender_updates.html` | Weekly changes list |
| `templates/sex_offender_county.html` | County map + registrants |
| `templates/sex_offender_alerts.html` | Alert subscription form |
| `templates/admin_sex_offender.html` | Admin snapshot view |
| `tests/test_sex_offender_scraper.py` | Scraper tests |
| `tests/test_sex_offender_delta.py` | Delta engine tests |

## Modified Files

| File | Change |
|------|--------|
| `init_db.py` | Schema + migration |
| `app.py` | Register blueprint, nav link |
| `blueprints/admin/__init__.py` | Register admin blueprint |
