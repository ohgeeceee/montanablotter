"""
Test attorney sponsorship checkout — Stripe session redirect + webhook handler.

Mirrors tests/test_stripe_checkout_redirects.py: bootstraps its own temp DB,
stubs config values, and exercises the attorney_checkout blueprint directly.

Key behaviour under test:
  - POST /advertise/attorney-sponsorship/checkout with valid Stripe keys
    calls stripe.checkout.Session.create and redirects (303) to the checkout URL.
  - POST to the same endpoint with no STRIPE_SECRET_KEY returns 503.
  - apply_stripe_attorney_event(conn, event) is the webhook handler called from
    blueprints/payments.py.  It mutates conn directly and returns None.
    Tests assert on the DB state after the call, not on a return value.
"""
import importlib
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

import config
import init_db


ATTORNEY_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS attorney_checkout_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stripe_customer_id TEXT,
    stripe_subscription_id TEXT,
    stripe_session_id TEXT UNIQUE,
    firm_name TEXT NOT NULL,
    contact_name TEXT,
    email TEXT NOT NULL,
    phone TEXT,
    website TEXT,
    counties_served TEXT,
    practice_areas TEXT,
    blurb TEXT,
    mt_bar_number TEXT,
    package_id TEXT NOT NULL,
    billing_cycle TEXT NOT NULL DEFAULT 'monthly',
    status TEXT NOT NULL DEFAULT 'pending',
    token TEXT NOT NULL,
    activated_at TEXT,
    cancelled_at TEXT,
    amount_cents INTEGER NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS attorney_checkout_listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    token TEXT NOT NULL,
    firm_name TEXT NOT NULL,
    contact_name TEXT,
    email TEXT NOT NULL,
    phone TEXT,
    website TEXT,
    logo_path TEXT,
    photo_path TEXT,
    blurb TEXT,
    tagline TEXT,
    is_featured INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    montana_bar_verified INTEGER NOT NULL DEFAULT 0,
    montana_bar_member_at TEXT,
    is_disqualified INTEGER NOT NULL DEFAULT 0,
    disqualify_reason TEXT,
    placement_county TEXT,
    placement_tier TEXT,
    listing_position INTEGER,
    ttl_at TEXT,
    impressions INTEGER NOT NULL DEFAULT 0,
    clicks INTEGER NOT NULL DEFAULT 0,
    stripe_session_id TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_aco_status
    ON attorney_checkout_orders(status, created_at DESC);
"""


class AttorneyCheckoutSessionRedirectTests(unittest.TestCase):
    """POST /advertise/attorney-sponsorship/checkout returns a Stripe checkout URL."""

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(prefix='mb-asc-redirect-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        self.previous_stripe_secret = getattr(config, 'STRIPE_SECRET_KEY', '')
        self.previous_stripe_pub = getattr(config, 'STRIPE_PUBLISHABLE_KEY', '')
        self.previous_logo_dir = getattr(config, 'ATTORNEY_LOGO_DIR', '')

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        config.STRIPE_SECRET_KEY = 'sk_test_acts_like_real'
        config.STRIPE_PUBLISHABLE_KEY = 'pk_test_attorney_checkout'
        config.ATTORNEY_LOGO_DIR = os.path.join(self.db_path, 'logos')

        bootstrap_conn = sqlite3.connect(self.db_path)
        bootstrap_conn.executescript(ATTORNEY_TABLES_SQL)
        bootstrap_conn.commit()
        bootstrap_conn.close()

        sys.modules.pop('app', None)
        self.app_module = importlib.import_module('app')
        self.app_module.app.config['TESTING'] = True
        self.client = self.app_module.app.test_client()

    def tearDown(self):
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        config.STRIPE_SECRET_KEY = self.previous_stripe_secret
        config.STRIPE_PUBLISHABLE_KEY = self.previous_stripe_pub
        config.ATTORNEY_LOGO_DIR = self.previous_logo_dir
        sys.modules.pop('app', None)
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_checkout_redirects_with_listed_firm_contact(self):
        """Pre-filled checkout with a found_via_website contact returns 303 to Stripe."""
        fake_session = {
            'id': 'cs_test_attorney_sponsorship',
            'url': 'https://checkout.stripe.com/c/pay/test_sponsorship',
        }

        with patch('stripe.checkout.Session.create', return_value=fake_session):
            response = self.client.post(
                '/advertise/attorney-sponsorship/checkout',
                data={
                    'firm_name': 'Trailhead Law PLLC',
                    'contact_name': 'Nicholas Levi Owens',
                    'email': 'nick@trailheadlaw.com',
                    'phone': '(406) 555-0100',
                    'website': 'https://www.trailheadlaw.com/',
                    'counties_served': 'Yellowstone',
                    'practice_areas': 'DUI, criminal defense, litigation',
                    'blurb': 'Flat-fee DUI and criminal defense in Billings and statewide.',
                    'mt_bar_number': 'BAR-12345',
                    'package_id': 'silver',
                    'billing_cycle': 'monthly',
                    'terms_ack': 'yes',
                },
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers['Location'], fake_session['url'])

        # The blueprint does NOT insert an order on the checkout POST — only the
        # Stripe webhook handler does.  So the orders table should still be empty.
        conn = self.app_module.get_db()
        count = conn.execute(
            'SELECT COUNT(*) AS c FROM attorney_checkout_orders'
        ).fetchone()
        self.assertEqual(count['c'], 0)
        conn.close()

    def test_checkout_falls_back_to_form_when_no_stripe_key(self):
        """Missing STRIPE_SECRET_KEY returns 503 rather than starting checkout."""
        config.STRIPE_SECRET_KEY = ''
        config.STRIPE_PUBLISHABLE_KEY = ''

        response = self.client.post(
            '/advertise/attorney-sponsorship/checkout',
            data={
                'firm_name': 'Alpine Law',
                'contact_name': 'Joe Zavatsky',
                'email': 'contact@alpinelawmt.com',
                'package_id': 'silver',
                'billing_cycle': 'monthly',
                'terms_ack': 'yes',
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 503)
        body = response.data.decode('utf-8').lower()
        self.assertIn('secure checkout is not configured', body)


class AttorneyCheckoutWebhookTests(unittest.TestCase):
    """apply_stripe_attorney_event handles checkout.session.completed and
    customer.subscription.deleted idempotently."""

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(prefix='mb-asc-webhook-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        self.previous_stripe_secret = getattr(config, 'STRIPE_SECRET_KEY', '')
        self.previous_stripe_pub = getattr(config, 'STRIPE_PUBLISHABLE_KEY', '')
        self.previous_logo_dir = getattr(config, 'ATTORNEY_LOGO_DIR', '')

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        config.STRIPE_SECRET_KEY = 'sk_test_acts_like_real'
        config.STRIPE_PUBLISHABLE_KEY = 'pk_test_attorney_webhook'
        config.ATTORNEY_LOGO_DIR = os.path.join(self.db_path, 'logos')

        bootstrap_conn = sqlite3.connect(self.db_path)
        bootstrap_conn.executescript(ATTORNEY_TABLES_SQL)
        bootstrap_conn.commit()
        bootstrap_conn.close()

        sys.modules.pop('app', None)
        self.app_module = importlib.import_module('app')
        self.app_module.app.config['TESTING'] = True

    def tearDown(self):
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        config.STRIPE_SECRET_KEY = self.previous_stripe_secret
        config.STRIPE_PUBLISHABLE_KEY = self.previous_stripe_pub
        config.ATTORNEY_LOGO_DIR = self.previous_logo_dir
        sys.modules.pop('app', None)
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _insert_order(self, **kwargs):
        defaults = dict(
            stripe_customer_id='cus_test',
            stripe_session_id='cs_test',
            stripe_subscription_id='sub_test',
            firm_name='Test Firm PLLC',
            contact_name='Test Contact',
            email='contact@test.com',
            website='https://test.com/',
            package_id='silver',
            billing_cycle='monthly',
            status='pending',
            token='tok_test',
            amount_cents=9900,
        )
        defaults.update(kwargs)
        conn = self.app_module.get_db()
        conn.execute(
            """
            INSERT INTO attorney_checkout_orders
                (stripe_customer_id, stripe_session_id, stripe_subscription_id,
                 firm_name, contact_name, email, website,
                 package_id, billing_cycle, status, token, amount_cents)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (defaults['stripe_customer_id'], defaults['stripe_session_id'],
             defaults['stripe_subscription_id'], defaults['firm_name'],
             defaults['contact_name'], defaults['email'], defaults['website'],
             defaults['package_id'], defaults['billing_cycle'],
             defaults['status'], defaults['token'], defaults['amount_cents']),
        )
        conn.commit()
        conn.close()

    def test_webhook_checkout_completed_sets_active_and_listing(self):
        """checkout.session.completed marks the order active and inserts a listing."""
        from blueprints.attorney_checkout import apply_stripe_attorney_event

        self._insert_order(
            stripe_session_id='cs_test_completed',
            stripe_subscription_id='sub_AttorneyTest',
            firm_name='Holloway & Hulling PLLC',
            contact_name='Nathan Hulling',
            email='contact@montanalawyers.net',
            website='https://montanalawyers.net/',
            package_id='gold',
            token='tok_holloway',
        )

        event = {
            'type': 'checkout.session.completed',
            'data': {
                'object': {
                    'id': 'cs_test_completed',
                    'customer': 'cus_AttorneyTest',
                    'subscription': 'sub_AttorneyTest',
                    'mode': 'subscription',
                    'payment_status': 'paid',
                    'customer_email': 'contact@montanalawyers.net',
                    'metadata': {
                        'flow': 'attorney_sponsorship',
                        'firm_name': 'Holloway & Hulling PLLC',
                        'contact_name': 'Nathan Hulling',
                        'email': 'contact@montanalawyers.net',
                        'website': 'https://montanalawyers.net/',
                        'package_id': 'gold',
                        'billing_cycle': 'monthly',
                    },
                },
            },
        }

        conn = self.app_module.get_db()
        apply_stripe_attorney_event(conn, event)

        order = conn.execute(
            'SELECT status, package_id FROM attorney_checkout_orders '
            'WHERE stripe_session_id = ?',
            ('cs_test_completed',),
        ).fetchone()
        self.assertEqual(order['status'], 'active')
        self.assertEqual(order['package_id'], 'gold')

        listing = conn.execute(
            'SELECT firm_name, status, token FROM attorney_checkout_listings '
            'WHERE stripe_session_id = ?',
            ('cs_test_completed',),
        ).fetchone()
        self.assertIsNotNone(listing)
        self.assertEqual(listing['firm_name'], 'Holloway & Hulling PLLC')
        self.assertEqual(listing['status'], 'pending')
        self.assertEqual(listing['token'], 'tok_holloway')
        conn.close()

    def test_webhook_checkout_completed_idempotent(self):
        """Second checkout.session.completed for the same session is a no-op."""
        from blueprints.attorney_checkout import apply_stripe_attorney_event

        self._insert_order(
            stripe_session_id='cs_test_idempotent',
            stripe_subscription_id='sub_AttorneyTest2',
            firm_name='406 Legal',
            contact_name='Alex Neill',
            email='contact@406legal.com',
            website='https://www.406legal.com/',
            package_id='silver',
            token='tok_406legal',
        )

        event = {
            'type': 'checkout.session.completed',
            'data': {
                'object': {
                    'id': 'cs_test_idempotent',
                    'customer': 'cus_AttorneyTest',
                    'subscription': 'sub_AttorneyTest2',
                    'mode': 'subscription',
                    'payment_status': 'paid',
                    'customer_email': 'contact@406legal.com',
                    'metadata': {
                        'flow': 'attorney_sponsorship',
                        'firm_name': '406 Legal',
                        'contact_name': 'Alex Neill',
                        'email': 'contact@406legal.com',
                        'website': 'https://www.406legal.com/',
                        'package_id': 'silver',
                        'billing_cycle': 'monthly',
                    },
                },
            },
        }

        conn = self.app_module.get_db()
        apply_stripe_attorney_event(conn, event)
        apply_stripe_attorney_event(conn, event)

        count = conn.execute(
            'SELECT COUNT(*) AS c FROM attorney_checkout_listings '
            'WHERE stripe_session_id = ?',
            ('cs_test_idempotent',),
        ).fetchone()
        self.assertEqual(count['c'], 1)
        conn.close()

    def test_webhook_subscription_deleted_marks_cancelled(self):
        """customer.subscription.deleted flips the order status to cancelled."""
        from blueprints.attorney_checkout import apply_stripe_attorney_event

        self._insert_order(
            stripe_session_id='cs_unused',
            stripe_subscription_id='sub_CancelTest',
            firm_name='Watson Law Office PC',
            contact_name='Herman Watson',
            email='herman@watlawoffice.com',
            website='https://www.montanacriminallawyer.com/',
            package_id='silver',
            status='active',
            token='tok_watson',
        )

        event = {
            'type': 'customer.subscription.deleted',
            'data': {
                'object': {
                    'id': 'sub_CancelTest',
                    'customer': 'cus_CancelTest',
                    'status': 'canceled',
                    'customer_email': 'herman@watlawoffice.com',
                },
            },
        }

        conn = self.app_module.get_db()
        apply_stripe_attorney_event(conn, event)

        order = conn.execute(
            'SELECT status FROM attorney_checkout_orders '
            'WHERE stripe_subscription_id = ?',
            ('sub_CancelTest',),
        ).fetchone()
        self.assertEqual(order['status'], 'cancelled')
        conn.close()


if __name__ == '__main__':
    unittest.main()
