#!/usr/bin/env python3
"""
Discover police blotter / public safety data sources for Montana jurisdictions.
Probes common URL patterns, portals, and APIs.
Outputs JSON report of found sources.
"""

import argparse
import json
import re
import sys
import time
import urllib.request
from urllib.error import HTTPError, URLError
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from mt_jurisdictions import COUNTIES

TIMEOUT = 8
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; MontanaBlotterBot/1.0; +https://montanablotter.com)"
}

import concurrent.futures

# Common URL pattern templates to try
PATTERNS = {
    "pd_homepage": [
        "https://www.cityof{city_slug}.com/departments/police",
        "https://www.cityof{city_slug}.com/police",
        "https://www.{city_slug}.gov/departments/police",
        "https://www.{city_slug}.gov/police",
        "https://{city_slug}police.com",
        "https://{city_slug}pd.com",
    ],
    "so_homepage": [
        "https://www.{county_slug}county.org/sheriff",
        "https://www.{county_slug}county.org/sheriffs-office",
        "https://www.{county_slug}county.com/sheriff",
        "https://www.{county_slug}county.gov/sheriff",
        "https://sheriff.{county_slug}county.org",
    ],
    "blotter_page": [
        "https://www.cityof{city_slug}.com/departments/police/blotter",
        "https://www.cityof{city_slug}.com/police/blotter",
        "https://www.{city_slug}.gov/departments/police/blotter",
        "https://www.{city_slug}.gov/police/blotter",
        "https://www.{city_slug}pd.com/blotter",
        "https://www.{county_slug}county.org/sheriff/blotter",
        "https://www.{county_slug}county.org/sheriff/police-log",
        "https://www.{county_slug}county.org/sheriff/daily-log",
        "https://www.{county_slug}county.org/sheriff/incidents",
    ],
    "rss_feed": [
        "https://www.cityof{city_slug}.com/departments/police/feed",
        "https://www.cityof{city_slug}.com/police/feed",
        "https://www.{city_slug}.gov/departments/police/feed",
        "https://www.{city_slug}.gov/police/feed",
        "https://www.{county_slug}county.org/sheriff/feed",
    ],
    "crimemapping": [
        "https://www.crimemapping.com/map/mt/{city_slug}/",
        "https://www.crimemapping.com/map/mt/{county_slug}/",
    ],
    "zuercher": [
        "https://{county_slug}-so-mt.zuercherportal.com/#/inmates",
        "https://{city_slug}-pd-mt.zuercherportal.com/#/inmates",
    ],
    "civicplus": [
        "https://www.{county_slug}county.org/civicax/filebank/blobdload.aspx?blobid=",
        "https://www.{city_slug}.gov/civicax/filebank/blobdload.aspx?blobid=",
    ],
    "arcgis_dashboard": [
        "https://{city_slug}.maps.arcgis.com/home/item.html",
        "https://gisweb.{city_slug}.net/",
        "https://gis.{county_slug}county.org/",
        "https://maps.{county_slug}county.org/",
    ],
    "lexisnexis_cj": [
        "https://communitycrimemap.com/?address={city_slug},mt",
        "https://communitycrimemap.com/?address={county_slug},mt",
    ],
    "calls_for_service": [
        "https://www.cityof{city_slug}.com/departments/police/calls-for-service",
        "https://www.{city_slug}.gov/departments/police/calls-for-service",
        "https://www.{city_slug}.gov/police/calls-for-service",
        "https://www.cityof{city_slug}.com/police/calls-for-service",
    ],
    "activity_log": [
        "https://www.cityof{city_slug}.com/departments/police/activity-log",
        "https://www.{city_slug}.gov/departments/police/activity-log",
        "https://www.{city_slug}.gov/police/activity-log",
        "https://www.cityof{city_slug}.com/police/activity-log",
        "https://www.{county_slug}county.org/sheriff/activity-log",
    ],
    "daily_bulletin": [
        "https://www.cityof{city_slug}.com/departments/police/daily-bulletin",
        "https://www.{city_slug}.gov/departments/police/daily-bulletin",
        "https://www.{city_slug}.gov/police/daily-bulletin",
    ],
    "incident_reports": [
        "https://www.cityof{city_slug}.com/departments/police/incident-reports",
        "https://www.{city_slug}.gov/departments/police/incident-reports",
        "https://www.{city_slug}.gov/police/incident-reports",
        "https://www.{county_slug}county.org/sheriff/incident-reports",
    ],
    "press_releases": [
        "https://www.cityof{city_slug}.com/departments/police/press-releases",
        "https://www.{city_slug}.gov/departments/police/press-releases",
        "https://www.{city_slug}.gov/police/press-releases",
        "https://www.{county_slug}county.org/sheriff/press-releases",
    ],
}


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS, method="HEAD")
    try:
        resp = urllib.request.urlopen(req, timeout=TIMEOUT)
        return resp.getcode(), resp.geturl(), resp.headers.get("content-type", "")
    except HTTPError as e:
        return e.code, url, ""
    except URLError as e:
        return 0, url, str(e.reason)
    except Exception as e:
        return -1, url, str(e)


def probe_patterns(patterns, **kwargs):
    results = []
    for tmpl in patterns:
        try:
            url = tmpl.format(**kwargs)
        except KeyError:
            continue
        code, final_url, ctype = fetch(url)
        if code in (200, 301, 302, 307, 308):
            results.append({"url": url, "code": code, "content_type": ctype})
        time.sleep(0.3)
    return results


def discover_for_jurisdiction(county, seat, cities):
    county_slug = slugify(county)
    seat_slug = slugify(seat)
    results = {
        "county": county,
        "county_slug": county_slug,
        "county_seat": seat,
        "seat_slug": seat_slug,
        "cities": cities,
        "sources": {},
    }

    # Try county sheriff patterns (seat as primary city)
    for pattern_name, pattern_list in PATTERNS.items():
        found = probe_patterns(
            pattern_list,
            county_slug=county_slug,
            city_slug=seat_slug,
        )
        if found:
            results["sources"][pattern_name] = found

    # Try each city
    for city in cities:
        city_slug = slugify(city)
        city_results = {}
        for pattern_name, pattern_list in PATTERNS.items():
            found = probe_patterns(
                pattern_list,
                county_slug=county_slug,
                city_slug=city_slug,
            )
            if found:
                city_results[pattern_name] = found
        if city_results:
            if "city_sources" not in results:
                results["city_sources"] = {}
            results["city_sources"][city] = city_results

    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--county", help="Specific county to probe")
    parser.add_argument("--batch", type=int, default=0, help="Batch number (0=all)")
    parser.add_argument("--batch-size", type=int, default=10, help="Counties per batch")
    parser.add_argument("--output", default="/root/montanablotter/scripts/discovery/discovery_results.json")
    args = parser.parse_args()

    counties = COUNTIES
    if args.county:
        counties = [c for c in COUNTIES if c[0].lower() == args.county.lower()]
    elif args.batch > 0:
        start = (args.batch - 1) * args.batch_size
        end = start + args.batch_size
        counties = COUNTIES[start:end]

    all_results = []
    for county, seat, cities in counties:
        print(f"Probing {county}...", file=sys.stderr)
        result = discover_for_jurisdiction(county, seat, cities)
        all_results.append(result)
        # Be polite
        time.sleep(1.0)

    with open(args.output, "w") as f:
        json.dump(all_results, f, indent=2)

    # Print summary
    found_counties = [r["county"] for r in all_results if r.get("sources") or r.get("city_sources")]
    print(f"\nProbed {len(counties)} counties. Found sources in {len(found_counties)}:", file=sys.stderr)
    for c in found_counties:
        print(f"  - {c}", file=sys.stderr)


if __name__ == "__main__":
    main()
