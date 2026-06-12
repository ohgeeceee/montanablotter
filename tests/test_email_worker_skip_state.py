"""Pin the email_worker._process_attachments skip contract.

The 2026-06-11 backlog of 32 stuck ingestion_jobs was caused by this
loop in email_worker.py: every 15-min cron run would re-save the same
PDF, re-set status='extracted', and re-enqueue a duplicate RQ job —
because the only skip condition was ``status == 'published'``. The fix
extends the skip to cover all terminal states (published, failed,
skipped) and short-circuits in-flight attachments whose updated_at is
within a 30-minute debounce window so the live RQ job isn't stomped.

These tests pin the contract so a future refactor doesn't accidentally
narrow the skip back to the original 3-line check.
"""
import io
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, UTC
from email.message import EmailMessage
from pathlib import Path
from unittest import mock

sys.path.insert(0, "/root/montanablotter")

import app as app_module
import config
import init_db
from email_worker import EmailWorker


# Minimal valid PDF blob (one-page empty PDF). Good enough for the
# sha256 + storage_path paths that the worker touches before the
# status check.
_MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
    b"xref\n0 4\n"
    b"0000000000 65535 f \n"
    b"0000000010 00000 n \n"
    b"0000000053 00000 n \n"
    b"0000000102 00000 n \n"
    b"trailer<</Size 4/Root 1 0 R>>\n"
    b"startxref\n"
    b"149\n"
    b"%%EOF"
)


def _pdf_email(filename: str = "test.pdf", message_id: str = "<seed@test>") -> EmailMessage:
    m = EmailMessage()
    m["Subject"] = "Test blotter"
    m["From"] = "seed@example.com"
    m["To"] = "records@montanablotter.com"
    m["Message-ID"] = message_id
    m["Date"] = "Wed, 09 Jun 2026 16:00:00 +0000"
    m.set_content("body")
    m.add_attachment(
        _MINIMAL_PDF,
        maintype="application",
        subtype="pdf",
        filename=filename,
    )
    return m


_SEED_MESSAGE_ID = "<seed@test>"


class EmailWorkerSkipStateTests(unittest.TestCase):
    """Exercise the terminal / in-flight skip branches in
    _process_attachments. These tests run in inline mode so we can
    observe the side effects (no real RQ queue needed).
    """

    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="mb-skip-state-", suffix=".db")
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        self.previous_app_db_path = app_module.config.DB_PATH

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path

        # Build just the tables this test needs. init_db.init_database()
        # has a pre-existing bootstrap ordering bug (it calls
        # ensure_incident_notification_schema before _create_core_tables
        # reaches the subscribers CREATE TABLE) that fails on a fresh
        # test DB.
        conn = sqlite3.connect(self.db_path)
        conn.executescript(
            """
            CREATE TABLE source_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_type TEXT,
                source_message_id TEXT,
                source_sender TEXT,
                source_subject TEXT,
                source_received_at TEXT,
                filename TEXT,
                content_sha256 TEXT,
                storage_path TEXT,
                raw_text TEXT,
                extraction_method TEXT,
                extraction_warnings TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE ingestion_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_document_id INTEGER,
                status TEXT,
                retry_count INTEGER DEFAULT 0,
                last_error TEXT,
                started_at TEXT,
                finished_at TEXT,
                source_key TEXT,
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE pipeline_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ingestion_job_id INTEGER,
                stage TEXT,
                status TEXT,
                details_json TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            """
        )
        conn.commit()
        conn.close()

        self.tmpdir = tempfile.mkdtemp(prefix="mb-skip-state-uploads-")
        self.worker = EmailWorker()
        self.worker.upload_dir = self.tmpdir

        # Force inline mode so the test doesn't need a live RQ worker.
        self._pipeline_mode_patch = mock.patch.dict(
            os.environ, {"MB_PIPELINE_MODE": "inline"}
        )
        self._pipeline_mode_patch.start()

    def tearDown(self) -> None:
        self._pipeline_mode_patch.stop()
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = self.previous_app_db_path
        for path in Path(self.tmpdir).glob("*"):
            with contextlib.suppress(Exception):
                path.unlink()
        with contextlib.suppress(Exception):
            Path(self.tmpdir).rmdir()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _seed_job(self, status: str, updated_minutes_ago: int) -> int:
        """Insert a source_document + ingestion_job with the given status
        and an updated_at that's ``updated_minutes_ago`` minutes in the
        past. Returns the ingestion_job id.

        content_sha256 is computed from _MINIMAL_PDF so it matches the
        hash the worker will compute from the email's PDF attachment.
        Without that match, ensure_source_document would create a new
        row keyed on (source_type, sha256) and the skip check would
        never see the seeded status.
        """
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        updated_at = (datetime.now(UTC) - timedelta(minutes=updated_minutes_ago)).strftime("%Y-%m-%d %H:%M:%S")
        sha256 = hashlib.sha256(_MINIMAL_PDF).hexdigest()
        cur.execute(
            """
            INSERT INTO source_documents (
                source_type, source_message_id, source_sender, source_subject,
                source_received_at, filename, content_sha256, storage_path,
                raw_text, extraction_method, extraction_warnings, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                "imap_pdf", _SEED_MESSAGE_ID, "seed@example.com", "Test blotter",
                "2026-06-09 16:00:00", "test.pdf", sha256,
                "/dev/null", None, "pdf_attachment", "[]",
            ),
        )
        sd_id = cur.lastrowid
        cur.execute(
            """
            INSERT INTO ingestion_jobs (
                source_document_id, status, retry_count, last_error,
                started_at, finished_at, source_key, updated_at
            ) VALUES (?, ?, 0, NULL, datetime('now'), NULL, ?, ?)
            """,
            (sd_id, status, _SEED_MESSAGE_ID, updated_at),
        )
        job_id = cur.lastrowid
        conn.commit()
        conn.close()
        return job_id

    def _files_in_upload_dir(self) -> list[str]:
        return sorted(p.name for p in Path(self.tmpdir).glob("*"))

    # --- Terminal state branches ---

    def test_published_attachment_is_skipped_and_not_resaved(self) -> None:
        """Pre-existing 'published' status: skip, no file written, no
        re-enqueue. Preserves the original behavior."""
        self._seed_job("published", updated_minutes_ago=5)
        before = self._files_in_upload_dir()
        with mock.patch("email_worker.process_new_blotter") as inline:
            had, succeeded = self.worker._process_attachments(
                _pdf_email(filename="already_done.pdf"),
                source_message_id=_SEED_MESSAGE_ID,
                sender="seed@example.com",
                subject="Test blotter",
                received_at="Wed, 09 Jun 2026 16:00:00 +0000",
            )
        self.assertTrue(had)
        self.assertTrue(succeeded)
        self.assertEqual(self._files_in_upload_dir(), before)
        inline.assert_not_called()

    def test_failed_attachment_is_skipped_and_not_resaved(self) -> None:
        """A 'failed' status (e.g. LLM call timed out on first attempt)
        must NOT trigger a re-save + re-enqueue. The pre-fix code did
        re-enqueue these every 15 min."""
        self._seed_job("failed", updated_minutes_ago=60)
        before = self._files_in_upload_dir()
        with mock.patch("email_worker.process_new_blotter") as inline:
            self.worker._process_attachments(
                _pdf_email(filename="failed_first_attempt.pdf"),
                source_message_id=_SEED_MESSAGE_ID,
                sender="seed@example.com",
                subject="Test blotter",
                received_at="Wed, 09 Jun 2026 16:00:00 +0000",
            )
        self.assertEqual(self._files_in_upload_dir(), before)
        inline.assert_not_called()

    def test_skipped_attachment_is_skipped(self) -> None:
        """Same as published/failed: 'skipped' is a terminal state we
        don't want to retry."""
        self._seed_job("skipped", updated_minutes_ago=60)
        with mock.patch("email_worker.process_new_blotter") as inline:
            self.worker._process_attachments(
                _pdf_email(filename="skipped.pdf"),
                source_message_id=_SEED_MESSAGE_ID,
                sender="seed@example.com",
                subject="Test blotter",
                received_at="Wed, 09 Jun 2026 16:00:00 +0000",
            )
        inline.assert_not_called()

    # --- In-flight state branches ---

    def test_inflight_within_debounce_is_skipped(self) -> None:
        """'extracted' status with updated_at = 2 minutes ago: an RQ
        worker is presumably still chewing on it. Skip to avoid
        stomping the live job."""
        self._seed_job("extracted", updated_minutes_ago=2)
        with mock.patch("email_worker.process_new_blotter") as inline:
            self.worker._process_attachments(
                _pdf_email(filename="live_rq_job.pdf"),
                source_message_id=_SEED_MESSAGE_ID,
                sender="seed@example.com",
                subject="Test blotter",
                received_at="Wed, 09 Jun 2026 16:00:00 +0000",
            )
        inline.assert_not_called()

    def test_inflight_outside_debounce_is_re_enqueued(self) -> None:
        """'extracted' status with updated_at = 60 minutes ago: the RQ
        job is presumed dead. Allow the worker to re-save and
        re-process, otherwise the source stays stuck forever."""
        self._seed_job("extracted", updated_minutes_ago=60)
        with mock.patch("email_worker.process_new_blotter") as inline:
            inline.return_value = 999
            self.worker._process_attachments(
                _pdf_email(filename="presumed_dead.pdf"),
                source_message_id=_SEED_MESSAGE_ID,
                sender="seed@example.com",
                subject="Test blotter",
                received_at="Wed, 09 Jun 2026 16:00:00 +0000",
            )
        # Inline mode means the re-enqueue path is process_new_blotter.
        inline.assert_called_once()


import contextlib  # noqa: E402  (used in tearDown)
import hashlib  # noqa: E402  (used in _seed_job to match worker-computed sha256)


if __name__ == "__main__":
    unittest.main()
