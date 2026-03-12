"""
Morning Briefing - emails a daily digest of yesterday's posts.
Sends to the admin and to all active public subscribers.
Runs daily at 7am via cron.
"""

from collections import Counter
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
import re
import smtplib
import sqlite3

import config

ADMIN_EMAIL = "ohjoncurrie@gmail.com"
BASE_URL = "https://montanablotter.com"


def _slugify(value):
    value = (value or "").strip().lower()
    value = re.sub(r"[^\w\s-]", "", value)
    value = re.sub(r"[\s_-]+", "-", value)
    return value


def get_db():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_digest_tables(conn):
    conn.execute('''
        CREATE TABLE IF NOT EXISTS digest_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            target_date TEXT NOT NULL,
            audience TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            subject TEXT,
            preview_posts INTEGER DEFAULT 0,
            preview_subscribers INTEGER DEFAULT 0,
            sent_count INTEGER DEFAULT 0,
            skipped_count INTEGER DEFAULT 0,
            failed_count INTEGER DEFAULT 0,
            initiated_by TEXT,
            notes TEXT,
            created_by_user_id INTEGER,
            started_at TEXT,
            finished_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_digest_runs_created ON digest_runs(created_at)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_digest_runs_target ON digest_runs(target_date)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_digest_runs_status ON digest_runs(status)')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS digest_run_recipients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            recipient_email TEXT NOT NULL,
            counties TEXT DEFAULT '',
            status TEXT NOT NULL,
            post_count INTEGER DEFAULT 0,
            error_message TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (run_id) REFERENCES digest_runs(id) ON DELETE CASCADE
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_digest_run_recipients_run ON digest_run_recipients(run_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_digest_run_recipients_status ON digest_run_recipients(status)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_digest_run_recipients_created ON digest_run_recipients(created_at)')


def _create_digest_run(conn, *, target_date, subject, preview_posts, preview_subscribers):
    cursor = conn.execute(
        '''
        INSERT INTO digest_runs (
            kind, target_date, audience, status, subject, preview_posts,
            preview_subscribers, initiated_by, notes, started_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ''',
        (
            'morning_briefing',
            target_date,
            'subscribers',
            'running',
            (subject or '').strip()[:255],
            int(preview_posts or 0),
            int(preview_subscribers or 0),
            'cron',
            'Scheduled daily briefing run.',
        ),
    )
    return cursor.lastrowid


def _record_digest_recipient(conn, run_id, email, counties, status, post_count=0, error_message=''):
    conn.execute(
        '''
        INSERT INTO digest_run_recipients (
            run_id, recipient_email, counties, status, post_count, error_message
        ) VALUES (?, ?, ?, ?, ?, ?)
        ''',
        (
            run_id,
            (email or '').strip().lower(),
            (counties or '').strip()[:255],
            (status or '').strip()[:40] or 'pending',
            int(post_count or 0),
            (error_message or '').strip()[:500] or None,
        ),
    )


def _finish_digest_run(conn, run_id, *, status, sent_count=0, skipped_count=0, failed_count=0, notes=''):
    conn.execute(
        '''
        UPDATE digest_runs
        SET status = ?,
            sent_count = ?,
            skipped_count = ?,
            failed_count = ?,
            notes = ?,
            finished_at = datetime('now')
        WHERE id = ?
        ''',
        (
            (status or '').strip()[:40] or 'completed',
            int(sent_count or 0),
            int(skipped_count or 0),
            int(failed_count or 0),
            (notes or '').strip()[:500] or None,
            run_id,
        ),
    )


def get_posts_for_date(date_str, counties=None):
    """Return posts for a given YYYY-MM-DD date, optionally filtered by county list."""
    conn = get_db()
    sql = """
        SELECT p.id, p.title, p.summary, p.agency_name, p.county, p.incident_date
        FROM posts p
        WHERE (p.incident_date = ? OR DATE(p.created_at) = ?)
    """
    params = [date_str, date_str]
    if counties:
        placeholders = ','.join('?' * len(counties))
        sql += f" AND p.county IN ({placeholders})"
        params.extend(counties)
    sql += " ORDER BY p.incident_date, p.created_at"
    posts = conn.execute(sql, params).fetchall()
    conn.close()
    return posts


def group_posts_by_county(posts):
    grouped = {}
    for post in posts:
        county = (post["county"] or "Unknown").strip() or "Unknown"
        grouped.setdefault(county, []).append(post)
    return grouped


def build_html(posts, date_str, unsubscribe_url=None):
    county_counts = Counter((post["county"] or "Unknown") for post in posts)
    county_total = len(county_counts)
    top_county = county_counts.most_common(1)[0] if county_counts else None
    grouped_posts = group_posts_by_county(posts)
    top_county_html = ""
    if top_county and top_county[0] != "Unknown":
        top_county_slug = _slugify(top_county[0])
        top_county_html = (
            f'<p style="color:#64748b;margin:6px 0 0;">'
            f'Top county: <a href="{BASE_URL}/county/{top_county_slug}" '
            f'style="color:#2563eb;text-decoration:none;">{escape(top_county[0])} County</a> '
            f'({top_county[1]} report{"s" if top_county[1] != 1 else ""})'
            f"</p>"
        )

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
    <h2 style="color:#1e293b;">Montana Blotter: Morning Briefing</h2>
    <p style="color:#64748b;"><strong>Date:</strong> {datetime.now().strftime('%B %d, %Y')}</p>
    <p style="color:#64748b;">{len(posts)} report(s) from {escape(date_str)} across {county_total} count{"ies" if county_total != 1 else "y"}</p>
    {top_county_html}
    <p style="margin:14px 0 0;">
        <a href="{BASE_URL}/counties" style="display:inline-block;background:#2563eb;color:#ffffff;padding:10px 14px;border-radius:8px;text-decoration:none;font-weight:700;">Browse county pages</a>
        <a href="{BASE_URL}/arrests" style="display:inline-block;margin-left:8px;background:#eff6ff;color:#1d4ed8;padding:10px 14px;border-radius:8px;text-decoration:none;font-weight:700;">View arrest log</a>
    </p>
    <hr style="border:1px solid #e2e8f0;">
    """
    for county_name, county_posts in grouped_posts.items():
        county_slug = _slugify(county_name)
        county_heading = (
            f'<a href="{BASE_URL}/county/{county_slug}" style="color:#1e293b;text-decoration:none;">{escape(county_name)} County</a>'
            if county_name != "Unknown" else "Unknown County"
        )
        html += f"""
        <h3 style="color:#1e293b;margin:0 0 4px;">{county_heading}</h3>
        <p style="color:#64748b;font-size:13px;margin:0 0 14px;">{len(county_posts)} report{"s" if len(county_posts) != 1 else ""}</p>
        """
        for post in county_posts:
            agency = escape(post['agency_name'] or post['county'] or 'Unknown Agency')
            summary_html = escape(post["summary"] or "").replace('\n', '<br>')
            title = escape(post["title"] or "Daily Activity Report")
            post_url = f"{BASE_URL}/post/{post['id']}"
            html += f"""
            <div style="padding:0 0 14px;">
                <h4 style="color:#1e293b;margin:0 0 6px;">{title}</h4>
                <p style="color:#64748b;font-size:13px;margin:0 0 8px;">{agency} &mdash; {escape(post['incident_date'] or date_str)}</p>
                <p style="color:#374151;line-height:1.6;margin:0;">{summary_html}</p>
                <p style="margin:12px 0 0;">
                    <a href="{post_url}" style="display:inline-block;background:#111827;color:#ffffff;padding:8px 12px;border-radius:7px;text-decoration:none;font-weight:700;font-size:13px;">Read full report</a>
                </p>
            </div>
            """
        html += '<hr style="border:1px solid #e2e8f0;">'
    html += f"""
    <p style="color:#94a3b8;font-size:12px;margin-top:24px;">
        <a href="{BASE_URL}" style="color:#3b82f6;">montanablotter.com</a>
    """
    if unsubscribe_url:
        html += f' &mdash; <a href="{unsubscribe_url}" style="color:#94a3b8;">Unsubscribe</a>'
    html += "</p></div>"
    return html


def send_email(to_addr, subject, html_body):
    smtp_user = getattr(config, 'SMTP_USER', config.EMAIL_USER)
    smtp_password = getattr(config, 'SMTP_PASSWORD', config.EMAIL_PASSWORD)
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = smtp_user
    msg['To'] = to_addr
    msg.attach(MIMEText(html_body, 'html'))
    with smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT) as server:
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.sendmail(smtp_user, to_addr, msg.as_string())


def run_briefing():
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    subject = f"Montana Blotter Briefing – {datetime.now().strftime('%b %d, %Y')}"

    # --- Admin briefing (all counties) ---
    posts = get_posts_for_date(yesterday)
    if posts:
        html = build_html(posts, yesterday)
        try:
            send_email(ADMIN_EMAIL, subject, html)
            print(f"Admin briefing sent ({len(posts)} posts)")
        except Exception as e:
            print(f"Admin briefing failed: {e}")
    else:
        print(f"No posts for {yesterday} — skipping admin briefing.")

    # --- Public subscriber briefings ---
    conn = get_db()
    subscribers = conn.execute(
        'SELECT email, counties, token FROM subscribers WHERE active=1'
    ).fetchall()
    _ensure_digest_tables(conn)
    run_id = _create_digest_run(
        conn,
        target_date=yesterday,
        subject=subject,
        preview_posts=len(posts),
        preview_subscribers=len(subscribers),
    )
    conn.commit()
    conn.close()

    sent = skipped = 0
    failed = 0
    run_conn = get_db()
    for sub in subscribers:
        county_filter = [c.strip() for c in (sub['counties'] or '').split(',') if c.strip()]
        sub_posts = get_posts_for_date(yesterday, county_filter or None)
        if not sub_posts:
            skipped += 1
            _record_digest_recipient(
                run_conn,
                run_id,
                sub['email'],
                sub['counties'] or '',
                'skipped',
                post_count=0,
                error_message='No matching posts for subscriber counties.',
            )
            continue
        unsub_url = f"{BASE_URL}/unsubscribe?token={sub['token']}"
        html = build_html(sub_posts, yesterday, unsubscribe_url=unsub_url)
        try:
            send_email(sub['email'], subject, html)
            sent += 1
            _record_digest_recipient(
                run_conn,
                run_id,
                sub['email'],
                sub['counties'] or '',
                'sent',
                post_count=len(sub_posts),
            )
        except Exception as e:
            print(f"Failed to send to {sub['email']}: {e}")
            failed += 1
            _record_digest_recipient(
                run_conn,
                run_id,
                sub['email'],
                sub['counties'] or '',
                'failed',
                post_count=len(sub_posts),
                error_message=str(e),
            )

    final_status = 'completed'
    if failed and sent:
        final_status = 'completed_with_errors'
    elif failed and not sent:
        final_status = 'failed'
    _finish_digest_run(
        run_conn,
        run_id,
        status=final_status,
        sent_count=sent,
        skipped_count=skipped,
        failed_count=failed,
        notes='Scheduled daily briefing run.',
    )
    run_conn.commit()
    run_conn.close()

    print(f"Subscriber briefings: {sent} sent, {skipped} skipped, {failed} failed")


if __name__ == "__main__":
    run_briefing()
