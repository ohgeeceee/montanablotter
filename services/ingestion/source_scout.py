#!/usr/bin/env python3
"""
source_scout.py
===============
Agent 1 of 3 in the autonomous source-discovery pipeline.

Runs every 2 hours. Scans all 56 Montana county sheriff sites, city PDs,
tribal law enforcement, and state agency pages for new blotter/roster sources
not yet tracked in source_registry or source_candidates.

Discovered candidates are written to the source_candidates table where
source_reviewer.py picks them up for Claude-powered evaluation.

Cron (add to crontab.txt):
  0 */2 * * * TZ=America/Denver /root/montanablotter/venv/bin/python3 \\
      /root/montanablotter/job_runner.py --name source_scout \\
      --log /root/montanablotter/logs/source_scout.log \\
      --workdir /root/montanablotter -- \\
      /root/montanablotter/venv/bin/python3 -m services.ingestion.source_scout
"""
from __future__ import annotations

import argparse
import logging
import re
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests

import config
from services.agents.events_bus import publish_agent_event

DB_PATH = config.DB_PATH
LOG_PATH = "/root/montanablotter/logs/source_scout.log"
UA = "Mozilla/5.0 (compatible; MontanaBlotter-Scout/2.0; +https://montanablotter.com)"

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("source_scout")


# All 56 Montana counties + known city PDs + tribal + state agencies
CANDIDATE_SITES: list[tuple[str, str, str, str]] = [
    # (county, city, agency_name, url)
    ("Beaverhead", "", "Beaverhead County Sheriff", "https://www.beaverheadcounty.org/sheriff"),
    ("Big Horn", "", "Big Horn County Sheriff", "https://www.bighorncountymt.gov/sheriff"),
    ("Blaine", "", "Blaine County Sheriff", "https://www.blainecounty.org/sheriff"),
    ("Broadwater", "", "Broadwater County Sheriff", "https://www.broadwatercountysheriff.org"),
    ("Carbon", "", "Carbon County Sheriff", "https://www.carboncounty.mt.gov/sheriff"),
    ("Carter", "", "Carter County Sheriff", "https://www.cartercountymt.gov/sheriff"),
    ("Cascade", "", "Cascade County Sheriff", "https://www.cascadecountymt.gov/sheriff"),
    ("Cascade", "", "Cascade County Inmate Roster", "https://www.cascadecountymt.gov/314/Inmate-Roster"),
    ("Chouteau", "", "Chouteau County Sheriff", "https://www.chouteaucountymt.gov/sheriff"),
    ("Custer", "", "Custer County Sheriff", "https://www.co.custer.mt.us/sheriff"),
    ("Daniels", "", "Daniels County Sheriff", "https://www.danielscountymt.gov/sheriff"),
    ("Dawson", "", "Dawson County Sheriff", "https://www.dawsoncountymontana.org/sheriff"),
    ("Deer Lodge", "", "Deer Lodge County Sheriff", "https://www.anacondadeerlodge.mt.gov/sheriff"),
    ("Fallon", "", "Fallon County Sheriff", "https://www.falloncounty.net/sheriff"),
    ("Fergus", "", "Fergus County Sheriff", "https://www.ferguscounty.gov/sheriff"),
    ("Flathead", "", "Flathead County Jail Roster", "https://apps.flathead.mt.gov/jailroster/"),
    ("Gallatin", "", "Gallatin County Jail Roster", "https://gallatin-so-mt.zuercherportal.com/#/inmates"),
    ("Garfield", "", "Garfield County Sheriff", "https://www.garfieldcounty.com/sheriff"),
    ("Glacier", "", "Glacier County Sheriff", "https://www.glaciercountymt.gov/sheriff"),
    ("Golden Valley", "", "Golden Valley County Sheriff", "https://www.goldenvalleymontana.com/sheriff"),
    ("Granite", "", "Granite County Sheriff", "https://www.granitecountymt.gov/sheriff"),
    ("Hill", "", "Hill County Sheriff", "https://www.hillcounty.us/sheriff"),
    ("Jefferson", "", "Jefferson County Jail Roster", "https://jefferson-so-mt.zuercherportal.com/#/inmates"),
    ("Judith Basin", "", "Judith Basin County Sheriff", "https://www.co.judith-basin.mt.us/sheriff"),
    ("Lake", "", "Lake County Sheriff", "https://www.lakecountymt.gov/sheriff"),
    ("Lewis and Clark", "", "Lewis and Clark County Sheriff", "https://www.lccountymt.gov/sheriff"),
    ("Liberty", "", "Liberty County Sheriff", "https://www.libertycountymt.gov/sheriff"),
    ("Lincoln", "", "Lincoln County Sheriff", "https://www.lincolncountymt.gov/sheriff"),
    ("Madison", "", "Madison County Sheriff", "https://www.madisoncountymt.gov/sheriff"),
    ("McCone", "", "McCone County Sheriff", "https://www.mcconecountymt.com/sheriff"),
    ("Meagher", "", "Meagher County Jail", "https://meagher-so-mt.zuercherportal.com/#/inmates"),
    ("Mineral", "", "Mineral County Sheriff", "https://www.mineralcountymt.gov/sheriff"),
    ("Musselshell", "", "Musselshell County Sheriff", "https://www.co.musselshell.mt.us/sheriff"),
    ("Park", "", "Park County Sheriff", "https://www.parkcounty.org/sheriff"),
    ("Petroleum", "", "Petroleum County Sheriff", "https://www.petroleumcountymt.org/sheriff"),
    ("Phillips", "", "Phillips County Sheriff", "https://www.phillipscounty.org/sheriff"),
    ("Pondera", "", "Pondera County Sheriff", "https://www.ponderacountymontana.org/sheriff"),
    ("Powder River", "", "Powder River County Sheriff", "https://www.powderrivercounty.org/sheriff"),
    ("Powell", "", "Powell County Sheriff", "https://www.powellcountymt.gov/sheriff"),
    ("Prairie", "", "Prairie County Sheriff", "https://www.prairiecountymt.gov/sheriff"),
    ("Ravalli", "", "Ravalli County Detention", "https://ravallicounty.gov/239/Adult-Detention-Center"),
    ("Richland", "", "Richland County Sheriff", "https://www.richlandcountymt.gov/sheriff"),
    ("Roosevelt", "", "Roosevelt County Jail", "https://roosevelt-so-mt.zuercherportal.com/#/inmates"),
    ("Rosebud", "", "Rosebud County Sheriff", "https://www.rosebudcountymt.gov/departments/sheriff/"),
    ("Sanders", "", "Sanders County Public Logs", "https://sanders-mt.publiclogs.com/"),
    ("Sheridan", "", "Sheridan County Sheriff", "https://www.sheridancountymt.gov/sheriff"),
    ("Silver Bow", "", "Silver Bow County Sheriff", "https://www.silverbowcountymt.gov/departments/sheriff"),
    ("Stillwater", "", "Stillwater County Jail", "https://stillwater-so-mt.zuercherportal.com/#/inmates"),
    ("Sweet Grass", "", "Sweet Grass County Sheriff", "https://www.sweetgrasscounty.gov/sheriff"),
    ("Teton", "", "Teton County Sheriff", "https://www.tetoncountymt.gov/sheriff"),
    ("Toole", "", "Toole County Sheriff", "https://www.toolecountymt.gov/sheriff"),
    ("Treasure", "", "Treasure County Sheriff", "https://www.treasurecounty.org/sheriff"),
    ("Valley", "", "Valley County Jail", "https://valley-so-mt.zuercherportal.com/#/inmates"),
    ("Wheatland", "", "Wheatland County Jail", "https://wheatland-so-mt.zuercherportal.com/#/inmates"),
    ("Wibaux", "", "Wibaux County Sheriff", "https://www.wibauxcounty.org/sheriff"),
    ("Yellowstone", "", "Yellowstone County Jail Roster", "https://www.yellowstonecountymt.gov/sheriff"),
    # City PDs
    ("Cascade", "Great Falls", "Great Falls Police Department", "https://www.greatfallsmt.net/government/police-department"),
    ("Lewis and Clark", "Helena", "Helena Police Department", "https://www.helenamt.gov/police"),
    ("Silver Bow", "Butte", "Butte-Silver Bow Police", "https://www.buttemt.gov/police"),
    ("Flathead", "Kalispell", "Kalispell Police Department", "https://www.kalispell.com/police"),
    ("Custer", "Miles City", "Miles City Police Department", "https://www.milescity.com/police"),
    ("Hill", "Havre", "Havre Police Department", "https://www.havre.mt.gov/police"),
    ("Dawson", "Glendive", "Glendive Police Department", "https://www.glendive.us/police"),
    ("Beaverhead", "Dillon", "Dillon Police Department", "https://www.dillonmt.gov/police"),
    ("Gallatin", "Bozeman", "Bozeman Police Department", "https://www.bozeman.net/departments/police"),
    ("Missoula", "Missoula", "Missoula Police Department", "https://www.ci.missoula.mt.us/1664/Police"),
    ("Yellowstone", "Billings", "Billings Police Department", "https://www.billingsmt.gov/190/Police-Department"),
    ("Flathead", "Whitefish", "Whitefish Police Department", "https://www.cityofwhitefish.org/government/police_department"),
    ("Lake", "Polson", "Polson Police Department", "https://www.cityofpolson.com/police"),
    ("Lincoln", "Libby", "Libby Police Department", "https://www.ci.libby.mt.us/police"),
    ("Ravalli", "Hamilton", "Hamilton Police Department", "https://www.cityofhamilton.net/government/police"),
    ("Carbon", "Red Lodge", "Red Lodge Police Department", "https://www.cityofredlodge.com/police"),
    ("Park", "Livingston", "Livingston Police Department", "https://www.livingstonmt.gov/police"),
    ("Gallatin", "Belgrade", "Belgrade Police Department", "https://www.cityofbelgrade.org/police"),
    # Tribal law enforcement
    ("Glacier", "Browning", "Blackfeet Tribal Police", "https://www.blackfeetnation.com/departments/law-enforcement"),
    ("Big Horn", "Crow Agency", "Crow Tribal Police", "https://www.crow-nsn.gov/departments/tribal-police"),
    ("Roosevelt", "Poplar", "Fort Peck Tribal Police", "https://www.fortpecktribes.org/law-enforcement"),
    ("Lake", "Pablo", "Confederated Salish Kootenai Tribal Police", "https://www.cskt.org/departments/police"),
    ("Hill", "Box Elder", "Chippewa Cree Tribal Police", "https://www.chippewacree.org/law-enforcement"),
    # State-level sources
    ("", "", "Montana Highway Patrol Press Releases", "https://dojmt.gov/highway/news-releases/"),
    ("", "", "Montana DOJ Crime Statistics", "https://dojmt.gov/enforcement/crimestats/"),
    ("", "", "Montana Fish Wildlife and Parks Violations", "https://fwp.mt.gov/aboutfwp/enforcement/violations"),
]

# Patterns that indicate a blotter/roster data source
BLOTTER_SIGNALS = [
    re.compile(r'\bblotter\b', re.I),
    re.compile(r'\binmate\s+roster\b', re.I),
    re.compile(r'\bjail\s+roster\b', re.I),
    re.compile(r'\barrest\s+log\b', re.I),
    re.compile(r'\bdaily\s+log\b', re.I),
    re.compile(r'\bpublic\s+report\b', re.I),
    re.compile(r'\bcalls?\s+for\s+service\b', re.I),
    re.compile(r'\bincident\s+report\b', re.I),
    re.compile(r'\bcrime\s+report\b', re.I),
    re.compile(r'\bdetention\s+roster\b', re.I),
    re.compile(r'\bwarrant\s+list\b', re.I),
    re.compile(r'\bzuercher\b', re.I),
]

ZUERCHER_URL_RE = re.compile(r'https?://[^\s"\'<>]+zuercherportal\.com[^\s"\'<>]*', re.I)
PDF_LINK_RE = re.compile(
    r'href=["\']([^"\']*(?:blotter|roster|arrest|log|warrant|inmate)[^"\']*\.pdf)["\']',
    re.I,
)
IFRAME_SRC_RE = re.compile(r'<iframe[^>]+src=["\']([^"\']+)["\']', re.I)
LINK_RE = re.compile(r'href=["\']([^"\'#?]+)["\']', re.I)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _known_urls(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT base_url FROM source_registry WHERE base_url IS NOT NULL").fetchall()
    candidates = conn.execute("SELECT url FROM source_candidates").fetchall()
    known: set[str] = set()
    for r in rows:
        known.add((r["base_url"] or "").rstrip("/"))
    for r in candidates:
        known.add((r["url"] or "").rstrip("/"))
    return known


def _fetch(url: str, timeout: int = 15) -> str:
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": UA}, allow_redirects=True)
        resp.raise_for_status()
        return resp.text or ""
    except Exception as exc:
        logger.debug("fetch error %s: %s", url, exc)
        return ""


def _page_title(html: str) -> str:
    m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
    return m.group(1).strip()[:200] if m else ""


def _has_blotter_signal(text: str) -> str:
    for pat in BLOTTER_SIGNALS:
        m = pat.search(text)
        if m:
            return m.group(0)
    return ""


def _find_deep_candidates(
    base_url: str,
    html: str,
) -> list[dict]:
    found: list[dict] = []
    if not html:
        return found

    for m in ZUERCHER_URL_RE.finditer(html):
        found.append({
            "source_type": "zuercher",
            "url": m.group(0).rstrip("/").rstrip(")").rstrip('"').rstrip("'"),
            "context_snippet": html[max(0, m.start()-80): m.end()+80].strip()[:500],
        })

    for m in PDF_LINK_RE.finditer(html):
        href = m.group(1)
        full_url = urljoin(base_url, href)
        found.append({
            "source_type": "pdf",
            "url": full_url,
            "context_snippet": html[max(0, m.start()-100): m.end()+100].strip()[:500],
        })

    for m in IFRAME_SRC_RE.finditer(html):
        src = m.group(1)
        if any(kw in src.lower() for kw in ("tableau", "arcgis", "socrata", "crimemapping", "nexgen")):
            found.append({
                "source_type": "dashboard",
                "url": src if src.startswith("http") else urljoin(base_url, src),
                "context_snippet": m.group(0)[:500],
            })

    for m in LINK_RE.finditer(html):
        href = m.group(1)
        context = html[max(0, m.start()-150): m.end()+150]
        signal = _has_blotter_signal(context)
        if signal and not href.endswith((".jpg", ".png", ".gif", ".css", ".js")):
            parsed = urlparse(urljoin(base_url, href))
            if parsed.scheme in ("http", "https"):
                found.append({
                    "source_type": "html_roster",
                    "url": parsed.geturl(),
                    "context_snippet": signal,
                })

    seen: set[str] = set()
    deduped: list[dict] = []
    for item in found:
        u = item["url"].rstrip("/")
        if u not in seen:
            seen.add(u)
            deduped.append(item)
    return deduped


def _save_candidate(
    conn: sqlite3.Connection,
    *,
    county: str,
    city: str,
    agency_name: str,
    source_type: str,
    url: str,
    page_title: str,
    context_snippet: str,
    scout_run_at: str,
) -> bool:
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO source_candidates
                (scout_run_at, source_type, county, city, agency_name,
                 url, page_title, context_snippet, review_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (scout_run_at, source_type, county, city, agency_name,
             url.rstrip("/"), page_title, context_snippet, scout_run_at),
        )
        inserted = conn.execute("SELECT changes()").fetchone()[0]
        conn.commit()
        return bool(inserted)
    except Exception as exc:
        logger.warning("save_candidate error for %s: %s", url, exc)
        conn.rollback()
        return False


def run_scout(dry_run: bool = False) -> dict:
    run_at = datetime.now(timezone.utc).isoformat()
    stats = {"scanned": 0, "new_candidates": 0, "errors": 0}
    conn = _connect()
    known = _known_urls(conn)

    for county, city, agency_name, base_url in CANDIDATE_SITES:
        stats["scanned"] += 1
        logger.info("Scanning %s: %s", agency_name, base_url)

        url_norm = base_url.rstrip("/")
        if url_norm in known:
            logger.debug("Already known: %s", base_url)
            continue

        try:
            html = _fetch(base_url)
            title = _page_title(html)
            signal = _has_blotter_signal(html)

            if signal:
                if not dry_run:
                    saved = _save_candidate(
                        conn,
                        county=county, city=city, agency_name=agency_name,
                        source_type="html_roster", url=base_url,
                        page_title=title, context_snippet=signal,
                        scout_run_at=run_at,
                    )
                    if saved:
                        stats["new_candidates"] += 1
                        known.add(url_norm)
                        logger.info("NEW: %s — %s (%s)", agency_name, base_url, signal)
                else:
                    logger.info("[dry-run] Would add: %s — %s", agency_name, base_url)
                    stats["new_candidates"] += 1

            # Deep scan page for embedded Zuercher portals, PDFs, dashboards
            deep = _find_deep_candidates(base_url, html)
            for c in deep:
                cu = c["url"].rstrip("/")
                if cu in known:
                    continue
                if not dry_run:
                    saved = _save_candidate(
                        conn,
                        county=county, city=city, agency_name=agency_name,
                        source_type=c["source_type"], url=cu,
                        page_title=title, context_snippet=c["context_snippet"],
                        scout_run_at=run_at,
                    )
                    if saved:
                        stats["new_candidates"] += 1
                        known.add(cu)
                        logger.info("NEW deep: %s — %s [%s]", agency_name, cu, c["source_type"])
                else:
                    stats["new_candidates"] += 1

        except Exception as exc:
            stats["errors"] += 1
            logger.error("Error scanning %s: %s", agency_name, exc)

    conn.close()

    if stats["new_candidates"] > 0:
        try:
            publish_agent_event({
                "agent": "source_scout",
                "event": "new_candidates_found",
                "count": stats["new_candidates"],
                "run_at": run_at,
                "stats": stats,
            })
        except Exception:
            pass

    logger.info(
        "Scout complete — scanned=%d new=%d errors=%d",
        stats["scanned"], stats["new_candidates"], stats["errors"],
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Scout for new Montana law enforcement blotter sources")
    parser.add_argument("--dry-run", action="store_true", help="Scan but do not write to DB")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    stats = run_scout(dry_run=args.dry_run)
    print(f"Scout: scanned={stats['scanned']} new_candidates={stats['new_candidates']} errors={stats['errors']}")


if __name__ == "__main__":
    main()
