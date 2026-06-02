import os
import tempfile
import unittest

import app as app_module
import config
import init_db


class PricingPageTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-pricing-', suffix='.db')
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

    def test_pricing_page_monthly_buttons_use_direct_stripe_link(self) -> None:
        client = app_module.app.test_client()
        response = client.get('/pricing')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(html.count('https://buy.stripe.com/14A4gzajyeoAcDU4qh8EM03'), 2)


if __name__ == '__main__':
    unittest.main()
