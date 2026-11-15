#!/usr/bin/env python3
import sys
_APP_ROOT = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(_APP_ROOT, "config.py")):
    _parent = os.path.dirname(_APP_ROOT)
    if _parent == _APP_ROOT:
        break
    _APP_ROOT = _parent
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)
from services.ingestion.fetchers.yellowstone_inmate import fetch_bookings

records = fetch_bookings(
    "https://www.yellowstonecountymt.gov/sheriff/detention/dcsearch.asp",
    fetch_charges=True,
    max_charge_lookups=0,
)
print(f"Fetched {len(records)} records")

# Count how many got charge enrichment vs default
enriched = sum(1 for r in records if "Charge details available on the official Yellowstone County inmate page." not in r.charges_summary)
print(f"Enriched with charges: {enriched}")

# Show a few samples
for r in records[:5]:
    print(f"  {r.person_name} | {r.booking_at} | {r.charges_summary[:80]}...")
