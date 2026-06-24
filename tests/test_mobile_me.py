import os
import sqlite3
import tempfile
import unittest

import app as app_module
import config
import init_db


class MobileMeTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="mb-mobile-me-", suffix=".db")
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

        self.client = app_module.app.test_client()
        register_response = self.client.post(
            "/api/v1/auth/register",
            json={
                "display_name": "Test User",
                "email": "test@example.com",
                "password": "securepassword123",
            },
        )
        self.token = register_response.get_json()["token"]
        self.user_id = register_response.get_json()["user"]["id"]
        self._seed_post()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = self.previous_app_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _seed_post(self) -> None:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO blotters (filename, county, status, file_path, source_type, upload_date)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("report.pdf", "Yellowstone", "processed", "/tmp/report.pdf", "local_pdf", "2026-04-21"),
        )
        blotter_id = cursor.lastrowid
        cursor.execute(
            """
            INSERT INTO posts (
                blotter_id, title, summary, city, county, agency_type, agency_name,
                incident_date, incident_type, created_at, audit_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                blotter_id,
                "Test Post",
                "Summary.",
                "Billings",
                "Yellowstone",
                "police",
                "Billings Police Department",
                "2026-04-21",
                "Daily Digest",
                "2026-04-21 08:00:00",
                "clean",
            ),
        )
        self.post_id = cursor.lastrowid
        conn.commit()
        conn.close()

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def test_watchlist_requires_auth(self) -> None:
        response = self.client.get("/api/me/watchlist")
        self.assertEqual(response.status_code, 401)

    def test_watchlist_add_and_list(self) -> None:
        add_response = self.client.post(
            "/api/me/watchlist",
            json={"post_id": self.post_id},
            headers=self._auth_headers(),
        )
        self.assertEqual(add_response.status_code, 201)

        list_response = self.client.get("/api/me/watchlist", headers=self._auth_headers())
        self.assertEqual(list_response.status_code, 200)
        posts = list_response.get_json()["posts"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["id"], self.post_id)

    def test_watchlist_remove(self) -> None:
        self.client.post(
            "/api/me/watchlist",
            json={"post_id": self.post_id},
            headers=self._auth_headers(),
        )
        remove_response = self.client.delete(
            f"/api/me/watchlist/{self.post_id}",
            headers=self._auth_headers(),
        )
        self.assertEqual(remove_response.status_code, 200)

        list_response = self.client.get("/api/me/watchlist", headers=self._auth_headers())
        self.assertEqual(list_response.get_json()["posts"], [])

    def test_alert_profiles_crud(self) -> None:
        create_response = self.client.post(
            "/api/me/alert-profiles",
            json={"name": "Test Alert", "counties": ["Yellowstone"]},
            headers=self._auth_headers(),
        )
        self.assertEqual(create_response.status_code, 201)
        profile_id = create_response.get_json()["id"]

        list_response = self.client.get("/api/me/alert-profiles", headers=self._auth_headers())
        self.assertEqual(list_response.status_code, 200)
        profiles = list_response.get_json()["profiles"]
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["counties"], ["Yellowstone"])

        update_response = self.client.put(
            f"/api/me/alert-profiles/{profile_id}",
            json={"counties": ["Gallatin"]},
            headers=self._auth_headers(),
        )
        self.assertEqual(update_response.status_code, 200)

        list_response = self.client.get("/api/me/alert-profiles", headers=self._auth_headers())
        profiles = list_response.get_json()["profiles"]
        self.assertEqual(profiles[0]["counties"], ["Gallatin"])

        delete_response = self.client.delete(
            f"/api/me/alert-profiles/{profile_id}",
            headers=self._auth_headers(),
        )
        self.assertEqual(delete_response.status_code, 200)

        list_response = self.client.get("/api/me/alert-profiles", headers=self._auth_headers())
        self.assertEqual(list_response.get_json()["profiles"], [])


if __name__ == "__main__":
    unittest.main()
