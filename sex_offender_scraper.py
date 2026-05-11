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
import json
import math
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

        photo_match = re.search(r'<img[^>]*src="(/photos/[^"]+)"', html)
        if photo_match:
            record['photo_url'] = BASE_URL + photo_match.group(1)

        if record['address_street'] and record['address_city']:
            lat, lon = _geocode_address(record['address_street'], record['address_city'], 'MT', record['address_zip'])
            record['lat'] = lat
            record['lon'] = lon
            time.sleep(0.5)

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
            if all_registry_ids:
                placeholders = ','.join('?' * len(all_registry_ids))
                conn.execute(
                    f"UPDATE sex_offenders SET status = 'removed', updated_at = datetime('now') WHERE registry_id NOT IN ({placeholders}) AND status = 'active'",
                    list(all_registry_ids),
                )
            else:
                conn.execute(
                    "UPDATE sex_offenders SET status = 'removed', updated_at = datetime('now') WHERE status = 'active'"
                )
            conn.commit()

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
