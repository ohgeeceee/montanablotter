"""
Montana Tax Appeal Board Scraper

Fetches decisions from the Montana Tax Appeal Board public website
(https://mtab.mt.gov/decisions/) and stores them as court_cases + court_filings
in the tracker schema.

CLI:
    python -m services.court.taxappeal_scraper [--dry-run] [--json]
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

TAX_APPEAL_BASE_URL = 'https://mtab.mt.gov/'
TAX_APPEAL_DECISIONS_URL = 'https://mtab.mt.gov/decisions/'

_TAG_RE = re.compile(r'<[^>]+>')
_WHITESPACE_RE = re.compile(r'\s+')
_DATE_RE = re.compile(r'\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b')
_YEAR_RE = re.compile(r'\b(20\d{2}|19\d{2})\b')
_CASE_NUM_RE = re.compile(r'\b(TAB[- ]?\d{2,4}[- ]?\d{0,4}|[A-Z]{1,3}-?\d{2,4}[A-Z]?[-/]\d{1,4})\b', re.IGNORECASE)
_A_RE = re.compile(r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE)
_LI_RE = re.compile(r'<li[^>]*>(.*?)</li>', re.DOTALL | re.IGNORECASE)
_TR_RE = re.compile(r'<tr[^>]*>(.*?)</tr>', re.DOTALL | re.IGNORECASE)
_TD_RE = re.compile(r'<t[hd][^>]*>(.*?)</t[hd]>', re.DOTALL | re.IGNORECASE)
_H_RE = re.compile(r'<h[2-4][^>]*>(.*?)</h[2-4]>', re.DOTALL | re.IGNORECASE)


def _strip_tags(html: str) -> str:
    return _WHITESPACE_RE.sub(' ', _TAG_RE.sub(' ', html)).strip()


def _clean(text: str) -> str:
    return _WHITESPACE_RE.sub(' ', text).strip()


def _parse_date(text: str) -> str:
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


def _fingerprint(text: str) -> str:
    import hashlib
    return hashlib.sha1(text.encode('utf-8')).hexdigest()[:16]


def _absolute_url(href: str) -> str:
    href = href.strip()
    if href.startswith('http'):
        return href
    if href.startswith('/'):
        return TAX_APPEAL_BASE_URL.rstrip('/') + href
    return TAX_APPEAL_DECISIONS_URL + href


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


def _parse_decisions(html: str) -> list[dict[str, str]]:
    """
    Parse TAB decisions from page HTML.
    Returns list of {title, date, source_url, case_number, description}.
    """
    decisions: list[dict[str, str]] = []
    seen: set[str] = set()

    # Try table rows (structured decision list)
    for tr_m in _TR_RE.finditer(html):
        row_html = tr_m.group(1)
        cells = [_strip_tags(c.group(1)) for c in _TD_RE.finditer(row_html)]
        if len(cells) < 2:
            continue
        row_text = ' '.join(cells)
        if not row_text.strip():
            continue
        lower = row_text.lower()
        if lower.startswith('case') or lower.startswith('date') or lower.startswith('year'):
            continue
        a_m = _A_RE.search(row_html)
        href = _absolute_url(a_m.group(1)) if a_m else ''
        case_m = _CASE_NUM_RE.search(row_text)
        case_num = case_m.group(1).strip() if case_m else _fingerprint(row_text)
        if case_num in seen:
            continue
        seen.add(case_num)
        decisions.append({
            'title': _clean(cells[0])[:300],
            'date': _parse_date(row_text),
            'source_url': href,
            'case_number': case_num,
            'description': _clean(row_text)[:500],
        })

    if decisions:
        return decisions

    # Fall back to <li> items containing links to PDFs or decision pages
    for li_m in _LI_RE.finditer(html):
        li_html = li_m.group(1)
        li_text = _strip_tags(li_html)
        if len(li_text) < 8:
            continue
        a_m = _A_RE.search(li_html)
        href = _absolute_url(a_m.group(1)) if a_m else ''
        link_text = _strip_tags(a_m.group(2)) if a_m else ''
        title = link_text or li_text[:200]
        case_m = _CASE_NUM_RE.search(li_text)
        case_num = case_m.group(1).strip() if case_m else _fingerprint(li_text)
        if case_num in seen:
            continue
        seen.add(case_num)
        decisions.append({
            'title': _clean(title)[:300],
            'date': _parse_date(li_text),
            'source_url': href,
            'case_number': case_num,
            'description': _clean(li_text)[:500],
        })

    # Last resort: all anchor links pointing at PDFs or decision paths
    if not decisions:
        for a_m in _A_RE.finditer(html):
            href_raw = a_m.group(1)
            link_text = _strip_tags(a_m.group(2))
            if not href_raw or not link_text:
                continue
            lower_href = href_raw.lower()
            if not (lower_href.endswith('.pdf') or 'decision' in lower_href or 'order' in lower_href):
                continue
            href = _absolute_url(href_raw)
            case_m = _CASE_NUM_RE.search(link_text)
            case_num = case_m.group(1).strip() if case_m else _fingerprint(link_text)
            if case_num in seen:
                continue
            seen.add(case_num)
            decisions.append({
                'title': _clean(link_text)[:300],
                'date': _parse_date(link_text),
                'source_url': href,
                'case_number': case_num,
                'description': _clean(link_text)[:500],
            })

    return decisions


def sync_montana_tax_appeal_board(
    conn: sqlite3.Connection,
    *,
    html: str | None = None,
) -> dict[str, Any]:
    ensure_court_tracker_schema(conn)
    source_id = upsert_court_source(
        conn,
        slug='montana-tax-appeal-board',
        name='Montana Tax Appeal Board Decisions',
        source_url=TAX_APPEAL_BASE_URL,
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
        slug='montana-tax-appeal-board',
        name='Montana Tax Appeal Board',
        court_type='Tax Appeal Board',
        county='Lewis and Clark',
        city='Helena',
        portal_url=TAX_APPEAL_BASE_URL,
    )

    errors = 0
    if html is None:
        try:
            html = _fetch_html(TAX_APPEAL_DECISIONS_URL)
        except URLError as exc:
            conn.execute(
                "UPDATE court_sources SET last_error=?, updated_at=datetime('now') WHERE id=?",
                (str(exc)[:500], source_id),
            )
            return {
                'source_slug': 'montana_tax_appeal_board',
                'source_id': source_id,
                'case_count': 0,
                'filing_count': 0,
                'errors': 1,
                'error': str(exc),
                'synced_at': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
            }

    decisions = _parse_decisions(html)
    case_count = 0
    filing_count = 0

    for decision in decisions:
        try:
            case_id = upsert_court_case(
                conn,
                court_id=court_id,
                case_number=decision['case_number'],
                caption=decision['title'] or decision['case_number'],
                status='closed',
                filed_date=decision['date'],
                case_type='Tax Appeal',
                source_url=decision['source_url'],
            )
            case_count += 1
            if decision['title']:
                add_court_filing(
                    conn,
                    case_id=case_id,
                    filing_date=decision['date'] or datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                    filing_title=decision['title'],
                    source_url=decision['source_url'],
                )
                filing_count += 1
        except Exception as exc:
            print(f'  ⚠️ TAB decision failed: {exc}')
            errors += 1

    conn.execute(
        "UPDATE court_sources SET last_success_at=datetime('now'), last_error=NULL, updated_at=datetime('now') WHERE id=?",
        (source_id,),
    )

    return {
        'source_slug': 'montana_tax_appeal_board',
        'source_id': source_id,
        'case_count': case_count,
        'filing_count': filing_count,
        'event_count': 0,
        'errors': errors,
        'synced_at': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Scrape Montana Tax Appeal Board decisions')
    parser.add_argument('--dry-run', action='store_true', help='Parse but do not write to DB')
    parser.add_argument('--json', action='store_true', dest='json_out', help='Print JSON summary')
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    conn = sqlite3.connect(config.DB_PATH, timeout=float(getattr(config, 'DB_TIMEOUT_SECONDS', 30)))
    conn.row_factory = sqlite3.Row
    _configure_sqlite(conn)
    try:
        result = sync_montana_tax_appeal_board(conn)
        if not args.dry_run:
            conn.commit()
    finally:
        conn.close()

    if args.json_out:
        print(_json.dumps(result, indent=2))
    else:
        print(
            f"tax_appeal: cases={result['case_count']} filings={result['filing_count']} "
            f"errors={result['errors']}"
        )
    return 0 if result['errors'] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
