#!/usr/bin/env python3
"""
dashboard_detector.py
=====================
Deep detector for embedded Tableau and PowerBI dashboards on Montana
law-enforcement websites.  Identifies script tags, iframes, and JS-init
embeds, then attempts to surface direct CSV/JSON/PNG feed URLs.

Usage (standalone):
    python services/ingestion/dashboard_detector.py --url https://...

Usage (as library):
    from services.ingestion.dashboard_detector import detect_dashboards
    results = detect_dashboards(html_text, base_url)
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

logger = logging.getLogger("dashboard_detector")

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

TABLEAU_JS_RE = re.compile(
    r'<script[^>]*src=["\']([^"\']*tableau[^"\']*\.js)["\'][^>]*>',
    re.IGNORECASE,
)

POWERBI_JS_RE = re.compile(
    r'<script[^>]*src=["\']([^"\']*powerbi[^"\']*\.js)["\'][^>]*>',
    re.IGNORECASE,
)

IFRAME_EMBED_RE = re.compile(
    r'<iframe[^>]*src=["\']([^"\']+)["\'][^>]*>',
    re.IGNORECASE | re.DOTALL,
)

TABLEAU_VIZ_RE = re.compile(
    r'new\s+tableau\.Viz\s*\(\s*[^,]*,\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)

TABLEAU_URL_RE = re.compile(
    r'(?:https?:)?//[^\s"\']*tableau[^\s"\']*',
    re.IGNORECASE,
)

POWERBI_EMBED_RE = re.compile(
    r'powerbi\.\s*embed\s*\(\s*[^,]+,\s*\{[^}]*"?embedUrl"?\s*:\s*["\']([^"\']+)["\']',
    re.IGNORECASE | re.DOTALL,
)

POWERBI_URL_RE = re.compile(
    r'(?:https?:)?//[^\s"\']*powerbi[^\s"\']*',
    re.IGNORECASE,
)

# Generic export / download links
EXPORT_LINK_RE = re.compile(
    r'href=["\']([^"\']*(?:csv|json|xlsx|download|export)[^"\']*)["\']',
    re.IGNORECASE,
)

# Tableau specific: `.csv` on a view URL, or `format=csv` / `format=json`
TABLEAU_VIEW_PATH_RE = re.compile(
    r'/views/[^/]+/[^/?\s"\']+',
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DashboardFinding:
    platform: str  # 'tableau' | 'powerbi' | 'unknown'
    evidence_type: str  # 'script', 'iframe', 'js_init', 'url_match'
    url: str
    raw_html_snippet: str = ""
    feed_urls: list[str] = field(default_factory=list)
    screenshot_url: Optional[str] = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "evidence_type": self.evidence_type,
            "url": self.url,
            "feed_urls": self.feed_urls,
            "screenshot_url": self.screenshot_url,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _abs_url(url: str, base: str) -> str:
    if url.startswith("//"):
        return f"https:{url}"
    return urljoin(base, url)


def _is_tableau(url: str) -> bool:
    netloc = urlparse(url).netloc.lower()
    return "tableau" in netloc or "tableausoftware" in netloc


def _is_powerbi(url: str) -> bool:
    netloc = urlparse(url).netloc.lower()
    return "powerbi" in netloc or "app.powerbi.com" in netloc


def _build_tableau_feed_urls(view_url: str) -> tuple[list[str], Optional[str]]:
    """
    Given a Tableau view URL, try to construct direct CSV/JSON/PNG URLs.
    Returns (feed_urls, screenshot_url).
    """
    feeds: list[str] = []
    screenshot: Optional[str] = None

    parsed = urlparse(view_url)
    path = parsed.path.rstrip("/")

    # Skip asset URLs (scripts, images, fonts)
    if any(path.lower().endswith(ext) for ext in (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".woff", ".woff2", ".ttf", ".eot")):
        return feeds, screenshot

    # 1) Direct .csv / .json suffix on the view path
    for ext in (".csv", ".json"):
        candidate = urlunparse(
            parsed._replace(path=path + ext, query="", fragment="")
        )
        feeds.append(candidate)

    # 2) ?:format=csv / ?:format=json query parameter
    for fmt in ("csv", "json"):
        qdict = parse_qs(parsed.query, keep_blank_values=True)
        qdict[":format"] = [fmt]
        new_query = urlencode(qdict, doseq=True)
        candidate = urlunparse(parsed._replace(query=new_query))
        feeds.append(candidate)

    # 3) PNG screenshot
    qdict = parse_qs(parsed.query, keep_blank_values=True)
    qdict[":format"] = ["png"]
    qdict[":size"] = ["1200,900"]
    screenshot = urlunparse(
        parsed._replace(query=urlencode(qdict, doseq=True))
    )

    return feeds, screenshot


def _probe_tableau_from_js(html: str, base_url: str) -> list[DashboardFinding]:
    """Extract tableau.Viz() init calls and derive URLs."""
    findings: list[DashboardFinding] = []
    for match in TABLEAU_VIZ_RE.finditer(html):
        url = _abs_url(match.group(1), base_url)
        feeds, screenshot = _build_tableau_feed_urls(url)
        findings.append(
            DashboardFinding(
                platform="tableau",
                evidence_type="js_init",
                url=url,
                raw_html_snippet=match.group(0)[:200],
                feed_urls=feeds,
                screenshot_url=screenshot,
                notes=["Detected via new tableau.Viz() init"],
            )
        )
    return findings


def _probe_powerbi_from_js(html: str, base_url: str) -> list[DashboardFinding]:
    """Extract powerbi.embed() calls."""
    findings: list[DashboardFinding] = []
    for match in POWERBI_EMBED_RE.finditer(html):
        url = _abs_url(match.group(1), base_url)
        findings.append(
            DashboardFinding(
                platform="powerbi",
                evidence_type="js_init",
                url=url,
                raw_html_snippet=match.group(0)[:200],
                notes=["Detected via powerbi.embed() init"],
            )
        )
    return findings


def _probe_iframes(html: str, base_url: str) -> list[DashboardFinding]:
    findings: list[DashboardFinding] = []
    for match in IFRAME_EMBED_RE.finditer(html):
        src = match.group(1)
        if not src or src.startswith("javascript:"):
            continue
        url = _abs_url(src, base_url)
        platform: Optional[str] = None
        if _is_tableau(url):
            platform = "tableau"
        elif _is_powerbi(url):
            platform = "powerbi"
        else:
            # Skip non-dashboard iframes
            continue

        feeds: list[str] = []
        screenshot: Optional[str] = None
        notes: list[str] = ["Detected via <iframe> embed"]

        if platform == "tableau":
            feeds, screenshot = _build_tableau_feed_urls(url)

        findings.append(
            DashboardFinding(
                platform=platform,
                evidence_type="iframe",
                url=url,
                raw_html_snippet=match.group(0)[:200],
                feed_urls=feeds,
                screenshot_url=screenshot,
                notes=notes,
            )
        )
    return findings


def _probe_script_tags(html: str, base_url: str) -> list[DashboardFinding]:
    findings: list[DashboardFinding] = []

    for match in TABLEAU_JS_RE.finditer(html):
        url = _abs_url(match.group(1), base_url)
        findings.append(
            DashboardFinding(
                platform="tableau",
                evidence_type="script",
                url=url,
                raw_html_snippet=match.group(0)[:200],
                notes=["Detected via tableau.js script tag"],
            )
        )

    for match in POWERBI_JS_RE.finditer(html):
        url = _abs_url(match.group(1), base_url)
        findings.append(
            DashboardFinding(
                platform="powerbi",
                evidence_type="script",
                url=url,
                raw_html_snippet=match.group(0)[:200],
                notes=["Detected via powerbi.js script tag"],
            )
        )

    return findings


def _probe_url_matches(html: str, base_url: str) -> list[DashboardFinding]:
    """Catch any remaining raw URLs that smell like dashboards."""
    findings: list[DashboardFinding] = []
    seen: set[str] = set()

    for pattern, platform in ((TABLEAU_URL_RE, "tableau"), (POWERBI_URL_RE, "powerbi")):
        for match in pattern.finditer(html):
            url = _abs_url(match.group(0), base_url)
            if url in seen:
                continue
            seen.add(url)

            feeds: list[str] = []
            screenshot: Optional[str] = None
            notes: list[str] = ["Detected via raw URL match in page"]

            if platform == "tableau":
                feeds, screenshot = _build_tableau_feed_urls(url)

            findings.append(
                DashboardFinding(
                    platform=platform,
                    evidence_type="url_match",
                    url=url,
                    feed_urls=feeds,
                    screenshot_url=screenshot,
                    notes=notes,
                )
            )
    return findings


def _priority(evidence_type: str) -> int:
    """Higher number = more specific / preferred."""
    return {
        "url_match": 1,
        "script": 2,
        "iframe": 3,
        "js_init": 4,
    }.get(evidence_type, 0)


def _deduplicate(findings: list[DashboardFinding]) -> list[DashboardFinding]:
    seen: dict[str, DashboardFinding] = {}
    for f in findings:
        key = f"{f.platform}|{f.url}"
        if key not in seen:
            seen[key] = f
        else:
            # Merge notes / feed URLs
            existing = seen[key]
            existing.notes = list(dict.fromkeys(existing.notes + f.notes))
            existing.feed_urls = list(dict.fromkeys(existing.feed_urls + f.feed_urls))
            if _priority(f.evidence_type) > _priority(existing.evidence_type):
                existing.evidence_type = f.evidence_type
                existing.raw_html_snippet = f.raw_html_snippet or existing.raw_html_snippet
    return list(seen.values())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_dashboards(html: str, base_url: str) -> list[DashboardFinding]:
    """
    Scan HTML for Tableau / PowerBI artefacts and return structured findings.
    """
    if not html or not base_url:
        return []

    findings: list[DashboardFinding] = []
    findings.extend(_probe_script_tags(html, base_url))
    findings.extend(_probe_iframes(html, base_url))
    findings.extend(_probe_tableau_from_js(html, base_url))
    findings.extend(_probe_powerbi_from_js(html, base_url))
    findings.extend(_probe_url_matches(html, base_url))

    return _deduplicate(findings)


def detect_dashboards_as_dicts(html: str, base_url: str) -> list[dict]:
    return [f.to_dict() for f in detect_dashboards(html, base_url)]


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def _fetch_html(url: str, timeout: int = 15) -> str:
    import requests  # local import so library import stays light

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; MontanaBlotter-DashboardBot/1.0; +https://montanablotter.com)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        if resp.status_code == 200:
            return resp.text
    except Exception as exc:
        logger.debug("Fetch error for %s: %s", url, exc)
    return ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Detect Tableau/PowerBI dashboards on a page.")
    parser.add_argument("--url", required=True, help="URL to scan")
    parser.add_argument("--output", default="", help="Write JSON results to file")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    html = _fetch_html(args.url)
    if not html:
        print(json.dumps({"error": "Failed to fetch page"}, indent=2))
        sys.exit(1)

    findings = detect_dashboards_as_dicts(html, args.url)
    payload = {"url": args.url, "findings": findings}

    if args.output:
        with open(args.output, "w") as f:
            json.dump(payload, f, indent=2)
        logger.info("Wrote %d findings to %s", len(findings), args.output)
    else:
        print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
