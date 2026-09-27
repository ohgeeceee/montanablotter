import os
import tempfile
import unittest

import app as app_module
import config
import init_db


class AdminGeocodeStatusTests(unittest.TestCase):
    """Regression: /admin/geocode/status 500'd for anonymous users because the
    login redirect used url_for('auth.admin_login'), an endpoint that does not
    exist (the login view is admin.admin_login). BuildError raised on every
    guest hit. Fixed in blueprints/admin_geocode.py."""

    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-geocode-status-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        self.previous_app_db_path = app_module.config.DB_PATH

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        init_db.init_database()
        init_db.migrate()
        app_module.app.config['TESTING'] = True
        self.client = app_module.app.test_client()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = self.previous_app_db_path
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_anonymous_redirects_to_admin_login_not_500(self) -> None:
        resp = self.client.get('/admin/geocode/status')
        # Before the fix this was a 500 (BuildError: 'auth.admin_login').
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/admin/login', resp.headers['Location'])

    def test_admin_session_reaches_json_status(self) -> None:
        with self.client.session_transaction() as sess:
            sess['admin_logged_in'] = True
        resp = self.client.get('/admin/geocode/status')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn('total_with_location', data)
        self.assertIn('geocoded', data)


if __name__ == '__main__':
    unittest.main()
