"""CSRF protection on advertising-checkout POSTs and the advertiser control panel."""
import os
import secrets
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import app as app_module
import config
import init_db
from blueprints.lawyer_ads import apply_stripe_lawyer_ad_event


def _make_token(client):
    # Hit any GET route so _csrf_token() runs and the token is committed to the session.
    token = client.application.config.get('SECRET_KEY', '')  # not used; placeholder
    with client.session_transaction() as sess:
        sess['_csrf_token'] = secrets.token_urlsafe(32)
        token = sess['_csrf_token']
    return token


class LawyerCheckoutCsrfTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(prefix='mb-lawyer-csrf-', suffix='.db')
        os.close(fd)
        self.previous_config_db = config.DB_PATH
        self.previous_app_db = app_module.config.DB_PATH
        config.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        init_db.ensure_lawyer_ad_schema(conn)
        conn.close()
        self.client = app_module.app.test_client()

    def tearDown(self):
        config.DB_PATH = self.previous_config_db
        app_module.config.DB_PATH = self.previous_app_db
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_lawyer_checkout_post_without_csrf_is_rejected(self):
        # No session token issued, no token submitted.
        with patch('blueprints.lawyer_ads._checkout_ready', return_value=True), \
             patch('blueprints.lawyer_ads._create_stripe_session') as mock_session:
            response = self.client.post(
                '/advertise/lawyers/checkout',
                data={
                    'firm_name': 'CSRF Test',
                    'contact_name': 'Owner',
                    'email': 'csrf@example.com',
                    'phone': '406-555-0100',
                    'bar_number': '12345',
                    'counties_served': 'Gallatin',
                    'package_id': 'bronze',
                    'billing_cycle': 'monthly',
                    'terms_ack': 'yes',
                },
                follow_redirects=False,
            )
        # No token => 400 with csrf_validation_failed
        self.assertEqual(response.status_code, 400)
        self.assertFalse(mock_session.called)
        # No order should be persisted
        conn = sqlite3.connect(self.db_path)
        count = conn.execute('SELECT COUNT(*) FROM lawyer_ad_orders').fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)

    def test_lawyer_checkout_post_with_valid_csrf_proceeds(self):
        # Prime a session token
        token = _make_token(self.client)
        self.assertTrue(token)
        with patch('blueprints.lawyer_ads._checkout_ready', return_value=True), \
             patch('blueprints.lawyer_ads._create_stripe_session') as mock_session:
            mock_session.return_value = {'url': 'https://stripe.test/cs_test_001'}
            response = self.client.post(
                '/advertise/lawyers/checkout',
                data={
                    'firm_name': 'CSRF OK',
                    'contact_name': 'Owner',
                    'email': 'csrfok@example.com',
                    'phone': '406-555-0100',
                    'bar_number': '12345',
                    'counties_served': 'Gallatin',
                    'package_id': 'bronze',
                    'billing_cycle': 'monthly',
                    'terms_ack': 'yes',
                    'csrf_token': token,
                },
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        self.assertTrue(mock_session.called)


class LawyerControlPanelCsrfTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(prefix='mb-lawyer-panel-csrf-', suffix='.db')
        os.close(fd)
        self.previous_config_db = config.DB_PATH
        self.previous_app_db = app_module.config.DB_PATH
        config.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        init_db.ensure_lawyer_ad_schema(conn)
        cur = conn.execute(
            """INSERT INTO lawyer_ad_orders
               (firm_name, email, counties_served, package_id, status, onboarding_token)
               VALUES ('CSRF Firm', 'panel@example.com', 'Gallatin', 'gold', 'active', 'tok-1')"""
        )
        self.order_id = cur.lastrowid
        conn.execute(
            """INSERT INTO lawyer_ad_listings
               (order_id, firm_name, counties_served, is_active)
               VALUES (?, 'CSRF Firm', 'Gallatin', 1)""",
            (self.order_id,),
        )
        conn.commit()
        conn.close()
        self.client = app_module.app.test_client()

    def tearDown(self):
        config.DB_PATH = self.previous_config_db
        app_module.config.DB_PATH = self.previous_app_db
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_control_panel_update_without_csrf_is_rejected(self):
        response = self.client.post(
            '/lawyer-control-panel/tok-1/update',
            data={
                'form_token': 'tok-1',
                'phone': '406-555-9999',
                'description': 'no token',
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 400)
        conn = sqlite3.connect(self.db_path)
        phone = conn.execute(
            'SELECT phone FROM lawyer_ad_orders WHERE id = ?', (self.order_id,)
        ).fetchone()[0]
        conn.close()
        self.assertNotEqual(phone, '406-555-9999')

    def test_control_panel_update_with_csrf_and_form_token_proceeds(self):
        token = _make_token(self.client)
        response = self.client.post(
            '/lawyer-control-panel/tok-1/update',
            data={
                'form_token': 'tok-1',
                'csrf_token': token,
                'phone': '406-555-1234',
                'description': 'with token',
                'cta_text': 'Call us',
                'target_url': 'https://example.com/contact',
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        conn = sqlite3.connect(self.db_path)
        phone = conn.execute(
            'SELECT phone FROM lawyer_ad_orders WHERE id = ?', (self.order_id,)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(phone, '406-555-1234')


if __name__ == '__main__':
    unittest.main()
