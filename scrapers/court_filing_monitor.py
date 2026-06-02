"""
Court Filing Monitor
Polls three sources for new public court filings:
  1. CourtListener REST API  — federal District of Montana (mtd)
  2. MT Supreme Court docket — supremecourtdocket.mt.gov
  3. MT District Court portal — dcportal.pubcourts.mt.gov (FullCourt Enterprise)

Usage:
  python scrapers/court_filing_monitor.py                  # run all sources
  python scrapers/court_filing_monitor.py --source cl      # CourtListener only
  python scrapers/court_filing_monitor.py --since 2026-05-01
  python scrapers/court_filing_monitor.py --dry-run
  python scrapers/court_filing_monitor.py --search-name "Benavides,Lincoln"

Requires:
  COURTLISTENER_TOKEN in config.py or environment (free at courtlistener.com/sign-in/)
  MT portal credentials (optional): MT_COURT_USER / MT_COURT_PASS in env or config.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# ── project imports ──────────────────────────────────────────────────────────
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db as blotter_db

try:
    import config as cfg
    _CL_TOKEN = getattr(cfg, "COURTLISTENER_TOKEN", None) or os.environ.get("COURTLISTENER_TOKEN")
    _MT_USER  = getattr(cfg, "MT_COURT_USER",       None) or os.environ.get("MT_COURT_USER")
    _MT_PASS  = getattr(cfg, "MT_COURT_PASS",       None) or os.environ.get("MT_COURT_PASS")
except ImportError:
    _CL_TOKEN = os.environ.get("COURTLISTENER_TOKEN")
    _MT_USER  = os.environ.get("MT_COURT_USER")
    _MT_PASS  = os.environ.get("MT_COURT_PASS")

# ── logging ───────────────────────────────────────────────────────────────────
log = logging.getLogger("court_filing_monitor")
log.setLevel(logging.INFO)
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    log.addHandler(_h)

# ── constants ─────────────────────────────────────────────────────────────────
CL_BASE        = "https://www.courtlistener.com/api/rest/v4"
CL_RATE_DELAY  = 13   # seconds between CL requests (5/min safe margin)
MT_SUPREME_URL = "https://supremecourtdocket.mt.gov"
MT_DC_URL      = "https://dcportal.pubcourts.mt.gov"

HEADERS = {
    "User-Agent": "MontanaBlotterBot/1.0 (public records research; ohjoncurrie@gmail.com)",
    "Accept":     "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


# ── data model ────────────────────────────────────────────────────────────────
@dataclass
class CourtFiling:
    source:      str          # courtlistener | mt_supreme | mt_district
    court:       str          # e.g. "D. Mont." or "Missoula County District"
    case_number: str
    case_title:  str
    filing_type: str          # new_docket | docket_entry | new_case | opinion
    filing_date: str          # YYYY-MM-DD
    description: str
    url:         str
    raw:         dict = field(default_factory=dict, repr=False)

    def fingerprint(self) -> str:
        key = f"{self.source}|{self.case_number}|{self.filing_date}|{self.description[:80]}"
        return hashlib.sha256(key.encode()).hexdigest()[:32]


# ── schema ────────────────────────────────────────────────────────────────────
_SCHEMA_STMTS = [
    """
    CREATE TABLE IF NOT EXISTS court_monitor_filings (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        source       TEXT NOT NULL,
        court        TEXT,
        case_number  TEXT,
        case_title   TEXT,
        filing_type  TEXT,
        filing_date  TEXT,
        description  TEXT,
        url          TEXT,
        fingerprint  TEXT NOT NULL UNIQUE,
        raw_json     TEXT,
        seen         INTEGER DEFAULT 0,
        created_at   TEXT DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cmf_source ON court_monitor_filings(source)",
    "CREATE INDEX IF NOT EXISTS idx_cmf_date   ON court_monitor_filings(filing_date)",
    "CREATE INDEX IF NOT EXISTS idx_cmf_case   ON court_monitor_filings(case_number)",
    "CREATE INDEX IF NOT EXISTS idx_cmf_seen   ON court_monitor_filings(seen)",
    """
    CREATE TABLE IF NOT EXISTS court_monitor_checkpoints (
        source      TEXT PRIMARY KEY,
        last_run_at TEXT NOT NULL,
        last_result TEXT,
        updated_at  TEXT DEFAULT (datetime('now'))
    )
    """,
]


def ensure_schema(conn: sqlite3.Connection) -> None:
    for stmt in _SCHEMA_STMTS:
        conn.execute(stmt)
    conn.commit()


# ── checkpoint helpers ────────────────────────────────────────────────────────
def get_checkpoint(conn: sqlite3.Connection, source: str) -> datetime | None:
    row = conn.execute(
        "SELECT last_run_at FROM court_monitor_checkpoints WHERE source = ?", (source,)
    ).fetchone()
    if row:
        try:
            return datetime.fromisoformat(row[0]).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def set_checkpoint(conn: sqlite3.Connection, source: str, ts: datetime, result: dict) -> None:
    conn.execute(
        """
        INSERT INTO court_monitor_checkpoints(source, last_run_at, last_result, updated_at)
        VALUES(?, ?, ?, datetime('now'))
        ON CONFLICT(source) DO UPDATE SET
            last_run_at = excluded.last_run_at,
            last_result = excluded.last_result,
            updated_at  = excluded.updated_at
        """,
        (source, ts.isoformat(), json.dumps(result)),
    )
    conn.commit()


# ── insert helper ─────────────────────────────────────────────────────────────
def insert_filings(conn: sqlite3.Connection, filings: list[CourtFiling]) -> dict[str, int]:
    new_count = dup_count = 0
    for f in filings:
        try:
            conn.execute(
                """
                INSERT INTO court_monitor_filings
                    (source, court, case_number, case_title, filing_type,
                     filing_date, description, url, fingerprint, raw_json)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (f.source, f.court, f.case_number, f.case_title, f.filing_type,
                 f.filing_date, f.description, f.url, f.fingerprint(), json.dumps(f.raw)),
            )
            new_count += 1
        except sqlite3.IntegrityError:
            dup_count += 1
    conn.commit()
    return {"new": new_count, "duplicates": dup_count}


# ── HTTP helpers ──────────────────────────────────────────────────────────────
def _get(url: str, *, headers: dict | None = None, params: dict | None = None,
         timeout: int = 30, retries: int = 3) -> requests.Response | None:
    h = {**HEADERS, **(headers or {})}
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=h, params=params, timeout=timeout)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 60))
                log.warning("Rate limited — sleeping %ds", wait)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as exc:
            log.warning("GET %s attempt %d failed: %s", url, attempt + 1, exc)
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return None


# ── date normalizer ───────────────────────────────────────────────────────────
_DATE_PATTERNS = [
    (r"(\d{4}-\d{2}-\d{2})",          "%Y-%m-%d"),
    (r"(\d{1,2}/\d{1,2}/\d{4})",      "%m/%d/%Y"),
    (r"(\d{1,2}-\d{1,2}-\d{4})",      "%m-%d-%Y"),
    (r"([A-Z][a-z]+ \d{1,2},? \d{4})", "%B %d, %Y"),
]

def _normalize_date(raw: str) -> str:
    for pat, fmt in _DATE_PATTERNS:
        m = re.search(pat, raw)
        if m:
            try:
                return datetime.strptime(m.group(1), fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
    return raw


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE 1: CourtListener (federal D. Mont.)
# ─────────────────────────────────────────────────────────────────────────────
class CourtListenerMonitor:
    """
    Uses CourtListener REST API v4 to find new dockets and docket entries
    filed in the U.S. District of Montana (court code: mtd).

    Free token: https://www.courtlistener.com/sign-in/
    Rate limit: 5 req/min — we sleep CL_RATE_DELAY seconds between calls.
    """

    def __init__(self, token: str | None = None):
        self.token = token or _CL_TOKEN

    def _cl_get(self, path: str, params: dict | None = None,
                full_url: str | None = None) -> dict | None:
        if not self.token:
            return None
        url = full_url or f"{CL_BASE}/{path}/"
        r = _get(url, headers={"Authorization": f"Token {self.token}"}, params=params)
        time.sleep(CL_RATE_DELAY)
        return r.json() if r else None

    def _paginate(self, path: str, params: dict, max_pages: int = 10) -> list[dict]:
        results: list[dict] = []
        page_url: str | None = f"{CL_BASE}/{path}/"
        page = 0
        while page_url and page < max_pages:
            data = self._cl_get(path, params if page == 0 else None, full_url=page_url if page > 0 else None)
            if not data:
                break
            results.extend(data.get("results", []))
            page_url = data.get("next")
            page += 1
        return results

    def fetch_new_dockets(self, since: datetime) -> list[CourtFiling]:
        rows = self._paginate("dockets", {
            "court":           "mtd",
            "date_filed__gte": since.strftime("%Y-%m-%d"),
            "order_by":       "-date_filed",
            "page_size":       50,
            "format":          "json",
        })
        filings = []
        for d in rows:
            filings.append(CourtFiling(
                source      = "courtlistener",
                court       = "U.S. District Court, D. Mont.",
                case_number = d.get("docket_number") or "",
                case_title  = d.get("case_name") or "",
                filing_type = "new_docket",
                filing_date = (d.get("date_filed") or "")[:10],
                description = f"New case: {d.get('case_name', '')}",
                url         = f"https://www.courtlistener.com{d.get('absolute_url', '')}",
                raw         = d,
            ))
        log.info("CourtListener dockets: %d since %s", len(filings), since.date())
        return filings

    def fetch_new_entries(self, since: datetime) -> list[CourtFiling]:
        """Individual documents filed within existing dockets."""
        rows = self._paginate("docket-entries", {
            "docket__court":   "mtd",
            "date_filed__gte": since.strftime("%Y-%m-%d"),
            "order_by":       "-date_filed",
            "page_size":       50,
            "format":          "json",
        }, max_pages=5)
        filings = []
        for e in rows:
            filings.append(CourtFiling(
                source      = "courtlistener",
                court       = "U.S. District Court, D. Mont.",
                case_number = "",
                case_title  = "",
                filing_type = "docket_entry",
                filing_date = (e.get("date_filed") or "")[:10],
                description = (e.get("description") or "")[:500],
                url         = f"https://www.courtlistener.com{e.get('absolute_url', '')}",
                raw         = e,
            ))
        log.info("CourtListener entries: %d since %s", len(filings), since.date())
        return filings

    def run(self, since: datetime) -> list[CourtFiling]:
        if not self.token:
            log.warning("Skipping CourtListener — set COURTLISTENER_TOKEN in config.py")
            return []
        return self.fetch_new_dockets(since) + self.fetch_new_entries(since)


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE 2: Montana Supreme Court docket
# ─────────────────────────────────────────────────────────────────────────────
class MTSupremeCourtMonitor:
    """
    Uses the supremecourtdocket.mt.gov REST API (Angular SPA backend).

    Endpoint: POST /api/docket/search
    caseStatus: 0=active, 1=closed, 2=closedBefore2006
    Dates must be ISO datetime strings: "YYYY-MM-DDTHH:MM:SS"
    No auth required for public searches.
    """

    API_URL = f"{MT_SUPREME_URL}/api/docket/search"
    CASE_URL = f"{MT_SUPREME_URL}/case-info"

    _API_HEADERS = {
        **HEADERS,
        "Accept":       "application/json",
        "Content-Type": "application/json",
        "Referer":      f"{MT_SUPREME_URL}/",
        "Origin":       MT_SUPREME_URL,
    }

    def _search(self, since: datetime, case_status: int) -> list[dict]:
        since_str = since.strftime("%Y-%m-%dT00:00:00")
        now_str   = datetime.now().strftime("%Y-%m-%dT23:59:59")
        results   = []
        page      = 0

        while True:
            payload = {
                "caseStatus":    case_status,
                "dateFrom":      since_str,
                "dateTo":        now_str,
                "page":          page,
                "pageSize":      100,
                "sortDirection": "asc",
                "sortColumn":    "caseNumber",
            }
            try:
                r = requests.post(self.API_URL, headers=self._API_HEADERS,
                                  json=payload, timeout=20)
                r.raise_for_status()
                data = r.json()
            except Exception as exc:
                log.warning("MT Supreme Court API page %d (status=%d) failed: %s",
                            page, case_status, exc)
                break

            content = data.get("content", [])
            results.extend(content)

            page_info = data.get("page", {})
            total_pages = page_info.get("totalPages", 1)
            page += 1
            if page >= total_pages or not content:
                break

        return results

    def run(self, since: datetime) -> list[CourtFiling]:
        filings: list[CourtFiling] = []

        for status_code, status_label in [(0, "active"), (1, "closed")]:
            rows = self._search(since, status_code)
            for d in rows:
                case_num   = d.get("caseNumber") or ""
                case_id    = d.get("caseId") or ""
                case_title = d.get("caseTitle") or ""
                attorneys  = ", ".join(d.get("attorneys") or [])
                # Case URL: /case-info/{status}/{caseId or caseNumber}
                url = f"{MT_SUPREME_URL}/case-info/{status_label}/{case_id or case_num}"

                filings.append(CourtFiling(
                    source      = "mt_supreme",
                    court       = "Montana Supreme Court",
                    case_number = case_num,
                    case_title  = case_title,
                    filing_type = "new_case",
                    filing_date = since.strftime("%Y-%m-%d"),  # API doesn't return filed date in search
                    description = f"Attorneys: {attorneys}" if attorneys else case_title,
                    url         = url,
                    raw         = d,
                ))

        log.info("MT Supreme Court: %d filings since %s", len(filings), since.date())
        return filings


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE 3: Montana District Court portal (FullCourt Enterprise)
# ─────────────────────────────────────────────────────────────────────────────
class MTDistrictCourtMonitor:
    """
    Scrapes dcportal.pubcourts.mt.gov (FullCourt Enterprise, JusticeSystems).

    Public date-range search works without credentials.
    Set MT_COURT_USER / MT_COURT_PASS in config.py or env for authenticated access.
    Portal data refreshes every 24h — daily polling is sufficient.
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def _login(self) -> bool:
        if not (_MT_USER and _MT_PASS):
            return False
        try:
            home = self.session.get(MT_DC_URL, timeout=30)
            soup = BeautifulSoup(home.text, "html.parser")
            token_tag = soup.find("input", {"name": "__RequestVerificationToken"})
            token = token_tag["value"] if token_tag else ""
            resp = self.session.post(
                f"{MT_DC_URL}/Account/LogOn",
                data={"UserName": _MT_USER, "Password": _MT_PASS,
                      "__RequestVerificationToken": token},
                timeout=30, allow_redirects=True,
            )
            ok = "LogOff" in resp.text or "logout" in resp.text.lower()
            log.info("MT DC portal login: %s", "ok" if ok else "failed")
            return ok
        except Exception as exc:
            log.warning("MT DC login error: %s", exc)
            return False

    def run(self, since: datetime) -> list[CourtFiling]:
        if _MT_USER and _MT_PASS:
            self._login()

        filings: list[CourtFiling] = []
        since_str = since.strftime("%m/%d/%Y")
        today_str = datetime.now().strftime("%m/%d/%Y")

        try:
            resp = self.session.get(
                f"{MT_DC_URL}/Search/SearchResults",
                params={
                    "SearchType":    "DateFiled",
                    "DateFiledFrom": since_str,
                    "DateFiledTo":   today_str,
                    "CourtType":     "District",
                },
                timeout=30,
            )
            resp.raise_for_status()
            filings = self._parse(resp.text)
        except Exception as exc:
            log.warning("MT District Court search failed: %s", exc)

        log.info("MT District Court: %d filings since %s", len(filings), since.date())
        return filings

    def search_by_name(self, last_name: str, first_name: str = "") -> list[CourtFiling]:
        """One-off: find all public cases for a named party."""
        try:
            resp = self.session.get(
                f"{MT_DC_URL}/Search/SearchResults",
                params={"SearchType": "ByParty", "LastName": last_name, "FirstName": first_name},
                timeout=30,
            )
            resp.raise_for_status()
            return self._parse(resp.text)
        except Exception as exc:
            log.warning("MT DC name search failed: %s", exc)
            return []

    def _parse(self, html: str) -> list[CourtFiling]:
        filings: list[CourtFiling] = []
        soup  = BeautifulSoup(html, "html.parser")
        table = (
            soup.find("table", class_=re.compile(r"result|search|case", re.I))
            or soup.find("table")
        )
        if not table:
            log.debug("MT DC: no results table in response")
            return filings

        headers  = [th.get_text(strip=True).lower() for th in table.find_all("th")]

        def _col(row_cells: list[str], name: str, fallback: int) -> str:
            for i, h in enumerate(headers):
                if name in h and i < len(row_cells):
                    return row_cells[i]
            return row_cells[fallback] if fallback < len(row_cells) else ""

        for row in table.find_all("tr")[1:]:
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            if not cells:
                continue
            link = row.find("a", href=True)
            url  = urljoin(MT_DC_URL, link["href"]) if link else MT_DC_URL

            case_num   = _col(cells, "case",  0)
            case_title = _col(cells, "title", 1) or _col(cells, "party", 1)
            filed_date = _normalize_date(_col(cells, "filed", 2) or _col(cells, "date", 2))
            description = _col(cells, "type", 3) or _col(cells, "description", 3)

            if not case_num:
                continue

            filings.append(CourtFiling(
                source      = "mt_district",
                court       = "Montana District Court",
                case_number = case_num,
                case_title  = case_title,
                filing_type = "new_case",
                filing_date = filed_date,
                description = description or case_title,
                url         = url,
                raw         = {"cells": cells, "headers": headers},
            ))
        return filings


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────────────
_SOURCE_MAP: dict[str, tuple[str, Any]] = {
    "cl":          ("courtlistener", CourtListenerMonitor),
    "mt_supreme":  ("mt_supreme",    MTSupremeCourtMonitor),
    "mt_district": ("mt_district",   MTDistrictCourtMonitor),
}


def run_monitor(
    sources:  list[str] | None        = None,
    since:    datetime | None         = None,
    dry_run:  bool                    = False,
    conn:     sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """
    Run all requested sources, persist new filings, update checkpoints.
    Falls back to 24h lookback when no checkpoint exists for a source.
    """
    sources = sources or list(_SOURCE_MAP.keys())
    close_conn = conn is None
    if conn is None:
        conn = blotter_db.connect_db()
    ensure_schema(conn)

    run_time = datetime.now(timezone.utc)
    summary:  dict[str, Any] = {}

    for key in sources:
        if key not in _SOURCE_MAP:
            log.warning("Unknown source '%s' — skipping", key)
            continue

        src_id, MonitorClass = _SOURCE_MAP[key]
        monitor = MonitorClass()

        effective_since = since or get_checkpoint(conn, src_id) or (run_time - timedelta(hours=24))
        log.info("source=%s  since=%s", key, effective_since.date())

        try:
            filings = monitor.run(effective_since)
        except Exception as exc:
            log.error("Source %s crashed: %s", key, exc, exc_info=True)
            summary[key] = {"error": str(exc)}
            continue

        if dry_run:
            log.info("[dry-run] %s: %d filings found", key, len(filings))
            for f in filings[:5]:
                log.info("  %s | %s | %s", f.filing_date, f.case_number, f.case_title[:60])
            summary[key] = {"dry_run": True, "found": len(filings)}
        else:
            stats = insert_filings(conn, filings)
            set_checkpoint(conn, src_id, run_time, stats)
            summary[key] = stats
            log.info("source=%s  %s", key, stats)

    if close_conn:
        conn.close()

    summary["total_new"] = sum(
        s.get("new", 0) for s in summary.values() if isinstance(s, dict)
    )
    return summary


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Monitor public MT court filings")
    p.add_argument("--source", "-s",
                   choices=["cl", "mt_supreme", "mt_district", "all"], default="all")
    p.add_argument("--since", metavar="YYYY-MM-DD",
                   help="Override checkpoint start date")
    p.add_argument("--dry-run", action="store_true",
                   help="Fetch but do not write to DB")
    p.add_argument("--search-name", metavar="LASTNAME[,FIRSTNAME]",
                   help="One-off MT District Court party name lookup")
    p.add_argument("--db", default=None,
                   help="Path to SQLite DB (default: blotter.db via config)")
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    conn: sqlite3.Connection | None = None
    if args.db:
        conn = sqlite3.connect(args.db)

    # One-off name search
    if args.search_name:
        parts = args.search_name.split(",", 1)
        last  = parts[0].strip()
        first = parts[1].strip() if len(parts) > 1 else ""
        results = MTDistrictCourtMonitor().search_by_name(last, first)
        print(f"\n{len(results)} case(s) for '{first} {last}'.strip():\n")
        for r in results:
            print(f"  {r.filing_date}  {r.case_number:<22}  {r.case_title}")
        return

    sources = list(_SOURCE_MAP.keys()) if args.source == "all" else [args.source]

    since: datetime | None = None
    if args.since:
        try:
            since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            log.error("Invalid --since: %s", args.since)
            raise SystemExit(1)

    result = run_monitor(sources=sources, since=since, dry_run=args.dry_run, conn=conn)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
