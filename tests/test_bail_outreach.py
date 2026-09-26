"""Tests for the bail agency outreach cadence + admin panel.

Covers:
  - run_cadence queues Day-1 emails for 'new' agencies with an email,
    phone-task drafts for those without
  - idempotency via campaign_dedupe_key (re-run creates no dupes)
  - contacted agencies age into day_5 / day_10 follow-ups
  - 14-day stall
  - mark_draft_sent flips the agency to 'contacted'
  - exclusive-slot probe flips the P.S. when a county is sold
  - /admin/revenue/bail-outreach renders for logged-in admin
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta

from app import app as _app_module
import app as app_module
import config
import init_db
from db import get_db
from services.bail_outreach import cadence


def _iso(dt: datetime) -> str:
    return dt.strftime('%Y-%m-%d %H:%M:%S')


class BailCadenceTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(prefix='mb-bail-cad-', suffix='.db')
        os.close(fd)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        # Full schema via init + migrate so bail_agency_outreach +
        # outreach_drafts + bail_ad_orders exist exactly as in production.
        prev_init = init_db.DB_PATH
        init_db.DB_PATH = self.db_path
        try:
            init_db.init_database()
            init_db.migrate()
        finally:
            init_db.DB_PATH = prev_init

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db_path)

    def _agency(self, name, email=None, phone=None, counties='Yellowstone',
                status='new', contacted_days_ago=None):
        last = (None if contacted_days_ago is None
                else _iso(datetime.utcnow() - timedelta(days=contacted_days_ago)))
        self.conn.execute(
            """INSERT INTO bail_agency_outreach
               (agency_name, counties, email, phone, outreach_status,
                dedupe_key, last_contacted_at, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,datetime('now'),datetime('now'))""",
            (name, counties, email, phone, status, name.lower().replace(' ', '-'), last))
        self.conn.commit()
        return self.conn.execute(
            "SELECT id FROM bail_agency_outreach WHERE agency_name=?",
            (name,)).fetchone()['id']

    def _drafts(self, dedupe_prefix=None):
        q = ("SELECT * FROM outreach_drafts WHERE worker_name=?"
             + (" AND campaign_dedupe_key LIKE ?" if dedupe_prefix else ""))
        args = (cadence.WORKER_NAME,)
        if dedupe_prefix:
            args += (f'{dedupe_prefix}%',)
        return self.conn.execute(q, args).fetchall()

    # ------------------------------------------------------------ day 1 ---

    def test_new_agency_with_email_queues_day1(self):
        self._agency('Lisas Family Bail Bonds', email='agent@example.com')
        counts = cadence.run_cadence(self.conn)
        self.assertEqual(counts['queued_email'], 1)
        d = self._drafts()[0]
        self.assertEqual(d['status'], 'pending')
        self.assertIn('Yellowstone', d['subject'])
        self.assertIn('montanablotter@gmail.com', d['body'])
        # agency flipped to draft_queued
        row = self.conn.execute(
            "SELECT outreach_status FROM bail_agency_outreach").fetchone()
        self.assertEqual(row['outreach_status'], 'draft_queued')

    def test_new_agency_without_email_queues_phone_task(self):
        self._agency('No Email Bonding', phone='406-555-0100')
        counts = cadence.run_cadence(self.conn)
        self.assertEqual(counts['queued_phone'], 1)
        d = self._drafts()[0]
        self.assertEqual(d['email_address'], '')
        self.assertIn('PHONE', d['subject'])
        self.assertIn('406-555-0100', d['body'])

    def test_rerun_is_idempotent(self):
        self._agency('Lisas Family Bail Bonds', email='agent@example.com')
        cadence.run_cadence(self.conn)
        # status is now draft_queued which is NOT in the active set for
        # day_1; force it back to 'new' to prove dedupe_key catches dupes
        self.conn.execute(
            "UPDATE bail_agency_outreach SET outreach_status='new'")
        self.conn.commit()
        counts = cadence.run_cadence(self.conn)
        self.assertEqual(counts['skipped'], 1)
        self.assertEqual(len(self._drafts()), 1)

    # -------------------------------------------------------- follow-ups ---

    def test_contacted_5_days_queues_day5(self):
        self._agency('AAA Bail Bonds', email='aaa@example.com',
                     status='contacted', contacted_days_ago=6)
        counts = cadence.run_cadence(self.conn)
        self.assertEqual(counts['queued_followup'], 1)
        self.assertIn('Re:', self._drafts()[0]['subject'])

    def test_contacted_11_days_queues_day10(self):
        self._agency('AAA Bail Bonds', email='aaa@example.com',
                     status='contacted', contacted_days_ago=11)
        counts = cadence.run_cadence(self.conn)
        self.assertEqual(counts['queued_followup'], 1)
        self.assertIn('Last note', self._drafts()[0]['subject'])

    def test_contacted_15_days_stalls(self):
        self._agency('Dead Lead Bonding', email='dl@example.com',
                     status='contacted', contacted_days_ago=15)
        counts = cadence.run_cadence(self.conn)
        self.assertEqual(counts['stalled'], 1)
        self.assertEqual(len(self._drafts()), 0)
        row = self.conn.execute(
            "SELECT outreach_status FROM bail_agency_outreach").fetchone()
        self.assertEqual(row['outreach_status'], 'stalled')

    # ----------------------------------------------------------- slots ---

    def test_exclusive_slot_sold_flips_ps(self):
        self.conn.execute(
            """INSERT INTO bail_ad_orders
               (business_name, email, package_id, county_targets, status)
               VALUES ('Winner Bail', 'w@e.com',
                       'exclusive_county_sponsorship', 'Yellowstone',
                       'active')""")
        self.conn.commit()
        self._agency('Lisas Family Bail Bonds', email='agent@example.com')
        cadence.run_cadence(self.conn)
        body = self._drafts()[0]['body']
        self.assertIn('already held by another agency', body)

    def test_exclusive_slot_open_default_ps(self):
        self._agency('Lisas Family Bail Bonds', email='agent@example.com')
        cadence.run_cadence(self.conn)
        self.assertIn('1 of 1', self._drafts()[0]['body'])

    # ------------------------------------------------- mark_draft_sent ---

    def test_mark_draft_sent_flips_agency_contacted(self):
        aid = self._agency('Lisas Family Bail Bonds', email='a@e.com')
        cadence.run_cadence(self.conn)
        draft = self._drafts()[0]
        self.assertTrue(cadence.mark_draft_sent(self.conn, draft['id']))
        a = self.conn.execute(
            "SELECT outreach_status, last_contacted_at FROM bail_agency_outreach "
            "WHERE id=?", (aid,)).fetchone()
        self.assertEqual(a['outreach_status'], 'contacted')
        self.assertIsNotNone(a['last_contacted_at'])
        d = self.conn.execute("SELECT status, sent_at FROM outreach_drafts WHERE id=?",
                              (draft['id'],)).fetchone()
        self.assertEqual(d['status'], 'sent')

    def test_mark_draft_sent_ignores_foreign_draft(self):
        self._agency('X', email='x@e.com')
        cadence.run_cadence(self.conn)
        draft_id = self._drafts()[0]['id']
        self.conn.execute("UPDATE outreach_drafts SET worker_name='other' WHERE id=?",
                          (draft_id,))
        self.conn.commit()
        self.assertFalse(cadence.mark_draft_sent(self.conn, draft_id))


# --------------------------------------------------- admin blueprint tests ---

class BailOutreachBlueprintTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(prefix='mb-bail-bp-', suffix='.db')
        os.close(fd)
        self.prev_config = config.DB_PATH
        self.prev_init = init_db.DB_PATH
        self.prev_app = app_module.config.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        init_db.init_database()
        init_db.migrate()
        self.conn.execute(
            """INSERT INTO bail_agency_outreach
               (agency_name, counties, email, outreach_status, dedupe_key,
                created_at, updated_at)
               VALUES ('Lisas Family Bail Bonds', 'Yellowstone',
                       'agent@example.com', 'new', 'lisas-family-bail-bonds',
                       datetime('now'), datetime('now'))""")
        self.conn.commit()
        self.client = app_module.app.test_client()
        self._create_admin_user('testadmin', 'password123')

    def _create_admin_user(self, username, password):
        from flask_bcrypt import generate_password_hash
        hashed = generate_password_hash(password).decode('utf-8')
        conn = sqlite3.connect(self.db_path)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY, value TEXT,
                updated_at TEXT NOT NULL DEFAULT (datetime('now')))''')
        conn.execute('INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)',
                     ('admin_login_max_attempts', '999'))
        conn.execute(
            'INSERT OR REPLACE INTO users (username, password, role, is_active) '
            'VALUES (?, ?, ?, 1)', (username, hashed, 'super_admin'))
        conn.commit()
        conn.close()

    def _login(self):
        r_get = self.client.get('/admin/login')
        self.assertEqual(r_get.status_code, 200)
        with self.client.session_transaction() as sess:
            csrf_token = sess.get('_csrf_token')
        r = self.client.post('/admin/login', data={
            'username': 'testadmin', 'password': 'password123',
            'csrf_token': csrf_token}, follow_redirects=False)
        self.assertEqual(r.status_code, 302)

    def tearDown(self):
        config.DB_PATH = self.prev_config
        init_db.DB_PATH = self.prev_init
        app_module.config.DB_PATH = self.prev_app
        self.conn.close()
        os.unlink(self.db_path)

    def test_anonymous_redirects(self):
        r = self.client.get('/admin/revenue/bail-outreach')
        self.assertIn(r.status_code, (302, 303))

    def test_panel_renders_with_agency(self):
        self._login()
        r = self.client.get('/admin/revenue/bail-outreach')
        self.assertEqual(r.status_code, 200)
        body = r.data.decode()
        self.assertIn('Lisas Family Bail Bonds', body)
        self.assertIn('Bail Agency Outreach', body)

    def test_run_worker_queues_drafts(self):
        self._login()
        with self.client.session_transaction() as sess:
            tok = sess.get('_csrf_token')
        r = self.client.post('/admin/revenue/bail-outreach/run-worker',
                             data={'csrf_token': tok},
                             follow_redirects=False)
        self.assertIn(r.status_code, (302, 303))
        n = self.conn.execute(
            "SELECT COUNT(*) FROM outreach_drafts WHERE worker_name=?",
            (cadence.WORKER_NAME,)).fetchone()[0]
        self.assertEqual(n, 1)

    def test_send_disabled_without_smtp(self):
        # Force missing SMTP creds -> send route must fail soft, not 500.
        self._login()
        cadence.run_cadence(self.conn)
        draft_id = self.conn.execute(
            "SELECT id FROM outreach_drafts WHERE worker_name=?",
            (cadence.WORKER_NAME,)).fetchone()['id']
        with self.client.session_transaction() as sess:
            tok = sess.get('_csrf_token')
        from unittest.mock import patch as _patch
        with _patch('blueprints.admin.bail_outreach._smtp_settings',
                    return_value={'server': '', 'port': 0,
                                  'user': '', 'password': ''}):
            r = self.client.post(
                f'/admin/revenue/bail-outreach/draft/{draft_id}/send',
                data={'csrf_token': tok}, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn('smtp_not_configured', r.data.decode())

    def test_skip_draft(self):
        self._login()
        cadence.run_cadence(self.conn)
        draft_id = self.conn.execute(
            "SELECT id FROM outreach_drafts WHERE worker_name=?",
            (cadence.WORKER_NAME,)).fetchone()['id']
        with self.client.session_transaction() as sess:
            tok = sess.get('_csrf_token')
        r = self.client.post(
            f'/admin/revenue/bail-outreach/draft/{draft_id}/skip',
            data={'csrf_token': tok}, follow_redirects=False)
        self.assertIn(r.status_code, (302, 303))
        st = self.conn.execute("SELECT status FROM outreach_drafts WHERE id=?",
                               (draft_id,)).fetchone()['status']
        self.assertEqual(st, 'skipped')


if __name__ == '__main__':
    unittest.main()
