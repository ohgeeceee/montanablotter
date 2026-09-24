"""Tests for the jail roster search feature.

Covers:
- Route registration and basic HTTP behavior (anonymous access, preview gating)
- Full-text search returns matching rows with proper decoration
- Paywall gating blocks anonymous users after preview limit
- Subscriber access returns full results
- API endpoint returns structured JSON
"""
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from flask import Flask

from blueprints.detention import register_detention_blueprint


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class JailRosterSearchTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(
            __name__,
            template_folder=os.path.join(ROOT, 'templates'),
            static_folder=os.path.join(ROOT, 'static'),
        )
        self.app.secret_key = 'test-secret'
        self.client = self.app.test_client()

        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.db')
        os.close(self.db_fd)
        self.pv_fd, self.pv_path = tempfile.mkstemp(suffix='.db')
        os.close(self.pv_fd)

        self._build_schema(self.db_path)
        self._seed_sources()
        self._seed_bookings()
        self._build_page_views_db(self.pv_path)

        register_detention_blueprint(
            self.app,
            get_db=self._get_db,
            booking_context_loader=lambda *a, **k: {},
            roster_directory_loader=lambda: {},
        )

        # Inject the template context vars the public_page_base.html nav
        # expects (normally provided by the app-level context processor).
        @self.app.context_processor
        def _inject_nav():
            return {
                'csrf_token': lambda: 'test-csrf-token',
                'public_action_labels': {
                    'subscribe': 'Subscribe',
                    'subscribe_full': 'Subscribe to Warrant Access',
                    'signin': 'Sign In',
                },
                'public_primary_nav_items': [],
                'public_more_nav_groups': [],
                'public_secondary_nav_items': [],
                'public_nav_menu_labels_by_href': {},
                'public_nav_full_labels_by_href': {},
                'public_mobile_short_label_legend': {},
                'public_nav_experiment': {},
                'public_footer_items': [],
                'footer_featured_city_items': [],
                'public_user': None,
                'winter_storm_banner': None,
            }

    def tearDown(self):
        for p in [self.db_path, self.pv_path]:
            if os.path.exists(p):
                os.unlink(p)

    def _get_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _build_schema(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.executescript('''
            CREATE TABLE jail_booking_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                county_slug TEXT UNIQUE NOT NULL,
                county_name TEXT NOT NULL,
                facility_name TEXT NOT NULL,
                roster_url TEXT,
                phone TEXT,
                source_type TEXT NOT NULL DEFAULT 'official_roster',
                coverage_tier TEXT NOT NULL DEFAULT 'standard',
                is_enabled INTEGER NOT NULL DEFAULT 1,
                is_featured INTEGER NOT NULL DEFAULT 0,
                last_checked_at TEXT,
                last_success_at TEXT,
                latest_error TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE jail_bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER,
                county_slug TEXT NOT NULL,
                county_name TEXT NOT NULL,
                facility_name TEXT NOT NULL,
                person_name TEXT NOT NULL,
                age INTEGER,
                booking_number TEXT,
                booking_at TEXT,
                release_at TEXT,
                charges_summary TEXT DEFAULT '',
                charges_json TEXT,
                arresting_agency TEXT,
                source_url TEXT,
                source_record_id TEXT,
                booking_status TEXT NOT NULL DEFAULT 'current',
                is_current INTEGER NOT NULL DEFAULT 1,
                first_seen_at TEXT DEFAULT (datetime('now')),
                last_seen_at TEXT DEFAULT (datetime('now')),
                notes TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (source_id) REFERENCES jail_booking_sources(id) ON DELETE SET NULL
            );
            CREATE TABLE public_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT,
                display_name TEXT,
                is_active INTEGER DEFAULT 1,
                is_subscribed INTEGER DEFAULT 0,
                subscriber_plan TEXT DEFAULT 'free',
                subscription_status TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            );
        ''')
        conn.commit()
        conn.close()

    def _build_page_views_db(self, pv_path):
        conn = sqlite3.connect(pv_path)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS preview_views (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                viewer_type TEXT NOT NULL DEFAULT 'anonymous',
                viewer_id TEXT NOT NULL,
                resource_type TEXT NOT NULL DEFAULT 'incident',
                resource_id INTEGER,
                viewed_at TEXT DEFAULT (datetime('now'))
            )
        ''')
        conn.commit()
        conn.close()

    def _seed_sources(self):
        conn = self._get_db()
        for slug, name in [('cascade', 'Cascade'), ('yellowstone', 'Yellowstone'), ('missoula', 'Missoula')]:
            conn.execute(
                '''INSERT INTO jail_booking_sources (county_slug, county_name, facility_name, roster_url, phone, last_success_at)
                   VALUES (?, ?, ?, ?, ?, datetime('now'))''',
                (slug, name, f'{name} County Detention', f'https://example.com/{slug}', '406-555-0000'),
            )
        conn.commit()
        conn.close()

    def _seed_bookings(self):
        conn = self._get_db()
        sources = conn.execute("SELECT id, county_slug FROM jail_booking_sources").fetchall()
        source_map = {row['county_slug']: row['id'] for row in sources}

        bookings = [
            ('John Smith', 'cascade', 'cascade', 'DUI', '2026-09-15 10:00:00', 'current'),
            ('Jane Doe', 'cascade', 'cascade', 'Theft', '2026-09-14 09:00:00', 'current'),
            ('Bob Johnson', 'yellowstone', 'yellowstone', 'Assault', '2026-09-13 08:00:00', 'current'),
            ('Alice Brown', 'yellowstone', 'yellowstone', 'DUI', '2026-09-12 07:00:00', 'released'),
            ('Charlie Smith', 'missoula', 'missoula', 'Burglary', '2026-09-11 06:00:00', 'current'),
            ('Eve Wilson', 'cascade', 'cascade', 'Fraud', '2026-09-10 05:00:00', 'released'),
            ('Frank Davis', 'cascade', 'cascade', 'Robbery', '2026-09-09 04:00:00', 'current'),
        ]
        for person, county_slug, county_name, charges, booking_at, status in bookings:
            conn.execute(
                '''INSERT INTO jail_bookings (source_id, county_slug, county_name, facility_name, person_name, booking_at, charges_summary, booking_status, is_current)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (source_map[county_slug], county_slug, county_name, f'{county_name} County Detention', person, booking_at, charges, status, 1 if status == 'current' else 0),
            )
        conn.commit()
        conn.close()

    def _patch_paywall_db(self):
        """Patch paywall module to use our test DBs instead of production."""
        import services.monetization.paywall as paywall_module

        def _tmp_db():
            c = sqlite3.connect(self.db_path)
            c.row_factory = sqlite3.Row
            return c

        def _tmp_pv():
            c = sqlite3.connect(self.pv_path)
            c.row_factory = sqlite3.Row
            return c

        self._orig_paywall_db = paywall_module.get_db
        self._orig_pv_conn = paywall_module.connect_page_views
        paywall_module.get_db = _tmp_db
        paywall_module.connect_page_views = _tmp_pv

    def _restore_paywall_db(self):
        import services.monetization.paywall as paywall_module
        paywall_module.get_db = self._orig_paywall_db
        paywall_module.connect_page_views = self._orig_pv_conn

    def _mock_anon_user(self):
        """Return a patcher for an anonymous Flask-Login current_user."""
        user = MagicMock()
        user.is_authenticated = False
        return patch('services.monetization.paywall.current_user', user)

    def test_search_page_returns_200(self):
        self._patch_paywall_db()
        try:
            with self._mock_anon_user():
                response = self.client.get('/jail-roster-search')
                self.assertEqual(response.status_code, 200)
        finally:
            self._restore_paywall_db()

    def test_search_by_name_returns_matches(self):
        self._patch_paywall_db()
        try:
            with self._mock_anon_user():
                response = self.client.get('/jail-roster-search?q=Smith')
                self.assertEqual(response.status_code, 200)
                body = response.get_data(as_text=True)
                self.assertIn('John Smith', body)
                self.assertIn('Charlie Smith', body)
                self.assertNotIn('Jane Doe', body)
        finally:
            self._restore_paywall_db()

    def test_search_filters_by_county(self):
        self._patch_paywall_db()
        try:
            with self._mock_anon_user():
                response = self.client.get('/jail-roster-search?county=cascade')
                self.assertEqual(response.status_code, 200)
                body = response.get_data(as_text=True)
                self.assertIn('John Smith', body)
                self.assertIn('Jane Doe', body)
                self.assertNotIn('Bob Johnson', body)
        finally:
            self._restore_paywall_db()

    def test_search_filters_by_status_current(self):
        self._patch_paywall_db()
        try:
            with self._mock_anon_user():
                response = self.client.get('/jail-roster-search?status=current')
                self.assertEqual(response.status_code, 200)
                body = response.get_data(as_text=True)
                # Should not contain released persons
                self.assertNotIn('Alice Brown', body)
                self.assertNotIn('Eve Wilson', body)
        finally:
            self._restore_paywall_db()

    def test_anonymous_user_sees_preview_gate_after_limit(self):
        # 4 current-booking matches exceed the preview limit of 3 → gate should appear
        self._patch_paywall_db()
        try:
            with self._mock_anon_user():
                response = self.client.get('/jail-roster-search?status=current')
                self.assertEqual(response.status_code, 200)
                body = response.get_data(as_text=True)
                self.assertIn('more result', body)
        finally:
            self._restore_paywall_db()

    def test_anonymous_user_no_gate_under_preview_limit(self):
        # 2 results (Smith) — under preview limit of 3, no gate
        self._patch_paywall_db()
        try:
            with self._mock_anon_user():
                response = self.client.get('/jail-roster-search?q=Smith')
                self.assertEqual(response.status_code, 200)
                body = response.get_data(as_text=True)
                self.assertNotIn('more result', body)
        finally:
            self._restore_paywall_db()

    def test_api_endpoint_returns_json(self):
        self._patch_paywall_db()
        try:
            with self._mock_anon_user():
                response = self.client.get('/api/jail-roster-search?q=Doe')
                self.assertEqual(response.status_code, 200)
                payload = response.get_json()
                self.assertTrue(payload['ok'])
                self.assertEqual(payload['total_matches'], 1)
                self.assertEqual(payload['results'][0]['person_name'], 'Jane Doe')
        finally:
            self._restore_paywall_db()

    def test_subscribed_user_sees_all_results(self):
        # Seed a public_user with an active plus subscription
        conn = self._get_db()
        cur = conn.execute(
            '''INSERT INTO public_users (email, password_hash, is_active, is_subscribed, subscriber_plan, subscription_status, display_name)
               VALUES (?, ?, 1, 1, 'plus', 'active', 'Test User')''',
            ('sub@example.com', 'x'),
        )
        user_id = cur.lastrowid
        conn.commit()
        conn.close()

        self._patch_paywall_db()
        try:
            with self._mock_anon_user():
                with self.client.session_transaction() as sess:
                    sess['public_user_id'] = int(user_id)
                response = self.client.get('/jail-roster-search?status=current')
                self.assertEqual(response.status_code, 200)
                body = response.get_data(as_text=True)
                self.assertIn('Full access', body)
                # All current results visible (John Smith, Jane Doe, Bob Johnson, Charlie Smith, Frank Davis)
                self.assertIn('John Smith', body)
                self.assertIn('Jane Doe', body)
                self.assertIn('Bob Johnson', body)
                self.assertIn('Charlie Smith', body)
                self.assertIn('Frank Davis', body)
                # No paywall gate
                self.assertNotIn('more result', body)
        finally:
            self._restore_paywall_db()


if __name__ == '__main__':
    unittest.main()
