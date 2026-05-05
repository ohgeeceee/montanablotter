import os
import sqlite3
import tempfile
import unittest

import db as db_module


class DbTursoFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-db-turso-', suffix='.db')
        os.close(fd)
        self.previous_db_path = db_module.config.DB_PATH
        self.previous_use_turso = db_module._USE_TURSO
        self.previous_turso_url = db_module._TURSO_URL
        self.previous_turso_token = db_module._TURSO_TOKEN
        self.previous_libsql = getattr(db_module, '_libsql', None)

        db_module.config.DB_PATH = self.db_path

    def tearDown(self) -> None:
        db_module.config.DB_PATH = self.previous_db_path
        db_module._USE_TURSO = self.previous_use_turso
        db_module._TURSO_URL = self.previous_turso_url
        db_module._TURSO_TOKEN = self.previous_turso_token
        if self.previous_libsql is None:
            if hasattr(db_module, '_libsql'):
                delattr(db_module, '_libsql')
        else:
            db_module._libsql = self.previous_libsql
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_connect_db_disables_turso_after_forbidden_sync(self) -> None:
        attempts: list[str] = []

        class FakeLibsqlConnection:
            def sync(self) -> None:
                raise RuntimeError(
                    'failed to pull db file from remote server, status=403 Forbidden, '
                    'body={"error":"reads are blocked"}'
                )

        class FakeLibsqlModule:
            @staticmethod
            def connect(*, database, sync_url, auth_token):
                attempts.append(database)
                return FakeLibsqlConnection()

        db_module._USE_TURSO = True
        db_module._TURSO_URL = 'libsql://example.turso.io'
        db_module._TURSO_TOKEN = 'token'
        db_module._libsql = FakeLibsqlModule()

        first = db_module.connect_db()
        try:
            self.assertIsInstance(first, sqlite3.Connection)
        finally:
            first.close()

        self.assertFalse(db_module._USE_TURSO)
        self.assertEqual(len(attempts), 1)

        second = db_module.connect_db()
        try:
            self.assertIsInstance(second, sqlite3.Connection)
        finally:
            second.close()

        self.assertEqual(len(attempts), 1)


if __name__ == '__main__':
    unittest.main()
