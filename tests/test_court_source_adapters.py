import os
import tempfile
import unittest
from unittest import mock

import app as app_module
import config
import init_db
from court_source_adapters import (
    load_district_court_portal_html,
    parse_montana_supreme_court_oral_arguments,
    parse_district_court_names,
    sync_montana_district_court_directory,
    sync_montana_supreme_court_daily_opinions,
    sync_montana_supreme_court_oral_arguments,
    sync_montana_supreme_court_previous_oral_arguments,
)


class CourtSourceAdaptersTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-court-src-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        init_db.migrate()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = config.DB_PATH
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_parse_district_court_names_from_select_html(self) -> None:
        html = '''
            <select name="court">
                <option>Yellowstone District Court</option>
                <option>Missoula District Court</option>
                <option>Asbestos Court</option>
            </select>
        '''
        names = parse_district_court_names(html)
        self.assertEqual(
            names,
            ['Yellowstone District Court', 'Missoula District Court', 'Asbestos Court'],
        )

    def test_parse_montana_supreme_court_oral_arguments(self) -> None:
        html = '''
            <div class="content-block my-3">
                <h3>2026</h3>
                <h4>MARCH</h4>
                <p><a href="https://courts.mt.gov/external/orders/caseInfo?id=DA%2025-0409">DA 25-0409</a></p>
                <p>STATE OF MONTANA, Plaintiff and Appellee, v. JANE DOE, Defendant and Appellant. <strong>Oral Argument </strong>is set for Wednesday, March 25, 2026, at 9:30 a.m. in the Courtroom of the Montana Supreme Court, Joseph P. Mazurek Justice Building, in Helena.</p>
                <p>Additional case background.</p>
            </div>
        '''

        entries = parse_montana_supreme_court_oral_arguments(html)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['case_number'], 'DA 25-0409')
        self.assertEqual(entries[0]['event_date'], '2026-03-25')
        self.assertEqual(entries[0]['event_time'], '09:30')
        self.assertIn('JANE DOE', entries[0]['caption'])
        self.assertIn('Additional case background.', entries[0]['notes'])

    def test_parse_montana_supreme_court_oral_arguments_supports_caseinfo_html_links(self) -> None:
        html = '''
            <div class="content-block my-3">
                <h3>2024</h3>
                <h4>SEPTEMBER</h4>
                <p><strong><a href="https://courts.mt.gov/external/orders/caseInfo.html?id=da%2023-0555">DA 23-0555</a></strong></p>
                <p>O'NEILL, Plaintiff and Appellee, v. GIANFORTE, Defendant and Appellant. <strong>Oral Argument </strong>is set for Friday, September 13, 2024, at 9:30 a.m. at the Wilma Theater in Missoula, Montana, with an introduction to the argument beginning at 9:00 a.m.</p>
            </div>
        '''

        entries = parse_montana_supreme_court_oral_arguments(html)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['case_number'], 'DA 23-0555')
        self.assertEqual(entries[0]['event_date'], '2024-09-13')
        self.assertEqual(entries[0]['external_case_id'].lower(), 'da 23-0555')

    def test_parse_montana_supreme_court_oral_arguments_supports_inline_archive_entries(self) -> None:
        html = '''
            <div class="content-block my-3">
                <h3>2019</h3>
                <h4>MAY</h4>
                <p><strong>--</strong><strong><a href="/external/orders/caseInfo.html?id=DA 16-0473">DA 16-0473</a></strong> STATE OF MONTANA, Plaintiff and Appellee, v. BRIAN DAVID LAIRD, Defendant and Appellant. Oral Argument is set for Wednesday, May 1, 2019, at 10:00 a.m. in the Strand Union Building, Ballroom A, Montana State University, Bozeman, Montana, with an introduction to the oral argument beginning at 9:30 a.m.</p>
            </div>
        '''

        entries = parse_montana_supreme_court_oral_arguments(html)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['case_number'], 'DA 16-0473')
        self.assertEqual(entries[0]['event_date'], '2019-05-01')
        self.assertIn('BRIAN DAVID LAIRD', entries[0]['caption'])
        self.assertFalse(entries[0]['caption'].startswith('DA 16-0473'))
        self.assertTrue(entries[0]['case_url'].startswith('https://courts.mt.gov/'))

    def test_sync_montana_district_court_directory_persists_courts(self) -> None:
        html = '''
            <select name="court">
                <option>Yellowstone District Court</option>
                <option>Missoula District Court</option>
            </select>
        '''
        conn = app_module.get_db()
        summary = sync_montana_district_court_directory(conn, html_text=html)
        conn.commit()
        row = conn.execute('SELECT COUNT(*) AS total FROM courts').fetchone()
        source = conn.execute('SELECT last_success_at FROM court_sources WHERE slug = ?', ('montana-district-court-portal',)).fetchone()
        conn.close()

        self.assertEqual(summary['court_count'], 2)
        self.assertEqual(row['total'], 2)
        self.assertIsNotNone(source['last_success_at'])

    def test_sync_montana_supreme_court_oral_arguments_persists_cases_and_events(self) -> None:
        html = '''
            <div class="content-block my-3">
                <h3>2026</h3>
                <h4>APRIL</h4>
                <p><a href="https://courts.mt.gov/external/orders/caseInfo?id=DA%2025-0142">DA 25-0142</a></p>
                <p>ELLINGSON, Plaintiffs, v. STATE OF MONTANA, Defendants. <strong>Oral Argument </strong>is set for Friday, April 10, 2026, at 10:00 a.m. in the Dennison Theatre, on the campus of the University of Montana, Missoula, Montana, with an introduction to the argument beginning at 9:30 a.m.</p>
            </div>
        '''
        conn = app_module.get_db()
        summary = sync_montana_supreme_court_oral_arguments(conn, html_text=html)
        second_summary = sync_montana_supreme_court_oral_arguments(conn, html_text=html)
        conn.commit()
        court_row = conn.execute('SELECT name, court_type FROM courts WHERE slug = ?', ('montana-supreme-court',)).fetchone()
        case_row = conn.execute('SELECT case_number, caption, status FROM court_cases').fetchone()
        event_row = conn.execute('SELECT event_date, event_time, event_title, room FROM court_events').fetchone()
        event_count_row = conn.execute('SELECT COUNT(*) AS total FROM court_events').fetchone()
        source = conn.execute(
            'SELECT last_success_at FROM court_sources WHERE slug = ?',
            ('montana-supreme-court-oral-arguments',),
        ).fetchone()
        conn.close()

        self.assertEqual(summary['case_count'], 1)
        self.assertEqual(summary['event_count'], 1)
        self.assertEqual(second_summary['event_count'], 1)
        self.assertEqual(court_row['court_type'], 'Supreme Court')
        self.assertEqual(case_row['case_number'], 'DA 25-0142')
        self.assertEqual(case_row['status'], 'scheduled')
        self.assertEqual(event_row['event_date'], '2026-04-10')
        self.assertEqual(event_row['event_time'], '10:00')
        self.assertIn('Dennison Theatre', event_row['event_title'])
        self.assertIn('Missoula', event_row['room'])
        self.assertEqual(event_count_row['total'], 1)
        self.assertIsNotNone(source['last_success_at'])

    def test_sync_montana_supreme_court_previous_oral_arguments_marks_cases_completed(self) -> None:
        html = '''
            <div class="card-body">
                <h4>FEBRUARY</h4>
                <p><a href="https://courts.mt.gov/external/orders/caseInfo?id=DA%2025-0187">DA 25-0187</a></p>
                <p>MONTANA CONSERVATION VOTERS, Plaintiffs and Appellants, v. CHRISTI JACOBSEN, Defendant and Appellee. <strong>Oral Argument </strong>is set for Wednesday, February 11, 2026, at 9:30 a.m. in the Courtroom of the Montana Supreme Court, Joseph P. Mazurek Justice Building, in Helena.</p>
            </div>
        '''
        conn = app_module.get_db()
        summary = sync_montana_supreme_court_previous_oral_arguments(conn, html_text=html)
        conn.commit()
        case_row = conn.execute(
            'SELECT case_number, status FROM court_cases WHERE case_number = ?',
            ('DA 25-0187',),
        ).fetchone()
        source = conn.execute(
            'SELECT last_success_at FROM court_sources WHERE slug = ?',
            ('montana-supreme-court-previous-oral-arguments',),
        ).fetchone()
        conn.close()

        self.assertEqual(summary['case_count'], 1)
        self.assertEqual(case_row['status'], 'completed')
        self.assertIsNotNone(source['last_success_at'])

    def test_sync_montana_supreme_court_daily_opinions_persists_filings(self) -> None:
        rows = [
            {
                'documentDescription': 'Opinion - Published - Justice Gustafson - REVERSED and REMANDED',
                'fileDate': '2026-03-10 16:02:26.0',
                'caseNumber': 'DA 23-0346',
                'title': 'State v. K. Sandberg',
                'cTrackId': '560004',
            },
            {
                'documentDescription': 'Order - Grant - Extension of Time',
                'fileDate': '2026-03-10 15:57:00.0',
                'caseNumber': 'DA 25-0167',
                'title': 'State v. T. Wallace',
                'cTrackId': '560005',
            },
        ]
        conn = app_module.get_db()
        summary = sync_montana_supreme_court_daily_opinions(conn, rows=rows)
        conn.commit()
        case_row = conn.execute(
            'SELECT case_number, caption, status FROM court_cases WHERE case_number = ?',
            ('DA 23-0346',),
        ).fetchone()
        filing_row = conn.execute(
            'SELECT filing_date, filing_title, document_number, source_url FROM court_filings WHERE document_number = ?',
            ('560004',),
        ).fetchone()
        source = conn.execute(
            'SELECT last_success_at FROM court_sources WHERE slug = ?',
            ('montana-supreme-court-daily-opinions',),
        ).fetchone()
        conn.close()

        self.assertEqual(summary['case_count'], 1)
        self.assertEqual(summary['filing_count'], 1)
        self.assertEqual(case_row['status'], 'completed')
        self.assertEqual(case_row['caption'], 'State v. K. Sandberg')
        self.assertEqual(filing_row['filing_date'], '2026-03-10')
        self.assertEqual(filing_row['document_number'], '560004')
        self.assertIn('getDocByCTrackId?DocId=560004', filing_row['source_url'])
        self.assertIsNotNone(source['last_success_at'])

    @mock.patch('court_source_adapters.fetch_district_court_portal_html_via_playwright')
    @mock.patch('court_source_adapters.fetch_district_court_portal_html')
    def test_load_district_court_portal_html_falls_back_to_playwright(self, fetch_http, fetch_playwright) -> None:
        fetch_http.side_effect = ConnectionResetError('reset')
        fetch_playwright.return_value = '<html><option>Yellowstone District Court</option></html>'

        html, method = load_district_court_portal_html()

        self.assertEqual(method, 'playwright')
        self.assertIn('Yellowstone District Court', html)


if __name__ == '__main__':
    unittest.main()
