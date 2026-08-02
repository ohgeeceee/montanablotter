"""Tests for LEA REST API (Phase 4).

Tests: token auth, blotter publish/batch, roster sync, audit retrieval.
Uses Flask test_client() with temp SQLite DB.
"""
import json
import os
import sqlite3
import tempfile
import unittest

import config
import init_db
import app as app_module

from services.lea_auth.user_auth import hash_password
from services.lea_auth.api_tokens import hash_token, generate_token


class TestLEARestAPI(unittest.TestCase):
    """Full integration test suite for the /api/v1/lea/ REST API."""

    @classmethod
    def setUpClass(cls):
        """Patch config so tests use consistent values."""
        cls._orig_jwt_secret = getattr(config, 'LEA_JWT_SECRET', None)
        config.LEA_JWT_SECRET = 'test-jwt-secret-for-testing'

    @classmethod
    def tearDownClass(cls):
        if cls._orig_jwt_secret is not None:
            config.LEA_JWT_SECRET = cls._orig_jwt_secret
        elif hasattr(config, 'LEA_JWT_SECRET'):
            delattr(config, 'LEA_JWT_SECRET')

    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-api-lea-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path

        conn = sqlite3.connect(self.db_path)
        conn.execute('PRAGMA foreign_keys = ON')
        conn.row_factory = sqlite3.Row
        init_db.init_database()
        init_db.ensure_lea_schema(conn)
        self.conn = conn

        app_module.app.config['TESTING'] = True
        self.client = app_module.app.test_client()

        # --- Fixture: test agency ---
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO lea_agencies (org_name, agency_type, county_slug, county_name, "
            "primary_contact_email, verification_status, enable_api_access) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("Test Sheriff", "sheriff", "test", "Test County",
             "sheriff@testco.gov", "verified", 1),
        )
        self.agency_id = cursor.lastrowid

        # --- Fixture: test user ---
        pwd_hash = hash_password("ApiKey123!")
        cursor.execute(
            "INSERT INTO lea_users (agency_id, username, email, full_name, password_hash, role) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (self.agency_id, "apiuser", "api@testco.gov", "API User", pwd_hash, "pio"),
        )
        self.user_id = cursor.lastrowid

        # --- Fixture: API token stored in lea_api_tokens ---
        self.raw_token = generate_token()  # e.g. "abc123..."
        token_hash = hash_token(self.raw_token)
        cursor.execute(
            "INSERT INTO lea_api_tokens (agency_id, user_id, token_name, token_hash, scopes) "
            "VALUES (?, ?, ?, ?, ?)",
            (self.agency_id, self.user_id, "Test API Token", token_hash,
             json.dumps(["blotter.publish", "blotter.batch", "roster.sync", "audit.read"])),
        )
        conn.commit()
        self.auth_header = f"Bearer {self.raw_token}"

    def tearDown(self) -> None:
        self.conn.close()
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    # ------------------------------------------------------------------
    # Token Endpoint
    # ------------------------------------------------------------------

    def test_post_token_valid_credentials(self) -> None:
        """POST /api/v1/lea/auth/token with valid credentials returns JWT."""
        response = self.client.post(
            '/api/v1/lea/auth/token',
            json={
                'grant_type': 'password',
                'username': 'apiuser',
                'password': 'ApiKey123!',
            },
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertIn('access_token', data)
        self.assertIn('token_type', data)
        self.assertEqual(data['token_type'], 'Bearer')
        self.assertIn('expires_in', data)

    def test_post_token_invalid_credentials(self) -> None:
        """POST /api/v1/lea/auth/token with wrong password returns 401."""
        response = self.client.post(
            '/api/v1/lea/auth/token',
            json={
                'grant_type': 'password',
                'username': 'apiuser',
                'password': 'WrongPassword',
            },
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 401)
        data = json.loads(response.data)
        self.assertIn('error', data)
        self.assertEqual(data['error'], 'invalid_credentials')

    def test_post_token_missing_fields(self) -> None:
        """POST /api/v1/lea/auth/token with missing fields returns 400."""
        response = self.client.post(
            '/api/v1/lea/auth/token',
            json={'grant_type': 'password'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_post_token_unsupported_grant_type(self) -> None:
        """POST /api/v1/lea/auth/token with bad grant_type returns 400."""
        response = self.client.post(
            '/api/v1/lea/auth/token',
            json={'grant_type': 'client_credentials'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    # ------------------------------------------------------------------
    # Auth Decorator
    # ------------------------------------------------------------------

    def test_auth_missing_header(self) -> None:
        """Endpoint without Authorization header returns 401."""
        response = self.client.post(
            '/api/v1/lea/blotter/publish',
            json={},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 401)
        data = json.loads(response.data)
        self.assertIn('error', data)

    def test_auth_bad_token(self) -> None:
        """Endpoint with invalid token returns 401."""
        response = self.client.post(
            '/api/v1/lea/blotter/publish',
            json={},
            content_type='application/json',
            headers={'Authorization': 'Bearer invalidtoken123'},
        )
        self.assertEqual(response.status_code, 401)
        data = json.loads(response.data)
        self.assertIn('error', data)

    def test_auth_bad_scheme(self) -> None:
        """Endpoint with non-Bearer scheme returns 401."""
        response = self.client.post(
            '/api/v1/lea/blotter/publish',
            json={},
            content_type='application/json',
            headers={'Authorization': 'Basic dGVzdDp0ZXN0'},
        )
        self.assertEqual(response.status_code, 401)

    # ------------------------------------------------------------------
    # Blotter Publish (single)
    # ------------------------------------------------------------------

    def test_blotter_publish_valid(self) -> None:
        """POST /api/v1/lea/blotter/publish with valid data returns 201."""
        response = self.client.post(
            '/api/v1/lea/blotter/publish',
            json={
                'incident_date': '2026-08-02',
                'incident_time': '14:30',
                'cad_number': 'CAD-2026-1234',
                'location': '300 BLK MAIN ST',
                'charges': ['45-5-202', '45-5-206'],
                'narrative': 'Officers responded to disturbance.',
            },
            content_type='application/json',
            headers={'Authorization': self.auth_header},
        )
        self.assertEqual(response.status_code, 201)
        data = json.loads(response.data)
        self.assertIn('draft_id', data)
        self.assertIn('status', data)
        self.assertEqual(data['status'], 'draft')

        # Verify it's in the DB
        row = self.conn.execute(
            "SELECT * FROM lea_blotter_drafts WHERE id = ?", (data['draft_id'],)
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row['agency_id'], self.agency_id)
        self.assertEqual(row['cad_number'], 'CAD-2026-1234')

    def test_blotter_publish_missing_required_field(self) -> None:
        """POST /api/v1/lea/blotter/publish missing incident_date returns 400."""
        response = self.client.post(
            '/api/v1/lea/blotter/publish',
            json={
                'incident_time': '14:30',
                'cad_number': 'CAD-2026-1234',
            },
            content_type='application/json',
            headers={'Authorization': self.auth_header},
        )
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data)
        self.assertIn('error', data)

    # ------------------------------------------------------------------
    # Blotter Batch
    # ------------------------------------------------------------------

    def test_blotter_batch_valid(self) -> None:
        """POST /api/v1/lea/blotter/batch with valid incidents returns 202."""
        response = self.client.post(
            '/api/v1/lea/blotter/batch',
            json={
                'incidents': [
                    {
                        'incident_date': '2026-08-01',
                        'incident_time': '10:00',
                        'cad_number': 'CAD-1001',
                        'location': '100 FIRST ST',
                        'charges': ['45-5-101'],
                    },
                    {
                        'incident_date': '2026-08-01',
                        'incident_time': '11:00',
                        'cad_number': 'CAD-1002',
                        'location': '200 SECOND ST',
                        'charges': ['45-5-202'],
                    },
                ],
            },
            content_type='application/json',
            headers={'Authorization': self.auth_header},
        )
        self.assertEqual(response.status_code, 202)
        data = json.loads(response.data)
        self.assertIn('batch_id', data)
        self.assertIn('status_url', data)
        self.assertEqual(data['records_queued'], 2)
        self.assertEqual(data['status'], 'processing')

        # Verify they're in the DB
        count = self.conn.execute(
            "SELECT COUNT(*) FROM lea_blotter_drafts "
            "WHERE agency_id = ? AND submission_status = 'batch_pending'",
            (self.agency_id,),
        ).fetchone()[0]
        self.assertEqual(count, 2)

    def test_blotter_batch_empty(self) -> None:
        """POST /api/v1/lea/blotter/batch with empty incidents returns 400."""
        response = self.client.post(
            '/api/v1/lea/blotter/batch',
            json={'incidents': []},
            content_type='application/json',
            headers={'Authorization': self.auth_header},
        )
        self.assertEqual(response.status_code, 400)

    def test_blotter_batch_no_incidents_key(self) -> None:
        """POST /api/v1/lea/blotter/batch without incidents key returns 400."""
        response = self.client.post(
            '/api/v1/lea/blotter/batch',
            json={},
            content_type='application/json',
            headers={'Authorization': self.auth_header},
        )
        self.assertEqual(response.status_code, 400)

    # ------------------------------------------------------------------
    # Batch Status
    # ------------------------------------------------------------------

    def test_batch_status_valid(self) -> None:
        """GET /api/v1/lea/blotter/batch/<batch_id>/status returns counts."""
        # First create a batch
        batch_resp = self.client.post(
            '/api/v1/lea/blotter/batch',
            json={
                'incidents': [
                    {
                        'incident_date': '2026-08-01',
                        'cad_number': 'CAD-B001',
                        'location': '100 TEST',
                        'charges': ['45-5-101'],
                    },
                ],
            },
            content_type='application/json',
            headers={'Authorization': self.auth_header},
        )
        batch_data = json.loads(batch_resp.data)
        batch_id = batch_data['batch_id']

        # Check status
        response = self.client.get(
            f'/api/v1/lea/blotter/batch/{batch_id}/status',
            headers={'Authorization': self.auth_header},
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data['batch_id'], batch_id)
        self.assertIn('total', data)
        self.assertIn('status', data)

    def test_batch_status_not_found(self) -> None:
        """GET /api/v1/lea/blotter/batch/<unknown_id>/status returns 404."""
        response = self.client.get(
            '/api/v1/lea/blotter/batch/nonexistent_batch/status',
            headers={'Authorization': self.auth_header},
        )
        self.assertEqual(response.status_code, 404)

    # ------------------------------------------------------------------
    # Roster Sync
    # ------------------------------------------------------------------

    def test_roster_sync_valid(self) -> None:
        """POST /api/v1/lea/roster/sync with valid snapshot returns 202."""
        response = self.client.post(
            '/api/v1/lea/roster/sync',
            json={
                'sync_type': 'incremental',
                'facility_name': 'Test County Detention',
                'snapshot_date': '2026-08-02T14:30:00Z',
                'inmates': [
                    {
                        'booking_number': 'BK-2026-001',
                        'full_name': 'John Doe',
                        'dob': '1985-06-15',
                        'booking_date': '2026-08-01',
                        'charges': ['45-5-202'],
                        'bond_amount': 5000.00,
                    },
                ],
            },
            content_type='application/json',
            headers={'Authorization': self.auth_header},
        )
        self.assertEqual(response.status_code, 202)
        data = json.loads(response.data)
        self.assertIn('sync_id', data)
        self.assertIn('records_received', data)
        self.assertEqual(data['records_received'], 1)

        # Verify it's in the DB
        row = self.conn.execute(
            "SELECT * FROM lea_roster_snapshots WHERE agency_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (self.agency_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row['sync_type'], 'incremental')
        self.assertEqual(row['total_inmates'], 1)

    def test_roster_sync_no_inmates(self) -> None:
        """POST /api/v1/lea/roster/sync with empty inmates list returns 400."""
        response = self.client.post(
            '/api/v1/lea/roster/sync',
            json={
                'sync_type': 'full',
                'facility_name': 'Test Jail',
                'snapshot_date': '2026-08-02T14:30:00Z',
                'inmates': [],
            },
            content_type='application/json',
            headers={'Authorization': self.auth_header},
        )
        self.assertEqual(response.status_code, 400)

    # ------------------------------------------------------------------
    # Audit Log
    # ------------------------------------------------------------------

    def test_audit_get_empty(self) -> None:
        """GET /api/v1/lea/audit returns empty list when no audit entries."""
        response = self.client.get(
            '/api/v1/lea/audit',
            headers={'Authorization': self.auth_header},
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertIn('entries', data)
        self.assertIsInstance(data['entries'], list)

    def test_audit_get_with_entries(self) -> None:
        """GET /api/v1/lea/audit returns audit entries after actions."""
        # Perform an action that creates an audit log
        self.client.post(
            '/api/v1/lea/blotter/publish',
            json={
                'incident_date': '2026-08-02',
                'cad_number': 'CAD-AUDIT-001',
                'location': '100 TEST',
                'charges': ['45-5-101'],
            },
            content_type='application/json',
            headers={'Authorization': self.auth_header},
        )

        # Fetch audit log
        response = self.client.get(
            '/api/v1/lea/audit',
            headers={'Authorization': self.auth_header},
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertIn('entries', data)
        self.assertGreater(len(data['entries']), 0)
        # Should contain the publish action
        actions = [e.get('action') for e in data['entries']]
        self.assertIn('api_blotter_publish', actions)

    def test_audit_get_filtered_by_action(self) -> None:
        """GET /api/v1/lea/audit?action=api_blotter_publish filters correctly."""
        response = self.client.get(
            '/api/v1/lea/audit?action=api_blotter_publish',
            headers={'Authorization': self.auth_header},
        )
        self.assertEqual(response.status_code, 200)

    # ------------------------------------------------------------------
    # CORS Headers
    # ------------------------------------------------------------------

    def test_cors_headers_present(self) -> None:
        """All API responses include CORS headers."""
        response = self.client.get(
            '/api/v1/lea/audit',
            headers={'Authorization': self.auth_header},
        )
        self.assertIn('Access-Control-Allow-Origin', response.headers)
        self.assertIn('Access-Control-Allow-Methods', response.headers)
        self.assertIn('Access-Control-Allow-Headers', response.headers)

    # ------------------------------------------------------------------
    # Error Response Format
    # ------------------------------------------------------------------

    def test_error_response_format(self) -> None:
        """Error responses have standardized {'error': ..., 'code': ...} format."""
        response = self.client.post(
            '/api/v1/lea/blotter/publish',
            json={},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 401)
        data = json.loads(response.data)
        self.assertIn('error', data)
        self.assertIn('code', data)

    def test_error_json_not_html(self) -> None:
        """Errors should be JSON, not HTML."""
        response = self.client.post(
            '/api/v1/lea/blotter/publish',
            json={},
            content_type='application/json',
            headers={'Authorization': 'Bearer invalid'},
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn('application/json', response.content_type or '')


if __name__ == '__main__':
    unittest.main()
