from __future__ import annotations

import re
import sqlite3
import time
import json
import os
from datetime import date, datetime, timedelta, timezone
from typing import Callable

import requests

from services.ingestion.civil_filings import extract_parties_from_caption
from services.ingestion.civil_filings import ingest_civil_filings

DEFAULT_PORTAL_BASE_URLS = [
    "https://dcportal.pubcourts.mt.gov",
]

DEFAULT_SEARCH_PATHS = [
    "/public-portal/search/civil",
]


def _portal_base_urls() -> list[str]:
    raw = (os.getenv("ICOURTCASE_BASE_URLS") or "").strip()
    if not raw:
        return DEFAULT_PORTAL_BASE_URLS
    urls = [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]
    return urls or DEFAULT_PORTAL_BASE_URLS


def _portal_search_paths() -> list[str]:
    raw = (os.getenv("ICOURTCASE_SEARCH_PATHS") or "").strip()
    if not raw:
        return DEFAULT_SEARCH_PATHS
    paths = [item.strip() for item in raw.split(",") if item.strip()]
    return paths or DEFAULT_SEARCH_PATHS


def _candidate_urls(*, county: str, date_from: str, page: int) -> list[str]:
    urls: list[str] = []
    for base in _portal_base_urls():
        for path in _portal_search_paths():
            urls.append(f"{base.rstrip('/')}{path}?county={county}&date_from={date_from}&page={page}")
    return urls


def _build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )
    cookie_header = (os.getenv("ICOURTCASE_COOKIE_HEADER") or "").strip()
    if cookie_header:
        session.headers["Cookie"] = cookie_header
    return session


def parse_icourtcase_search_results(html_text: str, *, county: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    pattern = re.compile(r'<tr\s+([^>]*data-case-number="[^"]+"[^>]*)></tr>', re.IGNORECASE)

    for match in pattern.finditer(html_text):
        attr_text = match.group(1)

        def attr(name: str) -> str:
            attr_match = re.search(rf'{re.escape(name)}="([^"]*)"', attr_text)
            return attr_match.group(1) if attr_match else ''

        row = {
            'county': county,
            'city': attr('data-city'),
            'case_number': attr('data-case-number'),
            'case_type_code': attr('data-case-type'),
            'case_type_label': attr('data-case-type-label'),
            'caption': attr('data-caption'),
            'filing_date': attr('data-filing-date'),
            'case_status': attr('data-status'),
            'address': attr('data-address'),
            'source_url': attr('data-url'),
        }
        plaintiff_name, defendant_name = extract_parties_from_caption(row['caption'])
        row['plaintiff_name'] = plaintiff_name or ''
        row['defendant_name'] = defendant_name or ''
        rows.append(row)

    return rows


def _looks_like_waf_block(html_text: str) -> bool:
    hay = (html_text or "").lower()
    return "request rejected" in hay and "support id" in hay


def fetch_county_search_pages(
    *,
    county: str,
    date_from: str,
    session: requests.Session | None = None,
    max_pages: int = 10,
    delay_seconds: float = 0.5,
    fetcher: Callable[[str], str] | None = None,
) -> list[dict[str, str]]:
    """
    Fetch paginated iCourt civil search pages and parse all rows.
    The default HTTP request can be overridden with `fetcher` for tests or custom auth/session handling.
    """
    active_session = session or _build_session()
    records: list[dict[str, str]] = []
    selected_url_template: str | None = None
    for page in range(1, max_pages + 1):
        html_text = ""
        last_exc: Exception | None = None

        if selected_url_template:
            candidates = [selected_url_template.format(page=page)]
        else:
            candidates = _candidate_urls(county=county, date_from=date_from, page=page)

        for url in candidates:
            try:
                if fetcher:
                    html_text = fetcher(url)
                else:
                    response = active_session.get(url, timeout=30)
                    response.raise_for_status()
                    html_text = response.text
                if _looks_like_waf_block(html_text):
                    raise PermissionError(f"WAF blocked request for URL: {url}")
                if not selected_url_template:
                    selected_url_template = re.sub(r"page=\d+\b", "page={page}", url)
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                html_text = ""
                continue

        if not html_text:
            if last_exc:
                raise last_exc
            break

        page_rows = parse_icourtcase_search_results(html_text, county=county)
        if not page_rows:
            break
        records.extend(page_rows)
        if delay_seconds > 0:
            time.sleep(delay_seconds)
    return records


def default_checkpoint(*, county: str, date_from: str, page: int = 1) -> dict[str, str | int]:
    return {
        'county': county,
        'date_from': date_from,
        'page': page,
    }


def load_source_checkpoint(conn: sqlite3.Connection, *, source_key: str, county: str) -> dict[str, str | int]:
    row = conn.execute(
        'SELECT checkpoint_json FROM civil_filing_sources WHERE source_key = ?',
        (source_key,),
    ).fetchone()
    if not row or not row['checkpoint_json']:
        return default_checkpoint(county=county, date_from=(date.today() - timedelta(days=1)).isoformat(), page=1)
    try:
        payload = json.loads(row['checkpoint_json'])
    except json.JSONDecodeError:
        return default_checkpoint(county=county, date_from=(date.today() - timedelta(days=1)).isoformat(), page=1)
    return {
        'county': str(payload.get('county') or county),
        'date_from': str(payload.get('date_from') or (date.today() - timedelta(days=1)).isoformat()),
        'page': int(payload.get('page') or 1),
    }


def save_source_checkpoint(
    conn: sqlite3.Connection,
    *,
    source_key: str,
    checkpoint: dict[str, str | int],
) -> None:
    conn.execute(
        '''
        UPDATE civil_filing_sources
        SET checkpoint_json = ?,
            updated_at = datetime('now')
        WHERE source_key = ?
        ''',
        (json.dumps(checkpoint), source_key),
    )
    conn.commit()


def run_county_import(
    conn: sqlite3.Connection,
    *,
    county: str,
    date_from: str,
    html_text: str,
    page: int = 1,
) -> dict[str, int]:
    checkpoint = default_checkpoint(county=county, date_from=date_from, page=page)
    records = parse_icourtcase_search_results(html_text, county=county)
    result = ingest_civil_filings(
        conn,
        source_key=f'icourtcase-{county.lower().replace(" ", "-")}',
        display_name=f'iCourtCase {county}',
        adapter_type='icourtcase',
        county=county,
        records=records,
        source_url=_portal_base_urls()[0] + '/',
    )
    result['checkpoint_page'] = int(checkpoint['page'])
    return result


def run_daily_county_ingest(
    conn: sqlite3.Connection,
    *,
    county: str,
    source_key: str | None = None,
    max_pages: int = 10,
    delay_seconds: float = 0.5,
    session: requests.Session | None = None,
    fetcher: Callable[[str], str] | None = None,
) -> dict[str, int | str]:
    """
    Run daily county ingestion with persisted checkpoint on civil_filing_sources.
    """
    normalized_county = county.strip()
    key = source_key or f'icourtcase-{normalized_county.lower().replace(" ", "-")}'
    checkpoint = load_source_checkpoint(conn, source_key=key, county=normalized_county)
    records = fetch_county_search_pages(
        county=normalized_county,
        date_from=str(checkpoint['date_from']),
        session=session,
        max_pages=max_pages,
        delay_seconds=delay_seconds,
        fetcher=fetcher,
    )
    result = ingest_civil_filings(
        conn,
        source_key=key,
        display_name=f'iCourtCase {normalized_county}',
        adapter_type='icourtcase',
        county=normalized_county,
        records=records,
        source_url=_portal_base_urls()[0] + '/',
    )
    next_checkpoint = {
        'county': normalized_county,
        'date_from': date.today().isoformat(),
        'page': 1,
        'last_run_at': datetime.now(timezone.utc).isoformat(),
    }
    save_source_checkpoint(conn, source_key=key, checkpoint=next_checkpoint)
    return {
        **result,
        'source_key': key,
        'county': normalized_county,
        'fetched': len(records),
        'checkpoint_date_from': str(next_checkpoint['date_from']),
    }
