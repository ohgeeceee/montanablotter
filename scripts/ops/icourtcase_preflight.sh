#!/usr/bin/env bash
set -euo pipefail

ROOT="/root/montanablotter"
PY="$ROOT/venv/bin/python"

cd "$ROOT"

if [[ -z "${ICOURTCASE_BASE_URLS:-}" ]]; then
  echo "ERROR: ICOURTCASE_BASE_URLS is not set"
  exit 2
fi
if [[ -z "${ICOURTCASE_SEARCH_PATHS:-}" ]]; then
  echo "ERROR: ICOURTCASE_SEARCH_PATHS is not set"
  exit 2
fi
if [[ -z "${ICOURTCASE_COOKIE_HEADER:-}" ]]; then
  echo "ERROR: ICOURTCASE_COOKIE_HEADER is not set"
  exit 2
fi

"$PY" - <<'PY'
import os
from datetime import date, timedelta
import requests

base = os.environ["ICOURTCASE_BASE_URLS"].split(",")[0].strip().rstrip("/")
path = os.environ["ICOURTCASE_SEARCH_PATHS"].split(",")[0].strip()
cookie = os.environ["ICOURTCASE_COOKIE_HEADER"].strip()
date_from = (date.today() - timedelta(days=1)).isoformat()
url = f"{base}{path}?county=Yellowstone&date_from={date_from}&page=1"

resp = requests.get(
    url,
    timeout=30,
    headers={
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Cookie": cookie,
    },
)
body = (resp.text or "").lower()
if resp.status_code >= 400:
    raise SystemExit(f"ERROR: preflight HTTP status {resp.status_code} for {url}")
if "request rejected" in body and "support id" in body:
    raise SystemExit("ERROR: preflight blocked by WAF (request rejected)")
print(f"OK: preflight reachable status={resp.status_code}")
PY
