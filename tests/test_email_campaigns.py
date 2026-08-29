"""
Integration tests for the Email Campaigns admin tab.

Run:  source venv/bin/activate && python3 -m pytest tests/test_email_campaigns.py -v
"""
from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import patch

from blueprints.admin.email_campaigns import (
    _ensure_template_schema,
    _seed_default_templates,
    _count_recipients,
    _sample_recipients,
    _collect_recipient_emails,
    _render_template,
    _send_email,
    _smtp_settings,
    _send_email as send_one,
)


class TestEmailCampaignsSchema(unittest.TestCase):
    """Test that the email_templates and email_campaigns tables are created correctly."""

    def setUp(self):
        # Use a temporary in-memory database for isolation
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        _ensure_template_schema(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_email_templates_table_exists(self):
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='email_templates'"
        ).fetchone()
        self.assertIsNotNone(row, "email_templates table should exist")

    def test_email_campaigns_table_exists(self):
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='email_campaigns'"
        ).fetchone()
        self.assertIsNotNone(row, "email_campaigns table should exist")

    def test_email_templates_has_expected_columns(self):
        cols = {r['name'] for r in self.conn.execute("PRAGMA table_info(email_templates)").fetchall()}
        expected = {'id', 'name', 'audience', 'subject', 'body', 'notes', 'created_at', 'updated_at'}
        missing = expected - cols
        self.assertEqual(missing, set(), f"Missing columns: {missing}")

    def test_email_campaigns_has_expected_columns(self):
        cols = {r['name'] for r in self.conn.execute("PRAGMA table_info(email_campaigns)").fetchall()}
        expected = {
            'id', 'campaign_name', 'template_id', 'audience', 'subject', 'body',
            'html_body', 'status', 'sent_at', 'sent_by', 'total_recipients',
            'success_count', 'failure_count', 'failure_emails', 'created_at', 'updated_at',
        }
        missing = expected - cols
        self.assertEqual(missing, set(), f"Missing columns: {missing}")

    def test_seed_default_templates(self):
        _seed_default_templates(self.conn)
        count = self.conn.execute("SELECT COUNT(*) FROM email_templates").fetchone()[0]
        self.assertEqual(count, 6, "Should seed 6 default templates")

    def test_seed_is_idempotent(self):
        _seed_default_templates(self.conn)
        first = self.conn.execute("SELECT COUNT(*) FROM email_templates").fetchone()[0]
        _seed_default_templates(self.conn)
        second = self.conn.execute("SELECT COUNT(*) FROM email_templates").fetchone()[0]
        self.assertEqual(first, second, "Seeding twice should not duplicate templates")


class TestTemplateRendering(unittest.TestCase):
    """Test the [Placeholder] token replacement in templates."""

    def test_basic_replacement(self):
        body = "Hello [Name], welcome to [County]."
        ctx = {"Name": "Alex", "County": "Flathead"}
        result = _render_template(body, ctx)
        self.assertEqual(result, "Hello Alex, welcome to Flathead.")

    def test_missing_placeholder_left_unchanged(self):
        body = "Hello [Name], your [Plan] plan."
        ctx = {"Name": "Alex"}  # no Plan
        result = _render_template(body, ctx)
        self.assertEqual(result, "Hello Alex, your [Plan] plan.")

    def test_multiple_placeholders(self):
        body = "[Name] from [County], plan: [Plan]"
        ctx = {"Name": "Sam", "County": "Gallatin", "Plan": "Free"}
        result = _render_template(body, ctx)
        self.assertEqual(result, "Sam from Gallatin, plan: Free")

    def test_empty_context(self):
        body = "Hello [Name]"
        result = _render_template(body, {})
        self.assertEqual(result, "Hello [Name]")  # token stays

    def test_no_placeholders(self):
        body = "Plain text email body"
        result = _render_template(body, {"Name": "X"})
        self.assertEqual(result, "Plain text email body")


class TestRecipientCounting(unittest.TestCase):
    """Test that recipient queries return correct counts from sample data."""

    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        # Create minimal schema needed for lookups
        self.conn.execute("""
            CREATE TABLE subscribers (
                id INTEGER PRIMARY KEY,
                email TEXT,
                agency_name TEXT,
                counties TEXT,
                subscriber_plan TEXT,
                active INTEGER DEFAULT 1
            )
        """)
        self.conn.execute("""
            CREATE TABLE public_users (
                id INTEGER PRIMARY KEY,
                email TEXT,
                display_name TEXT,
                subscriber_plan TEXT,
                is_active INTEGER DEFAULT 1
            )
        """)
        self.conn.execute("""
            CREATE TABLE emailed_agencies (
                id INTEGER PRIMARY KEY,
                agency_name TEXT,
                email_address TEXT
            )
        """)
        # Seed test data: lawyers and bail bondsmen subscribers
        # NOTE: lawyers/bondsmen queries filter on agency_name LIKE keywords
        # so we must populate agency_name with matching text — empty strings find nothing.
        self.conn.executemany(
            "INSERT INTO subscribers (email, agency_name, counties, subscriber_plan, active) VALUES (?, ?, ?, ?, 1)",
            [
                ("law1@andersonlaw.example", "Anderson Law Firm", "Yellowstone", "pro",),
                ("law2@borealis.example", "Borealis Legal Counsel LLC", "Gallatin", "free",),
                ("bond1@suretybond.example", "Surety Bond Agency of MT", "Flathead", "pro",),
                ("bond2@bigskybail.example", "Big Sky Bail Bonds", "Cascade", "free",),
                # clients (subscribers with no agency = general subscribers)
                ("client1@test.com", "", "", "free",),
                ("client2@test.com", "", "", "pro",),
            ],
        )
        self.conn.executemany(
            "INSERT INTO public_users (email, display_name, subscriber_plan, is_active) VALUES (?, ?, ?, 1)",
            [
                ("user1@test.com", "Jane User", "free",),
                ("user2@test.com", "Bob Viewer", "pro",),
            ],
        )
        self.conn.executemany(
            "INSERT INTO emailed_agencies (agency_name, email_address) VALUES (?, ?)",
            [
                ("Yellowstone County Court", "court@yellowstone.mt.gov",),
                ("Billings Police Department", "bpd@billingsmt.gov",),
                ("Helena Police Department", "hpd@helenamt.gov",),
            ],
        )

    def tearDown(self):
        self.conn.close()

    def test_lawyers_count(self):
        n = _count_recipients(self.conn, 'lawyers')
        self.assertEqual(n, 2, "Should find 2 lawyer-type subscribers (Anderson + Borealis)")

    def test_bail_bondsmen_count(self):
        n = _count_recipients(self.conn, 'bail_bondsmen')
        self.assertEqual(n, 2, "Should find 2 bail-bondsman-type subscribers (Surety + Big Sky)")

    def test_clients_count(self):
        n = _count_recipients(self.conn, 'clients')
        self.assertEqual(n, 8, "Should find 2 subscribers (clients) + 2 public_users + 4 audience-specific subscribers (their emails are UNION'd) = 8")

    def test_courts_count(self):
        n = _count_recipients(self.conn, 'courts')
        self.assertEqual(n, 3, "Should find 1 court + 2 police = 3 (court.mt.gov matches both)")

    def test_police_count(self):
        n = _count_recipients(self.conn, 'police')
        self.assertEqual(n, 2, "Should find 2 police department emails")

    def test_sample_recipients_lawyers(self):
        sample = _sample_recipients(self.conn, 'lawyers', limit=2)
        self.assertEqual(len(sample), 2)
        self.assertIn("Anderson Law Firm", [s['name'] for s in sample])
        self.assertIn("Borealis Legal Counsel LLC", [s['name'] for s in sample])

    def test_sample_recipients_courts(self):
        sample = _sample_recipients(self.conn, 'courts', limit=5)
        self.assertEqual(len(sample), 3)  # 1 court + 2 police match .gov pattern

    def test_collect_with_extra_emails(self):
        # Pass an extra email that overlaps with a lawyer recipient to test dedup
        emails = _collect_recipient_emails(self.conn, 'lawyers', 'extra@example.com, law1@andersonlaw.example')
        self.assertIn('extra@example.com', emails)
        self.assertIn('law1@andersonlaw.example', emails)  # from both lawyers + extra; deduped
        self.assertEqual(len(emails), 3)  # 2 lawyers + 1 extra (law1 deduped)


class TestSMTPSettings(unittest.TestCase):
    """Test SMTP config lookup (without actually sending)."""

    def test_smtp_settings_from_config(self):
        with patch('blueprints.admin.email_campaigns.config') as mock_config:
            mock_config.SMTP_SERVER = 'smtp.test.com'
            mock_config.SMTP_PORT = '587'
            mock_config.SMTP_USER = 'test@example.com'
            mock_config.SMTP_PASSWORD = 'secret'
            settings = _smtp_settings()
            self.assertEqual(settings['server'], 'smtp.test.com')
            self.assertEqual(settings['port'], 587)
            self.assertEqual(settings['user'], 'test@example.com')
            self.assertEqual(settings['password'], 'secret')

    def test_smtp_settings_falls_back_to_email(self):
        import types
        with patch.dict('os.environ', {'EMAIL_USER': 'fallback@example.com'}, clear=True):
            mock_config = types.SimpleNamespace()
            mock_config.SMTP_SERVER = ''
            mock_config.SMTP_PORT = 0
            mock_config.SMTP_PASSWORD = ''
            mock_config.EMAIL_USER = 'fallback@example.com'
            # SMTP_USER absent (not set on mock) → getattr falls through to EMAIL_USER
            with patch('blueprints.admin.email_campaigns.config', mock_config):
                settings = _smtp_settings()
                self.assertEqual(settings['user'], 'fallback@example.com')

    def test_smtp_not_configured(self):
        with patch('blueprints.admin.email_campaigns.config') as mock_config:
            mock_config.SMTP_SERVER = ''
            mock_config.SMTP_PORT = 0
            mock_config.SMTP_USER = ''
            mock_config.SMTP_PASSWORD = ''
            ok, err = send_one('test@example.com', 'Subject', 'Body')
            self.assertFalse(ok)
            self.assertEqual(err, 'smtp_not_configured')


if __name__ == '__main__':
    unittest.main()
