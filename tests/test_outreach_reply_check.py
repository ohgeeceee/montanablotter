"""Tests for services/outreach/reply_check.py (bounce/reply classification)."""
import os
import sqlite3
import tempfile
import unittest

from services.outreach import reply_check


class ClassificationTests(unittest.TestCase):
    def test_autobounce_subject(self):
        for subj in ('Undeliverable: your message to Bo',
                     'Delivery Status Notification (Failure)',
                     'Mail delivery failed: returning message to sender'):
            self.assertTrue(reply_check.AUTOBOUNCE_RE.search(subj), subj)

    def test_real_reply_not_notice(self):
        self.assertFalse(reply_check.AUTOBOUNCE_RE.search(
            'Re: Montana firms: your name came up in a recent booking'))

    def test_bounce_sender_guard(self):
        self.assertTrue(reply_check.BOUNCE_SENDERS.match('mailer-daemon'))
        self.assertFalse(reply_check.BOUNCE_SENDERS.match('bo'))

    def test_bounce_inner_recipient(self):
        # Bounce body quotes original recipient; inner match must win.
        body = ('Delivery to the following recipient failed: '
                'witness@example.com 5.1.1 user unknown')
        self.assertTrue(reply_check.AUTOBOUNCE_RE.search(body))


class ProspectDirectoryTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript("""
            CREATE TABLE lawyer_outreach_prospects (id INTEGER PRIMARY KEY,
                firm_name TEXT, status TEXT DEFAULT 'prospect');
            CREATE TABLE lawyer_outreach_emails (id INTEGER PRIMARY KEY,
                prospect_id INTEGER, to_addr TEXT, stage TEXT, status TEXT);
            CREATE TABLE bail_agency_outreach (id INTEGER PRIMARY KEY,
                agency_name TEXT, email TEXT, outreach_status TEXT DEFAULT 'new');
        """)
        self.conn.execute("INSERT INTO lawyer_outreach_prospects (id, firm_name) VALUES (1, 'A')")
        self.conn.execute("INSERT INTO lawyer_outreach_emails VALUES (1,1,'bo@x.com','day_1','sent')")
        self.conn.execute("INSERT INTO lawyer_outreach_emails VALUES (2,1,'dup@x.com','day_3','pending')")
        self.conn.execute("INSERT INTO bail_agency_outreach (id, agency_name, email) VALUES (9,'B','lisa@y.com')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)

    def test_directory_only_mailed_and_active(self):
        d = reply_check.prospect_directory(self.conn)
        self.assertEqual(d['bo@x.com'], ('lawyer', 'lawyer_outreach_prospects', 1))
        self.assertEqual(d['lisa@y.com'], ('bail', 'bail_agency_outreach', 9))
        self.assertNotIn('dup@x.com', d)  # pending, not sent yet

    def test_terminal_statuses_excluded(self):
        self.conn.execute("UPDATE lawyer_outreach_prospects SET status='replied' WHERE id=1")
        self.conn.execute("UPDATE bail_agency_outreach SET outreach_status='closed_won' WHERE id=9")
        self.conn.commit()
        d = reply_check.prospect_directory(self.conn)
        self.assertNotIn('bo@x.com', d)
        self.assertNotIn('lisa@y.com', d)

    def test_ledger_table_created(self):
        reply_check.prospect_directory(self.conn)
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE name='outreach_replies'").fetchone()
        self.assertIsNotNone(row)


if __name__ == '__main__':
    unittest.main()
