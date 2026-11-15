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
    fetch_charges=False,
    max_charge_lookups=0,
)
print(f"Fetched {len(records)} records")
# Show date distribution
from collections import Counter
dates = [r.booking_at[:10] if r.booking_at else "None" for r in records]
for date, count in Counter(dates).most_common(10):
    print(f"{date}: {count}")
