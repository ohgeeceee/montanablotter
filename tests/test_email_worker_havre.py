import io
import contextlib
import os
import sys
import tempfile
import unittest
import zipfile
from email.message import EmailMessage
from pathlib import Path
from unittest import mock

sys.path.insert(0, "/root/montanablotter")

from email_worker import EmailWorker, _ImapAccount


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


class HavreEmailWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="havre_email_worker_")
        self.worker = EmailWorker()
        self.worker.upload_dir = self.tmpdir
        os.makedirs(self.tmpdir, exist_ok=True)

    def tearDown(self) -> None:
        for path in Path(self.tmpdir).glob("*"):
            with contextlib.suppress(Exception):
                path.unlink()
        with contextlib.suppress(Exception):
            Path(self.tmpdir).rmdir()

    def _build_message(self) -> EmailMessage:
        msg = EmailMessage()
        msg["Subject"] = "Havre Daily Jail Roster"
        msg["From"] = '"Havre Police Department" <records@havremt.gov>'
        msg["To"] = "records@montanablotter.com"
        msg.set_content("Attached is the daily roster.")
        msg.add_attachment(
            _build_minimal_docx_bytes(),
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename="Havre_Roster.docx",
        )
        return msg

    def _build_forwarded_message(self) -> EmailMessage:
        msg = EmailMessage()
        msg["Subject"] = "Hill County Detention Center Jail Roster"
        msg["From"] = '"My Email" <me@example.com>'
        msg["To"] = "records@montanablotter.com"
        msg.set_content("Forwarded daily roster from the county.")
        msg.add_attachment(
            _build_minimal_docx_bytes(),
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename="Hill_County_Jail_Roster.docx",
        )
        return msg

    def _build_multi_docx_message(self) -> EmailMessage:
        msg = EmailMessage()
        msg["Subject"] = "Havre Daily Jail Roster"
        msg["From"] = '"Havre Police Department" <records@havremt.gov>'
        msg["To"] = "records@montanablotter.com"
        msg.set_content("Attached are the daily roster files.")
        msg.add_attachment(
            _build_minimal_docx_bytes(),
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename="Havre_Roster_1.docx",
        )
        msg.add_attachment(
            _build_minimal_docx_bytes(),
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename="Havre_Roster_2.docx",
        )
        return msg

    @mock.patch("services.ingestion.fetchers.havre_inmate.ingest_havre_roster")
    def test_havre_docx_email_routes_to_roster_ingest(self, ingest_mock) -> None:
        ingest_mock.return_value = mock.Mock(
            fetched_count=1,
            new_count=1,
            updated_count=0,
            missing_count=0,
        )

        msg = self._build_message()
        had_attachment, any_succeeded = self.worker._process_attachments(
            msg,
            source_message_id="<message-id>",
            sender=msg["From"],
            subject=msg["Subject"],
            received_at="Mon, 01 Jan 2026 10:00:00 +0000",
        )

        self.assertTrue(had_attachment)
        self.assertTrue(any_succeeded)
        ingest_mock.assert_called_once()
        saved_path = Path(ingest_mock.call_args.args[0])
        self.assertTrue(saved_path.exists())
        self.assertEqual(saved_path.suffix.lower(), ".docx")
        self.assertEqual(saved_path.parent, Path(self.tmpdir))

    @mock.patch("services.ingestion.fetchers.havre_inmate.ingest_havre_roster")
    def test_forwarded_hill_county_docx_email_routes_to_roster_ingest(self, ingest_mock) -> None:
        ingest_mock.return_value = mock.Mock(
            fetched_count=1,
            new_count=1,
            updated_count=0,
            missing_count=0,
        )

        msg = self._build_forwarded_message()
        had_attachment, any_succeeded = self.worker._process_attachments(
            msg,
            source_message_id="<forwarded-message-id>",
            sender=msg["From"],
            subject=msg["Subject"],
            received_at="Mon, 01 Jan 2026 10:00:00 +0000",
        )

        self.assertTrue(had_attachment)
        self.assertTrue(any_succeeded)
        ingest_mock.assert_called_once()

    @mock.patch("services.ingestion.fetchers.havre_inmate.ingest_havre_roster")
    def test_fetch_and_process_emails_processes_havre_docx_inbox(self, ingest_mock) -> None:
        ingest_mock.return_value = mock.Mock(
            fetched_count=1,
            new_count=1,
            updated_count=0,
            missing_count=0,
        )

        class FakeMail:
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

        msg_bytes = self._build_message().as_bytes()
        fake_mail = FakeMail(msg_bytes)
        self.worker.accounts = [
            _ImapAccount(
                label="havre",
                user="records@montanablotter.com",
                password="secret",
                server="imap.example.test",
                port=993,
            )
        ]

        with mock.patch.object(self.worker, "_connect_imap", return_value=fake_mail):
            processed = self.worker.fetch_and_process_emails()

        self.assertEqual(processed, 1)
        ingest_mock.assert_called_once()
        self.assertEqual(fake_mail.selected_mailbox, "INBOX")
        self.assertEqual(fake_mail.copy_calls, [(b"1", self.worker.processed_folder)])
        self.assertIn((b"1", "+FLAGS", "\\Deleted"), fake_mail.store_calls)
        self.assertTrue(fake_mail.closed)
        self.assertTrue(fake_mail.logged_out)

    @mock.patch("services.ingestion.fetchers.havre_inmate.ingest_havre_roster")
    def test_havre_docx_email_with_multiple_attachments_ingests_each_file(
        self,
        ingest_mock,
    ) -> None:
        ingest_mock.return_value = mock.Mock(
            fetched_count=1,
            new_count=1,
            updated_count=0,
            missing_count=0,
        )

        msg = self._build_multi_docx_message()
        had_attachment, any_succeeded = self.worker._process_attachments(
            msg,
            source_message_id="<message-id>",
            sender=msg["From"],
            subject=msg["Subject"],
            received_at="Mon, 01 Jan 2026 10:00:00 +0000",
        )

        self.assertTrue(had_attachment)
        self.assertTrue(any_succeeded)
        self.assertEqual(ingest_mock.call_count, 2)

        saved_paths = [Path(call.args[0]) for call in ingest_mock.call_args_list]
        # Filenames are date-tagged (YYYYMMDD_<original>) so HCSO's reuse of
        # the same DOCX attachment name across days can't clobber prior
        # rosters on disk.
        self.assertEqual(
            [p.name for p in saved_paths],
            ["20260101_Havre_Roster_1.docx", "20260101_Havre_Roster_2.docx"],
        )
        self.assertTrue(all(p.exists() for p in saved_paths))
        self.assertTrue(all(p.parent == Path(self.tmpdir) for p in saved_paths))

    def test_queue_scan_emits_warning_when_email_consumed_with_no_action(self) -> None:
        """An email with no PDF, no havre roster match, and no skip reason
        must surface a WARNING — the diagnostic gap that let the hillso.org
        outage hide for 4+ days.
        """

        class FakeMail:
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

        # Plain text email — no PDF, no docx, no havre keywords.
        msg = EmailMessage()
        msg["Subject"] = "Quarterly Newsletter"
        msg["From"] = '"Marketing" <marketing@example.com>'
        msg["To"] = "records@montanablotter.com"
        msg.set_content("Hello, here's our spring newsletter.")
        fake_mail = FakeMail(msg.as_bytes())
        self.worker.accounts = [
            _ImapAccount(
                label="ionos",
                user="records@montanablotter.com",
                password="secret",
                server="imap.example.test",
                port=993,
            )
        ]

        with mock.patch.object(self.worker, "_connect_imap", return_value=fake_mail), \
             self.assertLogs(level="WARNING") as cm:
            self.worker.scan_mailbox_for_new_items()

        self.assertTrue(
            any("consumed email with no action" in line for line in cm.output),
            f"Expected silent-consume WARNING, got: {cm.output}",
        )
        # The email is still moved to processed (operator can find it in the
        # Processed folder if they want to inspect / restore).
        self.assertEqual(fake_mail.copy_calls, [(b"1", self.worker.processed_folder)])

    @mock.patch("services.ingestion.fetchers.havre_inmate.ingest_havre_roster")
    def test_same_attachment_name_on_different_days_keeps_separate_files(
        self,
        ingest_mock,
    ) -> None:
        """HCSO reuses the same DOCX attachment name ('JAILROSTER - 12-24-25.docx')
        across every daily email. Two emails with the same attachment name but
        different received_at dates must land at different storage paths so
        the second email doesn't silently clobber the first on disk.
        """
        ingest_mock.return_value = mock.Mock(
            fetched_count=1, new_count=1, updated_count=0, missing_count=0,
        )

        def _msg_for(received_at: str) -> EmailMessage:
            m = EmailMessage()
            m["Subject"] = "JAILROSTER"
            m["From"] = "reichl <reichl@hillso.org>"
            m["To"] = "records@montanablotter.com"
            m.set_content("attached")
            m.add_attachment(
                _build_minimal_docx_bytes(),
                maintype="application",
                subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
                filename="JAILROSTER - 12-24-25.docx",
            )
            return m

        for received_at in ("Mon, 08 Jun 2026 05:00:00 +0000",
                            "Tue, 09 Jun 2026 05:00:00 +0000"):
            msg = _msg_for(received_at)
            self.worker._process_attachments(
                msg,
                source_message_id="<id-{}>".format(received_at[:10]),
                sender=msg["From"],
                subject=msg["Subject"],
                received_at=received_at,
            )

        saved_paths = [Path(call.args[0]) for call in ingest_mock.call_args_list]
        self.assertEqual(len(saved_paths), 2)
        # Different dates => different filenames. The original 'JAILROSTER -
        # 12-24-25.docx' name is preserved as a suffix for traceability.
        self.assertNotEqual(saved_paths[0].name, saved_paths[1].name)
        self.assertTrue(all(p.exists() for p in saved_paths))
        self.assertEqual(saved_paths[0].name, "20260608_JAILROSTER - 12-24-25.docx")
        self.assertEqual(saved_paths[1].name, "20260609_JAILROSTER - 12-24-25.docx")

if __name__ == "__main__":
    unittest.main()
