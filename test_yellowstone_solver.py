#!/usr/bin/env python3
import sys
_APP_ROOT = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(_APP_ROOT, "config.py")):
    _parent = os.path.dirname(_APP_ROOT)
    if _parent == _APP_ROOT:
        break
    _APP_ROOT = _parent
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)
from services.ingestion.fetchers.yellowstone_inmate import _solve_prompt, _parse_full_roster
import requests, re

url = "https://www.yellowstonecountymt.gov/sheriff/detention/dcsearch.asp"
s = requests.Session()
s.headers.update({"User-Agent": "Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://mtblotter.com)"})
page = s.get(url, timeout=45).text

try:
    answer = _solve_prompt(page)
    print("prompt answer:", answer)
except Exception as e:
    print("solver failed:", e)
    answer = None

if answer:
    resp = s.post(url, data={"ViewFullRoster": "True", "Answer": answer, "action": "Search"}, timeout=45)
    print("status:", resp.status_code)
    print("url:", resp.url)
    rows = _parse_full_roster(resp.text)
    print("parsed rows:", len(rows))
    if rows:
        print("first row:", rows[0])
