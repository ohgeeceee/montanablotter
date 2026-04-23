import os
import sqlite3
import tempfile
import unittest

import init_db


class InitDbMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="mb-init-db-", suffix=".db")
        os.close(fd)
        self.previous_db_path = init_db.DB_PATH
        init_db.DB_PATH = self.db_path

    def tearDown(self) -> None:
        init_db.DB_PATH = self.previous_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_migrate_preserves_posts_when_record_id_becomes_nullable(self) -> None:
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
        conn = sqlite3.connect(self.db_path)
        conn.execute("DROP TABLE IF EXISTS posts")
        conn.execute(
            """
            CREATE TABLE posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                record_id INTEGER NOT NULL,
                blotter_id INTEGER NOT NULL,
                title TEXT,
                summary TEXT,
                city TEXT,
                county TEXT,
                agency_type TEXT DEFAULT 'other',
                agency_name TEXT,
                incident_date TEXT,
                incident_type TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                audit_status TEXT DEFAULT 'pending'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO blotters (id, filename, county, upload_date, incident_count, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (456, "legacy.pdf", "Yellowstone", "2026-04-21", 1, "processed"),
        )
        conn.execute(
            """
            INSERT INTO posts (
                record_id, blotter_id, title, summary, city, county, agency_type,
                agency_name, incident_date, incident_type, audit_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                123,
                456,
                "Legacy post",
                "Legacy summary",
                "Billings",
                "Yellowstone",
                "police",
                "Billings Police Department",
                "2026-04-21",
                "Daily Digest",
                "clean",
            ),
        )
        conn.commit()
        conn.close()

        init_db.migrate()

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        record_id_col = conn.execute(
            "SELECT [notnull] FROM pragma_table_info('posts') WHERE name='record_id'"
        ).fetchone()
        row = conn.execute(
            "SELECT title, audit_status FROM posts WHERE title = 'Legacy post'"
        ).fetchone()
        conn.close()

        self.assertEqual(record_id_col[0], 0)
        self.assertIsNotNone(row)
        self.assertEqual(row["audit_status"], "clean")


if __name__ == "__main__":
    unittest.main()
