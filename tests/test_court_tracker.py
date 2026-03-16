import os
import tempfile
import unittest

import app as app_module
import config
import init_db
from court_tracker import (
    add_court_event,
    add_court_filing,
    court_admin_context,
    upsert_court,
    upsert_court_case,
    upsert_court_source,
)


class CourtTrackerTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-courts-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        app_module.app.config['TESTING'] = True

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

    def _seed_case(self) -> str:
        conn = app_module.get_db()
        source_id = upsert_court_source(
            conn,
            slug='yellowstone-district-portal',
            name='Yellowstone District Court Portal',
            source_url='https://example.gov/courts/yellowstone',
            provider_type='calendar',
        )
        court_id = upsert_court(
            conn,
            source_id=source_id,
            slug='yellowstone-district-court',
            name='Yellowstone County District Court',
            court_type='District Court',
            county='Yellowstone',
            city='Billings',
            portal_url='https://example.gov/courts/yellowstone',
        )
        case_id = upsert_court_case(
            conn,
            court_id=court_id,
            case_number='DV-26-1234',
            caption='State of Montana v. Jane Doe',
            status='scheduled',
            filed_date='2026-03-12',
            case_type='Criminal',
            source_url='https://example.gov/courts/yellowstone/cases/dv-26-1234',
        )
        add_court_event(
            conn,
            case_id=case_id,
            event_type='arraignment',
            event_date='2026-03-20',
            event_time='09:00',
            event_title='Arraignment Hearing',
            judge='Smith',
            notes='Initial appearance on criminal charges.',
            source_url='https://example.gov/courts/yellowstone/calendar',
        )
        add_court_filing(
            conn,
            case_id=case_id,
            filing_date='2026-03-12',
            filing_title='Information Filed',
            document_number='1',
            source_url='https://example.gov/courts/yellowstone/cases/dv-26-1234',
        )
        slug = conn.execute('SELECT slug FROM court_cases WHERE id = ?', (case_id,)).fetchone()['slug']
        conn.commit()
        conn.close()
        return slug

    def test_courts_route_renders_seeded_court(self) -> None:
        self._seed_case()
        client = app_module.app.test_client()
        response = client.get('/courts')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Montana Court Tracker', html)
        self.assertIn('Yellowstone County District Court', html)

    def test_court_hearings_route_renders_seeded_hearing(self) -> None:
        self._seed_case()
        client = app_module.app.test_client()
        response = client.get('/court-hearings')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Arraignment Hearing', html)
        self.assertIn('DV-26-1234', html)

    def test_court_case_detail_route_renders_seeded_case(self) -> None:
        slug = self._seed_case()
        client = app_module.app.test_client()
        response = client.get(f'/court-case/{slug}')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('State of Montana v. Jane Doe', html)
        self.assertIn('Information Filed', html)

    def test_court_search_route_includes_historical_hearings(self) -> None:
        conn = app_module.get_db()
        source_id = upsert_court_source(
            conn,
            slug='supreme-court-archive',
            name='Montana Supreme Court Previous Oral Arguments',
            source_url='https://courts.mt.gov/courts/supreme/oral_arguments/Previous',
            provider_type='court_calendar',
        )
        court_id = upsert_court(
            conn,
            source_id=source_id,
            slug='montana-supreme-court',
            name='Montana Supreme Court',
            court_type='Supreme Court',
            county='Lewis and Clark',
            city='Helena',
            portal_url='https://courts.mt.gov/courts/supreme/oral_arguments/',
        )
        case_id = upsert_court_case(
            conn,
            court_id=court_id,
            case_number='DA 25-0187',
            caption='Montana Conservation Voters v. Christi Jacobsen',
            status='completed',
            filed_date='2025-01-10',
            case_type='Oral Argument',
            source_url='https://courts.mt.gov/external/orders/caseInfo?id=DA%2025-0187',
        )
        add_court_event(
            conn,
            case_id=case_id,
            event_type='Oral Argument',
            event_date='2026-02-11',
            event_time='09:30',
            event_title='Oral Argument · Montana Supreme Court Courtroom',
            notes='Archived oral argument.',
            source_url='https://courts.mt.gov/external/orders/caseInfo?id=DA%2025-0187',
        )
        conn.commit()
        conn.close()

        client = app_module.app.test_client()
        response = client.get('/court-search?q=DA%2025-0187')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Search court records', html)
        self.assertIn('DA 25-0187', html)

    def test_court_search_route_includes_filing_only_cases(self) -> None:
        conn = app_module.get_db()
        source_id = upsert_court_source(
            conn,
            slug='supreme-court-daily-opinions',
            name='Montana Supreme Court Daily Opinions',
            source_url='https://courts.mt.gov/external/orders/dailyorders',
            provider_type='document_feed',
        )
        court_id = upsert_court(
            conn,
            source_id=source_id,
            slug='montana-supreme-court',
            name='Montana Supreme Court',
            court_type='Supreme Court',
            county='Lewis and Clark',
            city='Helena',
            portal_url='https://courts.mt.gov/external/orders/dailyorders',
        )
        case_id = upsert_court_case(
            conn,
            court_id=court_id,
            case_number='DA 23-0346',
            caption='State v. K. Sandberg',
            status='completed',
            filed_date='2026-03-10',
            case_type='Opinion',
            source_url='https://juddocumentservice.mt.gov/getDocByCTrackId?DocId=560004',
        )
        add_court_filing(
            conn,
            case_id=case_id,
            filing_date='2026-03-10',
            filing_title='Opinion - Published - Justice Gustafson - REVERSED and REMANDED',
            document_number='560004',
            source_url='https://juddocumentservice.mt.gov/getDocByCTrackId?DocId=560004',
        )
        conn.commit()
        conn.close()

        client = app_module.app.test_client()
        response = client.get('/court-search?q=Sandberg')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Search court records', html)
        self.assertIn('State v. K. Sandberg', html)
        self.assertIn('Opinion - Published - Justice Gustafson - REVERSED and REMANDED', html)

    def test_court_admin_context_marks_stale_and_error_sources(self) -> None:
        conn = app_module.get_db()
        healthy_id = upsert_court_source(
            conn,
            slug='healthy-source',
            name='Healthy Source',
            source_url='https://example.gov/healthy',
            provider_type='document_feed',
        )
        stale_id = upsert_court_source(
            conn,
            slug='stale-source',
            name='Stale Source',
            source_url='https://example.gov/stale',
            provider_type='court_calendar',
        )
        error_id = upsert_court_source(
            conn,
            slug='error-source',
            name='Error Source',
            source_url='https://example.gov/error',
            provider_type='court_calendar',
        )
        conn.execute("UPDATE court_sources SET last_success_at = datetime('now'), last_scraped_at = datetime('now') WHERE id = ?", (healthy_id,))
        conn.execute("UPDATE court_sources SET last_success_at = datetime('now', '-2 days'), last_scraped_at = datetime('now', '-2 days') WHERE id = ?", (stale_id,))
        conn.execute(
            "UPDATE court_sources SET last_scraped_at = datetime('now'), last_error = 'network timeout' WHERE id = ?",
            (error_id,),
        )
        conn.commit()

        context = court_admin_context(conn)
        conn.close()

        by_slug = {row['slug']: row for row in context['sources']}
        self.assertEqual(by_slug['healthy-source']['health_state'], 'healthy')
        self.assertEqual(by_slug['stale-source']['health_state'], 'stale')
        self.assertEqual(by_slug['error-source']['health_state'], 'error')
        self.assertEqual(context['health_counts']['healthy'], 1)
        self.assertEqual(context['health_counts']['stale'], 1)
        self.assertEqual(context['health_counts']['error'], 1)


if __name__ == '__main__':
    unittest.main()
