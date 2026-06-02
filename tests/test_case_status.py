"""Case status free lookup — form rendering, search, and rate limiting."""
import os
import sqlite3
import tempfile
import unittest

import app as app_module
import config
import init_db


class CaseStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-case-status-', suffix='.db')
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
        self._seed()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = self.previous_app_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _seed(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS courts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT, name TEXT, court_type TEXT, county TEXT,
                portal_url TEXT, active INTEGER DEFAULT 1,
                created_at TEXT, updated_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO courts (id, slug, name, court_type, county) "
            "VALUES (1, 'mt-supreme', 'Montana Supreme Court', 'Supreme Court', 'Lewis and Clark')"
        )
        conn.execute(
            """
            INSERT INTO court_cases
              (id, court_id, slug, case_number, caption, status, case_type,
               filed_date, is_criminal, defendant_name, charges_text,
               plea, disposition, sentence_text)
            VALUES
              (1, 1, 'mt-supreme-da-25-0409', 'DA 25-0409',
               'STATE v. JOHN DOE', 'completed', 'Oral Argument',
               '2025-04-15', 1, 'John Doe', 'Partner/Spouse Family Member Assault',
               'Guilty', 'affirmed', 'Deferred imposition, 3 years')
            """
        )
        conn.commit()
        conn.close()

    def test_get_renders_form(self) -> None:
        client = app_module.app.test_client()
        r = client.get('/case-status')
        html = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn('Case Status Lookup', html)
        self.assertIn('All counties', html)
        self.assertIn('Lewis and Clark', html)  # county appears in select

    def test_post_with_match_shows_results(self) -> None:
        client = app_module.app.test_client()
        r = client.post('/case-status', data={'q': 'DA 25-0409'}, follow_redirects=True)
        html = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn('DA 25-0409', html)
        self.assertIn('John Doe', html)
        self.assertIn('affirmed', html)
        self.assertIn('Deferred imposition', html)

    def test_post_short_query_errors(self) -> None:
        client = app_module.app.test_client()
        r = client.post('/case-status', data={'q': 'X'}, follow_redirects=True)
        html = r.get_data(as_text=True)
        self.assertIn('at least 2 characters', html)

    def test_post_no_match(self) -> None:
        client = app_module.app.test_client()
        r = client.post('/case-status', data={'q': 'NOSUCH9999'}, follow_redirects=True)
        html = r.get_data(as_text=True)
        self.assertIn('No matching cases', html)

    def test_search_is_logged(self) -> None:
        client = app_module.app.test_client()
        client.post('/case-status', data={'q': 'DA 25-0409'}, follow_redirects=True)
        conn = sqlite3.connect(self.db_path)
        n = conn.execute('SELECT COUNT(*) FROM case_status_searches').fetchone()[0]
        conn.close()
        self.assertEqual(n, 1)

    def test_rate_limiting_blocks_after_threshold(self) -> None:
        # Pre-fill the rate-limit counter past the threshold
        conn = sqlite3.connect(self.db_path)
        from app import _CASE_STATUS_HOURLY_LIMIT
        for _ in range(_CASE_STATUS_HOURLY_LIMIT):
            conn.execute(
                "INSERT INTO case_status_searches (ip_address, query_text) VALUES (?, ?)",
                ('127.0.0.1', 'fake'),
            )
        conn.commit()
        conn.close()

        client = app_module.app.test_client()
        r = client.post('/case-status', data={'q': 'DA 25-0409'}, follow_redirects=True)
        html = r.get_data(as_text=True)
        self.assertIn('hit the', html)
        # Should NOT show results when rate-limited
        self.assertNotIn('John Doe', html)


if __name__ == '__main__':
    unittest.main()
