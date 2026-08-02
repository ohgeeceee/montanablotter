"""Per-firm lawyer outreach workflow tests.

Exercises:
  - schema present after init_db.ensure_lawyer_outreach_schema
  - target_list.csv importer upserts into lawyer_outreach_prospects
  - cadence worker queues Day 1 email for new prospects ONLY (never auto-sends)
  - cadence worker advances stage based on days-since-last-action
  - admin blueprint routes wired and admin email send is the ONLY SMTP path
  - dedupe UNIQUE prevents double-queueing the same Day 1 email
"""
from __future__ import annotations

import csv
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from app import app as _app_module
import app as app_module
import bcrypt
import config
import init_db
from db import get_db


# ------------------------------------------------------------------ helpers ---

def _new_db() -> sqlite3.Connection:
    fd, path = tempfile.mkstemp(prefix='mb-lawyer-outreach-', suffix='.db')
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    init_db.ensure_lawyer_outreach_schema(conn)
    return conn


def _write_csv(tmpdir: str, rows: list[dict[str, str]]) -> str:
    path = os.path.join(tmpdir, 'target_list.csv')
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return path


def _firm_row(firm: str = 'Alpine Law', county: str = 'Yellowstone',
              email: str = 'jane@alpinelawmt.com') -> dict[str, str]:
    return {
        'firm_name': firm,
        'county': county,
        'city': 'Billings',
        'website': f'https://{firm.lower().replace(" ", "")}.com/',
        'contact_name': 'Jane Doe',
        'contact_email': email,
        'email_status': 'needs_research',
        'practice_areas': 'Criminal defense, DUI',
        'notes': '',
    }


# ---------------------------------------------------------------- schema tests

class LawyerOutreachSchemaTests(unittest.TestCase):
    def setUp(self):
        self.conn = _new_db()

    def tearDown(self):
        self.conn.close()

    def test_prospects_table_exists(self):
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='lawyer_outreach_prospects'"
        ).fetchone()
        self.assertIsNotNone(row)

    def test_emails_table_exists(self):
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='lawyer_outreach_emails'"
        ).fetchone()
        self.assertIsNotNone(row)

    def test_unique_constraint_on_firm_county(self):
        self.conn.execute(
            '''INSERT INTO lawyer_outreach_prospects
               (firm_name, county, stage, status)
               VALUES (?, ?, 'day_1', 'queued')''',
            ('Alpine Law', 'Yellowstone'),
        )
        self.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                '''INSERT INTO lawyer_outreach_prospects
                   (firm_name, county, stage, status)
                   VALUES (?, ?, 'day_1', 'queued')''',
                ('Alpine Law', 'Yellowstone'),
            )


# ------------------------------------------------------- CSV importer tests --

class LawyerOutreachImporterTests(unittest.TestCase):
    def setUp(self):
        self.conn = _new_db()
        self.tmpdir = tempfile.mkdtemp(prefix='mb-lawyer-outreach-csv-')
        self.csv_path = _write_csv(self.tmpdir, [_firm_row()])

    def tearDown(self):
        self.conn.close()
        os.unlink(self.csv_path)
        os.rmdir(self.tmpdir)

    def test_importer_inserts_prospect_row(self):
        from services.lawyer_outreach.importer import import_prospects_from_csv

        counts = import_prospects_from_csv(self.conn, self.csv_path)
        self.assertEqual(counts['inserted'], 1)
        self.assertEqual(counts['updated'], 0)
        prospect = self.conn.execute(
            'SELECT firm_name, county, contact_email, stage, status '
            'FROM lawyer_outreach_prospects LIMIT 1'
        ).fetchone()
        self.assertEqual(prospect['firm_name'], 'Alpine Law')
        self.assertEqual(prospect['county'], 'Yellowstone')
        self.assertEqual(prospect['contact_email'], 'jane@alpinelawmt.com')
        self.assertEqual(prospect['stage'], 'day_1')
        self.assertEqual(prospect['status'], 'queued')

    def test_importer_updates_existing_prospect_without_resetting_stage(self):
        from services.lawyer_outreach.importer import import_prospects_from_csv

        import_prospects_from_csv(self.conn, self.csv_path)
        # Operator already advanced this prospect past day_1.
        self.conn.execute(
            '''UPDATE lawyer_outreach_prospects
               SET stage = 'day_5', status = 'in_progress' WHERE firm_name = ?''',
            ('Alpine Law',),
        )
        self.conn.commit()
        counts = import_prospects_from_csv(self.conn, self.csv_path)
        self.assertEqual(counts['inserted'], 0)
        self.assertEqual(counts['updated'], 1)
        stage = self.conn.execute(
            "SELECT stage FROM lawyer_outreach_prospects WHERE firm_name = ?",
            ('Alpine Law',),
        ).fetchone()['stage']
        self.assertEqual(stage, 'day_5')  # import must NOT reset stage


# ----------------------------------------------------- cadence worker tests --

class LawyerOutreachCadenceTests(unittest.TestCase):
    def setUp(self):
        self.conn = _new_db()
        self.tmpdir = tempfile.mkdtemp(prefix='mb-lawyer-outreach-cadence-')
        self.csv_path = _write_csv(self.tmpdir, [_firm_row()])
        from services.lawyer_outreach.importer import import_prospects_from_csv
        import_prospects_from_csv(self.conn, self.csv_path)
        # Stamp last_action_at as just-now so the worker treats this as fresh.
        self.conn.execute(
            "UPDATE lawyer_outreach_prospects SET last_action_at = datetime('now')"
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        os.unlink(self.csv_path)
        os.rmdir(self.tmpdir)

    def test_worker_queues_day_1_email_for_new_prospect(self):
        from services.lawyer_outreach.cadence import run_cadence

        run_cadence(self.conn, dry_run=False)

        email = self.conn.execute(
            "SELECT subject, body, status, stage FROM lawyer_outreach_emails LIMIT 1"
        ).fetchone()
        self.assertIsNotNone(email)
        self.assertEqual(email['status'], 'pending')
        self.assertEqual(email['stage'], 'day_1')
        self.assertIn('Yellowstone', email['subject'])
        self.assertIn('Yellowstone', email['body'])
        self.assertIn('Jane', email['body'])  # contact_name first name

    def test_worker_dedupes_same_day_1_email(self):
        from services.lawyer_outreach.cadence import run_cadence

        run_cadence(self.conn, dry_run=False)
        run_cadence(self.conn, dry_run=False)
        n = self.conn.execute(
            "SELECT COUNT(*) FROM lawyer_outreach_emails WHERE stage = 'day_1'"
        ).fetchone()[0]
        self.assertEqual(n, 1)

    def test_worker_advances_to_day_3_after_two_days(self):
        from services.lawyer_outreach.cadence import run_cadence

        run_cadence(self.conn, dry_run=False)
        # Send Day 1 manually to advance the clock.
        self.conn.execute(
            "UPDATE lawyer_outreach_emails SET status='sent', sent_at=datetime('now') "
            "WHERE stage='day_1'"
        )
        self.conn.execute(
            "UPDATE lawyer_outreach_prospects "
            "SET last_action_at = datetime('now', '-3 days')"
        )
        self.conn.commit()
        run_cadence(self.conn, dry_run=False)

        stage = self.conn.execute(
            "SELECT stage FROM lawyer_outreach_prospects LIMIT 1"
        ).fetchone()['stage']
        self.assertEqual(stage, 'day_3')

    def test_worker_does_not_queue_for_won_prospects(self):
        from services.lawyer_outreach.cadence import run_cadence

        self.conn.execute(
            "UPDATE lawyer_outreach_prospects SET status='won' WHERE firm_name='Alpine Law'"
        )
        self.conn.commit()
        run_cadence(self.conn, dry_run=False)
        n = self.conn.execute(
            "SELECT COUNT(*) FROM lawyer_outreach_emails"
        ).fetchone()[0]
        self.assertEqual(n, 0)

    def test_worker_dry_run_does_not_insert(self):
        from services.lawyer_outreach.cadence import run_cadence

        counts = run_cadence(self.conn, dry_run=True)
        self.assertEqual(counts['queued'], 0)
        n = self.conn.execute(
            "SELECT COUNT(*) FROM lawyer_outreach_emails"
        ).fetchone()[0]
        self.assertEqual(n, 0)

    def test_worker_skips_prospect_without_email(self):
        from services.lawyer_outreach.cadence import run_cadence

        self.conn.execute(
            "UPDATE lawyer_outreach_prospects SET contact_email = NULL WHERE firm_name='Alpine Law'"
        )
        self.conn.commit()
        run_cadence(self.conn, dry_run=False)
        n = self.conn.execute(
            "SELECT COUNT(*) FROM lawyer_outreach_emails"
        ).fetchone()[0]
        self.assertEqual(n, 0)


# --------------------------------------------------- admin blueprint tests ---

class LawyerOutreachBlueprintTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(prefix='mb-lawyer-bp-', suffix='.db')
        os.close(fd)
        self.prev_config = config.DB_PATH
        self.prev_init = init_db.DB_PATH
        self.prev_app = app_module.config.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        # Seed the schema + the prospect + a pending email.
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        init_db.ensure_lawyer_outreach_schema(self.conn)
        self.conn.execute(
            '''INSERT INTO lawyer_outreach_prospects
               (firm_name, county, contact_email, stage, status, last_action_at)
               VALUES (?, ?, ?, 'day_1', 'queued', datetime('now'))''',
            ('Alpine Law', 'Yellowstone', 'jane@alpinelawmt.com'),
        )
        self.conn.execute(
            '''INSERT INTO lawyer_outreach_emails
               (prospect_id, stage, to_addr, subject, body)
               VALUES (1, 'day_1', 'jane@alpinelawmt.com', 'Hi',
                       'Body for Alpine Law — Yellowstone County test')'''
        )
        self.conn.commit()
        # Seed a real admin user so the /admin/login form accepts our POST.
        # The full users table is created by app startup, so spin up a test
        # client once to ensure the table exists, then insert the user.
        self.client = app_module.app.test_client()
        self._create_admin_user('testadmin', 'password123')

    def _create_admin_user(self, username: str, password: str) -> None:
        from flask_bcrypt import generate_password_hash
        hashed = generate_password_hash(password).decode('utf-8')
        # Bring the full schema up via init_db.migrate() so the users table
        # has the exact shape the /admin/login handler expects (mfa_enabled,
        # last_login_at, display_name, etc.). Without this, the test's
        # minimal CREATE TABLE omits columns and the row never matches the
        # SELECT * FROM users lookup.
        init_db.migrate()
        conn = sqlite3.connect(self.db_path)
        # Disable admin-login rate limit for tests so repeated POSTs to
        # /admin/login don't trip the lockout.
        conn.execute('''
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        ''')
        conn.execute(
            'INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)',
            ('admin_login_max_attempts', '999'),
        )
        conn.execute(
            'INSERT OR REPLACE INTO users (username, password, role, is_active) '
            'VALUES (?, ?, ?, 1)',
            (username, hashed, 'super_admin'),
        )
        conn.commit()
        conn.close()

    def _login(self) -> None:
        r = self.client.post('/admin/login', data={
            'username': 'testadmin',
            'password': 'password123',
        }, follow_redirects=False)
        self.assertEqual(r.status_code, 302, f'login failed: {r.status_code} {r.data[:200]!r}')

    def tearDown(self):
        config.DB_PATH = self.prev_config
        init_db.DB_PATH = self.prev_init
        app_module.config.DB_PATH = self.prev_app
        self.conn.close()
        os.unlink(self.db_path)

    def _db(self):
        """Return the Flask app's live DB connection so we see in-flight writes."""
        return get_db()

    def _login(self) -> None:
        # GET /admin/login first so the session gets a _csrf_token, then POST
        # with it. The enforce_admin_csrf before_request hook rejects POSTs
        # without the matching csrf_token in both session AND form.
        r_get = self.client.get('/admin/login')
        self.assertEqual(r_get.status_code, 200)
        # Pull the session cookie + token the GET set.
        with self.client.session_transaction() as sess:
            csrf_token = sess.get('_csrf_token')
        self.assertIsNotNone(csrf_token, 'no csrf_token in session after GET /admin/login')

        r = self.client.post('/admin/login', data={
            'username': 'testadmin',
            'password': 'password123',
            'csrf_token': csrf_token,
        }, follow_redirects=False)
        self.assertEqual(r.status_code, 302,
                         f'login failed: status={r.status_code} '
                         f'loc={r.headers.get("Location", "")} '
                         f'body={r.data[:200]!r}')

    def test_admin_list_route_redirects_when_anonymous(self):
        r = self.client.get('/admin/lawyer-outreach')
        self.assertIn(r.status_code, (302, 303))

    def test_admin_list_route_loads_when_logged_in(self):
        self._login()
        r = self.client.get('/admin/lawyer-outreach')
        self.assertEqual(r.status_code, 200)
        body = r.data.decode()
        self.assertIn('Alpine Law', body)
        self.assertIn('Yellowstone', body)

    def _csrf_token(self) -> str:
        """Fetch the current session's _csrf_token (seeded by GET /admin/login)."""
        with self.client.session_transaction() as sess:
            token = sess.get('_csrf_token')
        if token is None:
            # First call in a test — hit the login page to seed it.
            self.client.get('/admin/login')
            with self.client.session_transaction() as sess:
                token = sess.get('_csrf_token')
        self.assertIsNotNone(token, 'no _csrf_token after GET /admin/login')
        return token

    def test_admin_send_action_calls_smtp_and_marks_sent(self):
        from blueprints.admin import lawyer_outreach as bp
        self._login()
        with patch.object(bp, '_send_email', return_value=(True, '')) as mock_send:
            r = self.client.post(
                '/admin/lawyer-outreach/email/1/send',
                data={'csrf_token': self._csrf_token()},
                follow_redirects=False,
            )
        self.assertIn(r.status_code, (302, 303))
        mock_send.assert_called_once()
        conn = self._db()
        row = conn.execute(
            "SELECT status, sent_at FROM lawyer_outreach_emails WHERE id = 1"
        ).fetchone()
        self.assertEqual(row['status'], 'sent')
        self.assertIsNotNone(row['sent_at'])

    def test_admin_skip_action_marks_skipped_without_smtp(self):
        from blueprints.admin import lawyer_outreach as bp
        self._login()
        with patch.object(bp, '_send_email') as mock_send:
            r = self.client.post(
                '/admin/lawyer-outreach/email/1/skip',
                data={'csrf_token': self._csrf_token()},
                follow_redirects=False,
            )
        self.assertIn(r.status_code, (302, 303))
        mock_send.assert_not_called()
        conn = self._db()
        row = conn.execute(
            "SELECT status, skipped_at FROM lawyer_outreach_emails WHERE id = 1"
        ).fetchone()
        self.assertEqual(row['status'], 'skipped')

    def test_admin_advance_stage_moves_prospect(self):
        self._login()
        r = self.client.post(
            '/admin/lawyer-outreach/prospect/1/advance',
            data={'stage': 'day_3', 'csrf_token': self._csrf_token()},
            follow_redirects=False,
        )
        self.assertIn(r.status_code, (302, 303))
        conn = self._db()
        row = conn.execute(
            "SELECT stage, status FROM lawyer_outreach_prospects WHERE id = 1"
        ).fetchone()
        self.assertEqual(row['stage'], 'day_3')

    def test_admin_mark_won_terminates_cadence(self):
        self._login()
        r = self.client.post(
            '/admin/lawyer-outreach/prospect/1/won',
            data={'csrf_token': self._csrf_token()},
            follow_redirects=False,
        )
        self.assertIn(r.status_code, (302, 303))
        conn = self._db()
        row = conn.execute(
            "SELECT status FROM lawyer_outreach_prospects WHERE id = 1"
        ).fetchone()
        self.assertEqual(row['status'], 'won')

    def test_admin_sample_report_renders_for_prospect(self):
        # Sample mode — no lawyer_ad_orders row matches, so report uses
        # reference numbers for the requested tier.
        self._login()
        r = self.client.get(
            '/admin/lawyer-outreach/prospect/1/sample-report?package=gold',
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 200)
        body = r.data.decode()
        # Sample-mode markers and the prospect's firm + county.
        self.assertIn('Alpine Law', body)
        self.assertIn('Yellowstone', body)
        self.assertIn('SAMPLE', body)
        self.assertIn('Gold Priority', body)
        # Reference numbers for gold: 412 impressions, 6 leads.
        self.assertIn('>412</p>', body)
        # Disclaimer copy — the regulator / firm / customer must see this.
        self.assertIn('illustrative', body.lower())
        # Page is noindex.
        self.assertIn('noindex', body)

    def test_admin_sample_report_package_param_chooses_tier(self):
        # ?package=silver should switch the reference numbers to silver tier.
        self._login()
        r = self.client.get(
            '/admin/lawyer-outreach/prospect/1/sample-report?package=silver',
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 200)
        body = r.data.decode()
        self.assertIn('Silver Featured', body)
        # Silver reference: 184 impressions, 3 leads.
        self.assertIn('>184</p>', body)

    def test_admin_sample_report_invalid_package_falls_back_to_gold(self):
        self._login()
        r = self.client.get(
            '/admin/lawyer-outreach/prospect/1/sample-report?package=platinum',
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 200)
        body = r.data.decode()
        self.assertIn('Gold Priority', body)

    def test_admin_sample_report_unknown_prospect_404s(self):
        self._login()
        r = self.client.get(
            '/admin/lawyer-outreach/prospect/9999/sample-report',
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 404)


if __name__ == '__main__':
    unittest.main()