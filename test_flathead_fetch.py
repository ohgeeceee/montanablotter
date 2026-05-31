#!/usr/bin/env python3
import sys
sys.path.insert(0, '/root/montanablotter')
from services.ingestion.fetchers.flathead_inmate import fetch_flathead_bookings

records = fetch_flathead_bookings()
print(f'Fetched {len(records)} records')
if records:
    for r in records[:5]:
        print(r)
        print('---')
else:
    print('No records returned')
