"""Tests for the subscription paywall / preview limit system."""
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from flask import Flask, session as flask_session

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestPaywall(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = 'test-secret'
        self.client = self.app.test_client()

        self.pv_fd, self.pv_path = tempfile.mkstemp(suffix='.db')
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.db')

        self._init_dbs()

        # Build patched helpers
        def _tmp_pv():
            conn = sqlite3.connect(self.pv_path)
            conn.row_factory = sqlite3.Row
            return conn

        def _tmp_db():
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            return conn

        self._tmp_pv = _tmp_pv
        self._tmp_db = _tmp_db

        # Patch paywall module DB helpers BEFORE importing
        import services.monetization.paywall as paywall_module
        self._orig_connect_page_views = paywall_module.connect_page_views
        self._orig_get_db = paywall_module.get_db
        paywall_module.connect_page_views = _tmp_pv
        paywall_module.get_db = _tmp_db

        # Now import the functions
        from services.monetization.paywall import preview_allowed, get_user_plan, user_has_access
        self.preview_allowed = preview_allowed
        self.get_user_plan = get_user_plan
        self.user_has_access = user_has_access

    def tearDown(self):
        import services.monetization.paywall as paywall_module
        paywall_module.connect_page_views = self._orig_connect_page_views
        paywall_module.get_db = self._orig_get_db
        os.close(self.pv_fd)
        os.unlink(self.pv_path)
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def _init_dbs(self):
        conn = sqlite3.connect(self.pv_path)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS preview_views (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                viewer_type TEXT NOT NULL DEFAULT 'anonymous',
                viewer_id TEXT NOT NULL,
                resource_type TEXT NOT NULL DEFAULT 'incident',
                resource_id INTEGER,
                viewed_at TEXT DEFAULT (datetime('now'))
            )
        ''')
        conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_preview_views_lookup
            ON preview_views(viewer_type, viewer_id, viewed_at)
        ''')
        conn.commit()
        conn.close()

        conn = sqlite3.connect(self.db_path)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS public_users (
                id INTEGER PRIMARY KEY,
                email TEXT,
                is_active INTEGER DEFAULT 1,
                is_subscribed INTEGER DEFAULT 0,
                subscriber_plan TEXT DEFAULT 'scout',
                subscription_status TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()

    def _mock_anon(self):
        """Return a patcher for an anonymous current_user."""
        user = MagicMock()
        user.is_authenticated = False
        return patch('services.monetization.paywall.current_user', user)

    def test_anonymous_preview_limits(self):
        with self.app.test_request_context():
            flask_session['mb_session_id'] = 'test-session-123'
            with self._mock_anon():
                for i in range(3):
                    allowed, counts = self.preview_allowed('incident', i)
                    self.assertTrue(allowed, f"View {i+1} should be allowed")
                    self.assertEqual(counts['day'], i + 1)

                allowed, counts = self.preview_allowed('incident', 99)
                self.assertFalse(allowed, "4th view should be blocked")

    def test_subscribed_user_unlimited(self):
        conn = sqlite3.connect(self.db_path)
        cur = conn.execute(
            "INSERT INTO public_users (email, is_subscribed, subscriber_plan, subscription_status) VALUES (?, ?, ?, ?)",
            ("paid@example.com", 1, "insider", "active"),
        )
        uid = cur.lastrowid
        conn.commit()
        conn.close()

        with self.app.test_request_context():
            flask_session['public_user_id'] = uid
            with self._mock_anon():
                allowed, counts = self.preview_allowed('incident', 1)
                self.assertTrue(allowed)
                self.assertEqual(counts['day'], 0)

    def test_get_user_plan_for_public_user(self):
        conn = sqlite3.connect(self.db_path)
        cur = conn.execute(
            "INSERT INTO public_users (email, is_subscribed, subscriber_plan, subscription_status) VALUES (?, ?, ?, ?)",
            ("pro@example.com", 1, "professional", "active"),
        )
        uid = cur.lastrowid
        conn.commit()
        conn.close()

        with self.app.test_request_context():
            flask_session['public_user_id'] = uid
            with self._mock_anon():
                self.assertEqual(self.get_user_plan(), 'professional')
                self.assertTrue(self.user_has_access('insider'))


if __name__ == '__main__':
    unittest.main()
