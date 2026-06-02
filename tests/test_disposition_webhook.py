"""
Tests for the disposition_api Stripe webhook handler.

Covers:
- _apply_disposition_api_stripe_event() provisions an api_data_tokens row
  on checkout.session.completed
- Idempotency: a second checkout event for the same subscription is a no-op
- Customer email fallback when client_reference_id is missing
- Subscription cancellation deactivates the token
- Token hash matches the plaintext via _hash_api_token (so _check_api_token works)
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest

import app as app_module
import config
import init_db
from app import (
    _apply_disposition_api_stripe_event,
    _hash_api_token,
    _new_api_token,
)


def _seed_user(conn: sqlite3.Connection, email: str = 'test@example.com') -> int:
    """Insert a public_user with a dummy password hash. Returns the user id."""
    cur = conn.execute(
        '''
        INSERT INTO public_users (email, password_hash, display_name)
        VALUES (?, ?, ?)
        ''',
        (email, 'sha256:fakehash', 'Test User'),
    )
    conn.commit()
    return cur.lastrowid


def _checkout_event(user_id: int, subscription_id: str, email: str) -> dict:
    """Build a minimal Stripe checkout.session.completed event."""
    return {
        'type': 'checkout.session.completed',
        'data': {
            'object': {
                'id': 'cs_test_123',
                'client_reference_id': str(user_id),
                'customer_email': email,
                'subscription': subscription_id,
                'metadata': {
                    'flow': 'disposition_api',
                    'public_user_id': str(user_id),
                },
            }
        },
    }


def _subscription_event(subscription_id: str, status: str) -> dict:
    return {
        'type': f'customer.subscription.{status}',
        'data': {
            'object': {
                'id': subscription_id,
                'status': status,
            }
        },
    }


class DispositionWebhookTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-disposition-webhook-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        init_db.migrate()
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row

    def tearDown(self) -> None:
        self.conn.close()
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = config.DB_PATH
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_provisions_token_on_checkout(self) -> None:
        user_id = _seed_user(self.conn)
        event = _checkout_event(user_id, 'sub_test_abc', 'test@example.com')
        _apply_disposition_api_stripe_event(self.conn, event)
        row = self.conn.execute(
            'SELECT * FROM api_data_tokens WHERE tier = ?', ('disposition',)
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row['tier'], 'disposition')
        self.assertEqual(row['user_id'], user_id)
        self.assertEqual(row['is_active'], 1)
        self.assertEqual(row['rate_limit_per_minute'], 30)
        self.assertTrue(row['label'].startswith('disposition:'))
        # Delivery row exists with plaintext
        delivery = self.conn.execute(
            'SELECT * FROM api_token_deliveries WHERE token_id = ?', (row['id'],)
        ).fetchone()
        self.assertIsNotNone(delivery)
        self.assertEqual(delivery['public_user_id'], user_id)
        self.assertEqual(delivery['email'], 'test@example.com')
        self.assertTrue(delivery['plaintext_token'].startswith('mb_live_'))
        # Hash matches the plaintext
        self.assertEqual(
            _hash_api_token(delivery['plaintext_token']),
            row['token_hash'],
        )

    def test_idempotent_on_duplicate_checkout(self) -> None:
        user_id = _seed_user(self.conn)
        event = _checkout_event(user_id, 'sub_test_dup', 'test@example.com')
        _apply_disposition_api_stripe_event(self.conn, event)
        _apply_disposition_api_stripe_event(self.conn, event)
        rows = self.conn.execute(
            'SELECT * FROM api_data_tokens WHERE tier = ?', ('disposition',)
        ).fetchall()
        self.assertEqual(len(rows), 1)

    def test_email_fallback_when_no_client_ref(self) -> None:
        user_id = _seed_user(self.conn, email='lookup@example.com')
        event = {
            'type': 'checkout.session.completed',
            'data': {
                'object': {
                    'id': 'cs_test_email',
                    'subscription': 'sub_test_email',
                    'customer_details': {'email': 'lookup@example.com'},
                    'metadata': {'flow': 'disposition_api'},
                }
            },
        }
        _apply_disposition_api_stripe_event(self.conn, event)
        row = self.conn.execute(
            'SELECT * FROM api_data_tokens WHERE tier = ?', ('disposition',)
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row['user_id'], user_id)

    def test_no_user_no_token(self) -> None:
        # No matching public_user — should be a no-op, no token created
        event = {
            'type': 'checkout.session.completed',
            'data': {
                'object': {
                    'id': 'cs_test_orphan',
                    'subscription': 'sub_test_orphan',
                    'customer_details': {'email': 'unknown@example.com'},
                    'metadata': {'flow': 'disposition_api'},
                }
            },
        }
        _apply_disposition_api_stripe_event(self.conn, event)
        rows = self.conn.execute(
            'SELECT * FROM api_data_tokens WHERE tier = ?', ('disposition',)
        ).fetchall()
        self.assertEqual(len(rows), 0)

    def test_subscription_cancel_deactivates_token(self) -> None:
        user_id = _seed_user(self.conn)
        event = _checkout_event(user_id, 'sub_test_cancel', 'test@example.com')
        _apply_disposition_api_stripe_event(self.conn, event)
        cancel_event = _subscription_event('sub_test_cancel', 'deleted')
        _apply_disposition_api_stripe_event(self.conn, cancel_event)
        row = self.conn.execute(
            'SELECT is_active FROM api_data_tokens WHERE label = ?',
            ('disposition:sub_test_cancel',),
        ).fetchone()
        self.assertEqual(row['is_active'], 0)

    def test_subscription_active_keeps_token(self) -> None:
        user_id = _seed_user(self.conn)
        _apply_disposition_api_stripe_event(
            self.conn, _checkout_event(user_id, 'sub_test_active', 'test@example.com')
        )
        _apply_disposition_api_stripe_event(
            self.conn, _subscription_event('sub_test_active', 'active')
        )
        row = self.conn.execute(
            'SELECT is_active FROM api_data_tokens WHERE label = ?',
            ('disposition:sub_test_active',),
        ).fetchone()
        self.assertEqual(row['is_active'], 1)

    def test_invoice_paid_is_noop(self) -> None:
        user_id = _seed_user(self.conn)
        _apply_disposition_api_stripe_event(
            self.conn, _checkout_event(user_id, 'sub_test_paid', 'test@example.com')
        )
        _apply_disposition_api_stripe_event(
            self.conn, {'type': 'invoice.paid', 'data': {'object': {}}}
        )
        rows = self.conn.execute(
            'SELECT * FROM api_data_tokens WHERE tier = ?', ('disposition',)
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['is_active'], 1)

    def test_new_api_token_format(self) -> None:
        token = _new_api_token()
        self.assertTrue(token.startswith('mb_live_'))
        self.assertGreaterEqual(len(token), 40)
        # Hash roundtrip
        h = _hash_api_token(token)
        self.assertEqual(len(h), 64)  # SHA-256 hex
        self.assertEqual(_hash_api_token(token), h)


if __name__ == '__main__':
    unittest.main()
