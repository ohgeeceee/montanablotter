#!/usr/bin/env python3
import sys
sys.path.insert(0, "/root/montanablotter")
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
