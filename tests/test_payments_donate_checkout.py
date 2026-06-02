import importlib
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import config
import init_db


class DonateCheckoutTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(prefix='mb-donate-checkout-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        self.previous_stripe_secret = getattr(config, 'STRIPE_SECRET_KEY', '')
        self.previous_stripe_publishable = getattr(config, 'STRIPE_PUBLISHABLE_KEY', '')

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        config.STRIPE_SECRET_KEY = 'sk_test_donate_checkout'
        config.STRIPE_PUBLISHABLE_KEY = 'pk_test_donate_checkout'

        sys.modules.pop('app', None)
        self.app_module = importlib.import_module('app')
        self.app_module.app.config['TESTING'] = True
        self.client = self.app_module.app.test_client()

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

    def tearDown(self):
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        config.STRIPE_SECRET_KEY = self.previous_stripe_secret
        config.STRIPE_PUBLISHABLE_KEY = self.previous_stripe_publishable
        sys.modules.pop('app', None)
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_donate_checkout_redirects_to_stripe_checkout_session(self):
        fake_session = Mock()
        fake_session.id = 'cs_test_session'
        fake_session.url = 'https://checkout.stripe.com/c/pay/test_session'
        fake_session.payment_intent = 'pi_test_intent'
        fake_session.subscription = 'sub_test_subscription'

        with patch('blueprints.payments.stripe.checkout.Session.create', return_value=fake_session) as create_mock:
            response = self.client.post(
                '/donate/checkout',
                data={
                    'mode': 'monthly',
                    'amount_cents': '2500',
                    'source': 'support_hub_reader',
                    'name': 'Jane Doe',
                    'email': 'jane@example.com',
                },
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], fake_session.url)
        create_mock.assert_called_once()

        kwargs = create_mock.call_args.kwargs
        self.assertEqual(kwargs['mode'], 'subscription')
        self.assertEqual(kwargs['customer_email'], 'jane@example.com')
        self.assertEqual(kwargs['success_url'], f'{self.app_module.BASE_URL}/donate/success?session_id={{CHECKOUT_SESSION_ID}}')
        self.assertEqual(kwargs['cancel_url'], f'{self.app_module.BASE_URL}/donate/cancel')
        self.assertEqual(kwargs['metadata']['source'], 'support_hub_reader')
        self.assertEqual(kwargs['metadata']['mode'], 'monthly')
        self.assertEqual(kwargs['metadata']['amount_cents'], '2500')
        self.assertEqual(kwargs['metadata']['donor_name'], 'Jane Doe')
        self.assertEqual(kwargs['line_items'][0]['price_data']['recurring']['interval'], 'month')
        self.assertEqual(kwargs['line_items'][0]['price_data']['unit_amount'], 2500)

        conn = self.app_module.get_db()
        row = conn.execute(
            """
            SELECT provider, mode, status, amount_cents, source, donor_name, provider_session_id
            FROM donations
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row['provider'], 'stripe')
        self.assertEqual(row['mode'], 'monthly')
        self.assertEqual(row['status'], 'pending')
        self.assertEqual(row['amount_cents'], 2500)
        self.assertEqual(row['source'], 'support_hub_reader')
        self.assertEqual(row['donor_name'], 'Jane Doe')
        self.assertEqual(row['provider_session_id'], 'cs_test_session')


if __name__ == '__main__':
    unittest.main()
