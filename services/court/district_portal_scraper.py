"""
Montana District Court Public Access Portal — Case & Calendar Scraper

Integrates with the existing court_tracker schema:
  court_sources → courts → court_cases → court_events / court_filings

Targets: https://dcportal.pubcourts.mt.gov
"""
from __future__ import annotations

import hashlib
import random
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

from agendas_scraper.browser import launch_browser
from services.court.tracker import (
    add_court_event,
    ensure_court_tracker_schema,
    upsert_court,
    upsert_court_case,
    upsert_court_source,
)

DISTRICT_COURT_PORTAL_URL = 'https://dcportal.pubcourts.mt.gov/fullcourtweb/start.do'
_COURT_SELECT_RE = re.compile(r'<option\s+[^>]*value="([^"]+)"[^>]*>([^<]*District Court[^<]*)</option>', re.IGNORECASE)
_CASE_STATUS_RE = re.compile(r'Case\s*Status\s*:\s*(.+?)(?:\n|\r|<)', re.IGNORECASE)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def _strip_tags(value: str) -> str:
    return re.sub(r'<[^>]+>', ' ', value or '')


def _clean_text(value: str) -> str:
    return ' '.join((_strip_tags(value) or '').split()).strip()


def _parse_date(value: str) -> str:
    """Normalize MM/DD/YYYY → YYYY-MM-DD."""
    raw = (value or '').strip()
    if not raw:
        return ''
    for fmt in ('%m/%d/%Y', '%m/%d/%y', '%Y-%m-%d', '%B %d, %Y', '%b %d, %Y'):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return raw


def _source_hash(*parts: str) -> str:
    return hashlib.sha1('||'.join(p.strip().lower() for p in parts).encode('utf-8')).hexdigest()[:24]


def _extract_table_rows(html: str) -> list[dict[str, str]]:
    """Parse generic data tables from FullCourt HTML."""
    rows = []
    for tr_match in re.finditer(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL | re.IGNORECASE):
        tr_html = tr_match.group(1)
        link_match = re.search(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', tr_html, re.DOTALL | re.IGNORECASE)
        if not link_match:
            continue
        tds = re.findall(r'<td[^>]*>(.*?)</td>', tr_html, re.DOTALL | re.IGNORECASE)
        if len(tds) < 2:
            continue
        rows.append({
            'detail_url': _clean_text(link_match.group(1)),
            'case_number': _clean_text(link_match.group(2)),
            'cells': [_clean_text(td) for td in tds],
        })
    return rows


def _get_court_options_from_start_page(html: str) -> list[tuple[str, str]]:
    """Extract (value, label) tuples from the court dropdown."""
    options = []
    for match in _COURT_SELECT_RE.finditer(html):
        val = match.group(1).strip()
        label = _clean_text(match.group(2))
        if val and label and 'district court' in label.lower():
            options.append((val, label))
    return options


def _county_from_court_label(label: str) -> str:
    raw = label.strip()
    if ' - ' in raw:
        raw = raw.split(' - ', 1)[1]
    if raw.endswith(' District Court'):
        return raw[:-15].strip()
    return raw


class DistrictPortalScraper:
    """Playwright scraper for the District Court Public Access Portal."""

    def __init__(self):
        self.base_url = 'https://dcportal.pubcourts.mt.gov'
        self.playwright = None
        self.browser = None
        self.page = None

    def _start(self):
        self.playwright = sync_playwright().start()
        self.browser = launch_browser(self.playwright)
        self.page = self.browser.new_page()
        self.page.set_default_timeout(30000)

    def _stop(self):
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()

    def _login(self, court_value: str, court_label: str) -> bool:
        """Navigate to portal, select court, and log in using a fresh browser context."""
        # Fresh context for each court to avoid WAF detection
        context = self.browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        )
        self.page = context.new_page()
        self.page.set_default_timeout(30000)

        self.page.goto(DISTRICT_COURT_PORTAL_URL, wait_until='domcontentloaded')
        self.page.wait_for_timeout(4000 + random.randint(0, 2000))

        try:
            self.page.wait_for_selector("select[name='tenant']", timeout=10000)
            self.page.select_option("select[name='tenant']", value=court_value)
        except Exception as exc:
            print(f'  ⚠️ Could not select court dropdown for {court_label}: {exc}')
            return False

        self.page.wait_for_timeout(2000)

        try:
            # Submit form via JS since the login button is hidden
            self.page.evaluate("() => { const form = document.querySelector('form'); if (form) form.submit(); }")
            self.page.wait_for_load_state('networkidle')
            self.page.wait_for_timeout(4000 + random.randint(0, 2000))
        except Exception as exc:
            print(f'  ⚠️ Login failed for {court_label}: {exc}')
            return False

        # Verify we're on dashboard
        if 'Dashboard' not in self.page.title() and 'mainMenu' not in self.page.url:
            print(f'  ⚠️ Unexpected page after login: {self.page.title()}')
            return False

        return True

    def _scrape_weekly_calendar(self, county: str) -> list[dict[str, Any]]:
        """Scrape the weekly court calendar for upcoming hearings."""
        events = []
        try:
            # Navigate directly to weekly calendar (must stay in same session)
            self.page.goto(f'{self.base_url}/fullcourtweb/courtCalendarWeekly.do', wait_until='domcontentloaded')
            self.page.wait_for_timeout(1500)
        except Exception as exc:
            print(f'  ⚠️ Calendar navigation failed for {county}: {exc}')
            return events

        # The calendar defaults to a specific date; update to today and retrieve
        today = datetime.now().strftime('%m/%d/%Y')
        try:
            self.page.fill("input[name='wrapper.queryBean.queryDate']", today)
            self.page.wait_for_timeout(300)
            self.page.evaluate("document.querySelector('input[name=\"retrieveAction\"]').click()")
            self.page.wait_for_load_state('networkidle')
            self.page.wait_for_timeout(2000)
        except Exception as exc:
            print(f'  ⚠️ Calendar retrieve failed for {county}: {exc}')
            return events

        html = self.page.content()
        rows = _extract_table_rows(html)

        for row in rows:
            cells = row['cells']
            event_date = _parse_date(cells[2]) if len(cells) > 2 else ''
            event_time = cells[3] if len(cells) > 3 else ''
            event_title = cells[1] if len(cells) > 1 else 'Hearing'
            judge = cells[4] if len(cells) > 4 else ''
            notes = cells[5] if len(cells) > 5 else ''

            events.append({
                'case_number': row['case_number'],
                'detail_url': urljoin(self.base_url, row['detail_url']) if row['detail_url'].startswith('/') else row['detail_url'],
                'event_date': event_date,
                'event_time': event_time,
                'event_title': event_title,
                'judge': judge,
                'notes': notes,
                'source_url': DISTRICT_COURT_PORTAL_URL,
            })

        return events

    def _scrape_case_detail(self, detail_url: str) -> dict[str, Any] | None:
        """Fetch and parse a single case detail page."""
        try:
            self.page.goto(detail_url, wait_until='domcontentloaded')
            self.page.wait_for_timeout(1000)
        except Exception as exc:
            print(f'  ⚠️ Failed to load case detail {detail_url}: {exc}')
            return None

        html = self.page.content()
        text = _clean_text(html)

        case_number = ''
        caption = ''
        status = 'open'
        filed_date = ''
        case_type = ''
        judge = ''

        cn_match = re.search(r'Case\s*Number\s*[:\-]?\s*([A-Z0-9\-]+)', text, re.IGNORECASE)
        if cn_match:
            case_number = cn_match.group(1).strip()

        title_match = re.search(r'Case\s*Title\s*[:\-]?\s*(.+?)(?:Case\s|Number|Status|Filed)', text, re.IGNORECASE)
        if title_match:
            caption = title_match.group(1).strip()

        st_match = _CASE_STATUS_RE.search(text)
        if st_match:
            status_raw = st_match.group(1).strip().lower()
            if 'closed' in status_raw or 'disposed' in status_raw:
                status = 'completed'
            else:
                status = 'open'

        fd_match = re.search(r'Filed\s*Date\s*[:\-]?\s*(\d{1,2}/\d{1,2}/\d{4})', text, re.IGNORECASE)
        if fd_match:
            filed_date = _parse_date(fd_match.group(1))

        ct_match = re.search(r'Case\s*Type\s*[:\-]?\s*([A-Z]{2,})', text, re.IGNORECASE)
        if ct_match:
            case_type = ct_match.group(1).strip()

        return {
            'case_number': case_number,
            'caption': caption or case_number,
            'status': status,
            'filed_date': filed_date,
            'case_type': case_type,
            'judge': judge,
            'source_url': detail_url,
        }

    def scrape_all_courts(
        self,
        conn: sqlite3.Connection,
        days_ahead: int = 14,
        max_courts: int | None = None,
    ) -> dict[str, Any]:
        """
        Scrape calendars for all available District Courts.
        Returns summary stats.
        """
        ensure_court_tracker_schema(conn)
        source_id = upsert_court_source(
            conn,
            slug='montana-district-court-calendar',
            name='Montana District Court Calendar',
            source_url=DISTRICT_COURT_PORTAL_URL,
            provider_type='court_calendar',
            status='active',
        )
        conn.execute(
            "UPDATE court_sources SET last_scraped_at = datetime('now'), updated_at = datetime('now') WHERE id = ?",
            (source_id,),
        )

        # Use a single browser but fresh context for each court
        self.playwright = sync_playwright().start()
        self.browser = launch_browser(self.playwright)

        try:
            # Load start page to discover available courts (one-time)
            temp_page = self.browser.new_page()
            temp_page.goto(DISTRICT_COURT_PORTAL_URL, wait_until='domcontentloaded')
            temp_page.wait_for_timeout(4000 + random.randint(0, 2000))
            start_html = temp_page.content()
            temp_page.close()
            court_options = _get_court_options_from_start_page(start_html)

            if max_courts:
                court_options = court_options[:max_courts]

            print(f'  Discovered {len(court_options)} District Courts on portal')

            total_events = 0
            total_cases = 0
            court_names = []

            for court_value, court_label in court_options:
                county = _county_from_court_label(court_label)
                court_names.append(court_label)
                print(f'  → {court_label} ({county})')

                if not self._login(court_value, court_label):
                    continue

                court_id = upsert_court(
                    conn,
                    source_id=source_id,
                    slug=court_label,
                    name=court_label,
                    court_type='District Court',
                    county=county,
                    portal_url=DISTRICT_COURT_PORTAL_URL,
                )

                events = self._scrape_weekly_calendar(county)
                print(f'    Found {len(events)} upcoming events')

                seen_cases: set[str] = set()
                for event in events:
                    case_number = event['case_number']
                    if not case_number:
                        continue

                    case_detail = None
                    if case_number not in seen_cases:
                        seen_cases.add(case_number)
                        case_detail = self._scrape_case_detail(event['detail_url'])
                        total_cases += 1

                    case_id = upsert_court_case(
                        conn,
                        court_id=court_id,
                        case_number=case_number,
                        caption=(case_detail['caption'] if case_detail else case_number),
                        status=(case_detail['status'] if case_detail else 'open'),
                        filed_date=(case_detail['filed_date'] if case_detail else ''),
                        case_type=(case_detail['case_type'] if case_detail else event['event_title']),
                        source_url=event['detail_url'],
                        external_case_id=case_number,
                    )

                    add_court_event(
                        conn,
                        case_id=case_id,
                        event_type='Hearing',
                        event_date=event['event_date'],
                        event_time=event['event_time'],
                        event_title=event['event_title'],
                        judge=event['judge'],
                        notes=event['notes'],
                        source_url=event['source_url'],
                    )
                    total_events += 1

                # Close the page after each court to free resources
                if self.page:
                    self.page.close()
                    self.page = None

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
                'source_slug': 'montana-district-court-calendar',
                'source_url': DISTRICT_COURT_PORTAL_URL,
                'court_count': len(court_options),
                'case_count': total_cases,
                'event_count': total_events,
                'synced_at': _now_iso(),
                'fetched_live': True,
                'fetch_method': 'playwright',
                'court_names': court_names,
                'upserts': total_events,
            }
        finally:
            self._stop()


def sync_montana_district_court_calendar(
    conn: sqlite3.Connection,
    *,
    days_ahead: int = 14,
    max_courts: int | None = None,
) -> dict[str, Any]:
    """Adapter entry-point for the refresh runner."""
    scraper = DistrictPortalScraper()
    return scraper.scrape_all_courts(conn, days_ahead=days_ahead, max_courts=max_courts)
