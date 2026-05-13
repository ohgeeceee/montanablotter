from __future__ import annotations

import re
import sqlite3


def slugify_address(street: str, city: str, state: str = 'MT', zip_code: str = '') -> str:
    parts = [street, city, state, zip_code]
    raw = ' '.join(part for part in parts if part)
    slug = raw.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_-]+", "-", slug)
    return slug.strip("-") or 'unknown'


def parse_address_parts(
    raw_address: str,
    *,
    fallback_city: str = '',
    fallback_state: str = 'MT',
) -> dict[str, str]:
    value = ' '.join((raw_address or '').strip().split())
    zip_code = ''
    match = re.search(r'\b(\d{5}(?:-\d{4})?)\s*$', value)
    if match:
        zip_code = match.group(1)
        value = value[:match.start()].strip().rstrip(',').strip()
    state = fallback_state
    city = fallback_city
    street = value
    parts = [part.strip() for part in value.split(',') if part.strip()]
    if len(parts) >= 3:
        street = ', '.join(parts[:-2])
        city = parts[-2] or fallback_city
        state_part = parts[-1].strip()
        state_match = re.search(r'\b([A-Z]{2})\b', state_part.upper())
        if state_match:
            state = state_match.group(1)
    elif len(parts) == 2:
        street = parts[0]
        city_or_state = parts[1]
        state_match = re.search(r'\b([A-Z]{2})\b', city_or_state.upper())
        if state_match and city_or_state.upper() == state_match.group(1):
            state = state_match.group(1)
        else:
            city = city_or_state
    return {
        'street': street,
        'city': city,
        'state': state,
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
