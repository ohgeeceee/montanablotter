import sqlite3
import os
import tempfile
import unittest
from unittest.mock import patch

import app as app_module
import config
import init_db
from services.ingestion.warrants.models import ensure_warrant_schema


class HomepageLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-homepage-', suffix='.db')
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

        # Seed a warrant record so we can test homepage gating.
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        ensure_warrant_schema(conn)
        run_ts = '2026-01-01 00:00:00'
        conn.execute(
            '''INSERT INTO warrants (source_record_id, county, city, person_name, dob,
               warrant_type, charges_text, issued_by, issue_date, bond_amount, bond_type,
               status, source_url, resolved_at, scraped_at, first_seen_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            ('test-warrant:hidden-person', 'Hidden', '', 'Hidden Person', '', 'active',
             'TEST CHARGE', 'Hidden Sheriff', '', '', '', 'active', 'https://example.gov/warrant/1',
             '', run_ts, run_ts, run_ts),
        )
        conn.commit()
        conn.close()
        conn = sqlite3.connect(self.db_path)
        for statement in [
            "ALTER TABLE bail_ad_orders ADD COLUMN simulator_logo_path TEXT",
            "ALTER TABLE bail_ad_orders ADD COLUMN simulator_target_url TEXT",
        ]:
            try:
                conn.execute(statement)
            except sqlite3.OperationalError:
                pass
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = self.previous_app_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_homepage_renders_newsroom_layout(self) -> None:
        client = app_module.app.test_client()
        response = client.get('/')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Montana Blotter', html)
        self.assertIn('Search Records', html)
        self.assertIn('Jurisdictions', html)
        self.assertIn('Breaking / Recent', html)
        self.assertIn('Live Activity', html)
        self.assertIn('Most Viewed This Week', html)
        self.assertIn('public-redesign.css', html)
        self.assertIn('public-shell-header', html)
        self.assertIn('/counties', html)
        self.assertIn('/missing-persons', html)
        self.assertIn('Missing Persons', html)
        self.assertIn('data-full-label="Missing Persons"', html)
        self.assertIn('public-nav-more', html)
        self.assertIn('Look up records', html)
        self.assertIn('/support?source=homepage_top', html)
        self.assertIn('id="mb-tab-bar"', html)
        self.assertIn('data-nav-track="1"', html)
        self.assertNotIn('Show Full Labels', html)
        self.assertNotIn('Command Center', html)
        self.assertTrue('>Get Alerts<' in html or '>Subscribe<' in html)

    def test_homepage_hides_warrant_spotlight_without_access(self) -> None:
        client = app_module.app.test_client()
        with patch.object(app_module, 'user_has_warrant_access', return_value=False):
            response = client.get('/')
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Hidden Person', html)
        self.assertNotIn('wanted-spotlight__grid', html)

    def test_homepage_shows_warrant_spotlight_with_access(self) -> None:
        client = app_module.app.test_client()
        with patch.object(app_module, 'user_has_warrant_access', return_value=True):
            response = client.get('/')
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Hidden Person', html)
        self.assertIn('wanted-spotlight__grid', html)

    def test_legacy_posts_route_redirects_into_homepage_filters(self) -> None:
        client = app_module.app.test_client()
        response = client.get('/posts?county=Yellowstone&city=Billings&q=theft')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.headers['Location'],
            '/?county=Yellowstone&city=Billings&q=theft',
        )

    def test_public_templates_do_not_link_to_legacy_posts_route(self) -> None:
        template_root = os.path.join(os.path.dirname(app_module.__file__), 'templates')
        checked_templates = (
            'cities.html',
            'city_page.html',
            'counties.html',
            'county_page.html',
            'pattern_page.html',
            'post_detail.html',
            'posts.html',
        )

        for template_name in checked_templates:
            with self.subTest(template=template_name):
                template_path = os.path.join(template_root, template_name)
                with open(template_path, 'r', encoding='utf-8') as handle:
                    html = handle.read()
                self.assertNotIn('/posts?', html)
                self.assertNotIn('action="/posts"', html)


if __name__ == '__main__':
    unittest.main()
