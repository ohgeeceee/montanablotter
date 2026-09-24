"""Tests for the paid name-removal / privacy-suppression feature."""

import importlib
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import app as app_module
import config
import init_db
from services.monetization import name_suppression as ns


def _make_tables(conn):
    conn.executescript(
        """
        CREATE TABLE name_suppression_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_user_id INTEGER, email TEXT NOT NULL, person_name TEXT NOT NULL,
            dob TEXT, county TEXT, status TEXT NOT NULL DEFAULT 'pending',
            stripe_session_id TEXT, stripe_payment_id TEXT,
            reviewed_by INTEGER, reviewed_at TEXT, applied_at TEXT,
            rejection_reason TEXT, created_at TEXT DEFAULT (datetime('now')),
            ip_address TEXT, notes TEXT
        );
        CREATE TABLE suppressed_names (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_name_normalized TEXT NOT NULL, county TEXT,
            request_id INTEGER, applied_by INTEGER,
            applied_at TEXT DEFAULT (datetime('now'))
        );
        """
    )
    conn.commit()


class NameSuppressionHelperTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        self.prev = config.DB_PATH
        config.DB_PATH = self.db_path
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        _make_tables(self.conn)

    def tearDown(self):
        self.conn.close()
        config.DB_PATH = self.prev
        os.unlink(self.db_path)

    def test_normalize_matches_comma_and_space_forms(self):
        self.assertEqual(ns._normalize_name('Doe, John'), ns._normalize_name('John Doe'))

    def test_redact_person_name_masks_when_suppressed(self):
        ns.apply_suppression(1, 'John Doe', 'Cascade', applied_by=None)
        self.assertEqual(ns.redact_person_name('John Doe', 'Cascade'), ns.WITHHELD_LABEL)
        # different county should still match (county is optional / non-exclusive)
        self.assertEqual(ns.redact_person_name('John Doe'), ns.WITHHELD_LABEL)

    def test_redact_person_name_passthrough_when_not_suppressed(self):
        self.assertEqual(ns.redact_person_name('Jane Roe', 'Cascade'), 'Jane Roe')
        self.assertEqual(ns.redact_person_name('', 'Cascade'), '')
        self.assertIsNone(ns.redact_person_name(None))

    def test_redact_text_masks_name_in_prose(self):
        ns.apply_suppression(2, 'John Doe', None, applied_by=None)
        out = ns.redact_text('John Doe was booked in Cascade County on Tuesday.', 'Cascade')
        self.assertIn(ns.WITHHELD_LABEL, out)
        self.assertNotIn('John Doe', out)
        self.assertIn('Cascade County', out)  # county text untouched

    def test_redact_text_masks_three_token_name_in_any_order(self):
        ns.apply_suppression(3, 'Robert Stephen Swacker', 'Flathead', applied_by=None)
        out = ns.redact_text('Swacker, Robert Stephen listed in charging text.', 'Flathead')
        self.assertIn(ns.WITHHELD_LABEL, out)
        self.assertNotIn('Swacker, Robert Stephen', out)

    def test_app_booking_decorator_redacts_embedded_name_in_charges_summary(self):
        ns.apply_suppression(4, 'John Doe', None, applied_by=None)
        sys.modules.pop('app', None)
        app_module = importlib.import_module('app')
        row = {
            'person_name': 'John Doe',
            'county_name': 'Cascade',
            'county_slug': 'cascade',
            'charges_summary': 'Doe, John Misdemeanor Theft',
            'booking_status': 'released',
            'booking_at': None,
            'first_seen_at': None,
            'created_at': None,
            'last_seen_at': None,
            'updated_at': None,
            'notes': '',
        }
        item = app_module._decorate_jail_booking_row(row)
        self.assertEqual(item['person_name'], ns.WITHHELD_LABEL)
        self.assertIn(ns.WITHHELD_LABEL, item['charges_summary'])
        self.assertNotIn('John Doe', item['charges_summary'])
        self.assertNotIn('Doe, John', item['charges_summary'])

    def test_warrant_decorator_redacts_embedded_name_in_charges_text(self):
        ns.apply_suppression(5, 'John Doe', None, applied_by=None)
        from services.persons.warrants_public import _decorate_warrant_row

        row = {
            'id': 1,
            'source_record_id': 'src-1',
            'county': 'Cascade',
            'city': 'Great Falls',
            'person_name': 'John Doe',
            'dob': '1990-01-01',
            'warrant_type': 'bench',
            'charges_text': 'Doe, John Failure to Appear',
            'issued_by': 'Cascade County District Court',
            'issue_date': '2026-01-01',
            'bond_amount': '$500',
            'bond_type': 'cash',
            'status': 'active',
            'source_url': 'https://example.com',
            'mugshot_url': '',
            'photo_url': '',
            'social_profile_url': '',
            'updated_at': '2026-01-01 00:00:00',
            'first_seen_at': '2026-01-01 00:00:00',
            'resolved_at': '',
        }
        item = _decorate_warrant_row(row)
        self.assertEqual(item['person_name'], ns.WITHHELD_LABEL)
        self.assertIn(ns.WITHHELD_LABEL, item['charges_text'])
        self.assertNotIn('John Doe', item['charges_text'])
        self.assertNotIn('Doe, John', item['charges_text'])

    def test_apply_suppression_idempotent(self):
        first = ns.apply_suppression(6, 'John Doe', 'Cascade')
        second = ns.apply_suppression(6, 'John Doe', 'Cascade')
        self.assertTrue(first)
        self.assertFalse(second)
        rows = self.conn.execute('SELECT COUNT(*) AS c FROM suppressed_names').fetchone()['c']
        self.assertEqual(rows, 1)


class NameRemovalWebhookTestCase(unittest.TestCase):
    """Verify the Stripe webhook marks a paid request without auto-applying."""

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        self.prev = config.DB_PATH
        config.DB_PATH = self.db_path
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        _make_tables(self.conn)
        self.conn.execute(
            "INSERT INTO name_suppression_requests (email, person_name, status) VALUES (?, ?, 'pending')",
            ('requester@example.com', 'John Doe'),
        )
        self.conn.commit()
        self.request_id = self.conn.execute('SELECT last_insert_rowid() AS id').fetchone()['id']
        self.conn.close()
        sys.modules.pop('app', None)
        self.app_module = importlib.import_module('app')

    def tearDown(self):
        config.DB_PATH = self.prev
        os.unlink(self.db_path)

    def _run_webhook(self, session_id='cs_test_123', payment_id='pi_test_456'):
        event = {
            'id': 'evt_test',
            'type': 'checkout.session.completed',
            'data': {
                'object': {
                    'id': session_id,
                    'payment_intent': payment_id,
                    'client_reference_id': str(self.request_id),
                    'metadata': {'flow': 'name_removal', 'request_id': str(self.request_id)},
                }
            },
        }
        conn = sqlite3.connect(self.db_path)
        try:
            self.app_module._apply_name_removal_stripe_event(conn, event)
        finally:
            conn.close()

    def test_webhook_marks_paid_and_does_not_apply(self):
        self._run_webhook()
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            'SELECT status, stripe_payment_id FROM name_suppression_requests WHERE id = ?',
            (self.request_id,),
        ).fetchone()
        suppressed = conn.execute('SELECT COUNT(*) AS c FROM suppressed_names').fetchone()['c']
        conn.close()
        self.assertEqual(row['status'], 'paid')
        self.assertEqual(row['stripe_payment_id'], 'pi_test_456')
        # critical: name must NOT be suppressed until human review
        self.assertEqual(suppressed, 0)


class NameRemovalCheckoutTestCase(unittest.TestCase):
    """Verify the checkout route creates a request row and a Stripe session."""

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        self.prev = config.DB_PATH
        config.DB_PATH = self.db_path
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        _make_tables(self.conn)
        self.conn.commit()
        self.conn.close()
        sys.modules.pop('app', None)
        self.app_module = importlib.import_module('app')
        self.app_module.app.config['TESTING'] = True
        self.app_module.config.NAME_SUPPRESS_PRICE_ID = 'price_test_name'
        self.client = self.app_module.app.test_client()

    def tearDown(self):
        config.DB_PATH = self.prev
        os.unlink(self.db_path)

    def test_checkout_creates_request_and_stripe_session(self):
        fake_session = MagicMock()
        fake_session.id = 'cs_test_abc'
        fake_session.url = 'https://checkout.stripe.com/c/pay/cs_test_abc'

        with patch.multiple(config, NAME_SUPPRESS_PRICE_ID='price_test_name',
                            STRIPE_SECRET_KEY='sk_test'):
            with patch('stripe.checkout.Session.create', return_value=fake_session) as create_mock:
                resp = self.client.post(
                    '/remove-my-name',
                    data={
                        'person_name': 'John Doe',
                        'dob': '1990-01-01',
                        'county': 'Cascade',
                        'email': 'requester@example.com',
                    },
                    follow_redirects=False,
                )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(create_mock.call_args.kwargs['mode'], 'payment')
        self.assertEqual(create_mock.call_args.kwargs['line_items'][0]['price'], 'price_test_name')
        md = create_mock.call_args.kwargs['metadata']
        self.assertEqual(md['flow'], 'name_removal')

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            'SELECT person_name, county, email, status, stripe_session_id FROM name_suppression_requests'
        ).fetchone()
        conn.close()
        self.assertEqual(row['person_name'], 'John Doe')
        self.assertEqual(row['county'], 'Cascade')
        self.assertEqual(row['status'], 'pending')
        self.assertEqual(row['stripe_session_id'], 'cs_test_abc')


class NameRemovalPublicRenderTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix='.db')
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

        conn = app_module.get_db()
        source_id = conn.execute(
            """
            INSERT INTO jail_booking_sources (county_slug, county_name, facility_name, roster_url)
            VALUES (?, ?, ?, ?)
            """,
            ('flathead', 'Flathead', 'Flathead County Detention Center', 'https://example.com/roster'),
        ).lastrowid
        self.booking_id = conn.execute(
            """
            INSERT INTO jail_bookings (
                source_id, county_slug, county_name, facility_name, person_name,
                booking_number, booking_at, charges_summary, charges_json, name_slug
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                'flathead',
                'Flathead',
                'Flathead County Detention Center',
                'Swacker, Robert Stephen',
                'B-123',
                '2026-08-30 12:00:00',
                'Swacker, Robert Stephen listed in charging text',
                '[{"description": "Swacker, Robert Stephen listed in charge JSON"}]',
                'swacker-robert-stephen',
            ),
        ).lastrowid
        request_id = conn.execute(
            """
            INSERT INTO name_suppression_requests (email, person_name, county, status)
            VALUES (?, ?, ?, 'paid')
            """,
            ('requester@example.com', 'Robert Stephen Swacker', 'Flathead'),
        ).lastrowid
        conn.commit()
        conn.close()

        ns.apply_suppression(request_id, 'Robert Stephen Swacker', 'Flathead', applied_by=None)
        self.client = app_module.app.test_client()

    def tearDown(self):
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = self.previous_app_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_booking_detail_redacts_suppressed_name(self):
        resp = self.client.get(f'/booking/{self.booking_id}')
        body = resp.get_data(as_text=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(ns.WITHHELD_LABEL, body)
        self.assertNotIn('Swacker, Robert Stephen', body)

    def test_person_detail_redacts_suppressed_name(self):
        resp = self.client.get('/person/swacker-robert-stephen')
        body = resp.get_data(as_text=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(ns.WITHHELD_LABEL, body)
        self.assertNotIn('Robert Stephen Swacker', body)
        self.assertNotIn('Swacker, Robert Stephen', body)


if __name__ == '__main__':
    unittest.main()
