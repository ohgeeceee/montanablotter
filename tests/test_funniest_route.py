"""Tests for the public /funniest route and /funniest.json API."""
import os
import sqlite3
import tempfile
import unittest

os.environ.setdefault("MB_REQUIRE_SIGNIN", "false")

import app as app_module
import config
import init_db
from services.blotter.humor import score_humor


class FunniestFeedTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="mb-funny-", suffix=".db")
        os.close(fd)
        self.prev_cfg = config.DB_PATH
        self.prev_init = init_db.DB_PATH
        self.prev_app = app_module.config.DB_PATH

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        app_module.app.config["TESTING"] = True

        init_db.init_database()
        init_db.migrate()

        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._seed()
        self.client = app_module.app.test_client()

    def tearDown(self) -> None:
        self.conn.close()
        config.DB_PATH = self.prev_cfg
        init_db.DB_PATH = self.prev_init
        app_module.config.DB_PATH = self.prev_app
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _seed(self) -> None:
        rows = [
            # eligible + funny -> positive score, should appear
            ("goose chased a man down Elm Street", "loud honking", "suspicious", "cascade", "2026-01-02"),
            # eligible but not funny -> score 0, excluded by query
            ("traffic stop conducted at the intersection with no further action taken", "", "traffic", "cascade", "2026-01-03"),
            # denied type -> must never appear even if scored
            ("domestic disturbance reported", "argument", "domestic_violence", "cascade", "2026-01-04"),
            # funny but contains PII -> must be redacted, not excluded
            ("goat stuck in mailbox, call 406-555-1234", "", "traffic", "cascade", "2026-01-05"),
        ]
        for incident, details, itype, county, date in rows:
            s = score_humor(incident, details, itype)
            self.conn.execute(
                "INSERT INTO records (blotter_id, incident, details, incident_type, county, date, humor_score) "
                "VALUES (1,?,?,?,?,?,?)",
                (incident, details, itype, county, date, s),
            )
        self.conn.commit()

    def test_page_is_public_and_200(self):
        r = self.client.get("/funniest")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn("Funniest Police Blotters", html)

    def test_denied_type_excluded(self):
        html = self.client.get("/funniest").get_data(as_text=True)
        self.assertNotIn("domestic disturbance", html)

    def test_non_funny_excluded(self):
        html = self.client.get("/funniest").get_data(as_text=True)
        self.assertNotIn("routine traffic stop", html)

    def test_pii_redacted_in_feed(self):
        html = self.client.get("/funniest").get_data(as_text=True)
        self.assertNotIn("406-555-1234", html)
        self.assertIn("goat stuck in mailbox", html)

    def test_json_api_shape(self):
        r = self.client.get("/funniest.json")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertIn("items", data)
        self.assertIn("page", data)
        self.assertIn("total", data)
        self.assertGreaterEqual(data["total"], 2)  # goose + goat (redacted)
        # every JSON item must have redacted fields, no raw phone
        for it in data["items"]:
            self.assertNotIn("406-555-1234", it["incident"])
            self.assertIn("id", it)
            self.assertIn("share_url", it)

    def test_pagination_param(self):
        r1 = self.client.get("/funniest?page=1")
        self.assertEqual(r1.status_code, 200)
        # bad page falls back to 1, not 500
        rbad = self.client.get("/funniest?page=abc")
        self.assertEqual(rbad.status_code, 200)

    def test_json_ld_present(self):
        html = self.client.get("/funniest").get_data(as_text=True)
        self.assertIn("CollectionPage", html)
        self.assertIn("https://montanablotter.com/funniest", html)

    def test_copy_link_button_renders(self):
        html = self.client.get("/funniest").get_data(as_text=True)
        self.assertIn("mb-copy-link", html)
        self.assertIn("data-url=", html)

    def test_empty_state_renders(self):
        self.conn.execute("DELETE FROM records")
        self.conn.commit()
        html = self.client.get("/funniest").get_data(as_text=True)
        self.assertIn("No howlers", html)


if __name__ == "__main__":
    unittest.main()
