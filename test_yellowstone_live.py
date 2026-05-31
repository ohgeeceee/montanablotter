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
if records:
    for r in records[:10]:
        print(r)
