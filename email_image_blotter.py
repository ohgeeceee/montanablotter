#!/usr/bin/env python3
"""
email_image_blotter.py — Poll IMAP for police blotter emails with IMAGE attachments,
convert images to PDF, and ingest into the existing email_worker pipeline.

This extends the existing email_worker.py to handle image-based blotters
(sent by agencies that email screenshots instead of PDFs).
"""

from __future__ import annotations

import hashlib
import imaplib
import email
import email.policy
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

# Ensure project root is importable
PROJECT_ROOT = Path("/root/montanablotter")
sys.path.insert(0, str(PROJECT_ROOT))

import config
from db import get_db
from email_worker import EmailWorker
from processor import process_new_blotter
from pipeline_state import (
    ensure_ingestion_job,
    ensure_source_document,
    get_ingestion_job_status,
    increment_ingestion_retry,
    log_pipeline_event,
    set_ingestion_job_status,
    set_ingestion_job_status_legacy,
    sha256_bytes,
)

try:
    import img2pdf
    HAS_IMG2PDF = True
except ImportError:
    HAS_IMG2PDF = False

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# County/agency mapping from sender or subject
# Configure these to match your email sources
COUNTY_MAP: dict[str, str] = {
    "hill county": "Hill",
    "hill sheriff": "Hill",
    "hill county sheriff": "Hill",
    "havre": "Hill",
    "cascade county": "Cascade",
    "great falls": "Cascade",
    "missoula county": "Missoula",
    "missoula": "Missoula",
    "gallatin county": "Gallatin",
    "bozeman": "Gallatin",
    "yellowstone county": "Yellowstone",
    "billings": "Yellowstone",
    "flathead county": "Flathead",
    "kalispell": "Flathead",
}

AGENCY_MAP: dict[str, str] = {
    "hill county": "Hill County Sheriff's Office",
    "hill sheriff": "Hill County Sheriff's Office",
    "havre police": "Havre Police Department",
    "cascade county": "Cascade County Sheriff's Office",
    "great falls police": "Great Falls Police Department",
    "missoula county": "Missoula County Sheriff's Office",
    "missoula police": "Missoula Police Department",
    "gallatin county": "Gallatin County Sheriff's Office",
    "bozeman police": "Bozeman Police Department",
    "yellowstone county": "Yellowstone County Sheriff's Office",
    "billings police": "Billings Police Department",
    "flathead county": "Flathead County Sheriff's Office",
    "kalispell police": "Kalispell Police Department",
}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp"}


class ImageBlotterWorker(EmailWorker):
    """Extends EmailWorker to handle image attachments converted to PDF."""

    def _detect_county(self, text: str) -> Optional[str]:
        lower = text.lower()
        for key, county in COUNTY_MAP.items():
            if key in lower:
                return county
        return None

    def _detect_agency(self, text: str) -> Optional[str]:
        lower = text.lower()
        for key, agency in AGENCY_MAP.items():
            if key in lower:
                return agency
        return None

    def _image_to_pdf(self, image_paths: list[Path], output_path: Path) -> Path:
        """Convert image(s) to a single PDF."""
        if HAS_IMG2PDF:
            img2pdf.convert([str(p) for p in image_paths], output_path=str(output_path))
            return output_path

        if HAS_PIL:
            images = [Image.open(p).convert("RGB") for p in image_paths]
            if len(images) == 1:
                images[0].save(output_path, "PDF", resolution=150.0)
            else:
                first, *rest = images
                first.save(output_path, "PDF", resolution=150.0, save_all=True, append_images=rest)
            return output_path

        raise RuntimeError("No image-to-PDF library available. Install img2pdf or Pillow.")

    def _process_image_attachments(
        self,
        msg: email.message.EmailMessage,
        source_message_id: str,
        sender: str,
        subject: str,
        received_at: str,
    ) -> tuple[bool, bool]:
        """
        Extract image attachments, convert to PDF, and ingest.
        Returns (had_images, any_succeeded).
        """
        had_images = False
        any_succeeded = False

        image_parts = []
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            content_disposition = part.get("Content-Disposition", "")
            if "attachment" not in content_disposition.lower():
                continue
            filename = part.get_filename()
            if not filename:
                continue
            ext = Path(filename).suffix.lower()
            if ext in IMAGE_EXTENSIONS:
                image_parts.append((filename, part.get_payload(decode=True)))

        if not image_parts:
            return False, False

        had_images = True
        detection_text = f"{subject} {sender}"
        county = self._detect_county(detection_text)
        agency = self._detect_agency(detection_text)

        with tempfile.TemporaryDirectory(prefix="blotter_") as tmpdir:
            tmp_path = Path(tmpdir)
            image_paths: list[Path] = []
            for filename, payload in image_parts:
                img_path = tmp_path / filename
                with open(img_path, "wb") as f:
                    f.write(payload)
                image_paths.append(img_path)

            safe_sender = re.sub(r"[^\w\-]", "_", sender.split("<")[0].strip())[:30]
            safe_subject = re.sub(r"[^\w\-]", "_", subject)[:40]
            pdf_name = f"{safe_sender}_{safe_subject}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
            pdf_path = Path(self.upload_dir) / pdf_name
            os.makedirs(self.upload_dir, exist_ok=True)

            self._image_to_pdf(image_paths, pdf_path)
            print(f"[email_image_blotter] Created PDF: {pdf_path}")

            with open(pdf_path, "rb") as f:
                file_hash = sha256_bytes(f.read())

            source_document_id = ensure_source_document(
                source_type="imap_image_blotter",
                source_message_id=source_message_id,
                source_sender=sender,
                source_subject=subject,
                source_received_at=received_at,
                filename=pdf_name,
                content_sha256=file_hash,
                storage_path=str(pdf_path),
                raw_text=f"Subject: {subject}\nFrom: {sender}\nDate: {received_at}\nDetectedCounty: {county or 'Unknown'}\nDetectedAgency: {agency or 'Unknown'}",
                extraction_method="img2pdf" if HAS_IMG2PDF else "pillow",
                extraction_warnings=[],
            )
            ingestion_job_id = ensure_ingestion_job(str(source_document_id))
            existing_status = get_ingestion_job_status(str(source_document_id))
            if existing_status == "published":
                print(f"[email_image_blotter] Already published source_document #{source_document_id}. Skipping.")
                any_succeeded = True
                return had_images, any_succeeded

            conn = get_db()
            existing = conn.execute(
                "SELECT id FROM blotters WHERE source_document_id = ?",
                (source_document_id,),
            ).fetchone()
            conn.close()

            if existing:
                print(f"[email_image_blotter] Duplicate blotter already exists (blotter #{existing[0]}). Skipping.")
                any_succeeded = True
                return had_images, any_succeeded

            set_ingestion_job_status_legacy(ingestion_job_id, "extracted")
            log_pipeline_event(
                ingestion_job_id,
                "extract",
                "ok",
                {"filename": pdf_name, "storage_path": str(pdf_path)},
            )

            try:
                batch_id = process_new_blotter(
                    str(pdf_path),
                    county=county,
                    source_document_id=source_document_id,
                    ingestion_job_id=ingestion_job_id,
                )
                if batch_id:
                    print(f"[email_image_blotter] Ingested blotter #{batch_id} for {county or 'Unknown'} from {sender}")
                else:
                    print(f"[email_image_blotter] All incidents duplicate for {county or 'Unknown'} from {sender} — no new batch")
                any_succeeded = True
            except Exception as e:
                print(f"[email_image_blotter] Failed to process PDF: {e}")
                increment_ingestion_retry(ingestion_job_id, str(e))
                set_ingestion_job_status_legacy(
                    ingestion_job_id, "failed", last_error=str(e), finished=True
                )
                log_pipeline_event(
                    ingestion_job_id,
                    "publish",
                    "error",
                    {"filename": pdf_name, "error": str(e)},
                )

        return had_images, any_succeeded

    def fetch_and_process_emails(self):
        """Override to handle both PDF and image attachments."""
        config_error = self._validate_imap_config()
        if config_error:
            print(f"[email_image_blotter] Config error: {config_error}")
            return 0

        try:
            mail = imaplib.IMAP4_SSL(self.imap_server, self.imap_port)
            mail.login(self.email_user, self.email_pass)
            mail.select("INBOX")
            print("[email_image_blotter] Connected to IMAP successfully")

            status, messages = mail.search(None, "UNSEEN")
            if status != "OK" or not messages[0]:
                print("[email_image_blotter] No new emails found")
                mail.logout()
                return 0

            email_ids = messages[0].split()
            print(f"[email_image_blotter] Found {len(email_ids)} unread emails")

            processed_count = 0

            for num in email_ids:
                try:
                    res, msg_data = mail.fetch(num, "(RFC822)")
                    for response_part in msg_data:
                        if isinstance(response_part, tuple):
                            msg = email.message_from_bytes(response_part[1], policy=email.policy.default)
                            subject = msg.get("subject", "No Subject")
                            sender = msg.get("from", "Unknown")
                            message_id = msg.get("Message-ID", "")
                            msg_date = msg.get("Date", "")

                            print(f"[email_image_blotter] Processing: {subject} from {sender}")

                            if "mailer-daemon" in sender.lower() or "delivery" in subject.lower():
                                print(f"[email_image_blotter] Skipping bounce email: {subject}")
                                continue

                            # First try PDF attachments (existing behavior)
                            had_pdf, pdf_succeeded = self._process_attachments(
                                msg, message_id, sender, subject, msg_date
                            )

                            if had_pdf:
                                if pdf_succeeded:
                                    self._move_to_processed(mail, num)
                                    processed_count += 1
                                    print(f"[email_image_blotter] Processed PDF email: {subject}")
                                else:
                                    print(f"[email_image_blotter] PDF failed: {subject}")
                                continue

                            # No PDF — try image attachments
                            had_images, images_succeeded = self._process_image_attachments(
                                msg, message_id, sender, subject, msg_date
                            )

                            if had_images:
                                if images_succeeded:
                                    self._move_to_processed(mail, num)
                                    processed_count += 1
                                    print(f"[email_image_blotter] Processed image email: {subject}")
                                else:
                                    print(f"[email_image_blotter] Image processing failed: {subject}")
                                continue

                            # No PDF or images — try plain-text body
                            body, body_method = self._extract_body_text(msg)
                            if body and len(body.strip()) > 200:
                                if not self._looks_like_blotter_email(subject, sender, body):
                                    print(f"[email_image_blotter] Skipping non-blotter: {subject}")
                                    self._move_to_processed(mail, num)
                                    continue

                                from pdf_parser import parse_text_blotter
                                try:
                                    preview = parse_text_blotter(body)
                                except Exception as e:
                                    print(f"[email_image_blotter] Text preview failed: {e}")
                                    preview = {"total_count": 0}

                                if preview.get("total_count", 0) <= 0:
                                    print(f"[email_image_blotter] No incidents in text: {subject}")
                                    self._move_to_processed(mail, num)
                                    continue

                                if not self._preview_is_plausible_text_blotter(
                                    preview, subject=subject, sender=sender, body=body
                                ):
                                    print(f"[email_image_blotter] Weak blotter structure: {subject}")
                                    self._move_to_processed(mail, num)
                                    continue

                                from pipeline_state import sha256_text, ensure_ingestion_job, get_ingestion_job_status
                                body_hash = sha256_text(body)
                                source_document_id = ensure_source_document(
                                    source_type="imap_text",
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
                                ingestion_job_id = ensure_ingestion_job(str(source_document_id))
                                existing_status = get_ingestion_job_status(str(source_document_id))
                                if existing_status == "published":
                                    self._move_to_processed(mail, num)
                                    processed_count += 1
                                    continue

                                set_ingestion_job_status_legacy(ingestion_job_id, "extracted")
                                try:
                                    from processor import process_text_blotter
                                    process_text_blotter(
                                        body,
                                        sender_email=sender,
                                        source_document_id=source_document_id,
                                        ingestion_job_id=ingestion_job_id,
                                    )
                                    self._move_to_processed(mail, num)
                                    processed_count += 1
                                except Exception as e:
                                    print(f"[email_image_blotter] Text blotter failed: {e}")
                                    increment_ingestion_retry(ingestion_job_id, str(e))
                                    set_ingestion_job_status_legacy(
                                        ingestion_job_id, "failed", last_error=str(e), finished=True
                                    )
                            else:
                                print(f"[email_image_blotter] No content found: {subject}")

                except Exception as e:
                    print(f"[email_image_blotter] Error processing email {num}: {e}")
                    continue

            mail.expunge()
            mail.logout()
            print(f"[email_image_blotter] Complete: {processed_count} emails processed")
            return processed_count

        except Exception as e:
            print(f"[email_image_blotter] Critical error: {e}")
            return 0


def run_image_blotter():
    worker = ImageBlotterWorker()
    count = worker.fetch_and_process_emails()
    print(f"Processed {count} emails")
    return count


if __name__ == "__main__":
    run_image_blotter()
