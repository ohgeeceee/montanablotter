import re
import sqlite3
import urllib.request
import json
from datetime import datetime, timezone
from html import unescape
from urllib.parse import parse_qs, urljoin, urlparse
from http.client import RemoteDisconnected

from agendas_scraper.browser import launch_browser
from services.court.tracker import add_court_event, add_court_filing, ensure_court_tracker_schema, upsert_court, upsert_court_case, upsert_court_source
from services.court.district_portal_scraper import sync_montana_district_court_calendar
from services.court.colj_portal_scraper import sync_montana_colj_calendar


DISTRICT_COURT_PORTAL_URL = 'https://dcportal.pubcourts.mt.gov/fullcourtweb/start.do'
SUPREME_COURT_ORAL_ARGUMENTS_URL = 'https://courts.mt.gov/courts/supreme/oral_arguments/'
SUPREME_COURT_PREVIOUS_ORAL_ARGUMENTS_URL = 'https://courts.mt.gov/courts/supreme/oral_arguments/Previous'
SUPREME_COURT_DAILY_ORDERS_URL = 'https://courts.mt.gov/external/orders/dailyorders'
JUD_DOCUMENT_SERVICE_DAILY_ORDERS_URL = 'https://juddocumentservice.mt.gov/getDailyOrders'
JUD_DOCUMENT_URL_TEMPLATE = 'https://juddocumentservice.mt.gov/getDocByCTrackId?DocId={doc_id}'
_COURT_NAME_RE = re.compile(r'([A-Z][A-Za-z&\-\s]+?District Court|Asbestos Court)')
_TAG_TOKEN_RE = re.compile(r'<(h3|h4|p)\b[^>]*>(.*?)</\1>', re.IGNORECASE | re.DOTALL)
_CASE_INFO_LINK_RE = re.compile(r'<a[^>]+href="([^"]*caseInfo(?:\.html)?\?id=[^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_ORAL_ARGUMENT_RE = re.compile(
    r'Oral Argument\s+is set for\s+(?:\w+,\s+)?([A-Za-z]+\s+\d{1,2},\s+\d{4}),\s+at\s+(\d{1,2}:\d{2}\s*[ap]\.m\.)\s+(?:in|at)\s+(.+?)(?:,\s+with an introduction\b|$)',
    re.IGNORECASE | re.DOTALL,
)
_MONTHS = {
    'JANUARY': '01',
    'FEBRUARY': '02',
    'MARCH': '03',
    'APRIL': '04',
    'MAY': '05',
    'JUNE': '06',
    'JULY': '07',
    'AUGUST': '08',
    'SEPTEMBER': '09',
    'OCTOBER': '10',
    'NOVEMBER': '11',
    'DECEMBER': '12',
}


def _strip_tags(value: str) -> str:
    return re.sub(r'<[^>]+>', ' ', value or '')


def _normalize_whitespace(value: str) -> str:
    return ' '.join((value or '').split()).strip()


def _county_from_court_name(name: str) -> str:
    raw = (name or '').strip()
    if raw.endswith(' District Court'):
        return raw[:-15].strip()
    if raw.endswith(' Asbestos Court'):
        return raw[:-15].strip()
    return raw


def _clean_html_text(value: str) -> str:
    return _normalize_whitespace(unescape(_strip_tags(value)))


def _extract_case_info_id(source_url: str) -> str:
    query = parse_qs(urlparse(source_url).query)
    return (query.get('id') or [''])[0].strip()


def _caption_without_case_number(text: str, case_number: str, detail_match) -> str:
    caption = (text or '')[:detail_match.start()].strip().rstrip('.')
    if not caption:
        return ''
    pattern = rf'^\s*(?:--\s*)?{re.escape(case_number)}(?:\s*[-:]\s*|\s+)'
    caption = re.sub(pattern, '', caption, flags=re.IGNORECASE).strip()
    return caption.rstrip('.')


def _parse_human_date(value: str) -> str:
    parts = (value or '').replace(',', '').split()
    if len(parts) != 3:
        return ''
    month = _MONTHS.get(parts[0].upper())
    if not month or not parts[1].isdigit() or not parts[2].isdigit():
        return ''
    return f"{parts[2]}-{month}-{int(parts[1]):02d}"


def _parse_human_time(value: str) -> str:
    raw = _normalize_whitespace((value or '').lower().replace(' ', ''))
    match = re.match(r'(\d{1,2}):(\d{2})(a\.m\.|p\.m\.)', raw)
    if not match:
        return ''
    hour = int(match.group(1))
    minute = match.group(2)
    meridiem = match.group(3)
    if meridiem == 'p.m.' and hour != 12:
        hour += 12
    if meridiem == 'a.m.' and hour == 12:
        hour = 0
    return f'{hour:02d}:{minute}'


def parse_district_court_names(html_text: str) -> list[str]:
    text = _normalize_whitespace(unescape(_strip_tags(html_text)))
    names = []
    for match in _COURT_NAME_RE.findall(text):
        court_name = _normalize_whitespace(match)
        if court_name and court_name not in names:
            names.append(court_name)
    return names


def parse_montana_supreme_court_oral_arguments(html_text: str) -> list[dict]:
    entries = []
    current_year = ''
    current_month = ''
    pending_entry = None

    for tag_name, raw_content in _TAG_TOKEN_RE.findall(html_text or ''):
        tag = (tag_name or '').lower()
        text = _clean_html_text(raw_content)
        if not text:
            continue
        if tag == 'h3' and text.isdigit():
            current_year = text
            continue
        if tag == 'h4':
            current_month = text.title()
            continue
        if tag != 'p':
            continue

        link_match = _CASE_INFO_LINK_RE.search(raw_content or '')
        if link_match:
            source_url = urljoin(SUPREME_COURT_ORAL_ARGUMENTS_URL, link_match.group(1).strip())
            case_number = _clean_html_text(link_match.group(2))
            pending_entry = {
                'year_heading': current_year,
                'month_heading': current_month,
                'case_number': case_number,
                'external_case_id': _extract_case_info_id(source_url) or case_number,
                'case_url': source_url,
                'caption': '',
                'event_date': '',
                'event_time': '',
                'event_title': 'Oral Argument',
                'location': '',
                'notes': '',
            }
            entries.append(pending_entry)
            detail_match = _ORAL_ARGUMENT_RE.search(text)
            if detail_match:
                pending_entry['notes'] = text
                pending_entry['caption'] = _caption_without_case_number(text, case_number, detail_match)
                pending_entry['event_date'] = _parse_human_date(detail_match.group(1))
                pending_entry['event_time'] = _parse_human_time(detail_match.group(2))
                pending_entry['location'] = _normalize_whitespace(detail_match.group(3)).rstrip('.')
                if pending_entry['location']:
                    pending_entry['event_title'] = f"Oral Argument · {pending_entry['location']}"
            continue

        if not pending_entry:
            continue

        notes = pending_entry['notes']
        pending_entry['notes'] = '\n\n'.join(part for part in (notes, text) if part).strip()
        if pending_entry['caption']:
            continue

        detail_match = _ORAL_ARGUMENT_RE.search(text)
        if not detail_match:
            continue
        pending_entry['caption'] = text[:detail_match.start()].strip().rstrip('.')
        pending_entry['event_date'] = _parse_human_date(detail_match.group(1))
        pending_entry['event_time'] = _parse_human_time(detail_match.group(2))
        pending_entry['location'] = _normalize_whitespace(detail_match.group(3)).rstrip('.')
        if pending_entry['location']:
            pending_entry['event_title'] = f"Oral Argument · {pending_entry['location']}"

    return [entry for entry in entries if entry.get('case_number') and entry.get('caption') and entry.get('event_date')]


def fetch_district_court_portal_html(timeout: int = 45) -> str:
    request = urllib.request.Request(
        DISTRICT_COURT_PORTAL_URL,
        headers={
            'User-Agent': 'Mozilla/5.0 (compatible; MontanaBlotterCourtTracker/1.0)',
            'Accept': 'text/html,application/xhtml+xml',
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode('utf-8', errors='replace')


def fetch_district_court_portal_html_via_playwright(timeout_ms: int = 45000) -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        try:
            page = browser.new_page()
            page.goto(DISTRICT_COURT_PORTAL_URL, wait_until='domcontentloaded', timeout=timeout_ms)
            page.wait_for_timeout(1200)
            return page.content()
        finally:
            browser.close()


def load_district_court_portal_html(timeout: int = 45) -> tuple[str, str]:
    try:
        return fetch_district_court_portal_html(timeout=timeout), 'http'
    except (RemoteDisconnected, urllib.error.URLError, ConnectionResetError):
        return fetch_district_court_portal_html_via_playwright(timeout_ms=timeout * 1000), 'playwright'


def fetch_supreme_court_oral_arguments_html(timeout: int = 45) -> str:
    request = urllib.request.Request(
        SUPREME_COURT_ORAL_ARGUMENTS_URL,
        headers={
            'User-Agent': 'Mozilla/5.0 (compatible; MontanaBlotterCourtTracker/1.0)',
            'Accept': 'text/html,application/xhtml+xml',
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode('utf-8', errors='replace')


def fetch_supreme_court_previous_oral_arguments_html(timeout: int = 45) -> str:
    request = urllib.request.Request(
        SUPREME_COURT_PREVIOUS_ORAL_ARGUMENTS_URL,
        headers={
            'User-Agent': 'Mozilla/5.0 (compatible; MontanaBlotterCourtTracker/1.0)',
            'Accept': 'text/html,application/xhtml+xml',
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode('utf-8', errors='replace')


def fetch_supreme_court_daily_orders_json(timeout: int = 45) -> list[dict]:
    request = urllib.request.Request(
        JUD_DOCUMENT_SERVICE_DAILY_ORDERS_URL,
        headers={
            'User-Agent': 'Mozilla/5.0 (compatible; MontanaBlotterCourtTracker/1.0)',
            'Accept': 'application/json,text/plain,*/*',
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8', errors='replace'))


def _case_status_from_event_date(event_date: str) -> str:
    normalized = (event_date or '').strip()
    if normalized and normalized < datetime.now(timezone.utc).date().isoformat():
        return 'completed'
    return 'scheduled'


def _parse_daily_orders_date(value: str) -> str:
    raw = (value or '').strip()
    if len(raw) >= 10 and raw[4] == '-' and raw[7] == '-':
        return raw[:10]
    return ''


def _is_opinion_document(row: dict) -> bool:
    description = _normalize_whitespace((row or {}).get('documentDescription', ''))
    return description.lower().startswith('opinion -')


def _sync_supreme_court_oral_argument_feed(
    conn: sqlite3.Connection,
    *,
    source_slug: str,
    source_name: str,
    source_url: str,
    html_text: str | None,
    fetch_html,
) -> dict:
    ensure_court_tracker_schema(conn)
    source_id = upsert_court_source(
        conn,
        slug=source_slug,
        name=source_name,
        source_url=source_url,
        provider_type='court_calendar',
        status='active',
    )
    conn.execute(
        "UPDATE court_sources SET last_scraped_at = datetime('now'), updated_at = datetime('now') WHERE id = ?",
        (source_id,),
    )

    fetched_live = html_text is None
    if fetched_live:
        html_text = fetch_html()

    court_id = upsert_court(
        conn,
        source_id=source_id,
        slug='montana-supreme-court',
        name='Montana Supreme Court',
        court_type='Supreme Court',
        county='Lewis and Clark',
        city='Helena',
        portal_url=SUPREME_COURT_ORAL_ARGUMENTS_URL,
    )

    entries = parse_montana_supreme_court_oral_arguments(html_text or '')
    case_numbers = []
    event_count = 0
    for entry in entries:
        case_numbers.append(entry['case_number'])
        case_id = upsert_court_case(
            conn,
            court_id=court_id,
            case_number=entry['case_number'],
            caption=entry['caption'],
            status=_case_status_from_event_date(entry['event_date']),
            case_type='Oral Argument',
            source_url=entry['case_url'],
            external_case_id=entry['external_case_id'],
        )
        add_court_event(
            conn,
            case_id=case_id,
            event_type='Oral Argument',
            event_date=entry['event_date'],
            event_time=entry['event_time'],
            event_title=entry['event_title'],
            room=entry['location'],
            notes=entry['notes'],
            source_url=entry['case_url'],
        )
        event_count += 1

    conn.execute(
        '''
        UPDATE court_sources
        SET last_success_at = datetime('now'),
            last_error = NULL,
            updated_at = datetime('now')
        WHERE id = ?
        ''',
        (source_id,),
    )

    return {
        'source_id': source_id,
        'source_slug': source_slug,
        'source_url': source_url,
        'court_count': 1,
        'case_count': len(entries),
        'event_count': event_count,
        'synced_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'fetched_live': fetched_live,
        'fetch_method': 'http' if fetched_live else 'fixture',
        'court_names': ['Montana Supreme Court'],
        'case_numbers': case_numbers,
        'upserts': len(entries),
    }


def sync_montana_district_court_directory(
    conn: sqlite3.Connection,
    *,
    html_text: str | None = None,
) -> dict:
    ensure_court_tracker_schema(conn)
    source_id = upsert_court_source(
        conn,
        slug='montana-district-court-portal',
        name='Montana District Court Public Portal',
        source_url=DISTRICT_COURT_PORTAL_URL,
        provider_type='portal_directory',
        status='active',
    )
    conn.execute(
        "UPDATE court_sources SET last_scraped_at = datetime('now'), updated_at = datetime('now') WHERE id = ?",
        (source_id,),
    )

    owns_html = html_text is None
    fetch_method = 'fixture'
    if owns_html:
        html_text, fetch_method = load_district_court_portal_html()
    court_names = parse_district_court_names(html_text or '')

    created_or_updated = 0
    for name in court_names:
        county = _county_from_court_name(name)
        court_type = 'Specialty Court' if 'Asbestos Court' in name else 'District Court'
        upsert_court(
            conn,
            source_id=source_id,
            slug=name,
            name=name,
            court_type=court_type,
            county=county,
            portal_url=DISTRICT_COURT_PORTAL_URL,
        )
        created_or_updated += 1

    conn.execute(
        '''
        UPDATE court_sources
        SET last_success_at = datetime('now'),
            last_error = NULL,
            updated_at = datetime('now')
        WHERE id = ?
        ''',
        (source_id,),
    )

    return {
        'source_id': source_id,
        'source_slug': 'montana-district-court-portal',
        'source_url': DISTRICT_COURT_PORTAL_URL,
        'court_count': len(court_names),
        'synced_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'fetched_live': owns_html,
        'fetch_method': fetch_method,
        'court_names': court_names,
        'upserts': created_or_updated,
    }


def sync_montana_supreme_court_oral_arguments(
    conn: sqlite3.Connection,
    *,
    html_text: str | None = None,
) -> dict:
    return _sync_supreme_court_oral_argument_feed(
        conn,
        source_slug='montana-supreme-court-oral-arguments',
        source_name='Montana Supreme Court Oral Argument Schedule',
        source_url=SUPREME_COURT_ORAL_ARGUMENTS_URL,
        html_text=html_text,
        fetch_html=fetch_supreme_court_oral_arguments_html,
    )


def sync_montana_supreme_court_previous_oral_arguments(
    conn: sqlite3.Connection,
    *,
    html_text: str | None = None,
) -> dict:
    return _sync_supreme_court_oral_argument_feed(
        conn,
        source_slug='montana-supreme-court-previous-oral-arguments',
        source_name='Montana Supreme Court Previous Oral Arguments',
        source_url=SUPREME_COURT_PREVIOUS_ORAL_ARGUMENTS_URL,
        html_text=html_text,
        fetch_html=fetch_supreme_court_previous_oral_arguments_html,
    )


def sync_montana_supreme_court_daily_opinions(
    conn: sqlite3.Connection,
    *,
    rows: list[dict] | None = None,
) -> dict:
    ensure_court_tracker_schema(conn)
    source_id = upsert_court_source(
        conn,
        slug='montana-supreme-court-daily-opinions',
        name='Montana Supreme Court Daily Opinions',
        source_url=SUPREME_COURT_DAILY_ORDERS_URL,
        provider_type='document_feed',
        status='active',
    )
    conn.execute(
        "UPDATE court_sources SET last_scraped_at = datetime('now'), updated_at = datetime('now') WHERE id = ?",
        (source_id,),
    )

    fetched_live = rows is None
    if fetched_live:
        rows = fetch_supreme_court_daily_orders_json()

    court_id = upsert_court(
        conn,
        source_id=source_id,
        slug='montana-supreme-court',
        name='Montana Supreme Court',
        court_type='Supreme Court',
        county='Lewis and Clark',
        city='Helena',
        portal_url=SUPREME_COURT_DAILY_ORDERS_URL,
    )

    opinion_rows = [row for row in (rows or []) if _is_opinion_document(row)]
    case_numbers = []
    filing_count = 0
    for row in opinion_rows:
        case_number = _normalize_whitespace(str(row.get('caseNumber', '')))
        title = _normalize_whitespace(str(row.get('title', '')))
        description = _normalize_whitespace(str(row.get('documentDescription', '')))
        ctrack_id = _normalize_whitespace(str(row.get('cTrackId', '')))
        filing_date = _parse_daily_orders_date(str(row.get('fileDate', '')))
        if not case_number or not description or not ctrack_id or not filing_date:
            continue

        document_url = JUD_DOCUMENT_URL_TEMPLATE.format(doc_id=ctrack_id)
        existing_case = conn.execute(
            '''
            SELECT caption, case_type, filed_date, source_url
            FROM court_cases
            WHERE court_id = ? AND case_number = ?
            ''',
            (court_id, case_number),
        ).fetchone()
        case_id = upsert_court_case(
            conn,
            court_id=court_id,
            case_number=case_number,
            caption=(existing_case['caption'] if existing_case and existing_case['caption'] else title) or case_number,
            status='completed',
            filed_date=(existing_case['filed_date'] if existing_case and existing_case['filed_date'] else filing_date),
            case_type=(existing_case['case_type'] if existing_case and existing_case['case_type'] else 'Opinion'),
            source_url=(existing_case['source_url'] if existing_case and existing_case['source_url'] else document_url),
            external_case_id=case_number,
        )
        add_court_filing(
            conn,
            case_id=case_id,
            filing_date=filing_date,
            filing_title=description,
            document_number=ctrack_id,
            source_url=document_url,
        )
        case_numbers.append(case_number)
        filing_count += 1

    conn.execute(
        '''
        UPDATE court_sources
        SET last_success_at = datetime('now'),
            last_error = NULL,
            updated_at = datetime('now')
        WHERE id = ?
        ''',
        (source_id,),
    )

    return {
        'source_id': source_id,
        'source_slug': 'montana-supreme-court-daily-opinions',
        'source_url': SUPREME_COURT_DAILY_ORDERS_URL,
        'court_count': 1,
        'case_count': len(set(case_numbers)),
        'filing_count': filing_count,
        'synced_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'fetched_live': fetched_live,
        'fetch_method': 'http' if fetched_live else 'fixture',
        'court_names': ['Montana Supreme Court'],
        'case_numbers': case_numbers,
        'upserts': filing_count,
    }
