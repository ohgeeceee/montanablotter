import os
import sqlite3
import tempfile
import unittest

import config
import init_db
import source_registry


class SourceRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-source-registry-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        self.previous_registry_db_path = source_registry.config.DB_PATH

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        source_registry.config.DB_PATH = self.db_path

        bootstrap_conn = sqlite3.connect(self.db_path)
        bootstrap_conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS subscribers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                counties TEXT DEFAULT '',
                token TEXT NOT NULL,
                active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
            '''
        )
        bootstrap_conn.commit()
        bootstrap_conn.close()
        init_db.init_database()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        source_registry.config.DB_PATH = self.previous_registry_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_source_registry_table_is_created(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='source_registry'").fetchone()
        artifact_row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='source_artifacts'").fetchone()
        conn.close()

        self.assertIsNotNone(row)
        self.assertIsNotNone(artifact_row)

    def test_ensure_source_registry_is_idempotent(self) -> None:
        first = source_registry.ensure_source_registry(
            source_key='yellowstone-jail-roster',
            source_type='jail_roster',
            display_name='Yellowstone County Jail Roster',
            base_url='https://example.com/roster',
            adapter_name='roster_html',
            poll_interval_seconds=600,
            notes='official roster',
        )
        second = source_registry.ensure_source_registry(
            source_key='yellowstone-jail-roster',
            source_type='jail_roster',
            display_name='Yellowstone County Jail Roster',
            base_url='https://example.com/roster',
            adapter_name='roster_html',
            poll_interval_seconds=600,
            notes='official roster',
        )

        self.assertEqual(first, second)

    def test_attach_source_artifact_is_idempotent_for_same_hash(self) -> None:
        source_id = source_registry.ensure_source_registry(
            source_key='yellowstone-daily-dispatch',
            source_type='dispatch_log',
            display_name='Yellowstone Daily Dispatch',
            base_url='https://example.com/dispatch',
            adapter_name='pdf_fetch',
            poll_interval_seconds=900,
        )
        first = source_registry.attach_source_artifact(
            source_registry_id=source_id,
            artifact_kind='raw_pdf',
            source_url='https://example.com/dispatch/report.pdf',
            storage_path='/tmp/report.pdf',
            content_sha256='abc123',
            metadata={'county': 'Yellowstone'},
        )
        second = source_registry.attach_source_artifact(
            source_registry_id=source_id,
            artifact_kind='raw_pdf',
            source_url='https://example.com/dispatch/report.pdf',
            storage_path='/tmp/report.pdf',
            content_sha256='abc123',
            metadata={'county': 'Yellowstone'},
        )

        self.assertEqual(first, second)


if __name__ == '__main__':
    unittest.main()
