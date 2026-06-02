"""Admin social shares — log view + per-post view."""
import os
import sqlite3
import tempfile
import unittest

import app as app_module
import config
import init_db


class AdminSocialSharesTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-social-shares-', suffix='.db')
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
        self._seed_log()

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
            ('shares-admin', 'not-used', 'shares@example.com', 'ops', 1),
        )
        conn.commit()
        conn.close()
        return int(cursor.lastrowid)

    def _seed_log(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO posts (id, blotter_id, title, seo_slug) "
            "VALUES (1, 0, 'Test Post Title', 'test-post')"
        )
        conn.executemany(
            """
            INSERT INTO social_share_log
              (post_id, platform, target_url, post_url, status, response_code, triggered_by)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, 'facebook', 'https://fb.com/test', 'https://example.com/p/1', 'success', 200, 'auto'),
                (1, 'reddit',   'https://reddit.com/r/mt', 'https://example.com/p/1', 'failed', 500, 'auto'),
                (1, 'manual', '', '', 'success', 200, 'admin'),
            ],
        )
        conn.commit()
        conn.close()

    def _login_admin_session(self, client) -> None:
        with client.session_transaction() as session:
            session['_user_id'] = str(self.admin_user_id)
            session['_fresh'] = True
            session['_csrf_token'] = 'test-csrf-token'

    def test_list_page_renders(self) -> None:
        client = app_module.app.test_client()
        self._login_admin_session(client)

        response = client.get('/admin/social-shares')
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Social Share Log', html)
        self.assertIn('facebook', html)
        self.assertIn('reddit', html)

    def test_list_filters_by_platform(self) -> None:
        client = app_module.app.test_client()
        self._login_admin_session(client)

        response = client.get('/admin/social-shares?platform=facebook')
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        # The filter select should reflect the active filter
        self.assertIn('value="facebook" selected', html)
        # The table body should only show the facebook row, not the reddit one
        # (reddit row would render in the tbody with `<span ...>reddit</span>`)
        self.assertIn('>facebook</span>', html)
        self.assertNotIn('>reddit</span>', html)

    def test_per_post_view(self) -> None:
        client = app_module.app.test_client()
        self._login_admin_session(client)

        response = client.get('/admin/social-shares/post/1')
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Shares for post #1', html)
        self.assertIn('Test Post Title', html)
        self.assertIn('facebook', html)

    def test_unauthenticated_redirects(self) -> None:
        client = app_module.app.test_client()
        response = client.get('/admin/social-shares', follow_redirects=False)
        self.assertIn(response.status_code, (302, 303))


if __name__ == '__main__':
    unittest.main()
