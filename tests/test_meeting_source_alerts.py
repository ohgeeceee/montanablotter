import os
import tempfile
import unittest
from unittest import mock

import config
import init_db
import meeting_source_alerts
from public_meetings import ensure_public_meeting_schema


class MeetingSourceAlertsTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-meeting-alerts-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        init_db.migrate()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _seed_source(self, *, slug: str, last_success_sql: str, last_error: str | None = None) -> None:
        conn = meeting_source_alerts._connect()
        ensure_public_meeting_schema(conn)
        conn.execute(
            f'''
            INSERT INTO meeting_sources (
                slug, name, provider_type, source_url, meeting_scope, is_active, last_scraped_at, last_success_at, last_error
            ) VALUES (?, ?, 'custom_html', ?, 'city', 1, {last_success_sql}, {last_success_sql}, ?)
            ''',
            (slug, slug.replace('-', ' ').title(), f'https://example.gov/{slug}', last_error),
        )
        if last_error:
            conn.execute(
                '''
                UPDATE meeting_sources
                SET last_error = ?, last_scraped_at = datetime('now'), last_success_at = datetime('now', '-4 days')
                WHERE slug = ?
                ''',
                (last_error, slug),
            )
        conn.commit()
        conn.close()

    @mock.patch('meeting_source_alerts._send_plaintext_email', return_value=True)
    @mock.patch('meeting_source_alerts._collect_recipients', return_value=['alerts@example.com'])
    def test_run_creates_open_alert_for_failing_source(self, _collect_recipients: mock.Mock, _send_email: mock.Mock) -> None:
        self._seed_source(slug='broken-meeting-source', last_success_sql="datetime('now')", last_error='HTTP 500')

        rc = meeting_source_alerts.run()
        self.assertEqual(rc, 0)

        conn = meeting_source_alerts._connect()
        row = conn.execute(
            "SELECT source_slug, alert_kind, state, last_sent_at FROM meeting_source_alerts WHERE source_slug = 'broken-meeting-source'"
        ).fetchone()
        conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row['alert_kind'], 'error')
        self.assertEqual(row['state'], 'open')
        self.assertTrue(row['last_sent_at'])

    @mock.patch('meeting_source_alerts._send_plaintext_email', return_value=True)
    @mock.patch('meeting_source_alerts._collect_recipients', return_value=['alerts@example.com'])
    def test_run_resolves_alert_when_source_recovers(self, _collect_recipients: mock.Mock, send_email: mock.Mock) -> None:
        self._seed_source(slug='stale-meeting-source', last_success_sql="datetime('now', '-4 days')")

        first_rc = meeting_source_alerts.run()
        self.assertEqual(first_rc, 0)

        conn = meeting_source_alerts._connect()
        conn.execute(
            """
            UPDATE meeting_sources
            SET last_scraped_at = datetime('now'),
                last_success_at = datetime('now'),
                last_error = NULL
            WHERE slug = 'stale-meeting-source'
            """
        )
        conn.commit()
        conn.close()

        second_rc = meeting_source_alerts.run()
        self.assertEqual(second_rc, 0)

        conn = meeting_source_alerts._connect()
        row = conn.execute(
            "SELECT state, resolved_at FROM meeting_source_alerts WHERE source_slug = 'stale-meeting-source' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()

        self.assertEqual(row['state'], 'resolved')
        self.assertTrue(row['resolved_at'])
        self.assertEqual(send_email.call_count, 2)


if __name__ == '__main__':
    unittest.main()
