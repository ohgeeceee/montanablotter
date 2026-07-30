import sqlite3
import os
import tempfile
import unittest

import app as app_module
import config
import init_db


class AdminDashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-admin-dashboard-', suffix='.db')
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

    def _create_admin_user(self, role: str = 'ops') -> int:
        conn = app_module.get_db()
        cursor = conn.execute(
            """
            INSERT INTO users (username, password, email, role, is_active)
            VALUES (?, ?, ?, ?, ?)
            """,
            (f'dashboard-{role}', 'not-used-in-tests', f'{role}@example.com', role, 1),
        )
        conn.commit()
        conn.close()
        return int(cursor.lastrowid)

    def _login_session(self, client, user_id: int) -> None:
        with client.session_transaction() as session:
            session['_user_id'] = str(user_id)
            session['_fresh'] = True
            session['_csrf_token'] = 'test-csrf-token'

    def _login_admin_session(self, client) -> None:
        self._login_session(client, self.admin_user_id)

    def test_admin_root_redirects_ops_user_to_dashboard(self) -> None:
        """Non-super_admin users land on the operations dashboard."""
        client = app_module.app.test_client()
        self._login_admin_session(client)

        response = client.get('/admin', follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/admin/dashboard')

    def test_admin_root_redirects_super_admin_to_command_center(self) -> None:
        """super_admin users land on the live-ops command center."""
        super_admin_id = self._create_admin_user(role='super_admin')
        client = app_module.app.test_client()
        self._login_session(client, super_admin_id)

        response = client.get('/admin', follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/admin/command-center')

    def test_admin_hub_redirects_to_dashboard(self) -> None:
        """/admin/hub is a backwards-compat alias for /admin/dashboard."""
        client = app_module.app.test_client()
        self._login_admin_session(client)

        response = client.get('/admin/hub', follow_redirects=False)
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.headers['Location'], '/admin/dashboard')

    def test_admin_dashboard_renders_operations_summary(self) -> None:
        client = app_module.app.test_client()
        self._login_admin_session(client)

        response = client.get('/admin/dashboard')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Operations Summary', html)
        self.assertIn('Recent source files', html)
        self.assertIn('County record volume', html)
        self.assertIn('/admin/ingestion', html)
        self.assertIn('/admin/operations/sources', html)
        self.assertIn('/admin/operations/redaction', html)
        self.assertIn('/admin/audience/subscribers', html)
        self.assertIn('/admin/analytics', html)
        self.assertIn('Operations Shortcuts', html)
        self.assertIn('/admin/office/', html)
        self.assertIn('>Office<', html)


if __name__ == '__main__':
    unittest.main()
