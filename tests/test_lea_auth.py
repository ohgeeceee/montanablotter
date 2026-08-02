"""Tests for LEA Auth Module (Phase 2).

Tests: user_auth (bcrypt), agency_verification (ORI/email), api_tokens (JWT), invitations.
"""

import os
import sqlite3
import tempfile
import time
import unittest

import config
import init_db

from services.lea_auth.user_auth import hash_password, verify_password
from services.lea_auth.agency_verification import (
    verify_email_domain,
    verify_ori_number,
)
from services.lea_auth.api_tokens import (
    generate_token,
    hash_token,
    create_jwt,
    verify_jwt,
)
from services.lea_auth.invitations import (
    create_invitation,
    get_invitation,
    accept_invitation,
)


class TestLEAUserAuth(unittest.TestCase):
    """Tests for bcrypt password hashing (user_auth.py)."""

    def test_hash_password(self) -> None:
        password = "SecurePass123!"
        hashed = hash_password(password)
        self.assertNotEqual(hashed, password)
        self.assertEqual(len(hashed), 60)  # bcrypt hash length

    def test_verify_password_correct(self) -> None:
        password = "SecurePass123!"
        hashed = hash_password(password)
        self.assertTrue(verify_password(password, hashed))

    def test_verify_password_incorrect(self) -> None:
        password = "SecurePass123!"
        hashed = hash_password(password)
        self.assertFalse(verify_password("WrongPassword", hashed))


class TestLEAAgencyVerification(unittest.TestCase):
    """Tests for email domain and ORI verification (agency_verification.py)."""

    def test_verify_email_domain_gov(self) -> None:
        """Verify that .gov emails are allowed."""
        self.assertTrue(verify_email_domain("officer@gfpd.gov"))
        self.assertTrue(verify_email_domain("sheriff@cascadecountymt.gov"))

    def test_verify_email_domain_non_gov_rejected(self) -> None:
        """Verify that non-.gov emails are rejected."""
        self.assertFalse(verify_email_domain("officer@gmail.com"))
        self.assertFalse(verify_email_domain("officer@gfpd.com"))

    def test_verify_email_domain_no_at(self) -> None:
        """No @ sign should be rejected."""
        self.assertFalse(verify_email_domain("notanemail"))

    def test_verify_ori_number_format(self) -> None:
        """ORI numbers should be 9 chars: 2 letters + 7 digits."""
        self.assertTrue(verify_ori_number("MT0120100"))  # Valid format
        self.assertFalse(verify_ori_number("MT012"))     # Too short
        self.assertFalse(verify_ori_number("INVALID!"))  # Invalid chars

    def test_verify_ori_number_none(self) -> None:
        """None/falsy values should be rejected."""
        self.assertFalse(verify_ori_number(""))
        self.assertFalse(verify_ori_number(None))  # type: ignore


class TestLEAAPITokens(unittest.TestCase):
    """Tests for token generation, hashing, and JWT (api_tokens.py)."""

    def test_generate_token_length(self) -> None:
        """Generated tokens should be URL-safe base64."""
        token = generate_token()
        self.assertEqual(len(token), 43)  # 32 bytes -> base64url

    def test_generate_token_unique(self) -> None:
        """Consecutive tokens should be different."""
        tokens = {generate_token() for _ in range(10)}
        self.assertEqual(len(tokens), 10)

    def test_hash_token_deterministic(self) -> None:
        """Same token should always produce the same hash."""
        token = generate_token()
        h1 = hash_token(token)
        h2 = hash_token(token)
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)  # SHA-256 hexdigest

    def test_hash_token_different_inputs(self) -> None:
        """Different tokens produce different hashes."""
        t1 = generate_token()
        t2 = generate_token()
        self.assertNotEqual(hash_token(t1), hash_token(t2))

    def test_create_and_verify_jwt(self) -> None:
        """Create a JWT and verify it returns the original payload."""
        payload = {"sub": "user_42", "agency_id": 1, "scopes": ["blotter.publish"]}
        secret = "test-secret-key"
        token = create_jwt(payload, secret, expiry_hours=1)
        decoded = verify_jwt(token, secret)
        self.assertEqual(decoded["sub"], "user_42")
        self.assertEqual(decoded["agency_id"], 1)
        self.assertEqual(decoded["scopes"], ["blotter.publish"])
        self.assertIn("iat", decoded)
        self.assertIn("exp", decoded)

    def test_verify_jwt_bad_secret(self) -> None:
        """Verifying with wrong secret should raise."""
        payload = {"sub": "test"}
        token = create_jwt(payload, "correct-secret", expiry_hours=1)
        with self.assertRaises(Exception):
            verify_jwt(token, "wrong-secret")

    def test_verify_jwt_expired(self) -> None:
        """Expired token should raise ExpiredSignatureError."""
        payload = {"sub": "test"}
        token = create_jwt(payload, "secret", expiry_hours=0)  # expires immediately
        time.sleep(1)  # Ensure we're past expiry
        with self.assertRaises(Exception):
            verify_jwt(token, "secret")


class TestLEAInvitations(unittest.TestCase):
    """Tests for invitation creation and acceptance flow (invitations.py)."""

    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-lea-auth-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path

        conn = sqlite3.connect(self.db_path)
        conn.execute('PRAGMA foreign_keys = ON')
        conn.row_factory = sqlite3.Row
        init_db.ensure_lea_schema(conn)

        # Create a test agency
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO lea_agencies (org_name, agency_type, county_slug, county_name, primary_contact_email) VALUES (?, ?, ?, ?, ?)",
            ("Test PD", "police", "test", "Test", "admin@test.gov"),
        )
        conn.commit()
        self.conn = conn
        self.agency_id = cursor.lastrowid

    def tearDown(self) -> None:
        self.conn.close()
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_create_invitation(self) -> None:
        """Creating an invitation returns a dict with id, email, role, token."""
        result = create_invitation(
            self.conn,
            agency_id=self.agency_id,
            email="officer@test.gov",
            role="records_officer",
            invited_by_user_id=None,
        )
        self.assertIn("id", result)
        self.assertIn("token", result)
        self.assertEqual(result["email"], "officer@test.gov")
        self.assertEqual(result["role"], "records_officer")
        self.assertIsInstance(result["token"], str)
        self.assertEqual(len(result["token"]), 43)  # token_urlsafe(32)

    def test_create_invitation_stores_in_db(self) -> None:
        """Invitation should be stored in lea_invitations table."""
        result = create_invitation(
            self.conn,
            agency_id=self.agency_id,
            email="officer@test.gov",
            role="pio",
            invited_by_user_id=None,
        )
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM lea_invitations WHERE id = ?", (result["id"],))
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["email"], "officer@test.gov")

    def test_get_invitation_valid(self) -> None:
        """get_invitation should return the invitation for a valid token."""
        result = create_invitation(
            self.conn,
            agency_id=self.agency_id,
            email="officer@test.gov",
            role="records_officer",
            invited_by_user_id=None,
        )
        inv = get_invitation(self.conn, result["token"])
        self.assertIsNotNone(inv)
        self.assertEqual(inv["email"], "officer@test.gov")

    def test_get_invitation_invalid_token(self) -> None:
        """get_invitation should return None for an invalid token."""
        inv = get_invitation(self.conn, "nonexistent-token")
        self.assertIsNone(inv)

    def test_accept_invitation_creates_user(self) -> None:
        """Accepting a valid invitation creates a user in lea_users."""
        inv_result = create_invitation(
            self.conn,
            agency_id=self.agency_id,
            email="officer@test.gov",
            role="records_officer",
            invited_by_user_id=None,
        )
        from services.lea_auth.user_auth import hash_password

        accept_result = accept_invitation(
            self.conn,
            inv_result["token"],
            hash_password("MyNewPass123!"),
        )
        self.assertIn("user_id", accept_result)
        self.assertEqual(accept_result["agency_id"], self.agency_id)
        self.assertEqual(accept_result["email"], "officer@test.gov")
        self.assertEqual(accept_result["role"], "records_officer")

        # Verify user exists in DB
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM lea_users WHERE id = ?", (accept_result["user_id"],))
        user = cursor.fetchone()
        self.assertIsNotNone(user)
        self.assertEqual(user["email"], "officer@test.gov")

    def test_accept_invitation_marks_accepted(self) -> None:
        """After accepting, the invitation should have accepted_at set."""
        inv_result = create_invitation(
            self.conn,
            agency_id=self.agency_id,
            email="officer@test.gov",
            role="records_officer",
            invited_by_user_id=None,
        )
        from services.lea_auth.user_auth import hash_password

        accept_invitation(self.conn, inv_result["token"], hash_password("MyNewPass123!"))

        # Token should no longer work
        inv = get_invitation(self.conn, inv_result["token"])
        self.assertIsNone(inv)

    def test_accept_invitation_invalid_token(self) -> None:
        """Accepting with an invalid token should raise."""
        with self.assertRaises(ValueError):
            accept_invitation(self.conn, "bad-token", "somehash")
