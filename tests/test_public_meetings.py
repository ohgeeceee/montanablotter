import os
import tempfile
import unittest

import app as app_module
import config
import init_db
from agendas_scraper.config import CityScrapeConfig
from agendas_scraper.models import AgendaDocument, MeetingRecord
from services.meetings.public import (
    dedupe_stored_meetings,
    _effective_meeting_status,
    _normalized_meeting_title,
    _parse_meeting_start,
    meeting_admin_context,
    review_duplicate_meeting_group,
    sync_scraped_meetings,
)


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
                    starts_at='03/20/2999 06:00 PM',
                    source_url='https://example.gov/agendas',
                    meeting_page_url='https://example.gov/agendas/regular-session',
                    location_name='Billings',
                    body_name='Billings City Council',
                    documents=[
                        AgendaDocument(
                            label='Agenda PDF',
                            url='https://example.gov/agendas/2999-03-20-agenda.pdf',
                            document_type='agenda',
                            target_type='pdf',
                        ),
                        AgendaDocument(
                            label='Minutes PDF',
                            url='https://example.gov/agendas/2999-03-20-minutes.pdf',
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
        self.assertEqual(row['meeting_date'], '2999-03-20')
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

    def test_effective_meeting_status_archives_past_dates(self) -> None:
        self.assertEqual(_effective_meeting_status('2020-01-01', 'upcoming'), 'archived')
        self.assertEqual(_effective_meeting_status('2999-01-01', 'archived'), 'upcoming')
        self.assertEqual(_effective_meeting_status('', 'scheduled'), 'scheduled')

    def test_normalized_meeting_title_upgrades_generic_agenda_title(self) -> None:
        config_obj = CityScrapeConfig(
            slug='fort-benton-city-council',
            name='Fort Benton City Council',
            provider='custom_html',
            url='https://example.gov/city-council',
            metadata={'body_name': 'Fort Benton City Council'},
        )
        meeting = MeetingRecord(
            title='Agenda',
            starts_at='City Council will meet on Monday, June 15th, 2026 at 6:30 PM in a regular session.',
            body_name='Fort Benton City Council',
        )

        self.assertEqual(
            _normalized_meeting_title(meeting, config_obj),
            'Fort Benton City Council Agenda',
        )

    def test_normalized_meeting_title_upgrades_body_name_duplicate(self) -> None:
        config_obj = CityScrapeConfig(
            slug='sample-city-council',
            name='Sample City Council',
            provider='custom_html',
            url='https://example.gov/city-council',
            metadata={'body_name': 'Sample City Council'},
        )
        meeting = MeetingRecord(
            title='Sample City Council',
            starts_at='June 20, 2026',
            body_name='Sample City Council',
        )

        self.assertEqual(
            _normalized_meeting_title(meeting, config_obj),
            'Sample City Council Meeting',
        )

    def test_normalized_meeting_title_prefixes_generic_council_title(self) -> None:
        config_obj = CityScrapeConfig(
            slug='belgrade-city-council',
            name='Belgrade City Council',
            provider='granicus',
            url='https://example.gov/city-council',
            metadata={'body_name': 'Belgrade City Council'},
        )
        meeting = MeetingRecord(
            title='City Council Workshop Agenda',
            starts_at='June 8, 2026',
            body_name='Belgrade City Council',
        )

        self.assertEqual(
            _normalized_meeting_title(meeting, config_obj),
            'Belgrade City Council Workshop',
        )

    def test_normalized_meeting_title_strips_packet_pdf_suffix(self) -> None:
        config_obj = CityScrapeConfig(
            slug='whitefish-city-council',
            name='Whitefish Mayor and City Council',
            provider='granicus',
            url='https://example.gov/city-council',
            metadata={'body_name': 'Whitefish Mayor and City Council'},
        )
        meeting = MeetingRecord(
            title='Mayor and City Council FY27 Preliminary Budget Work Session Packet (PDF)',
            starts_at='June 8, 2026',
            body_name='Whitefish Mayor and City Council',
        )

        self.assertEqual(
            _normalized_meeting_title(meeting, config_obj),
            'Whitefish Mayor and City Council FY27 Preliminary Budget Work Session',
        )

    def test_normalized_meeting_title_rewrites_agenda_prefix_with_subject(self) -> None:
        config_obj = CityScrapeConfig(
            slug='columbia-falls-city-council',
            name='Columbia Falls City Council',
            provider='granicus',
            url='https://example.gov/city-council',
            metadata={'body_name': 'Columbia Falls City Council'},
        )
        meeting = MeetingRecord(
            title='Council Agenda - MLUPA Land Use Plan, Future Land Use Map',
            starts_at='June 8, 2026',
            body_name='Columbia Falls City Council',
        )

        self.assertEqual(
            _normalized_meeting_title(meeting, config_obj),
            'Columbia Falls City Council Agenda: MLUPA Land Use Plan, Future Land Use Map',
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
        self.assertIn('https://montanablotter.com/meetings', html)

    def test_meetings_route_excludes_past_rows_marked_upcoming(self) -> None:
        self._seed_meetings()
        conn = app_module.get_db()
        conn.execute(
            """
            UPDATE public_meetings
            SET meeting_date = '2020-01-01',
                status = 'upcoming'
            WHERE title = 'Regular Session'
            """
        )
        conn.commit()
        conn.close()

        client = app_module.app.test_client()
        response = client.get('/meetings')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Regular Session', html)
        self.assertIn('No upcoming meetings matched those filters.', html)

    def test_legacy_public_meetings_route_redirects(self) -> None:
        client = app_module.app.test_client()
        response = client.get('/public-meetings')

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.headers['Location'], '/meetings')

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

    def test_meeting_admin_context_uses_effective_upcoming_status(self) -> None:
        self._seed_meetings()
        conn = app_module.get_db()
        conn.execute(
            """
            UPDATE public_meetings
            SET meeting_date = '2020-01-01',
                status = 'upcoming'
            WHERE title = 'Regular Session'
            """
        )
        conn.commit()

        context = meeting_admin_context(conn)
        conn.close()

        by_slug = {row['slug']: row for row in context['sources']}
        self.assertEqual(by_slug['billings-city-council']['current_meeting_count'], 1)
        self.assertEqual(by_slug['billings-city-council']['upcoming_meeting_count'], 0)

    def test_meeting_admin_context_surfaces_same_slot_review_groups(self) -> None:
        conn = app_module.get_db()
        sync_scraped_meetings(
            conn,
            CityScrapeConfig(
                slug='great-falls-city-commission',
                name='Great Falls City Commission',
                provider='custom_html',
                url='https://example.gov/commission',
                metadata={
                    'meeting_scope': 'city',
                    'location_slug': 'great-falls',
                    'location_name': 'Great Falls',
                    'county_name': 'Cascade',
                    'city_name': 'Great Falls',
                    'body_name': 'Great Falls City Commission',
                },
            ),
            [
                MeetingRecord(
                    title='Special City Commission Meeting, March 17, 2026',
                    starts_at='March 17, 2026',
                    source_url='https://example.gov/commission',
                    body_name='Great Falls City Commission',
                    documents=[
                        AgendaDocument(
                            label='Agenda PDF',
                            url='https://example.gov/commission/special-agenda.pdf',
                            document_type='agenda',
                            target_type='pdf',
                        )
                    ],
                ),
                MeetingRecord(
                    title='Great Falls City Commission Meeting, March 17, 2026',
                    starts_at='March 17, 2026',
                    source_url='https://example.gov/commission',
                    body_name='Great Falls City Commission',
                    documents=[
                        AgendaDocument(
                            label='Agenda PDF',
                            url='https://example.gov/commission/regular-agenda.pdf',
                            document_type='agenda',
                            target_type='pdf',
                        )
                    ],
                ),
            ],
            config_path='configs/agendas/montana_live.json',
        )
        conn.commit()

        context = meeting_admin_context(conn)
        conn.close()

        self.assertEqual(len(context['duplicate_review_groups']), 1)
        group = context['duplicate_review_groups'][0]
        self.assertEqual(group['source_slug'], 'great-falls-city-commission')
        self.assertEqual(group['meeting_date'], '2026-03-17')
        self.assertEqual(group['meeting_count'], 2)
        self.assertEqual(len(group['meetings']), 2)
        self.assertEqual(group['meetings'][0]['document_count'], 1)

    def test_meeting_admin_context_hides_keep_both_reviewed_group(self) -> None:
        conn = app_module.get_db()
        sync_scraped_meetings(
            conn,
            CityScrapeConfig(
                slug='great-falls-city-commission',
                name='Great Falls City Commission',
                provider='custom_html',
                url='https://example.gov/commission',
                metadata={
                    'meeting_scope': 'city',
                    'location_slug': 'great-falls',
                    'location_name': 'Great Falls',
                    'county_name': 'Cascade',
                    'city_name': 'Great Falls',
                    'body_name': 'Great Falls City Commission',
                },
            ),
            [
                MeetingRecord(
                    title='Special City Commission Meeting, March 17, 2026',
                    starts_at='March 17, 2026',
                    source_url='https://example.gov/commission',
                    body_name='Great Falls City Commission',
                ),
                MeetingRecord(
                    title='Great Falls City Commission Meeting, March 17, 2026',
                    starts_at='March 17, 2026',
                    source_url='https://example.gov/commission',
                    body_name='Great Falls City Commission',
                ),
            ],
            config_path='configs/agendas/montana_live.json',
        )
        conn.commit()

        ids = [
            row['id']
            for row in conn.execute(
                """
                SELECT id
                FROM public_meetings
                WHERE body_name = 'Great Falls City Commission'
                ORDER BY id
                """
            ).fetchall()
        ]
        review_duplicate_meeting_group(
            conn,
            source_slug='great-falls-city-commission',
            meeting_date='2026-03-17',
            meeting_time='',
            meeting_ids=ids,
            action='keep_both',
            decided_by_user_id=7,
        )
        conn.commit()

        context = meeting_admin_context(conn)
        conn.close()

        self.assertEqual(context['duplicate_review_groups'], [])

    def test_sync_scraped_meetings_merges_exact_same_slot_duplicates(self) -> None:
        conn = app_module.get_db()
        sync_scraped_meetings(
            conn,
            CityScrapeConfig(
                slug='fort-benton-city-council',
                name='Fort Benton City Council',
                provider='custom_html',
                url='https://example.gov/city-council',
                metadata={
                    'meeting_scope': 'city',
                    'location_slug': 'fort-benton',
                    'location_name': 'Fort Benton',
                    'county_name': 'Chouteau',
                    'city_name': 'Fort Benton',
                    'body_name': 'Fort Benton City Council',
                },
            ),
            [
                MeetingRecord(
                    title='Agenda',
                    starts_at='City Council will meet on Monday, June 15th, 2026 at 6:30 PM in a regular session.',
                    source_url='https://example.gov/city-council',
                    body_name='Fort Benton City Council',
                ),
                MeetingRecord(
                    title='Agenda',
                    starts_at='City Council will meet on Monday, June 15th, 2026 at 6:30 PM in a regular session.',
                    source_url='https://example.gov/city-council',
                    body_name='Fort Benton City Council',
                ),
            ],
            config_path='configs/agendas/montana_live.json',
        )
        conn.commit()

        count = conn.execute(
            "SELECT COUNT(*) AS cnt FROM public_meetings WHERE body_name = 'Fort Benton City Council'"
        ).fetchone()['cnt']
        conn.close()

        self.assertEqual(count, 1)

    def test_sync_scraped_meetings_merges_notice_row_into_documented_row(self) -> None:
        conn = app_module.get_db()
        sync_scraped_meetings(
            conn,
            CityScrapeConfig(
                slug='choteau-city-council',
                name='Choteau City Council',
                provider='custom_html',
                url='https://example.gov/agendas',
                metadata={
                    'meeting_scope': 'city',
                    'location_slug': 'choteau',
                    'location_name': 'Choteau',
                    'county_name': 'Teton',
                    'city_name': 'Choteau',
                    'body_name': 'Choteau City Council',
                },
            ),
            [
                MeetingRecord(
                    title='January 13, 2026 - Work Session',
                    starts_at='January 13, 2026 - Work Session Agenda',
                    source_url='https://example.gov/agendas',
                    body_name='Choteau City Council',
                ),
                MeetingRecord(
                    title='January 13, 2026 - Work Session Agenda',
                    starts_at='January 13, 2026 - Work Session Agenda',
                    source_url='https://example.gov/agendas',
                    body_name='Choteau City Council',
                    documents=[
                        AgendaDocument(
                            label='Agenda PDF',
                            url='https://example.gov/agendas/2026-01-13-work-session.pdf',
                            document_type='agenda',
                            target_type='pdf',
                        )
                    ],
                ),
            ],
            config_path='configs/agendas/montana_live.json',
        )
        conn.commit()

        row = conn.execute(
            """
            SELECT public_meetings.title, COUNT(meeting_documents.id) AS doc_count
            FROM public_meetings
            LEFT JOIN meeting_documents ON meeting_documents.meeting_id = public_meetings.id
            WHERE public_meetings.body_name = 'Choteau City Council'
            GROUP BY public_meetings.id
            """
        ).fetchone()
        conn.close()

        self.assertEqual(row['title'], 'January 13, 2026 - Work Session')
        self.assertEqual(row['doc_count'], 1)

    def test_sync_scraped_meetings_merges_same_slot_rows_with_shared_document_url(self) -> None:
        conn = app_module.get_db()
        sync_scraped_meetings(
            conn,
            CityScrapeConfig(
                slug='columbia-falls-city-council',
                name='Columbia Falls City Council',
                provider='granicus',
                url='https://example.gov/agendas',
                metadata={
                    'meeting_scope': 'city',
                    'location_slug': 'columbia-falls',
                    'location_name': 'Columbia Falls',
                    'county_name': 'Flathead',
                    'city_name': 'Columbia Falls',
                    'body_name': 'Columbia Falls City Council',
                },
            ),
            [
                MeetingRecord(
                    title='Council Agenda - Teakettle Heights Public Hearing',
                    starts_at='Jun 1, 2026 — Posted May 27, 2026 6:27 PM',
                    source_url='https://example.gov/agendas',
                    body_name='Columbia Falls City Council',
                    documents=[
                        AgendaDocument(
                            label='Agenda PDF',
                            url='https://example.gov/agendas/06012026.pdf',
                            document_type='agenda',
                            target_type='pdf',
                        )
                    ],
                ),
                MeetingRecord(
                    title='Council Agenda - *PRELIMINARY PACKET* Teakettle Heights Public Hearing',
                    starts_at='Jun 1, 2026 — Amended May 29, 2026 5:48 PM',
                    source_url='https://example.gov/agendas',
                    body_name='Columbia Falls City Council',
                    documents=[
                        AgendaDocument(
                            label='Agenda PDF',
                            url='https://example.gov/agendas/06012026.pdf',
                            document_type='agenda',
                            target_type='pdf',
                        ),
                        AgendaDocument(
                            label='Packet PDF',
                            url='https://example.gov/agendas/06012026.pdf',
                            document_type='packet',
                            target_type='pdf',
                        )
                    ],
                ),
            ],
            config_path='configs/agendas/montana_live.json',
        )
        conn.commit()

        row = conn.execute(
            """
            SELECT public_meetings.title, COUNT(meeting_documents.id) AS doc_count
            FROM public_meetings
            LEFT JOIN meeting_documents ON meeting_documents.meeting_id = public_meetings.id
            WHERE public_meetings.body_name = 'Columbia Falls City Council'
            GROUP BY public_meetings.id
            """
        ).fetchone()
        conn.close()

        self.assertEqual(row['doc_count'], 2)

    def test_dedupe_stored_meetings_removes_historical_duplicates_and_migrates_docs(self) -> None:
        conn = app_module.get_db()
        sync_scraped_meetings(
            conn,
            CityScrapeConfig(
                slug='choteau-city-council',
                name='Choteau City Council',
                provider='custom_html',
                url='https://example.gov/agendas',
                metadata={
                    'meeting_scope': 'city',
                    'location_slug': 'choteau',
                    'location_name': 'Choteau',
                    'county_name': 'Teton',
                    'city_name': 'Choteau',
                    'body_name': 'Choteau City Council',
                },
            ),
            [
                MeetingRecord(
                    title='January 13, 2026 - Work Session',
                    starts_at='January 13, 2026 - Work Session Agenda',
                    source_url='https://example.gov/agendas',
                    body_name='Choteau City Council',
                ),
                MeetingRecord(
                    title='January 13, 2026 - Work Session Agenda',
                    starts_at='January 13, 2026 - Work Session Agenda',
                    source_url='https://example.gov/agendas',
                    body_name='Choteau City Council',
                    documents=[
                        AgendaDocument(
                            label='Agenda PDF',
                            url='https://example.gov/agendas/2026-01-13-work-session.pdf',
                            document_type='agenda',
                            target_type='pdf',
                        )
                    ],
                ),
            ],
            config_path='configs/agendas/montana_live.json',
        )
        conn.commit()

        dup_row = conn.execute(
            """
            SELECT source_id, location_id, external_key, title, body_name, meeting_scope, starts_at_raw,
                   meeting_date, meeting_time, status, source_url, meeting_page_url, location_name,
                   is_current, last_seen_at, created_at, updated_at
            FROM public_meetings
            WHERE body_name = 'Choteau City Council'
            LIMIT 1
            """
        ).fetchone()
        conn.execute(
            """
            INSERT INTO public_meetings (
                source_id, location_id, external_key, title, body_name, meeting_scope, starts_at_raw,
                meeting_date, meeting_time, status, source_url, meeting_page_url, location_name,
                is_current, last_seen_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dup_row['source_id'],
                dup_row['location_id'],
                f"{dup_row['external_key']}-dupe",
                'January 13, 2026 - Work Session Agenda',
                dup_row['body_name'],
                dup_row['meeting_scope'],
                dup_row['starts_at_raw'],
                dup_row['meeting_date'],
                dup_row['meeting_time'],
                dup_row['status'],
                dup_row['source_url'],
                dup_row['meeting_page_url'],
                dup_row['location_name'],
                dup_row['is_current'],
                dup_row['last_seen_at'],
                dup_row['created_at'],
                dup_row['updated_at'],
            ),
        )
        dup_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        conn.execute(
            """
            INSERT INTO meeting_documents (meeting_id, label, document_type, url, target_type)
            VALUES (?, 'Agenda PDF', 'agenda', 'https://example.gov/agendas/2026-01-13-work-session.pdf', 'pdf')
            """,
            (dup_id,),
        )
        conn.commit()

        result = dedupe_stored_meetings(conn, source_slug='choteau-city-council')
        conn.commit()

        row = conn.execute(
            """
            SELECT public_meetings.title, COUNT(meeting_documents.id) AS doc_count
            FROM public_meetings
            LEFT JOIN meeting_documents ON meeting_documents.meeting_id = public_meetings.id
            WHERE public_meetings.body_name = 'Choteau City Council'
            GROUP BY public_meetings.id
            """
        ).fetchone()
        remaining = conn.execute(
            "SELECT COUNT(*) AS cnt FROM public_meetings WHERE body_name = 'Choteau City Council'"
        ).fetchone()['cnt']
        conn.close()

        self.assertEqual(result['deleted'], 1)
        self.assertEqual(remaining, 1)
        self.assertEqual(row['doc_count'], 1)

    def test_review_duplicate_meeting_group_merges_selected_pair_and_moves_docs(self) -> None:
        conn = app_module.get_db()
        sync_scraped_meetings(
            conn,
            CityScrapeConfig(
                slug='great-falls-city-commission',
                name='Great Falls City Commission',
                provider='custom_html',
                url='https://example.gov/commission',
                metadata={
                    'meeting_scope': 'city',
                    'location_slug': 'great-falls',
                    'location_name': 'Great Falls',
                    'county_name': 'Cascade',
                    'city_name': 'Great Falls',
                    'body_name': 'Great Falls City Commission',
                },
            ),
            [
                MeetingRecord(
                    title='Special City Commission Meeting, March 17, 2026',
                    starts_at='March 17, 2026',
                    source_url='https://example.gov/commission',
                    body_name='Great Falls City Commission',
                    documents=[
                        AgendaDocument(
                            label='Agenda PDF',
                            url='https://example.gov/commission/special-agenda.pdf',
                            document_type='agenda',
                            target_type='pdf',
                        )
                    ],
                ),
                MeetingRecord(
                    title='Great Falls City Commission Meeting, March 17, 2026',
                    starts_at='March 17, 2026',
                    source_url='https://example.gov/commission',
                    body_name='Great Falls City Commission',
                    documents=[
                        AgendaDocument(
                            label='Minutes PDF',
                            url='https://example.gov/commission/regular-minutes.pdf',
                            document_type='minutes',
                            target_type='pdf',
                        )
                    ],
                ),
            ],
            config_path='configs/agendas/montana_live.json',
        )
        conn.commit()

        rows = conn.execute(
            """
            SELECT id
            FROM public_meetings
            WHERE body_name = 'Great Falls City Commission'
            ORDER BY id
            """
        ).fetchall()
        keeper_id = rows[0]['id']
        duplicate_id = rows[1]['id']

        result = review_duplicate_meeting_group(
            conn,
            source_slug='great-falls-city-commission',
            meeting_date='2026-03-17',
            meeting_time='',
            meeting_ids=[keeper_id, duplicate_id],
            action='merge',
            keeper_meeting_id=keeper_id,
            duplicate_meeting_id=duplicate_id,
            decided_by_user_id=9,
        )
        conn.commit()

        remaining = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM public_meetings
            WHERE body_name = 'Great Falls City Commission'
            """
        ).fetchone()['cnt']
        doc_count = conn.execute(
            'SELECT COUNT(*) AS cnt FROM meeting_documents WHERE meeting_id = ?',
            (keeper_id,),
        ).fetchone()['cnt']
        conn.close()

        self.assertEqual(result['deleted'], 1)
        self.assertEqual(result['migrated_docs'], 1)
        self.assertEqual(remaining, 1)
        self.assertEqual(doc_count, 2)

    def test_dedupe_stored_meetings_merges_livingston_budget_workshop_into_work_session(self) -> None:
        conn = app_module.get_db()
        sync_scraped_meetings(
            conn,
            CityScrapeConfig(
                slug='livingston-city-commission',
                name='Livingston City Commission',
                provider='custom_html',
                url='https://www.livingstonmontana.org/meetings?field_microsite_tid_1=27',
                metadata={
                    'meeting_scope': 'city',
                    'location_slug': 'livingston',
                    'location_name': 'Livingston',
                    'county_name': 'Park',
                    'city_name': 'Livingston',
                    'body_name': 'Livingston City Commission',
                },
            ),
            [
                MeetingRecord(
                    title='City Commission Budget Workshop',
                    starts_at='04/27/2026 - 1:00pm',
                    source_url='https://www.livingstonmontana.org/meetings?field_microsite_tid_1=27',
                    body_name='Livingston City Commission',
                ),
                MeetingRecord(
                    title='Livingston City Commission Work Session',
                    starts_at='04/27/2026 - 1:00pm',
                    source_url='https://www.livingstonmontana.org/meetings?field_microsite_tid_1=27',
                    body_name='Livingston City Commission',
                    documents=[
                        AgendaDocument(
                            label='Agenda PDF',
                            url='https://example.gov/agendas/livingston-work-session.pdf',
                            document_type='agenda',
                            target_type='pdf',
                        )
                    ],
                ),
            ],
            config_path='configs/agendas/montana_live.json',
        )
        conn.commit()

        result = dedupe_stored_meetings(conn, source_slug='livingston-city-commission')
        conn.commit()

        rows = conn.execute(
            """
            SELECT public_meetings.title, COUNT(meeting_documents.id) AS doc_count
            FROM public_meetings
            LEFT JOIN meeting_documents ON meeting_documents.meeting_id = public_meetings.id
            WHERE public_meetings.body_name = 'Livingston City Commission'
            GROUP BY public_meetings.id
            """
        ).fetchall()
        conn.close()

        self.assertEqual(result['deleted'], 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['title'], 'Livingston City Commission Work Session')
        self.assertEqual(rows[0]['doc_count'], 1)


if __name__ == '__main__':
    unittest.main()
