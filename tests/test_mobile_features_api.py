"""
Tests for Phase 3/4 mobile feature APIs:
- /api/health
- /api/v1/court/lookup
- /api/v1/warrants
- /api/v1/missing-persons
- /api/v1/push/register
"""

import os
import tempfile
import unittest

import app as app_module
import config
import init_db


class MobileFeaturesApiTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="mb-mobile-features-", suffix=".db")
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        self.previous_app_db_path = app_module.config.DB_PATH

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        app_module.app.config["TESTING"] = True

        init_db.init_database()
        init_db.migrate()
        self._seed_data()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = self.previous_app_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _seed_data(self) -> None:
        conn = app_module.get_db()
        try:
            # Court case
            conn.execute(
                """
                INSERT INTO courts (name, slug, county, court_type, created_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                """,
                ("Yellowstone County Justice Court", "yellowstone-county-justice-court", "Yellowstone", "justice"),
            )
            court_id = conn.execute("SELECT id FROM courts WHERE name = ?", ("Yellowstone County Justice Court",)).fetchone()["id"]
            conn.execute(
                """
                INSERT INTO court_cases (
                    court_id, case_number, slug, caption, defendant_name, defendant_slug,
                    defendant_last, defendant_first, case_type, status, is_criminal,
                    filed_date, charges_text, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    court_id,
                    "CR-2024-1234",
                    "cr-2024-1234",
                    "State v. John Doe",
                    "John Doe",
                    "john-doe",
                    "doe",
                    "john",
                    "criminal",
                    "open",
                    1,
                    "2024-01-15",
                    "Theft",
                ),
            )

            # Warrant
            conn.execute(
                """
                INSERT INTO warrants (
                    source_record_id, county, person_name, warrant_type,
                    charges_text, status, scraped_at, first_seen_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'), datetime('now'))
                """,
                (
                    "rosebud-warrant:john-doe",
                    "Rosebud",
                    "John Doe",
                    "arrest",
                    "Bench warrant",
                    "active",
                ),
            )

            # Missing person
            conn.execute(
                """
                INSERT INTO missing_persons (
                    full_name, slug, status, age, county, city,
                    last_seen_location, summary, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                (
                    "Jane Doe",
                    "jane-doe",
                    "missing",
                    30,
                    "Yellowstone",
                    "Billings",
                    "Billings",
                    "Missing person summary",
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def test_health_endpoint(self) -> None:
        client = app_module.app.test_client()
        response = client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["database"], "ok")
        self.assertIn("timestamp", payload)

    def test_court_lookup_by_name(self) -> None:
        client = app_module.app.test_client()
        response = client.get("/api/v1/court/lookup?name=John%20Doe")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["match_count"], 1)
        self.assertEqual(payload["matches"][0]["person"]["name"], "John Doe")
        self.assertEqual(payload["matches"][0]["court_cases"][0]["case_number"], "CR-2024-1234")

    def test_warrants_endpoint(self) -> None:
        client = app_module.app.test_client()
        response = client.get("/api/v1/warrants")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(len(payload["warrants"]), 1)
        self.assertEqual(payload["warrants"][0]["person_name"], "John Doe")
        self.assertEqual(payload["total"], 1)

    def test_missing_persons_endpoint(self) -> None:
        client = app_module.app.test_client()
        response = client.get("/api/v1/missing-persons")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(len(payload["people"]), 1)
        self.assertEqual(payload["people"][0]["full_name"], "Jane Doe")
        self.assertEqual(payload["total_active"], 1)

    def test_push_register_endpoint(self) -> None:
        client = app_module.app.test_client()
        response = client.post(
            "/api/v1/push/register",
            json={
                "expo_push_token": "ExponentPushToken[xxxxxxxxxxxxxxxxxxxxxx]",
                "platform": "ios",
                "device_id": "test-device",
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["registered"])
        self.assertIn("token_id", payload)


if __name__ == "__main__":
    unittest.main()
