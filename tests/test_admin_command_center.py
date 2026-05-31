import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import app as app_module
import config
import init_db


class AdminCommandCenterTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-admin-command-center-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        self.previous_app_db_path = app_module.config.DB_PATH

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        app_module.app.config['TESTING'] = True

        bootstrap_conn = sqlite3.connect(self.db_path)
        bootstrap_conn.execute(
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
        bootstrap_conn.commit()
        bootstrap_conn.close()

        init_db.init_database()
        init_db.migrate()
        self.admin_user_id = self._create_admin_user()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = self.previous_app_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _create_admin_user(self) -> int:
        conn = app_module.get_db()
        cursor = conn.execute(
            """
            INSERT INTO users (username, password, email, role, is_active)
            VALUES (?, ?, ?, ?, ?)
            """,
            ('cc-admin', 'not-used-in-tests', 'cc@example.com', 'ops', 1),
        )
        conn.commit()
        conn.close()
        return int(cursor.lastrowid)

    def _login(self, client) -> None:
        with client.session_transaction() as session:
            session['_user_id'] = str(self.admin_user_id)
            session['_fresh'] = True
            session['_csrf_token'] = 'test-csrf-token'

    def test_command_center_page_renders_slim_sidebar(self) -> None:
        client = app_module.app.test_client()
        self._login(client)

        response = client.get('/admin/command-center')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Live Ops', html)
        self.assertIn('Browse All Admin Tools', html)
        self.assertIn('All Admin Tools', html)
        self.assertIn('ops-health-bar', html)
        self.assertNotIn('Mission Control', html)
        self.assertNotIn('/admin/mission-control', html)

    def test_command_center_feed_includes_alerts_and_coverage(self) -> None:
        client = app_module.app.test_client()
        self._login(client)

        with mock.patch(
            'blueprints.admin.command_center.build_snapshot',
            return_value={'agents': []},
        ), mock.patch(
            'blueprints.admin.command_center.recent_events',
            return_value=[],
        ), mock.patch(
            'blueprints.admin.command_center._agent_snapshot',
            return_value={'agents': {}},
        ), mock.patch(
            'blueprints.admin.command_center.system_snapshot',
            return_value={'services': [], 'queues': [], 'alerts': []},
        ), mock.patch(
            'blueprints.admin.command_center._pipeline_jobs',
            return_value=[],
        ), mock.patch(
            'blueprints.admin.command_center._stats',
            return_value={
                'total_records': 10,
                'total_blotters': 2,
                'today_records': 1,
                'failed_24h': 0,
                'total_counties': 3,
                'alert_rollup': {'ingestion': 1, 'courts': 0, 'meetings': 2, 'total': 3},
                'source_coverage': {
                    'summary': {'live': 4, 'covered': 5, 'no_source': 1},
                    'entries': [{'agency': 'Gallatin County', 'freshness': '2h ago'}],
                },
            },
        ):
            response = client.get('/admin/api/command-center/feed')

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['stats']['alert_rollup']['total'], 3)
        self.assertEqual(payload['stats']['source_coverage']['summary']['live'], 4)


if __name__ == '__main__':
    unittest.main()
