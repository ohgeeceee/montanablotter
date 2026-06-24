#!/usr/bin/env python3
"""Probe uncovered Montana counties for Zuercher/publiclogs jail roster portals."""
from __future__ import annotations

import re
import sys
import time
import urllib3
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
sys.path.insert(0, "/root/montanablotter")

UA = "Mozilla/5.0 (compatible; MontanaBlotter-Probe/2.0)"
TIMEOUT = 20

# 35 uncovered Montana counties (no working tracked source)
UNCOVERED_COUNTIES = [
    "Big Horn", "Blaine", "Carter", "Chouteau", "Custer", "Daniels", "Dawson",
    "Deer Lodge", "Fallon", "Fergus", "Garfield", "Glacier", "Golden Valley",
    "Granite", "Judith Basin", "Lewis and Clark", "Liberty", "Lincoln", "McCone",
    "Mineral", "Musselshell", "Petroleum", "Phillips", "Pondera", "Powder River",
    "Powell", "Prairie", "Richland", "Sheridan", "Silver Bow", "Sweet Grass",
    "Teton", "Toole", "Treasure", "Wibaux",
]


def _slug(name: str) -> str:
    return name.lower().replace(" ", "-")


def _check(url: str) -> tuple[int, str, str, str]:
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT, allow_redirects=True, verify=False)
        text = r.text or ""
        return r.status_code, text, r.url, ""
    except requests.exceptions.Timeout:
        return -1, "timeout", url, "timeout"
    except Exception as exc:
        return -2, str(exc)[:200], url, type(exc).__name__


def probe_zuercher(county: str) -> dict:
    slug = _slug(county)
    url = f"https://{slug}-so-mt.zuercherportal.com/#/inmates"
    status, text, final_url, err = _check(url)
    result = {"county": county, "platform": "zuercher", "url": url, "status": status, "final_url": final_url}
    if err:
        result["error"] = err
    if status == 200:
        low = text.lower()
        if "maintenance" in low:
            result["note"] = "maintenance_mode"
        elif any(k in low for k in ("inmate", "roster", "booking", "zuercher")):
            result["live"] = True
            result["title"] = re.search(r'<title[^>]*>([^<]+)</title>', text, re.I).group(1).strip() if re.search(r'<title[^>]*>([^<]+)</title>', text, re.I) else ""
    return result


def probe_publiclogs(county: str) -> dict:
    slug = _slug(county)
    url = f"https://{slug}-mt.publiclogs.com/"
    status, text, final_url, err = _check(url)
    result = {"county": county, "platform": "publiclogs", "url": url, "status": status, "final_url": final_url}
    if err:
        result["error"] = err
    if status == 200:
        low = text.lower()
        if any(k in low for k in ("inmate", "roster", "jms", "public logs")):
            result["live"] = True
            result["title"] = re.search(r'<title[^>]*>([^<]+)</title>', text, re.I).group(1).strip() if re.search(r'<title[^>]*>([^<]+)</title>', text, re.I) else ""
    return result


def main():
    results = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        futures = []
        for c in UNCOVERED_COUNTIES:
            futures.append(ex.submit(probe_zuercher, c))
            futures.append(ex.submit(probe_publiclogs, c))
        for f in as_completed(futures):
            results.append(f.result())

    hits = [r for r in results if r.get("live")]
    maintenance = [r for r in results if r.get("note") == "maintenance_mode"]
    other_200 = [r for r in results if r.get("status") == 200 and r not in hits and r not in maintenance]

    print("=== LIVE ROSTER HITS ===")
    for r in sorted(hits, key=lambda x: (x["county"], x["platform"])):
        print(f"{r['county']:15s} {r['platform']:12s} {r['url']} -> {r['final_url']}")
        print(f"  title: {r.get('title','')[:120]}")

    print("\n=== MAINTENANCE / OTHER 200 ===")
    for r in sorted(maintenance + other_200, key=lambda x: (x["county"], x["platform"])):
        print(f"{r['county']:15s} {r['platform']:12s} status={r['status']} note={r.get('note','')} url={r['url']}")

    print("\n=== ALL STATUSES ===")
    for r in sorted(results, key=lambda x: (x["county"], x["platform"])):
        extra = "LIVE" if r.get("live") else r.get("error", "")
        print(f"{r['county']:15s} {r['platform']:12s} status={r['status']:4d} {extra:20s} {r['url']}")


if __name__ == "__main__":
    main()
