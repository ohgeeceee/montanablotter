#!/usr/bin/env python3
"""
discover_sources.py
===================
Periodic discovery scan for new Montana law enforcement data sources.
Runs weekly via cron and reports potential new scrapable sources.
"""

import argparse
import json
import logging
import re
import sqlite3
import sys
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests

sys.path.insert(0, "/root/montanablotter")
try:
    from config import BASE_DIR
except Exception:
    BASE_DIR = "/root/montanablotter"

DB_PATH = f"{BASE_DIR}/data/blotter.db"
LOG_PATH = f"{BASE_DIR}/logs/source_discovery.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
)
logger = logging.getLogger("source_discovery")


# Known sheriff website patterns to check
SHERIFF_SITES = [
    ("Beaverhead", "https://www.beaverheadcounty.org/sheriff"),
    ("Big Horn", "https://www.bighorncountymt.gov/sheriff"),
    ("Blaine", "https://www.blainecounty.org/sheriff"),
    ("Broadwater", "https://www.broadwatercountysheriff.org/roster.php"),
    ("Carbon", "https://www.carboncounty.mt.gov/sheriff"),
    ("Carter", "https://www.cartercountymt.gov/sheriff"),
    ("Cascade", "https://www.cascadecountymt.gov/314/Inmate-Roster"),
    ("Chouteau", "https://www.chouteaucCountyMT.gov/sheriff"),
    ("Custer", "https://www.co.custer.mt.us/sheriff"),
    ("Daniels", "https://www.danielscountymt.gov/sheriff"),
    ("Dawson", "https://www.dawsoncountymontana.org/sheriff"),
    ("Deer Lodge", "https://www.anacondadeerlodge.mt.gov/sheriff"),
    ("Fallon", "https://www.falloncounty.net/sheriff"),
    ("Fergus", "https://www.ferguscounty.gov/sheriff"),
    ("Flathead", "https://apps.flathead.mt.gov/jailroster/"),
    ("Gallatin", "https://gallatin-so-mt.zuercherportal.com/#/inmates"),
    ("Garfield", "https://www.garfieldcounty.com/sheriff"),
    ("Glacier", "https://www.glaciercountymt.gov/sheriff"),
    ("Golden Valley", "https://www.goldenvvalleycounty.org/sheriff"),
    ("Granite", "https://www.granitecountymt.gov/sheriff"),
    ("Hill", "https://www.hillcounty.us/sheriff"),
    ("Jefferson", "https://jefferson-so-mt.zuercherportal.com/#/inmates"),
    ("Judith Basin", "https://www.co.judith-basin.mt.us/sheriff"),
    ("Lake", "https://www.lakecountyMT.gov/sheriff"),
    ("Lewis and Clark", "https://www.lccountymt.gov/sheriff"),
    ("Liberty", "https://www.libertycountyMT.gov/sheriff"),
    ("Lincoln", "https://www.lincolncountymt.gov/sheriff"),
    ("Madison", "https://www.madisoncountymt.gov/sheriff"),
    ("McCone", "https://www.mcconecountymt.com/sheriff"),
    ("Meagher", "https://www.meaghercounty.org/sheriff"),
    ("Mineral", "https://www.mineralcountymt.gov/sheriff"),
    ("Missoula", "https://webapps.missoulacounty.us/jailroster/Inmates"),
    ("Musselshell", "https://www.co.musselshell.mt.us/sheriff"),
    ("Park", "https://www.parkcounty.org/sheriff"),
    ("Petroleum", "https://www.petroleumcountymt.org/sheriff"),
    ("Phillips", "https://www.phillipscounty.org/sheriff"),
    ("Pondera", "https://www.ponderacountymontana.org/sheriff"),
    ("Powder River", "https://www.powderrivercounty.org/sheriff"),
    ("Powell", "https://www.powellcountymt.gov/sheriff"),
    ("Prairie", "https://www.prairiecountyMT.gov/sheriff"),
    ("Ravalli", "https://ravallicounty.gov/239/Adult-Detention-Center"),
    ("Richland", "https://www.richlandcountymt.gov/sheriff"),
    ("Roosevelt", "https://www.rooseveltcounty.org/sheriff"),
    ("Rosebud", "https://www.rosebudcountymt.gov/sheriff"),
    ("Sanders", "https://sanders-mt.publiclogs.com/"),
    ("Sheridan", "https://www.sheridancountymt.gov/sheriff"),
    ("Silver Bow", "https://www.silverbowcountymt.gov/sheriff"),
    ("Stillwater", "https://www.stillwatercountymt.gov/sheriff"),
    ("Sweet Grass", "https://www.sweetgrasscounty.gov/sheriff"),
    ("Teton", "https://www.tetoncountymt.gov/sheriff"),
    ("Toole", "https://www.toolecountymt.gov/sheriff"),
    ("Treasure", "https://www.treasurecounty.org/sheriff"),
    ("Valley", "https://www.valleycountymt.net/sheriff"),
    ("Wheatland", "https://www.wheatlandcountymt.gov/sheriff"),
    ("Wibaux", "https://www.wibauxcounty.org/sheriff"),
    ("Yellowstone", "https://www.yellowstonecountymt.gov/sheriff"),
]


def _fetch_html(url: str, timeout: int = 15) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; MontanaBlotter-SourceBot/1.0; +https://montanablotter.com)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        if resp.status_code == 200:
            return resp.text
    except Exception as exc:
        logger.debug("%s fetch error: %s", url, exc)
    return ""


def _find_zuercher_portals(html: str, base_url: str) -> list[str]:
    """Find Zuercher portal links in HTML."""
    found = []
    for match in re.finditer(r"https?://[^\s\"]+\.zuercherportal\.com[^\s\"]*", html, re.IGNORECASE):
        url = match.group(0)
        if url not in found:
            found.append(url)
    return found


def _find_pdf_links(html: str, base_url: str) -> list[dict]:
    """Find PDF links that look like blotters or rosters."""
    found = []
    for match in re.finditer(r'href="([^"]+\.pdf)"', html, re.IGNORECASE):
        href = match.group(1)
        url = urljoin(base_url, href)
        text_match = re.search(r'<a[^>]*href="' + re.escape(href) + r'"[^>]*>(.*?)</a>', html, re.IGNORECASE | re.DOTALL)
        text = re.sub(r'<[^>]+>', '', text_match.group(1) if text_match else "").strip()
        lower_text = text.lower()
        if any(k in lower_text for k in ["blotter", "roster", "incarceration", "inmate", "booking", "warrant", "arrest", "log", "report"]):
            found.append({"url": url, "text": text})
    return found


def _find_tableau_dashboards(html: str, base_url: str) -> list[dict]:
    """Find Tableau/PowerBI dashboards using the deep detector."""
    from services.ingestion.dashboard_detector import detect_dashboards

    findings = detect_dashboards(html, base_url)
    return [f.to_dict() for f in findings]


def _already_tracked(conn: sqlite3.Connection, url: str) -> bool:
    """Check if a URL or its domain is already in our sources."""
    domain = urlparse(url).netloc.lower()
    c = conn.cursor()
    # Check jail_booking_sources
    try:
        c.execute("SELECT 1 FROM jail_booking_sources WHERE LOWER(roster_url) LIKE ? LIMIT 1", (f"%{domain}%",))
        if c.fetchone():
            return True
    except sqlite3.OperationalError:
        pass
    # Check ingestion_sources
    try:
        c.execute("SELECT 1 FROM ingestion_sources WHERE LOWER(source_url) LIKE ? LIMIT 1", (f"%{domain}%",))
        if c.fetchone():
            return True
    except sqlite3.OperationalError:
        pass
    return False


def run_discovery() -> dict:
    results = {
        "scan_time": datetime.now(timezone.utc).isoformat(),
        "new_zuercher": [],
        "new_pdfs": [],
        "new_dashboards": [],
        "errors": [],
    }

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    for county_name, url in SHERIFF_SITES:
        try:
            html = _fetch_html(url)
            if not html:
                continue

            # Zuercher portals
            for portal_url in _find_zuercher_portals(html, url):
                if not _already_tracked(conn, portal_url):
                    results["new_zuercher"].append({"county": county_name, "url": portal_url})
                    logger.info("NEW Zuercher portal: %s - %s", county_name, portal_url)

            # PDF links
            for pdf in _find_pdf_links(html, url):
                if not _already_tracked(conn, pdf["url"]):
                    results["new_pdfs"].append({"county": county_name, **pdf})
                    logger.info("NEW PDF: %s - %s (%s)", county_name, pdf["text"], pdf["url"])

            # Dashboards
            for dash in _find_tableau_dashboards(html, url):
                dash_url = dash["url"]
                if not _already_tracked(conn, dash_url):
                    results["new_dashboards"].append({"county": county_name, **dash})
                    logger.info("NEW dashboard: %s - %s (%s)", county_name, dash.get("platform"), dash_url)

        except Exception as exc:
            logger.error("Discovery error for %s: %s", county_name, exc)
            results["errors"].append({"county": county_name, "error": str(exc)})

    conn.close()

    # Also check for known city PD sites
    city_sites = [
        ("Great Falls PD", "https://www.greatfallsmt.net/government/police-department"),
        ("Helena PD", "https://www.helenamt.gov/police"),
        ("Butte PD", "https://www.buttemt.gov/police"),
        ("Kalispell PD", "https://www.kalispell.com/police"),
        ("Miles City PD", "https://www.milescity.com/police"),
        ("Havre PD", "https://www.havre.mt.gov/police"),
        ("Glendive PD", "https://www.glendive.us/police"),
        ("Dillon PD", "https://www.dillonmt.gov/police"),
    ]

    for city_name, url in city_sites:
        try:
            html = _fetch_html(url)
            if not html:
                continue
            for pdf in _find_pdf_links(html, url):
                if not _already_tracked(conn, pdf["url"]):
                    results["new_pdfs"].append({"city": city_name, **pdf})
                    logger.info("NEW city PDF: %s - %s (%s)", city_name, pdf["text"], pdf["url"])
        except Exception as exc:
            logger.error("Discovery error for %s: %s", city_name, exc)

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover new Montana law enforcement data sources.")
    parser.add_argument("--output", default="", help="Write JSON results to this file")
    args = parser.parse_args()

    logger.info("Starting source discovery scan...")
    results = run_discovery()

    total_new = len(results["new_zuercher"]) + len(results["new_pdfs"]) + len(results["new_dashboards"])
    logger.info(
        "Discovery complete: %d new sources (zuercher=%d, pdfs=%d, dashboards=%d, errors=%d)",
        total_new,
        len(results["new_zuercher"]),
        len(results["new_pdfs"]),
        len(results["new_dashboards"]),
        len(results["errors"]),
    )

    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        logger.info("Results written to %s", args.output)
    else:
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
