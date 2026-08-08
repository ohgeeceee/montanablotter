#!/usr/bin/env python3
"""Discover working sheriff/jail URLs for 27 Montana counties."""
import urllib.request, urllib.error, socket, os, hashlib, sys
from concurrent.futures import ThreadPoolExecutor, as_completed

socket.setdefaulttimeout(15)
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
COUNTIES = {
    'blaine': [
        'https://www.blainecounty-mt.gov/sheriff',
        'https://blainecounty-mt.gov/',
        'https://www.blainecountymt.gov/',
        'https://www.blainecountymt.gov/sheriff',
    ],
    'carter': [
        'https://www.cartercountymt.gov/161/Sheriff',
        'https://cartercountymt.gov/',
        'https://www.cartercountymt.gov/',
    ],
    'chouteau': [
        'https://chouteaucountymt.gov/',
        'https://www.chouteaucountymt.gov/',
        'https://chouteaucountymt.gov/sheriff',
    ],
    'daniels': [
        'https://www.danielscountymt.gov/sheriff',
        'https://danielscountymt.gov/',
        'https://www.danielscountymt.gov/',
    ],
    'dawson': [
        'https://dawsoncountymontana.com/',
        'https://www.dawsoncountymontana.com/',
        'https://www.dawsoncountymontana.com/sheriff',
    ],
    'deer-lodge': [
        'https://www.anacondadeerlodgecounty.com/',
        'https://anacondadeerlodgecounty.com/',
        'https://www.adlcmt.gov/',
    ],
    'garfield': [
        'https://www.garfieldcountymt.gov/sheriff',
        'https://garfieldcountymt.gov/',
        'https://www.garfieldcountymt.gov/',
    ],
    'golden-valley': [
        'https://www.goldenvalleymt.com/',
        'https://goldenvalleymt.com/',
    ],
    'granite': [
        'https://www.granitecountymt.gov/',
        'https://granitecountymt.gov/',
        'https://www.granitecountymt.gov/sheriff',
    ],
    'judith-basin': [
        'https://www.co.judith-basin.mt.us/',
        'https://co.judith-basin.mt.us/',
        'https://www.judithbasincountymt.gov/',
        'https://judithbasincountymt.gov/',
    ],
    'liberty': [
        'https://www.co.liberty.mt.gov/',
        'https://www.libertycountymt.gov/',
        'https://libertycountymt.gov/',
    ],
    'mccone': [
        'https://www.mcconecountymt.gov/',
        'https://mcconecountymt.gov/',
        'https://www.mccone.mt.gov/',
    ],
    'mineral': [
        'https://co.mineral.mt.us/',
        'https://co.mineral.mt.us/departments/sheriff/',
    ],
    'musselshell': [
        'https://www.musselshellcounty.org/',
        'https://musselshellcounty.org/',
        'https://www.musselshellcountymt.gov/',
    ],
    'petroleum': [
        'https://petroleumcountymt.gov/',
        'https://www.petroleumcountymt.gov/',
    ],
    'phillips': [
        'https://phillipscosheriff.com/',
        'https://www.phillipscosheriff.com/',
        'https://www.phillipscountymt.gov/',
        'https://co.phillips.mt.us/',
    ],
    'pondera': [
        'https://www.ponderacountymt.gov/',
        'https://ponderacountymt.gov/',
        'https://ponderacountyjail.org/',
    ],
    'powder-river': [
        'https://www.powderivercountymt.gov/',
        'https://powderivercountymt.gov/',
        'https://www.powderrivercountymt.gov/',
    ],
    'powell': [
        'https://www.powellcountymt.gov/',
        'https://powellcountymt.gov/',
        'https://www.powellco.org/',
    ],
    'prairie': [
        'https://www.prairiecountymt.gov/',
        'https://prairiecountymt.gov/',
    ],
    'richland': [
        'https://www.richland.org/',
        'https://richland.org/',
        'https://www.richlandcountymt.gov/',
    ],
    'sheridan': [
        'https://www.sheridancountymt.gov/',
        'https://sheridancountymt.gov/',
        'https://www.co.sheridan.mt.us/',
    ],
    'sweet-grass': [
        'https://www.sweetgrasscountymt.gov/',
        'https://sweetgrasscountymt.gov/',
        'https://www.co.sweetgrass.mt.us/',
    ],
    'teton': [
        'https://www.tetoncomt.gov/',
        'https://tetoncomt.gov/',
        'https://www.tetonmt.org/',
    ],
    'toole': [
        'https://www.co.toole.mt.gov/',
        'https://www.toolecountymt.gov/',
        'https://toolecountymt.gov/',
    ],
    'treasure': [
        'https://www.treasurecountymt.gov/',
        'https://treasurecountymt.gov/',
    ],
    'wibaux': [
        'https://www.wibauxcountymt.gov/',
        'https://wibauxcountymt.gov/',
    ],
}

OUT = '/root/montanablotter/docs/research/jail-roster-coverage'
RAW = f'{OUT}/raw'
os.makedirs(RAW, exist_ok=True)

def fetch(slug, url):
    out = f'{RAW}/{slug}.html'
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': '*/*'})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            body = r.read()
            size = len(body)
            if size < 1024:
                return (slug, url, r.status, size, None)
            with open(out, 'wb') as f:
                f.write(body)
            return (slug, url, r.status, size, None)
    except urllib.error.HTTPError as e:
        return (slug, url, e.code, 0, None)
    except Exception as e:
        return (slug, url, 0, 0, str(e))

# Build full task list, dedupe
tasks = []
for slug, urls in COUNTIES.items():
    seen = set()
    for u in urls:
        if u not in seen:
            seen.add(u)
            tasks.append((slug, u))

results = []
with ThreadPoolExecutor(max_workers=6) as ex:
    futs = {ex.submit(fetch, s, u): (s, u) for s, u in tasks}
    for fut in as_completed(futs):
        results.append(fut.result())

# First good (200, size>5k) per slug
best = {}
for slug, url, code, size, err in results:
    if code == 200 and (size > 5000 or size == 0):
        if slug not in best:
            best[slug] = (url, size)
        else:
            # Prefer shorter URL (homepage) as a fallback landing
            if len(url) < len(best[slug][0]):
                best[slug] = (url, size)

print("=== Best URL per county ===")
for slug in sorted(best):
    print(f"  {slug:15s} {best[slug][0]:60s} {best[slug][1]} bytes")

print()
print("=== Counties without working URL (showing all attempts) ===")
for slug in sorted(COUNTIES):
    if slug not in best:
        print(f"  {slug}:")
        for slug2, url, code, size, err in results:
            if slug2 == slug:
                print(f"    {code} ({size}b) {url} {err or ''}")
