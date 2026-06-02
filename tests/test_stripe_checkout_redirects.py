import importlib
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

import config
import init_db


class SubscriptionCheckoutRedirectTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(prefix='mb-subscription-checkout-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        self.previous_stripe_secret = getattr(config, 'STRIPE_SECRET_KEY', '')
        self.previous_stripe_publishable = getattr(config, 'STRIPE_PUBLISHABLE_KEY', '')

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        config.STRIPE_SECRET_KEY = 'sk_test_subscription_checkout'
        config.STRIPE_PUBLISHABLE_KEY = 'pk_test_subscription_checkout'

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

        conn = self.app_module.get_db()
        conn.execute(
            """
            INSERT INTO public_users (id, email, display_name, password_hash, is_active, subscriber_plan)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (11, 'subscriber@example.com', 'Subscriber', 'hash', 1, 'free'),
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        config.STRIPE_SECRET_KEY = self.previous_stripe_secret
        config.STRIPE_PUBLISHABLE_KEY = self.previous_stripe_publishable
        sys.modules.pop('app', None)
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_subscription_checkout_redirects_with_dict_session(self):
        fake_session = {
            'id': 'cs_test_subscription',
            'url': 'https://checkout.stripe.com/c/pay/test_subscription',
        }

        with self.client.session_transaction() as session_:
            session_['public_user_id'] = 11

        with patch('blueprints.payments.stripe.checkout.Session.create', return_value=fake_session):
            response = self.client.post(
                '/checkout/subscription',
                data={'plan': 'insider', 'interval': 'monthly'},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], fake_session['url'])


class WatchdogCheckoutRedirectTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(prefix='mb-watchdog-checkout-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        self.previous_stripe_secret = getattr(config, 'STRIPE_SECRET_KEY', '')
        self.previous_stripe_publishable = getattr(config, 'STRIPE_PUBLISHABLE_KEY', '')

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        config.STRIPE_SECRET_KEY = 'sk_test_watchdog_checkout'
        config.STRIPE_PUBLISHABLE_KEY = 'pk_test_watchdog_checkout'

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
        bootstrap_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS watchdog_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                name TEXT,
                organization TEXT,
                tier TEXT NOT NULL,
                counties TEXT,
                beats TEXT,
                is_nonprofit INTEGER DEFAULT 0,
                token TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                verified INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(email, tier)
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

    def test_watchdog_checkout_redirects_with_dict_session(self):
        fake_session = {
            'id': 'cs_test_watchdog',
            'url': 'https://checkout.stripe.com/c/pay/test_watchdog',
        }

        with patch('blueprints.watchdog.stripe.checkout.Session.create', return_value=fake_session):
            response = self.client.post(
                '/watchdog/subscribe',
                data={
                    'email': 'reporter@example.com',
                    'name': 'Reporter',
                    'organization': 'Newsroom',
                    'counties': 'Gallatin',
                    'beats': 'Courts',
                },
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], fake_session['url'])


if __name__ == '__main__':
    unittest.main()
