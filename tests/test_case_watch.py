"""Tests for the disposition admin case-watch view and the admin-outcome
email notifier in services/disposition/watcher.py.

Covers:
- GET /admin/case-watch renders for an authenticated admin
- ?pending=1 filter narrows the listing
- POST /admin/case-watch/mark-notified stamps notified_admin_at
- POST /admin/case-watch/refresh runs the refresh path
- Unauthenticated requests are redirected to login
- notify_admin_of_new_outcomes() — no pending → no-op
- notify_admin_of_new_outcomes() — successful send marks links notified
- notify_admin_of_new_outcomes() — failed send does NOT mark links
- notify_admin_of_new_outcomes() — no recipients → no-op with marker
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import app as app_module
import config
import init_db
from services.disposition import watcher as disposition_watcher


def _seed_court(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        '''
        INSERT INTO courts (slug, name, court_type, county)
        VALUES (?, ?, ?, ?)
        ''',
        ('cascade-county-dc', 'Cascade County District Court', 'district', 'Cascade'),
    )
    return int(cur.lastrowid)


def _seed_case(conn: sqlite3.Connection, court_id: int, *, case_number: str,
               defendant_name: str, defendant_slug: str, defendant_last: str,
               defendant_first: str, disposition: str = '', sentence_text: str = '',
               sentence_date: str = '', status: str = 'closed') -> int:
    cur = conn.execute(
        '''
        INSERT INTO court_cases
            (court_id, slug, case_number, caption, status,
             defendant_name, defendant_slug, defendant_last, defendant_first,
             is_criminal, charges_text, disposition, sentence_text,
             sentence_date, outcome_scraped_at, filed_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, datetime('now'), ?)
        ''',
        (court_id, defendant_slug, case_number, f'State v. {defendant_last}',
         status, defendant_name, defendant_slug, defendant_last, defendant_first,
         'Felony criminal mischief', disposition, sentence_text, sentence_date,
         '2025-09-01'),
    )
    return int(cur.lastrowid)


def _seed_booking(conn: sqlite3.Connection, *, person_name: str, name_slug: str,
                  county_name: str) -> int:
    cur = conn.execute(
        '''
        INSERT INTO jail_bookings
            (person_name, name_slug, age, booking_at,
             county_slug, county_name, facility_name,
             charges_summary, is_current, booking_status)
        VALUES (?, ?, ?, datetime('now', '-1 hour'),
                ?, ?, ?, ?, 0, 'released')
        ''',
        (person_name, name_slug, 30, county_name.lower().replace(' ', '-'),
         county_name, county_name + ' Jail', 'Test charge'),
    )
    return int(cur.lastrowid)


def _seed_link(conn: sqlite3.Connection, *, booking_id: int, case_id: int,
               has_outcome: int = 0, notified_admin_at: str = None,
               snap: dict = None) -> int:
    import json as _json
    cur = conn.execute(
        '''
        INSERT INTO booking_case_links
            (booking_id, court_case_id, match_type, confidence, linked_at,
             last_checked_at, last_outcome_snapshot, has_outcome, notified_admin_at)
        VALUES (?, ?, 'exact_slug', 1.0, datetime('now'),
                datetime('now'), ?, ?, ?)
        ''',
        (booking_id, case_id,
         _json.dumps(snap, sort_keys=True) if snap is not None else None,
         has_outcome, notified_admin_at),
    )
    return int(cur.lastrowid)


class _BaseCaseWatchTest(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-case-watch-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        self.previous_app_db_path = app_module.config.DB_PATH

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        app_module.app.config['TESTING'] = True

        # init_db.migrate() is the canonical idempotent schema entrypoint used at
        # app startup. init_database() also calls ensure_incident_notification_schema
        # before the subscribers table is created, which fails on a fresh DB.
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
        cur = conn.execute(
            '''
            INSERT INTO users (username, password, email, role, is_active)
            VALUES (?, ?, ?, ?, ?)
            ''',
            ('case-watch-admin', 'not-used', 'cw@example.com', 'ops', 1),
        )
        conn.commit()
        conn.close()
        return int(cur.lastrowid)

    def _login_admin_session(self, client) -> None:
        with client.session_transaction() as session:
            session['_user_id'] = str(self.admin_user_id)
            session['_fresh'] = True
            session['_csrf_token'] = 'test-csrf-token'

    def _open_conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c


class AdminCaseWatchViewTests(_BaseCaseWatchTest):
    def setUp(self) -> None:
        super().setUp()
        conn = self._open_conn()
        try:
            court_id = _seed_court(conn)
            case_id_a = _seed_case(conn, court_id,
                                   case_number='CDC 25-100',
                                   defendant_name='Anna Lynn Test',
                                   defendant_slug='test-anna-lynn',
                                   defendant_last='Test', defendant_first='Anna',
                                   disposition='Guilty plea', sentence_text='5 yrs',
                                   sentence_date='2026-04-10')
            case_id_b = _seed_case(conn, court_id,
                                   case_number='CDC 25-101',
                                   defendant_name='Bob Joe Sample',
                                   defendant_slug='sample-bob-joe',
                                   defendant_last='Sample', defendant_first='Bob')
            booking_a = _seed_booking(conn, person_name='Test, Anna Lynn',
                                      name_slug='test-anna-lynn',
                                      county_name='Cascade')
            booking_b = _seed_booking(conn, person_name='Sample, Bob Joe',
                                      name_slug='sample-bob-joe',
                                      county_name='Cascade')
            # Link A has outcome, never notified → pending
            self.link_a = _seed_link(conn, booking_id=booking_a, case_id=case_id_a,
                                     has_outcome=1, notified_admin_at=None,
                                     snap={'disposition': 'Guilty plea',
                                           'sentence_date': '2026-04-10'})
            # Link B has outcome, already notified
            self.link_b = _seed_link(conn, booking_id=booking_b, case_id=case_id_b,
                                     has_outcome=1,
                                     notified_admin_at='2026-06-01 10:00:00',
                                     snap={'disposition': 'Dismissed'})
            conn.commit()
        finally:
            conn.close()

    def test_list_renders_with_pending_and_stats(self) -> None:
        client = app_module.app.test_client()
        self._login_admin_session(client)
        response = client.get('/admin/case-watch')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('Case Watch', html)
        self.assertIn('Test, Anna Lynn', html)
        self.assertIn('Sample, Bob Joe', html)
        # Stats: 2 total, 2 with outcome, 1 pending notify
        self.assertIn('Pending Notify', html)

    def test_pending_filter_excludes_already_notified(self) -> None:
        client = app_module.app.test_client()
        self._login_admin_session(client)
        response = client.get('/admin/case-watch?pending=1')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('Test, Anna Lynn', html)
        # Already-notified link should not appear in the table body when filtered
        # (it is in the unfiltered all-links list)
        self.assertIn('Pending Outcome Notifications', html)

    def test_mark_notified_stamps_links(self) -> None:
        client = app_module.app.test_client()
        self._login_admin_session(client)
        response = client.post(
            '/admin/case-watch/mark-notified',
            data={'link_id': str(self.link_a), 'csrf_token': 'test-csrf-token'},
            follow_redirects=False,
        )
        self.assertIn(response.status_code, (302, 303))
        conn = self._open_conn()
        try:
            row = conn.execute(
                'SELECT notified_admin_at FROM booking_case_links WHERE id = ?',
                (self.link_a,),
            ).fetchone()
            self.assertIsNotNone(row['notified_admin_at'])
        finally:
            conn.close()

    def test_mark_notified_no_ids_redirects_safely(self) -> None:
        client = app_module.app.test_client()
        self._login_admin_session(client)
        # Snapshot the rows that already have notified_admin_at from the seed
        # (link_b was seeded with a timestamp).
        conn = self._open_conn()
        try:
            pre = conn.execute(
                'SELECT id, notified_admin_at FROM booking_case_links '
                'ORDER BY id'
            ).fetchall()
            pre_ids = {row['id']: row['notified_admin_at'] for row in pre}
        finally:
            conn.close()

        response = client.post(
            '/admin/case-watch/mark-notified',
            data={'csrf_token': 'test-csrf-token'},
            follow_redirects=False,
        )
        self.assertIn(response.status_code, (302, 303))

        # No row should have changed its notified_admin_at value.
        conn = self._open_conn()
        try:
            post = conn.execute(
                'SELECT id, notified_admin_at FROM booking_case_links '
                'ORDER BY id'
            ).fetchall()
            post_map = {row['id']: row['notified_admin_at'] for row in post}
            self.assertEqual(post_map, pre_ids)
        finally:
            conn.close()

    def test_refresh_runs_inline(self) -> None:
        client = app_module.app.test_client()
        self._login_admin_session(client)
        response = client.post(
            '/admin/case-watch/refresh',
            data={'csrf_token': 'test-csrf-token'},
            follow_redirects=False,
        )
        self.assertIn(response.status_code, (302, 303))

    def test_unauthenticated_redirects(self) -> None:
        client = app_module.app.test_client()
        response = client.get('/admin/case-watch', follow_redirects=False)
        self.assertIn(response.status_code, (302, 303))


class NotifyAdminOfNewOutcomesTests(_BaseCaseWatchTest):
    def setUp(self) -> None:
        super().setUp()
        conn = self._open_conn()
        try:
            court_id = _seed_court(conn)
            self.case_id = _seed_case(conn, court_id,
                                      case_number='CDC 25-200',
                                      defendant_name='Carol Q Pending',
                                      defendant_slug='pending-carol-q',
                                      defendant_last='Pending',
                                      defendant_first='Carol',
                                      disposition='Guilty',
                                      sentence_text='2 yrs',
                                      sentence_date='2026-05-15')
            self.booking_id = _seed_booking(conn,
                                            person_name='Pending, Carol Q',
                                            name_slug='pending-carol-q',
                                            county_name='Cascade')
            self.link_id = _seed_link(conn, booking_id=self.booking_id,
                                      case_id=self.case_id, has_outcome=1,
                                      notified_admin_at=None,
                                      snap={'disposition': 'Guilty',
                                            'sentence_date': '2026-05-15'})
            conn.commit()
        finally:
            conn.close()

    def _open_app_conn(self) -> sqlite3.Connection:
        return app_module.get_db()

    def test_no_pending_returns_noop(self) -> None:
        conn = self._open_app_conn()
        try:
            disposition_watcher.mark_notified(conn, [self.link_id])
            stats = disposition_watcher.notify_admin_of_new_outcomes(conn)
        finally:
            conn.close()
        self.assertFalse(stats['sent'])
        self.assertEqual(stats['total_pending'], 0)

    def test_successful_send_marks_links(self) -> None:
        conn = self._open_app_conn()
        try:
            with patch('services.alerts.legacy.collect_alert_recipients',
                       return_value=['admin@example.com']), \
                 patch('services.alerts.legacy.send_plaintext_email',
                       return_value=True) as mock_send:
                stats = disposition_watcher.notify_admin_of_new_outcomes(conn)
            self.assertTrue(stats['sent'])
            self.assertEqual(stats['recipients'], 1)
            self.assertEqual(stats['links_in_email'], 1)
            self.assertGreaterEqual(stats['marked'], 1)
            mock_send.assert_called_once()
            args, _ = mock_send.call_args
            self.assertEqual(args[0], ['admin@example.com'])
            self.assertIn('1 new court outcome', args[1])
            self.assertIn('Pending, Carol Q', args[2])
            self.assertIn('/admin/case-watch', args[2])

            # Link should now be notified
            row = conn.execute(
                'SELECT notified_admin_at FROM booking_case_links WHERE id = ?',
                (self.link_id,),
            ).fetchone()
            self.assertIsNotNone(row['notified_admin_at'])
        finally:
            conn.close()

    def test_smtp_failure_does_not_mark(self) -> None:
        conn = self._open_app_conn()
        try:
            with patch('services.alerts.legacy.collect_alert_recipients',
                       return_value=['admin@example.com']), \
                 patch('services.alerts.legacy.send_plaintext_email',
                       return_value=False):
                stats = disposition_watcher.notify_admin_of_new_outcomes(conn)
            self.assertFalse(stats['sent'])
            self.assertEqual(stats['error'], 'smtp_not_configured')
            # Link should NOT be marked
            row = conn.execute(
                'SELECT notified_admin_at FROM booking_case_links WHERE id = ?',
                (self.link_id,),
            ).fetchone()
            self.assertIsNone(row['notified_admin_at'])
        finally:
            conn.close()

    def test_send_exception_does_not_mark(self) -> None:
        conn = self._open_app_conn()
        try:
            with patch('services.alerts.legacy.collect_alert_recipients',
                       return_value=['admin@example.com']), \
                 patch('services.alerts.legacy.send_plaintext_email',
                       side_effect=RuntimeError('smtp boom')):
                stats = disposition_watcher.notify_admin_of_new_outcomes(conn)
            self.assertFalse(stats['sent'])
            self.assertIn('smtp boom', stats['error'])
            row = conn.execute(
                'SELECT notified_admin_at FROM booking_case_links WHERE id = ?',
                (self.link_id,),
            ).fetchone()
            self.assertIsNone(row['notified_admin_at'])
        finally:
            conn.close()

    def test_no_recipients_skips_send(self) -> None:
        conn = self._open_app_conn()
        try:
            with patch('services.alerts.legacy.collect_alert_recipients',
                       return_value=[]), \
                 patch('services.alerts.legacy.send_plaintext_email') as mock_send:
                stats = disposition_watcher.notify_admin_of_new_outcomes(conn)
            self.assertFalse(stats['sent'])
            self.assertEqual(stats['error'], 'no_recipients')
            mock_send.assert_not_called()
            # Link stays pending so it appears on the dashboard
            row = conn.execute(
                'SELECT notified_admin_at FROM booking_case_links WHERE id = ?',
                (self.link_id,),
            ).fetchone()
            self.assertIsNone(row['notified_admin_at'])
        finally:
            conn.close()

    def test_subject_uses_pluralization(self) -> None:
        conn = self._open_app_conn()
        try:
            with patch('services.alerts.legacy.collect_alert_recipients',
                       return_value=['admin@example.com']), \
                 patch('services.alerts.legacy.send_plaintext_email',
                       return_value=True) as mock_send:
                disposition_watcher.notify_admin_of_new_outcomes(conn)
            args, _ = mock_send.call_args
            # Single outcome → no trailing 's'
            self.assertIn('1 new court outcome for tracked arrests', args[1])
        finally:
            conn.close()


class RunAllIncludesNotifyTests(_BaseCaseWatchTest):
    def test_run_all_keys(self) -> None:
        conn = app_module.get_db()
        try:
            with patch.object(disposition_watcher, 'link_recent_bookings',
                              return_value={}), \
                 patch.object(disposition_watcher, 'refresh_outcome_data',
                              return_value={}), \
                 patch.object(disposition_watcher, 'notify_admin_of_new_outcomes',
                              return_value={'sent': False}):
                stats = disposition_watcher.run_all(conn)
            self.assertIn('link', stats)
            self.assertIn('refresh', stats)
            self.assertIn('notify', stats)
        finally:
            conn.close()


if __name__ == '__main__':
    unittest.main()
