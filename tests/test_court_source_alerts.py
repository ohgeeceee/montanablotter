import os
import tempfile
import unittest
from unittest import mock

import app as app_module
import config
import init_db
from court_source_alerts import run
from court_tracker import ensure_court_tracker_schema, upsert_court_source


class CourtSourceAlertsTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-court-alerts-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        init_db.migrate()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = config.DB_PATH
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _connection(self):
        conn = app_module.get_db()
        ensure_court_tracker_schema(conn)
        return conn

    @mock.patch('court_source_alerts._collect_recipients', return_value=['alerts@example.com'])
    @mock.patch('court_source_alerts._send_plaintext_email', return_value=True)
    def test_run_creates_open_alert_for_failing_source(self, send_email, _collect_recipients) -> None:
        conn = self._connection()
        source_id = upsert_court_source(
            conn,
            slug='failing-source',
            name='Failing Source',
            source_url='https://example.gov/failing',
            provider_type='court_calendar',
        )
        conn.execute(
            "UPDATE court_sources SET last_scraped_at = datetime('now'), last_error = 'timeout' WHERE id = ?",
            (source_id,),
        )
        conn.commit()
        conn.close()

        result = run()
        self.assertEqual(result, 0)

        conn = self._connection()
        row = conn.execute(
            "SELECT source_slug, alert_kind, state, last_sent_at FROM court_source_alerts WHERE source_slug = ?",
            ('failing-source',),
        ).fetchone()
        conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row['alert_kind'], 'error')
        self.assertEqual(row['state'], 'open')
        self.assertIsNotNone(row['last_sent_at'])
        send_email.assert_called_once()

    @mock.patch('court_source_alerts._collect_recipients', return_value=['alerts@example.com'])
    @mock.patch('court_source_alerts._send_plaintext_email', return_value=True)
    def test_run_resolves_alert_when_source_recovers(self, send_email, _collect_recipients) -> None:
        conn = self._connection()
        source_id = upsert_court_source(
            conn,
            slug='recovering-source',
            name='Recovering Source',
            source_url='https://example.gov/recovering',
            provider_type='court_calendar',
        )
        conn.execute(
            "UPDATE court_sources SET last_success_at = datetime('now', '-2 days'), last_scraped_at = datetime('now', '-2 days') WHERE id = ?",
            (source_id,),
        )
        conn.commit()
        conn.close()

        run()

        conn = self._connection()
        conn.execute(
            "UPDATE court_sources SET last_success_at = datetime('now'), last_scraped_at = datetime('now'), last_error = NULL WHERE id = ?",
            (source_id,),
        )
        conn.commit()
        conn.close()

        run()

        conn = self._connection()
        row = conn.execute(
            "SELECT state, resolved_at FROM court_source_alerts WHERE source_slug = ? ORDER BY id DESC LIMIT 1",
            ('recovering-source',),
        ).fetchone()
        conn.close()

        self.assertEqual(row['state'], 'resolved')
        self.assertIsNotNone(row['resolved_at'])
        self.assertEqual(send_email.call_count, 2)


if __name__ == '__main__':
    unittest.main()
