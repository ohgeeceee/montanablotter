import importlib
import os
import sqlite3
import sys
import tempfile
import unittest

import config
import init_db


class SigninWallTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(prefix='mb-signin-wall-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        self._previous_env_signin = os.environ.get('MB_REQUIRE_SIGNIN')
        os.environ['MB_REQUIRE_SIGNIN'] = 'true'

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        sys.modules.pop('app', None)
        self.app_module = importlib.import_module('app')
        self.app_module.app.config['TESTING'] = True
        self.client = self.app_module.app.test_client()
        with self.client.session_transaction() as s:
            s.clear()

        bootstrap_conn = sqlite3.connect(self.db_path)
        bootstrap_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS public_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                display_name TEXT NOT NULL,
                subscriber_plan TEXT DEFAULT 'scout',
                subscription_status TEXT DEFAULT '',
                is_active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        bootstrap_conn.execute(
            "INSERT OR REPLACE INTO public_users (id, email, password_hash, display_name) VALUES (?, ?, ?, ?)",
            (7, 'wall-user@example.com', 'hash', 'Wall User'),
        )
        bootstrap_conn.commit()
        bootstrap_conn.close()

        init_db.init_database()
        init_db.migrate()

        # Toggle the exact config module object that the freshly imported app
        # module references, so changes survive cross-test import side effects.
        self._app_config = self.app_module.config
        self._original_flag = getattr(self._app_config, 'REQUIRE_SIGNIN_WALL', None)
        self._app_config.REQUIRE_SIGNIN_WALL = True
        # Keep the top-level config module in sync for any test assertions.
        config.REQUIRE_SIGNIN_WALL = True

    def tearDown(self):
        if self._previous_env_signin is None:
            os.environ.pop('MB_REQUIRE_SIGNIN', None)
        else:
            os.environ['MB_REQUIRE_SIGNIN'] = self._previous_env_signin
        if self._original_flag is None:
            if hasattr(self._app_config, 'REQUIRE_SIGNIN_WALL'):
                delattr(self._app_config, 'REQUIRE_SIGNIN_WALL')
            if hasattr(config, 'REQUIRE_SIGNIN_WALL'):
                delattr(config, 'REQUIRE_SIGNIN_WALL')
        else:
            self._app_config.REQUIRE_SIGNIN_WALL = self._original_flag
            config.REQUIRE_SIGNIN_WALL = self._original_flag
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        sys.modules.pop('app', None)
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_homepage_is_allowed_anonymously(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)

    def test_content_page_redirects_anonymous_to_login(self):
        # Use a detail-style path that would normally 404; the wall should
        # intercept it first and send the visitor to login.
        response = self.client.get('/wanted/1', follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_auth_routes_are_exempt(self):
        for path in ['/login', '/register']:
            response = self.client.get(path, follow_redirects=False)
            self.assertIn(response.status_code, (200, 302), f'{path} should not be blocked')
            if response.status_code == 302:
                self.assertNotIn('/login', response.headers['Location'])

    def test_static_assets_are_exempt(self):
        response = self.client.get('/static/public-redesign.css', follow_redirects=False)
        # Static file may or may not exist; a 404 is fine, but we must not redirect to login.
        self.assertNotIn('/login', response.headers.get('Location', ''))

    def test_logged_in_user_bypasses_wall(self):
        with self.client.session_transaction() as session_:
            session_['public_user_id'] = 7
        response = self.client.get('/wanted', follow_redirects=False)
        self.assertIn(response.status_code, (200, 302))
        if response.status_code == 302:
            self.assertNotIn('/login', response.headers['Location'])


if __name__ == '__main__':
    unittest.main()
