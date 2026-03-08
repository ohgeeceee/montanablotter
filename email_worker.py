"""
Email Worker - Fetches blotter PDFs from IONOS email and processes them
Unified version replacing email_worker.py and fetch_mail.py
"""

import imaplib
import email
import os
import logging
import smtplib
import re
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import config
from processor import process_new_blotter, process_text_blotter
from pdf_parser import parse_text_blotter
from pipeline_state import (
    ensure_ingestion_job,
    ensure_source_document,
    get_ingestion_job_status,
    increment_ingestion_retry,
    log_pipeline_event,
    set_ingestion_job_status,
    sha256_bytes,
    sha256_text,
)

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
        self.upload_dir = config.UPLOAD_DIR
        self.processed_folder = config.PROCESSED_FOLDER
        
        # Ensure upload directory exists
        os.makedirs(self.upload_dir, exist_ok=True)

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
        )
        if any(marker in text for marker in negative_markers):
            return False

        positive_markers = (
            'blotter',
            'media log',
            'daily activity',
            'daily log',
            'calls for service',
            'call log',
            'dispatch',
            'incident',
            'arrest',
            'cad',
            'press:',
            'police',
            'sheriff',
        )
        if any(marker in text for marker in positive_markers):
            return True

        sender_match = re.search(r'[\w.+-]+@([\w.-]+)', sender or '', re.I)
        if not sender_match:
            return False

        domain = sender_match.group(1).lower()
        return any(marker in domain for marker in ('mt.gov', 'county', 'sheriff', 'police', 'cityof', 'ci.'))
    
    def fetch_and_process_emails(self):
        """Main method - fetch emails and process PDFs"""
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

                                    set_ingestion_job_status(ingestion_job_id, 'extracted')
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
                                        set_ingestion_job_status(ingestion_job_id, 'failed', last_error=str(e), finished=True)
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
                set_ingestion_job_status(ingestion_job_id, 'extracted')
                log_pipeline_event(
                    ingestion_job_id,
                    'extract',
                    'ok',
                    {'filename': filename, 'storage_path': filepath},
                )

                try:
                    batch_id = process_new_blotter(
                        filepath,
                        source_document_id=source_document_id,
                        ingestion_job_id=ingestion_job_id,
                    )
                    if batch_id:
                        logging.info(f"Processed PDF: {filename} -> Batch #{batch_id}")
                    else:
                        logging.info(f"Processed PDF: {filename} -> duplicate-only, no new batch created")
                    any_succeeded = True
                except Exception as e:
                    logging.error(f"Failed to process PDF {filename}: {str(e)}")
                    increment_ingestion_retry(ingestion_job_id, str(e))
                    set_ingestion_job_status(ingestion_job_id, 'failed', last_error=str(e), finished=True)
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
                    import re as _re
                    html = _re.sub(r'<[^>]+>', ' ', raw_html)
                    html = _re.sub(r'\s+', ' ', html).strip()

        if plain is not None:
            return plain, 'email_plain'
        if html:
            return html, 'email_html'
        return "", 'none'
    
    def _move_to_processed(self, mail, email_num):
        """Move processed email to Processed folder"""
        try:
            # Try to create folder if it doesn't exist
            mail.create(self.processed_folder)
        except:
            pass  # Folder probably already exists
        
        try:
            # Copy to Processed folder
            mail.copy(email_num, self.processed_folder)
            # Mark for deletion from inbox
            mail.store(email_num, '+FLAGS', '\\Deleted')
        except Exception as e:
            logging.warning(f"Could not move email to Processed folder: {e}")
    
    def send_email(self, to_address, subject, body, html_body=None):
        """Send an email via SMTP"""
        try:
            # Create message
            msg = MIMEMultipart('alternative')
            msg['From'] = config.SMTP_USER
            msg['To'] = to_address
            msg['Subject'] = subject
            
            # Attach plain text version
            msg.attach(MIMEText(body, 'plain'))
            
            # Attach HTML version if provided
            if html_body:
                msg.attach(MIMEText(html_body, 'html'))
            
            # Connect to Gmail SMTP
            smtp = smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT)
            smtp.starttls()
            smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)

            # Send from Gmail, reply-to IONOS address
            msg['Reply-To'] = self.email_user
            smtp.sendmail(config.SMTP_USER, to_address, msg.as_string())
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


if __name__ == "__main__":
    run_worker()
