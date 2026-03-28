import os
import sqlite3
import tempfile
import unittest


def _make_conn(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


class RecoveryAdSchemaTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        self.conn = _make_conn(self.db_path)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db_path)

    def test_ensure_creates_orders_table(self):
        from init_db import ensure_recovery_ad_schema
        ensure_recovery_ad_schema(self.conn)
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='recovery_ad_orders'"
        ).fetchone()
        self.assertIsNotNone(row)

    def test_ensure_creates_listings_table(self):
        from init_db import ensure_recovery_ad_schema
        ensure_recovery_ad_schema(self.conn)
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='recovery_ad_listings'"
        ).fetchone()
        self.assertIsNotNone(row)

    def test_ensure_is_idempotent(self):
        from init_db import ensure_recovery_ad_schema
        ensure_recovery_ad_schema(self.conn)
        ensure_recovery_ad_schema(self.conn)  # should not raise


class RecoveryAdPackageTests(unittest.TestCase):
    def test_package_lookup_has_three_tiers(self):
        from blueprints.recovery_ads import _recovery_ad_package_lookup
        lookup = _recovery_ad_package_lookup()
        self.assertIn('bronze', lookup)
        self.assertIn('silver', lookup)
        self.assertIn('gold', lookup)

    def test_price_cents_monthly(self):
        from blueprints.recovery_ads import _recovery_ad_price_cents
        self.assertEqual(_recovery_ad_price_cents('bronze', 'monthly'), 9900)
        self.assertEqual(_recovery_ad_price_cents('silver', 'monthly'), 19900)
        self.assertEqual(_recovery_ad_price_cents('gold', 'monthly'), 39900)

    def test_price_cents_annual(self):
        from blueprints.recovery_ads import _recovery_ad_price_cents
        self.assertEqual(_recovery_ad_price_cents('bronze', 'annual'), 100900)
        self.assertEqual(_recovery_ad_price_cents('silver', 'annual'), 203000)
        self.assertEqual(_recovery_ad_price_cents('gold', 'annual'), 407000)

    def test_price_cents_unknown_package(self):
        from blueprints.recovery_ads import _recovery_ad_price_cents
        self.assertEqual(_recovery_ad_price_cents('platinum', 'monthly'), 0)


class RecoveryAdEventHandlerTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        self.conn = _make_conn(self.db_path)
        from init_db import ensure_recovery_ad_schema
        ensure_recovery_ad_schema(self.conn)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db_path)

    def _make_event(self, event_type, session_id, package_id='silver', billing_cycle='monthly'):
        return {
            'type': event_type,
            'data': {
                'object': {
                    'id': session_id,
                    'amount_total': 19900,
                    'currency': 'usd',
                    'customer': 'cus_test123',
                    'subscription': 'sub_test123',
                    'metadata': {
                        'flow': 'recovery_ad',
                        'package_id': package_id,
                        'billing_cycle': billing_cycle,
                        'center_name': 'Big Sky Recovery',
                        'contact_name': 'Jane Doe',
                        'email': 'jane@example.com',
                        'phone': '406-555-1234',
                        'website': 'https://bigskyrec.com',
                    },
                }
            },
        }

    def test_completed_event_activates_order(self):
        from blueprints.recovery_ads import apply_stripe_recovery_ad_event
        event = self._make_event('checkout.session.completed', 'cs_test_001')
        apply_stripe_recovery_ad_event(self.conn, event)
        row = self.conn.execute(
            "SELECT status, center_name FROM recovery_ad_orders WHERE stripe_session_id = ?",
            ('cs_test_001',),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row['status'], 'active')
        self.assertEqual(row['center_name'], 'Big Sky Recovery')

    def test_listing_row_created_on_activation(self):
        from blueprints.recovery_ads import apply_stripe_recovery_ad_event
        event = self._make_event('checkout.session.completed', 'cs_test_002')
        apply_stripe_recovery_ad_event(self.conn, event)
        order = self.conn.execute(
            "SELECT id FROM recovery_ad_orders WHERE stripe_session_id = ?",
            ('cs_test_002',),
        ).fetchone()
        listing = self.conn.execute(
            "SELECT order_id FROM recovery_ad_listings WHERE order_id = ?",
            (order['id'],),
        ).fetchone()
        self.assertIsNotNone(listing)

    def test_wrong_flow_is_ignored(self):
        from blueprints.recovery_ads import apply_stripe_recovery_ad_event
        event = self._make_event('checkout.session.completed', 'cs_test_003')
        event['data']['object']['metadata']['flow'] = 'bail_ad'
        apply_stripe_recovery_ad_event(self.conn, event)
        row = self.conn.execute(
            "SELECT id FROM recovery_ad_orders WHERE stripe_session_id = ?",
            ('cs_test_003',),
        ).fetchone()
        self.assertIsNone(row)

    def test_expired_event_sets_pending(self):
        from blueprints.recovery_ads import apply_stripe_recovery_ad_event
        event = self._make_event('checkout.session.expired', 'cs_test_004')
        apply_stripe_recovery_ad_event(self.conn, event)
        row = self.conn.execute(
            "SELECT status FROM recovery_ad_orders WHERE stripe_session_id = ?",
            ('cs_test_004',),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row['status'], 'expired')

    def test_idempotent_on_duplicate_session(self):
        from blueprints.recovery_ads import apply_stripe_recovery_ad_event
        event = self._make_event('checkout.session.completed', 'cs_test_005')
        apply_stripe_recovery_ad_event(self.conn, event)
        apply_stripe_recovery_ad_event(self.conn, event)  # second call must not raise
        count = self.conn.execute(
            "SELECT COUNT(*) FROM recovery_ad_orders WHERE stripe_session_id = ?",
            ('cs_test_005',),
        ).fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == '__main__':
    unittest.main()
