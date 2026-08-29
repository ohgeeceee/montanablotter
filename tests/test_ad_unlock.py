"""Tests for the ad-watched warrant unlock path.

Covers:
- Paywall helpers: `user_has_ad_unlocked_warrant`, `get_ad_unlock_remaining_seconds`,
  `record_ad_unlock`, `count_recent_ad_unlocks_by_ip`.
- `user_has_warrant_access` ORs ad-grant check alongside paid `warrant_access` plan.
- Stacking: new grant extends to `now() + duration` (paywall uses MAX(expires_at)).
- Blueprint `/ad/watch` requires login.
- Blueprint `/api/ad-unlock/complete` validates nonce + watch_seconds + rate-limit.
- Nonce single-use: replay of the same nonce is rejected.
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _mk_public_user(db_path: str, email: str = "tester@example.com") -> int:
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "INSERT INTO public_users (email, is_subscribed, subscriber_plan, subscription_status) "
        "VALUES (?, 0, 'scout', NULL)",
        (email,),
    )
    uid = cur.lastrowid
    conn.commit()
    conn.close()
    return uid


class AdUnlockPaywallTestCase(unittest.TestCase):
    """Unit tests for the paywall-level ad-unlock helpers."""

    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = 'test-secret'
        self.client = self.app.test_client()

        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.db')

        # Build temp DB with the ad_unlock_grants + public_users schema.
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
        conn.execute('''
            CREATE TABLE IF NOT EXISTS ad_unlock_grants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_user_id INTEGER NOT NULL,
                granted_at TEXT NOT NULL DEFAULT (datetime('now')),
                expires_at TEXT NOT NULL,
                ad_id TEXT,
                watch_seconds INTEGER NOT NULL,
                ip_address TEXT,
                user_agent TEXT,
                provider TEXT DEFAULT 'youtube',
                FOREIGN KEY (public_user_id) REFERENCES public_users(id)
            )
        ''')
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_ad_unlock_grants_user_expiry '
            'ON ad_unlock_grants(public_user_id, expires_at)'
        )
        conn.commit()
        conn.close()

        # Patch paywall.get_db to return our temp connection (with row_factory).
        def _tmp_db():
            c = sqlite3.connect(self.db_path)
            c.row_factory = sqlite3.Row
            return c

        import services.monetization.paywall as paywall_module
        self._orig_get_db = paywall_module.get_db
        paywall_module.get_db = _tmp_db

        from services.monetization.paywall import (
            user_has_ad_unlocked_warrant,
            get_ad_unlock_remaining_seconds,
            record_ad_unlock,
            count_recent_ad_unlocks_by_ip,
            user_has_warrant_access,
            get_user_plan,
        )
        self.user_has_ad_unlocked_warrant = user_has_ad_unlocked_warrant
        self.get_ad_unlock_remaining_seconds = get_ad_unlock_remaining_seconds
        self.record_ad_unlock = record_ad_unlock
        self.count_recent_ad_unlocks_by_ip = count_recent_ad_unlocks_by_ip
        self.user_has_warrant_access = user_has_warrant_access
        self.get_user_plan = get_user_plan

    def tearDown(self):
        import services.monetization.paywall as paywall_module
        paywall_module.get_db = self._orig_get_db
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def _mock_anon(self):
        user = MagicMock()
        user.is_authenticated = False
        return patch('services.monetization.paywall.current_user', user)

    def _now_utc_str(self) -> str:
        return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

    def test_record_and_check_grant(self):
        uid = _mk_public_user(self.db_path)
        with self.app.test_request_context() as ctx:
            from flask import session as flask_session
            flask_session['public_user_id'] = uid
            with self._mock_anon():
                self.assertFalse(self.user_has_ad_unlocked_warrant())
                expires_at = self.record_ad_unlock(
                    public_user_id=uid, watch_seconds=15, ip_address='1.2.3.4', ad_id='video123',
                )
                self.assertTrue(self.user_has_ad_unlocked_warrant())
                # expires_at should be ~24h from now
                expiry = datetime.strptime(expires_at, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                delta_hours = (expiry - now).total_seconds() / 3600
                self.assertAlmostEqual(delta_hours, 24.0, delta=0.1)

    def test_expiry_check(self):
        """A grant that has already expired must not satisfy the paywall."""
        uid = _mk_public_user(self.db_path)
        # Insert an already-expired row directly.
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            'INSERT INTO ad_unlock_grants (public_user_id, expires_at, ad_id, watch_seconds) '
            'VALUES (?, ?, ?, ?)',
            (uid, past, 'video123', 15),
        )
        conn.commit()
        conn.close()

        with self.app.test_request_context() as ctx:
            from flask import session as flask_session
            flask_session['public_user_id'] = uid
            with self._mock_anon():
                self.assertFalse(self.user_has_ad_unlocked_warrant())
                self.assertEqual(self.get_ad_unlock_remaining_seconds(uid), 0)

    def test_paywall_or_behavior(self):
        """user_has_warrant_access() should be True if ad-grant is active, regardless of plan."""
        uid = _mk_public_user(self.db_path)
        # No subscription plan set, but with a valid ad grant.
        with self.app.test_request_context() as ctx:
            from flask import session as flask_session
            flask_session['public_user_id'] = uid
            with self._mock_anon():
                # Plan is 'free' (no subscription), no ad grant.
                self.assertEqual(self.get_user_plan(), 'free')
                # No grant yet — no access.
                self.assertFalse(self.user_has_warrant_access())
                # Record a grant — now access.
                self.record_ad_unlock(public_user_id=uid, watch_seconds=20, ip_address='5.6.7.8')
                self.assertTrue(self.user_has_warrant_access())
                self.assertTrue(self.user_has_ad_unlocked_warrant())

    def test_warrant_access_plan_grants_access(self):
        """A user stored with subscriber_plan='warrant_access' (the value real
        Stripe provisioning writes) must pass user_has_warrant_access().

        Regression: WARRANT_PLANS only contained {'plus','pro'}, so paid users
        whose plan was literally 'warrant_access' were locked out of the warrant
        pages despite an active subscription. See fix in paywall.py WARRANT_PLANS.
        """
        uid = _mk_public_user(self.db_path)
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE public_users SET subscriber_plan='warrant_access', is_subscribed=1, subscription_status='active' WHERE id=?",
            (uid,),
        )
        conn.commit()
        conn.close()
        with self.app.test_request_context() as ctx:
            from flask import session as flask_session
            flask_session['public_user_id'] = uid
            with self._mock_anon():
                self.assertTrue(self.user_has_warrant_access())

    def test_paywall_anon_user_returns_false(self):
        """Anonymous user (no public_user_id) cannot have an ad grant."""
        with self.app.test_request_context() as ctx:
            from flask import session as flask_session
            flask_session.clear()
            with self._mock_anon():
                self.assertFalse(self.user_has_ad_unlocked_warrant())
                self.assertFalse(self.user_has_warrant_access())

    def test_stacking_extends(self):
        """A new grant extends access to now() + duration (not stacked on top of existing)."""
        uid = _mk_public_user(self.db_path)
        with self.app.test_request_context() as ctx:
            from flask import session as flask_session
            flask_session['public_user_id'] = uid
            with self._mock_anon():
                # Grant #1: 24h from now.
                first_expiry = self.record_ad_unlock(public_user_id=uid, watch_seconds=15)
                # Simulate 23h passing: rewrite expires_at to 1h from now.
                one_hour_from_now = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
                conn = sqlite3.connect(self.db_path)
                conn.execute(
                    'UPDATE ad_unlock_grants SET expires_at = ? WHERE public_user_id = ?',
                    (one_hour_from_now, uid),
                )
                conn.commit()
                conn.close()
                # Now ~1h remaining.
                remaining = self.get_ad_unlock_remaining_seconds(uid)
                self.assertGreater(remaining, 3500)
                self.assertLess(remaining, 3700)
                # Grant #2: extends to 24h from now.
                second_expiry = self.record_ad_unlock(public_user_id=uid, watch_seconds=15)
                expiry_dt = datetime.strptime(second_expiry, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                delta_hours = (expiry_dt - now).total_seconds() / 3600
                # After extending, should be ~24h again, not 25h.
                self.assertAlmostEqual(delta_hours, 24.0, delta=0.1)

    def test_count_recent_by_ip(self):
        """Rate limit helper counts grants by IP within a time window."""
        uid = _mk_public_user(self.db_path)
        # Insert 3 grants from 1.2.3.4 in the last 30 minutes.
        conn = sqlite3.connect(self.db_path)
        for _ in range(3):
            conn.execute(
                'INSERT INTO ad_unlock_grants (public_user_id, expires_at, ad_id, watch_seconds, ip_address) '
                'VALUES (?, ?, ?, ?, ?)',
                (uid, '2099-12-31 00:00:00', 'v1', 15, '1.2.3.4'),
            )
        # And 1 from 9.9.9.9.
        conn.execute(
            'INSERT INTO ad_unlock_grants (public_user_id, expires_at, ad_id, watch_seconds, ip_address) '
            'VALUES (?, ?, ?, ?, ?)',
            (uid, '2099-12-31 00:00:00', 'v1', 15, '9.9.9.9'),
        )
        conn.commit()
        conn.close()
        self.assertEqual(self.count_recent_ad_unlocks_by_ip('1.2.3.4', hours=1), 3)
        self.assertEqual(self.count_recent_ad_unlocks_by_ip('9.9.9.9', hours=1), 1)
        self.assertEqual(self.count_recent_ad_unlocks_by_ip('5.5.5.5', hours=1), 0)
        # Empty IP returns 0.
        self.assertEqual(self.count_recent_ad_unlocks_by_ip('', hours=1), 0)


class AdUnlockBlueprintTestCase(unittest.TestCase):
    """Integration tests for the warrant_unlock blueprint routes."""

    def setUp(self):
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.app = Flask(
            __name__,
            template_folder=os.path.join(project_root, 'templates'),
        )
        self.app.secret_key = 'test-secret'
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()

        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.db')
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
        conn.execute('''
            CREATE TABLE IF NOT EXISTS ad_unlock_grants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_user_id INTEGER NOT NULL,
                granted_at TEXT NOT NULL DEFAULT (datetime('now')),
                expires_at TEXT NOT NULL,
                ad_id TEXT,
                watch_seconds INTEGER NOT NULL,
                ip_address TEXT,
                user_agent TEXT,
                provider TEXT DEFAULT 'youtube',
                FOREIGN KEY (public_user_id) REFERENCES public_users(id)
            )
        ''')
        conn.commit()
        conn.close()

        # Patch get_db on the paywall module AND on the blueprint module BEFORE importing.
        def _tmp_db():
            c = sqlite3.connect(self.db_path)
            c.row_factory = sqlite3.Row
            return c

        import services.monetization.paywall as paywall_module
        self._orig_paywall_db = paywall_module.get_db
        paywall_module.get_db = _tmp_db

        # Register the blueprint under test.
        from blueprints.warrant_unlock import register_warrant_unlock_blueprint
        register_warrant_unlock_blueprint(self.app)

        # Stub the `auth.public_login` route that the unauthenticated branch
        # of /ad/watch redirects to (url_for needs a registered endpoint).
        from flask import Blueprint
        auth_stub = Blueprint('auth', __name__)

        @auth_stub.route('/login')
        def public_login():
            return 'login stub'

        self.app.register_blueprint(auth_stub)

        # Inject the template context vars the public_page_base.html nav
        # expects (normally provided by the app-level context processor).
        @self.app.context_processor
        def _inject_nav():
            return {
                'public_action_labels': {
                    'subscribe': 'Subscribe',
                    'subscribe_full': 'Subscribe to Warrant Access',
                    'signin': 'Sign In',
                },
                'public_primary_nav_items': [],
                'public_more_nav_groups': [],
                'public_secondary_nav_items': [],
                'public_nav_menu_labels_by_href': {},
                'public_nav_full_labels_by_href': {},
                'public_mobile_short_label_legend': {},
                'public_nav_experiment': {},
                'public_footer_items': [],
                'footer_featured_city_items': [],
                'public_user': None,
                'winter_storm_banner': None,
            }

        # Stub config: provide a YouTube video id so /ad/watch renders without
        # the "config_missing" branch firing.
        self._config_patches = [
            patch('blueprints.warrant_unlock.config.WARRANT_UNLOCK_YOUTUBE_VIDEO_ID', 'testvideo123'),
            patch('blueprints.warrant_unlock.config.WARRANT_UNLOCK_MIN_WATCH_SECONDS', 15),
            patch('blueprints.warrant_unlock.config.WARRANT_UNLOCK_DURATION_HOURS', 24),
            patch('blueprints.warrant_unlock.config.WARRANT_UNLOCK_RATE_LIMIT_PER_HOUR', 5),
            patch('blueprints.warrant_unlock.config.WARRANT_UNLOCK_NONCE_TTL_SECONDS', 600),
        ]
        for p in self._config_patches:
            p.start()

    def tearDown(self):
        for p in self._config_patches:
            p.stop()
        import services.monetization.paywall as paywall_module
        paywall_module.get_db = self._orig_paywall_db
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def _login_as(self, user_id: int):
        with self.client.session_transaction() as sess:
            sess['public_user_id'] = int(user_id)

    def test_blueprint_unauthenticated_rejected(self):
        """GET /ad/watch with no session should redirect to /login."""
        resp = self.client.get('/ad/watch')
        # Flask-Login-style redirect for "please log in" pattern in this app.
        self.assertIn(resp.status_code, (302, 401))

    def test_blueprint_no_active_nonce(self):
        """POST /api/ad-unlock/complete without first visiting /ad/watch should 400."""
        uid = _mk_public_user(self.db_path)
        self._login_as(uid)
        resp = self.client.post('/api/ad-unlock/complete', json={'nonce': 'whatever', 'watch_seconds': 20})
        self.assertEqual(resp.status_code, 400)
        body = resp.get_json()
        self.assertFalse(body['ok'])
        self.assertEqual(body['error'], 'no_active_nonce')

    def test_blueprint_nonce_mismatch(self):
        """POST with a nonce that doesn't match the session should 400."""
        uid = _mk_public_user(self.db_path)
        self._login_as(uid)
        # Prime the session with a nonce by hitting /ad/watch.
        self.client.get('/ad/watch')
        # Now POST with a different nonce.
        resp = self.client.post('/api/ad-unlock/complete', json={'nonce': 'wrong-nonce', 'watch_seconds': 20})
        self.assertEqual(resp.status_code, 400)
        body = resp.get_json()
        self.assertFalse(body['ok'])
        self.assertEqual(body['error'], 'nonce_mismatch')

    def test_blueprint_insufficient_watch(self):
        """POST with watch_seconds < minimum should 400 with insufficient_watch."""
        uid = _mk_public_user(self.db_path)
        self._login_as(uid)
        self.client.get('/ad/watch')
        # Pull the nonce out of the session.
        with self.client.session_transaction() as sess:
            nonce = sess.get('_ad_unlock_nonce')
        self.assertIsNotNone(nonce, 'Nonce should be issued by GET /ad/watch')
        resp = self.client.post(
            '/api/ad-unlock/complete',
            json={'nonce': nonce, 'watch_seconds': 5},
        )
        self.assertEqual(resp.status_code, 400)
        body = resp.get_json()
        self.assertFalse(body['ok'])
        self.assertEqual(body['error'], 'insufficient_watch')
        self.assertEqual(body['min_watch_seconds'], 15)

    def test_blueprint_successful_claim(self):
        """Happy path: visit /ad/watch, POST a sufficient watch, get a grant."""
        uid = _mk_public_user(self.db_path)
        self._login_as(uid)
        self.client.get('/ad/watch')
        with self.client.session_transaction() as sess:
            nonce = sess.get('_ad_unlock_nonce')
        resp = self.client.post(
            '/api/ad-unlock/complete',
            json={'nonce': nonce, 'watch_seconds': 20},
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        body = resp.get_json()
        self.assertTrue(body['ok'])
        self.assertIn('expires_at', body)
        self.assertEqual(body['duration_hours'], 24)
        self.assertGreater(body['remaining_seconds'], 0)
        # Verify a row was actually written.
        conn = sqlite3.connect(self.db_path)
        count = conn.execute(
            'SELECT COUNT(*) FROM ad_unlock_grants WHERE public_user_id = ?', (uid,)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_blueprint_nonce_consumed(self):
        """A successfully-used nonce cannot be replayed."""
        uid = _mk_public_user(self.db_path)
        self._login_as(uid)
        self.client.get('/ad/watch')
        with self.client.session_transaction() as sess:
            nonce = sess.get('_ad_unlock_nonce')
        # First claim succeeds.
        resp1 = self.client.post(
            '/api/ad-unlock/complete',
            json={'nonce': nonce, 'watch_seconds': 20},
        )
        self.assertEqual(resp1.status_code, 200)
        # Replaying the same nonce must fail (session nonce was popped).
        resp2 = self.client.post(
            '/api/ad-unlock/complete',
            json={'nonce': nonce, 'watch_seconds': 20},
        )
        self.assertEqual(resp2.status_code, 400)
        body2 = resp2.get_json()
        self.assertFalse(body2['ok'])
        self.assertEqual(body2['error'], 'no_active_nonce')


if __name__ == '__main__':
    unittest.main()
