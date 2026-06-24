import io
import contextlib
import os
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from email.message import EmailMessage
from pathlib import Path
from unittest import mock

sys.path.insert(0, "/root/montanablotter")

from email_image_blotter import ImageBlotterWorker
from email_worker import _ImapAccount


def _build_minimal_docx_bytes() -> bytes:
    body_xml = (
        '<w:p><w:r><w:t xml:space="preserve">DOE, JOHN, 35, 1/15/2026 14:30, DUI</w:t></w:r></w:p>'
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body>{body_xml}</w:body>'
        '</w:document>'
    )
    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>'
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>'
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types_xml)
        archive.writestr("_rels/.rels", rels_xml)
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def _build_havre_roster_email() -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = "JAILROSTER 6-23-26"
    msg["From"] = "reichl <reichl@hillso.org>"
    msg["To"] = "records@montanablotter.com"
    msg["Message-ID"] = "<lock-test-msg@hillso.org>"
    msg["Date"] = "Tue, 23 Jun 2026 12:00:00 +0000"
    msg.set_content("Jail roster attached.")
    msg.add_attachment(
        _build_minimal_docx_bytes(),
        maintype="application",
        subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename="JAILROSTER.docx",
    )
    return msg


class _FakeImap:
    def __init__(self, message_bytes: bytes) -> None:
        self.message_bytes = message_bytes
        self.copy_calls: list[tuple[bytes, str]] = []
        self.store_calls: list[tuple[bytes, str, str]] = []
        self.selected_mailbox: str | None = None
        self.closed = False
        self.logged_out = False

    def select(self, mailbox: str):
        self.selected_mailbox = mailbox
        return ("OK", [b""])

    def search(self, charset, criteria):
        return ("OK", [b"1"])

    def fetch(self, num, query):
        return ("OK", [(b"1 (RFC822)", self.message_bytes)])

    def copy(self, num, folder):
        self.copy_calls.append((num, folder))
        return ("OK", [b""])

    def store(self, num, action, flag):
        self.store_calls.append((num, action, flag))
        return ("OK", [b""])

    def expunge(self):
        return ("OK", [b""])

    def close(self):
        self.closed = True

    def logout(self):
        self.logged_out = True


class ImageBlotterLockDeferralTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="email_image_blotter_test_")
        self.worker = ImageBlotterWorker()
        self.worker.upload_dir = self.tmpdir
        self.worker.accounts = [
            _ImapAccount(
                label="gmail",
                user="records@montanablotter.com",
                password="secret",
                server="imap.example.test",
                port=993,
            )
        ]
        # Avoid touching the production DB for duplicate/Message-ID checks.
        self._already_processed_patch = mock.patch.object(
            self.worker, "_already_processed", return_value=False
        )
        self._already_processed_patch.start()

    def tearDown(self) -> None:
        self._already_processed_patch.stop()
        for path in Path(self.tmpdir).glob("*"):
            with contextlib.suppress(Exception):
                path.unlink()
        with contextlib.suppress(Exception):
            Path(self.tmpdir).rmdir()

    @mock.patch("services.ingestion.fetchers.havre_inmate.ingest_havre_roster")
    def test_havre_db_lock_is_deferred_not_failed(self, ingest_mock) -> None:
        """A transient 'database is locked' error must not fail the job."""
        ingest_mock.side_effect = sqlite3.OperationalError("database is locked")

        msg_bytes = _build_havre_roster_email().as_bytes()
        fake_mail = _FakeImap(msg_bytes)

        with mock.patch.object(self.worker, "_connect_imap", return_value=fake_mail):
            processed, skipped, failed, lock_deferred = self.worker._fetch_and_process_for_account(
                self.worker.accounts[0],
                '(SINCE "01-Jan-2026")',
            )

        self.assertEqual(processed, 0)
        self.assertEqual(skipped, 0)
        self.assertEqual(failed, 0)
        self.assertEqual(lock_deferred, 1)
        # Email must remain in the inbox so the next cron tick can retry it.
        self.assertEqual(fake_mail.copy_calls, [])
        self.assertEqual(fake_mail.store_calls, [])
        ingest_mock.assert_called_once()

    @mock.patch("services.ingestion.fetchers.havre_inmate.ingest_havre_roster")
    def test_havre_success_still_counts_processed(self, ingest_mock) -> None:
        ingest_mock.return_value = mock.Mock(
            fetched_count=1,
            new_count=1,
            updated_count=0,
            missing_count=0,
        )

        msg_bytes = _build_havre_roster_email().as_bytes()
        fake_mail = _FakeImap(msg_bytes)

        with mock.patch.object(self.worker, "_connect_imap", return_value=fake_mail):
            processed, skipped, failed, lock_deferred = self.worker._fetch_and_process_for_account(
                self.worker.accounts[0],
                '(SINCE "01-Jan-2026")',
            )

        self.assertEqual(processed, 1)
        self.assertEqual(skipped, 0)
        self.assertEqual(failed, 0)
        self.assertEqual(lock_deferred, 0)
        self.assertEqual(fake_mail.copy_calls, [(b"1", self.worker.processed_folder)])

    @mock.patch("services.ingestion.fetchers.havre_inmate.ingest_havre_roster")
    def test_havre_non_lock_error_counts_as_failed(self, ingest_mock) -> None:
        """Other ingest errors should still be treated as failures."""
        ingest_mock.side_effect = RuntimeError("parser exploded")

        msg_bytes = _build_havre_roster_email().as_bytes()
        fake_mail = _FakeImap(msg_bytes)

        with mock.patch.object(self.worker, "_connect_imap", return_value=fake_mail):
            processed, skipped, failed, lock_deferred = self.worker._fetch_and_process_for_account(
                self.worker.accounts[0],
                '(SINCE "01-Jan-2026")',
            )

        self.assertEqual(processed, 0)
        self.assertEqual(skipped, 0)
        self.assertEqual(failed, 1)
        self.assertEqual(lock_deferred, 0)
        # Failed attachments are left in the inbox; only max-retried emails move.
        self.assertEqual(fake_mail.copy_calls, [])


if __name__ == "__main__":
    unittest.main()
