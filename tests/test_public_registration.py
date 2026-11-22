"""Regression coverage for the public web registration flow at /register.

The mobile API has its own registration tests (tests/test_mobile_auth.py),
but the browser flow — which powers the site sign-up page — had no direct
coverage. These tests exercise the exact path a new customer takes:
render the form, POST valid details, land in a session, and optionally
join the county digest at the same time.
"""
import importlib
import os
import sqlite3
import sys
import tempfile
import unittest

import config
import init_db


class PublicRegistrationTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(prefix='mb-register-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        sys.modules.pop('app', None)
        self.app_module = importlib.import_module('app')
        self.app_module.app.config['TESTING'] = True
        # Point the freshly imported app's own config object at this test's
        # DB too. Other tests (e.g. test_datasets_pages) pop 'config'/'db'
        # from sys.modules, so the reimported app can end up bound to a
        # different config module than the one imported at the top of this
        # file; the register route would then write to one DB while
        # _load_public_user reads from another. Same pattern as
        # SigninWallTestCase toggling self.app_module.config. Restore the
        # path in tearDown so later tests reusing this cached config module
        # don't inherit a deleted DB file.
        self._app_config = self.app_module.config
        self._previous_app_config_db_path = self._app_config.DB_PATH
        self._app_config.DB_PATH = self.db_path
        self.client = self.app_module.app.test_client()
        with self.client.session_transaction() as s:
            s.clear()

        init_db.init_database()
        init_db.migrate()

    def tearDown(self):
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        self._app_config.DB_PATH = self._previous_app_config_db_path
        sys.modules.pop('app', None)
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _query(self, sql, params=()):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def _register(self, **overrides):
        payload = {
            'display_name': 'New Customer',
            'email': 'new-customer@example.com',
            'password': 'SuperSecret1!',
            'confirm_password': 'SuperSecret1!',
            'next': '/',
        }
        payload.update(overrides)
        return self.client.post('/register', data=payload, follow_redirects=False)

    def test_register_page_renders_form(self):
        response = self.client.get('/register')
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        for field in ('display_name', 'email', 'password', 'confirm_password'):
            self.assertIn(f'name="{field}"', body)

    def test_valid_registration_creates_user_and_session(self):
        response = self._register()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/')

        rows = self._query(
            "SELECT id, email, display_name, is_active, password_hash "
            "FROM public_users WHERE email = ?",
            ('new-customer@example.com',),
        )
        self.assertEqual(len(rows), 1)
        user = rows[0]
        self.assertEqual(user['display_name'], 'New Customer')
        self.assertEqual(user['is_active'], 1)
        self.assertTrue(user['password_hash'])

        with self.client.session_transaction() as session_:
            self.assertEqual(session_.get('public_user_id'), user['id'])

    def test_password_is_bcrypt_hashed(self):
        self._register()
        row = self._query(
            "SELECT password_hash FROM public_users WHERE email = ?",
            ('new-customer@example.com',),
        )[0]
        self.assertTrue(row['password_hash'].startswith('$2'))
        self.assertNotIn('SuperSecret1!', row['password_hash'])

    def test_duplicate_email_is_rejected(self):
        self._register()
        # A successful registration signs the user in; /register redirects
        # signed-in visitors before looking at the form, so start anonymous.
        with self.client.session_transaction() as s:
            s.clear()
        response = self._register(display_name='Someone Else')
        self.assertEqual(response.status_code, 200)
        count = self._query(
            "SELECT COUNT(*) AS c FROM public_users WHERE email = ?",
            ('new-customer@example.com',),
        )[0]['c']
        self.assertEqual(count, 1)

    def test_invalid_submissions_create_no_user(self):
        cases = {
            'short display name': {'display_name': 'A'},
            'missing email': {'email': ''},
            'malformed email': {'email': 'not-an-email'},
            'short password': {'password': 'short', 'confirm_password': 'short'},
            'mismatched passwords': {'confirm_password': 'Different1!'},
        }
        for label, overrides in cases.items():
            with self.subTest(case=label):
                response = self._register(**overrides)
                self.assertEqual(response.status_code, 200)
                total = self._query('SELECT COUNT(*) AS c FROM public_users')[0]['c']
                self.assertEqual(total, 0, f'{label} should not create a user')

    def test_next_param_is_sanitized(self):
        response = self._register(next='https://evil.example.com/steal')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/')

    def test_digest_opt_in_creates_subscription_and_event(self):
        response = self._register(
            subscribe_digest='1',
            counties=['Gallatin', 'Gallatin', ' Flathead '],
        )
        self.assertEqual(response.status_code, 302)

        subs = self._query(
            "SELECT counties, active, source FROM subscribers WHERE email = ?",
            ('new-customer@example.com',),
        )
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]['counties'], 'Gallatin,Flathead')
        self.assertEqual(subs[0]['active'], 1)
        self.assertEqual(subs[0]['source'], 'account_registration')

        events = self._query(
            "SELECT event_type, source FROM subscribe_events "
            "WHERE event_type = 'subscribe_success' AND source = 'account_registration'"
        )
        self.assertEqual(len(events), 1)

        user = self._query(
            "SELECT subscribe_digest FROM public_users WHERE email = ?",
            ('new-customer@example.com',),
        )[0]
        self.assertEqual(user['subscribe_digest'], 1)

    def test_already_signed_in_user_is_redirected_away(self):
        # A successful registration leaves the client signed in; the register
        # page should bounce an authenticated visitor to the site instead.
        self._register()
        response = self.client.get('/register', follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('/register', response.headers['Location'])


if __name__ == '__main__':
    unittest.main()
