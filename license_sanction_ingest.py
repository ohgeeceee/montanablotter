"""
license_sanction_ingest.py

Weekly cron job: fetch each MT licensing board discipline page,
extract structured records via Kimi API, and write to license_sanctions.
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from scrapers.professional_boards import parse_generic_table, parse_list_items

from db import connect_db
from license_sanction_sources import BOARD_SOURCES

DB_PATH = os.getenv('MB_DB_PATH', 'blotter.db')
KIMI_API_KEY = os.getenv('KIMI_API_KEY', '')
KIMI_API_BASE = os.getenv('KIMI_API_BASE', 'https://api.kimi.com/coding')
KIMI_MODEL = os.getenv('KIMI_MODEL', 'kimi-k2.6')

_EXTRACTION_PROMPT = """
You are a data extraction assistant. The following HTML or text comes from a Montana professional licensing board disciplinary actions page.

Extract every disciplinary action as a JSON array of objects with these fields:
- name: full name of the sanctioned person (string, required)
- license_number: license or permit number if shown (string, optional)
- board: name of the licensing board (string, required)
- violation_type: type of violation or misconduct (string, optional)
- action_taken: disciplinary action taken (e.g., suspension, revocation, fine, probation) (string, required)
- effective_date: date the action took effect in ISO 8601 format YYYY-MM-DD if shown, else null (string, optional)
- county_if_known: Montana county if mentioned, else null (string, optional)
- description: brief summary of the action (string, optional)
- source_url: the URL of the specific document or page section if available (string, optional)

Return ONLY a valid JSON array. No markdown, no explanation, no preamble.
If no actions are found, return an empty array [].
"""

DISCOVERY_KEYWORDS = (
    'disciplin',
    'complaint',
    'order',
    'suspend',
    'revok',
    'reprimand',
    'board-action',
    'notice',
    'news',
    'faq',
    'regulation',
)

HTML_DISCOVERY_LIMIT = 5

ACTION_KEYWORDS = (
    'disciplin',
    'suspend',
    'revok',
    'reprimand',
    'probation',
    'fine',
    'penalt',
    'order',
    'consent',
    'settlement',
    'stipulation',
)

BLOCKED_NAME_PREFIXES = (
    'montana board',
    'board of',
    'department of',
    'administrative',
    'board regulations',
    'license information',
    'faq',
    'forms',
    'contact information',
    'service of process',
    'upcoming events',
    'careers at dli',
)


def _slugify_name(name: str) -> str:
    slug = (name or 'unknown').lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_-]+", "-", slug)
    return slug.strip("-") or 'unknown'


def _fingerprint(
    name: str,
    license_number: str | None,
    board: str,
    effective_date: str | None,
    action_taken: str | None,
) -> str:
    payload = f"{name}|{license_number or ''}|{board}|{effective_date or ''}|{action_taken or ''}"
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()[:32]


def _fetch_page(url: str, timeout: int = 30) -> str:
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        ),
    }
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def _page_text_from_html(html: str) -> str:
    soup = BeautifulSoup(html, 'html.parser')
    return soup.get_text('\n', strip=True)


def _discover_related_urls(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, 'html.parser')
    base_netloc = urlparse(base_url).netloc
    discovered: list[str] = []
    seen: set[str] = set()

    for anchor in soup.find_all('a', href=True):
        href = anchor.get('href', '').strip()
        if not href or href.startswith(('mailto:', 'tel:', '#', 'javascript:')):
            continue
        abs_url = urljoin(base_url, href)
        parsed = urlparse(abs_url)
        if parsed.scheme not in {'http', 'https'}:
            continue
        if parsed.netloc and parsed.netloc != base_netloc:
            continue
        text = ' '.join([anchor.get_text(' ', strip=True), href, parsed.path]).lower()
        if not any(keyword in text for keyword in DISCOVERY_KEYWORDS):
            continue
        if abs_url in seen:
            continue
        seen.add(abs_url)
        discovered.append(abs_url)
        if len(discovered) >= HTML_DISCOVERY_LIMIT:
            break
    return discovered


def _bundle_documents(source: dict[str, Any], html_by_url: dict[str, str]) -> str:
    parts = [
        f"Board: {source['board_name']}",
        f"Source URL: {source['board_url']}",
        '',
    ]
    for url, html in html_by_url.items():
        parts.append(f"### URL: {url}")
        parts.append(_page_text_from_html(html))
        parts.append('')
    return '\n'.join(parts)


def _call_kimi_extraction(html: str, board_name: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not KIMI_API_KEY:
        raise RuntimeError('KIMI_API_KEY not set')

    messages = [
        {'role': 'system', 'content': _EXTRACTION_PROMPT},
        {'role': 'user', 'content': f"Board: {board_name}\n\n{html[:80000]}"},
    ]

    resp = requests.post(
        f"{KIMI_API_BASE}/v1/chat/completions",
        headers={'Authorization': f'Bearer {KIMI_API_KEY}', 'Content-Type': 'application/json'},
        json={'model': KIMI_MODEL, 'messages': messages, 'temperature': 0.1},
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    content = data['choices'][0]['message']['content']

    content = content.strip()
    if content.startswith('```json'):
        content = content[7:]
    if content.startswith('```'):
        content = content[3:]
    if content.endswith('```'):
        content = content[:-3]
    content = content.strip()

    parsed = json.loads(content)
    if isinstance(parsed, dict) and 'actions' in parsed:
        parsed = parsed['actions']
    if not isinstance(parsed, list):
        raise ValueError(f'Expected JSON array, got {type(parsed).__name__}')
    return parsed, data


def _insert_raw_extraction(
    conn: sqlite3.Connection,
    source_id: int,
    html: str,
    status: str,
    kimi_json: dict[str, Any] | None = None,
    error: str | None = None,
) -> int:
    cursor = conn.execute(
        '''
        INSERT INTO license_sanction_raw_extractions
        (source_id, fetched_at, raw_html, kimi_response_json, extraction_status, error_message)
        VALUES (?, ?, ?, ?, ?, ?)
        ''',
        (
            source_id,
            datetime.now(timezone.utc).isoformat(),
            html,
            json.dumps(kimi_json) if kimi_json is not None else None,
            status,
            error,
        ),
    )
    conn.commit()
    return cursor.lastrowid


def _upsert_sanction(
    conn: sqlite3.Connection,
    source_id: int,
    source_board_key: str,
    raw_id: int,
    record: dict[str, Any],
) -> None:
    name = (record.get('name') or '').strip()
    if not name:
        return
    name_slug = _slugify_name(name)
    board = (record.get('board') or '').strip()
    license_number = (record.get('license_number') or '').strip() or None
    violation_type = (record.get('violation_type') or '').strip() or None
    action_taken = (record.get('action_taken') or '').strip() or None
    effective_date = (record.get('effective_date') or '').strip() or None
    county = (record.get('county_if_known') or '').strip() or None
    description = (record.get('description') or '').strip() or None
    source_url = (record.get('source_url') or '').strip() or None
    fingerprint = _fingerprint(name, license_number, board, effective_date, action_taken)
    raw_text = description or name
    now = datetime.now(timezone.utc).isoformat()

    existing = conn.execute(
        '''
        SELECT id FROM license_sanctions
        WHERE fingerprint = ?
        LIMIT 1
        ''',
        (fingerprint,),
    ).fetchone()

    if existing:
        conn.execute(
            '''
            UPDATE license_sanctions SET
                person_name = ?,
                name = ?,
                name_slug = ?,
                license_number = COALESCE(?, license_number),
                violation_type = COALESCE(?, violation_type),
                action_taken = ?,
                board = ?,
                board_type = COALESCE(board_type, ?),
                effective_date = COALESCE(?, effective_date),
                county = COALESCE(?, county),
                description = COALESCE(?, description),
                source_url = COALESCE(?, source_url),
                source_document_url = COALESCE(?, source_document_url),
                raw_extraction_id = ?,
                raw_text = COALESCE(?, raw_text),
                updated_at = ?,
                is_active = 1
            WHERE id = ?
            ''',
            (
                name,
                name,
                name_slug,
                license_number,
                violation_type,
                action_taken,
                board,
                source['board_key'],
                effective_date,
                county,
                description,
                source_url,
                source_url,
                raw_id,
                raw_text,
                now,
                existing['id'],
            ),
        )
    else:
        conn.execute(
            '''
            INSERT INTO license_sanctions
            (source_id, person_name, name, name_slug, license_number, board, board_type, violation_type, action_taken, effective_date, county, description, source_url, source_document_url, fingerprint, raw_extraction_id, raw_text, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            ''',
            (
                source_id,
                name,
                name,
                name_slug,
                license_number,
                board,
                source_board_key,
                violation_type,
                action_taken,
                effective_date,
                county,
                description,
                source_url,
                source_url,
                fingerprint,
                raw_id,
                raw_text,
                now,
                now,
            ),
        )
    conn.commit()


def _upsert_professional_board_sanction(
    conn: sqlite3.Connection,
    source_id: int,
    source_board_key: str,
    raw_id: int,
    source: dict[str, Any],
    sanction,
) -> None:
    record = {
        'name': sanction.name,
        'license_number': sanction.license_number,
        'board': sanction.board,
        'violation_type': sanction.violation_type,
        'action_taken': sanction.action_taken,
        'effective_date': sanction.effective_date,
        'county_if_known': sanction.county,
        'description': sanction.raw_text,
        'source_url': sanction.source_url or source['board_url'],
    }
    _upsert_sanction(conn, source_id, source_board_key, raw_id, record)


def _heuristic_extract_sanctions(source: dict[str, Any], html_by_url: dict[str, str]) -> list[Any]:
    board = {
        'name': source['board_name'],
        'type': source['board_key'],
        'url': source['board_url'],
    }
    extracted: list[Any] = []
    seen: set[tuple[str, str | None, str | None, str | None]] = set()

    for url, html in html_by_url.items():
        soup = BeautifulSoup(html, 'html.parser')
        for sanction in [*parse_generic_table(soup, board), *parse_list_items(soup, board)]:
            fp = (sanction.name, sanction.license_number, sanction.effective_date, sanction.action_taken)
            if fp in seen:
                continue
            haystack = ' '.join(
                part for part in [
                    sanction.name or '',
                    sanction.action_taken or '',
                    sanction.violation_type or '',
                    sanction.raw_text or '',
                ]
                if part
            ).lower()
            if not any(keyword in haystack for keyword in ACTION_KEYWORDS):
                continue
            if not sanction.action_taken:
                continue
            normalized_name = (sanction.name or '').strip().lower()
            if len(normalized_name.split()) < 2:
                continue
            if normalized_name.startswith(BLOCKED_NAME_PREFIXES):
                continue
            seen.add(fp)
            extracted.append(sanction)
            # Preserve the most specific URL we found, if the parser did not set one.
            sanction.source_url = sanction.source_url or url

    return extracted


def ingest_board(source: dict[str, Any], conn: sqlite3.Connection) -> dict[str, Any]:
    source_id = source['id']
    board_name = source['board_name']
    url = source['board_url']

    print(f"[ingest] {board_name} -- {url}")
    try:
        html = _fetch_page(url)
    except Exception as exc:
        error = f"Fetch failed: {exc}"
        print(f"[ingest] ERROR {board_name}: {error}")
        _insert_raw_extraction(conn, source_id, '', 'fetch_failed', error=error)
        conn.execute(
            "UPDATE license_sanction_sources SET last_fetched_at = ?, last_status = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), 'fetch_failed', source_id),
        )
        conn.commit()
        return {'board': board_name, 'status': 'fetch_failed', 'error': error, 'count': 0}

    html_by_url: dict[str, str] = {url: html}
    for related_url in _discover_related_urls(html, url):
        if related_url in html_by_url:
            continue
        try:
            html_by_url[related_url] = _fetch_page(related_url)
        except Exception as exc:
            print(f"[ingest] WARN {board_name}: related fetch failed for {related_url}: {exc}")

    try:
        records: list[dict[str, Any]]
        kimi_json: dict[str, Any] | None = None
        if KIMI_API_KEY:
            bundle_text = _bundle_documents(source, html_by_url)
            records, kimi_json = _call_kimi_extraction(bundle_text, board_name)
        else:
            records = []
    except Exception as exc:
        error = f"Extraction failed: {exc}"
        print(f"[ingest] WARN {board_name}: {error}")
        records = []
        kimi_json = None

    raw_bundle = _bundle_documents(source, html_by_url)
    raw_status = 'success' if records else 'heuristic_pending'
    raw_id = _insert_raw_extraction(conn, source_id, raw_bundle, raw_status, kimi_json=kimi_json)

    if not records:
        heuristic_records = _heuristic_extract_sanctions(source, html_by_url)
        if heuristic_records:
            for sanction in heuristic_records:
                try:
                    _upsert_professional_board_sanction(conn, source_id, source['board_key'], raw_id, source, sanction)
                except Exception as exc:
                    print(f"[ingest] WARN skipping heuristic record for {board_name}: {exc}")
            count = len(heuristic_records)
            conn.execute(
                "UPDATE license_sanction_sources SET last_fetched_at = ?, last_status = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), 'heuristic_success', source_id),
            )
            conn.commit()
            print(f"[ingest] {board_name} -- {count} heuristic records")
            return {'board': board_name, 'status': 'heuristic_success', 'count': count}

    count = 0
    for record in records:
        try:
            _upsert_sanction(conn, source_id, source['board_key'], raw_id, record)
            count += 1
        except Exception as exc:
            print(f"[ingest] WARN skipping record for {board_name}: {exc}")

    conn.execute(
        "UPDATE license_sanction_sources SET last_fetched_at = ?, last_status = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), 'success' if count else 'no_records', source_id),
    )
    conn.commit()
    print(f"[ingest] {board_name} -- {count} records")
    return {'board': board_name, 'status': 'success' if count else 'no_records', 'count': count}


def run_ingestion(db_path: str = DB_PATH) -> list[dict[str, Any]]:
    conn = connect_db(enable_wal=True, timeout_seconds=120, busy_timeout_ms=120000)
    try:
        rows = conn.execute('SELECT * FROM license_sanction_sources ORDER BY board_name').fetchall()
        active_keys = {source['board_key'] for source in BOARD_SOURCES}
        sources = [dict(r) for r in rows if r['board_key'] in active_keys]
        results = []
        for source in sources:
            result = ingest_board(source, conn)
            results.append(result)
        return results
    finally:
        conn.close()


if __name__ == '__main__':
    results = run_ingestion()
    total = sum(r['count'] for r in results if r['status'] == 'success')
    print(f"\nDone. {total} total sanctions ingested.")
    for result in results:
        print(f"  {result['board']}: {result['status']} ({result.get('count', 0)} records)")
