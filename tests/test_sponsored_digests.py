"""Sponsored county digest — public inquiry form + admin CRUD + sponsor lookup helper."""
import os
import sqlite3
import tempfile
import unittest

import app as app_module
import config
import init_db


class SponsoredDigestsTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-spons-', suffix='.db')
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
            ('spons-admin', 'not-used', 'spons@example.com', 'ops', 1),
        )
        conn.commit()
        conn.close()
        return int(cursor.lastrowid)

    def _login_admin_session(self, client) -> None:
        with client.session_transaction() as session:
            session['_user_id'] = str(self.admin_user_id)
            session['_fresh'] = True
            session['_csrf_token'] = 'test-csrf-token'

    def _post(self, client, url, data):
        """Helper: POST to admin URL with CSRF token included."""
        data = dict(data)
        data.setdefault('csrf_token', 'test-csrf-token')
        return client.post(url, data=data)

    # ----- Public landing -----

    def test_public_get_renders(self) -> None:
        client = app_module.app.test_client()
        r = client.get('/sponsored-digest')
        html = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn('Reach the readers', html)
        self.assertIn('Pricing', html)
        self.assertIn('Send inquiry', html)
        self.assertIn('Top of every email', html)

    def test_public_post_creates_inquiry(self) -> None:
        client = app_module.app.test_client()
        r = client.post('/sponsored-digest', data={
            'county': 'Gallatin',
            'business': 'Test Sponsor LLC',
            'contact': 'Tester',
            'email': 'inquiry-test@example.com',
            'message': 'Test inquiry',
        })
        html = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn('got your inquiry', html)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            'SELECT * FROM sponsored_digests WHERE contact_email = ?',
            ('inquiry-test@example.com',),
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row['county'], 'Gallatin')
        self.assertEqual(row['sponsor_name'], 'Test Sponsor LLC')
        self.assertEqual(row['is_active'], 0)  # inquiries are inactive by default

    def test_public_post_validates(self) -> None:
        client = app_module.app.test_client()
        r = client.post('/sponsored-digest', data={
            'county': '',  # missing
            'business': 'Test',
            'email': 'not-an-email',
        })
        html = r.get_data(as_text=True)
        self.assertIn('required', html.lower())

    # ----- Admin module -----

    def test_admin_list_renders(self) -> None:
        client = app_module.app.test_client()
        self._login_admin_session(client)
        r = client.get('/admin/sponsored-digests')
        html = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn('Sponsored County Digests', html)
        self.assertIn('Add a sponsorship', html)

    def test_admin_add_creates_active_sponsorship(self) -> None:
        client = app_module.app.test_client()
        self._login_admin_session(client)
        self._post(client, '/admin/sponsored-digests/add', {
            'county': 'Yellowstone',
            'sponsor_name': 'Billings Bail Bonds',
            'sponsor_pitch': 'Call us first.',
            'sponsor_url': 'https://example.com',
            'contact_email': 'billing@example.com',
            'monthly_rate': '99.00',
            'starts_on': '2026-06-01',
            'expires_on': '2026-12-31',
            'notes': 'Test sponsorship',
        })
        r = client.get('/admin/sponsored-digests')
        html = r.get_data(as_text=True)
        html = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn('Billings Bail Bonds', html)
        self.assertIn('active', html)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            'SELECT * FROM sponsored_digests WHERE contact_email = ?',
            ('billing@example.com',),
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row['is_active'], 1)
        self.assertEqual(row['monthly_rate_cents'], 9900)
        self.assertEqual(row['county'], 'Yellowstone')

    def test_admin_add_requires_county_and_name(self) -> None:
        client = app_module.app.test_client()
        self._login_admin_session(client)
        self._post(client, '/admin/sponsored-digests/add', {
            'county': '',
            'sponsor_name': '',
        })
        r = client.get('/admin/sponsored-digests')
        html = r.get_data(as_text=True)
        self.assertIn('required', html.lower())

    def test_admin_add_deactivates_existing_sponsor_for_same_county(self) -> None:
        client = app_module.app.test_client()
        self._login_admin_session(client)
        # First sponsor
        self._post(client, '/admin/sponsored-digests/add', {
            'county': 'Gallatin',
            'sponsor_name': 'Old Sponsor',
            'sponsor_url': 'https://old.example.com',
        })
        # New sponsor for the same county
        self._post(client, '/admin/sponsored-digests/add', {
            'county': 'Gallatin',
            'sponsor_name': 'New Sponsor',
            'sponsor_url': 'https://new.example.com',
        })
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        actives = conn.execute(
            "SELECT sponsor_name FROM sponsored_digests WHERE county = 'Gallatin' AND is_active = 1"
        ).fetchall()
        all_rows = conn.execute(
            "SELECT sponsor_name, is_active FROM sponsored_digests WHERE county = 'Gallatin' ORDER BY id"
        ).fetchall()
        conn.close()
        self.assertEqual(len(actives), 1, f"expected 1 active, got {len(actives)}")
        self.assertEqual(actives[0]['sponsor_name'], 'New Sponsor')
        # Both rows still present
        self.assertEqual(len(all_rows), 2)

    def test_admin_toggle_deactivates(self) -> None:
        client = app_module.app.test_client()
        self._login_admin_session(client)
        self._post(client, '/admin/sponsored-digests/add', {
            'county': 'Cascade',
            'sponsor_name': 'GF Bail',
        })
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        sid = conn.execute(
            "SELECT id FROM sponsored_digests WHERE county = 'Cascade'"
        ).fetchone()['id']
        conn.close()

        self._post(client, f'/admin/sponsored-digests/{sid}/toggle', {})
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        active = conn.execute(
            'SELECT is_active FROM sponsored_digests WHERE id = ?', (sid,)
        ).fetchone()['is_active']
        conn.close()
        self.assertEqual(active, 0)

    # ----- Sponsor lookup helper -----

    def test_get_active_sponsor_helper(self) -> None:
        from services.monetization.sponsored_digests import get_active_sponsor, render_sponsor_block

        client = app_module.app.test_client()
        self._login_admin_session(client)
        self._post(client, '/admin/sponsored-digests/add', {
            'county': 'Flathead',
            'sponsor_name': 'Kalispell Bail',
            'sponsor_url': 'https://kalispell.example.com',
            'sponsor_pitch': '24/7 service.',
            'starts_on': '2020-01-01',
            'expires_on': '2099-12-31',
        })

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        sponsor = get_active_sponsor(conn, 'Flathead')
        self.assertIsNotNone(sponsor)
        self.assertEqual(sponsor['sponsor_name'], 'Kalispell Bail')

        # No sponsor for an unrelated county
        none_sponsor = get_active_sponsor(conn, 'Somewhere Else')
        self.assertIsNone(none_sponsor)

        # render block contains the name and URL
        block = render_sponsor_block(sponsor)
        self.assertIn('Kalispell Bail', block)
        self.assertIn('https://kalispell.example.com', block)
        self.assertIn('24/7 service', block)
        conn.close()

    def test_get_active_sponsor_respects_expiry(self) -> None:
        from services.monetization.sponsored_digests import get_active_sponsor

        client = app_module.app.test_client()
        self._login_admin_session(client)
        # Sponsorship that has already expired
        self._post(client, '/admin/sponsored-digests/add', {
            'county': 'Ravalli',
            'sponsor_name': 'Hamilton Bonds',
            'starts_on': '2020-01-01',
            'expires_on': '2020-12-31',
        })

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        sponsor = get_active_sponsor(conn, 'Ravalli')
        conn.close()
        self.assertIsNone(sponsor, 'expired sponsorship should not be returned')


if __name__ == '__main__':
    unittest.main()
