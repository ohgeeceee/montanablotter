import sqlite3
import unittest

from services.admin.case_journeys import ensure_case_journey_schema, seed_case_journeys


class CaseJourneyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                blotter_id INTEGER,
                title TEXT
            );
            CREATE TABLE records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                blotter_id INTEGER NOT NULL,
                cfs_number TEXT,
                date TEXT NOT NULL,
                time TEXT,
                incident TEXT,
                incident_type TEXT,
                location TEXT,
                details TEXT,
                county TEXT NOT NULL,
                officer TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE command_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                record_id INTEGER NOT NULL,
                timestamp TEXT,
                officer TEXT,
                entry TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            """
        )

    def tearDown(self) -> None:
        self.conn.close()

    def test_seed_case_journeys_creates_journey_and_events(self) -> None:
        ensure_case_journey_schema(self.conn)
        self.conn.execute(
            "INSERT INTO posts (blotter_id, title) VALUES (?, ?)",
            (10, "Daily Activity Report - Billings Police Department"),
        )
        self.conn.execute(
            """
            INSERT INTO records (blotter_id, cfs_number, date, time, incident_type, location, details, county)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (10, "CFS26-1001", "03/14/26", "08:00", "Burglary", "123 Main St", "Initial burglary report", "Yellowstone"),
        )
        record_id = self.conn.execute("SELECT id FROM records").fetchone()["id"]
        self.conn.executemany(
            "INSERT INTO command_logs (record_id, timestamp, officer, entry) VALUES (?, ?, ?, ?)",
            [
                (record_id, "2026-03-14 08:30:00", "D12", "Caller contacted for supplemental details."),
                (record_id, "2026-03-14 09:00:00", "D12", "Scene cleared after evidence collection."),
            ],
        )

        created = seed_case_journeys(self.conn)

        journey = self.conn.execute(
            "SELECT * FROM case_journeys"
        ).fetchone()
        event_count = self.conn.execute(
            "SELECT COUNT(*) AS cnt FROM case_journey_events WHERE journey_id = ?",
            (journey["id"],),
        ).fetchone()["cnt"]

        self.assertEqual(created, 1)
        self.assertEqual(journey["title"], "Burglary")
        self.assertEqual(journey["link_confidence_label"], "exact")
        self.assertEqual(event_count, 3)

    def test_seed_case_journeys_is_idempotent(self) -> None:
        ensure_case_journey_schema(self.conn)
        self.conn.execute(
            """
            INSERT INTO records (blotter_id, cfs_number, date, incident_type, county)
            VALUES (?, ?, ?, ?, ?)
            """,
            (3, "", "03/14/26", "Welfare Check", "Cascade"),
        )
        record_id = self.conn.execute("SELECT id FROM records").fetchone()["id"]
        self.conn.execute(
            "INSERT INTO command_logs (record_id, timestamp, officer, entry) VALUES (?, ?, ?, ?)",
            (record_id, "2026-03-14 10:00:00", "D10", "Officer completed follow-up."),
        )

        first = seed_case_journeys(self.conn)
        second = seed_case_journeys(self.conn)
        total = self.conn.execute(
            "SELECT COUNT(*) AS cnt FROM case_journeys"
        ).fetchone()["cnt"]

        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertEqual(total, 1)


if __name__ == "__main__":
    unittest.main()
