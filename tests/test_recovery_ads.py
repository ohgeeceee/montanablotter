import os
import sqlite3
import tempfile
import unittest


def _make_conn(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


class RecoveryAdSchemaTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        self.conn = _make_conn(self.db_path)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db_path)

    def test_ensure_creates_orders_table(self):
        from init_db import ensure_recovery_ad_schema
        ensure_recovery_ad_schema(self.conn)
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='recovery_ad_orders'"
        ).fetchone()
        self.assertIsNotNone(row)

    def test_ensure_creates_listings_table(self):
        from init_db import ensure_recovery_ad_schema
        ensure_recovery_ad_schema(self.conn)
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='recovery_ad_listings'"
        ).fetchone()
        self.assertIsNotNone(row)

    def test_ensure_is_idempotent(self):
        from init_db import ensure_recovery_ad_schema
        ensure_recovery_ad_schema(self.conn)
        ensure_recovery_ad_schema(self.conn)  # should not raise


if __name__ == '__main__':
    unittest.main()
