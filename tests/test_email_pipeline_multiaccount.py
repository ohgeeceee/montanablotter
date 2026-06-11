"""Tests for the bug where:
  1. email_image_blotter.py only polls the primary IMAP account (IONOS),
     missing Gmail-hosted county blotters and jail rosters.
  2. email_worker.py queue mode uses mail.search(None, "UNSEEN") which
     skips emails that were already marked as read by another client
     (phone push, webmail, Gmail auto-mark) — this is the gap that
     caused the 2026-06-09 YCSO weekly / Hill County jail roster /
     Havre 6-9 LOG / Helena HPD Press 6/9/26 emails to sit unprocessed
     in montanablotter@gmail.com.

These tests pin the contract: workers MUST scan every configured account,
and queue scan MUST use a date cutoff that catches already-read mail.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest import mock

sys.path.insert(0, "/root/montanablotter")

from email_image_blotter import ImageBlotterWorker
from email_worker import EmailWorker, _ImapAccount


def _msg_pdf(subject: str, sender: str, message_id: str = "<abc@test>") -> EmailMessage:
    m = EmailMessage()
    m["Subject"] = subject
    m["From"] = sender
    m["Message-ID"] = message_id
    m["Date"] = "Wed, 09 Jun 2026 16:00:00 +0000"
    m.set_content("Fake PDF body")
    return m


class _FakeIMAP:
    """Minimal stub of imaplib.IMAP4_SSL that records search/fetch calls."""

    def __init__(self, messages_by_account: dict[str, list[EmailMessage]]):
        # Map account label -> list of messages
        self.messages_by_account = messages_by_account
        self.connected_label: str | None = None
        self.selected: str | None = None
        self.searched: str | None = None
        self.store_calls: list[tuple[bytes, str, str]] = []
        self.copy_calls: list[tuple[bytes, str]] = []
        self.logout_called = False
        self.close_called = False

    def login(self, user, password):  # noqa: ARG002
        return ("OK", [b"login ok"])

    def select(self, mailbox, readonly=False):  # noqa: ARG002
        self.selected = mailbox
        return ("OK", [b"1"])

    def search(self, charset, criteria):
        self.searched = criteria
        msgs = self.messages_by_account.get(self.connected_label or "", [])
        # Filter by SINCE date in criteria for realism
        ids = [str(i + 1).encode() for i in range(len(msgs))]
        return ("OK", [b" ".join(ids)])

    def fetch(self, num, what):
        idx = int(num) - 1
        msgs = self.messages_by_account.get(self.connected_label or "", [])
        if idx >= len(msgs):
            return ("OK", [None])
        msg = msgs[idx]
        from email import policy
        raw = msg.as_bytes(policy=policy.default)
        return ("OK", [(b"1 (RFC822 {N}", raw)])

    def store(self, num, flags, value):
        self.store_calls.append((num, flags, value))
        return ("OK", [b""])

    def copy(self, num, folder):
        self.copy_calls.append((num, folder))
        return ("OK", [b""])

    def expunge(self):
        return ("OK", [b""])

    def create(self, folder):
        return ("OK", [b""])

    def close(self):
        self.close_called = True
        return ("OK", [b""])

    def logout(self):
        self.logout_called = True
        return ("OK", [b"BYE"])


def _accounts_two() -> list[_ImapAccount]:
    return [
        _ImapAccount(label="ionos", user="a", password="b", server="imap.ionos.com", port=993),
        _ImapAccount(label="gmail", user="c", password="d", server="imap.gmail.com", port=993),
    ]


class ImageBlotterWorkerScansAllAccountsTests(unittest.TestCase):
    """Pin: ImageBlotterWorker.fetch_and_process_emails MUST process mail from
    every configured IMAP account, not just the primary."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="imgblot_multi_")

    def tearDown(self):
        for p in Path(self.tmpdir).glob("*"):
            try:
                p.unlink()
            except Exception:
                pass

    def _make_worker(self, messages_by_account):
        # Build the worker, then inject a fake imaplib class
        worker = ImageBlotterWorker.__new__(ImageBlotterWorker)
        worker.upload_dir = self.tmpdir
        worker.accounts = _accounts_two()
        # Last-seen bookkeeping used by parent
        worker.last_havre_roster_published_count = 0
        # Parent __init__ would set these; we set them too for completeness
        worker.email_user = worker.accounts[0].user
        worker.email_pass = worker.accounts[0].password
        worker.imap_server = worker.accounts[0].server
        worker.imap_port = worker.accounts[0].port

        # Per-account fake IMAP instances keyed by account label
        self.fakes = {
            acct.label: _FakeIMAP({acct.label: messages_by_account.get(acct.label, [])})
            for acct in worker.accounts
        }
        return worker

    def _fake_connect(self, account, retries=3):
        """Replacement for EmailWorker._connect_imap; must be set as a bound
        attribute on the worker because the worker calls ``self._connect_imap``."""
        fake = self.fakes[account.label]
        fake.connected_label = account.label
        return fake

    def test_processes_pdf_from_secondary_gmail_account(self):
        """Gmail's '6-9 LOG' / 'JAILROSTER - 6/9/26' / 'HPD Press 6/9/26'
        must be processed even though IONOS is the primary account."""
        gmail_msg = _msg_pdf(
            subject="JAILROSTER - 6/9/26",
            sender="reichl@hillso.org",
            message_id="<gmail-1@test>",
        )
        # Attach a tiny PDF so the standard PDF path picks it up
        gmail_msg.add_attachment(
            b"%PDF-1.4\n%test\n%%EOF",
            maintype="application",
            subtype="pdf",
            filename="hill-6-9.pdf",
        )
        worker = self._make_worker({"ionos": [], "gmail": [gmail_msg]})

        # Bind a fake _connect_imap on the worker. Use a real instance method
        # so `self` binds correctly when the worker calls self._connect_imap(account).
        call_log: list[str] = []
        test_self = self
        def fake_connect(self_arg, account, retries=3):
            call_log.append(account.label)
            fake = test_self.fakes[account.label]
            fake.connected_label = account.label
            return fake
        import types
        worker._connect_imap = types.MethodType(fake_connect, worker)

        with mock.patch("email_image_blotter.ensure_source_document", return_value=1), \
             mock.patch("email_image_blotter.ensure_ingestion_job", return_value=1), \
             mock.patch("email_image_blotter.get_ingestion_job_status", return_value=None), \
             mock.patch("email_image_blotter.set_ingestion_job_status_legacy"), \
             mock.patch("email_image_blotter.log_pipeline_event"), \
             mock.patch("email_image_blotter.sha256_bytes", return_value="deadbeef"), \
             mock.patch("email_image_blotter.process_new_blotter", return_value=42):
            count = worker.fetch_and_process_emails(since_days=14)

        # The whole point: gmail account MUST be contacted.
        self.assertIn("gmail", call_log,
                      f"Worker must iterate all accounts; got call_log={call_log}")
        self.assertEqual(count, 0, "ImageBlotterWorker must process mail from gmail account (0 failed = 1 success)")
        # The gmail connection saw a SINCE search (date-based, not UNSEEN)
        self.assertIn("SINCE", self.fakes["gmail"].searched or "",
                      "Gmail scan must use a SINCE date, not UNSEEN, to catch read mail")

    def test_processes_mail_from_both_accounts(self):
        """If IONOS has 1 msg and gmail has 2, all 3 should be processed."""
        ionos_msg = _msg_pdf("havre old", "old@ci.havre.mt.us", "<i@test>")
        ionos_msg.add_attachment(b"%PDF-x", maintype="application", subtype="pdf", filename="a.pdf")
        g1 = _msg_pdf("havre 6-9 LOG", "broen@ci.havre.mt.us", "<g1@test>")
        g1.add_attachment(b"%PDF-y", maintype="application", subtype="pdf", filename="b.pdf")
        g2 = _msg_pdf("YCSO 06/01-06/07", "cherman@yellowstonecountymt.gov", "<g2@test>")
        g2.add_attachment(b"%PDF-z", maintype="application", subtype="pdf", filename="c.pdf")
        worker = self._make_worker({"ionos": [ionos_msg], "gmail": [g1, g2]})

        call_log: list[str] = []
        test_self = self
        def fake_connect(self_arg, account, retries=3):
            call_log.append(account.label)
            fake = test_self.fakes[account.label]
            fake.connected_label = account.label
            return fake
        import types
        worker._connect_imap = types.MethodType(fake_connect, worker)

        with mock.patch("email_image_blotter.ensure_source_document", return_value=1), \
             mock.patch("email_image_blotter.ensure_ingestion_job", return_value=1), \
             mock.patch("email_image_blotter.get_ingestion_job_status", return_value=None), \
             mock.patch("email_image_blotter.set_ingestion_job_status_legacy"), \
             mock.patch("email_image_blotter.log_pipeline_event"), \
             mock.patch("email_image_blotter.sha256_bytes", return_value="x"), \
             mock.patch("email_image_blotter.process_new_blotter", return_value=42):
            count = worker.fetch_and_process_emails(since_days=14)

        self.assertIn("gmail", call_log, "gmail account must be polled")
        self.assertIn("ionos", call_log, "ionos account must be polled")
        self.assertEqual(count, 0, f"Expected 3 processed (1 ionos + 2 gmail), got failure_count={count}")


class EmailWorkerQueueScanUsesDateCutoffTests(unittest.TestCase):
    """Pin: queue-mode scan must use SINCE date so already-read mail
    (marked read by phone push / webmail) still gets enqueued."""

    def test_queue_scan_uses_since_date_not_unseen(self):
        """The search criteria passed to IMAP must include SINCE."""
        worker = EmailWorker.__new__(EmailWorker)
        worker.accounts = _accounts_two()
        worker.last_havre_roster_published_count = 0
        worker.email_user = "a"
        worker.email_pass = "b"
        worker.imap_server = "imap.ionos.com"
        worker.imap_port = 993
        worker.upload_dir = tempfile.mkdtemp(prefix="queue_test_")
        worker.processed_folder = "Processed"

        # Single message that's "read" (no \Seen flag would be set if UNSEEN-only)
        # but should still be picked up by SINCE date
        msg = _msg_pdf(
            subject="YCSO reports 06/01 through 06/07",
            sender="cherman@yellowstonecountymt.gov",
            message_id="<y-1@test>",
        )
        msg.add_attachment(b"%PDF-y", maintype="application", subtype="pdf", filename="ycso.pdf")

        fake = _FakeIMAP({"gmail": [msg]})
        fake.connected_label = "gmail"

        def fake_connect(self, account, retries=3):
            fake.connected_label = account.label
            return fake

        with mock.patch.object(EmailWorker, "_connect_imap", new=fake_connect):
            items = worker.scan_mailbox_for_new_items()

        # At least one item should be enqueued
        self.assertEqual(len(items), 1, "SINCE-based scan must pick up the YCSO PDF")
        # And the search MUST be SINCE-based, not UNSEEN
        self.assertIn("SINCE", fake.searched or "",
                      f"Expected SINCE-based search, got {fake.searched!r}")
        self.assertNotEqual(fake.searched, "UNSEEN",
                            "Queue scan must not rely on UNSEEN flag — that misses already-read mail")


if __name__ == "__main__":
    unittest.main()
