"""
Montana Water Court Scraper

Fetches notices and hearing listings from the Montana Water Court public website
(https://watercourt.mt.gov/) and stores them as court_cases + court_filings in the
tracker schema.

CLI:
    python -m services.court.watercourt_scraper [--dry-run] [--json]
"""
from __future__ import annotations

import argparse
import json as _json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import urlopen, Request
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import config
from init_db import _configure_sqlite
from services.court.tracker import (
    ensure_court_tracker_schema,
    upsert_court_source,
    upsert_court,
    upsert_court_case,
    add_court_filing,
)

WATER_COURT_BASE_URL = 'https://watercourt.mt.gov/'
WATER_COURT_NOTICES_URL = 'https://watercourt.mt.gov/notices.asp'

_DATE_RE = re.compile(r'\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b')
_CASE_NUM_RE = re.compile(r'\b([A-Z]{1,3}-?\d{2,4}[A-Z]?\s*[-/]?\s*\d{1,4})\b')
_TAG_RE = re.compile(r'<[^>]+>')
_WHITESPACE_RE = re.compile(r'\s+')
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
_LI_RE = re.compile(r'<li[^>]*>(.*?)</li>', re.DOTALL | re.IGNORECASE)
_A_RE = re.compile(r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE)
_P_RE = re.compile(r'<p[^>]*>(.*?)</p>', re.DOTALL | re.IGNORECASE)
_TR_RE = re.compile(r'<tr[^>]*>(.*?)</tr>', re.DOTALL | re.IGNORECASE)
_TD_RE = re.compile(r'<t[hd][^>]*>(.*?)</t[hd]>', re.DOTALL | re.IGNORECASE)


def _strip_tags(html: str) -> str:
    return _WHITESPACE_RE.sub(' ', _TAG_RE.sub(' ', html)).strip()


def _clean(text: str) -> str:
    return _WHITESPACE_RE.sub(' ', text).strip()


def _parse_date(text: str) -> str:
    """Return ISO date string YYYY-MM-DD from common date formats, or ''."""
    m = _DATE_RE.search(text)
    if not m:
        return ''
    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year < 100:
        year += 2000
    try:
        return datetime(year, month, day).strftime('%Y-%m-%d')
    except ValueError:
        return ''


def _absolute_url(href: str) -> str:
    href = href.strip()
    if href.startswith('http'):
        return href
    if href.startswith('/'):
        return WATER_COURT_BASE_URL.rstrip('/') + href
    return WATER_COURT_BASE_URL + href


def _fetch_html(url: str, timeout: int = 30) -> str:
    req = Request(url, headers={'User-Agent': 'MontanaBlotter/1.0 (+https://montanablotter.com)'})
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    for enc in ('utf-8', 'latin-1'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')


def _fingerprint(text: str) -> str:
    import hashlib
    return hashlib.sha1(text.encode('utf-8')).hexdigest()[:16]


def _parse_notices(html: str) -> list[dict[str, str]]:
    """
    Parse notices/items from the Water Court page.
    Returns list of {title, date, source_url, case_number, description}.
    """
    notices: list[dict[str, str]] = []

    # Try table rows first (structured hearing schedule)
    for tr_m in _TR_RE.finditer(html):
        row_html = tr_m.group(1)
        cells = [_strip_tags(c.group(1)) for c in _TD_RE.finditer(row_html)]
        if len(cells) < 2:
            continue
        row_text = ' '.join(cells)
        if not row_text.strip() or row_text.lower().startswith('case') or row_text.lower().startswith('date'):
            continue
        case_num_m = _CASE_NUM_RE.search(row_text)
        if not case_num_m:
            continue
        href = ''
        a_m = _A_RE.search(row_html)
        if a_m:
            href = _absolute_url(a_m.group(1))
        notices.append({
            'title': _clean(cells[0] if cells else row_text)[:300],
            'date': _parse_date(row_text),
            'source_url': href,
            'case_number': case_num_m.group(1).strip(),
            'description': _clean(row_text)[:500],
        })

    if notices:
        return notices

    # Fall back to <li> items with links
    for li_m in _LI_RE.finditer(html):
        li_html = li_m.group(1)
        li_text = _strip_tags(li_html)
        if len(li_text) < 10:
            continue
        a_m = _A_RE.search(li_html)
        href = _absolute_url(a_m.group(1)) if a_m else ''
        link_text = _strip_tags(a_m.group(2)) if a_m else ''
        title = link_text or li_text[:200]
        case_m = _CASE_NUM_RE.search(li_text)
        notices.append({
            'title': _clean(title)[:300],
            'date': _parse_date(li_text),
            'source_url': href,
            'case_number': case_m.group(1).strip() if case_m else _fingerprint(li_text),
            'description': _clean(li_text)[:500],
        })

    return notices


def sync_montana_water_court(
    conn: sqlite3.Connection,
    *,
    html: str | None = None,
) -> dict[str, Any]:
    ensure_court_tracker_schema(conn)
    source_id = upsert_court_source(
        conn,
        slug='montana-water-court',
        name='Montana Water Court Notices',
        source_url=WATER_COURT_BASE_URL,
        provider_type='document_feed',
        status='active',
    )
    conn.execute(
        "UPDATE court_sources SET last_scraped_at=datetime('now'), updated_at=datetime('now') WHERE id=?",
        (source_id,),
    )

    court_id = upsert_court(
        conn,
        source_id=source_id,
        slug='montana-water-court',
        name='Montana Water Court',
        court_type='Water Court',
        county='Lewis and Clark',
        city='Helena',
        portal_url=WATER_COURT_BASE_URL,
    )

    errors = 0
    if html is None:
        try:
            html = _fetch_html(WATER_COURT_NOTICES_URL)
        except URLError as exc:
            conn.execute(
                "UPDATE court_sources SET last_error=?, updated_at=datetime('now') WHERE id=?",
                (str(exc)[:500], source_id),
            )
            return {
                'source_slug': 'montana_water_court',
                'source_id': source_id,
                'case_count': 0,
                'filing_count': 0,
                'errors': 1,
                'error': str(exc),
                'synced_at': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
            }

    notices = _parse_notices(html)
    case_count = 0
    filing_count = 0

    for notice in notices:
        try:
            case_id = upsert_court_case(
                conn,
                court_id=court_id,
                case_number=notice['case_number'],
                caption=notice['title'] or notice['case_number'],
                status='open',
                filed_date=notice['date'],
                case_type='Water Right',
                source_url=notice['source_url'],
            )
            case_count += 1
            if notice['title']:
                add_court_filing(
                    conn,
                    case_id=case_id,
                    filing_date=notice['date'] or datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                    filing_title=notice['title'],
                    source_url=notice['source_url'],
                )
                filing_count += 1
        except Exception as exc:
            print(f'  ⚠️ water court notice failed: {exc}')
            errors += 1

    conn.execute(
        "UPDATE court_sources SET last_success_at=datetime('now'), last_error=NULL, updated_at=datetime('now') WHERE id=?",
        (source_id,),
    )

    return {
        'source_slug': 'montana_water_court',
        'source_id': source_id,
        'case_count': case_count,
        'filing_count': filing_count,
        'event_count': 0,
        'errors': errors,
        'synced_at': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Scrape Montana Water Court notices')
    parser.add_argument('--dry-run', action='store_true', help='Parse but do not write to DB')
    parser.add_argument('--json', action='store_true', dest='json_out', help='Print JSON summary')
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    conn = sqlite3.connect(config.DB_PATH, timeout=float(getattr(config, 'DB_TIMEOUT_SECONDS', 30)))
    conn.row_factory = sqlite3.Row
    _configure_sqlite(conn)
    try:
        result = sync_montana_water_court(conn)
        if not args.dry_run:
            conn.commit()
    finally:
        conn.close()

    if args.json_out:
        print(_json.dumps(result, indent=2))
    else:
        print(
            f"water_court: cases={result['case_count']} filings={result['filing_count']} "
            f"errors={result['errors']}"
        )
    return 0 if result['errors'] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
