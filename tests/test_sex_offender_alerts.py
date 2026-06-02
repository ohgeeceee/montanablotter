"""Sex offender proximity alerts — signup, unsubscribe, and worker logic."""
import os
import sqlite3
import tempfile
import unittest

import app as app_module
import config
import init_db


class SexOffenderAlertsTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-so-alerts-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        self.previous_app_db_path = app_module.config.DB_PATH

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        app_module.app.config['TESTING'] = True

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

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = self.previous_app_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_get_renders_form(self) -> None:
        client = app_module.app.test_client()
        r = client.get('/sex-offender-alerts')
        html = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn('Proximity Alerts', html)
        self.assertIn('name="email"', html)
        self.assertIn('name="zip_code"', html)
        self.assertIn('name="radius"', html)

    def test_post_creates_subscription(self) -> None:
        client = app_module.app.test_client()
        r = client.post('/sex-offender-alerts', data={
            'email': 'so-selftest@example.com',
            'zip_code': '59715',  # Bozeman-area MT zip
            'radius': '5',
        })
        html = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn("You're subscribed", html)
        self.assertIn('59715', html)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            'SELECT email, zip_code, lat, lon, radius_miles, is_active, unsubscribe_token '
            'FROM sex_offender_alert_subscriptions WHERE email = ?',
            ('so-selftest@example.com',),
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row['zip_code'], '59715')
        self.assertEqual(row['radius_miles'], 5.0)
        self.assertEqual(row['is_active'], 1)
        self.assertIsNotNone(row['lat'])
        self.assertIsNotNone(row['lon'])
        self.assertGreater(len(row['unsubscribe_token'] or ''), 20)

    def test_post_rejects_invalid_zip(self) -> None:
        client = app_module.app.test_client()
        r = client.post('/sex-offender-alerts', data={
            'email': 'so-selftest@example.com',
            'zip_code': '99999',  # not a MT zip
            'radius': '5',
        })
        html = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn('not found in Montana', html)

    def test_post_rejects_bad_email(self) -> None:
        client = app_module.app.test_client()
        r = client.post('/sex-offender-alerts', data={
            'email': 'not-an-email',
            'zip_code': '59715',
            'radius': '5',
        })
        html = r.get_data(as_text=True)
        self.assertIn('valid email', html)

    def test_post_rejects_non_mt_zip(self) -> None:
        client = app_module.app.test_client()
        r = client.post('/sex-offender-alerts', data={
            'email': 'so-selftest@example.com',
            'zip_code': '10001',  # NYC, not MT
            'radius': '5',
        })
        html = r.get_data(as_text=True)
        self.assertIn('not found in Montana', html)

    def test_post_updates_existing_subscription(self) -> None:
        client = app_module.app.test_client()
        # First signup
        client.post('/sex-offender-alerts', data={
            'email': 'so-selftest@example.com',
            'zip_code': '59715',
            'radius': '5',
        })
        # Second signup with new radius — should update, not duplicate
        r = client.post('/sex-offender-alerts', data={
            'email': 'so-selftest@example.com',
            'zip_code': '59715',
            'radius': '10',
        })
        html = r.get_data(as_text=True)
        self.assertIn('updated', html.lower())

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        n = conn.execute(
            'SELECT COUNT(*) FROM sex_offender_alert_subscriptions WHERE email = ?',
            ('so-selftest@example.com',),
        ).fetchone()[0]
        radius = conn.execute(
            'SELECT radius_miles FROM sex_offender_alert_subscriptions WHERE email = ?',
            ('so-selftest@example.com',),
        ).fetchone()['radius_miles']
        conn.close()
        self.assertEqual(n, 1)
        self.assertEqual(radius, 10.0)

    def test_unsubscribe_via_token(self) -> None:
        client = app_module.app.test_client()
        client.post('/sex-offender-alerts', data={
            'email': 'so-selftest@example.com',
            'zip_code': '59715',
            'radius': '5',
        })
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        token = conn.execute(
            'SELECT unsubscribe_token FROM sex_offender_alert_subscriptions WHERE email = ?',
            ('so-selftest@example.com',),
        ).fetchone()['unsubscribe_token']
        conn.close()

        r = client.get(f'/sex-offender-alerts/unsubscribe?token={token}')
        html = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn('unsubscribed', html.lower())

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        active = conn.execute(
            'SELECT is_active FROM sex_offender_alert_subscriptions WHERE email = ?',
            ('so-selftest@example.com',),
        ).fetchone()['is_active']
        conn.close()
        self.assertEqual(active, 0)

    def test_unsubscribe_invalid_token_shows_error(self) -> None:
        client = app_module.app.test_client()
        r = client.get('/sex-offender-alerts/unsubscribe?token=fake-token-does-not-exist')
        html = r.get_data(as_text=True)
        self.assertIn('Invalid or expired', html)


if __name__ == '__main__':
    unittest.main()
