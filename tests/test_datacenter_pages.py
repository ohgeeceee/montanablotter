import os
import sqlite3
import tempfile
import unittest

import app as app_module
import config
import init_db
from services.datasets.schema import ensure_dataset_metrics_schema


class DataCenterPagesTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-datacenter-', suffix='.db')
        os.close(fd)
        self._orig_config_db = config.DB_PATH
        self._orig_init_db = init_db.DB_PATH
        self._orig_app_db = app_module.config.DB_PATH

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        app_module.app.config['TESTING'] = True
        bootstrap = sqlite3.connect(self.db_path)
        bootstrap.execute(
            """
            CREATE TABLE IF NOT EXISTS subscribers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                counties TEXT DEFAULT '',
                token TEXT NOT NULL,
                active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        bootstrap.commit()
        bootstrap.close()
        init_db.init_database()
        init_db.migrate()

    def tearDown(self) -> None:
        config.DB_PATH = self._orig_config_db
        init_db.DB_PATH = self._orig_init_db
        app_module.config.DB_PATH = self._orig_app_db
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _create_admin_user(self) -> int:
        conn = app_module.get_db()
        cursor = conn.execute(
            """
            INSERT INTO users (username, password, email, role, is_active)
            VALUES (?, ?, ?, ?, ?)
            """,
            ('datacenter-admin', 'not-used-in-tests', 'datacenter@example.com', 'ops', 1),
        )
        conn.commit()
        conn.close()
        return int(cursor.lastrowid)

    def _login_admin_session(self, client) -> None:
        admin_user_id = self._create_admin_user()
        with client.session_transaction() as session:
            session['_user_id'] = str(admin_user_id)
            session['_fresh'] = True
            session['_csrf_token'] = 'test-csrf-token'

    def _seed_dataset_metrics(self) -> None:
        conn = app_module.get_db()
        ensure_dataset_metrics_schema(conn)
        conn.executemany(
            """
            INSERT INTO dataset_metrics (
                dataset_slug, updated_at,
                window_1d_count, window_7d_count, window_30d_count,
                trend_30d_json, top_categories_json, coverage_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    'jail-bookings',
                    '2026-06-05 12:00:00',
                    12,
                    48,
                    96,
                    '[]',
                    '[]',
                    '[{"label":"Hill","count":12}]',
                ),
                (
                    'warrants',
                    '2026-06-03 12:00:00',
                    4,
                    9,
                    18,
                    '[]',
                    '[]',
                    '[{"label":"Hill","count":4}]',
                ),
                (
                    'arrests',
                    '2026-05-31 12:00:00',
                    3,
                    11,
                    21,
                    '[]',
                    '[]',
                    '[{"label":"Hill","count":3}]',
                ),
                (
                    'public-meetings',
                    '2026-06-05 05:00:00',
                    2,
                    5,
                    8,
                    '[]',
                    '[]',
                    '[{"label":"Hill","count":2}]',
                ),
            ],
        )
        conn.commit()
        conn.close()

    def _seed_police_calls(self) -> None:
        conn = app_module.get_db()
        blotter_cursor = conn.execute(
            """
            INSERT INTO blotters (filename, county, incident_count, source_type, source_document_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            ('police-calls-2026-06-05.pdf', 'Hill', 2, 'incident feed', 'doc-police-calls-1'),
        )
        blotter_id = int(blotter_cursor.lastrowid)
        conn.executemany(
            """
            INSERT INTO records (
                blotter_id, cfs_number, date, time, incident_type, incident,
                location, details, county, officer
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    blotter_id,
                    'CFS-1001',
                    '2026-06-05',
                    '08:15',
                    'Traffic Stop',
                    'Traffic Stop',
                    'US Hwy 87 / 3rd St',
                    'Officer stopped a vehicle for a broken taillight and issued a warning.',
                    'Hill',
                    'Ofc. Smith',
                ),
                (
                    blotter_id,
                    'CFS-1002',
                    '2026-06-05',
                    '09:30',
                    'Disturbance',
                    'Disturbance',
                    '400 block of 2nd Ave',
                    'Multiple callers reported a loud disturbance in the alley behind the building.',
                    'Blaine',
                    'Ofc. Jones',
                ),
            ],
        )
        conn.commit()
        conn.close()

    def test_datacenter_index_renders(self) -> None:
        client = app_module.app.test_client()
        resp = client.get('/datacenter')
        html = resp.get_data(as_text=True)

        self.assertEqual(resp.status_code, 200)
        self.assertIn('Montana Public Data Center', html)
        self.assertIn('Jail Bookings', html)
        self.assertIn('Police Calls', html)

    def test_admin_data_center_requires_login(self) -> None:
        client = app_module.app.test_client()
        resp = client.get('/admin/operations/data-center')

        self.assertEqual(resp.status_code, 302)
        self.assertIn('/admin/login', resp.headers['Location'])

    def test_admin_data_center_renders_for_logged_in_admin(self) -> None:
        client = app_module.app.test_client()
        self._login_admin_session(client)
        self._seed_dataset_metrics()

        resp = client.get('/admin/operations/data-center')
        html = resp.get_data(as_text=True)

        self.assertEqual(resp.status_code, 200)
        self.assertIn('Data Center Operations', html)
        self.assertIn('Fresh datasets', html)
        self.assertIn('Stale datasets', html)
        self.assertIn('Failing feeds', html)
        self.assertIn('Jail Bookings', html)
        self.assertIn('Warrants', html)
        self.assertIn('Police Calls', html)

    def test_dataset_landing_page_renders(self) -> None:
        client = app_module.app.test_client()
        resp = client.get('/datasets/warrants')
        html = resp.get_data(as_text=True)

        self.assertEqual(resp.status_code, 200)
        self.assertIn('Warrants', html)
        self.assertIn('Montana Active Warrant Database', html)
        self.assertIn('Search active warrants posted by Montana sheriff offices.', html)
        self.assertIn('Open Warrant Feed', html)

    def test_jail_bookings_dataset_landing_page_renders(self) -> None:
        client = app_module.app.test_client()
        resp = client.get('/datasets/jail-bookings')
        html = resp.get_data(as_text=True)

        self.assertEqual(resp.status_code, 200)
        self.assertIn('Jail Bookings', html)
        self.assertIn('Daily Booking Monitor', html)
        self.assertIn("Search newly posted jail bookings from Montana county rosters.", html)
        self.assertIn('Open Booking Feed', html)
        self.assertIn('Jail Roster Directory', html)
        self.assertIn('Detention Hub', html)

    def test_arrests_dataset_landing_page_renders(self) -> None:
        client = app_module.app.test_client()
        resp = client.get('/datasets/arrests')
        html = resp.get_data(as_text=True)

        self.assertEqual(resp.status_code, 200)
        self.assertIn('Arrest Log', html)
        self.assertIn('Live Filtered Feed', html)
        self.assertIn("Records where an arrest was made across Montana Blotter's current archive window.", html)
        self.assertIn('Open Arrest Log', html)
        self.assertIn('Jail Booking Feed', html)
        self.assertIn('Police Calls Records', html)

    def test_dataset_records_route_redirects_to_existing_explorer(self) -> None:
        client = app_module.app.test_client()
        resp = client.get('/datasets/arrests/records', follow_redirects=False)

        self.assertIn(resp.status_code, (301, 302))
        self.assertEqual(resp.headers['Location'], '/arrests')

    def test_police_calls_records_shell_renders(self) -> None:
        client = app_module.app.test_client()
        self._seed_police_calls()
        resp = client.get('/datasets/police-calls/records')
        html = resp.get_data(as_text=True)

        self.assertEqual(resp.status_code, 200)
        self.assertIn('Police Calls', html)
        self.assertIn('Live Filtered Feed', html)
        self.assertIn('Traffic Stop', html)
        self.assertIn('CFS-1001', html)
        self.assertIn('Ofc. Smith', html)
        self.assertNotIn('Generic explorer shell', html)

    def test_police_calls_records_filters_and_search(self) -> None:
        client = app_module.app.test_client()
        self._seed_police_calls()

        resp = client.get('/datasets/police-calls/records?q=taillight&county=Hill&type=Traffic+Stop&page=1')
        html = resp.get_data(as_text=True)

        self.assertEqual(resp.status_code, 200)
        self.assertIn('Traffic Stop', html)
        self.assertIn('Hill County', html)
        self.assertIn('broken taillight', html.lower())
        self.assertNotIn('loud disturbance in the alley', html.lower())

    def test_public_nav_exposes_datacenter_link(self) -> None:
        with app_module.app.test_request_context('/'):
            nav = app_module.inject_public_nav()

        self.assertTrue(
            any(item['id'] == 'data_center' for item in nav['public_primary_nav_items']),
            'Expected a Data Center item in the public primary nav',
        )


if __name__ == '__main__':
    unittest.main()
