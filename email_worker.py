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


# Setup logging
logging.basicConfig(
    filename=config.LOG_FILE,
    level=getattr(logging, config.LOG_LEVEL),
    format=config.LOG_FORMAT
)

class EmailWorker:
    """Handles fetching and processing blotter emails"""
    
    def __init__(self):
        self.email_user = config.EMAIL_USER
        self.email_pass = config.EMAIL_PASSWORD
        self.imap_server = config.IMAP_SERVER
        self.imap_port = config.IMAP_PORT
        self.upload_dir = config.UPLOAD_DIR or UPLOAD_DIR
        self.processed_folder = config.PROCESSED_FOLDER
        
        # Ensure upload directory exists
        os.makedirs(self.upload_dir, exist_ok=True)

    def _validate_imap_config(self) -> str | None:
        if not (self.email_user or '').strip():
            return 'MB_EMAIL_USER is not set'
        if not (self.email_pass or '').strip():
            return 'MB_EMAIL_PASSWORD is not set'
        if (self.email_pass or '').strip().lower() in {'replace-me', 'changeme', 'change-me'}:
            return 'MB_EMAIL_PASSWORD is still a placeholder value'
        if not (self.imap_server or '').strip():
            return 'MB_IMAP_SERVER is not set'
        return None

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
    
    def fetch_and_process_emails(self):
        """Main method - fetch emails and process PDFs"""
        config_error = self._validate_imap_config()
        if config_error:
            logging.error(f"Email worker config error: {config_error}")
            return 0

        try:
            # Connect to IONOS IMAP
            mail = imaplib.IMAP4_SSL(self.imap_server, self.imap_port)
            mail.login(self.email_user, self.email_pass)
            mail.select("INBOX")
            
            logging.info("Connected to IONOS IMAP successfully")
            
            # Search all unread emails
            status, messages = mail.search(None, 'UNSEEN')

            if status != 'OK' or not messages[0]:
                logging.info("No new emails found")
                mail.logout()
                return 0

            email_ids = messages[0].split()
            logging.info(f"Found {len(email_ids)} unread emails to scan")
            
            processed_count = 0
            
            for num in email_ids:
                try:
                    # Fetch the email
                    res, msg_data = mail.fetch(num, "(RFC822)")
                    
                    for response_part in msg_data:
                        if isinstance(response_part, tuple):
                            msg = email.message_from_bytes(response_part[1])
                            
                            # Extract subject for logging
                            subject = msg.get('subject', 'No Subject')
                            sender = msg.get('from', 'Unknown')
                            logging.info(f"Processing email: {subject} from {sender}")

                            # Skip bounce / delivery-failure emails
                            if 'mailer-daemon' in sender.lower() or 'delivery' in subject.lower():
                                logging.info(f"Skipping bounce/delivery email: {subject}")
                                continue

                            # Process attachments
                            message_id = msg.get('Message-ID', '')
                            msg_date = msg.get('Date', '')
                            had_pdf, pdf_succeeded = self._process_attachments(
                                msg,
                                source_message_id=message_id,
                                sender=sender,
                                subject=subject,
                                received_at=msg_date,
                            )

                            if had_pdf:
                                # Email had PDF(s) — mark processed regardless of parse errors
                                if pdf_succeeded:
                                    self._move_to_processed(mail, num)
                                    processed_count += 1
                                    logging.info("Successfully processed email with PDF(s)")
                                else:
                                    logging.error(f"PDF(s) found but all failed to process: {subject}")
                            else:
                                # No PDF — try plain-text body as blotter
                                body, body_method = self._extract_body_text(msg)
                                if body and len(body.strip()) > 200:
                                    if not self._looks_like_blotter_email(subject, sender, body):
                                        logging.info(f"Skipping non-blotter text email: {subject}")
                                        self._move_to_processed(mail, num)
                                        continue

                                    try:
                                        preview = parse_text_blotter(body)
                                    except Exception as e:
                                        logging.warning(f"Text-body preview parse failed for {subject}: {e}")
                                        preview = {'total_count': 0}

                                    if preview.get('total_count', 0) <= 0:
                                        logging.info(
                                            f"Skipping text email with no extractable incidents: {subject}"
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
                                            f"Skipping text email with weak blotter structure: {subject}"
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
                                        logging.info('Skipped already-published text source document')
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
                                        logging.info("Processed text-body blotter from email")
                                    except Exception as e:
                                        logging.error(f"Failed to process text blotter: {e}")
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
                                    logging.info(f"No blotter content found in email: {subject} — skipping")
                
                except Exception as e:
                    logging.error(f"Error processing email {num}: {str(e)}")
                    continue
            
            mail.expunge()
            mail.logout()
            
            logging.info(f"Email worker complete: {processed_count} emails processed")
            return processed_count
            
        except imaplib.IMAP4.error as e:
            logging.error(f"IMAP Error: {str(e)}")
            return 0
        except Exception as e:
            logging.error(f"Email worker critical error: {str(e)}")
            return 0

    def scan_mailbox_for_new_items(self) -> list[dict]:
        """
        First-pass queue migration path:
        - fetch unread email
        - save PDF attachments locally
        - return enqueue item payloads
        """
        config_error = self._validate_imap_config()
        if config_error:
            logging.error(f"Email worker config error: {config_error}")
            return []

        items: list[dict] = []

        try:
            mail = imaplib.IMAP4_SSL(self.imap_server, self.imap_port)
            mail.login(self.email_user, self.email_pass)
            mail.select("INBOX")

            status, messages = mail.search(None, "UNSEEN")
            if status != "OK" or not messages[0]:
                logging.info("No new emails found for queue scan")
                mail.logout()
                return items

            email_ids = messages[0].split()
            logging.info(f"Queue scan found {len(email_ids)} unread emails")

            for num in email_ids:
                try:
                    res, msg_data = mail.fetch(num, "(RFC822)")
                    if res != "OK":
                        logging.warning(f"Failed to fetch email id {num!r}")
                        continue

                    for response_part in msg_data:
                        if not isinstance(response_part, tuple):
                            continue

                        msg = email.message_from_bytes(response_part[1])
                        subject = msg.get("subject", "No Subject")
                        sender = msg.get("from", "Unknown")
                        message_id = msg.get("Message-ID", "") or ""

                        if "mailer-daemon" in sender.lower() or "delivery" in subject.lower():
                            logging.info(f"Skipping bounce/delivery email in queue scan: {subject}")
                            continue

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
                                f"Queue scan saved PDF attachment: message_id={source_key} path={filepath}"
                            )

                        if found_pdf:
                            self._move_to_processed(mail, num)
                            logging.info(f"Queue scan moved email to processed: {subject}")

                except Exception as e:
                    logging.error(f"Queue scan failed for email {num}: {e}")
                    continue

            mail.expunge()
            mail.logout()
            return items

        except imaplib.IMAP4.error as e:
            logging.error(f"IMAP Error during queue scan: {e}")
            return items
        except Exception as e:
            logging.error(f"Queue scan critical error: {e}")
            return items

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
        Extract and process PDF attachments from email.
        Returns (had_pdf, any_succeeded):
          had_pdf      — True if at least one .pdf attachment was found
          any_succeeded — True if at least one was successfully processed
        """
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
                existing_status = get_ingestion_job_status(source_document_id)
                if existing_status == 'published':
                    logging.info(f"Skipping already published attachment: {filename}")
                    any_succeeded = True
                    continue

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

    with redis_lock("lock:email_worker_scan", timeout=15 * 60) as acquired:
        if not acquired:
            print(f"{utcnow_iso()} email_worker scan skipped: lock already held")
            return 0

        items = scan_mailbox_for_new_items()
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
    return queued


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["queue", "inline"],
        default="queue",
        help="queue = discover and enqueue, inline = legacy direct processing",
    )
    args = parser.parse_args()

    if args.mode == "inline":
        process_inline_legacy()
        return

    enqueue_mode()


if __name__ == "__main__":
    main()
