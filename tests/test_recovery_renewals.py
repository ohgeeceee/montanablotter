"""Tests for services.monetization.recovery_renewals — renewal projection logic."""
import os
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta

import app as app_module
import config
import init_db
from services.monetization.recovery_renewals import (
    days_until_renewal,
    find_upcoming_renewals,
    project_next_renewal,
)


class RecoveryRenewalsTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-renewals-', suffix='.db')
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
        self.today = date(2026, 6, 2)

    def tearDown(self) -> None:
        self.conn.close()
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = self.previous_app_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    # ----- project_next_renewal -----

    def test_project_monthly_renewal_in_past_steps_forward(self) -> None:
        activated = (self.today - timedelta(days=60)).strftime('%Y-%m-%d %H:%M:%S')
        result = project_next_renewal(activated, 'monthly', today=self.today)
        self.assertIsNotNone(result)
        self.assertGreaterEqual(result, self.today)
        self.assertLess(result, self.today + timedelta(days=31))

    def test_project_monthly_renewal_for_future_activation(self) -> None:
        activated = (self.today - timedelta(days=5)).strftime('%Y-%m-%d')
        result = project_next_renewal(activated, 'monthly', today=self.today)
        self.assertEqual(result, self.today + timedelta(days=25))

    def test_project_annual_renewal(self) -> None:
        activated = (self.today - timedelta(days=365)).strftime('%Y-%m-%d')
        result = project_next_renewal(activated, 'annual', today=self.today)
        self.assertEqual(result, self.today)

    def test_project_renewal_handles_garbage_input(self) -> None:
        self.assertIsNone(project_next_renewal(None, 'monthly'))
        self.assertIsNone(project_next_renewal('', 'monthly'))
        self.assertIsNone(project_next_renewal('not-a-date', 'monthly'))

    def test_project_renewal_handles_unknown_cycle_as_monthly(self) -> None:
        activated = (self.today - timedelta(days=10)).strftime('%Y-%m-%d')
        result = project_next_renewal(activated, 'biweekly', today=self.today)
        self.assertEqual(result, self.today + timedelta(days=20))

    # ----- days_until_renewal -----

    def test_days_until_renewal_positive(self) -> None:
        activated = (self.today - timedelta(days=10)).strftime('%Y-%m-%d')
        self.assertEqual(days_until_renewal(activated, 'monthly', today=self.today), 20)

    def test_days_until_renewal_zero(self) -> None:
        activated = (self.today - timedelta(days=30)).strftime('%Y-%m-%d')
        self.assertEqual(days_until_renewal(activated, 'monthly', today=self.today), 0)

    def test_days_until_renewal_returns_none_for_bad_input(self) -> None:
        self.assertIsNone(days_until_renewal(None, 'monthly'))
        self.assertIsNone(days_until_renewal('garbage', 'monthly'))

    # ----- find_upcoming_renewals -----

    def _insert_order(self, name, activated, billing='monthly', status='active'):
        self.conn.execute(
            '''
            INSERT INTO recovery_ad_orders
              (center_name, email, package_id, billing_cycle, status, token, activated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            (name, f'{name.lower().replace(" ", "")}@example.com',
             'silver', billing, status, f'tok-{name}', activated),
        )
        self.conn.commit()

    def test_find_upcoming_renewals_includes_within_window(self) -> None:
        activated_5d = (self.today - timedelta(days=25)).strftime('%Y-%m-%d %H:%M:%S')
        self._insert_order('Center A', activated_5d)
        activated_20d = (self.today - timedelta(days=10)).strftime('%Y-%m-%d %H:%M:%S')
        self._insert_order('Center B', activated_20d)

        result = find_upcoming_renewals(self.conn, within_days=7, today=self.today)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['center_name'], 'Center A')
        self.assertEqual(result[0]['days_until_renewal'], 5)

    def test_find_upcoming_renewals_excludes_inactive(self) -> None:
        activated = (self.today - timedelta(days=25)).strftime('%Y-%m-%d %H:%M:%S')
        self._insert_order('Inactive', activated, status='cancelled')
        result = find_upcoming_renewals(self.conn, within_days=7, today=self.today)
        self.assertEqual(len(result), 0)

    def test_find_upcoming_renewals_excludes_null_activation(self) -> None:
        self._insert_order('NoActivation', None)
        result = find_upcoming_renewals(self.conn, within_days=7, today=self.today)
        self.assertEqual(len(result), 0)

    def test_find_upcoming_renewals_sorted_by_soonest(self) -> None:
        self._insert_order('Later', (self.today - timedelta(days=22)).strftime('%Y-%m-%d %H:%M:%S'))
        self._insert_order('Sooner', (self.today - timedelta(days=27)).strftime('%Y-%m-%d %H:%M:%S'))
        result = find_upcoming_renewals(self.conn, within_days=10, today=self.today)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['center_name'], 'Sooner')
        self.assertEqual(result[1]['center_name'], 'Later')

    def test_find_upcoming_renewals_zero_window(self) -> None:
        activated = (self.today - timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
        self._insert_order('Today', activated)
        activated_far = (self.today - timedelta(days=25)).strftime('%Y-%m-%d %H:%M:%S')
        self._insert_order('NotToday', activated_far)
        result = find_upcoming_renewals(self.conn, within_days=0, today=self.today)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['center_name'], 'Today')
        self.assertEqual(result[0]['days_until_renewal'], 0)


if __name__ == '__main__':
    unittest.main()
