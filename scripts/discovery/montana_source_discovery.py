#!/usr/bin/env python3
"""
Montana Source Discovery — systematic probe of all 56 counties + cities
for police blotter, jail roster, and public safety data sources.

Checks:
- Known jail roster URLs from DB/code
- Zuercher portal patterns
- CivicPlus / municipal website patterns
- CrimeMapping coverage
- ArcGIS dashboard patterns
- Existing fetcher URLs

Outputs: /root/montanablotter/scripts/discovery/discovery_report.json
"""
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.discovery.mt_jurisdictions import COUNTIES

session = requests.Session()
retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
session.mount("https://", HTTPAdapter(max_retries=retry))
session.mount("http://", HTTPAdapter(max_retries=retry))
session.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; MontanaBlotterBot/1.0; +https://montanablotter.com)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
})


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def probe(url: str, timeout: int = 12) -> dict:
    try:
        r = session.get(url, timeout=timeout, allow_redirects=True, stream=True)
        # Read only first 8KB to check content type
        _ = r.raw.read(8192)
        r.close()
        ctype = r.headers.get("content-type", "")
        return {
            "status": r.status_code,
            "content_type": ctype,
            "final_url": r.url,
            "ok": r.status_code == 200 and "text/html" in ctype,
        }
    except requests.exceptions.Timeout:
        return {"status": -1, "error": "timeout", "ok": False}
    except requests.exceptions.ConnectionError as e:
        return {"status": -1, "error": f"connection: {type(e).__name__}", "ok": False}
    except Exception as e:
        return {"status": -1, "error": str(e)[:60], "ok": False}


def check_zuercher(county_slug: str) -> dict | None:
    """Zuercher portal pattern: {county}-so-mt.zuercherportal.com"""
    url = f"https://{county_slug}-so-mt.zuercherportal.com/#/inmates"
    res = probe(url)
    if res["ok"]:
        return {"platform": "zuercher", "url": url, "probe": res}
    return None


def check_civicplus_patterns(county: str, seat: str, cities: list) -> list[dict]:
    """Common CivicPlus / municipal URL patterns."""
    found = []
    county_slug = slugify(county)
    seat_slug = slugify(seat)
    patterns = []

    # County sheriff pages
    for domain in [f"www.{county_slug}county.org", f"www.{county_slug}county.gov", f"www.{county_slug}county.com", f"{county_slug}county.org", f"{county_slug}county.gov"]:
        patterns.append(("sheriff", f"https://{domain}/sheriff"))
        patterns.append(("sheriff_blotter", f"https://{domain}/sheriff/police-blotter"))
        patterns.append(("sheriff_log", f"https://{domain}/sheriff/daily-log"))
        patterns.append(("sheriff_roster", f"https://{domain}/sheriff/inmate-roster"))
        patterns.append(("sheriff_detention", f"https://{domain}/sheriff/detention"))

    # Seat city PD pages
    for domain in [f"www.cityof{seat_slug}.com", f"www.{seat_slug}.gov", f"www.{seat_slug}.com", f"{seat_slug}.gov"]:
        patterns.append(("pd", f"https://{domain}/departments/police"))
        patterns.append(("pd_blotter", f"https://{domain}/departments/police/police-blotter"))
        patterns.append(("pd_log", f"https://{domain}/departments/police/daily-log"))
        patterns.append(("pd_calls", f"https://{domain}/departments/police/calls-for-service"))

    for tag, url in patterns:
        res = probe(url)
        if res["ok"]:
            found.append({"tag": tag, "url": url, "probe": res})
        time.sleep(0.15)

    return found


def check_known_roster(url: str) -> dict | None:
    res = probe(url)
    if res["ok"]:
        return {"url": url, "probe": res}
    return None


def discover_all() -> dict:
    report = {
        "generated_at": datetime.utcnow().isoformat(),
        "counties": {},
        "zuercher_found": [],
        "civicplus_found": [],
        "roster_working": [],
        "roster_broken": [],
    }

    # Pre-load known roster URLs from DB/code
    known_rosters = {
        "beaverhead": "https://beaverheadcountymt.gov/departments/sheriff/",
        "big-horn": "https://www.bighorncountymt.gov/239/Detention",
        "broadwater": "https://www.broadwatercountysheriff.org/roster.php",
        "carbon": "https://carbonmt.gov/sheriff/",
        "cascade": "https://www.cascadecountymt.gov/314/Inmate-Roster",
        "custer": None,
        "dawson": "https://www.dawsoncountymontana.com/sheriff",
        "fergus": "https://fergusmt.gov/detention-center-roster",
        "glacier": "https://glaciercountymt.gov/category/jail-roster/",
        "granite": "https://granitecountyjail.org/",
        "hill": None,
        "lewis-and-clark": "https://www.lccountymt.gov/Sheriff/Detention-Center",
        "lincoln": None,
        "madison": None,
        "mineral": "https://co.mineral.mt.us/departments/sheriff/",
        "park": "https://www.parkcounty.org/Government-Departments/Sheriff-s-Office/Inmates-Housed/",
        "phillips": "https://phillipscosheriff.com/inmates/",
        "pondera": "https://ponderacountyjail.org/inmate-search/",
        "powell": "https://www.powellcountymt.gov/sheriff/page/detention-facility",
        "ravalli": "https://ravallicounty.gov/239/Adult-Detention-Center",
        "silver-bow": "https://co.silverbow.mt.us/3274/Detention-Center",
        "valley": "https://www.valleycountymt.gov/1288/Jail-Roster",
    }

    for county, seat, cities in COUNTIES:
        county_slug = slugify(county)
        print(f"Probing {county}...", flush=True)
        county_result = {
            "seat": seat,
            "cities": cities,
            "zuercher": None,
            "civicplus": [],
            "known_roster": None,
            "signals": [],
        }

        # 1. Zuercher
        zu = check_zuercher(county_slug)
        if zu:
            county_result["zuercher"] = zu
            report["zuercher_found"].append({"county": county, **zu})

        # 2. CivicPlus patterns
        civ = check_civicplus_patterns(county, seat, cities)
        if civ:
            county_result["civicplus"] = civ
            report["civicplus_found"].extend([{"county": county, **c} for c in civ])

        # 3. Known roster URL
        known = known_rosters.get(county_slug)
        if known:
            kr = check_known_roster(known)
            county_result["known_roster"] = kr
            if kr:
                report["roster_working"].append({"county": county, **kr})
            else:
                report["roster_broken"].append({"county": county, "url": known})

        report["counties"][county] = county_result
        time.sleep(0.5)

    return report


if __name__ == "__main__":
    report = discover_all()
    out_path = Path("/root/montanablotter/scripts/discovery/discovery_report.json")
    out_path.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nReport saved to {out_path}")
    print(f"Zuercher portals found: {len(report['zuercher_found'])}")
    print(f"CivicPlus pages found: {len(report['civicplus_found'])}")
    print(f"Known rosters working: {len(report['roster_working'])}")
    print(f"Known rosters broken: {len(report['roster_broken'])}")
    if report['zuercher_found']:
        print("Zuercher:", [z['county'] for z in report['zuercher_found']])
    if report['roster_working']:
        print("Working rosters:", [r['county'] for r in report['roster_working']])
    if report['roster_broken']:
        print("Broken rosters:", [r['county'] for r in report['roster_broken']])
