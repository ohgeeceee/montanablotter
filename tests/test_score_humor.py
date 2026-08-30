"""Tests for the humor scorer job against a temporary SQLite database."""
import os
import sqlite3
import tempfile
import unittest

os.environ.setdefault("MB_REQUIRE_SIGNIN", "false")

import config
from services.ingestion import score_humor as job


class TestScoreHumorJob(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp() + "/job.db"
        config.DB_PATH = self.tmp
        self.conn = sqlite3.connect(self.tmp)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE records ("
            "id INTEGER PRIMARY KEY, blotter_id INTEGER, incident TEXT, "
            "details TEXT, incident_type TEXT, county TEXT, humor_score REAL)"
        )
        # already-scored row (should be left alone)
        self.conn.execute(
            "INSERT INTO records (blotter_id, incident, details, incident_type, county, humor_score) "
            "VALUES (1, 'old goat bit mailman', '', 'traffic', 'cascade', 5.0)"
        )
        # unscored eligible
        self.conn.execute(
            "INSERT INTO records (blotter_id, incident, details, incident_type, county) "
            "VALUES (1, 'goose chased a man down Elm', '', 'suspicious', 'cascade')"
        )
        # unscored denied type -> must score 0, not re-scanned
        self.conn.execute(
            "INSERT INTO records (blotter_id, incident, details, incident_type, county) "
            "VALUES (1, 'domestic disturbance reported', 'loud argument', 'domestic_violence', 'cascade')"
        )
        # unscored too-short -> skipped by SQL length filter, stays NULL
        self.conn.execute(
            "INSERT INTO records (blotter_id, incident, details, incident_type, county) "
            "VALUES (1, 'x', '', 'traffic', 'cascade')"
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _run(self, **kw):
        # Patch connect_db to return our temp connection so the job writes there.
        # Wrap close() so the job's run() finally-block doesn't tear down the
        # test's shared connection before we assert on it.
        orig = job.connect_db

        class _KeepOpen:
            def __init__(self, c):
                self.c = c

            def __getattr__(self, name):
                return getattr(self.c, name)

            def close(self):
                pass

        job.connect_db = lambda **_: _KeepOpen(self.conn)
        try:
            return job.run(dry_run=kw.get("dry_run", False),
                           limit=kw.get("limit"), batch_size=kw.get("batch_size", 500))
        finally:
            job.connect_db = orig

    def test_only_unscored_rows_get_scored(self):
        res = self._run()
        self.assertEqual(res["scored"], 2)  # goose + DV row; short row skipped by length filter
        rows = {r["id"]: r for r in self.conn.execute("SELECT * FROM records")}
        # pre-scored row untouched
        self.assertEqual(rows[1]["humor_score"], 5.0)
        # eligible goose row positive
        self.assertGreater(rows[2]["humor_score"], 0)
        # DV row gets 0 (written, not re-scanned)
        self.assertEqual(rows[3]["humor_score"], 0)
        # short row stays NULL (length filter excludes it)
        self.assertIsNone(rows[4]["humor_score"])

    def test_dry_run_writes_nothing(self):
        res = self._run(dry_run=True)
        self.assertTrue(res["dry_run"])
        for r in self.conn.execute("SELECT humor_score FROM records WHERE id > 1"):
            self.assertIsNone(r["humor_score"])

    def test_idempotent_on_second_run(self):
        self._run()
        before = {r["id"]: r["humor_score"] for r in self.conn.execute("SELECT id, humor_score FROM records")}
        res2 = self._run()
        self.assertEqual(res2["scored"], 0)  # nothing left to score
        after = {r["id"]: r["humor_score"] for r in self.conn.execute("SELECT id, humor_score FROM records")}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
