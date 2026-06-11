"""Newsletter landing page at /newsletter."""
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import app as app_module
import config
import init_db


class NewsletterTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-newsletter-', suffix='.db')
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
        self._seed_posts()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = self.previous_app_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _seed_posts(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.executemany(
            """
            INSERT INTO posts
              (id, blotter_id, title, summary, county, agency_type,
               audit_status, seo_slug, meta_description, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'clean', ?, ?, ?)
            """,
            [
                (1, 0, 'Bozeman traffic stop leads to felony arrest',
                 'A Bozeman PD traffic stop...', 'Gallatin', 'police',
                 'bozeman-traffic-stop', 'A Bozeman PD traffic stop led to a felony arrest.',
                 '2026-06-01 07:00:00'),
                (2, 0, 'Missoula structure fire displaces two families',
                 'A Missoula structure fire...', 'Missoula', 'fire',
                 'missoula-fire', 'Two families were displaced by a Missoula structure fire.',
                 '2026-05-31 09:00:00'),
                (3, 0, 'Yellowstone County fugitive returned from Idaho',
                 'A Yellowstone County fugitive...', 'Yellowstone', 'sheriff',
                 'yellowstone-fugitive', 'A Yellowstone County fugitive was returned from Idaho.',
                 '2026-05-30 14:00:00'),
            ],
        )
        conn.commit()
        conn.close()

    def test_newsletter_renders(self) -> None:
        client = app_module.app.test_client()
        r = client.get('/newsletter')
        html = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn('summarized at 7am MT', html)
        self.assertIn('Overnight blotter digest', html)
        self.assertIn('Top calls', html)
        self.assertIn('Warrant', html)

    def test_newsletter_shows_sample_posts(self) -> None:
        client = app_module.app.test_client()
        r = client.get('/newsletter')
        html = r.get_data(as_text=True)
        self.assertIn('Bozeman traffic stop', html)
        self.assertIn('Missoula structure fire', html)
        self.assertIn('Yellowstone County fugitive', html)
        # Sample post links should resolve
        self.assertIn('/p/bozeman-traffic-stop', html)

    def test_newsletter_has_subscribe_form(self) -> None:
        client = app_module.app.test_client()
        r = client.get('/newsletter')
        html = r.get_data(as_text=True)
        # The form posts to /subscribe (the existing signup endpoint)
        self.assertIn('action="/subscribe"', html)
        self.assertIn('name="email"', html)
        self.assertIn('newsletter_landing', html)  # source attribution

    def test_newsletter_has_transparency_link(self) -> None:
        client = app_module.app.test_client()
        r = client.get('/newsletter')
        html = r.get_data(as_text=True)
        self.assertIn('/transparency', html)
        self.assertIn('How we keep it clean', html)

    def test_newsletter_lists_counties(self) -> None:
        client = app_module.app.test_client()
        r = client.get('/newsletter')
        html = r.get_data(as_text=True)
        # Top counties section should list at least the 3 seeded counties
        self.assertIn('Gallatin', html)
        self.assertIn('Missoula', html)
        self.assertIn('Yellowstone', html)

    def test_subscribe_page_falls_back_without_recaptcha_runtime(self) -> None:
        client = app_module.app.test_client()
        with patch.object(config, 'RECAPTCHA_SITE_KEY', 'test-site-key'), \
             patch.object(config, 'RECAPTCHA_SECRET_KEY', 'test-secret-key'), \
             patch.object(config, 'RECAPTCHA_ENABLED', True):
            r = client.get('/subscribe')
        html = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn("if (!window.grecaptcha", html)
        self.assertIn("form.submit();", html)


if __name__ == '__main__':
    unittest.main()
