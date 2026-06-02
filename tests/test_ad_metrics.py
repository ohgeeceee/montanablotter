"""Tests for services.monetization.ad_metrics — unified advertiser metrics."""
import os
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta

import app as app_module
import config
import init_db
from services.monetization.ad_metrics import (
    format_ctr,
    get_advertiser_metrics,
    get_bail_ad_metrics,
    get_recovery_ad_metrics,
)


class AdMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-admetrics-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        self.previous_app_db_path = app_module.config.DB_PATH

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        app_module.app.config['TESTING'] = True

        bootstrap = sqlite3.connect(self.db_path)
        bootstrap.execute(
            '''
            CREATE TABLE IF NOT EXISTS subscribers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                counties TEXT DEFAULT '',
                token TEXT NOT NULL,
                active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
            '''
        )
        bootstrap.commit()
        bootstrap.close()

        init_db.init_database()
        init_db.migrate()
        bootstrap = sqlite3.connect(self.db_path)
        init_db.ensure_recovery_ad_schema(bootstrap)
        bootstrap.commit()
        bootstrap.close()

        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row

    def tearDown(self) -> None:
        self.conn.close()
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = self.previous_app_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    # ----- format_ctr -----

    def test_format_ctr_zero_impressions(self) -> None:
        self.assertEqual(format_ctr(0, 0), 0.0)
        self.assertEqual(format_ctr(0, 5), 0.0)

    def test_format_ctr_basic(self) -> None:
        self.assertEqual(format_ctr(100, 25), 0.25)
        self.assertEqual(format_ctr(1000, 12), 0.012)

    def test_format_ctr_handles_negatives(self) -> None:
        # Defensive: negative inputs clamp to zero
        self.assertEqual(format_ctr(-5, 3), 0.0)

    def test_format_ctr_rounds_to_4_places(self) -> None:
        result = format_ctr(3, 1)
        self.assertEqual(result, 0.3333)

    # ----- get_recovery_ad_metrics -----

    def _insert_recovery_order(self, name, package_id, status='active'):
        cur = self.conn.execute(
            '''
            INSERT INTO recovery_ad_orders
              (center_name, email, package_id, billing_cycle, status, token, activated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            (name, f'{name.lower().replace(" ", "")}@example.com',
             package_id, 'monthly', status, f'tok-{name}',
             datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')),
        )
        self.conn.commit()
        order_id = cur.lastrowid
        self.conn.execute(
            'INSERT INTO recovery_ad_listings (order_id) VALUES (?)',
            (order_id,),
        )
        self.conn.commit()
        return order_id

    def _bump_recovery_counters(self, order_id, impressions, clicks):
        self.conn.execute(
            'UPDATE recovery_ad_listings SET impressions = ?, clicks = ? WHERE order_id = ?',
            (impressions, clicks, order_id),
        )
        self.conn.commit()

    def test_recovery_metrics_for_specific_order(self) -> None:
        oid = self._insert_recovery_order('Hope Center', 'silver')
        self._bump_recovery_counters(oid, 200, 10)
        result = get_recovery_ad_metrics(self.conn, order_id=oid)
        self.assertEqual(result['order_id'], oid)
        self.assertEqual(result['impressions'], 200)
        self.assertEqual(result['clicks'], 10)
        self.assertEqual(result['ctr'], 0.05)

    def test_recovery_metrics_for_unknown_order(self) -> None:
        result = get_recovery_ad_metrics(self.conn, order_id=9999)
        self.assertEqual(result['impressions'], 0)
        self.assertEqual(result['clicks'], 0)
        self.assertEqual(result['ctr'], 0.0)

    def test_recovery_metrics_all_orders_rolls_up_totals(self) -> None:
        a = self._insert_recovery_order('Center A', 'bronze')
        b = self._insert_recovery_order('Center B', 'gold')
        self._bump_recovery_counters(a, 100, 5)
        self._bump_recovery_counters(b, 300, 30)
        result = get_recovery_ad_metrics(self.conn)
        self.assertEqual(result['totals']['impressions'], 400)
        self.assertEqual(result['totals']['clicks'], 35)
        self.assertEqual(len(result['orders']), 2)

    # ----- get_bail_ad_metrics -----

    def _insert_bail_order(self, name, email):
        cur = self.conn.execute(
            '''
            INSERT INTO bail_ad_orders
              (business_name, email, package_id, status)
            VALUES (?, ?, ?, ?)
            ''',
            (name, email, 'gold', 'active'),
        )
        self.conn.commit()
        return cur.lastrowid

    def _log_bail_event(self, order_id, event_type, county='Gallatin'):
        self.conn.execute(
            '''
            INSERT INTO bail_ad_events
              (order_id, event_type, county)
            VALUES (?, ?, ?)
            ''',
            (order_id, event_type, county),
        )
        self.conn.commit()

    def test_bail_metrics_rolls_up_event_counts(self) -> None:
        oid = self._insert_bail_order('Bail Co', 'bail@example.com')
        for _ in range(5):
            self._log_bail_event(oid, 'impression')
        for _ in range(2):
            self._log_bail_event(oid, 'click')
        self._log_bail_event(oid, 'lead')
        self._log_bail_event(oid, 'call')
        result = get_bail_ad_metrics(self.conn, order_id=oid)
        self.assertEqual(result['events']['impression'], 5)
        self.assertEqual(result['events']['click'], 2)
        self.assertEqual(result['events']['lead'], 1)
        self.assertEqual(result['events']['call'], 1)
        self.assertEqual(result['events']['text'], 0)
        self.assertEqual(result['total'], 9)
        self.assertEqual(result['ctr'], 0.4)

    def test_bail_metrics_filter_by_county(self) -> None:
        a = self._insert_bail_order('A', 'a@example.com')
        b = self._insert_bail_order('B', 'b@example.com')
        self._log_bail_event(a, 'impression', county='Gallatin')
        self._log_bail_event(b, 'impression', county='Yellowstone')
        gallatin = get_bail_ad_metrics(self.conn, county='Gallatin')
        self.assertEqual(gallatin['events']['impression'], 1)
        yellowstone = get_bail_ad_metrics(self.conn, county='Yellowstone')
        self.assertEqual(yellowstone['events']['impression'], 1)

    def test_bail_metrics_no_filter_returns_empty(self) -> None:
        # No filter — should still return a clean shape, not error
        result = get_bail_ad_metrics(self.conn)
        self.assertEqual(result['total'], 0)
        self.assertEqual(result['ctr'], 0.0)

    # ----- get_advertiser_metrics -----

    def test_advertiser_metrics_combines_both_systems(self) -> None:
        rec_oid = self._insert_recovery_order('Acme Recovery', 'silver')
        self._bump_recovery_counters(rec_oid, 50, 1)
        bail_oid = self._insert_bail_order('Acme Recovery', 'ar@example.com')
        for _ in range(3):
            self._log_bail_event(bail_oid, 'impression')
        self._log_bail_event(bail_oid, 'click')

        result = get_advertiser_metrics(self.conn, business_name='Acme Recovery')
        self.assertEqual(len(result['recovery']['orders']), 1)
        self.assertEqual(result['recovery']['totals']['impressions'], 50)
        self.assertEqual(len(result['bail']['orders']), 1)
        self.assertEqual(result['bail']['events']['impression'], 3)
        self.assertEqual(result['bail']['events']['click'], 1)
        self.assertEqual(result['matched']['business_name'], 'Acme Recovery')

    def test_advertiser_metrics_email_only(self) -> None:
        oid = self._insert_bail_order('Some Bonds', 'matching@example.com')
        self._log_bail_event(oid, 'impression')
        result = get_advertiser_metrics(self.conn, email='matching@example.com')
        self.assertEqual(len(result['bail']['orders']), 1)
        self.assertEqual(result['recovery']['orders'], [])
        self.assertEqual(result['matched']['email'], 'matching@example.com')

    def test_advertiser_metrics_no_match(self) -> None:
        result = get_advertiser_metrics(self.conn, business_name='Nonexistent')
        self.assertEqual(result['recovery']['orders'], [])
        self.assertEqual(result['bail']['orders'], [])


if __name__ == '__main__':
    unittest.main()
