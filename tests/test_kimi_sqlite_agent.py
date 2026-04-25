import os
import sqlite3
import tempfile
import unittest


class KimiSqliteAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="mb-kimi-agent-", suffix=".db")
        os.close(fd)
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            CREATE TABLE records (
                id INTEGER PRIMARY KEY,
                county TEXT,
                incident_type TEXT,
                incident TEXT,
                details TEXT,
                location TEXT,
                created_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE missing_persons (
                id INTEGER PRIMARY KEY,
                full_name TEXT,
                status TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE missing_person_source_stats (
                id INTEGER PRIMARY KEY,
                total_active INTEGER,
                children INTEGER,
                indigenous INTEGER,
                synced_at TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO records (id, county, incident_type, incident, details, location, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "Yellowstone", "Theft", "Vehicle theft", "Truck stolen from lot", "Billings", "2026-04-23 10:00:00"),
                (2, "Yellowstone", "Assault", "Simple assault", "Fight reported downtown", "Billings", "2026-04-23 11:00:00"),
                (3, "Gallatin", "Burglary", "Commercial burglary", "Store break-in", "Bozeman", "2026-04-23 09:00:00"),
            ],
        )
        conn.executemany(
            "INSERT INTO missing_persons (id, full_name, status) VALUES (?, ?, ?)",
            [
                (1, "Alice Example", "missing"),
                (2, "Bob Example", "located"),
                (3, "Carol Example", "missing"),
            ],
        )
        conn.execute(
            """
            INSERT INTO missing_person_source_stats (id, total_active, children, indigenous, synced_at)
            VALUES (1, 2, 1, 1, '2026-04-23 19:14:33')
            """
        )
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_search_records_filters_by_county_and_keyword(self) -> None:
        import admin_ai as agent

        result = agent.run_registered_tool(
            "search_records",
            {"county": "Yellowstone", "keyword": "theft", "limit": 5},
            db_path=self.db_path,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], 1)
        self.assertEqual(result[0]["county"], "Yellowstone")

    def test_kimi_sqlite_agent_delegates_run_tool_to_shared_service(self) -> None:
        import kimi_sqlite_agent as agent

        result = agent.run_tool(
            "search_records",
            {"county": "Yellowstone", "keyword": "theft", "limit": 5},
            db_path=self.db_path,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], 1)

    def test_get_missing_persons_summary_returns_counts_and_stats(self) -> None:
        import admin_ai as agent

        result = agent.run_registered_tool(
            "get_missing_persons_summary",
            {},
            db_path=self.db_path,
        )

        counts = {row["status"]: row["total"] for row in result["counts_by_status"]}
        self.assertEqual(counts["missing"], 2)
        self.assertEqual(counts["located"], 1)
        self.assertEqual(result["source_stats"]["total_active"], 2)
        self.assertEqual(result["source_stats"]["children"], 1)


if __name__ == "__main__":
    unittest.main()
