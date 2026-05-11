import os
import sqlite3
import tempfile
import unittest

import app as app_module
import config
import init_db
from services.api.auth import (
    _hash_key,
    check_rate_limit,
    create_client,
    ensure_api_auth_schema,
    generate_api_key,
    get_client_by_key_hash,
    list_clients,
    revoke_client,
    validate_request,
)


class ApiAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="mb-api-auth-", suffix=".db")
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        self.previous_app_db_path = app_module.config.DB_PATH

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        app_module.app.config["TESTING"] = True

        # Bootstrap minimal schema that api_auth expects
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        ensure_api_auth_schema(conn)
        conn.close()

        # Ensure api_auth reads from the same DB
        import api_auth as _api_auth_module
        self._original_get_db = _api_auth_module._get_db
        def _test_get_db():
            c = sqlite3.connect(self.db_path)
            c.row_factory = sqlite3.Row
            return c
        _api_auth_module._get_db = _test_get_db

    def tearDown(self) -> None:
        import api_auth as _api_auth_module
        _api_auth_module._get_db = self._original_get_db
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = self.previous_app_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_generate_api_key_format(self) -> None:
        key = generate_api_key()
        self.assertTrue(key.startswith("mb_"))
        self.assertGreater(len(key), 40)

    def test_create_and_retrieve_client(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        plaintext, client_id = create_client(conn, "Test Client", "pro")
        client = get_client_by_key_hash(conn, _hash_key(plaintext))
        self.assertIsNotNone(client)
        self.assertEqual(client.name, "Test Client")
        self.assertEqual(client.tier, "pro")
        self.assertTrue(client.is_active)
        conn.close()

    def test_revoke_client(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        plaintext, client_id = create_client(conn, "Revoke Me", "free")
        ok = revoke_client(conn, client_id)
        self.assertTrue(ok)
        client = get_client_by_key_hash(conn, _hash_key(plaintext))
        self.assertIsNotNone(client)
        self.assertFalse(client.is_active)
        conn.close()

    def test_revoke_nonexistent_client(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        ok = revoke_client(conn, 99999)
        self.assertFalse(ok)
        conn.close()

    def test_list_clients(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        create_client(conn, "A", "free")
        create_client(conn, "B", "enterprise")
        clients = list_clients(conn)
        self.assertEqual(len(clients), 2)
        names = [c.name for c in clients]
        self.assertIn("A", names)
        self.assertIn("B", names)
        conn.close()

    def test_rate_limit_free_tier(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        plaintext, client_id = create_client(conn, "Ratelimited", "free")
        client = get_client_by_key_hash(conn, _hash_key(plaintext))

        # Should be allowed initially
        allowed, headers = check_rate_limit(conn, client)
        self.assertTrue(allowed)
        self.assertIn("X-RateLimit-Limit", headers)
        self.assertIn("X-RateLimit-Remaining", headers)

        # Exhaust quota by logging max requests
        from api_auth import DEFAULT_TIERS, log_request
        max_req = DEFAULT_TIERS["free"]["max_requests"]
        for _ in range(max_req):
            log_request(conn, client.id, status_code=200)

        allowed, _ = check_rate_limit(conn, client)
        self.assertFalse(allowed)
        conn.close()

    def test_rate_limit_pro_tier_higher(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        plaintext, client_id = create_client(conn, "Pro User", "pro")
        client = get_client_by_key_hash(conn, _hash_key(plaintext))

        from api_auth import DEFAULT_TIERS, log_request
        max_req = DEFAULT_TIERS["pro"]["max_requests"]
        # Log one less than max
        for _ in range(max_req - 1):
            log_request(conn, client.id, status_code=200)

        allowed, headers = check_rate_limit(conn, client)
        self.assertTrue(allowed)
        self.assertEqual(int(headers["X-RateLimit-Remaining"]), 1)
        conn.close()


class ApiAuthIntegrationTests(unittest.TestCase):
    """Tests that exercise the Flask endpoints through the test client."""

    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="mb-api-auth-int-", suffix=".db")
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        self.previous_app_db_path = app_module.config.DB_PATH

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        app_module.app.config["TESTING"] = True

        # Seed minimal subscribers table so init_database/migrate don't crash
        # on incident_notifications migration that expects it.
        bootstrap = sqlite3.connect(self.db_path)
        bootstrap.execute(
            """
            CREATE TABLE IF NOT EXISTS subscribers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                counties TEXT DEFAULT '',
                token TEXT NOT NULL,
                active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                phone TEXT,
                wants_notifications INTEGER DEFAULT 0,
                notification_channels TEXT DEFAULT 'email'
            )
            """
        )
        bootstrap.commit()
        bootstrap.close()

        init_db.init_database()
        init_db.migrate()

        self.client = app_module.app.test_client()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = self.previous_app_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_whoami_without_key_returns_401(self) -> None:
        resp = self.client.get("/api/auth/whoami")
        self.assertEqual(resp.status_code, 401)

    def test_create_key_requires_admin_secret(self) -> None:
        import api_auth as api_auth_module
        original = api_auth_module.MB_API_ADMIN_SECRET
        api_auth_module.MB_API_ADMIN_SECRET = "configured-secret"
        try:
            resp = self.client.post("/api/auth/keys", json={"name": "Test", "tier": "free"})
            self.assertEqual(resp.status_code, 403)
        finally:
            api_auth_module.MB_API_ADMIN_SECRET = original

    def test_create_key_with_bad_secret_returns_403(self) -> None:
        import api_auth as api_auth_module
        original = api_auth_module.MB_API_ADMIN_SECRET
        api_auth_module.MB_API_ADMIN_SECRET = "configured-secret"
        try:
            resp = self.client.post(
                "/api/auth/keys",
                json={"name": "Test", "tier": "free"},
                headers={"X-Admin-Secret": "wrong"},
            )
            self.assertEqual(resp.status_code, 403)
        finally:
            api_auth_module.MB_API_ADMIN_SECRET = original

    def test_full_key_lifecycle(self) -> None:
        import api_auth as api_auth_module

        # Patch admin secret for this test
        original_secret = api_auth_module.MB_API_ADMIN_SECRET
        api_auth_module.MB_API_ADMIN_SECRET = "test-admin-secret"
        # Ensure _get_db points to app get_db (which uses the temp DB path)
        original_get_db = api_auth_module._get_db
        api_auth_module._get_db = app_module.get_db
        try:
            # Create
            resp = self.client.post(
                "/api/auth/keys",
                json={"name": "Integration Client", "tier": "pro"},
                headers={"X-Admin-Secret": "test-admin-secret"},
            )
            self.assertEqual(resp.status_code, 201)
            payload = resp.get_json()
            self.assertIn("api_key", payload)
            api_key = payload["api_key"]

            # List
            resp = self.client.get(
                "/api/auth/keys",
                headers={"X-Admin-Secret": "test-admin-secret"},
            )
            self.assertEqual(resp.status_code, 200)
            clients = resp.get_json()["clients"]
            self.assertEqual(len(clients), 1)
            self.assertEqual(clients[0]["name"], "Integration Client")
            client_id = clients[0]["id"]

            # whoami with key
            resp = self.client.get(
                "/api/auth/whoami",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.get_json()["tier"], "pro")

            # Revoke
            resp = self.client.delete(
                f"/api/auth/keys/{client_id}",
                headers={"X-Admin-Secret": "test-admin-secret"},
            )
            self.assertEqual(resp.status_code, 200)

            # whoami after revoke should 401
            resp = self.client.get(
                "/api/auth/whoami",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            self.assertEqual(resp.status_code, 401)
        finally:
            api_auth_module.MB_API_ADMIN_SECRET = original_secret
            api_auth_module._get_db = original_get_db

    def test_public_api_anonymous_rate_limit_headers(self) -> None:
        resp = self.client.get("/api/stats")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("X-RateLimit-Limit", resp.headers)
        self.assertIn("X-RateLimit-Remaining", resp.headers)

    def test_public_api_with_valid_key_rate_limit_headers(self) -> None:
        import api_auth as api_auth_module

        original_secret = api_auth_module.MB_API_ADMIN_SECRET
        api_auth_module.MB_API_ADMIN_SECRET = "test-admin-secret"
        original_get_db = api_auth_module._get_db
        api_auth_module._get_db = app_module.get_db
        try:
            resp = self.client.post(
                "/api/auth/keys",
                json={"name": "Stats User", "tier": "free"},
                headers={"X-Admin-Secret": "test-admin-secret"},
            )
            api_key = resp.get_json()["api_key"]

            resp = self.client.get(
                "/api/stats",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            self.assertEqual(resp.status_code, 200)
            self.assertIn("X-RateLimit-Limit", resp.headers)
            self.assertIn("X-RateLimit-Remaining", resp.headers)
        finally:
            api_auth_module.MB_API_ADMIN_SECRET = original_secret
            api_auth_module._get_db = original_get_db


if __name__ == "__main__":
    unittest.main()
