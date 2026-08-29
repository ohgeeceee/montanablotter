import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import app as app_module
import config
import init_db
from blueprints.admin import agents as agents_module


class AdminAgentsTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-admin-agents-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        self.previous_app_db_path = app_module.config.DB_PATH

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        app_module.app.config['TESTING'] = True

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
        self.admin_user_id = self._create_admin_user()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = self.previous_app_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _create_admin_user(self) -> int:
        conn = app_module.get_db()
        cursor = conn.execute(
            """
            INSERT INTO users (username, password, email, role, is_active)
            VALUES (?, ?, ?, ?, ?)
            """,
            ('agents-admin', 'not-used-in-tests', 'agents@example.com', 'ops', 1),
        )
        conn.commit()
        conn.close()
        return int(cursor.lastrowid)

    def _login_admin_session(self, client) -> None:
        with client.session_transaction() as session:
            session['_user_id'] = str(self.admin_user_id)
            session['_fresh'] = True
            session['_csrf_token'] = 'test-csrf-token'

    def test_parse_entry_extracts_agent_and_builds_message(self) -> None:
        raw = json.dumps(
            {
                '0': 'Starting sweep',
                '1': 'lane=session:agent:reporter:main durationMs=123',
                '_meta': {'logLevelName': 'WARN'},
                'time': '2026-04-18T21:00:00+00:00',
            }
        )

        entry = agents_module._parse_entry(raw)

        self.assertEqual(entry['agent'], 'reporter')
        self.assertEqual(entry['level'], 'WARN')
        self.assertEqual(entry['time'], '2026-04-18T21:00:00+00:00')
        self.assertEqual(entry['msg'], 'Starting sweep | lane=session:agent:reporter:main durationMs=123')

    def test_agent_snapshot_reads_log_file_and_groups_history(self) -> None:
        now = datetime.now(timezone.utc)
        log_fd, log_path = tempfile.mkstemp(prefix='mb-openclaw-', suffix='.log')
        os.close(log_fd)
        try:
            entries = [
                {
                    '0': 'Reporter finished county scan',
                    '1': 'lane=session:agent:reporter:main durationMs=42',
                    '_meta': {'logLevelName': 'INFO'},
                    'time': (now - timedelta(seconds=20)).isoformat(),
                },
                {
                    '0': 'Scout idle heartbeat',
                    '1': 'lane=session:agent:scout:main durationMs=6',
                    '_meta': {'logLevelName': 'INFO'},
                    'time': (now - timedelta(minutes=30)).isoformat(),
                },
                {
                    '0': 'Malformed agent fallback should be ignored for dashboard cards',
                    '_meta': {'logLevelName': 'INFO'},
                    'time': (now - timedelta(seconds=5)).isoformat(),
                },
            ]
            with open(log_path, 'w', encoding='utf-8') as handle:
                for entry in entries:
                    handle.write(json.dumps(entry) + '\n')

            with mock.patch.object(agents_module, '_log_path', return_value=Path(log_path)):
                snapshot = agents_module._agent_snapshot()
        finally:
            os.unlink(log_path)

        self.assertEqual(snapshot['agents']['reporter']['status'], 'active')
        self.assertEqual(snapshot['agents']['scout']['status'], 'seen')
        self.assertEqual(snapshot['agents']['main']['status'], 'unknown')
        self.assertEqual(len(snapshot['history']['reporter']), 1)
        self.assertEqual(snapshot['history']['reporter'][0]['agent'], 'reporter')
        self.assertEqual(snapshot['history']['scout'][0]['msg'], 'Scout idle heartbeat | lane=session:agent:scout:main durationMs=6')

    def test_admin_agents_page_redirects_to_command_center(self) -> None:
        client = app_module.app.test_client()
        self._login_admin_session(client)

        response = client.get('/admin/agents')

        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/operations/live', response.headers['Location'])

    def test_admin_office_routes_removed(self) -> None:
        client = app_module.app.test_client()
        self._login_admin_session(client)

        # The clustered VPS canvas office view was removed from the admin panel.
        # These legacy routes must no longer resolve.
        for path in ('/admin/office', '/admin/operations/office',
                     '/admin/operations/office/'):
            response = client.get(path, follow_redirects=False)
            self.assertEqual(
                response.status_code, 404,
                f'{path} should be removed, got {response.status_code}',
            )

    def test_admin_agents_stream_returns_error_event_when_openclaw_missing(self) -> None:
        client = app_module.app.test_client()
        self._login_admin_session(client)

        response = client.get('/admin/system/agents/stream')

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'text/event-stream')
        self.assertIn('openclaw disabled', body)
        self.assertIn('Live agent stream is disabled on this host.', body)


if __name__ == '__main__':
    unittest.main()
