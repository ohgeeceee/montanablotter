#!/usr/bin/env python3
"""Outreach reply/bounce checker — closes the loop on sales cadences.

Scans montanablotter@gmail.com (INBOX + Processed; the PDF mail worker
sweeps non-PDF mail into Processed) for messages FROM outreach prospects:

  * auto-bounce -> status 'bounced'  (cadences stop following up)
  * real reply  -> status 'replied', row in outreach_replies, summary
                   email to the deal inbox

Cron-safe: readonly IMAP, no flag changes, exit 0 on transient errors.
"""
import email as email_lib
import imaplib
import json
import os
import re
import smtplib
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr, parsedate_to_datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

STATE_PATH = os.path.join(ROOT, 'data', 'outreach_reply_state.json')

AUTOBOUNCE_RE = re.compile(
    r'(?:undeliverable|delivery (?:status )?notification|mail delivery (?:failed|subsystem)|'
    r'delivery to the following recipient\(s\) failed|could not be delivered|'
    r'user unknown|mailbox unavailable|mailbox not found|no such user|'
    r'returned to sender|message .*rejected|5\.[012]\.\d)', re.I)

# Envelope/notice senders that must never count as a prospect reply.
BOUNCE_SENDERS = re.compile(r'^(mailer-daemon|postmaster|no-?reply|noreply)', re.I)


def _env_file():
    path = os.path.join(ROOT, '.env')
    vals = {}
    if os.path.exists(path):
        with open(path, encoding='utf-8') as fh:
            vals = dict(re.findall(r'^(\w+)=(.*)$', fh.read(), re.M))
    return {k: v.strip().strip('"').strip("'") for k, v in vals.items()}


def _load_state():
    try:
        with open(STATE_PATH, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {'seen_message_ids': [], 'last_run': ''}


def _save_state(state):
    state['seen_message_ids'] = state['seen_message_ids'][-5000:]
    state['last_run'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
    tmp = STATE_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(state, fh, indent=1)
    os.replace(tmp, STATE_PATH)


def prospect_directory(conn):
    """Map lowercased email -> (campaign, table, pk) for every mailed prospect.

    Also creates the outreach_replies ledger if missing (same non-destructive
    pattern the cadences use).
    """
    directory = {}
    conn.execute("""CREATE TABLE IF NOT EXISTS outreach_replies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        campaign TEXT NOT NULL,
        prospect_table TEXT NOT NULL,
        prospect_id INTEGER NOT NULL,
        from_addr TEXT NOT NULL,
        subject TEXT,
        snippet TEXT,
        is_bounce INTEGER NOT NULL DEFAULT 0,
        message_uid TEXT,
        received_at TEXT,
        logged_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_outreach_replies_uid
                    ON outreach_replies(message_uid) WHERE message_uid IS NOT NULL""")
    # Lawyers: address comes from the sent-emails ledger, not the prospect row.
    for row in conn.execute(
            "SELECT DISTINCT p.id, e.to_addr FROM lawyer_outreach_prospects p "
            "JOIN lawyer_outreach_emails e ON e.prospect_id = p.id "
            "WHERE e.status = 'sent' AND e.to_addr LIKE '%@%' "
            "AND p.status NOT IN ('bounced','replied','closed_won','closed_lost')"):
        directory[row[1].strip().lower()] = ('lawyer', 'lawyer_outreach_prospects', row[0])
    for row in conn.execute(
            "SELECT id, email FROM bail_agency_outreach "
            "WHERE email IS NOT NULL AND email != '' "
            "AND outreach_status NOT IN ('bounced','replied','closed_won','closed_lost')"):
        directory[row[1].strip().lower()] = ('bail', 'bail_agency_outreach', row[0])
    return directory


def _header(msg, name):
    return str(msg.get(name, '') or '')


def _body_preview(msg, limit=400):
    text = ''
    try:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == 'text/plain':
                    text = part.get_payload(decode=True).decode('utf-8', 'replace')
                    break
            if not text:
                for part in msg.walk():
                    if part.get_content_type() == 'text/html':
                        raw = part.get_payload(decode=True).decode('utf-8', 'replace')
                        text = re.sub(r'<[^>]+>', ' ', raw)
                        break
        else:
            text = (msg.get_payload(decode=True) or b'').decode('utf-8', 'replace')
    except Exception:
        text = ''
    return re.sub(r'\s+', ' ', text).strip()[:limit]


def _notify_deal(env, to_addr, campaign, who, addr, subject, preview):
    user = env.get('MB_SMTP_USER', '') or env.get('MB_GMAIL_IMAP_USER', '')
    password = env.get('MB_SMTP_PASSWORD', '') or env.get('MB_GMAIL_IMAP_PASSWORD', '')
    try:
        s = smtplib.SMTP(env.get('MB_SMTP_SERVER', 'smtp.gmail.com'),
                         int(env.get('MB_SMTP_PORT', '587')), timeout=30)
        s.starttls()
        s.login(user, password)
        body = ('Campaign: %s\nFrom: %s <%s>\nSubject: %s\n\n%s\n\n'
                '-- logged by services/outreach/reply_check.py' %
                (campaign, who, addr, subject, preview))
        s.sendmail(user, [to_addr], body)
        s.quit()
        print('deal notify sent to %s' % to_addr)
    except Exception as exc:
        print('deal notify failed: %s' % exc, file=sys.stderr)


def _write_db(conn, table, col, pk, uid, record):
    """Retry BEGIN IMMEDIATE on transient cron locks; raise if never free."""
    for attempt in range(5):
        try:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute("UPDATE %s SET %s=?, updated_at=datetime('now') WHERE id=?"
                         % (table, col), (record['status'], pk))
            conn.execute(
                "INSERT INTO outreach_replies (campaign, prospect_table, prospect_id,"
                " from_addr, subject, snippet, is_bounce, message_uid, received_at,"
                " logged_at) VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))",
                (record['campaign'], table, pk, record['from_addr'],
                 record['subject'][:300], record['snippet'][:800],
                 int(record['is_bounce']), uid, record['received_at']))
            conn.commit()
            return True
        except sqlite3.OperationalError:
            conn.rollback()
            time.sleep(2 + attempt * 3)
    return False


def run(dry=False):
    env = _env_file()
    user = env.get('MB_GMAIL_IMAP_USER', '')
    password = env.get('MB_GMAIL_IMAP_PASSWORD', '')
    server = env.get('MB_GMAIL_IMAP_SERVER', 'imap.gmail.com')
    deal_notify = env.get('MB_DEAL_NOTIFY_EMAIL', '') or \
        [x.strip() for x in env.get('MB_ADMIN_ALERT_EMAILS', '').split(',') if x.strip()][:1]
    deal_notify = deal_notify[0] if deal_notify else ''
    if not user or not password:
        print('no gmail imap creds; skipping', file=sys.stderr)
        return 0

    conn = sqlite3.connect(os.path.join(ROOT, 'data', 'blotter.db'), timeout=30)
    directory = prospect_directory(conn)
    if not directory:
        print('no prospects to watch')
        conn.close()
        return 0
    state = _load_state()
    seen = set(state.get('seen_message_ids', []))

    try:
        box = imaplib.IMAP4_SSL(server, 993)
        box.login(user, password)
    except (imaplib.IMAP4.error, OSError) as exc:
        print('imap connect failed: %s' % exc, file=sys.stderr)
        conn.close()
        return 0

    # Non-PDF inbound mail gets swept to Processed by email_worker; check both.
    # SINCE window keeps fetch cost bounded (~30 days of traffic).
    since = datetime.now(timezone.utc) - timedelta(days=30)
    since_str = since.strftime('%d-%b-%Y')
    processed = []
    for folder in ('INBOX', 'Processed'):
        try:
            typ, _ = box.select(folder, readonly=True)
            if typ != 'OK':
                continue
            typ, data = box.search(None, '(SINCE %s)' % since_str)
            if typ != 'OK' or not data or not data[0]:
                continue
            for num in data[0].split():
                processed.append((folder, num))
        except imaplib.IMAP4.error as exc:
            print('folder %s: %s' % (folder, exc), file=sys.stderr)

    hits = 0
    this_run: list[str] = []
    current_folder = None
    for folder, num in processed:
        if folder != current_folder:
            box.select(folder, readonly=True)   # fetch needs its folder selected
            current_folder = folder
        try:
            typ, fetched = box.fetch(num, '(BODY.PEEK[])')
            if typ != 'OK' or not fetched:
                continue
            raw = b''
            for item in fetched:
                if isinstance(item, tuple) and len(item) > 1:
                    raw += item[1]
            msg = email_lib.message_from_bytes(raw)
        except Exception as exc:
            print('fetch/parse failed for %s %s: %s' % (folder, num, exc), file=sys.stderr)
            continue
        mid = (msg.get('Message-ID') or '').strip()
        uid = mid or '%s:%s' % (folder, num.decode(errors='replace'))
        if uid in seen:
            continue
        this_run.append(uid)
        real_name, addr = parseaddr(_header(msg, 'From'))
        addr = (addr or '').lower()
        if not addr or addr == user.lower() or BOUNCE_SENDERS.match(addr.split('@')[0]):
            continue
        subject = _header(msg, 'Subject')
        preview = _body_preview(msg)
        is_notice = bool(AUTOBOUNCE_RE.search(subject + ' ' + preview))

        match_addr = addr
        if is_notice:
            # Bounce notices quote the ORIGINAL recipient in their body.
            inner = [a for a in re.findall(
                r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', preview)
                if a.lower() in directory]
            match_addr = inner[0].lower() if inner else addr

        hit = directory.get(match_addr)
        if not hit:
            continue
        campaign, table, pk = hit
        col = 'status' if table == 'lawyer_outreach_prospects' else 'outreach_status'
        new_status = 'bounced' if is_notice else 'replied'
        try:
            received = parsedate_to_datetime(_header(msg, 'Date')).isoformat()
        except Exception:
            received = ''
        print('%s %s: %s (%s) -> %s' % ('BOUNCE' if is_notice else 'REPLY',
                                        campaign, match_addr, real_name or '', new_status))
        hits += 1
        if dry:
            continue
        record = {'campaign': campaign, 'status': new_status, 'from_addr': addr,
                  'subject': subject, 'snippet': preview, 'is_bounce': is_notice,
                  'received_at': received}
        if not _write_db(conn, table, col, pk, uid, record):
            print('db locked; will retry this message next run', file=sys.stderr)
            if uid in this_run:
                this_run.remove(uid)
            continue
        seen.add(uid)
        if new_status == 'replied' and deal_notify:
            _notify_deal(env, deal_notify, campaign, real_name, addr, subject, preview)
        time.sleep(0.3)

    try:
        box.logout()
    except Exception:
        pass
    conn.close()
    # Mark everything inspected this run as seen (so non-matches are never
    # re-fetched); failed-write uids were removed above and will retry.
    state['seen_message_ids'] = list(seen | set(this_run))
    _save_state(state)
    print('scanned %d unseen messages, %d prospect hits' % (len(processed), hits))
    return 0


if __name__ == '__main__':
    sys.exit(run(dry='--dry-run' in sys.argv))
