"""Google Places Text Search (New) provider.

Verified: https://maps.googleapis.com/maps/api/place/textsearch/json exists
and returns a clean error without a key. Requires
GOOGLE_MAPS_API_KEY (or MB_GOOGLE_PLACES_KEY) in env — never hardcoded.

Business Phone / website come from the companion Places Details call
(field-masked to exactly what we store). Rate: one Details call per
result, sequential, polite.
"""
from __future__ import annotations

from services.crm.prospecting.engine import ProspectRecord
from services.crm.prospecting.providers import (
    ProviderUnavailable, VERTICALS, env, get)

TEXTSEARCH = 'https://maps.googleapis.com/maps/api/place/textsearch/json'
DETAILS = 'https://maps.googleapis.com/maps/api/place/details/json'


def _key() -> str:
    return env('MB_GOOGLE_PLACES_KEY') or env('GOOGLE_MAPS_API_KEY')


def _details(place_id: str) -> dict:
    r = get(DETAILS, params={
        'place_id': place_id, 'key': _key(),
        'fields': 'international_phone_number,website,email'})
    r.raise_for_status()
    return (r.json().get('result') or {})


def fetch(vertical: str, county: str | None = None, limit: int = 60):
    key = _key()
    if not key:
        raise ProviderUnavailable(
            'google_local: no MB_GOOGLE_PLACES_KEY / GOOGLE_MAPS_API_KEY set')
    v = VERTICALS[vertical]
    seen = set()
    for q in v.google_queries:
        query = q.format(county=county or 'Montana')
        nxt = ''
        while nxt is not None and len(seen) < limit:
            r = get(TEXTSEARCH, params={'query': query, 'key': key,
                                        **({'pagetoken': nxt} if nxt else {})})
            r.raise_for_status()
            data = r.json()
            status = data.get('status')
            if status not in ('OK', 'ZERO_RESULTS'):
                raise ProviderUnavailable(f'google_local: status {status}')
            for p in data.get('results', []):
                pid = p.get('place_id')
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                d = _details(pid)
                yield ProspectRecord(
                    business_name=p.get('name', '').strip(),
                    category=vertical,
                    phone=d.get('international_phone_number', ''),
                    website=d.get('website', ''),
                    email=d.get('email', ''),
                    address=p.get('formatted_address', ''),
                    city=_city(p),
                    source_provider='google_local',
                    source_ref=pid,
                    raw={'query': query, 'rating': p.get('rating'),
                         'user_ratings_total': p.get('user_ratings_total')})
            nxt = data.get('next_page_token')  # None => end; needs 2s delay
            if nxt:
                import time
                time.sleep(2.1)


def _city(place: dict) -> str:
    for c in place.get('address_components', []):
        if 'locality' in c.get('types', []):
            return c.get('long_name', '')
    return ''
