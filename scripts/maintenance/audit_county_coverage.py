#!/usr/bin/env python3
"""Live coverage audit for all Montana county jail-roster sources.

For every county in jail_bookings.TRACKED_SOURCES, fetch its roster_url, run the
generic Montana inmate parser, and classify the result:

  has_data        -- parser extracted >=1 inmate row
  reachable_empty -- HTTP 200 but parser found 0 rows (landing page / no roster)
  unreachable     -- network/HTTP error
  no_url          -- registry has no roster_url

This is READ-ONLY (no DB writes, no scraping side effects beyond a GET). It tells
us exactly which of the 56 counties already work, which stubs need wiring, and
which have no feasible online source.

Run from repo root:
    venv/bin/python3 scripts/maintenance/audit_county_coverage.py
"""
from __future__ import annotations

import sys
import time

import config
import requests

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"


def _classify(slug: str, url: str | None) -> tuple[str, str]:
    if not url:
        return "no_url", "registry has no roster_url"
    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=25, allow_redirects=True)
    except Exception as exc:
        return "unreachable", f"{type(exc).__name__}: {str(exc)[:80]}"
    if resp.status_code != 200:
        return "unreachable", f"HTTP {resp.status_code}"
    # Try the generic parser if the module is importable. It fetches the URL
    # itself (we already confirmed reachability above).
    try:
        from services.ingestion.fetchers.generic_mt_inmate import fetch_generic_mt_bookings
        recs = fetch_generic_mt_bookings(url, county_slug=slug)
        n = len(recs)
    except Exception as exc:
        return "reachable_empty", f"parser error: {type(exc).__name__}: {str(exc)[:60]}"
    if n > 0:
        return "has_data", f"{n} rows parsed"
    return "reachable_empty", "200 but 0 rows (no roster data on page)"


def main() -> int:
    from services.ingestion.jail_bookings import TRACKED_SOURCES
    rows = []
    for slug, meta in TRACKED_SOURCES.items():
        url = meta.get("roster_url")
        status, detail = _classify(slug, url)
        rows.append((slug, meta.get("county_name", ""), status, detail))
        time.sleep(0.3)  # be gentle on the counties

    order = {"has_data": 0, "reachable_empty": 1, "unreachable": 2, "no_url": 3}
    rows.sort(key=lambda r: (order.get(r[2], 9), r[0]))

    totals: dict[str, int] = {}
    print(f"{'COUNTY':<16}{'STATUS':<16}{'DETAIL'}")
    print("-" * 80)
    for slug, name, status, detail in rows:
        totals[status] = totals.get(status, 0) + 1
        print(f"{slug:<16}{status:<16}{detail}")
    print("-" * 80)
    print("SUMMARY:", ", ".join(f"{k}={v}" for k, v in sorted(totals.items())))
    print(f"TOTAL counties in registry: {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
