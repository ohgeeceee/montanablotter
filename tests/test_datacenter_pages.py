import os
import sqlite3
import tempfile
import unittest

import app as app_module
import config
import init_db


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

    def test_datacenter_index_renders(self) -> None:
        client = app_module.app.test_client()
        resp = client.get('/datacenter')
        html = resp.get_data(as_text=True)

        self.assertEqual(resp.status_code, 200)
        self.assertIn('Montana Public Data Center', html)
        self.assertIn('Jail Bookings', html)
        self.assertIn('Police Calls', html)

    def test_dataset_landing_page_renders(self) -> None:
        client = app_module.app.test_client()
        resp = client.get('/datasets/warrants')
        html = resp.get_data(as_text=True)

        self.assertEqual(resp.status_code, 200)
        self.assertIn('Warrants', html)
        self.assertIn('records', html)

    def test_dataset_records_route_redirects_to_existing_explorer(self) -> None:
        client = app_module.app.test_client()
        resp = client.get('/datasets/arrests/records', follow_redirects=False)

        self.assertIn(resp.status_code, (301, 302))
        self.assertEqual(resp.headers['Location'], '/arrests')

    def test_police_calls_records_shell_renders(self) -> None:
        client = app_module.app.test_client()
        resp = client.get('/datasets/police-calls/records')
        html = resp.get_data(as_text=True)

        self.assertEqual(resp.status_code, 200)
        self.assertIn('Police Calls', html)
        self.assertIn('Generic explorer shell', html)

    def test_public_nav_exposes_datacenter_link(self) -> None:
        with app_module.app.test_request_context('/'):
            nav = app_module.inject_public_nav()

        self.assertTrue(
            any(item['id'] == 'data_center' for item in nav['public_primary_nav_items']),
            'Expected a Data Center item in the public primary nav',
        )


if __name__ == '__main__':
    unittest.main()
