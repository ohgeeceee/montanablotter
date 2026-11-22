#!/usr/bin/env python3
"""
Health check for the public site ad configuration on mtblotter.com.

Verifies the live homepage:
  1. Responds with HTTP 200
  2. Contains NO Monetag verification meta tag or loader script
  3. AdSense state matches configuration: when enabled with a client ID the
     loader must be present, and every configured slot must render its unit;
     when disabled there must be no AdSense markup at all

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


def _load_env_file() -> None:
    """Load .env for the slot IDs this check validates.

    The app reads .env through systemd's EnvironmentFile, but this script runs
    as a standalone cron job, so without this the configured-slot assertions
    would always be skipped.
    """
    root = Path(__file__).resolve().parent.parent.parent
    env_path = root / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_env_file()

LOG_FILE = Path(os.environ.get(
    "MB_HEALTH_LOG",
    str(Path(__file__).resolve().parent.parent / "logs" / "ad_health.log"),
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

    # AdSense is a supported revenue channel now: the loader is emitted only
    # when it is enabled AND a publisher client ID is configured, and each
    # individual unit renders only when its slot ID is set. Guard that
    # intended state instead of the old "no adsbygoogle anywhere" rule.
    adsense_enabled = os.environ.get("MB_ADSENSE_ENABLED", "true").strip().lower() in {
        "1", "true", "yes", "on",
    }
    client_id = os.environ.get("MB_ADSENSE_CLIENT_ID", "ca-pub-2061188847733201").strip()
    loader_present = "pagead2.googlesyndication.com/pagead/js/adsbygoogle.js" in body

    if adsense_enabled and client_id:
        if not loader_present:
            failures.append("AdSense enabled but the adsbygoogle.js loader is missing")
        if client_id not in body:
            failures.append(f"AdSense client ID {client_id} not referenced")
        slot_vars = [
            ("MB_ADSENSE_SLOT_HEADER", "header"),
            ("MB_ADSENSE_SLOT_SIDEBAR", "sidebar"),
            ("MB_ADSENSE_SLOT_INCONTENT", "in-content"),
            ("MB_ADSENSE_SLOT_FOOTER", "footer"),
            ("MB_ADSENSE_SLOT_ARTICLE", "article"),
        ]
        for env_name, label in slot_vars:
            slot_id = os.environ.get(env_name, "").strip()
            if not slot_id:
                continue
            if f'data-ad-slot="{slot_id}"' not in body:
                failures.append(f"AdSense {label} unit missing (slot {slot_id})")
    else:
        if loader_present:
            failures.append("AdSense disabled but the adsbygoogle.js loader is still present")
        bad = sorted(set(ADSENSE_PATTERN.findall(body)))
        if bad:
            failures.append(f"AdSense references present while disabled: {bad}")

    elapsed_ms = int((time.time() - started) * 1000)
    if failures:
        log.error("REGRESSION (%dms) %s: %s", elapsed_ms, SITE_URL, "; ".join(failures))
        return 1

    log.info("OK (%dms) %s", elapsed_ms, SITE_URL)
    return 0


if __name__ == "__main__":
    sys.exit(main())
