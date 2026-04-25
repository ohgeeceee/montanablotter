import json
import os
import sqlite3
import tempfile
import unittest

import config
import init_db


class NewsWriterAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="mb-news-writer-", suffix=".db")
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

    def test_create_draft_from_candidate_inserts_unpublished_blog_post(self) -> None:
        from news_writer_agent import create_draft_from_candidate

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            INSERT INTO story_candidates (
                source_type, source_url, headline_hint, facts_json, dedupe_key, status, score
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "montanablotter",
                "https://montanablotter.com/example",
                "Billings police investigate robbery",
                '{"county":"Yellowstone"}',
                "writer-key",
                "new",
                9,
            ),
        )
        conn.commit()

        post_id = create_draft_from_candidate(conn, 1)
        post = conn.execute(
            "SELECT title, published, author FROM blog_posts WHERE id = ?",
            (post_id,),
        ).fetchone()
        conn.close()

        self.assertEqual(post["published"], 0)
        self.assertEqual(post["author"], "Montana Blotter News Writer")
        self.assertIn("Billings police investigate robbery", post["title"])

    def test_create_draft_from_candidate_records_blog_post_sources(self) -> None:
        from news_writer_agent import create_draft_from_candidate

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            INSERT INTO story_candidates (
                source_type, source_url, headline_hint, facts_json, dedupe_key, status, score
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "montana_public_records",
                "https://courts.mt.gov/external/orders/dailyorders/bozeman-vehicle-theft",
                "Gallatin County court documents reference vehicle theft case",
                '{"summary":"Vehicle theft report"}',
                "writer-source-key",
                "new",
                7,
            ),
        )
        conn.commit()

        post_id = create_draft_from_candidate(conn, 1)
        source = conn.execute(
            "SELECT source_url, source_type FROM blog_post_sources WHERE blog_post_id = ?",
            (post_id,),
        ).fetchone()
        conn.close()

        self.assertEqual(source["source_url"], "https://courts.mt.gov/external/orders/dailyorders/bozeman-vehicle-theft")
        self.assertEqual(source["source_type"], "montana_public_records")

    def test_create_draft_from_candidate_builds_multi_paragraph_local_news_body(self) -> None:
        from news_writer_agent import create_draft_from_candidate

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            INSERT INTO story_candidates (
                source_type, source_url, headline_hint, facts_json, dedupe_key, status, score
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "montanablotter",
                "https://montanablotter.com/posts/999",
                "Helena police investigate overnight burglary",
                '{"summary":"Investigators said the report came in shortly after midnight.","county":"Lewis and Clark","city":"Helena","agency_name":"Helena Police Department","incident_date":"2026-04-25"}',
                "writer-body-key",
                "new",
                9,
            ),
        )
        conn.commit()

        post_id = create_draft_from_candidate(conn, 1)
        post = conn.execute(
            "SELECT body FROM blog_posts WHERE id = ?",
            (post_id,),
        ).fetchone()
        conn.close()

        self.assertIn("Helena Police Department", post["body"])
        self.assertIn("Lewis and Clark County", post["body"])
        self.assertIn("Investigators said the report came in shortly after midnight.", post["body"])
        self.assertGreaterEqual(post["body"].count("\n\n"), 2)

    def test_create_draft_from_candidate_strips_non_factual_perspective_lines(self) -> None:
        from news_writer_agent import create_draft_from_candidate

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            INSERT INTO story_candidates (
                source_type, source_url, headline_hint, facts_json, dedupe_key, status, score
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "montanablotter",
                "https://montanablotter.com/posts/778",
                "Yellowstone deputies investigate overnight incidents",
                json.dumps(
                    {
                        "summary": "Authorities released an incident summary.\n\n// Historical Perspective: This should never appear.",
                        "county": "Yellowstone",
                        "agency_name": "Yellowstone County Sheriff's Office",
                        "incident_date": "2026-04-25",
                    }
                ),
                "writer-clean-summary-key",
                "new",
                9,
            ),
        )
        conn.commit()

        post_id = create_draft_from_candidate(conn, 1)
        post = conn.execute(
            "SELECT body FROM blog_posts WHERE id = ?",
            (post_id,),
        ).fetchone()
        conn.close()

        self.assertIn("Authorities released an incident summary.", post["body"])
        self.assertNotIn("Historical Perspective", post["body"])


if __name__ == "__main__":
    unittest.main()
