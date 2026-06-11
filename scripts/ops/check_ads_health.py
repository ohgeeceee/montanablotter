#!/usr/bin/env python3
"""
Health check for the public site ad configuration on montanablotter.com.

Verifies the live homepage:
  1. Responds with HTTP 200
  2. Contains NO Monetag verification meta tag or loader script
  3. Contains NO AdSense references (regression guard)

Cron example (every 15 minutes):
  */15 * * * * /root/montanablotter/venv/bin/python3 \\
      /root/montanablotter/scripts/ops/check_ads_health.py

Environment overrides:
  MB_HEALTH_URL     URL to check (default: http://localhost:5000/)
  MB_HEALTH_TIMEOUT Request timeout in seconds (default: 10)
  MB_HEALTH_LOG     Log file path (default: /root/montanablotter/logs/ad_health.log)

Exit codes:
  0 = all checks passed
  1 = one or more content checks failed (regression)
  2 = could not reach the site (network/HTTP error)
"""

import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SITE_URL = os.environ.get("MB_HEALTH_URL", "http://localhost:5000/")
TIMEOUT = float(os.environ.get("MB_HEALTH_TIMEOUT", "10"))
LOG_FILE = Path(os.environ.get(
    "MB_HEALTH_LOG",
    "/root/montanablotter/logs/ad_health.log",
))

MONETAG_PATTERN = re.compile(
    r'name="monetag"|quge5\.com/88/tag\.min\.js',
    re.IGNORECASE,
)
# Anything matching this regex in the response is treated as an AdSense
# regression. Covers the loader, the ad-client ID, the doubleclick domain,
# and the legacy googlesyndication CDN.
ADSENSE_PATTERN = re.compile(
    r"adsbygoogle|googlesyndication|googleads\.g\.doubleclick|ca-pub-9126743762075114",
    re.IGNORECASE,
)

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("ad_health")


def fetch(url: str) -> tuple[int, str]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "montanablotter-ad-health/1.0"},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.status, resp.read().decode("utf-8", errors="replace")


def main() -> int:
    started = time.time()
    try:
        status, body = fetch(SITE_URL)
    except urllib.error.HTTPError as exc:
        log.error("REGRESSION HTTP %s from %s", exc.code, SITE_URL)
        return 2
    except urllib.error.URLError as exc:
        log.error("REGRESSION network error for %s: %s", SITE_URL, exc.reason)
        return 2
    except Exception as exc:  # pragma: no cover
        log.error("REGRESSION unexpected error for %s: %s", SITE_URL, exc)
        return 2

    failures: list[str] = []

    if status != 200:
        failures.append(f"HTTP {status} (expected 200)")

    monetag_matches = sorted(set(MONETAG_PATTERN.findall(body)))
    if monetag_matches:
        failures.append(f"Monetag references present: {monetag_matches}")

    bad = sorted(set(ADSENSE_PATTERN.findall(body)))
    if bad:
        failures.append(f"AdSense references present: {bad}")

    elapsed_ms = int((time.time() - started) * 1000)
    if failures:
        log.error("REGRESSION (%dms) %s: %s", elapsed_ms, SITE_URL, "; ".join(failures))
        return 1

    log.info("OK (%dms) %s", elapsed_ms, SITE_URL)
    return 0


if __name__ == "__main__":
    sys.exit(main())
