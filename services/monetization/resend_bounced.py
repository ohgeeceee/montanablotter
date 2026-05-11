"""
Resend Bounced Emails
---------------------
Scans the IONOS inbox for bounce/delivery-failure emails, extracts the
original failed recipient address, and resends the blotter request email
via Gmail SMTP. Processed bounce emails are moved to the Processed folder.
"""

import imaplib
import email
import re
import smtplib
import sqlite3
import logging
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import config

logging.basicConfig(
    filename=config.LOG_FILE,
    level=getattr(logging, config.LOG_LEVEL),
    format=config.LOG_FORMAT
)
log = logging.getLogger('resend_bounced')

# ── Email template to resend ────────────────────────────────────────────────

SUBJECT = 'Request for Law Enforcement Blotter Records - Montana Blotter Project'

BODY = """\
Dear Sheriff / Records Department,

We are writing to request law enforcement blotter records from your agency \
as part of the Montana Blotter project, a public information initiative to \
make law enforcement activity more transparent and accessible to Montana citizens.

The Montana Blotter aggregates public blotter information from sheriffs' \
offices and police departments across the state, allowing citizens to search \
and view recent law enforcement incidents in their area.

We would greatly appreciate it if your office could provide regular blotter \
updates (weekly or daily) via email to records@montanablotter.com in any format:
  - PDF documents with incident listings
  - CSV/Excel spreadsheets with structured data
  - Any other standard format your office uses

All information shared will be made publicly available and properly attributed \
to your department.

Thank you for your time and consideration. Please contact us if you have any \
questions about this initiative.

Best regards,
Montana Blotter Project
records@montanablotter.com
https://montanablotter.com
"""

# ── Bounce patterns ──────────────────────────────────────────────────────────

BOUNCE_SUBJECTS = [
    'mail delivery failed',
    'delivery failure',
    'undeliverable',
    'undelivered',
    'returned to sender',
    'delivery status notification',
    'failure notice',
]

# Regex to pull an email address out of the bounce body (e.g. "* user@domain.com")
RECIPIENT_RE = re.compile(r'[*>]?\s*([\w.+\-]+@[\w.\-]+\.[a-zA-Z]{2,})', re.MULTILINE)


def is_bounce(subject: str, sender: str) -> bool:
    subj = subject.lower()
    if any(p in subj for p in BOUNCE_SUBJECTS):
        return True
    if 'mailer-daemon' in sender.lower() or 'mail delivery' in sender.lower():
        return True
    return False


def extract_failed_recipient(msg) -> str | None:
    """Pull the failed-to address from a bounce email."""
    # 1. Check X-Failed-Recipients header
    hdr = msg.get('X-Failed-Recipients', '').strip()
    if hdr and '@' in hdr:
        return hdr.split(',')[0].strip()

    # 2. Scan text/plain and message/delivery-status parts
    for part in msg.walk():
        ct = part.get_content_type()
        if ct in ('text/plain', 'message/delivery-status', 'text/rfc822-headers'):
            payload = part.get_payload(decode=True)
            if not payload:
                # delivery-status parts may not be bytes-encoded
                payload_str = part.get_payload()
                if isinstance(payload_str, str):
                    payload = payload_str.encode()
                else:
                    continue
            text = payload.decode('utf-8', errors='replace')
            # Skip if it looks like our own outgoing message body
            if 'Montana Blotter Project' in text and 'Dear Sheriff' in text:
                continue
            matches = RECIPIENT_RE.findall(text)
            for m in matches:
                # Ignore our own addresses
                if m in (config.EMAIL_USER, config.SMTP_USER):
                    continue
                return m

    return None


def send_via_gmail(to_address: str) -> bool:
    """Send the blotter request template via Gmail SMTP."""
    try:
        msg = MIMEMultipart('alternative')
        msg['From'] = config.SMTP_USER
        msg['To'] = to_address
        msg['Subject'] = SUBJECT
        msg['Reply-To'] = config.EMAIL_USER
        msg.attach(MIMEText(BODY, 'plain'))

        smtp = smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT, timeout=15)
        smtp.starttls()
        smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
        smtp.sendmail(config.SMTP_USER, to_address, msg.as_string())
        smtp.quit()
        log.info(f'Resent via Gmail to {to_address}')
        return True
    except Exception as e:
        log.error(f'Gmail send failed to {to_address}: {e}')
        return False


def log_resend(to_address: str, agency_name: str):
    """Update the emailed_agencies table to reflect the Gmail resend."""
    conn = sqlite3.connect(config.DB_PATH)
    # Remove the old failed entry (sent via IONOS) so UI shows fresh status
    conn.execute(
        'DELETE FROM emailed_agencies WHERE email_address = ?', (to_address,)
    )
    # Insert new entry for the Gmail resend
    conn.execute(
        'INSERT INTO emailed_agencies (agency_name, email_address, subject) VALUES (?, ?, ?)',
        (agency_name or to_address, to_address, SUBJECT)
    )
    conn.commit()
    conn.close()


def move_to_processed(mail, email_num):
    try:
        mail.create(config.PROCESSED_FOLDER)
    except Exception:
        pass
    try:
        mail.copy(email_num, config.PROCESSED_FOLDER)
        mail.store(email_num, '+FLAGS', '\\Deleted')
    except Exception as e:
        log.warning(f'Could not move bounce email to Processed: {e}')


def run():
    log.info('=== resend_bounced.py starting ===')
    print('Connecting to IONOS inbox...')

    try:
        mail = imaplib.IMAP4_SSL(config.IMAP_SERVER, config.IMAP_PORT)
        mail.login(config.EMAIL_USER, config.EMAIL_PASSWORD)
        mail.select('INBOX')
    except Exception as e:
        print(f'IMAP connection failed: {e}')
        log.error(f'IMAP connection failed: {e}')
        return

    status, msgs = mail.search(None, 'ALL')
    all_ids = msgs[0].split() if msgs[0] else []
    print(f'Found {len(all_ids)} emails in inbox.')

    resent = 0
    skipped = 0

    for num in all_ids:
        try:
            res, data = mail.fetch(num, '(RFC822)')
            msg = email.message_from_bytes(data[0][1])

            subject = msg.get('subject', '')
            sender  = msg.get('from', '')

            if not is_bounce(subject, sender):
                continue

            failed_addr = extract_failed_recipient(msg)
            if not failed_addr:
                log.warning(f'Could not extract recipient from bounce: {subject}')
                print(f'  SKIP (no address found): {subject}')
                skipped += 1
                continue

            print(f'  Bounce detected → failed address: {failed_addr}')

            # Try to find the agency name from the DB
            conn = sqlite3.connect(config.DB_PATH)
            row = conn.execute(
                'SELECT agency_name FROM emailed_agencies WHERE email_address = ?',
                (failed_addr,)
            ).fetchone()
            conn.close()
            agency_name = row[0] if row else failed_addr

            ok = send_via_gmail(failed_addr)
            if ok:
                log_resend(failed_addr, agency_name)
                move_to_processed(mail, num)
                resent += 1
                print(f'  Resent to {failed_addr} ({agency_name})')
                time.sleep(2)  # brief pause to avoid Gmail rate limiting
            else:
                skipped += 1
                print(f'  FAILED to resend to {failed_addr}')

        except imaplib.IMAP4.abort:
            # IMAP connection dropped — reconnect and continue
            log.warning('IMAP connection dropped, reconnecting...')
            print('  IMAP connection dropped, reconnecting...')
            try:
                mail = imaplib.IMAP4_SSL(config.IMAP_SERVER, config.IMAP_PORT)
                mail.login(config.EMAIL_USER, config.EMAIL_PASSWORD)
                mail.select('INBOX')
            except Exception as e:
                log.error(f'Reconnect failed: {e}')
                break
            continue
        except Exception as e:
            log.error(f'Error processing email {num}: {e}')
            print(f'  ERROR: {e}')
            continue

    try:
        mail.expunge()
        mail.logout()
    except Exception:
        pass  # Connection may already be closed

    summary = f'Done. Resent: {resent} | Skipped/failed: {skipped}'
    print(summary)
    log.info(summary)
    return resent, skipped


if __name__ == '__main__':
    run()
