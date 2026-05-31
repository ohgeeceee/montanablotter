"""
Data Watch Alert Dispatcher

Handles name/keyword watches across 5 Montana Blotter data sources:
  jail_booking      — alert when a person is booked at a county jail
  court_filing      — alert when a name/term appears in a new civil filing
  license_sanction  — alert when a professional is sanctioned
  code_violation    — alert when a property address or owner appears in violations
  meeting_agenda    — alert when a keyword or body name appears in new agendas

CLI usage:
    python -m services.alerts.data_watches --type all
    python -m services.alerts.data_watches --type jail_booking --dry-run
"""
from __future__ import annotations

import argparse
import secrets
import smtplib
import sqlite3
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import config
from db import connect_db

BASE_URL = getattr(config, 'BASE_URL', 'https://montanablotter.com')
COOLDOWN_HOURS = 24
WATCH_TYPES = ('jail_booking', 'court_filing', 'license_sanction', 'code_violation', 'meeting_agenda')


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def ensure_data_watch_schema(conn: sqlite3.Connection) -> None:
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS jail_booking_watches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            watch_name TEXT NOT NULL,
            county TEXT DEFAULT '',
            token TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            last_alerted_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS court_filing_watches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            watch_term TEXT NOT NULL,
            county TEXT DEFAULT '',
            token TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            last_alerted_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS license_sanction_watches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            watch_term TEXT NOT NULL,
            county TEXT DEFAULT '',
            token TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            last_alerted_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS code_violation_watches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            watch_term TEXT NOT NULL,
            county TEXT DEFAULT '',
            token TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            last_alerted_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS meeting_agenda_watches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            keyword TEXT DEFAULT '',
            body_name TEXT DEFAULT '',
            county TEXT DEFAULT '',
            token TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            last_alerted_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
    ''')
    conn.commit()


# ---------------------------------------------------------------------------
# Email helpers
# ---------------------------------------------------------------------------

def _send_email(to_addr: str, subject: str, html_body: str) -> bool:
    smtp_user = getattr(config, 'SMTP_USER', config.EMAIL_USER)
    smtp_password = getattr(config, 'SMTP_PASSWORD', config.EMAIL_PASSWORD)
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = f"Montana Blotter Alerts <{smtp_user}>"
    msg['To'] = to_addr
    msg.attach(MIMEText(html_body, 'html'))
    try:
        with smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, to_addr, msg.as_string())
        return True
    except Exception as exc:
        print(f"Email failed to {to_addr}: {exc}")
        return False


def _cooldown_ok(last_alerted_at: str | None) -> bool:
    if not last_alerted_at:
        return True
    last = datetime.fromisoformat(last_alerted_at).replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last > timedelta(hours=COOLDOWN_HOURS)


def _unsub_url(token: str, watch_type: str) -> str:
    return f"{BASE_URL}/data-alerts/unsubscribe?type={watch_type}&token={token}"


def _email_html(title: str, watch_label: str, rows: list[dict[str, str]],
                unsub_url: str, source_label: str) -> str:
    cards = ''
    for r in rows:
        fields = ''.join(
            f'<tr><td style="padding:3px 12px 3px 0;color:#64748b;font-size:13px;white-space:nowrap">{k}</td>'
            f'<td style="padding:3px 0;font-size:13px">{v}</td></tr>'
            for k, v in r.items() if v
        )
        cards += f'<div style="border:1px solid #e2e8f0;border-radius:8px;padding:12px 16px;margin-bottom:12px"><table>{fields}</table></div>'
    return (
        f'<html><body style="font-family:sans-serif;color:#1e293b;max-width:600px;margin:0 auto">'
        f'<h2 style="font-size:17px;margin-bottom:2px">{title}</h2>'
        f'<p style="color:#64748b;font-size:13px;margin-top:0">Watch: <strong>{watch_label}</strong> &middot; {len(rows)} new result(s)</p>'
        f'{cards}'
        f'<p style="font-size:12px;color:#94a3b8;margin-top:20px">Source: {source_label}<br>'
        f'<a href="{unsub_url}" style="color:#94a3b8">Unsubscribe from this watch</a></p>'
        f'</body></html>'
    )


# ---------------------------------------------------------------------------
# Per-type dispatchers
# ---------------------------------------------------------------------------

def dispatch_jail_booking_watches(conn: sqlite3.Connection, dry_run: bool = False) -> int:
    sent = 0
    for w in conn.execute("SELECT * FROM jail_booking_watches WHERE active=1").fetchall():
        if not _cooldown_ok(w['last_alerted_at']):
            continue
        since = w['last_alerted_at'] or '2000-01-01'
        like = f"%{w['watch_name']}%"
        sql = "SELECT * FROM jail_bookings WHERE person_name LIKE ? AND created_at > ?"
        params: list[Any] = [like, since]
        if w['county']:
            sql += " AND LOWER(county_name) LIKE ?"
            params.append(f"%{w['county'].lower()}%")
        matches = conn.execute(sql + " ORDER BY created_at DESC LIMIT 10", params).fetchall()
        if not matches:
            continue
        rows = [{'Name': m['person_name'], 'County': m['county_name'],
                 'Booked': (m['booking_at'] or '')[:10], 'Facility': m['facility_name'],
                 'Charges': (m['charges_summary'] or '')[:120]} for m in matches]
        html = _email_html('Jail Booking Alert', w['watch_name'], rows,
                           _unsub_url(w['token'], 'jail_booking'), 'Montana County Jail Rosters')
        if not dry_run:
            if _send_email(w['email'], f"Jail Booking Alert: {w['watch_name']}", html):
                conn.execute("UPDATE jail_booking_watches SET last_alerted_at=datetime('now') WHERE id=?", (w['id'],))
                sent += 1
        else:
            print(f"[dry-run] jail_booking → {w['email']}: {len(matches)} match(es) for '{w['watch_name']}'")
    if not dry_run:
        conn.commit()
    return sent


def dispatch_court_filing_watches(conn: sqlite3.Connection, dry_run: bool = False) -> int:
    sent = 0
    for w in conn.execute("SELECT * FROM court_filing_watches WHERE active=1").fetchall():
        if not _cooldown_ok(w['last_alerted_at']):
            continue
        since = w['last_alerted_at'] or '2000-01-01'
        like = f"%{w['watch_term']}%"
        sql = """SELECT * FROM civil_filings
                 WHERE (caption LIKE ? OR plaintiff_name LIKE ? OR defendant_name LIKE ?)
                   AND created_at > ?"""
        params = [like, like, like, since]
        if w['county']:
            sql += " AND LOWER(county) LIKE ?"
            params.append(f"%{w['county'].lower()}%")
        matches = conn.execute(sql + " ORDER BY created_at DESC LIMIT 10", params).fetchall()
        if not matches:
            continue
        rows = [{'Case': m['case_number'], 'Type': m['case_type_label'],
                 'Caption': (m['caption'] or '')[:100], 'Filed': (m['filing_date'] or '')[:10],
                 'County': m['county'], 'Plaintiff': m['plaintiff_name'],
                 'Defendant': m['defendant_name']} for m in matches]
        html = _email_html('Court Filing Alert', w['watch_term'], rows,
                           _unsub_url(w['token'], 'court_filing'), 'iCourtCase Montana')
        if not dry_run:
            if _send_email(w['email'], f"Court Filing Alert: {w['watch_term']}", html):
                conn.execute("UPDATE court_filing_watches SET last_alerted_at=datetime('now') WHERE id=?", (w['id'],))
                sent += 1
        else:
            print(f"[dry-run] court_filing → {w['email']}: {len(matches)} match(es) for '{w['watch_term']}'")
    if not dry_run:
        conn.commit()
    return sent


def dispatch_license_sanction_watches(conn: sqlite3.Connection, dry_run: bool = False) -> int:
    sent = 0
    for w in conn.execute("SELECT * FROM license_sanction_watches WHERE active=1").fetchall():
        if not _cooldown_ok(w['last_alerted_at']):
            continue
        since = w['last_alerted_at'] or '2000-01-01'
        like = f"%{w['watch_term']}%"
        sql = """SELECT * FROM license_sanctions
                 WHERE (person_name LIKE ? OR COALESCE(name,'') LIKE ? OR board LIKE ?)
                   AND created_at > ?"""
        params = [like, like, like, since]
        if w['county']:
            sql += " AND LOWER(COALESCE(county,'')) LIKE ?"
            params.append(f"%{w['county'].lower()}%")
        matches = conn.execute(sql + " ORDER BY created_at DESC LIMIT 10", params).fetchall()
        if not matches:
            continue
        rows = [{'Name': m['person_name'], 'Board': m['board'],
                 'Violation': (m['violation_type'] or '')[:80],
                 'Action': (m['action_taken'] or '')[:80],
                 'Effective': m['effective_date'], 'County': m['county']} for m in matches]
        html = _email_html('License Sanction Alert', w['watch_term'], rows,
                           _unsub_url(w['token'], 'license_sanction'),
                           'Montana Professional Licensing Boards')
        if not dry_run:
            if _send_email(w['email'], f"License Sanction Alert: {w['watch_term']}", html):
                conn.execute("UPDATE license_sanction_watches SET last_alerted_at=datetime('now') WHERE id=?", (w['id'],))
                sent += 1
        else:
            print(f"[dry-run] license_sanction → {w['email']}: {len(matches)} match(es) for '{w['watch_term']}'")
    if not dry_run:
        conn.commit()
    return sent


def dispatch_code_violation_watches(conn: sqlite3.Connection, dry_run: bool = False) -> int:
    sent = 0
    for w in conn.execute("SELECT * FROM code_violation_watches WHERE active=1").fetchall():
        if not _cooldown_ok(w['last_alerted_at']):
            continue
        since = w['last_alerted_at'] or '2000-01-01'
        like = f"%{w['watch_term']}%"
        matches = conn.execute(
            """SELECT * FROM code_violations
               WHERE (raw_address LIKE ? OR COALESCE(owner_name,'') LIKE ?)
                 AND created_at > ?
               ORDER BY created_at DESC LIMIT 10""",
            [like, like, since],
        ).fetchall()
        if not matches:
            continue
        rows = [{'Address': m['raw_address'], 'Type': m['violation_type'],
                 'Status': m['status'], 'Issued': m['date_issued'],
                 'Owner': m['owner_name'],
                 'Fine': f"${m['fine_amount']}" if m['fine_amount'] else ''} for m in matches]
        html = _email_html('Code Violation Alert', w['watch_term'], rows,
                           _unsub_url(w['token'], 'code_violation'),
                           'Montana Municipal Code Enforcement')
        if not dry_run:
            if _send_email(w['email'], f"Code Violation Alert: {w['watch_term']}", html):
                conn.execute("UPDATE code_violation_watches SET last_alerted_at=datetime('now') WHERE id=?", (w['id'],))
                sent += 1
        else:
            print(f"[dry-run] code_violation → {w['email']}: {len(matches)} match(es) for '{w['watch_term']}'")
    if not dry_run:
        conn.commit()
    return sent


def dispatch_meeting_agenda_watches(conn: sqlite3.Connection, dry_run: bool = False) -> int:
    sent = 0
    for w in conn.execute("SELECT * FROM meeting_agenda_watches WHERE active=1").fetchall():
        if not _cooldown_ok(w['last_alerted_at']):
            continue
        since = w['last_alerted_at'] or '2000-01-01'
        clauses = ["pm.created_at > ?"]
        params: list[Any] = [since]
        if w['keyword']:
            clauses.append("(pm.title LIKE ? OR COALESCE(pm.body_name,'') LIKE ?)")
            like = f"%{w['keyword']}%"
            params += [like, like]
        if w['body_name']:
            clauses.append("COALESCE(pm.body_name,'') LIKE ?")
            params.append(f"%{w['body_name']}%")
        if w['county']:
            clauses.append("COALESCE(ml.county_name,'') LIKE ?")
            params.append(f"%{w['county']}%")
        matches = conn.execute(
            f"""SELECT pm.title, pm.body_name, pm.meeting_date, pm.meeting_time,
                       pm.meeting_page_url, ml.display_name, ml.county_name
                FROM public_meetings pm
                LEFT JOIN meeting_locations ml ON ml.id = pm.location_id
                WHERE {' AND '.join(clauses)}
                ORDER BY pm.meeting_date ASC LIMIT 10""",
            params,
        ).fetchall()
        if not matches:
            continue
        watch_label = w['keyword'] or w['body_name'] or w['county'] or '(all)'
        rows = [{'Meeting': m['title'], 'Body': m['body_name'],
                 'Date': m['meeting_date'], 'Location': m['display_name'],
                 'County': m['county_name']} for m in matches]
        html = _email_html('Public Meeting Alert', watch_label, rows,
                           _unsub_url(w['token'], 'meeting_agenda'),
                           'Montana Public Meeting Feed')
        if not dry_run:
            if _send_email(w['email'], f"Meeting Agenda Alert: {watch_label}", html):
                conn.execute("UPDATE meeting_agenda_watches SET last_alerted_at=datetime('now') WHERE id=?", (w['id'],))
                sent += 1
        else:
            print(f"[dry-run] meeting_agenda → {w['email']}: {len(matches)} match(es) for '{watch_label}'")
    if not dry_run:
        conn.commit()
    return sent


# ---------------------------------------------------------------------------
# Subscribe / unsubscribe (called from app.py)
# ---------------------------------------------------------------------------

def _subscribe(table: str, conn: sqlite3.Connection, **fields) -> str:
    token = secrets.token_urlsafe(32)
    cols = ', '.join(list(fields.keys()) + ['token'])
    placeholders = ', '.join(['?'] * (len(fields) + 1))
    conn.execute(
        f"INSERT INTO {table} ({cols}) VALUES ({placeholders})",
        list(fields.values()) + [token],
    )
    conn.commit()
    return token


def subscribe_jail_booking_watch(conn: sqlite3.Connection, email: str, watch_name: str, county: str = '') -> str:
    return _subscribe('jail_booking_watches', conn, email=email, watch_name=watch_name, county=county)


def subscribe_court_filing_watch(conn: sqlite3.Connection, email: str, watch_term: str, county: str = '') -> str:
    return _subscribe('court_filing_watches', conn, email=email, watch_term=watch_term, county=county)


def subscribe_license_sanction_watch(conn: sqlite3.Connection, email: str, watch_term: str, county: str = '') -> str:
    return _subscribe('license_sanction_watches', conn, email=email, watch_term=watch_term, county=county)


def subscribe_code_violation_watch(conn: sqlite3.Connection, email: str, watch_term: str, county: str = '') -> str:
    return _subscribe('code_violation_watches', conn, email=email, watch_term=watch_term, county=county)


def subscribe_meeting_agenda_watch(conn: sqlite3.Connection, email: str, keyword: str = '',
                                   body_name: str = '', county: str = '') -> str:
    return _subscribe('meeting_agenda_watches', conn,
                      email=email, keyword=keyword, body_name=body_name, county=county)


def cancel_data_watch(watch_type: str, token: str) -> bool:
    table = {
        'jail_booking': 'jail_booking_watches',
        'court_filing': 'court_filing_watches',
        'license_sanction': 'license_sanction_watches',
        'code_violation': 'code_violation_watches',
        'meeting_agenda': 'meeting_agenda_watches',
    }.get(watch_type)
    if not table:
        return False
    conn = connect_db()
    ensure_data_watch_schema(conn)
    row = conn.execute(f"SELECT id FROM {table} WHERE token=?", (token,)).fetchone()
    if row:
        conn.execute(f"UPDATE {table} SET active=0 WHERE token=?", (token,))
        conn.commit()
        conn.close()
        return True
    conn.close()
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def dispatch_all(dry_run: bool = False) -> dict[str, int]:
    conn = connect_db()
    ensure_data_watch_schema(conn)
    results = {
        'jail_booking': dispatch_jail_booking_watches(conn, dry_run),
        'court_filing': dispatch_court_filing_watches(conn, dry_run),
        'license_sanction': dispatch_license_sanction_watches(conn, dry_run),
        'code_violation': dispatch_code_violation_watches(conn, dry_run),
        'meeting_agenda': dispatch_meeting_agenda_watches(conn, dry_run),
    }
    conn.close()
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--type', choices=list(WATCH_TYPES) + ['all'], default='all')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    conn = connect_db()
    ensure_data_watch_schema(conn)
    dispatchers = {
        'jail_booking': dispatch_jail_booking_watches,
        'court_filing': dispatch_court_filing_watches,
        'license_sanction': dispatch_license_sanction_watches,
        'code_violation': dispatch_code_violation_watches,
        'meeting_agenda': dispatch_meeting_agenda_watches,
    }
    for t in (list(dispatchers) if args.type == 'all' else [args.type]):
        n = dispatchers[t](conn, args.dry_run)
        print(f"{t}: {n} sent")
    conn.close()


if __name__ == '__main__':
    main()
