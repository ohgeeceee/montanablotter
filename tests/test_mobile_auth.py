import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import app as app_module
import config
import init_db


class MobileAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="mb-mobile-auth-", suffix=".db")
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        self.previous_app_db_path = app_module.config.DB_PATH

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        app_module.app.config["TESTING"] = True

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

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = self.previous_app_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_register_creates_user_and_token(self) -> None:
        client = app_module.app.test_client()
        response = client.post(
            "/api/v1/auth/register",
            json={
                "display_name": "Test User",
                "email": "test@example.com",
                "password": "securepassword123",
            },
        )
        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertIn("token", payload)
        self.assertIn("user", payload)
        self.assertEqual(payload["user"]["email"], "test@example.com")
        self.assertEqual(payload["user"]["display_name"], "Test User")

    def test_register_rejects_duplicate_email(self) -> None:
        client = app_module.app.test_client()
        client.post(
            "/api/v1/auth/register",
            json={
                "display_name": "Test User",
                "email": "test@example.com",
                "password": "securepassword123",
            },
        )
        response = client.post(
            "/api/v1/auth/register",
            json={
                "display_name": "Another User",
                "email": "test@example.com",
                "password": "anotherpassword123",
            },
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"], "email_already_registered")

    def test_login_returns_token(self) -> None:
        client = app_module.app.test_client()
        client.post(
            "/api/v1/auth/register",
            json={
                "display_name": "Test User",
                "email": "test@example.com",
                "password": "securepassword123",
            },
        )
        response = client.post(
            "/api/v1/auth/login",
            json={
                "email": "test@example.com",
                "password": "securepassword123",
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("token", payload)
        self.assertEqual(payload["user"]["email"], "test@example.com")

    def test_login_rejects_invalid_password(self) -> None:
        client = app_module.app.test_client()
        client.post(
            "/api/v1/auth/register",
            json={
                "display_name": "Test User",
                "email": "test@example.com",
                "password": "securepassword123",
            },
        )
        response = client.post(
            "/api/v1/auth/login",
            json={
                "email": "test@example.com",
                "password": "wrongpassword",
            },
        )
        self.assertEqual(response.status_code, 401)

    def test_me_requires_auth(self) -> None:
        client = app_module.app.test_client()
        response = client.get("/api/v1/auth/me")
        self.assertEqual(response.status_code, 401)

    def test_me_returns_authenticated_user(self) -> None:
        client = app_module.app.test_client()
        register_response = client.post(
            "/api/v1/auth/register",
            json={
                "display_name": "Test User",
                "email": "test@example.com",
                "password": "securepassword123",
            },
        )
        token = register_response.get_json()["token"]
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["user"]["email"], "test@example.com")

    def test_logout_revokes_token(self) -> None:
        client = app_module.app.test_client()
        register_response = client.post(
            "/api/v1/auth/register",
            json={
                "display_name": "Test User",
                "email": "test@example.com",
                "password": "securepassword123",
            },
        )
        token = register_response.get_json()["token"]
        logout_response = client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(logout_response.status_code, 200)

        me_response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(me_response.status_code, 401)

    @mock.patch("services.monetization.revenuecat.fetch_subscriber")
    def test_verify_purchase_activates_premium(self, mock_fetch_subscriber) -> None:
        mock_fetch_subscriber.return_value = {
            "subscriber": {
                "entitlements": {
                    "premium": {"is_active": True},
                }
            }
        }
        client = app_module.app.test_client()
        register_response = client.post(
            "/api/v1/auth/register",
            json={
                "display_name": "Test User",
                "email": "test@example.com",
                "password": "securepassword123",
            },
        )
        token = register_response.get_json()["token"]
        response = client.post(
            "/api/v1/purchases/verify",
            json={"app_user_id": "test-user-123"},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["is_premium"])

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT subscriber_plan FROM public_users WHERE email = ?",
            ("test@example.com",),
        ).fetchone()
        conn.close()
        self.assertEqual(row["subscriber_plan"], "insider")


if __name__ == "__main__":
    unittest.main()
