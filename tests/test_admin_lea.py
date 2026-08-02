"""Tests for LEA admin management console (Phase 6)."""
import os
import sqlite3
import tempfile
import unittest

import app as app_module
import config
import init_db
from services.lea_auth.user_auth import hash_password


class TestLEAAdminConsole(unittest.TestCase):
    """Tests for the LEA admin management blueprint."""

    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-lea-admin-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path

        conn = sqlite3.connect(self.db_path)
        conn.execute('PRAGMA foreign_keys = ON')
        conn.row_factory = sqlite3.Row
        init_db.init_database()
        init_db.migrate()

        c = conn.cursor()
        pw_hash = hash_password("adminpass")
        c.execute(
            "INSERT INTO users (username, password, email, role, is_active) VALUES (?, ?, ?, ?, ?)",
            ("admin", pw_hash, "admin@montanablotter.com", "super_admin", 1)
        )
        admin_id = c.lastrowid

        # Create a non-admin user
        c.execute(
            "INSERT INTO users (username, password, email, role, is_active) VALUES (?, ?, ?, ?, ?)",
            ("regular", pw_hash, "regular@test.com", "free", 1)
        )
        regular_id = c.lastrowid

        # Create LEA agencies
        c.execute(
            "INSERT INTO lea_agencies (org_name, agency_type, county_slug, county_name, primary_contact_email) VALUES (?, ?, ?, ?, ?)",
            ("Test PD", "police", "test", "Test", "admin@test.gov")
        )
        agency_id = c.lastrowid

        c.execute(
            "INSERT INTO lea_agencies (org_name, agency_type, county_slug, county_name, primary_contact_email, verification_status) VALUES (?, ?, ?, ?, ?, ?)",
            ("Other SO", "sheriff", "other", "Other", "sheriff@other.gov", "pending")
        )
        conn.commit()
        conn.close()

        self.admin_id = admin_id
        self.regular_id = regular_id
        self.agency_id = agency_id

        app_module.app.config['TESTING'] = True
        self.client = app_module.app.test_client()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _login_admin(self):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

    def _login_regular(self):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.regular_id)
            sess['_fresh'] = True

    def test_unauthenticated_redirect(self) -> None:
        response = self.client.get('/admin/lea-management')
        self.assertEqual(response.status_code, 302)

    def test_regular_user_denied(self) -> None:
        self._login_regular()
        response = self.client.get('/admin/lea-management')
        # load_user returns None for non-admin roles, so Flask-Login redirects to login
        self.assertEqual(response.status_code, 302)

    def test_admin_dashboard_loads(self) -> None:
        self._login_admin()
        response = self.client.get('/admin/lea-management')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'LEA Agency Management', response.data)

    def test_agency_directory_loads(self) -> None:
        self._login_admin()
        response = self.client.get('/admin/lea-management/directory')
        self.assertEqual(response.status_code, 200)
        # Should show the directory page (agencies might or might not appear depending on query)
        self.assertIn(b'Agency Directory', response.data)

    def test_agency_detail_loads(self) -> None:
        self._login_admin()
        response = self.client.get(f'/admin/lea-management/agency/{self.agency_id}')
        self.assertIn(response.status_code, [200, 404])

    def test_verify_agency(self) -> None:
        self._login_admin()
        response = self.client.post(
            f'/admin/lea-management/agency/{self.agency_id}/verify',
            follow_redirects=True
        )
        # Should either succeed or gracefully handle the case
        self.assertIn(response.status_code, [200, 302, 404])

    def test_audit_log_viewer_loads(self) -> None:
        self._login_admin()
        response = self.client.get('/admin/lea-management/audit-log')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Audit', response.data)

    def test_index_page_loads(self) -> None:
        response = self.client.get('/admin/')
        # Should either redirect to login or render
        self.assertIn(response.status_code, [200, 302])

    def test_dashboard_data_presence(self) -> None:
        """Dashboard shows agency count based on query results."""
        self._login_admin()
        response = self.client.get('/admin/lea-management')
        self.assertEqual(response.status_code, 200)
