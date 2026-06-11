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

# The Water Court was historically at watercourt.mt.gov but migrated into the
# unified Montana Courts site. We try a few candidates in order and keep the
# first one that responds, so a future rename doesn't put us back into the
# `last_success_at = never` failure mode.
WATER_COURT_BASE_URL = 'https://courts.mt.gov/courts/water/'
WATER_COURT_NOTICES_URL = 'https://courts.mt.gov/courts/water/Notices-Info/PublicNotices'
WATER_COURT_NOTICES_FALLBACKS = (
    'https://courts.mt.gov/courts/water/Notices-Info/PublicNotices',
    'https://courts.mt.gov/courts/water/Notices-Info/',
    'https://courts.mt.gov/Courts/Water/',
)

_DATE_RE = re.compile(r'\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b')
_DEADLINE_RE = re.compile(r'deadline\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})', re.IGNORECASE)
_BASIN_RE = re.compile(r'Basin\s+([0-9]{2,3}[A-Z]{1,3})\s*(.*)', re.IGNORECASE)
_TAG_RE = re.compile(r'<[^>]+>')
_WHITESPACE_RE = re.compile(r'\s+')
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
_LI_RE = re.compile(r'<li[^>]*>(.*?)</li>', re.DOTALL | re.IGNORECASE)
_A_RE = re.compile(r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE)
_P_RE = re.compile(r'<p[^>]*>(.*?)</p>', re.DOTALL | re.IGNORECASE)
_TR_RE = re.compile(r'<tr[^>]*>(.*?)</tr>', re.DOTALL | re.IGNORECASE)
_TD_RE = re.compile(r'<t[hd][^>]*>(.*?)</t[hd]>', re.DOTALL | re.IGNORECASE)
_H_RE = re.compile(r'<h[1-6][^>]*>(.*?)</h[1-6]>', re.DOTALL | re.IGNORECASE)


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


def _absolute_url(href: str, base: str | None = None) -> str:
    # The Water Court site uses backslashes in its href values (e.g.
    # "\external\Water\A-Notices and Information\76G NOIA.pdf"). Normalize
    # to forward slashes and strip the leading slash so we get a clean path
    # under the site root.
    href = href.strip().replace('\\', '/')
    while '//' in href:
        href = href.replace('//', '/')
    if href.startswith('http'):
        return href
    if not base:
        base = WATER_COURT_BASE_URL
    origin = base.split('/courts/', 1)[0] if '/courts/' in base else base
    origin = origin.rstrip('/')
    if href.startswith('/'):
        return origin + href
    return origin + '/' + href.lstrip('/')


def _fetch_html(url: str, timeout: int = 30) -> str:
    req = Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://montanablotter.com)',
        'Accept': 'text/html,application/xhtml+xml',
    })
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    for enc in ('utf-8', 'latin-1'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')


def _fetch_with_fallback(urls: tuple[str, ...], timeout: int = 30) -> tuple[str | None, str | None, str | None]:
    """Try each URL in order. Returns (html, used_url, error). On total failure
    html and used_url are None and error holds the last exception string."""
    last_error: str | None = None
    for url in urls:
        try:
            return _fetch_html(url, timeout=timeout), url, None
        except (URLError, Exception) as exc:  # noqa: BLE001 - any network error is a candidate
            last_error = f'{type(exc).__name__}: {exc}'
            continue
    return None, None, last_error


def _fingerprint(text: str) -> str:
    import hashlib
    return hashlib.sha1(text.encode('utf-8')).hexdigest()[:16]


def _parse_deadline(context: str) -> str:
    """Return ISO date from a 'deadline Month DD, YYYY' phrase, or ''."""
    m = _DEADLINE_RE.search(context)
    if not m:
        return ''
    try:
        return datetime.strptime(m.group(1), '%B %d, %Y').strftime('%Y-%m-%d')
    except ValueError:
        return ''


def _filename_from_href(href: str) -> str:
    """Best-effort document identifier from a PDF href (e.g. '76GNOA.pdf')."""
    href = href.strip().split('?', 1)[0].split('#', 1)[0]
    if not href:
        return ''
    return href.rsplit('/', 1)[-1]


def _parse_notices(html: str) -> list[dict[str, str]]:
    """
    Parse notices/items from the Water Court page.

    The current site organizes content as a sequence of <h2> basin sections
    (e.g. "Basin 76G Clark Fork Above Blackfoot River"), each followed by <p>
    blocks containing links to PDF documents. We treat each basin as a case
    and each PDF link as a filing within that case.

    Returns list of {title, date, source_url, case_number, description, basin_name,
    document_number}.
    """
    notices: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    # Pass 1: walk every <a> tag in document order, tracking the most recent
    # basin header that preceded it. Each PDF link becomes a filing under
    # that basin.
    cursor = 0
    last_basin_code: str | None = None
    last_basin_name: str = ''
    for a_m in _A_RE.finditer(html):
        href = a_m.group(1)
        if not href:
            continue
        href_lower = href.lower()
        if not (href_lower.endswith('.pdf') or 'water' in href_lower):
            continue
        # Look back to the nearest <h2> Basin header
        back = html[cursor:a_m.start()]
        cursor = a_m.end()
        h_iter = list(_H_RE.finditer(back))
        for h_m in h_iter:
            header_text = _clean(_strip_tags(h_m.group(1)))
            basin_m = _BASIN_RE.search(header_text)
            if basin_m:
                last_basin_code = basin_m.group(1).strip()
                last_basin_name = _clean(basin_m.group(2)).lstrip('—-: ').strip()

        link_text = _clean(_strip_tags(a_m.group(2)))
        if not link_text or len(link_text) < 4:
            continue

        # Sibling paragraph context (for deadline phrases)
        p_search_start = max(0, a_m.start() - 250)
        p_search_end = min(len(html), a_m.end() + 50)
        surrounding = html[p_search_start:p_search_end]
        deadline = _parse_deadline(surrounding) or _parse_deadline(link_text)

        basin_code = last_basin_code or 'unfiled'
        doc_num = _filename_from_href(href)
        key = (basin_code, doc_num or link_text)
        if key in seen:
            continue
        seen.add(key)

        if last_basin_code:
            caption = f'Basin {last_basin_code}'
            if last_basin_name:
                caption = f'{caption} — {last_basin_name}'
        else:
            caption = f'Water Court Notice — {link_text[:120]}'

        notices.append({
            'title': _clean(link_text)[:300],
            'date': deadline,
            'source_url': _absolute_url(href),
            'case_number': basin_code,
            'caption': caption,
            'basin_name': last_basin_name,
            'document_number': doc_num,
            'description': _clean(link_text)[:500],
        })

    if notices:
        return notices

    # Pass 3 fallback (legacy shape): rows in a table.
    for tr_m in _TR_RE.finditer(html):
        row_html = tr_m.group(1)
        cells = [_strip_tags(c.group(1)) for c in _TD_RE.finditer(row_html)]
        if len(cells) < 2:
            continue
        row_text = ' '.join(cells)
        if not row_text.strip() or row_text.lower().startswith('case') or row_text.lower().startswith('date'):
            continue
        a_m = _A_RE.search(row_html)
        href = _absolute_url(a_m.group(1)) if a_m else ''
        notices.append({
            'title': _clean(cells[0] if cells else row_text)[:300],
            'date': _parse_date(row_text),
            'source_url': href,
            'case_number': _fingerprint(row_text),
            'caption': _clean(cells[0] if cells else row_text)[:300],
            'basin_name': '',
            'document_number': _filename_from_href(href),
            'description': _clean(row_text)[:500],
        })
    if notices:
        return notices

    # Pass 4 fallback: <li> items with links.
    for li_m in _LI_RE.finditer(html):
        li_html = li_m.group(1)
        li_text = _strip_tags(li_html)
        if len(li_text) < 10:
            continue
        a_m = _A_RE.search(li_html)
        href = _absolute_url(a_m.group(1)) if a_m else ''
        link_text = _strip_tags(a_m.group(2)) if a_m else ''
        title = link_text or li_text[:200]
        notices.append({
            'title': _clean(title)[:300],
            'date': _parse_date(li_text),
            'source_url': href,
            'case_number': _fingerprint(li_text),
            'caption': _clean(title)[:300],
            'basin_name': '',
            'document_number': _filename_from_href(href),
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
        html, used_url, fetch_error = _fetch_with_fallback(WATER_COURT_NOTICES_FALLBACKS)
        if html is None:
            conn.execute(
                "UPDATE court_sources SET last_error=?, updated_at=datetime('now') WHERE id=?",
                ((fetch_error or 'unknown fetch error')[:1000], source_id),
            )
            return {
                'source_slug': 'montana_water_court',
                'source_id': source_id,
                'case_count': 0,
                'filing_count': 0,
                'errors': 1,
                'error': fetch_error or 'unknown fetch error',
                'synced_at': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
            }
        if used_url and used_url != WATER_COURT_NOTICES_URL:
            print(f'  ℹ️  water court notices URL rotated: {used_url}')

    notices = _parse_notices(html)
    case_count = 0
    filing_count = 0

    for notice in notices:
        try:
            case_id = upsert_court_case(
                conn,
                court_id=court_id,
                case_number=notice['case_number'],
                caption=notice.get('caption') or notice.get('title') or notice['case_number'],
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
                    document_number=notice.get('document_number', ''),
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
