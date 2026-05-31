#!/usr/bin/env python3
import sys
sys.path.insert(0, "/root/montanablotter")
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
