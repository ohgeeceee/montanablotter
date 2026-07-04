import os
import tempfile
import unittest

import app as app_module
import config
import init_db


class PublicShellTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-public-shell-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        self.previous_app_db_path = getattr(app_module.config, 'DB_PATH', None)

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        app_module.app.config['TESTING'] = True

        init_db.init_database()
        init_db.migrate()
        self.client = app_module.app.test_client()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        if self.previous_app_db_path is not None:
            app_module.config.DB_PATH = self.previous_app_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_homepage_has_new_header(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        html = response.data.decode('utf-8')
        self.assertIn('mb-shell-header', html)
        self.assertIn('mb-shell-header__wordmark', html)
        self.assertIn('Montana Blotter', html)

    def test_homepage_has_desktop_nav_links(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        html = response.data.decode('utf-8')
        self.assertIn('mb-shell-header__nav', html)
        self.assertIn('mb-nav-link', html)
        self.assertIn('data-nav-location="header_primary"', html)

    def test_homepage_has_mobile_sheet(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        html = response.data.decode('utf-8')
        self.assertIn('mb-mobile-sheet', html)
        self.assertIn('mb-mobile-sheet__panel', html)
        self.assertIn('id="mb-mobile-toggle"', html)
        self.assertIn('id="mb-mobile-sheet-close"', html)

    def test_homepage_has_new_footer(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        html = response.data.decode('utf-8')
        self.assertIn('mb-shell-footer', html)
        self.assertIn('mb-footer-grid', html)
        self.assertIn('mb-footer-column__heading', html)
        self.assertIn('All systems operational', html)

    def test_public_page_uses_new_shell(self):
        # Use /counties rather than /county/missoula because the county hub
        # currently triggers a pre-existing schema mismatch on fresh test DBs.
        response = self.client.get('/counties')
        self.assertEqual(response.status_code, 200)
        html = response.data.decode('utf-8')
        self.assertIn('mb-shell-header', html)
        self.assertIn('mb-shell-footer', html)

    def test_static_shell_css_is_loadable(self):
        response = self.client.get('/static/styles/shell.css')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'mb-shell-header', response.data)
        self.assertIn(b'mb-shell-footer', response.data)
