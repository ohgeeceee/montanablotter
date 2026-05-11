"""
Probe CrimeMapping.com /map/GetOrganizations for all Montana agencies.
Uses the same request pattern as crimemapping_fetcher.py.
"""
import json
import random
import requests

ROTATING_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:137.0) Gecko/20100101 Firefox/137.0",
]

session = requests.Session()
session.headers.update({
    "User-Agent":       random.choice(ROTATING_USER_AGENTS),
    "Accept":           "application/json, text/javascript, */*; q=0.01",
    "Accept-Language":  "en-US,en;q=0.9",
    "Referer":          "https://www.crimemapping.com/map/mt/billings/",
    "X-Requested-With": "XMLHttpRequest",
})

# Prime session
map_url = "https://www.crimemapping.com/map/mt/billings/"
session.get(map_url, timeout=30)

# Fetch organizations
url = "https://www.crimemapping.com/map/GetOrganizations"
resp = session.post(url, data={"accessStaging": "false"}, timeout=30)
resp.raise_for_status()
orgs = resp.json()

# Known Montana org_ids from current MONTANA_AGENCIES list
known_ids = {513, 587, 122, 682, 641, 643, 642, 620}

# The response only has x, y, id, nm. We need to look up each org's detail
# to get state/city/county. But we can infer Montana by the name or by
# checking the OrganizationDetail endpoint. Let's fetch details for all orgs
# and filter by state == MT.

montana_orgs = []
for o in orgs:
    org_id = o.get("id")
    name = o.get("nm", "")
    # Quick heuristic: if name contains "Montana" or "MT" or known MT cities
    if not org_id:
        continue
    org_id_int = int(org_id)
    if org_id_int in known_ids:
        montana_orgs.append({
            "org_id": org_id_int,
            "agency_name": name,
            "county": "",
            "city": "",
            "raw": o,
        })

print(f"Total organizations returned: {len(orgs)}")
print(f"Matched by known IDs: {len(montana_orgs)}")

# Now fetch details for all orgs to find state=MT
# The endpoint is /map/GetOrganizationDetailJson
org_detail_url = "https://www.crimemapping.com/map/GetOrganizationDetailJson"

mt_orgs_detailed = []
for o in orgs:
    org_id = int(o.get("id", 0))
    try:
        detail_resp = session.post(org_detail_url, data={"orgID": org_id}, timeout=30)
        detail = detail_resp.json()
        state = detail.get("State", "")
        if state and str(state).strip().upper() == "MT":
            mt_orgs_detailed.append({
                "org_id": org_id,
                "agency_name": detail.get("OrganizationName", o.get("nm", "")),
                "county": detail.get("County", ""),
                "city": detail.get("City", ""),
                "state": state,
                "address": detail.get("Address", ""),
                "raw": detail,
            })
    except Exception as e:
        print(f"Error fetching detail for org_id={org_id}: {e}")
        continue

print(f"\n{'org_id':>8} | {'agency_name':<45} | {'county':<16} | {'city':<16}")
print("-" * 95)
for o in sorted(mt_orgs_detailed, key=lambda x: x["org_id"]):
    print(f"{o['org_id']:>8} | {o['agency_name']:<45} | {o['county']:<16} | {o['city']:<16}")

print()
new_ids = [o["org_id"] for o in mt_orgs_detailed if o["org_id"] not in known_ids]
missing_ids = [oid for oid in known_ids if oid not in {o["org_id"] for o in mt_orgs_detailed}]

if new_ids:
    print(f"NEW Montana agencies not in current MONTANA_AGENCIES list: {sorted(new_ids)}")
else:
    print("No new Montana agencies found.")

if missing_ids:
    print(f"Known agencies MISSING from endpoint: {sorted(missing_ids)}")
else:
    print("All known agencies still present on endpoint.")

# Save full responses
with open("/root/montanablotter/getorganizations_response.json", "w") as f:
    json.dump(orgs, f, indent=2)
with open("/root/montanablotter/montana_agencies_detailed.json", "w") as f:
    json.dump(mt_orgs_detailed, f, indent=2)

print("\nFiles saved:")
print("  /root/montanablotter/getorganizations_response.json")
print("  /root/montanablotter/montana_agencies_detailed.json")
