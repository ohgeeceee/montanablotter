import os
import tempfile
import unittest

import app as app_module
import config
import init_db


class SupportPageTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-support-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        app_module.app.config['TESTING'] = True

        init_db.migrate()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = config.DB_PATH
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_support_hub_renders_existing_monetization_paths(self) -> None:
        client = app_module.app.test_client()
        response = client.get('/support')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Support &amp; Partnerships', html)
        self.assertIn('Donate Now', html)
        self.assertIn('Open Bail Ad Program', html)
        self.assertIn('Advertise Recovery Center', html)

    def test_homepage_links_to_support_hub(self) -> None:
        template_path = os.path.join(os.path.dirname(app_module.__file__), 'templates', 'index.html')
        with open(template_path, 'r', encoding='utf-8') as handle:
            html = handle.read()

        self.assertIn('/support?source=homepage_top', html)
        self.assertIn('See support options', html)


if __name__ == '__main__':
    unittest.main()
