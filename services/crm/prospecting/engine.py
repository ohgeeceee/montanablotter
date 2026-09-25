"""Dedupe + upsert engine: prospect records -> advertising_prospects.

Dedupe keys, strongest first:
  1. (category, license_number) — state-registry rows; unique index
     idx_ad_prospect_ext_key.
  2. business_key — the existing services.monetization.advertising_center
     .business_key normalizer (word-set of the name). Exact match only;
     no fuzzy merge, per that module's comment.
  3. normalized email — same firm submitting via two sources.

On match: fill empty fields, never overwrite operator-entered data;
append the source row to advertising_prospect_sources (source_type =
provider name, source_id = 0 since engine rows are ephemeral — the
UNIQUE(source_type, source_id) PK is reused with a per-provider
synthetic id from the raw payload when available).

Status/stage is NEVER changed by ingestion. An 'active' client that a
registry refresh re-touches stays 'active'.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from services.monetization.advertising_center import business_key
from services.crm.prospecting.counties import county_for


@dataclass
class ProspectRecord:
    business_name: str
    category: str                     # bail|legal|pi|process_server
    contact_name: str = ''
    email: str = ''
    phone: str = ''
    website: str = ''
    license_number: str = ''
    city: str = ''
    county: str = ''                  # explicit wins over derived
    address: str = ''
    state: str = 'MT'
    source_provider: str = ''
    source_ref: str = ''              # stable id from the provider payload
    raw: dict = field(default_factory=dict)

    def synthetic_source_id(self) -> int:
        key = f'{self.source_provider}|{self.source_ref or self.business_name}'
        return int(hashlib.sha256(key.encode()).hexdigest()[:12], 16)

    def resolved_county(self) -> str:
        return self.county or county_for(self.city, self.address, self.state)


def _norm_email(email: str) -> str:
    return (email or '').strip().casefold()


def _by_business_key(conn, business_key_value: str):
    return conn.execute(
        'SELECT * FROM advertising_prospects WHERE business_key = ? LIMIT 1',
        (business_key_value,)).fetchone()


def upsert_prospect(conn, rec: ProspectRecord, dry_run: bool = False) -> str:
    """Return 'inserted' | 'updated' | 'unchanged' | 'skipped'."""
    name = re.sub(r'\s+', ' ', (rec.business_name or '').strip())
    if not name or (rec.state or 'MT').upper() not in ('MT', 'MONTANA', ''):
        return 'skipped'
    bkey = business_key(name)
    lic = re.sub(r'[^A-Za-z0-9\-/]', '', (rec.license_number or '')).upper()
    email = _norm_email(rec.email)
    county = rec.resolved_county()

    existing = None
    if lic:
        existing = conn.execute(
            'SELECT * FROM advertising_prospects WHERE license_number = ? '
            'AND (category = ? OR category = \'general\') LIMIT 1',
            (lic, rec.category)).fetchone()
    if existing is None:
        existing = _by_business_key(conn, bkey)
    if existing is None and email:
        existing = conn.execute(
            'SELECT * FROM advertising_prospects '
            'WHERE lower(trim(email)) = ? AND email != \'\' LIMIT 1', (email,)).fetchone()

    if existing is None:
        if not dry_run:
            cur = conn.execute(
                '''INSERT INTO advertising_prospects
                   (business_name, business_key, contact_name, email, phone,
                    counties, category, target_vertical, source, website,
                    license_number, address, city, notes, stage)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'new')''',
                (name, bkey, rec.contact_name, email, rec.phone, county,
                 rec.category, rec.category, 'prospect_engine', rec.website,
                 lic, rec.address, rec.city,
                 json.dumps(rec.raw)[:4000] if rec.raw else ''))
            conn.execute(
                'INSERT OR IGNORE INTO advertising_prospect_sources '
                '(source_type, source_id, prospect_id) VALUES (?,?,?)',
                (f'engine:{rec.source_provider}', rec.synthetic_source_id(), cur.lastrowid))
        return 'inserted'

    # Update: fill blanks only. Operator-entered values win.
    fills = {}
    for col, val in (('contact_name', rec.contact_name), ('phone', rec.phone),
                     ('website', rec.website), ('license_number', lic),
                     ('address', rec.address), ('city', rec.city)):
        if val and not (existing[col] or '').strip():
            fills[col] = val
    if email and not (existing['email'] or '').strip():
        fills['email'] = email
    if county and not (existing['counties'] or '').strip():
        fills['counties'] = county
    if rec.category and (existing['category'] or 'general') == 'general':
        fills['category'] = rec.category
        if not (existing['target_vertical'] or '').strip():
            fills['target_vertical'] = rec.category
    if fills and not dry_run:
        sets = ', '.join(f'{k} = ?' for k in fills)
        conn.execute(
            f'UPDATE advertising_prospects SET {sets}, '
            'version = version + 1, updated_at = datetime(\'now\') WHERE id = ?',
            (*fills.values(), existing['id']))
    if not dry_run:
        conn.execute(
            'INSERT OR IGNORE INTO advertising_prospect_sources '
            '(source_type, source_id, prospect_id) VALUES (?,?,?)',
            (f'engine:{rec.source_provider}', rec.synthetic_source_id(), existing['id']))
    return 'updated' if fills else 'unchanged'


def run_ingest(conn, records, dry_run: bool = False) -> dict:
    counts = {'inserted': 0, 'updated': 0, 'unchanged': 0, 'skipped': 0}
    for rec in records:
        counts[upsert_prospect(conn, rec, dry_run=dry_run)] += 1
    if not dry_run:
        conn.commit()
    return counts
