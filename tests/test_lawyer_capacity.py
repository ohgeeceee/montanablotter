"""County-inventory caps for /lawyers (1 Gold / 2 Silver / 2 Bronze per county)."""
import os
import sqlite3
import tempfile
import unittest

import app as app_module
import config
import init_db
from blueprints.lawyer_ads import (
    _county_active_capacity,
    apply_stripe_lawyer_ad_event,
)


def _make_active_order(conn, firm_name, email, counties, package_id, status='active'):
    cur = conn.execute(
        """INSERT INTO lawyer_ad_orders
           (firm_name, email, counties_served, package_id, status, paid_at)
           VALUES (?, ?, ?, ?, ?, datetime('now'))""",
        (firm_name, email, counties, package_id, status),
    )
    oid = cur.lastrowid
    conn.execute(
        """INSERT INTO lawyer_ad_listings
           (order_id, firm_name, counties_served, is_active)
           VALUES (?, ?, ?, 1)""",
        (oid, firm_name, counties),
    )
    return oid


def _make_event(session_id, firm_name, email, counties, package_id, subscription_id=None):
    return {
        'type': 'checkout.session.completed',
        'data': {'object': {
            'id': session_id,
            'customer': 'cus_test_123',
            'subscription': subscription_id or f'sub_test_{session_id[-4:]}',
            'metadata': {
                'flow': 'lawyer_ad',
                'firm_name': firm_name,
                'email': email,
                'counties_served': counties,
                'package_id': package_id,
                'billing_cycle': 'monthly',
            },
        }},
    }


class CapacityHelperTests(unittest.TestCase):
    def test_capacity_counts_only_active_orders(self):
        fd, path = tempfile.mkstemp(suffix='.db'); os.close(fd)
        try:
            conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
            init_db.ensure_lawyer_ad_schema(conn)
            _make_active_order(conn, 'A', 'a@x.com', 'Gallatin', 'gold')
            _make_active_order(conn, 'B', 'b@x.com', 'Gallatin', 'silver')
            _make_active_order(conn, 'C', 'c@x.com', 'Gallatin', 'silver')
            conn.execute(
                "UPDATE lawyer_ad_orders SET status='cancelled' WHERE firm_name='B'"
            )
            counts = _county_active_capacity(conn, 'Gallatin')
            # A is active gold; B is cancelled silver (excluded); C is active silver.
            self.assertEqual(counts, {'gold': 1, 'silver': 1, 'bronze': 0})

            counts_all = _county_active_capacity(conn, 'gallatin')
            self.assertEqual(counts_all, {'gold': 1, 'silver': 1, 'bronze': 0})
            conn.close()
        finally:
            os.unlink(path)

    def test_capacity_handles_no_listings(self):
        fd, path = tempfile.mkstemp(suffix='.db'); os.close(fd)
        try:
            conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
            init_db.ensure_lawyer_ad_schema(conn)
            self.assertEqual(
                _county_active_capacity(conn, 'Flathead'),
                {'gold': 0, 'silver': 0, 'bronze': 0},
            )
            conn.close()
        finally:
            os.unlink(path)


class CapacityWebhookTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(prefix='mb-lawyer-cap-', suffix='.db'); os.close(fd)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        init_db.ensure_lawyer_ad_schema(self.conn)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db_path)

    def test_gold_cap_rejects_extra_gold_in_same_county(self):
        _make_active_order(self.conn, 'First Gold', 'g1@x.com', 'Gallatin', 'gold')
        # Second gold attempt for the same county should not activate.
        apply_stripe_lawyer_ad_event(self.conn, _make_event(
            'cs_law_gold_2', 'Second Gold', 'g2@x.com', 'Gallatin', 'gold',
        ))
        row = self.conn.execute(
            "SELECT status FROM lawyer_ad_orders WHERE firm_name='Second Gold'"
        ).fetchone()
        self.assertEqual(row['status'], 'capacity_blocked')

    def test_silver_cap_stops_at_two(self):
        _make_active_order(self.conn, 'S1', 's1@x.com', 'Gallatin', 'silver')
        _make_active_order(self.conn, 'S2', 's2@x.com', 'Gallatin', 'silver')
        apply_stripe_lawyer_ad_event(self.conn, _make_event(
            'cs_law_silver_3', 'S3', 's3@x.com', 'Gallatin', 'silver',
        ))
        row = self.conn.execute(
            "SELECT status FROM lawyer_ad_orders WHERE firm_name='S3'"
        ).fetchone()
        self.assertEqual(row['status'], 'capacity_blocked')

    def test_cap_per_county_is_independent(self):
        _make_active_order(self.conn, 'A', 'a@x.com', 'Gallatin', 'gold')
        # Different county — should still activate.
        apply_stripe_lawyer_ad_event(self.conn, _make_event(
            'cs_law_gold_other', 'B', 'b@x.com', 'Yellowstone', 'gold',
        ))
        row = self.conn.execute(
            "SELECT status FROM lawyer_ad_orders WHERE firm_name='B'"
        ).fetchone()
        self.assertEqual(row['status'], 'active')

    def test_multi_county_order_blocked_when_any_served_county_is_full(self):
        _make_active_order(self.conn, 'Existing', 'e@x.com', 'Gallatin', 'gold')
        # New applicant serves Gallatin + Yellowstone. Gallatin gold is full;
        # the listing is blocked to avoid confusing partial coverage.
        apply_stripe_lawyer_ad_event(self.conn, _make_event(
            'cs_law_multi', 'Multi', 'm@x.com', 'Gallatin, Yellowstone', 'gold',
        ))
        row = self.conn.execute(
            "SELECT status FROM lawyer_ad_orders WHERE firm_name='Multi'"
        ).fetchone()
        self.assertEqual(row['status'], 'capacity_blocked')


if __name__ == '__main__':
    unittest.main()
