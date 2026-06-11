import os
import importlib
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from blueprints import payments
import config
import init_db


class WarrantAccessCheckoutTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(prefix='mb-warrant-access-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        sys.modules.pop('app', None)
        self.app_module = importlib.import_module('app')
        self.app_module.app.config['TESTING'] = True
        self.client = self.app_module.app.test_client()

        bootstrap_conn = sqlite3.connect(self.db_path)
        bootstrap_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS public_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                display_name TEXT NOT NULL,
                subscriber_plan TEXT DEFAULT 'scout',
                subscription_status TEXT DEFAULT '',
                is_active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        bootstrap_conn.execute(
            """
            INSERT OR REPLACE INTO public_users (
                id, email, password_hash, display_name, subscriber_plan, subscription_status, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (7, 'trial-user@example.com', 'hash', 'Trial User', 'scout', '', 1),
        )
        bootstrap_conn.commit()
        bootstrap_conn.close()

        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            '''
            CREATE TABLE public_users (
                id INTEGER PRIMARY KEY,
                email TEXT,
                is_active INTEGER DEFAULT 1
            )
            '''
        )
        self.conn.execute(
            'INSERT INTO public_users (id, email, is_active) VALUES (?, ?, ?)',
            (7, 'trial-user@example.com', 1),
        )
        self.conn.commit()
        self._original_get_db = payments.get_db
        payments.get_db = lambda: self.conn

    def tearDown(self):
        payments.get_db = self._original_get_db
        self.conn.close()
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        sys.modules.pop('app', None)
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_checkout_redirects_to_stripe_checkout_session(self):
        with self.client.session_transaction() as session_:
            session_['public_user_id'] = 7

        fake_session = Mock()
        fake_session.url = 'https://checkout.stripe.com/c/pay/test_session'

        with patch('blueprints.payments.stripe.checkout.Session.create', return_value=fake_session) as create_mock:
            response = self.client.post('/checkout/warrant-access', follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], fake_session.url)
        create_mock.assert_called_once()
        kwargs = create_mock.call_args.kwargs
        self.assertEqual(kwargs['mode'], 'subscription')
        self.assertEqual(kwargs['client_reference_id'], '7')
        self.assertEqual(kwargs['customer_email'], 'trial-user@example.com')
        self.assertEqual(kwargs['metadata']['flow'], 'warrant_access')
        self.assertEqual(kwargs['metadata']['public_user_id'], '7')
        self.assertEqual(kwargs['subscription_data']['trial_period_days'], 7)
        self.assertEqual(kwargs['line_items'][0]['price'], 'price_1Td9jmGL8T8btZcumpWLuzLf')
        self.assertEqual(kwargs['line_items'][1]['price'], 'price_1Td9jmGL8T8btZcu5OXZzr9g')

    def test_checkout_uses_price_ids_from_environment(self):
        with self.client.session_transaction() as session_:
            session_['public_user_id'] = 7

        fake_session = Mock()
        fake_session.url = 'https://checkout.stripe.com/c/pay/test_session'

        env_overrides = {
            'MB_WARRANT_TRIAL_FEE_PRICE_ID': 'price_trial_env_override',
            'MB_WARRANT_MONTHLY_PRICE_ID': 'price_monthly_env_override',
        }
        with patch.dict(os.environ, env_overrides, clear=False):
            with patch('blueprints.payments.stripe.checkout.Session.create', return_value=fake_session) as create_mock:
                response = self.client.post('/checkout/warrant-access', follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], fake_session.url)
        kwargs = create_mock.call_args.kwargs
        self.assertEqual(kwargs['line_items'][0]['price'], env_overrides['MB_WARRANT_TRIAL_FEE_PRICE_ID'])
        self.assertEqual(kwargs['line_items'][1]['price'], env_overrides['MB_WARRANT_MONTHLY_PRICE_ID'])

    def test_wanted_subscribe_page_labels_paid_trial(self):
        with self.client.session_transaction() as session_:
            session_['public_user_id'] = 7

        response = self.client.get('/wanted/subscribe')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('This is the paid warrant access plan.', html)
        self.assertIn('Start Paid 7-Day Trial', html)
        self.assertIn('Secure Stripe checkout for paid warrant access', html)


if __name__ == '__main__':
    unittest.main()
