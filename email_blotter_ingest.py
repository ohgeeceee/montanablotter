#!/usr/bin/env python3
"""
email_blotter_ingest.py — Poll Gmail/IMAP for police blotter image attachments,
convert images to PDF, dedupe against existing blotters, and ingest into montanablotter.com.

Designed to run as a cron job on the montanablotter VPS.
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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Ensure project root is importable
PROJECT_ROOT = Path("/root/montanablotter")
sys.path.insert(0, str(PROJECT_ROOT))

import config
from db import get_db
from services.blotter.processor import process_new_blotter
from core.pipeline_state import (
    ensure_ingestion_job,
    ensure_source_document,
    increment_ingestion_retry,
    log_pipeline_event,
    set_ingestion_job_status,
    sha256_bytes,
)

# Try to import img2pdf for image->PDF conversion; fallback to reportlab
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

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

EMAIL_USER = getattr(config, "EMAIL_USER", os.getenv("MB_EMAIL_USER", ""))
EMAIL_PASSWORD = getattr(config, "EMAIL_PASSWORD", os.getenv("MB_EMAIL_PASSWORD", ""))
IMAP_SERVER = getattr(config, "IMAP_SERVER", os.getenv("MB_IMAP_SERVER", "imap.gmail.com"))
IMAP_PORT = getattr(config, "IMAP_PORT", int(os.getenv("MB_IMAP_PORT", "993")))
UPLOAD_DIR = Path(getattr(config, "UPLOAD_DIR", "/root/montanablotter/uploads"))
PROCESSED_FOLDER = getattr(config, "PROCESSED_FOLDER", "Processed")
BLOTTER_SUBJECT_KEYWORD = getattr(config, "BLOTTER_SUBJECT_KEYWORD", "blotter")
LOOKBACK_DAYS = int(os.getenv("MB_INGEST_LOOKBACK_DAYS", "14"))
MAX_RETRIES = 5

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _detect_county(text: str) -> Optional[str]:
    lower = text.lower()
    for key, county in COUNTY_MAP.items():
        if key in lower:
            return county
    return None


def _detect_agency(text: str) -> Optional[str]:
    lower = text.lower()
    for key, agency in AGENCY_MAP.items():
        if key in lower:
            return agency
    return None


def _image_to_pdf(image_paths: list[Path], output_path: Path) -> Path:
    """Convert image(s) to a single PDF."""
    if HAS_IMG2PDF:
        # img2pdf accepts file paths directly
        img2pdf.convert([str(p) for p in image_paths], output_path=str(output_path))
        return output_path

    if HAS_PIL:
        # Fallback: use PIL to save as PDF
        images = [Image.open(p).convert("RGB") for p in image_paths]
        if len(images) == 1:
            images[0].save(output_path, "PDF", resolution=150.0)
        else:
            first, *rest = images
            first.save(output_path, "PDF", resolution=150.0, save_all=True, append_images=rest)
        return output_path

    raise RuntimeError("No image-to-PDF library available. Install img2pdf or Pillow.")


def _get_message_id(msg: email.message.EmailMessage) -> str:
    """Extract a stable message identifier."""
    msg_id = msg.get("Message-ID", "")
    if msg_id:
        return msg_id.strip()
    # Fallback: hash of subject + date + from
    subject = msg.get("Subject", "")
    date = msg.get("Date", "")
    from_ = msg.get("From", "")
    return hashlib.sha256(f"{subject}|{date}|{from_}".encode()).hexdigest()[:32]


def _move_to_folder(mail: imaplib.IMAP4_SSL, num: bytes, folder_name: str) -> None:
    """Move an email to the named IMAP folder, creating it if needed."""
    try:
        mail.create(folder_name)
    except Exception:
        pass  # Already exists — IMAP CREATE is not idempotent, ignore error
    try:
        mail.copy(num, folder_name)
        mail.store(num, "+FLAGS", "\\Deleted")
    except Exception as e:
        print(f"[email_blotter_ingest] Failed to move email to {folder_name}/: {e}")


def _ensure_processed_folder(mail: imaplib.IMAP4_SSL) -> None:
    """Create the Processed folder if it doesn't exist."""
    status, folders = mail.list()
    if status != "OK":
        return
    folder_names = []
    for folder in folders:
        if folder:
            # Parse IMAP folder line: b'(\HasNoChildren) "/" "Processed"'
            parts = folder.decode().split(' "')
            if len(parts) >= 2:
                folder_names.append(parts[-1].strip('"'))
    if PROCESSED_FOLDER not in folder_names:
        mail.create(PROCESSED_FOLDER)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def fetch_and_process() -> dict:
    """Poll IMAP for unread blotter emails, convert images to PDF, ingest."""
    results = {
        "processed": 0,
        "skipped": 0,
        "errors": 0,
        "details": [],
    }

    if not EMAIL_USER or not EMAIL_PASSWORD:
        print("[email_blotter_ingest] Email credentials not configured. Exiting.")
        return results

    mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
    mail.login(EMAIL_USER, EMAIL_PASSWORD)
    mail.select("inbox")

    # Search by date range instead of UNSEEN+subject so emails previewed
    # in a mail client (marked read) and subject variations are not missed.
    # Dedup via SHA256 in ensure_source_document prevents reprocessing.
    since_date = (datetime.now() - timedelta(days=LOOKBACK_DAYS)).strftime("%d-%b-%Y")
    status, messages = mail.search(None, f'SINCE "{since_date}"')
    if status != "OK" or not messages[0]:
        print(f"[email_blotter_ingest] No emails found since {since_date}.")
        mail.close()
        mail.logout()
        return results

    msg_nums = messages[0].split()
    print(f"[email_blotter_ingest] Found {len(msg_nums)} email(s) since {since_date}.")

    _ensure_processed_folder(mail)

    for num in msg_nums:
        source_doc_id = None
        try:
            status, data = mail.fetch(num, "(RFC822)")
            if status != "OK" or not data or not data[0]:
                continue

            raw_email = data[0][1]
            msg = email.message_from_bytes(raw_email, policy=email.policy.default)

            subject = msg.get("Subject", "")
            sender = msg.get("From", "")
            date_str = msg.get("Date", "")
            msg_id = _get_message_id(msg)

            # Detect county/agency from subject + sender
            detection_text = f"{subject} {sender}"
            county = _detect_county(detection_text)
            agency = _detect_agency(detection_text)

            # Collect image attachments
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
                print(f"[email_blotter_ingest] No image attachments in email from {sender} — marking as read.")
                mail.store(num, "+FLAGS", "\\Seen")
                mail.copy(num, PROCESSED_FOLDER)
                mail.store(num, "+FLAGS", "\\Deleted")
                results["skipped"] += 1
                continue

            # Create a working temp directory
            with tempfile.TemporaryDirectory(prefix="blotter_") as tmpdir:
                tmp_path = Path(tmpdir)
                image_paths: list[Path] = []
                for filename, payload in image_parts:
                    img_path = tmp_path / filename
                    with open(img_path, "wb") as f:
                        f.write(payload)
                    image_paths.append(img_path)

                # Generate PDF filename
                safe_sender = re.sub(r"[^\w\-]", "_", sender.split("<")[0].strip())[:30]
                safe_subject = re.sub(r"[^\w\-]", "_", subject)[:40]
                pdf_name = f"{safe_sender}_{safe_subject}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
                pdf_path = UPLOAD_DIR / pdf_name
                UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

                # Convert images to PDF
                _image_to_pdf(image_paths, pdf_path)
                print(f"[email_blotter_ingest] Created PDF: {pdf_path}")

                # Compute hash and check for duplicate source document
                with open(pdf_path, "rb") as f:
                    file_hash = sha256_bytes(f.read())

                # Check if this email was already processed via source_documents
                source_doc_id = ensure_source_document(
                    source_type="imap_image_blotter",
                    filename=pdf_name,
                    content_sha256=file_hash,
                    storage_path=str(pdf_path),
                    raw_text=f"Subject: {subject}\nFrom: {sender}\nDate: {date_str}",
                    extraction_method="img2pdf" if HAS_IMG2PDF else "pillow",
                    extraction_warnings=[],
                )

                # Check if blotter already exists for this source document
                conn = get_db()
                existing = conn.execute(
                    "SELECT id FROM blotters WHERE source_document_id = ?",
                    (source_doc_id,),
                ).fetchone()
                conn.close()

                if existing:
                    print(f"[email_blotter_ingest] Duplicate blotter already exists (blotter #{existing[0]}). Skipping.")
                    mail.store(num, "+FLAGS", "\\Seen")
                    mail.copy(num, PROCESSED_FOLDER)
                    mail.store(num, "+FLAGS", "\\Deleted")
                    results["skipped"] += 1
                    continue

                # Ingest the PDF into the blotter pipeline
                blotter_id = process_new_blotter(
                    str(pdf_path),
                    county=county,
                    source_document_id=source_doc_id,
                )
                print(f"[email_blotter_ingest] Ingested blotter #{blotter_id} for {county or 'Unknown'} from {sender}")

                # Mark email as read and move to Processed
                mail.store(num, "+FLAGS", "\\Seen")
                mail.copy(num, PROCESSED_FOLDER)
                mail.store(num, "+FLAGS", "\\Deleted")

                results["processed"] += 1
                results["details"].append({
                    "sender": sender,
                    "subject": subject,
                    "county": county,
                    "agency": agency,
                    "blotter_id": blotter_id,
                    "pdf_path": str(pdf_path),
                })

        except Exception as e:
            print(f"[email_blotter_ingest] Error processing email #{num}: {e}")
            results["errors"] += 1
            if source_doc_id is not None:
                job_id = ensure_ingestion_job(source_doc_id)
                retry_count = increment_ingestion_retry(job_id, str(e))
                if retry_count >= MAX_RETRIES:
                    print(f"[email_blotter_ingest] Max retries reached, moving to Failed/: {e}")
                    _move_to_folder(mail, num, "Failed")

    mail.expunge()
    mail.close()
    mail.logout()

    print(f"[email_blotter_ingest] Done: {results['processed']} processed, {results['skipped']} skipped, {results['errors']} errors.")
    return results


if __name__ == "__main__":
    results = fetch_and_process()
    if results["errors"] > 0:
        print(f"[email_blotter_ingest] {results['errors']} email(s) failed — job_runner will alert on repeated failures.")
        sys.exit(1)
