"""
Email Worker - Fetches blotter PDFs from IONOS email and processes them
Unified version replacing email_worker.py and fetch_mail.py
"""

import argparse
import imaplib
import email
import os
import logging
import smtplib
import re
import contextlib
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, UTC
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from rq import Retry
import config
from services.blotter.processor import process_new_blotter, process_text_blotter
from services.blotter.parser import parse_text_blotter
from core.queue_config import ingestion_q
from core.queue_helpers import redis_lock
from core.pipeline_state import (
    ensure_ingestion_job,
    ensure_source_document,
    get_ingestion_job_status,
    get_ingestion_job_state,
    increment_ingestion_retry,
    log_pipeline_event,
    set_ingestion_job_status,
    set_ingestion_job_status_legacy,
    sha256_bytes,
    sha256_text,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso(value) -> datetime:
    """Parse a sqlite ``datetime('now')``-style string (``YYYY-MM-DD HH:MM:SS``)
    into a tz-aware UTC datetime. Pass-through if already a datetime."""
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    text = str(value).strip() if value is not None else ""
    if not text:
        raise ValueError("empty datetime string")
    # sqlite returns "YYYY-MM-DD HH:MM:SS" without a timezone — treat as UTC.
    # Tolerate ISO-8601 with "T" or trailing "Z"/offset too.
    if "T" in text or (len(text) > 10 and ("+" in text[10:] or text.endswith("Z"))):
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    else:
        dt = datetime.fromisoformat(text).replace(tzinfo=UTC)
    return dt


def _parse_email_date_to_iso(date_header: str) -> str | None:
    """Coerce an RFC 2822 ``Date:`` header into ``YYYY-MM-DD`` (UTC date).

    Returns ``None`` for missing / unparseable input — callers fall back to
    the filename-stem source_record_id format in that case.
    """
    if not date_header:
        return None
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(date_header)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return None


# Setup logging
logging.basicConfig(
    filename=config.LOG_FILE,
    level=getattr(logging, config.LOG_LEVEL),
    format=config.LOG_FORMAT
)

@dataclass(frozen=True)
class _ImapAccount:
    """One IMAP inbox the email worker polls.

    `label` is a short identifier used only for log lines ("ionos", "gmail").
    An account is considered configured when user and password are both
    non-empty. The IONOS account is always present if its envs are set;
    the Gmail account is present only when MB_GMAIL_IMAP_USER and
    MB_GMAIL_IMAP_PASSWORD are both non-empty.
    """
    label: str
    user: str
    password: str
    server: str
    port: int

    @property
    def is_configured(self) -> bool:
        return bool((self.user or '').strip() and (self.password or '').strip())


class EmailWorker:
    """Handles fetching and processing blotter emails from one or more IMAP inboxes."""

    def __init__(self):
        self.upload_dir = config.UPLOAD_DIR or UPLOAD_DIR
        self.processed_folder = config.PROCESSED_FOLDER
        self.accounts: list[_ImapAccount] = self._load_accounts()
        self.last_havre_roster_published_count = 0

        # Backwards-compat aliases: a single configured account keeps the
        # old attribute names working for any external reader.
        primary = self.accounts[0] if self.accounts else None
        self.email_user = primary.user if primary else ''
        self.email_pass = primary.password if primary else ''
        self.imap_server = primary.server if primary else ''
        self.imap_port = primary.port if primary else 0

        os.makedirs(self.upload_dir, exist_ok=True)

    @staticmethod
    def _load_accounts() -> list[_ImapAccount]:
        """Build the list of IMAP accounts to poll, in priority order.

        IONOS (records@montanablotter.com) is always first when configured.
        Gmail (montanablotter@gmail.com) is appended when its envs are set.
        """
        accounts: list[_ImapAccount] = []
        if (config.EMAIL_USER or '').strip() and (config.EMAIL_PASSWORD or '').strip():
            accounts.append(_ImapAccount(
                label='ionos',
                user=config.EMAIL_USER,
                password=config.EMAIL_PASSWORD,
                server=config.IMAP_SERVER or 'imap.ionos.com',
                port=int(config.IMAP_PORT or 993),
            ))
        if (config.GMAIL_IMAP_USER or '').strip() and (config.GMAIL_IMAP_PASSWORD or '').strip():
            accounts.append(_ImapAccount(
                label='gmail',
                user=config.GMAIL_IMAP_USER,
                password=config.GMAIL_IMAP_PASSWORD,
                server=config.GMAIL_IMAP_SERVER or 'imap.gmail.com',
                port=int(config.GMAIL_IMAP_PORT or 993),
            ))
        return accounts

    def _validate_accounts(self) -> str | None:
        """Return an error string if no account is usable, else None."""
        if not self.accounts:
            return (
                'No IMAP accounts configured — set MB_EMAIL_USER + MB_EMAIL_PASSWORD '
                'or MB_GMAIL_IMAP_USER + MB_GMAIL_IMAP_PASSWORD'
            )
        for acct in self.accounts:
            if (acct.password or '').strip().lower() in {'replace-me', 'changeme', 'change-me'}:
                return f'IMAP account {acct.label!r} password is still a placeholder value'
        return None

    # Backwards-compat shim: email_image_blotter.py and the inline caller
    # path historically called this method name. Delegates to the new
    # multi-account validator.
    def _validate_imap_config(self) -> str | None:
        return self._validate_accounts()

    def _connect_imap(self, account: _ImapAccount, retries: int = 3) -> imaplib.IMAP4_SSL:
        """Connect and authenticate to a specific IMAP account with exponential backoff.

        Uses a 30-second socket timeout so a hanging IMAP server never stalls
        the email pipeline for hours.
        """
        import socket
        delay = 2
        last_exc: Exception = RuntimeError(f"IMAP connection failed for {account.label}")
        for attempt in range(retries):
            try:
                mail = imaplib.IMAP4_SSL(account.server, account.port, timeout=30)
                mail.login(account.user, account.password)
                return mail
            except (imaplib.IMAP4.error, OSError, socket.timeout) as exc:
                last_exc = exc
                if attempt < retries - 1:
                    logging.warning(
                        f"IMAP connect [{account.label}] attempt {attempt + 1} failed: {exc}; "
                        f"retrying in {delay}s"
                    )
                    time.sleep(delay)
                    delay *= 2
        raise last_exc

    @staticmethod
    def _sender_domain(sender: str) -> str:
        sender_match = re.search(r'[\w.+-]+@([\w.-]+)', sender or '', re.I)
        return (sender_match.group(1).lower() if sender_match else '')

    @classmethod
    def _sender_looks_like_public_safety(cls, sender: str) -> bool:
        domain = cls._sender_domain(sender)
        if not domain:
            return False
        return any(
            marker in domain
            for marker in (
                '.gov',
                'county',
                'sheriff',
                'police',
                'cityof',
                'ci.',
                'mt.gov',
            )
        )

    @staticmethod
    def _has_date_like_text(body: str) -> bool:
        return bool(re.search(r'\b\d{1,2}/\d{1,2}/(?:\d{2}|\d{4})\b', body or ''))

    @classmethod
    def _preview_looks_structured(cls, preview: dict) -> bool:
        incidents = preview.get('incidents') or []
        if not incidents:
            return False
        for incident in incidents:
            date_text = (incident.get('date') or '').strip()
            if not re.match(r'^\d{1,2}/\d{1,2}/(?:\d{2}|\d{4})$', date_text):
                continue
            has_context = any(
                (incident.get(field) or '').strip()
                for field in ('cfs_number', 'incident_type', 'location', 'details')
            )
            if has_context:
                return True
        return False

    @classmethod
    def _preview_is_plausible_text_blotter(
        cls,
        preview: dict,
        *,
        subject: str,
        sender: str,
        body: str,
    ) -> bool:
        total_count = int(preview.get('total_count') or 0)
        if total_count <= 0:
            return False
        if not cls._preview_looks_structured(preview):
            return False
        if cls._sender_looks_like_public_safety(sender):
            return True
        text = " ".join([subject or "", body[:2000] or ""]).lower()
        strong_markers = (
            'blotter',
            'media log',
            'daily activity',
            'daily log',
            'calls for service',
            'call log',
            'dispatch log',
            'public report',
        )
        if any(marker in text for marker in strong_markers):
            return True
        return total_count >= 3 and cls._has_date_like_text(body)

    def _looks_like_blotter_email(self, subject: str, sender: str, body: str) -> bool:
        text = " ".join([subject or "", sender or "", body[:4000] or ""]).lower()
        # Self-sent pipeline notifications (cron failure/recovery alerts) must
        # never be ingested as blotter content — their bodies are worker logs,
        # not police activity. Subjects like "[Montana Blotter] Scheduled job ..."
        # and bodies mentioning "Missoula public report" would otherwise trip
        # the positive markers below.
        if sender and 'montanablotter' in sender.lower():
            return False

        negative_markers = (
            'unsubscribe',
            'manage preferences',
            'view in browser',
            'privacy policy',
            'marketing',
            'promotion',
            'promo',
            'webinar',
            'newsletter',
            'product update',
            'release notes',
            'free trial',
            'pricing',
            'invoice',
            'receipt',
            'trusted data sources',
            'microsoft azure',
            'product announcement',
            'scheduled job',
            'job recovered',
            'pipeline',
            'cron',
        )
        if any(marker in text for marker in negative_markers):
            return False

        strong_positive_markers = (
            'blotter',
            'media log',
            'daily activity',
            'daily log',
            'calls for service',
            'call log',
            'dispatch log',
            'public report',
        )
        if any(marker in text for marker in strong_positive_markers):
            return True

        weak_positive_markers = (
            'dispatch',
            'incident',
            'arrest',
            'cad',
            'police',
            'sheriff',
        )
        weak_matches = sum(1 for marker in weak_positive_markers if marker in text)
        if weak_matches >= 2 and self._has_date_like_text(body):
            return True

        return self._sender_looks_like_public_safety(sender) and self._has_date_like_text(body)

    @staticmethod
    def _has_docx_attachment(msg) -> bool:
        """Return True when the message carries at least one .docx attachment."""
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get("Content-Disposition") is None:
                continue
            filename = part.get_filename()
            if filename and filename.lower().endswith(".docx"):
                return True
        return False

    @staticmethod
    def _looks_like_havre_jail_roster(subject: str, sender: str, body: str = "", msg=None) -> bool:
        """Detect a Havre Police Department daily jail roster email.

        The roster is usually a DOCX attachment. We deliberately keep the
        allow-list narrow enough to avoid routing unrelated DOCX mail, but
        we do not require the sender to be a Havre domain because forwarded
        rosters often arrive from a personal inbox.
        """
        subj = (subject or "").lower()
        snd = (sender or "").lower()
        body_text = (body or "")[:4000].lower()
        combined = " ".join([subj, snd, body_text])

        # "hillso" matches the Hill County Sheriff's Office (hillso.org) sender
        # domain — HCSO is the source of record for the daily jail roster DOCX
        # and their messages carry an empty body, so the sender is the only
        # signal we get. "hill county" alone misses it (no space, no "county").
        if not any(
            kw in combined
            for kw in ("havre", "hill county", "hillso", "hpd")
        ):
            return False

        roster_markers = ("roster", "inmate", "booking report", "jail", "detention", "daily roster")
        if not any(marker in combined for marker in roster_markers):
            return False

        if msg is not None:
            if not EmailWorker._has_docx_attachment(msg):
                return False

        return True

    def _process_havre_roster_attachments(
        self, msg, *, source_message_id: str, sender: str, subject: str,
        received_at: str = "",
    ) -> tuple[bool, bool]:
        """Save the .docx attachments from a Havre roster email and ingest
        each one into the ``jail_bookings`` table via the havre_inmate
        adapter. Returns ``(had_attachment, any_succeeded)`` matching the
        contract of ``_process_attachments`` so the caller can move the
        email out of INBOX on success.

        ``received_at`` is the email's ``Date:`` header. It's used to derive
        a per-day ``roster_date`` (YYYY-MM-DD) so each HCSO roster is scoped
        to its own day in the jail_bookings.source_record_id — HCSO re-uses
        the same DOCX filename every day, so without this every re-ingest
        would silently no-op on dedup.
        """
        try:
            from services.ingestion.fetchers.havre_inmate import ingest_havre_roster
        except Exception as exc:  # pragma: no cover - import failure path
            logging.error("Could not import havre_inmate: %s", exc)
            return True, False

        roster_date = _parse_email_date_to_iso(received_at) if received_at else None

        had_attachment = False
        any_succeeded = False
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get("Content-Disposition") is None:
                continue
            filename = part.get_filename()
            if not (filename and filename.lower().endswith(".docx")):
                continue
            had_attachment = True
            payload = part.get_payload(decode=True) or b""
            # HCSO reuses the same DOCX filename across daily emails, so the
            # bare filename would silently overwrite prior rosters on disk.
            # Scope the storage path by roster_date so each day's ingest
            # sees its own file even if the same attachment name recurs.
            stem, ext = os.path.splitext(filename)
            date_tag = (roster_date or "undated").replace("-", "")
            dated_filename = f"{date_tag}_{filename}" if not stem.startswith(date_tag) else filename
            filepath = os.path.join(self.upload_dir, dated_filename)
            with open(filepath, "wb") as f:
                f.write(payload)
            logging.info(
                "Havre roster DOCX saved: message_id=%s filename=%s path=%s roster_date=%s",
                source_message_id, dated_filename, filepath, roster_date,
            )
            try:
                stats = ingest_havre_roster(filepath, roster_date=roster_date)
                logging.info(
                    "Havre roster ingested: filename=%s fetched=%s new=%s updated=%s missing=%s",
                    filename,
                    stats.fetched_count,
                    stats.new_count,
                    stats.updated_count,
                    stats.missing_count,
                )
                logging.info(
                    "Havre roster published to jail_bookings: message_id=%s filename=%s county=%s",
                    source_message_id,
                    filename,
                    "Hill",
                )
                any_succeeded = True
            except sqlite3.OperationalError as exc:
                # Transient lock contention with another writer (e.g. court_refresh)
                # should be decided by the caller. Re-raise so the email stays in
                # the inbox and the caller can retry on the next tick.
                if "database is locked" in str(exc).lower():
                    raise
                logging.exception(
                    "Failed to ingest Havre roster DOCX %s: %s", filename, exc
                )
            except Exception as exc:
                logging.exception(
                    "Failed to ingest Havre roster DOCX %s: %s", filename, exc
                )

        return had_attachment, any_succeeded

    def fetch_and_process_emails(self):
        """Main entry point — poll every configured IMAP account and process
        its UNSEEN messages. Accounts are processed sequentially in the
        order returned by ``_load_accounts`` (IONOS first, Gmail second if
        configured). A message already seen in an earlier account of this
        run is skipped (Message-ID dedup).
        """
        config_error = self._validate_accounts()
        if config_error:
            logging.error(f"Email worker config error: {config_error}")
            return 0

        total_processed = 0
        seen_message_ids: set[str] = set()

        for account in self.accounts:
            try:
                total_processed += self._process_account_inbox(account, seen_message_ids)
            except Exception as e:
                logging.error(f"Account {account.label!r} failed: {e}")
                continue

        logging.info(
            f"Email worker complete: {total_processed} emails processed across "
            f"{len(self.accounts)} account(s)"
        )
        return total_processed

    def _process_account_inbox(
        self,
        account: '_ImapAccount',
        seen_message_ids: set[str],
    ) -> int:
        """Process the UNSEEN folder of one IMAP account. Returns the
        number of emails successfully processed from that account."""
        mail = self._connect_imap(account)
        try:
            mail.select("INBOX")
            logging.info(
                f"Connected to {account.label} IMAP ({account.server}) successfully"
            )

            status, messages = mail.search(None, 'UNSEEN')
            if status != 'OK' or not messages[0]:
                logging.info(f"[{account.label}] No new emails found")
                return 0

            email_ids = messages[0].split()
            logging.info(
                f"[{account.label}] Found {len(email_ids)} unread emails to scan"
            )

            processed_count = 0
            for num in email_ids:
                try:
                    res, msg_data = mail.fetch(num, "(RFC822)")

                    for response_part in msg_data:
                        if not isinstance(response_part, tuple):
                            continue

                        msg = email.message_from_bytes(response_part[1])

                        subject = msg.get('subject', 'No Subject')
                        sender = msg.get('from', 'Unknown')
                        message_id = (msg.get('Message-ID', '') or '').strip()
                        logging.info(
                            f"[{account.label}] Processing email: {subject!r} from {sender}"
                        )

                        # Dedupe across accounts (rare, but possible with
                        # forwarding rules or shared inboxes).
                        if message_id and message_id in seen_message_ids:
                            logging.info(
                                f"[{account.label}] Skipping duplicate Message-ID: {message_id}"
                            )
                            self._move_to_processed(mail, num)
                            continue
                        if message_id:
                            seen_message_ids.add(message_id)

                        # Skip bounce / delivery-failure emails
                        if 'mailer-daemon' in sender.lower() or 'delivery' in subject.lower():
                            logging.info(
                                f"[{account.label}] Skipping bounce/delivery email: {subject}"
                            )
                            self._move_to_processed(mail, num)
                            continue

                        msg_date = msg.get('Date', '')
                        had_pdf, pdf_succeeded = self._process_attachments(
                            msg,
                            source_message_id=message_id,
                            sender=sender,
                            subject=subject,
                            received_at=msg_date,
                        )

                        if had_pdf:
                            if pdf_succeeded:
                                self._move_to_processed(mail, num)
                                processed_count += 1
                                logging.info(
                                    f"[{account.label}] Successfully processed email with attachment(s)"
                                )
                            else:
                                logging.error(
                                    f"[{account.label}] Attachment(s) found but all failed to process: {subject}"
                                )
                        else:
                            # No PDF — try plain-text body as blotter
                            body, body_method = self._extract_body_text(msg)
                            if body and len(body.strip()) > 200:
                                if not self._looks_like_blotter_email(subject, sender, body):
                                    logging.info(
                                        f"[{account.label}] Skipping non-blotter text email: {subject}"
                                    )
                                    self._move_to_processed(mail, num)
                                    continue

                                try:
                                    preview = parse_text_blotter(body)
                                except Exception as e:
                                    logging.warning(
                                        f"[{account.label}] Text-body preview parse failed for {subject}: {e}"
                                    )
                                    preview = {'total_count': 0}

                                if preview.get('total_count', 0) <= 0:
                                    logging.info(
                                        f"[{account.label}] Skipping text email with no extractable incidents: {subject}"
                                    )
                                    self._move_to_processed(mail, num)
                                    continue
                                if not self._preview_is_plausible_text_blotter(
                                    preview,
                                    subject=subject,
                                    sender=sender,
                                    body=body,
                                ):
                                    logging.info(
                                        f"[{account.label}] Skipping text email with weak blotter structure: {subject}"
                                    )
                                    self._move_to_processed(mail, num)
                                    continue

                                body_hash = sha256_text(body)
                                source_document_id = ensure_source_document(
                                    source_type='imap_text',
                                    source_message_id=message_id,
                                    source_sender=sender,
                                    source_subject=subject,
                                    source_received_at=msg_date,
                                    filename=None,
                                    content_sha256=body_hash,
                                    storage_path=None,
                                    raw_text=body,
                                    extraction_method=body_method,
                                    extraction_warnings=[],
                                )
                                ingestion_job_id = ensure_ingestion_job(source_document_id)
                                existing_status = get_ingestion_job_status(source_document_id)
                                if existing_status == 'published':
                                    self._move_to_processed(mail, num)
                                    processed_count += 1
                                    logging.info(
                                        f"[{account.label}] Skipped already-published text source document"
                                    )
                                    continue

                                set_ingestion_job_status_legacy(ingestion_job_id, 'extracted')
                                log_pipeline_event(
                                    ingestion_job_id,
                                    'extract',
                                    'ok',
                                    {'extraction_method': body_method, 'message': 'email-body-extracted'},
                                )
                                try:
                                    process_text_blotter(
                                        body,
                                        sender_email=sender,
                                        source_document_id=source_document_id,
                                        ingestion_job_id=ingestion_job_id,
                                    )
                                    self._move_to_processed(mail, num)
                                    processed_count += 1
                                    logging.info(
                                        f"[{account.label}] Processed text-body blotter from email"
                                    )
                                except Exception as e:
                                    logging.error(
                                        f"[{account.label}] Failed to process text blotter: {e}"
                                    )
                                    increment_ingestion_retry(ingestion_job_id, str(e))
                                    set_ingestion_job_status_legacy(
                                        ingestion_job_id, 'failed', last_error=str(e), finished=True
                                    )
                                    log_pipeline_event(
                                        ingestion_job_id,
                                        'publish',
                                        'error',
                                        {'error': str(e)},
                                    )
                            else:
                                logging.info(
                                    f"[{account.label}] No blotter content found in email: {subject} — skipping"
                                )

                except Exception as e:
                    logging.error(
                        f"[{account.label}] Error processing email {num}: {e}"
                    )
                    continue

            mail.expunge()
            return processed_count
        finally:
            with contextlib.suppress(Exception):
                mail.close()
            with contextlib.suppress(Exception):
                mail.logout()

    def scan_mailbox_for_new_items(self) -> list[dict]:
        """
        First-pass queue migration path. Polls every configured IMAP account
        and returns enqueue item payloads for the RQ worker.

        PDF attachments are saved and enqueued (the queue worker parses them).
        Havre PD .docx jail rosters are NOT enqueued — they're processed
        inline by ``_process_havre_roster_attachments`` because the RQ
        pipeline only knows about PDF blotters. (The inline path is the
        canonical one for havre_inmate.py.)
        """
        config_error = self._validate_accounts()
        if config_error:
            logging.error(f"Email worker config error: {config_error}")
            return []

        items: list[dict] = []
        havre_roster_published = 0
        seen_message_ids: set[str] = set()

        for account in self.accounts:
            try:
                account_items, account_havre_count = self._scan_account_for_new_items(
                    account, seen_message_ids
                )
                items.extend(account_items)
                havre_roster_published += account_havre_count
            except Exception as e:
                logging.error(f"Queue scan for account {account.label!r} failed: {e}")
                continue

        self.last_havre_roster_published_count = havre_roster_published
        logging.info(
            f"Queue scan complete: {len(items)} item(s) across "
            f"{len(self.accounts)} account(s)"
        )
        if havre_roster_published:
            logging.info(
                "Havre roster queue summary: published=%s account(s)=%s",
                havre_roster_published,
                len(self.accounts),
            )
        return items

    def _scan_account_for_new_items(
        self,
        account: '_ImapAccount',
        seen_message_ids: set[str],
    ) -> tuple[list[dict], int]:
        """Scan one IMAP account for new blotter items.

        Uses ``SINCE <date>`` rather than ``UNSEEN`` so emails that were
        marked as read by another IMAP client (phone push, webmail,
        Gmail's own auto-read) still get picked up. The Message-ID
        dedupe via ``source_documents`` and the per-email skip reasons
        keep this safe to run frequently.
        """
        mail = self._connect_imap(account)
        try:
            mail.select("INBOX")
            from datetime import datetime, timedelta
            since_date = (datetime.now() - timedelta(days=14)).strftime("%d-%b-%Y")
            search_criteria = f'(SINCE "{since_date}")'
            status, messages = mail.search(None, search_criteria)
            if status != "OK" or not messages[0]:
                logging.info(
                    f"[{account.label}] No new emails found for queue scan (SINCE {since_date})"
                )
                return [], 0

            email_ids = messages[0].split()
            logging.info(
                f"[{account.label}] Queue scan found {len(email_ids)} emails SINCE {since_date}"
            )

            items: list[dict] = []
            havre_roster_published = 0
            for num in email_ids:
                try:
                    res, msg_data = mail.fetch(num, "(RFC822)")
                    if res != "OK":
                        logging.warning(
                            f"[{account.label}] Failed to fetch email id {num!r}"
                        )
                        continue

                    for response_part in msg_data:
                        if not isinstance(response_part, tuple):
                            continue

                        msg = email.message_from_bytes(response_part[1])
                        subject = msg.get("subject", "No Subject")
                        sender = msg.get("from", "Unknown")
                        message_id = (msg.get("Message-ID", "") or "").strip()

                        # Tracks whether any handler acted on this email.
                        # Set True by every code path below (skip with reason,
                        # route to havre, save PDF, etc.). If still False at
                        # the end, the queue scan consumed the email silently
                        # — emit a WARNING so this never hides a real bug
                        # again (this is exactly the gap that let the
                        # hillso.org havre roster outage hide for 4+ days).
                        email_handled = False

                        # Dedupe across accounts
                        if message_id and message_id in seen_message_ids:
                            logging.info(
                                f"[{account.label}] Queue scan skipping duplicate Message-ID: {message_id}"
                            )
                            self._move_to_processed(mail, num)
                            email_handled = True
                            continue
                        if message_id:
                            seen_message_ids.add(message_id)

                        if "mailer-daemon" in sender.lower() or "delivery" in subject.lower():
                            logging.info(
                                f"[{account.label}] Skipping bounce/delivery email in queue scan: {subject}"
                            )
                            self._move_to_processed(mail, num)
                            email_handled = True
                            continue

                        # Havre PD daily roster: route to the docx pipeline
                        # inline. The RQ worker only knows about PDFs, so we
                        # can't enqueue this — call the handler synchronously.
                        if self._looks_like_havre_jail_roster(subject, sender, msg=msg):
                            email_handled = True
                            logging.info(
                                f"[{account.label}] Queue scan routing Havre roster to jail_bookings pipeline: subject={subject!r}"
                            )
                            had_attach, any_succeeded = self._process_havre_roster_attachments(
                                msg,
                                source_message_id=message_id,
                                sender=sender,
                                subject=subject,
                                received_at=msg.get("Date", ""),
                            )
                            if had_attach:
                                self._move_to_processed(mail, num)
                                if not any_succeeded:
                                    logging.error(
                                        f"[{account.label}] Havre roster attachments found but all failed: {subject}"
                                    )
                                else:
                                    logging.info(
                                        f"[{account.label}] Havre roster published to jail_bookings: subject={subject!r}"
                                    )
                                    havre_roster_published += 1
                            continue

                        # Standard PDF path: save attachment and enqueue.
                        found_pdf = False
                        for part in msg.walk():
                            if part.get_content_maintype() == "multipart":
                                continue
                            if part.get("Content-Disposition") is None:
                                continue

                            filename = part.get_filename()
                            if not (filename and filename.lower().endswith(".pdf")):
                                continue

                            payload = part.get_payload(decode=True) or b""
                            if not payload:
                                continue

                            found_pdf = True
                            safe_filename = os.path.basename(filename)
                            filepath = os.path.join(self.upload_dir, safe_filename)
                            with open(filepath, "wb") as f:
                                f.write(payload)

                            source_key = message_id or f"{num.decode(errors='ignore')}:{safe_filename}"
                            items.append(
                                {
                                    "source_type": "email",
                                    "source_key": source_key,
                                    "attachment_path": filepath,
                                }
                            )
                            logging.info(
                                f"[{account.label}] Queue scan saved PDF attachment: message_id={source_key} path={filepath}"
                            )

                        if found_pdf:
                            self._move_to_processed(mail, num)
                            logging.info(
                                f"[{account.label}] Queue scan moved email to processed: {subject}"
                            )
                            email_handled = True
                        elif not email_handled:
                            # No PDF, no havre match, no skip-with-reason — the
                            # email is being consumed without anyone doing
                            # anything with it. Surface this so it can't
                            # hide a misconfigured handler again.
                            logging.warning(
                                f"[{account.label}] Queue scan consumed email with no action: subject={subject!r} sender={sender!r} message_id={message_id!r} — no PDF attachment, no Havre roster match, no skip reason. Check whether a new handler is needed."
                            )
                            self._move_to_processed(mail, num)
                            email_handled = True

                except Exception as e:
                    logging.error(
                        f"[{account.label}] Queue scan failed for email {num}: {e}"
                    )
                    continue

            mail.expunge()
            return items, havre_roster_published
        finally:
            with contextlib.suppress(Exception):
                mail.close()
            with contextlib.suppress(Exception):
                mail.logout()

    def smoke_check_connection(self) -> dict:
        """Read-only IMAP connectivity check used by ingestion smoke tests."""
        config_error = self._validate_imap_config()
        if config_error:
            raise RuntimeError(config_error)

        mail = imaplib.IMAP4_SSL(self.imap_server, self.imap_port)
        try:
            mail.login(self.email_user, self.email_pass)
            status, _ = mail.select("INBOX")
            if status != 'OK':
                raise RuntimeError("Unable to select INBOX")
            status, messages = mail.search(None, 'UNSEEN')
            if status != 'OK':
                raise RuntimeError("Unable to search unread email")
            unread_count = len(messages[0].split()) if messages and messages[0] else 0
            return {
                'mailbox': 'INBOX',
                'unread_count': unread_count,
            }
        finally:
            with contextlib.suppress(Exception):
                mail.close()
            with contextlib.suppress(Exception):
                mail.logout()
    
    def _process_attachments(self, msg, source_message_id: str, sender: str, subject: str, received_at: str) -> tuple[bool, bool]:
        """
        Extract and process attachments from email. Routes Havre PD
        daily roster emails to the jail_bookings DOCX pipeline; otherwise
        falls back to the standard PDF blotter pipeline.
        Returns (had_attachment, any_succeeded):
          had_attachment — True if at least one .pdf or .docx attachment was found
          any_succeeded  — True if at least one was successfully processed
        """
        # Route Havre PD daily roster emails to the jail_bookings pipeline
        # before the regular blotter dispatch (which would reject them).
        if self._looks_like_havre_jail_roster(subject, sender, msg=msg):
            logging.info(
                "Routing Havre PD roster email to jail_bookings pipeline: subject=%r sender=%r",
                subject, sender,
            )
            return self._process_havre_roster_attachments(
                msg,
                source_message_id=source_message_id,
                sender=sender,
                subject=subject,
                received_at=received_at,
            )

        had_pdf = False
        any_succeeded = False

        for part in msg.walk():
            if part.get_content_maintype() == 'multipart':
                continue
            if part.get('Content-Disposition') is None:
                continue

            filename = part.get_filename()
            if filename and filename.lower().endswith('.pdf'):
                had_pdf = True
                payload = part.get_payload(decode=True) or b''
                file_hash = sha256_bytes(payload)
                filepath = os.path.join(self.upload_dir, filename)

                source_document_id = ensure_source_document(
                    source_type='imap_pdf',
                    source_message_id=source_message_id,
                    source_sender=sender,
                    source_subject=subject,
                    source_received_at=received_at,
                    filename=filename,
                    content_sha256=file_hash,
                    storage_path=filepath,
                    raw_text=None,
                    extraction_method='pdf_attachment',
                    extraction_warnings=[],
                )
                ingestion_job_id = ensure_ingestion_job(source_document_id)
                existing_status, existing_updated = get_ingestion_job_state(source_document_id)
                # Skip if the source has reached a terminal state.
                # The previous version only skipped on 'published', which
                # meant every 15-min cron run re-saved the PDF, re-set
                # status='extracted', and re-enqueued another RQ job for
                # any attachment whose first attempt was 'failed' (e.g.
                # LLM call timed out) or 'extracted' (RQ worker crashed
                # mid-flight). That re-parse loop is what created the
                # backlog of 32 stuck jobs in 2026-06-11.
                if existing_status in ('published', 'failed', 'skipped'):
                    logging.info(
                        "Skipping terminal-status attachment: %s (status=%s)",
                        filename, existing_status,
                    )
                    any_succeeded = True
                    continue
                # In-flight: skip if an RQ worker recently touched the
                # row. updated_at is bumped on every status write, so a
                # fresh timestamp means a worker is actively progressing
                # the job. After INFLIGHT_DEBOUNCE_MIN with no update we
                # assume the RQ job died and let the re-enqueue below
                # recover the source.
                INFLIGHT_DEBOUNCE_MIN = 30
                if existing_status in ('extracted', 'parsed', 'normalized') and existing_updated:
                    try:
                        age_seconds = (datetime.now(UTC) - _parse_iso(existing_updated)).total_seconds()
                    except (TypeError, ValueError):
                        age_seconds = INFLIGHT_DEBOUNCE_MIN * 60 + 1
                    if age_seconds < INFLIGHT_DEBOUNCE_MIN * 60:
                        logging.info(
                            "Skipping in-flight attachment: %s (status=%s, age=%.1fmin)",
                            filename, existing_status, age_seconds / 60,
                        )
                        any_succeeded = True
                        continue
                    logging.info(
                        "Re-enqueuing presumed-dead attachment: %s (status=%s, age=%.1fmin)",
                        filename, existing_status, age_seconds / 60,
                    )

                with open(filepath, 'wb') as f:
                    f.write(payload)

                logging.info(f"Saved PDF: {filename}")
                set_ingestion_job_status_legacy(ingestion_job_id, 'extracted')
                log_pipeline_event(
                    ingestion_job_id,
                    'extract',
                    'ok',
                    {'filename': filename, 'storage_path': filepath},
                )

                try:
                    pipeline_mode = (os.getenv("MB_PIPELINE_MODE", "queue") or "queue").strip().lower()
                    if pipeline_mode == "inline":
                        batch_id = process_new_blotter(
                            filepath,
                            source_document_id=source_document_id,
                            ingestion_job_id=ingestion_job_id,
                        )
                        if batch_id:
                            logging.info(f"Processed PDF inline: {filename} -> Batch #{batch_id}")
                        else:
                            logging.info(f"Processed PDF inline: {filename} -> duplicate-only, no new batch created")
                    else:
                        ingestion_retry = Retry(max=5, interval=[30, 120, 300, 900, 1800])
                        job = ingestion_q.enqueue(
                            "core.tasks.process_incoming_email_item",
                            {
                                "source_type": "email",
                                "source_key": source_message_id or filename,
                                "attachment_path": filepath,
                                "source_document_id": int(source_document_id),
                                "ingestion_job_id": int(ingestion_job_id),
                            },
                            job_timeout=15 * 60,
                            retry=ingestion_retry,
                            result_ttl=24 * 60 * 60,
                            failure_ttl=14 * 24 * 60 * 60,
                        )
                        logging.info(f"Queued PDF for staged pipeline: {filename} -> job_id={job.id}")
                    any_succeeded = True
                except Exception as e:
                    logging.error(f"Failed to process PDF {filename}: {str(e)}")
                    increment_ingestion_retry(ingestion_job_id, str(e))
                    set_ingestion_job_status_legacy(
                        ingestion_job_id, 'failed', last_error=str(e), finished=True
                    )
                    log_pipeline_event(
                        ingestion_job_id,
                        'publish',
                        'error',
                        {'filename': filename, 'error': str(e)},
                    )

        return had_pdf, any_succeeded

    def _extract_body_text(self, msg) -> tuple[str, str]:
        plain = None
        html = None

        for part in msg.walk():
            content_type = part.get_content_type()
            if part.get_content_maintype() == 'multipart':
                continue
            if content_type == 'text/plain' and plain is None:
                payload = part.get_payload(decode=True)
                if payload:
                    plain = payload.decode(part.get_content_charset() or 'utf-8', errors='replace')
            elif content_type == 'text/html' and html is None:
                payload = part.get_payload(decode=True)
                if payload:
                    raw_html = payload.decode(part.get_content_charset() or 'utf-8', errors='replace')
                    # Strip tags with a simple regex for fallback purposes
                    html = re.sub(r'<[^>]+>', ' ', raw_html)
                    html = re.sub(r'\s+', ' ', html).strip()

        if plain is not None:
            return plain, 'email_plain'
        if html:
            return html, 'email_html'
        return "", 'none'
    
    def _move_to_processed(self, mail: imaplib.IMAP4_SSL, num: bytes) -> None:
        """Move email to Processed folder if it exists; otherwise just mark as read."""
        try:
            # Try to copy to Processed folder
            copy_status = mail.copy(num, self.processed_folder)
            if copy_status[0] == 'OK':
                mail.store(num, '+FLAGS', '\\Deleted')
            else:
                # Folder may not exist — just mark as read (SEEN)
                mail.store(num, '+FLAGS', '\\Seen')
        except Exception as e:
            # If copy fails (folder missing), just mark as read
            try:
                mail.store(num, '+FLAGS', '\\Seen')
            except Exception:
                pass
    
    @staticmethod
    def _sanitize_header(value: str) -> str:
        """Strip newlines to prevent email header injection."""
        return str(value).replace('\r', '').replace('\n', '').strip()

    def send_email(self, to_address, subject, body, html_body=None):
        """Send an email via SMTP"""
        try:
            # Create message
            msg = MIMEMultipart('alternative')
            msg['From'] = self._sanitize_header(config.SMTP_USER)
            msg['To'] = self._sanitize_header(to_address)
            msg['Subject'] = self._sanitize_header(subject)
            
            # Attach plain text version
            msg.attach(MIMEText(body, 'plain'))
            
            # Attach HTML version if provided
            if html_body:
                msg.attach(MIMEText(html_body, 'html'))
            
            # Connect to Gmail SMTP
            smtp = smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT)
            try:
                smtp.starttls()
                smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
                # Send from Gmail, reply-to IONOS address
                msg['Reply-To'] = self.email_user
                smtp.sendmail(config.SMTP_USER, to_address, msg.as_string())
            finally:
                smtp.quit()
            
            logging.info(f"Email sent successfully to {to_address}")
            return True
        
        except Exception as e:
            logging.error(f"Error sending email to {to_address}: {str(e)}")
            return False
    
    def send_bulk_emails(self, recipients, subject, body, html_body=None):
        """Send email to multiple recipients"""
        results = {}
        for recipient in recipients:
            results[recipient] = self.send_email(recipient, subject, body, html_body)
        return results


def run_worker():
    """Run the email worker once"""
    worker = EmailWorker()
    count = worker.fetch_and_process_emails()
    print(f"Processed {count} emails")
    return count


def scan_mailbox_for_new_items() -> list[dict]:
    worker = EmailWorker()
    return worker.scan_mailbox_for_new_items()


def process_inline_legacy() -> None:
    run_worker()


def enqueue_mode() -> int:
    queued = 0
    ingestion_retry = Retry(max=5, interval=[30, 120, 300, 900, 1800])
    worker = EmailWorker()

    with redis_lock("lock:email_worker_scan", timeout=15 * 60) as acquired:
        if not acquired:
            print(f"{utcnow_iso()} email_worker scan skipped: lock already held")
            return 0

        items = worker.scan_mailbox_for_new_items()
        for item in items:
            try:
                job = ingestion_q.enqueue(
                    "core.tasks.process_incoming_email_item",
                    item,
                    job_timeout=15 * 60,
                    retry=ingestion_retry,
                    result_ttl=24 * 60 * 60,
                    failure_ttl=14 * 24 * 60 * 60,
                )
                queued += 1
                print(
                    f"{utcnow_iso()} queued ingestion job_id={job.id} "
                    f"source_key={item.get('source_key')} attachment={item.get('attachment_path')}"
                )
            except Exception as e:
                logging.error(f"Failed to enqueue email item {item}: {e}")

    print(f"{utcnow_iso()} email_worker queued_count={queued}")
    print(
        f"{utcnow_iso()} email_worker havre_roster_published="
        f"{worker.last_havre_roster_published_count}"
    )
    return queued


def _maybe_send_weekly_agency_briefs() -> None:
    """Send weekly crime briefs on Mondays if not yet sent today."""
    if datetime.now(UTC).weekday() != 0:
        return
    try:
        from services.email.agency_brief import send_weekly_briefs
        result = send_weekly_briefs()
        logging.info(f"Weekly agency briefs: {result}")
    except Exception as e:
        logging.error(f"Weekly agency briefs failed: {e}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["queue", "inline"],
        default="queue",
        help="queue = discover and enqueue, inline = legacy direct processing",
    )
    args = parser.parse_args()

    _maybe_send_weekly_agency_briefs()

    if args.mode == "inline":
        process_inline_legacy()
        return

    enqueue_mode()


if __name__ == "__main__":
    main()
