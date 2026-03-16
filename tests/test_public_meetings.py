import os
import tempfile
import unittest

import app as app_module
import config
import init_db
from agendas_scraper.config import CityScrapeConfig
from agendas_scraper.models import AgendaDocument, MeetingRecord
from public_meetings import _parse_meeting_start, meeting_admin_context, sync_scraped_meetings


class PublicMeetingsTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-meetings-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        self.previous_agendas_host = getattr(config, 'AGENDAS_HOST', '')
        self.previous_agendas_base_url = getattr(config, 'AGENDAS_BASE_URL', '')

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        config.AGENDAS_HOST = 'agendas.montanablotter.com'
        config.AGENDAS_BASE_URL = 'https://agendas.montanablotter.com'
        app_module.config.DB_PATH = self.db_path
        app_module.config.AGENDAS_HOST = config.AGENDAS_HOST
        app_module.config.AGENDAS_BASE_URL = config.AGENDAS_BASE_URL
        app_module.app.config['TESTING'] = True

        init_db.migrate()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        config.AGENDAS_HOST = self.previous_agendas_host
        config.AGENDAS_BASE_URL = self.previous_agendas_base_url
        app_module.config.DB_PATH = config.DB_PATH
        app_module.config.AGENDAS_HOST = config.AGENDAS_HOST
        app_module.config.AGENDAS_BASE_URL = config.AGENDAS_BASE_URL
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _seed_meetings(self) -> None:
        conn = app_module.get_db()
        summary = sync_scraped_meetings(
            conn,
            CityScrapeConfig(
                slug='billings-city-council',
                name='Billings City Council',
                provider='granicus',
                url='https://example.gov/agendas',
                metadata={
                    'meeting_scope': 'city',
                    'location_slug': 'billings',
                    'location_name': 'Billings',
                    'county_name': 'Yellowstone',
                    'city_name': 'Billings',
                },
            ),
            [
                MeetingRecord(
                    title='Regular Session',
                    starts_at='03/20/2026 06:00 PM',
                    source_url='https://example.gov/agendas',
                    meeting_page_url='https://example.gov/agendas/regular-session',
                    location_name='Billings',
                    body_name='Billings City Council',
                    documents=[
                        AgendaDocument(
                            label='Agenda PDF',
                            url='https://example.gov/agendas/2026-03-20-agenda.pdf',
                            document_type='agenda',
                            target_type='pdf',
                        ),
                        AgendaDocument(
                            label='Minutes PDF',
                            url='https://example.gov/agendas/2026-03-20-minutes.pdf',
                            document_type='minutes',
                            target_type='pdf',
                        ),
                    ],
                )
            ],
            config_path='configs/agendas/cities.example.json',
        )
        conn.commit()
        conn.close()
        self.assertEqual(summary['created'], 1)

    def test_sync_scraped_meetings_persists_documents(self) -> None:
        self._seed_meetings()
        conn = app_module.get_db()
        row = conn.execute(
            '''
            SELECT public_meetings.title, public_meetings.meeting_date, meeting_locations.county_name
            FROM public_meetings
            JOIN meeting_locations ON meeting_locations.id = public_meetings.location_id
            '''
        ).fetchone()
        docs = conn.execute('SELECT COUNT(*) AS cnt FROM meeting_documents').fetchone()['cnt']
        conn.close()

        self.assertEqual(row['title'], 'Regular Session')
        self.assertEqual(row['meeting_date'], '2026-03-20')
        self.assertEqual(row['county_name'], 'Yellowstone')
        self.assertEqual(docs, 2)

    def test_parse_meeting_start_extracts_date_from_title_text(self) -> None:
        self.assertEqual(
            _parse_meeting_start('City Council Agenda | December 11, 2025'),
            ('2025-12-11', ''),
        )

    def test_parse_meeting_start_handles_ordinals_and_time_in_sentence(self) -> None:
        self.assertEqual(
            _parse_meeting_start('City Council will meet on Monday, March 16th, 2026 at 6:30 PM in a regular session'),
            ('2026-03-16', '18:30'),
        )

    def test_meetings_route_renders_seeded_rows(self) -> None:
        self._seed_meetings()
        client = app_module.app.test_client()
        response = client.get('/meetings')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Public Meetings', html)
        self.assertIn('Regular Session', html)
        self.assertIn('Agenda PDF', html)

    def test_agendas_host_uses_dashboard_on_root(self) -> None:
        self._seed_meetings()
        client = app_module.app.test_client()
        response = client.get('/', headers={'Host': 'agendas.montanablotter.com'})
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Public Meetings', html)
        self.assertIn('Billings City Council', html)
        self.assertIn('Read sourcing standards', html)

    def test_standards_page_renders(self) -> None:
        client = app_module.app.test_client()
        response = client.get('/standards')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Sourcing Standards', html)
        self.assertIn('Primary Source Rule', html)

    def test_corrections_page_renders(self) -> None:
        client = app_module.app.test_client()
        response = client.get('/corrections')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Corrections Policy', html)
        self.assertIn('Correction Request', html)

    def test_meeting_admin_context_marks_stale_and_error_sources(self) -> None:
        self._seed_meetings()
        conn = app_module.get_db()
        conn.execute(
            """
            INSERT INTO meeting_sources (
                slug, name, provider_type, source_url, meeting_scope, is_active, last_success_at, last_error
            ) VALUES
                ('stale-source', 'Stale Source', 'custom_html', 'https://example.gov/stale', 'city', 1, datetime('now', '-4 days'), NULL),
                ('error-source', 'Error Source', 'custom_html', 'https://example.gov/error', 'city', 1, datetime('now'), 'HTTP 500')
            """
        )
        conn.commit()

        context = meeting_admin_context(conn)
        conn.close()

        by_slug = {row['slug']: row for row in context['sources']}
        self.assertEqual(by_slug['billings-city-council']['health_state'], 'healthy')
        self.assertEqual(by_slug['stale-source']['health_state'], 'stale')
        self.assertEqual(by_slug['error-source']['health_state'], 'error')
        self.assertEqual(context['health_counts']['healthy'], 1)
        self.assertEqual(context['health_counts']['stale'], 1)
        self.assertEqual(context['health_counts']['error'], 1)


if __name__ == '__main__':
    unittest.main()
