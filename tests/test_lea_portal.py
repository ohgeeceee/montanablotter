"""Tests for LEA Portal landing page."""
import os
import sqlite3
import tempfile
import unittest

import app as app_module
import config
import init_db


class TestLEAPortal(unittest.TestCase):
    """Tests for the public LEA Portal landing page."""

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
        self.assertIn(b'Agency Self-Service', response.data)

    def test_landing_page_has_login_links(self) -> None:
        response = self.client.get('/leaportal/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Agency Login', response.data)
        self.assertIn(b'Admin Login', response.data)

    def test_login_redirect(self) -> None:
        response = self.client.get('/leaportal/login')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/panel/login', response.headers.get('Location', ''))

    def test_admin_redirect(self) -> None:
        response = self.client.get('/leaportal/admin')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/lea-management', response.headers.get('Location', ''))
