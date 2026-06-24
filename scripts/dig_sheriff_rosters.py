#!/usr/bin/env python3
"""Deep-dig uncovered Montana sheriff sites for roster/blotter PDFs and links."""
from __future__ import annotations

import re
import sys
import urllib3
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
sys.path.insert(0, "/root/montanablotter")
from services.ingestion.source_scout import CANDIDATE_SITES

UA = "Mozilla/5.0 (compatible; MontanaBlotter-Probe/2.0)"
TIMEOUT = 25

TRACKED = {
    "yellowstone","missoula","gallatin","hill","flathead","lake","cascade","jefferson",
    "sanders","ravalli","rosebud","madison","carbon","stillwater","meagher","wheatland",
    "valley","roosevelt","broadwater",
}

def _county_slug(name: str) -> str:
    return name.lower().replace(" ", "")

SITES: dict[str, tuple[str, str]] = {}
for county, city, agency, url in CANDIDATE_SITES:
    if not county:
        continue
    slug = _county_slug(county)
    if slug in TRACKED:
        continue
    if "sheriff" in agency.lower() and slug not in SITES:
        SITES[slug] = (county, url)

def _fetch(url: str) -> tuple[int, str, str]:
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT, allow_redirects=True, verify=False)
        return r.status_code, r.text, r.url
    except Exception as exc:
        return -1, str(exc)[:300], url

def _find_links(base: str, text: str, extra_patterns: tuple = ()) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if not text:
        return found
    keywords = ['roster','inmate','jail','detention','booking','current inmates','warrant','pdf','correctional']
    keywords.extend(extra_patterns)
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', text, re.I|re.S):
        href, txt = m.groups()
        txt = re.sub(r'<[^>]+>', '', txt).strip()
        combined = (href + " " + txt).lower()
        if any(k in combined for k in keywords):
            full = urljoin(base, href)
            if full not in [x[0] for x in found]:
                found.append((full, txt[:120]))
    for m in re.finditer(r'<iframe[^>]+src=["\']([^"\']+)["\']', text, re.I):
        full = urljoin(base, m.group(1))
        if full not in [x[0] for x in found]:
            found.append((full, "iframe"))
    return found

def probe(county_slug: str, county_name: str, url: str) -> dict:
    status, text, final = _fetch(url)
    result = {"county": county_name, "url": url, "status": status, "final_url": final, "links": []}
    if status != 200:
        result["error"] = text[:200]
        return result

    # First-level links
    links = _find_links(final, text)
    # Follow likely pages one level deep
    deep_links: list[tuple[str, str]] = []
    for link, txt in links:
        if link.endswith('.pdf'):
            deep_links.append((link, txt + " [PDF]"))
            continue
        if any(k in (link+txt).lower() for k in ['detention','jail','roster','inmate','correctional','warrant']):
            s2, t2, f2 = _fetch(link)
            if s2 == 200:
                for l2, t2txt in _find_links(f2, t2):
                    if l2.endswith('.pdf') or 'roster' in (l2+t2txt).lower() or 'inmate' in (l2+t2txt).lower():
                        deep_links.append((l2, f"{txt} -> {t2txt}"))
    result["links"] = deep_links
    return result

def main():
    results = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(probe, slug, name, url): slug for slug, (name, url) in SITES.items()}
        for f in as_completed(futures):
            results.append(f.result())

    print("=== COUNTIES WITH ROSTER-LIKE LINKS ===")
    for r in sorted(results, key=lambda x: x["county"]):
        if r["links"]:
            print(f"\n{r['county']} ({r['status']}) {r['final_url']}")
            for link, txt in r["links"]:
                print(f"  -> {link}")
                print(f"     TEXT: {txt}")

    print("\n=== COUNTIES WITHOUT ROSTER-LIKE LINKS ===")
    for r in sorted(results, key=lambda x: x["county"]):
        if not r["links"]:
            print(f"{r['county']:15s} status={r['status']:4d} final={r['final_url']}")

if __name__ == "__main__":
    main()
