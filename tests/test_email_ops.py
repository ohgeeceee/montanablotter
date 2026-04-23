import sqlite3
import os
import tempfile
import unittest
from unittest import mock

import app as app_module
import config
import init_db


class EmailOpsTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-email-ops-', suffix='.db')
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
        self.admin_user_id = self._create_admin_user()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = self.previous_app_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _create_admin_user(self) -> int:
        conn = app_module.get_db()
        cursor = conn.execute(
            """
            INSERT INTO users (username, password, email, role, is_active)
            VALUES (?, ?, ?, ?, ?)
            """,
            ('ops-admin', 'not-used-in-tests', 'ops@example.com', 'ops', 1),
        )
        conn.commit()
        conn.close()
        return int(cursor.lastrowid)

    def _seed_post(self, target_date: str) -> None:
        conn = app_module.get_db()
        blotter_id = conn.execute(
            """
            INSERT INTO blotters (filename, county, status)
            VALUES (?, ?, ?)
            """,
            ('digest-source.pdf', 'Yellowstone', 'processed'),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO posts (
                record_id, blotter_id, title, summary, county, agency_name, incident_date, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                None,
                blotter_id,
                'Daily booking summary',
                'One useful digest post for preview rendering.',
                'Yellowstone',
                'Billings Police Department',
                target_date,
                f'{target_date} 08:00:00',
            ),
        )
        conn.commit()
        conn.close()

    def _seed_subscriber(self, email: str, counties: str = '', token: str = 'tok-test', active: int = 1) -> None:
        conn = app_module.get_db()
        conn.execute(
            """
            INSERT INTO subscribers (email, counties, token, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (email, counties, token, active),
        )
        conn.commit()
        conn.close()

    def _login_admin_session(self, client) -> str:
        csrf_token = 'test-csrf-token'
        with client.session_transaction() as session:
            session['_user_id'] = str(self.admin_user_id)
            session['_fresh'] = True
            session['_csrf_token'] = csrf_token
        return csrf_token

    def test_build_preview_includes_selected_run_details_and_failures(self) -> None:
        target_date = '2026-03-17'
        self._seed_post(target_date)
        conn = app_module.get_db()
        app_module._ensure_subscriber_schema(conn)
        run_id = app_module._record_digest_run(
            conn,
            kind='morning_briefing',
            target_date=target_date,
            audience='subscribers',
            status='completed_with_errors',
            subject='Montana Blotter Briefing - Mar 17, 2026',
            preview_posts=1,
            preview_subscribers=2,
            initiated_by='ops-admin',
            notes='SMTP timeout on one recipient.',
            created_by_user_id=self.admin_user_id,
        )
        app_module._record_digest_run_recipient(
            conn,
            run_id,
            'sent@example.com',
            'Yellowstone',
            'sent',
            post_count=1,
        )
        app_module._record_digest_run_recipient(
            conn,
            run_id,
            'failed@example.com',
            '',
            'failed',
            post_count=1,
            error_message='smtp timeout',
        )
        app_module._finish_digest_run(
            conn,
            run_id,
            status='completed_with_errors',
            sent_count=1,
            failed_count=1,
            notes='SMTP timeout on one recipient.',
        )
        conn.commit()
        conn.close()

        preview = app_module._build_email_ops_preview(target_date, selected_run_id=run_id)

        self.assertEqual(preview['selected_run']['id'], run_id)
        self.assertEqual(preview['selected_run']['failed_count'], 1)
        self.assertEqual(len(preview['selected_run_recipients']), 2)
        self.assertEqual(preview['selected_run_recipients'][0]['status'], 'failed')
        self.assertEqual(preview['recent_failures'][0]['run_id'], run_id)
        self.assertEqual(preview['recent_failures'][0]['recipient_email'], 'failed@example.com')

    @mock.patch('app.send_morning_briefing_email')
    def test_retry_failed_digest_recipients_creates_retry_run(self, send_email_mock: mock.Mock) -> None:
        target_date = '2026-03-17'
        self._seed_post(target_date)
        self._seed_subscriber('retry@example.com', counties='Yellowstone', token='retry-token')
        conn = app_module.get_db()
        app_module._ensure_subscriber_schema(conn)
        original_run_id = app_module._record_digest_run(
            conn,
            kind='morning_briefing',
            target_date=target_date,
            audience='subscribers',
            status='failed',
            subject='Montana Blotter Briefing - Mar 17, 2026',
            preview_posts=1,
            preview_subscribers=1,
            initiated_by='ops-admin',
            notes='Original failed run.',
            created_by_user_id=self.admin_user_id,
        )
        app_module._record_digest_run_recipient(
            conn,
            original_run_id,
            'retry@example.com',
            'Yellowstone',
            'failed',
            post_count=1,
            error_message='smtp timeout',
        )
        app_module._finish_digest_run(conn, original_run_id, status='failed', failed_count=1)
        conn.commit()
        conn.close()

        results = app_module._retry_failed_digest_recipients(original_run_id, initiated_by='ops-admin')

        self.assertEqual(results['sent_count'], 1)
        self.assertEqual(results['skipped_count'], 0)
        self.assertEqual(results['failed_count'], 0)
        send_email_mock.assert_called_once()
        self.assertEqual(send_email_mock.call_args.args[0], 'retry@example.com')

        verify_conn = app_module.get_db()
        retry_run = verify_conn.execute(
            "SELECT audience, notes, sent_count, failed_count FROM digest_runs WHERE id = ?",
            (results['run_id'],),
        ).fetchone()
        retry_recipient = verify_conn.execute(
            "SELECT recipient_email, status FROM digest_run_recipients WHERE run_id = ?",
            (results['run_id'],),
        ).fetchone()
        verify_conn.close()

        self.assertEqual(retry_run['audience'], 'retry_failed')
        self.assertIn(f'#{original_run_id}', retry_run['notes'])
        self.assertEqual(retry_run['sent_count'], 1)
        self.assertEqual(retry_run['failed_count'], 0)
        self.assertEqual(retry_recipient['recipient_email'], 'retry@example.com')
        self.assertEqual(retry_recipient['status'], 'sent')

    @mock.patch('app.send_morning_briefing_email')
    def test_admin_send_test_uses_custom_recipient_override(self, send_email_mock: mock.Mock) -> None:
        target_date = '2026-03-17'
        self._seed_post(target_date)
        client = app_module.app.test_client()
        csrf_token = self._login_admin_session(client)

        response = client.post(
            '/admin/audience/email-ops/send-test',
            data={
                'csrf_token': csrf_token,
                'target_date': target_date,
                'recipient_email': 'custom-test@example.com',
            },
            follow_redirects=True,
        )
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        send_email_mock.assert_called_once()
        self.assertEqual(send_email_mock.call_args.args[0], 'custom-test@example.com')
        self.assertIn('Test digest sent to custom-test@example.com.', html)

    def test_admin_sponsor_draft_save_persists_settings(self) -> None:
        client = app_module.app.test_client()
        csrf_token = self._login_admin_session(client)

        response = client.post(
            '/admin/audience/email-ops/sponsor-draft',
            data={
                'csrf_token': csrf_token,
                'target_date': '2026-03-17',
                'sponsor_subject': 'Montana Blotter Update: New Sponsor',
                'sponsor_name': 'Big Sky Legal Support',
                'sponsor_headline': 'A new sponsor is supporting statewide coverage.',
                'sponsor_body': 'Paragraph one.\n\nParagraph two.',
                'sponsor_cta_label': 'Visit Sponsor',
                'sponsor_cta_url': 'https://sponsor.example.com',
                'sponsor_notes': 'announce next Tuesday',
            },
            follow_redirects=True,
        )
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Sponsor announcement draft saved.', html)

        conn = app_module.get_db()
        rows = conn.execute(
            "SELECT key, value FROM app_settings WHERE key LIKE 'email_ops.sponsor_announcement.%'"
        ).fetchall()
        conn.close()
        settings = {row['key']: row['value'] for row in rows}

        self.assertEqual(
            settings['email_ops.sponsor_announcement.sponsor_name'],
            'Big Sky Legal Support',
        )
        self.assertEqual(
            settings['email_ops.sponsor_announcement.cta_url'],
            'https://sponsor.example.com',
        )
        self.assertEqual(
            settings['email_ops.sponsor_announcement.notes'],
            'announce next Tuesday',
        )

    @mock.patch('app.send_morning_briefing_email')
    def test_admin_sponsor_test_send_uses_custom_recipient(self, send_email_mock: mock.Mock) -> None:
        client = app_module.app.test_client()
        csrf_token = self._login_admin_session(client)

        response = client.post(
            '/admin/audience/email-ops/sponsor-test',
            data={
                'csrf_token': csrf_token,
                'target_date': '2026-03-17',
                'recipient_email': 'preview@example.com',
                'sponsor_subject': 'Montana Blotter Update: New Sponsor',
                'sponsor_name': 'Big Sky Legal Support',
                'sponsor_headline': 'A new sponsor is supporting statewide coverage.',
                'sponsor_body': 'Paragraph one.\n\nParagraph two.',
                'sponsor_cta_label': 'Visit Sponsor',
                'sponsor_cta_url': 'https://sponsor.example.com',
                'sponsor_notes': '',
            },
            follow_redirects=True,
        )
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        send_email_mock.assert_called_once()
        self.assertEqual(send_email_mock.call_args.args[0], 'preview@example.com')
        self.assertEqual(send_email_mock.call_args.args[1], '[TEST] Montana Blotter Update: New Sponsor')
        self.assertIn('Big Sky Legal Support', send_email_mock.call_args.args[2])
        self.assertIn('https://sponsor.example.com', send_email_mock.call_args.args[2])
        self.assertIn('Sponsor test email sent to preview@example.com.', html)

    @mock.patch('app.send_morning_briefing_email')
    def test_admin_sponsor_send_now_targets_active_subscribers(self, send_email_mock: mock.Mock) -> None:
        self._seed_subscriber('active-one@example.com', token='tok-one', active=1)
        self._seed_subscriber('active-two@example.com', token='tok-two', active=1)
        self._seed_subscriber('inactive@example.com', token='tok-three', active=0)

        client = app_module.app.test_client()
        csrf_token = self._login_admin_session(client)

        response = client.post(
            '/admin/audience/email-ops/sponsor-send-now',
            data={
                'csrf_token': csrf_token,
                'target_date': '2026-03-17',
                'recipient_email': '',
                'sponsor_subject': 'Montana Blotter Update: New Sponsor',
                'sponsor_name': 'Big Sky Legal Support',
                'sponsor_headline': 'A new sponsor is supporting statewide coverage.',
                'sponsor_body': 'Paragraph one.\n\nParagraph two.',
                'sponsor_cta_label': 'Visit Sponsor',
                'sponsor_cta_url': 'https://sponsor.example.com',
                'sponsor_notes': 'bulk send',
            },
            follow_redirects=True,
        )
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(send_email_mock.call_count, 2)
        recipients = {call.args[0] for call in send_email_mock.call_args_list}
        self.assertEqual(recipients, {'active-one@example.com', 'active-two@example.com'})
        self.assertIn('Sponsor email send complete: 2 sent, 0 failed.', html)

        conn = app_module.get_db()
        run = conn.execute(
            """
            SELECT kind, audience, subject, sent_count, failed_count, notes
            FROM digest_runs
            WHERE kind = 'sponsor_announcement'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        recipient_rows = conn.execute(
            """
            SELECT recipient_email, status
            FROM digest_run_recipients
            WHERE run_id = (
                SELECT id FROM digest_runs
                WHERE kind = 'sponsor_announcement'
                ORDER BY id DESC
                LIMIT 1
            )
            ORDER BY recipient_email ASC
            """
        ).fetchall()
        conn.close()

        self.assertEqual(run['audience'], 'subscribers')
        self.assertEqual(run['subject'], 'Montana Blotter Update: New Sponsor')
        self.assertEqual(run['sent_count'], 2)
        self.assertEqual(run['failed_count'], 0)
        self.assertIn('Big Sky Legal Support', run['notes'])
        self.assertEqual(
            [(row['recipient_email'], row['status']) for row in recipient_rows],
            [
                ('active-one@example.com', 'sent'),
                ('active-two@example.com', 'sent'),
            ],
        )


if __name__ == '__main__':
    unittest.main()
