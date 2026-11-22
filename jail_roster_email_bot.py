#!/usr/bin/env python3
"""Jail roster email support bot for MTBlotter.com.

Monitors support@ inbox for jail roster PDF attachments, validates, dedupes,
publishes via a configurable external Python script, and manages IMAP folders.
"""
from __future__ import annotations

import argparse
import email
import email.policy
import hashlib
import imaplib
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, UTC, timedelta
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path("/opt/montanablotter")
sys.path.insert(0, str(PROJECT_ROOT))

BASE_DIR = PROJECT_ROOT
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
TMP_DIR = Path(tempfile.gettempdir()) / "mtblotter_jail_roster"
CONFIG_FILE = BASE_DIR / "configs" / "jail_roster_departments.json"
DB_PATH = DATA_DIR / "jail_roster_processing.db"
LOG_FILE = LOG_DIR / "jail_roster_email_bot.log"

# Env-based credentials (never logged)
EMAIL_USER = os.getenv("JAIL_ROSTER_EMAIL_USER", os.getenv("MB_EMAIL_USER", ""))
EMAIL_PASSWORD = os.getenv("JAIL_ROSTER_EMAIL_PASSWORD", os.getenv("MB_EMAIL_PASSWORD", ""))
IMAP_SERVER = os.getenv("JAIL_ROSTER_IMAP_SERVER", os.getenv("MB_IMAP_SERVER", "imap.gmail.com"))
IMAP_PORT = int(os.getenv("JAIL_ROSTER_IMAP_PORT", os.getenv("MB_IMAP_PORT", "993")))
PUBLISH_SCRIPT = os.getenv("JAIL_ROSTER_PUBLISH_SCRIPT", "")
PROCESSED_FOLDER = os.getenv("JAIL_ROSTER_PROCESSED_FOLDER", "Processed")
REVIEW_FOLDER = os.getenv("JAIL_ROSTER_REVIEW_FOLDER", "Review")
FAILED_FOLDER = os.getenv("JAIL_ROSTER_FAILED_FOLDER", "Failed")
LOOKBACK_DAYS = int(os.getenv("JAIL_ROSTER_LOOKBACK_DAYS", "14"))
MAX_RETRIES = int(os.getenv("JAIL_ROSTER_MAX_RETRIES", "3"))
RETRY_DELAY = int(os.getenv("JAIL_ROSTER_RETRY_DELAY", "5"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger("jail_roster_bot")


def _load_config() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {"departments": []}


def _init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS processed (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message_id TEXT,
        sha256 TEXT,
        sender TEXT,
        county TEXT,
        filename TEXT,
        published_at TEXT,
        status TEXT,
        UNIQUE(message_id, sha256)
    )""")
    conn.commit()
    conn.close()


def _is_duplicate(message_id, sha256, sender, county, filename) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        "SELECT 1 FROM processed WHERE message_id=? OR (sha256=? AND sender=? AND county=? AND filename=?)",
        (message_id, sha256, sender, county, filename),
    )
    row = cur.fetchone()
    conn.close()
    return row is not None


def _record(message_id, sha256, sender, county, filename, status):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO processed (message_id, sha256, sender, county, filename, published_at, status) VALUES (?,?,?,?,?,?,?)",
        (message_id, sha256, sender, county, filename, datetime.now(UTC).isoformat(), status),
    )
    conn.commit()
    conn.close()


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _detect_county(text: str, cfg: dict) -> Optional[str]:
    lower = text.lower()
    for dept in cfg.get("departments", []):
        for kw in dept.get("keywords", []):
            if kw.lower() in lower:
                return dept.get("county")
    # fallback generic county map
    generic = {
        "hill county": "Hill", "cascade county": "Cascade", "missoula county": "Missoula",
        "gallatin county": "Gallatin", "yellowstone county": "Yellowstone",
        "flathead county": "Flathead",
    }
    for k, v in generic.items():
        if k in lower:
            return v
    return None


def _detect_department(text: str, cfg: dict) -> Optional[dict]:
    lower = text.lower()
    for dept in cfg.get("departments", []):
        for addr in dept.get("sender_addresses", []):
            if addr.lower() in lower:
                return dept
        for kw in dept.get("keywords", []):
            if kw.lower() in lower:
                return dept
    return None


def _valid_pdf(data: bytes) -> bool:
    if not data or len(data) < 8:
        return False
    if not data.startswith(b"%PDF"):
        return False
    # basic sanity: contains eof marker
    if b"%%EOF" not in data[-1024:]:
        # some pdfs omit; accept if header ok and size reasonable
        pass
    return True


def _ensure_folders(mail):
    for f in (PROCESSED_FOLDER, REVIEW_FOLDER, FAILED_FOLDER):
        try:
            mail.create(f)
        except Exception:
            pass


def _move(mail, num, folder):
    try:
        mail.copy(num, folder)
        mail.store(num, "+FLAGS", "\\Deleted")
        return True
    except Exception as e:
        log.error("Failed to move %s to %s: %s", num, folder, e)
        return False


def _call_publisher(payload: dict) -> dict:
    if not PUBLISH_SCRIPT:
        return {"success": False, "error": "PUBLISH_SCRIPT not configured"}
    if not Path(PUBLISH_SCRIPT).exists():
        return {"success": False, "error": f"publish script not found: {PUBLISH_SCRIPT}"}
    # write payload to temp json
    fd, path = tempfile.mkstemp(prefix="jr_payload_", suffix=".json", dir=str(TMP_DIR))
    os.close(fd)
    Path(path).write_text(json.dumps(payload))
    try:
        proc = subprocess.run(
            [sys.executable, PUBLISH_SCRIPT, path],
            capture_output=True, text=True, timeout=300,
        )
        out = proc.stdout.strip()
        try:
            result = json.loads(out)
        except Exception:
            result = {"success": proc.returncode == 0, "stdout": out, "stderr": proc.stderr.strip()}
        if proc.returncode != 0 and "success" not in result:
            result["success"] = False
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:
            pass


def process_once() -> dict:
    results = {"processed": 0, "review": 0, "failed": 0, "skipped": 0, "details": []}
    if not EMAIL_USER or not EMAIL_PASSWORD:
        log.error("Email credentials not configured.")
        return results

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    _init_db()
    cfg = _load_config()

    mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
    mail.login(EMAIL_USER, EMAIL_PASSWORD)
    mail.select("INBOX")
    _ensure_folders(mail)

    since = (datetime.now() - timedelta(days=LOOKBACK_DAYS)).strftime("%d-%b-%Y")
    status, messages = mail.search(None, f'SINCE "{since}"')
    if status != "OK" or not messages or not messages[0]:
        log.info("No emails since %s", since)
        mail.close(); mail.logout()
        return results

    nums = messages[0].split()
    log.info("Found %d email(s)", len(nums))

    for num in nums:
        try:
            status, data = mail.fetch(num, "(RFC822)")
            if status != "OK" or not data or not data[0]:
                continue
            raw = data[0][1] if isinstance(data[0], tuple) else data[0]
            msg = email.message_from_bytes(raw, policy=email.policy.default)
            subject = str(msg.get("Subject", ""))
            sender = str(msg.get("From", ""))
            date_str = str(msg.get("Date", ""))
            msg_id = msg.get("Message-ID", "").strip() or hashlib.sha256(f"{subject}|{date_str}|{sender}".encode()).hexdigest()[:32]

            # collect pdf attachments
            pdfs = []
            for part in msg.walk():
                if part.get_content_maintype() == "multipart":
                    continue
                cd = part.get("Content-Disposition", "")
                if "attachment" not in cd.lower():
                    continue
                filename = part.get_filename()
                if not filename:
                    continue
                if not filename.lower().endswith(".pdf"):
                    continue
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                pdfs.append((filename, payload))

            if not pdfs:
                results["skipped"] += 1
                continue

            # determine department/county
            body_text = ""
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    try:
                        body_text += part.get_content()
                    except Exception:
                        pass
            detection_text = f"{subject} {sender} {body_text}"
            for pfn, _ in pdfs:
                detection_text += " " + pfn

            dept = _detect_department(detection_text, cfg)
            county = _detect_county(detection_text, cfg)
            if dept:
                county = dept.get("county", county)
                agency = dept.get("agency")
            else:
                agency = None

            if len(pdfs) > 1:
                # try pick one with 'roster' in name
                roster_pdfs = [p for p in pdfs if "roster" in p[0].lower()]
                if len(roster_pdfs) == 1:
                    pdfs = roster_pdfs
                else:
                    log.warning("Multiple PDFs and cannot confidently pick; flagging for review")
                    _move(mail, num, REVIEW_FOLDER)
                    results["review"] += 1
                    continue

            filename, pdf_bytes = pdfs[0]

            if not _valid_pdf(pdf_bytes):
                log.warning("Invalid PDF in %s", filename)
                _move(mail, num, FAILED_FOLDER)
                results["failed"] += 1
                continue

            if not county:
                log.warning("County not identified; flagging for review")
                _move(mail, num, REVIEW_FOLDER)
                results["review"] += 1
                continue

            sha = _sha256_bytes(pdf_bytes)
            if _is_duplicate(msg_id, sha, sender, county, filename):
                log.info("Duplicate detected, skipping %s", filename)
                _move(mail, num, PROCESSED_FOLDER)
                results["skipped"] += 1
                continue

            # write temp pdf with restricted perms
            TMP_DIR.mkdir(parents=True, exist_ok=True)
            safe_name = Path(filename).name
            tmp_path = TMP_DIR / f"{msg_id.strip('<>')[:40]}_{safe_name}"
            tmp_path.write_bytes(pdf_bytes)
            try:
                os.chmod(tmp_path, 0o600)
            except Exception:
                pass

            payload = {
                "pdf_path": str(tmp_path),
                "original_filename": filename,
                "jail_department": (dept.get("name") if dept else None) or agency,
                "county": county,
                "sender_email": sender,
                "email_subject": subject,
                "email_received_date": date_str,
                "message_id": msg_id,
            }

            # retry loop
            pub_result = None
            for attempt in range(1, MAX_RETRIES + 1):
                pub_result = _call_publisher(payload)
                if pub_result.get("success"):
                    break
                log.warning("Publish attempt %d failed: %s", attempt, pub_result)
                time.sleep(RETRY_DELAY)

            if pub_result and pub_result.get("success"):
                _record(msg_id, sha, sender, county, filename, "published")
                _move(mail, num, PROCESSED_FOLDER)
                results["processed"] += 1
                log.info("Published %s (%s)", filename, county)
                # cleanup temp
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass
            else:
                _record(msg_id, sha, sender, county, filename, "failed")
                _move(mail, num, FAILED_FOLDER)
                results["failed"] += 1
                log.error("Publishing failed for %s: %s", filename, pub_result)

            results["details"].append({"message_id": msg_id, "county": county, "filename": filename, "result": pub_result})

        except Exception as e:
            log.exception("Error processing email: %s", e)
            try:
                _move(mail, num, FAILED_FOLDER)
            except Exception:
                pass
            results["failed"] += 1

    mail.close()
    mail.logout()
    # cleanup empty temp dir files older than 7 days
    try:
        for p in TMP_DIR.glob("*"):
            if p.is_file() and time.time() - p.stat().st_mtime > 7 * 86400:
                p.unlink(missing_ok=True)
    except Exception:
        pass
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run a single pass")
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=300)
    args = parser.parse_args()
    _init_db()
    if args.loop:
        while True:
            try:
                process_once()
            except Exception as e:
                log.exception("Loop error: %s", e)
            time.sleep(args.interval)
    else:
        r = process_once()
        print(json.dumps(r, indent=2))


if __name__ == "__main__":
    main()
