import importlib
import os
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

import app as app_module
import config
import init_db


class AdminAIConsoleTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-admin-ai-console-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        self.previous_app_db_path = app_module.config.DB_PATH
        self.previous_testing = app_module.app.config.get('TESTING')

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
        app_module.app.config['TESTING'] = self.previous_testing
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _create_admin_user(self) -> int:
        conn = app_module.get_db()
        cursor = conn.execute(
            """
            INSERT INTO users (username, password, email, role, is_active)
            VALUES (?, ?, ?, ?, ?)
            """,
            ('ai-console-admin', 'not-used-in-tests', 'aiconsole@example.com', 'super_admin', 1),
        )
        conn.commit()
        conn.close()
        return int(cursor.lastrowid)

    def _login_admin_session(self, client) -> None:
        with client.session_transaction() as session:
            session['_user_id'] = str(self.admin_user_id)
            session['_fresh'] = True
            session['_csrf_token'] = 'test-csrf-token'

    def test_admin_ai_requires_login(self) -> None:
        client = app_module.app.test_client()

        response = client.get('/admin/ai')

        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login', response.headers['Location'])

    def test_admin_ai_renders_for_logged_in_admin(self) -> None:
        client = app_module.app.test_client()
        self._login_admin_session(client)

        response = client.get('/admin/ai')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Admin AI Console', html)
        self.assertIn('Ask Montana Blotter AI', html)

    def test_admin_ai_query_returns_read_only_answer(self) -> None:
        client = app_module.app.test_client()
        self._login_admin_session(client)

        with mock.patch('blueprints.admin.ai_console.run_admin_ai_query') as mocked_query:
            mocked_query.return_value = {
                'answer': 'Yellowstone has 1 matching theft record.',
                'transcript': [{'role': 'assistant', 'content': 'Yellowstone has 1 matching theft record.'}],
                'pending_action': None,
            }
            response = client.post(
                '/admin/ai/query',
                data={'question': 'Find theft records', 'csrf_token': 'test-csrf-token'},
            )

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Yellowstone has 1 matching theft record.', html)
        self.assertNotIn('Confirm Draft Action', html)

    def test_admin_ai_query_shows_clean_auth_error_message(self) -> None:
        client = app_module.app.test_client()
        self._login_admin_session(client)

        with mock.patch('blueprints.admin.ai_console.run_admin_ai_query', side_effect=RuntimeError(
            "Error code: 401 - {'error': {'message': 'Invalid Authentication', 'type': 'invalid_authentication_error'}}"
        )):
            response = client.post(
                '/admin/ai/query',
                data={'question': 'Find theft records', 'csrf_token': 'test-csrf-token'},
            )

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Kimi API key is invalid.', html)
        self.assertNotIn('invalid_authentication_error', html)

    def test_admin_ai_query_stages_pending_action_without_executing_it(self) -> None:
        client = app_module.app.test_client()
        self._login_admin_session(client)

        with mock.patch('blueprints.admin.ai_console.run_admin_ai_query') as mocked_query:
            mocked_query.return_value = {
                'answer': 'I can draft that blog post.',
                'transcript': [{'role': 'assistant', 'content': 'I can draft that blog post.'}],
                'pending_action': {
                    'token': 'pending-token',
                    'tool_name': 'create_blog_draft',
                    'summary': 'Create a draft blog post about Yellowstone theft trends',
                    'arguments': {'title': 'Yellowstone theft trends', 'body': 'Draft body'},
                    'created_at': int(time.time()),
                },
            }
            response = client.post(
                '/admin/ai/query',
                data={'question': 'Draft a blog post', 'csrf_token': 'test-csrf-token'},
            )

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Confirm Draft Action', html)
        self.assertIn('create_blog_draft', html)
        with client.session_transaction() as session:
            # Session stores the token; the full action lives in
            # admin_ai_pending_actions table and is looked up via that token.
            token = session['admin_ai_pending_action']
            self.assertEqual(token, 'pending-token')

    def test_admin_ai_confirm_executes_matching_pending_action_once(self) -> None:
        client = app_module.app.test_client()
        self._login_admin_session(client)

        # Session stores the token; the full action lives in the
        # admin_ai_pending_actions DB table.
        with client.session_transaction() as session:
            session['admin_ai_pending_action'] = 'pending-token'

        # Seed the matching pending action in the DB so validate_pending_action
        # can find it.
        admin_ai = importlib.import_module('admin_ai')
        admin_ai.save_pending_action(
            self.admin_user_id,
            {
                'token': 'pending-token',
                'tool_name': 'create_blog_draft',
                'summary': 'Create a draft',
                'arguments': {'title': 'Draft title', 'body': 'Draft body'},
                'created_at': int(time.time()),
            },
            db_path=self.db_path,
        )

        with mock.patch('blueprints.admin.ai_console.execute_pending_admin_ai_action') as mocked_execute:
            mocked_execute.return_value = {'message': 'Draft created', 'target_id': 42}
            response = client.post(
                '/admin/ai/confirm',
                data={'token': 'pending-token', 'csrf_token': 'test-csrf-token'},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn('Draft created', response.get_data(as_text=True))
        mocked_execute.assert_called_once()
        with client.session_transaction() as session:
            self.assertNotIn('admin_ai_pending_action', session)

    def test_admin_ai_confirm_rejects_mismatched_token(self) -> None:
        client = app_module.app.test_client()
        self._login_admin_session(client)

        with client.session_transaction() as session:
            session['admin_ai_pending_action'] = {
                'token': 'expected-token',
                'tool_name': 'create_blog_draft',
                'summary': 'Create a draft',
                'arguments': {'title': 'Draft title', 'body': 'Draft body'},
                'created_at': int(time.time()),
            }

        response = client.post(
            '/admin/ai/confirm',
            data={'token': 'wrong-token', 'csrf_token': 'test-csrf-token'},
        )

        self.assertEqual(response.status_code, 400)

    def test_confirmed_blog_draft_action_creates_unpublished_post(self) -> None:
        import admin_ai

        result = admin_ai.execute_pending_admin_ai_action(
            {
                'tool_name': 'create_blog_draft',
                'arguments': {
                    'title': 'AI Draft Title',
                    'summary': 'AI Draft Summary',
                    'body': 'AI Draft Body',
                },
            },
            db_path=self.db_path,
        )

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT title, excerpt, body, published FROM blog_posts WHERE id = ?",
            (result['target_id'],),
        ).fetchone()
        conn.close()

        self.assertEqual(row['title'], 'AI Draft Title')
        self.assertEqual(row['excerpt'], 'AI Draft Summary')
        self.assertEqual(row['published'], 0)

    def test_run_admin_ai_query_returns_pending_action_for_write_intent(self) -> None:
        import admin_ai
        import anthropic

        # Build a real Anthropic-style response with a tool_use block.
        # The code does isinstance(b, _anthropic.types.ToolUseBlock), so
        # we must use the real type — a duck-typed stub won't pass.
        text_block = anthropic.types.TextBlock(type='text', text='I can prepare that draft.')
        tool_use_block = anthropic.types.ToolUseBlock(
            type='tool_use',
            id='tool-1',
            name='create_blog_draft',
            input={
                'title': 'AI Draft Title',
                'summary': 'AI Draft Summary',
                'body': 'AI Draft Body',
            },
        )

        class FakeResponse:
            stop_reason = 'tool_use'
            content = [text_block, tool_use_block]

        mocked_client = mock.Mock()
        mocked_client.messages.create.return_value = FakeResponse()

        with mock.patch(
            'admin_ai.create_claude_client', return_value=mocked_client
        ):
            result = admin_ai.run_admin_ai_query(
                'Draft a blog post', db_path=self.db_path
            )

        self.assertIsNotNone(result['pending_action'])
        self.assertEqual(result['pending_action']['tool_name'], 'create_blog_draft')
        self.assertEqual(
            result['pending_action']['arguments']['title'], 'AI Draft Title'
        )


if __name__ == '__main__':
    unittest.main()
