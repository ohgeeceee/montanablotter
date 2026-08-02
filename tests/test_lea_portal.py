"""Tests for LEA Portal — signup form, stats, FAQ, embed, contact."""
import json
import os
import sqlite3
import tempfile
import unittest

import app as app_module
import config
import init_db


class TestLEAPortal(unittest.TestCase):
    """Tests for the public LEA Portal pages and features."""

    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-lea-portal-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path

        conn = sqlite3.connect(self.db_path)
        init_db.init_database()
        init_db.migrate()
        conn.close()

        app_module.app.config['TESTING'] = True
        self.client = app_module.app.test_client()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_landing_page_loads(self) -> None:
        response = self.client.get('/leaportal/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'LEA Portal', response.data)

    def test_landing_page_has_login_links(self) -> None:
        response = self.client.get('/leaportal/')
        self.assertIn(b'Agency Login', response.data)
        self.assertIn(b'Admin Login', response.data)

    def test_landing_page_live_stats(self) -> None:
        """Stats section appears on the page."""
        response = self.client.get('/leaportal/')
        self.assertIn(b'Agencies Onboarded', response.data)
        self.assertIn(b'Incidents Published', response.data)

    def test_landing_page_has_faq(self) -> None:
        """FAQ section renders."""
        response = self.client.get('/leaportal/')
        self.assertIn(b'Frequently Asked Questions', response.data)
        self.assertIn(b'free for all Montana', response.data)

    def test_landing_page_has_embed_section(self) -> None:
        """Embed badge section renders."""
        response = self.client.get('/leaportal/')
        self.assertIn(b'Put a Badge on Your Site', response.data)

    def test_landing_page_has_contact(self) -> None:
        """Contact section renders."""
        response = self.client.get('/leaportal/')
        self.assertIn(b'Get in Touch', response.data)
        self.assertIn(b'lea@montanablotter.com', response.data)

    def test_register_interest_form(self) -> None:
        """POST to register saves to DB."""
        response = self.client.post('/leaportal/register', data={
            'agency_name': 'Test SO',
            'county': 'Test',
            'contact_name': 'John',
            'contact_email': 'john@test.gov',
            'contact_phone': '406-555-0000',
            'agency_type': 'sheriff',
            'message': 'Interested',
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Thanks John', response.data)

        # Verify DB
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM lea_registration_interest WHERE agency_name = 'Test SO'").fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row['contact_email'], 'john@test.gov')
        self.assertEqual(row['status'], 'new')

    def test_register_missing_fields(self) -> None:
        """Missing required fields returns 400."""
        response = self.client.post('/leaportal/register', data={
            'agency_name': '',
            'county': '',
            'contact_name': '',
            'contact_email': '',
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn(b'Agency name is required', response.data)

    def test_stats_json(self) -> None:
        """GET /leaportal/stats returns JSON."""
        response = self.client.get('/leaportal/stats')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertIn('agency_count', data)
        self.assertIn('published_count', data)

    def test_embed_page(self) -> None:
        """Embed page renders."""
        response = self.client.get('/leaportal/embed')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Embed Badge', response.data)
        self.assertIn(b'montanablotter.com/leaportal', response.data)

    def test_login_redirect(self) -> None:
        response = self.client.get('/leaportal/login')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/panel/login', response.headers.get('Location', ''))

    def test_admin_redirect(self) -> None:
        response = self.client.get('/leaportal/admin')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/lea-management', response.headers.get('Location', ''))
