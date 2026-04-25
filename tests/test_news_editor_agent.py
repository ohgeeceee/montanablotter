import os
import sqlite3
import tempfile
import unittest

import config
import init_db


class NewsEditorAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="mb-news-editor-", suffix=".db")
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

    def test_review_draft_rejects_missing_sources(self) -> None:
        from news_editor_agent import review_and_maybe_publish

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO blog_posts (title, slug, body, excerpt, author, published) VALUES (?, ?, ?, ?, ?, ?)",
            ("Missoula deputies investigate burglary", "missoula-burglary", "Body", "Excerpt", "Writer", 0),
        )
        conn.execute(
            "INSERT INTO story_candidates (source_type, source_url, headline_hint, facts_json, dedupe_key, status, score) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("montanablotter", "https://montanablotter.com/example", "Missoula deputies investigate burglary", "{}", "editor-key", "drafted", 10),
        )
        conn.commit()

        decision = review_and_maybe_publish(conn, blog_post_id=1, story_candidate_id=1)
        conn.close()

        self.assertEqual(decision["decision"], "rejected")

    def test_review_draft_publishes_when_sources_exist_and_cap_not_hit(self) -> None:
        from news_editor_agent import review_and_maybe_publish

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO blog_posts (title, slug, body, excerpt, author, published) VALUES (?, ?, ?, ?, ?, ?)",
            ("Gallatin deputies investigate burglary", "gallatin-burglary", "Body", "Excerpt", "Writer", 0),
        )
        conn.execute(
            "INSERT INTO story_candidates (source_type, source_url, headline_hint, facts_json, dedupe_key, status, score) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("montanablotter", "https://montanablotter.com/example", "Gallatin deputies investigate burglary", '{"county":"Gallatin"}', "editor-approve-key", "drafted", 10),
        )
        conn.execute(
            "INSERT INTO blog_post_sources (blog_post_id, source_url, source_type, notes) VALUES (?, ?, ?, ?)",
            (1, "https://montanablotter.com/example", "montanablotter", "source"),
        )
        conn.commit()

        decision = review_and_maybe_publish(conn, blog_post_id=1, story_candidate_id=1)
        post = conn.execute("SELECT published FROM blog_posts WHERE id = 1").fetchone()
        conn.close()

        self.assertEqual(decision["decision"], "approved")
        self.assertEqual(post["published"], 1)

    def test_review_draft_rejects_when_daily_publish_cap_reached(self) -> None:
        from news_editor_agent import review_and_maybe_publish

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        for index in range(5):
            conn.execute(
                "INSERT INTO blog_posts (title, slug, body, excerpt, author, published, created_at) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
                (f"Published {index}", f"published-{index}", "Body", "Excerpt", "Writer", 1),
            )
        conn.execute(
            "INSERT INTO blog_posts (title, slug, body, excerpt, author, published) VALUES (?, ?, ?, ?, ?, ?)",
            ("New Helena report", "new-helena-report", "Body", "Excerpt", "Writer", 0),
        )
        conn.execute(
            "INSERT INTO story_candidates (source_type, source_url, headline_hint, facts_json, dedupe_key, status, score) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("montanablotter", "https://montanablotter.com/new", "New Helena report", "{}", "editor-cap-key", "drafted", 10),
        )
        conn.execute(
            "INSERT INTO blog_post_sources (blog_post_id, source_url, source_type, notes) VALUES (?, ?, ?, ?)",
            (6, "https://montanablotter.com/new", "montanablotter", "source"),
        )
        conn.commit()

        decision = review_and_maybe_publish(conn, blog_post_id=6, story_candidate_id=1)
        conn.close()

        self.assertEqual(decision["decision"], "rejected")
        self.assertIn("daily cap", decision["reason"])

    def test_review_draft_rejects_duplicate_recent_title(self) -> None:
        from news_editor_agent import review_and_maybe_publish

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO blog_posts (title, slug, body, excerpt, author, published, created_at) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
            ("Billings robbery report", "billings-robbery-existing", "Body", "Excerpt", "Writer", 1),
        )
        conn.execute(
            "INSERT INTO blog_posts (title, slug, body, excerpt, author, published) VALUES (?, ?, ?, ?, ?, ?)",
            ("Billings robbery report", "billings-robbery-new", "Body", "Excerpt", "Writer", 0),
        )
        conn.execute(
            "INSERT INTO story_candidates (source_type, source_url, headline_hint, facts_json, dedupe_key, status, score) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("montanablotter", "https://montanablotter.com/robbery", "Billings robbery report", "{}", "editor-dup-key", "drafted", 10),
        )
        conn.execute(
            "INSERT INTO blog_post_sources (blog_post_id, source_url, source_type, notes) VALUES (?, ?, ?, ?)",
            (2, "https://montanablotter.com/robbery", "montanablotter", "source"),
        )
        conn.commit()

        decision = review_and_maybe_publish(conn, blog_post_id=2, story_candidate_id=1)
        conn.close()

        self.assertEqual(decision["decision"], "rejected")
        self.assertIn("duplicate", decision["reason"])

    def test_review_draft_rejects_sensitive_minor_or_victim_language(self) -> None:
        from news_editor_agent import review_and_maybe_publish

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO blog_posts (title, slug, body, excerpt, author, published) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "Helena police investigate alleged assault",
                "helena-assault",
                "Police said a 14-year-old victim reported the assault.",
                "Excerpt",
                "Writer",
                0,
            ),
        )
        conn.execute(
            "INSERT INTO story_candidates (source_type, source_url, headline_hint, facts_json, dedupe_key, status, score) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "montanablotter",
                "https://montanablotter.com/posts/777",
                "Helena police investigate alleged assault",
                '{"summary":"Police said a 14-year-old victim reported the assault."}',
                "editor-sensitive-key",
                "drafted",
                10,
            ),
        )
        conn.execute(
            "INSERT INTO blog_post_sources (blog_post_id, source_url, source_type, notes) VALUES (?, ?, ?, ?)",
            (1, "https://montanablotter.com/posts/777", "montanablotter", "source"),
        )
        conn.commit()

        decision = review_and_maybe_publish(conn, blog_post_id=1, story_candidate_id=1)
        conn.close()

        self.assertEqual(decision["decision"], "rejected")
        self.assertIn("sensitive", decision["reason"])

    def test_review_draft_rejects_historical_perspective_language(self) -> None:
        from news_editor_agent import review_and_maybe_publish

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO blog_posts (title, slug, body, excerpt, author, published) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "Yellowstone deputies investigate overnight incidents",
                "yellowstone-incidents",
                "Authorities released an incident summary.\n\n// Historical Perspective: This should never appear.",
                "Excerpt",
                "Writer",
                0,
            ),
        )
        conn.execute(
            "INSERT INTO story_candidates (source_type, source_url, headline_hint, facts_json, dedupe_key, status, score) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "montanablotter",
                "https://montanablotter.com/posts/778",
                "Yellowstone deputies investigate overnight incidents",
                '{"summary":"Authorities released an incident summary."}',
                "editor-history-key",
                "drafted",
                10,
            ),
        )
        conn.execute(
            "INSERT INTO blog_post_sources (blog_post_id, source_url, source_type, notes) VALUES (?, ?, ?, ?)",
            (1, "https://montanablotter.com/posts/778", "montanablotter", "source"),
        )
        conn.commit()

        decision = review_and_maybe_publish(conn, blog_post_id=1, story_candidate_id=1)
        conn.close()

        self.assertEqual(decision["decision"], "rejected")
        self.assertIn("policy", decision["reason"])


if __name__ == "__main__":
    unittest.main()
