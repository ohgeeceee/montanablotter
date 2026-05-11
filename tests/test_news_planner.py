import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import config
import init_db


class NewsPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="mb-news-planner-", suffix=".db")
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path

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
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_init_db_creates_autonomous_newsroom_tables(self) -> None:
        conn = sqlite3.connect(self.db_path)
        table_names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        conn.close()

        self.assertIn("story_candidates", table_names)
        self.assertIn("blog_draft_reviews", table_names)
        self.assertIn("blog_post_sources", table_names)

    def test_build_dedupe_key_is_deterministic(self) -> None:
        from services.publishing.news_planner import build_dedupe_key

        packet = {
            "headline_hint": "Missoula County deputies investigate overnight burglary",
            "location_label": "Missoula County",
            "occurred_at": "2026-04-25T01:00:00Z",
            "source_url": "https://montanablotter.com/example",
        }

        self.assertEqual(build_dedupe_key(packet), build_dedupe_key(packet))

    def test_normalize_candidate_packet_returns_required_fields(self) -> None:
        from services.publishing.news_planner import normalize_candidate_packet

        packet = normalize_candidate_packet(
            source_type="montanablotter",
            source_url="https://montanablotter.com/example",
            headline_hint="Gallatin County deputies respond to burglary report",
            facts={"county": "Gallatin", "incident_type": "Burglary"},
        )

        self.assertEqual(packet["status"], "new")
        self.assertEqual(packet["source_type"], "montanablotter")
        self.assertTrue(packet["dedupe_key"])

    def test_plan_candidates_inserts_only_new_rows(self) -> None:
        from services.publishing.news_planner import persist_candidate

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        packet = {
            "source_type": "montanablotter",
            "source_url": "https://montanablotter.com/example",
            "headline_hint": "Helena police investigate reported assault",
            "facts_json": "{}",
            "location_label": "Helena",
            "occurred_at": "2026-04-25T02:00:00Z",
            "agency_name": "Helena Police Department",
            "status": "new",
            "score": 10,
            "dedupe_key": "abc123",
        }

        first_id = persist_candidate(conn, packet)
        second_id = persist_candidate(conn, packet)
        count = conn.execute("SELECT COUNT(*) FROM story_candidates").fetchone()[0]
        conn.close()

        self.assertEqual(first_id, second_id)
        self.assertEqual(count, 1)

    def test_fetch_internal_story_packets_returns_clean_posts(self) -> None:
        from services.publishing.news_planner import fetch_internal_story_packets

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        blotter_id = conn.execute(
            """
            INSERT INTO blotters (filename, county, status, file_path, source_type, upload_date)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("planner.pdf", "Gallatin", "processed", "/tmp/planner.pdf", "local_pdf", "2026-04-25"),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO posts (
                blotter_id, title, summary, city, county, agency_type, agency_name,
                incident_date, incident_type, created_at, audit_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                blotter_id,
                "Bozeman police investigate theft report",
                "Police are investigating a theft report.",
                "Bozeman",
                "Gallatin",
                "police",
                "Bozeman Police Department",
                "2026-04-25",
                "Theft",
                "2026-04-25 01:00:00",
                "clean",
            ),
        )
        conn.commit()

        packets = fetch_internal_story_packets(conn)
        conn.close()

        self.assertEqual(len(packets), 1)
        self.assertEqual(packets[0]["source_type"], "montanablotter")
        self.assertIn("Bozeman police investigate theft report", packets[0]["headline_hint"])

    def test_fetch_external_story_packets_parses_rss_entries(self) -> None:
        from services.publishing.news_planner import fetch_external_story_packets

        rss_body = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel>
            <item>
              <title>Montana Supreme Court releases daily orders</title>
              <link>https://courts.mt.gov/external/orders/dailyorders/2026-04-25</link>
              <pubDate>Fri, 25 Apr 2026 03:00:00 GMT</pubDate>
              <description>The court published the latest statewide daily orders.</description>
            </item>
          </channel>
        </rss>
        """

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            handle.write(
                '[{"source_type":"montana_public_records","source_url":"https://courts.mt.gov/external/orders/rss","format":"rss"}]'
            )
            config_path = handle.name

        class FakeResponse:
            status_code = 200
            text = rss_body

            def raise_for_status(self) -> None:
                return None

        try:
            with mock.patch("news_planner.EXTERNAL_SOURCE_PATH", config_path):
                with mock.patch("news_planner.requests.get", return_value=FakeResponse()):
                    packets = fetch_external_story_packets()
        finally:
            os.unlink(config_path)

        self.assertEqual(len(packets), 1)
        self.assertEqual(packets[0]["source_type"], "montana_public_records")
        self.assertIn("Montana Supreme Court releases daily orders", packets[0]["headline_hint"])

    def test_fetch_external_story_packets_skips_malformed_rss(self) -> None:
        from services.publishing.news_planner import fetch_external_story_packets

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            handle.write(
                '[{"source_type":"montana_public_records","source_url":"https://courts.mt.gov/external/orders/rss","format":"rss"}]'
            )
            config_path = handle.name

        class FakeResponse:
            status_code = 200
            text = "<html>not xml</html>"

            def raise_for_status(self) -> None:
                return None

        try:
            with mock.patch("news_planner.EXTERNAL_SOURCE_PATH", config_path):
                with mock.patch("news_planner.requests.get", return_value=FakeResponse()):
                    packets = fetch_external_story_packets()
        finally:
            os.unlink(config_path)

        self.assertEqual(packets, [])

    def test_crontab_includes_newsroom_jobs(self) -> None:
        with open("/root/montanablotter/crontab.txt", "r", encoding="utf-8") as handle:
            content = handle.read()

        self.assertIn("news_planner", content)
        self.assertIn("news_writer_agent", content)
        self.assertIn("news_editor_agent", content)

    def test_watchdog_monitors_newsroom_logs(self) -> None:
        import script_watchdog

        monitored = {job.name for job in script_watchdog.JOBS}
        self.assertIn("news_planner", monitored)
        self.assertIn("news_writer_agent", monitored)
        self.assertIn("news_editor_agent", monitored)


if __name__ == "__main__":
    unittest.main()
