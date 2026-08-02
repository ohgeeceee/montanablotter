"""Tests for LEA Panel routes (Phase 3)."""
import json
import os
import sqlite3
import tempfile
import unittest

import app as app_module
import config
import init_db


class TestLEAPanelRoutes(unittest.TestCase):
    """Tests for the LEA agency dashboard blueprint."""

    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-lea-panel-', suffix='.db')
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
        conn.close()

        app_module.app.config['TESTING'] = True
        self.client = app_module.app.test_client()

        # Create a test agency and user
        conn = sqlite3.connect(self.db_path)
        conn.execute('PRAGMA foreign_keys = ON')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO lea_agencies (org_name, agency_type, county_slug, county_name, primary_contact_email) VALUES (?, ?, ?, ?, ?)",
            ("Test PD", "police", "test", "Test", "admin@test.gov")
        )
        self.agency_id = cursor.lastrowid
        conn.commit()

        from services.lea_auth.user_auth import hash_password
        pw_hash = hash_password("password123")
        cursor.execute(
            "INSERT INTO lea_users (agency_id, username, email, full_name, password_hash, role) VALUES (?, ?, ?, ?, ?, ?)",
            (self.agency_id, "officer1", "officer@test.gov", "Officer One", pw_hash, "pio")
        )
        self.user_id = cursor.lastrowid
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _login(self):
        """Helper: log in as the test LEA user."""
        with self.client.session_transaction() as sess:
            sess['lea_user_id'] = self.user_id
            sess['lea_agency_id'] = self.agency_id

    def test_unauthenticated_redirect(self) -> None:
        """Dashboard should redirect to login when not authenticated."""
        response = self.client.get('/panel/test/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/panel/login', response.headers.get('Location', ''))

    def test_login_page_loads(self) -> None:
        """Login page should render."""
        response = self.client.get('/panel/login')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Login', response.data)

    def test_dashboard_authenticated(self) -> None:
        """Dashboard should render for authenticated user."""
        self._login()
        response = self.client.get('/panel/test/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Dashboard', response.data)

    def test_submit_form_loads(self) -> None:
        """Submit incident form should render."""
        self._login()
        response = self.client.get('/panel/test/submit')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Submit Incident', response.data)

    def test_submit_incident_post_creates_draft(self) -> None:
        """POST to submit incident should create a draft."""
        self._login()
        response = self.client.post('/panel/test/submit', data={
            'incident_date': '2026-08-02',
            'cad_number': 'CAD-999',
            'primary_offense_mca': '45-5-202',
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)

        # Verify draft exists in DB
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM lea_blotter_drafts WHERE cad_number = ?", ("CAD-999",)
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row['submission_status'], 'draft')

    def test_batch_upload_form_loads(self) -> None:
        """Batch upload form should render."""
        self._login()
        response = self.client.get('/panel/test/batch-upload')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Batch Upload', response.data)

    def test_blotter_history_loads(self) -> None:
        """Blotter history page should render."""
        self._login()
        response = self.client.get('/panel/test/history')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'History', response.data)

    def test_wrong_county_denied(self) -> None:
        """User should be denied access to a different county."""
        self._login()

        # Create another agency
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO lea_agencies (org_name, agency_type, county_slug, county_name, primary_contact_email) VALUES (?, ?, ?, ?, ?)",
                     ("Other SO", "sheriff", "other", "Other", "sheriff@other.gov"))
        conn.commit()
        conn.close()

        response = self.client.get('/panel/other/')
        self.assertEqual(response.status_code, 403)
