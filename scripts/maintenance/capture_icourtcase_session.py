#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

PORTAL_START_URL = "https://dcportal.pubcourts.mt.gov/fullcourtweb/start.do"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture Montana court portal session/network info for scraper configuration."
    )
    parser.add_argument("--wait-seconds", type=int, default=180, help="How long to keep browser open")
    parser.add_argument(
        "--output-json",
        default="reports/civil_filings/icourtcase_capture.json",
        help="Where to write raw capture output",
    )
    parser.add_argument(
        "--output-env",
        default="reports/civil_filings/icourtcase_capture.env",
        help="Where to write scraper env suggestions",
    )
    parser.add_argument("--headless", action="store_true", help="Run browser headless (debug only)")
    parser.add_argument(
        "--include-cookies",
        action="store_true",
        help="Include session cookies in generated env output",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_json = Path(args.output_json)
    output_env = Path(args.output_env)
    output_json.parent.mkdir(parents=True, exist_ok=True)

    seen_requests: list[dict[str, str]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=args.headless,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        context = browser.new_context()
        page = context.new_page()

        def on_request(req) -> None:
            url = req.url
            if "pubcourts.mt.gov" not in url:
                return
            lower = url.lower()
            if not any(token in lower for token in ("civil", "case", "roa", "search", "courtcaseid")):
                return
            seen_requests.append(
                {
                    "method": req.method,
                    "url": url,
                    "resource_type": req.resource_type,
                }
            )

        page.on("request", on_request)
        page.goto(PORTAL_START_URL, wait_until="domcontentloaded", timeout=90000)
        print(f"Opened {PORTAL_START_URL}")
        print(
            "Use the browser to log in, navigate to Cases > Civil Case, run searches, then return here. "
            f"Capturing for {args.wait_seconds} seconds..."
        )
        page.wait_for_timeout(args.wait_seconds * 1000)

        cookies = context.cookies()
        browser.close()

    unique_urls: list[str] = []
    for item in seen_requests:
        if item["url"] not in unique_urls:
            unique_urls.append(item["url"])

    parsed = [urlparse(url) for url in unique_urls]
    base_urls = sorted({f"{part.scheme}://{part.netloc}" for part in parsed if part.scheme and part.netloc})
    paths = sorted({part.path for part in parsed if part.path})

    cookie_pairs = []
    for cookie in cookies:
        domain = cookie.get("domain", "")
        if "pubcourts.mt.gov" in domain:
            cookie_pairs.append(f"{cookie['name']}={cookie['value']}")
    cookie_header = "; ".join(cookie_pairs)

    capture_payload = {
        "start_url": PORTAL_START_URL,
        "request_count": len(seen_requests),
        "unique_request_count": len(unique_urls),
        "requests": seen_requests,
        "unique_urls": unique_urls,
        "base_urls": base_urls,
        "paths": paths,
        "cookie_names": [c.get("name", "") for c in cookies if "pubcourts.mt.gov" in c.get("domain", "")],
    }
    output_json.write_text(json.dumps(capture_payload, indent=2), encoding="utf-8")

    env_lines = [
        "# Suggested settings from capture_icourtcase_session.py",
        f"export ICOURTCASE_BASE_URLS={','.join(base_urls) if base_urls else 'https://dcportal.pubcourts.mt.gov'}",
        f"export ICOURTCASE_SEARCH_PATHS={','.join(paths) if paths else '/public-portal/search/civil'}",
        (
            f"export ICOURTCASE_COOKIE_HEADER={cookie_header}"
            if args.include_cookies
            else "export ICOURTCASE_COOKIE_HEADER="
        ),
    ]
    output_env.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    output_env.chmod(0o600)
    output_json.chmod(0o600)

    print(f"Wrote {output_json}")
    print(f"Wrote {output_env}")
    print(f"Captured {len(seen_requests)} matching requests ({len(unique_urls)} unique URLs).")
    if not args.include_cookies:
        print("Cookie capture disabled. Re-run with --include-cookies to export session cookie header.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
