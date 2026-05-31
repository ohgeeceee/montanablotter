#!/usr/bin/env python3
"""
Weekly probe of all Montana jail roster and blotter sources.
Reports which sources are online, broken, or have changed.
Run via cron: 0 6 * * 1 (Mondays 6am)
"""
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.discovery.mt_jurisdictions import COUNTIES

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; MontanaBlotterBot/1.0; +https://montanablotter.com)"})

REPORT_PATH = Path("/root/montanablotter/scripts/discovery/weekly_probe_report.json")

ZUERCHER_COUNTIES = [
    "broadwater","carbon","gallatin","jefferson","madison","meagher",
    "ravalli","roosevelt","rosebud","stillwater","valley","wheatland"
]

KNOWN_HTML_URLS = {
    "beaverhead": "https://beaverheadcountymt.gov/departments/sheriff/",
    "big-horn": "https://www.bighorncountymt.gov/239/Detention",
    "carbon": "https://carbonmt.gov/sheriff/",
    "cascade": "https://www.cascadecountymt.gov/314/Inmate-Roster",
    "dawson": "https://www.dawsoncountymontana.com/sheriff",
    "fergus": "https://fergusmt.gov/detention-center-roster",
    "glacier": "https://glaciercountymt.gov/category/jail-roster/",
    "granite": "https://granitecountyjail.org/",
    "lewis-and-clark": "https://www.lccountymt.gov/Sheriff/Detention-Center",
    "mineral": "https://co.mineral.mt.us/departments/sheriff/",
    "park": "https://www.parkcounty.org/Government-Departments/Sheriff-s-Office/Inmates-Housed/",
    "phillips": "https://phillipscosheriff.com/inmates/",
    "pondera": "https://ponderacountyjail.org/inmate-search/",
    "powell": "https://www.powellcountymt.gov/sheriff/page/detention-facility",
    "ravalli": "https://ravallicounty.gov/239/Adult-Detention-Center",
    "silver-bow": "https://co.silverbow.mt.us/3274/Detention-Center",
    "valley": "https://www.valleycountymt.gov/1288/Jail-Roster",
}

POLICE_BLOTTER_URLS = {
    "missoula_public": "https://www.ci.missoula.mt.us/195/Police-Blotter",
    "whitefish": "https://www.cityofwhitefish.org/191/Police-Blotter",
    "bozeman_calls": "https://services.arcgis.com/pVdY94ef7Pa4vKpE/arcgis/rest/services/Bozeman_Police_Calls/FeatureServer/0/query?where=1%3D1&outFields=*&outSR=4326&f=json&resultRecordCount=1",
    "kalispell": "https://www.kalispell.com/199/Police-Blotter",
}

def check_url(url: str, timeout: int = 10, expect_json: bool = False) -> dict:
    try:
        r = session.get(url, timeout=timeout, allow_redirects=True)
        status = r.status_code
        ctype = r.headers.get("content-type", "")
        if expect_json and status == 200:
            try:
                data = r.json()
                if data.get("error"):
                    return {"status": status, "ok": False, "error": data["error"].get("message","JSON error")}
                return {"status": status, "ok": True, "has_data": bool(data.get("features"))}
            except:
                return {"status": status, "ok": False, "error": "invalid JSON"}
        return {"status": status, "ok": status == 200 and "text/html" in ctype}
    except requests.exceptions.Timeout:
        return {"status": -1, "ok": False, "error": "timeout"}
    except Exception as e:
        return {"status": -1, "ok": False, "error": str(e)[:60]}

def check_zuercher_api(county: str) -> dict:
    url = f"https://{county}-so-mt.zuercherportal.com/api/portal/inmates/load"
    payload = {
        "name": "", "race": "all", "sex": "all", "cell_block": "all",
        "held_for_agency": "any", "in_custody": datetime.utcnow().strftime("%Y-%m-%dT00:00:00.000Z"),
        "paging": {"start": 0, "count": 5},
        "sorting": {"sort_by_column_tag": "name", "sort_descending": False},
    }
    try:
        r = session.post(url, json=payload, headers={"Referer": f"https://{county}-so-mt.zuercherportal.com/#/inmates"}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return {"status": 200, "ok": True, "records": len(data.get("records", [])), "total": data.get("total_record_count", 0)}
        return {"status": r.status_code, "ok": False, "error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"status": -1, "ok": False, "error": str(e)[:60]}

def main():
    report = {
        "probed_at": datetime.utcnow().isoformat(),
        "zuercher_api": {},
        "html_rosters": {},
        "police_blotters": {},
        "changes": [],
    }

    print("Probing Zuercher APIs...")
    for county in ZUERCHER_COUNTIES:
        res = check_zuercher_api(county)
        report["zuercher_api"][county] = res
        status = "OK" if res["ok"] else "FAIL"
        print(f"  {county:12} | {status} | {res.get('records','')} {res.get('error','')}")
        time.sleep(0.3)

    print("Probing HTML rosters...")
    for slug, url in KNOWN_HTML_URLS.items():
        res = check_url(url)
        report["html_rosters"][slug] = res
        status = "OK" if res["ok"] else "FAIL"
        print(f"  {slug:15} | {status} | {res.get('error','')}")
        time.sleep(0.3)

    print("Probing police blotter endpoints...")
    for name, url in POLICE_BLOTTER_URLS.items():
        res = check_url(url, expect_json=("arcgis" in url))
        report["police_blotters"][name] = res
        status = "OK" if res["ok"] else "FAIL"
        print(f"  {name:15} | {status} | {res.get('error','')}")
        time.sleep(0.3)

    # Load previous report for comparison
    if REPORT_PATH.exists():
        prev = json.loads(REPORT_PATH.read_text())
        for category in ["zuercher_api", "html_rosters", "police_blotters"]:
            for key, val in report[category].items():
                prev_val = prev.get(category, {}).get(key, {})
                if prev_val.get("ok") != val.get("ok"):
                    report["changes"].append({
                        "category": category,
                        "source": key,
                        "from_ok": prev_val.get("ok"),
                        "to_ok": val.get("ok"),
                    })

    REPORT_PATH.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nReport saved to {REPORT_PATH}")
    if report["changes"]:
        print(f"CHANGES DETECTED ({len(report['changes'])}):")
        for c in report["changes"]:
            print(f"  {c['category']}/{c['source']}: ok={c['from_ok']} -> {c['to_ok']}")
    else:
        print("No changes since last probe.")

if __name__ == "__main__":
    main()
