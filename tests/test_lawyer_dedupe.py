"""Impression / click / call dedupe for lawyer listings (24h per IP+listing)."""
import os
import sqlite3
import tempfile
import unittest

import app as app_module
import config
import init_db


def _seed(conn, firm='Dedupe Firm', counties='Gallatin', package='bronze', status='active'):
    cur = conn.execute(
        """INSERT INTO lawyer_ad_orders
           (firm_name, email, counties_served, package_id, status, paid_at)
           VALUES (?, ?, ?, ?, ?, datetime('now'))""",
        (firm, f'{firm.lower().replace(" ", "_")}@example.com', counties, package, status),
    )
    oid = cur.lastrowid
    cur = conn.execute(
        """INSERT INTO lawyer_ad_listings
           (order_id, firm_name, counties_served, is_active)
           VALUES (?, ?, ?, 1)""",
        (oid, firm, counties),
    )
    return oid, cur.lastrowid


class DedupeEventTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(prefix='mb-lawyer-dedupe-', suffix='.db'); os.close(fd)
        self.previous_config_db = config.DB_PATH
        self.previous_app_db = app_module.config.DB_PATH
        config.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        conn = sqlite3.connect(self.db_path); conn.row_factory = sqlite3.Row
        init_db.ensure_lawyer_ad_schema(conn)
        self.order_id, self.listing_id = _seed(conn)
        conn.commit(); conn.close()
        self.client = app_module.app.test_client()

    def tearDown(self):
        config.DB_PATH = self.previous_config_db
        app_module.config.DB_PATH = self.previous_app_db
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _post(self, event_type, **extra):
        return self.client.post(
            '/api/lawyer-ads/event',
            json={'event_type': event_type, 'order_id': self.order_id, 'county': 'Gallatin', **extra},
            environ_overrides={'REMOTE_ADDR': '203.0.113.10'},
            headers={'Origin': f'http://{self.client.application.config.get("MB_HOST", "localhost")}'},
        )

    def test_same_ip_same_day_impression_only_counted_once(self):
        first = self._post('impression')
        second = self._post('impression')
        self.assertEqual(first.status_code, 204)
        self.assertEqual(second.status_code, 204)
        conn = sqlite3.connect(self.db_path)
        impressions = conn.execute(
            'SELECT impressions FROM lawyer_ad_listings WHERE id = ?',
            (self.listing_id,),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(impressions, 1)

    def test_different_ip_impression_counts_separately(self):
        for addr in ('198.51.100.1', '198.51.100.1', '198.51.100.2'):
            self.client.post(
                '/api/lawyer-ads/event',
                json={'event_type': 'impression', 'order_id': self.order_id, 'county': 'Gallatin'},
                environ_overrides={'REMOTE_ADDR': addr},
                headers={'Origin': f'http://{self.client.application.config.get("MB_HOST", "localhost")}'},
            )
        conn = sqlite3.connect(self.db_path)
        impressions = conn.execute(
            'SELECT impressions FROM lawyer_ad_listings WHERE id = ?',
            (self.listing_id,),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(impressions, 2)


class ClicksNotDedupedTests(unittest.TestCase):
    """Click/call actions are NOT deduped. A repeat visitor should still register
    each intentional tap, and the audit's impression concern does not apply.
    Clicks + calls are atomic and explicit."""

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(prefix='mb-lawyer-click-', suffix='.db'); os.close(fd)
        self.previous_config_db = config.DB_PATH
        self.previous_app_db = app_module.config.DB_PATH
        config.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        conn = sqlite3.connect(self.db_path); conn.row_factory = sqlite3.Row
        init_db.ensure_lawyer_ad_schema(conn)
        self.order_id, self.listing_id = _seed(conn)
        conn.commit(); conn.close()
        self.client = app_module.app.test_client()

    def tearDown(self):
        config.DB_PATH = self.previous_config_db
        app_module.config.DB_PATH = self.previous_app_db
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_repeated_click_increments_each_time(self):
        for _ in range(3):
            self.client.post(
                '/api/lawyer-ads/event',
                json={'event_type': 'click', 'order_id': self.order_id, 'county': 'Gallatin'},
                environ_overrides={'REMOTE_ADDR': '203.0.113.10'},
                headers={'Origin': f'http://{self.client.application.config.get("MB_HOST", "localhost")}'},
            )
        conn = sqlite3.connect(self.db_path)
        clicks = conn.execute(
            'SELECT clicks FROM lawyer_ad_listings WHERE id = ?', (self.listing_id,)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(clicks, 3)


if __name__ == '__main__':
    unittest.main()
