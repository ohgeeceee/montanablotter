"""
Morning Briefing - emails a daily digest of yesterday's posts.
Sends to the admin and to all active public subscribers.
Runs daily at 7am via cron.
"""

from collections import Counter
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
import argparse
import re
import smtplib
import json
import sqlite3

import config
from utils.app_settings import _save_app_setting
from services.alerts.legacy import collect_alert_recipients, send_plaintext_email as _send_admin_alert

try:
    import anthropic
except ImportError:  # pragma: no cover - optional at runtime
    anthropic = None

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


def build_html(posts, date_str, unsubscribe_url=None, open_tracking_url=None):
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
            summary_raw = post["summary"] or ""
            # Format the summary for better readability in email
            summary_lines = summary_raw.split('\n')
            formatted_lines = []
            for line in summary_lines:
                line = line.strip()
                if not line:
                    continue
                # Highlight incident lines (those starting with time patterns like "06:00 –" or "10:30 AM –")
                if re.match(r'^(\d{1,2}:\d{2}\s*(?:AM|PM)?\s*[–-]\s*|\d{1,2}:\d{2}\s*[–-])', line, re.IGNORECASE):
                    # Bold the time and incident type, keep details readable
                    parts = line.split('–', 1)
                    if len(parts) == 2:
                        time_part = parts[0].strip()
                        rest = parts[1].strip()
                        formatted_lines.append(
                            f'<div style="margin:4px 0;padding:6px 8px;background:#f8fafc;border-left:3px solid #2563eb;border-radius:4px;">'
                            f'<strong style="color:#1e293b;">{escape(time_part)}</strong> – {escape(rest)}'
                            f'</div>'
                        )
                    else:
                        formatted_lines.append(f'<div style="margin:4px 0;padding:6px 8px;background:#f8fafc;border-left:3px solid #2563eb;border-radius:4px;">{escape(line)}</div>')
                elif line.startswith('The ') and 'responded to' in line:
                    formatted_lines.append(f'<p style="color:#475569;font-weight:600;margin:12px 0 8px;">{escape(line)}</p>')
                elif line.startswith('// Historical'):
                    formatted_lines.append(f'<p style="color:#94a3b8;font-size:12px;font-style:italic;margin:12px 0 0;border-top:1px solid #e2e8f0;padding-top:8px;">{escape(line)}</p>')
                else:
                    formatted_lines.append(f'<p style="color:#374151;margin:2px 0;">{escape(line)}</p>')
            summary_html = '\n'.join(formatted_lines) if formatted_lines else escape(summary_raw).replace('\n', '<br>')
            title = escape(post["title"] or "Daily Activity Report")
            post_url = f"{BASE_URL}/post/{post['id']}"
            html += f"""
            <div style="padding:0 0 14px;">
                <h4 style="color:#1e293b;margin:0 0 6px;">{title}</h4>
                <p style="color:#64748b;font-size:13px;margin:0 0 8px;">{agency} &mdash; {escape(post['incident_date'] or date_str)}</p>
                <div style="color:#374151;line-height:1.6;margin:0;">{summary_html}</div>
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
    html += "</p>"
    if open_tracking_url:
        html += f'<img src="{open_tracking_url}" width="1" height="1" style="display:block;border:0;" alt="">'
    html += "</div>"
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


_CRISIS_BANNER_EVERGREEN_HEADLINE = "Support Montana public safety journalism"
_CRISIS_BANNER_EVERGREEN_BODY = (
    "Help fund ongoing dispatch monitoring, records coverage, "
    "and county-by-county reporting across Montana."
)

_CRISIS_BANNER_PROMPT = """\
You are an editor for Montana Blotter, a Montana public safety news site.

Review these law enforcement blotter summaries from yesterday and determine if \
there is an active public safety crisis that readers should know about. \
Crises include: wildfires, floods, winter storms, major search-and-rescue \
operations, missing persons, or other significant public safety emergencies \
affecting Montana communities.

Respond ONLY with valid JSON in this exact format:
{
  "crisis_detected": true or false,
  "crisis_type": "brief crisis type or null",
  "headline": "Banner headline, max 80 characters",
  "body": "Banner body, max 160 characters"
}

If no crisis is detected, set crisis_detected to false and write an evergreen \
message encouraging readers to support Montana public safety coverage.

Blotter summaries:
{summaries}"""


def _update_crisis_banner(posts, conn=None):
    if anthropic is None:
        print("Banner update skipped: anthropic package not installed.")
        return
    """Call Claude to detect crises in yesterday's posts and update the banner settings.

    If posts is empty, writes the evergreen message without calling the API.
    If the API call fails or returns bad JSON, leaves existing settings unchanged.
    Always sets winter_storm_banner_enabled to '1'.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_db()

    try:
        if not posts:
            _save_app_setting(conn, "winter_storm_banner_enabled", "1")
            _save_app_setting(conn, "winter_storm_banner_headline", _CRISIS_BANNER_EVERGREEN_HEADLINE)
            _save_app_setting(conn, "winter_storm_banner_body", _CRISIS_BANNER_EVERGREEN_BODY)
            conn.commit()
            print("Banner updated: no posts — wrote evergreen message.")
            return

        # Build compact text blob (cap at 3000 chars to stay within token budget)
        lines = []
        for p in posts:
            title = (p["title"] or "").strip()
            summary = (p["summary"] or "").strip()
            if title or summary:
                lines.append(f"- {title}: {summary}" if title else f"- {summary}")
        summaries_text = "\n".join(lines)[:3000]

        api_key = getattr(config, "ANTHROPIC_API_KEY", None)
        client_kwargs = {}
        if api_key:
            client_kwargs["api_key"] = api_key

        client = anthropic.Anthropic(**client_kwargs)
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=256,
            messages=[
                {
                    "role": "user",
                    "content": _CRISIS_BANNER_PROMPT.replace("{summaries}", summaries_text),
                }
            ],
        )
        raw = message.content[0].text.strip()
        # Claude sometimes wraps JSON in markdown code fences — strip them
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
            raw = raw.rsplit("```", 1)[0].strip()
        data = json.loads(raw)

        headline = str(data.get("headline") or _CRISIS_BANNER_EVERGREEN_HEADLINE)[:80]
        body = str(data.get("body") or _CRISIS_BANNER_EVERGREEN_BODY)[:160]

        _save_app_setting(conn, "winter_storm_banner_enabled", "1")
        _save_app_setting(conn, "winter_storm_banner_headline", headline)
        _save_app_setting(conn, "winter_storm_banner_body", body)
        conn.commit()

        crisis_type = data.get("crisis_type") or "none"
        print(f"Banner updated: crisis_detected={data.get('crisis_detected')}, type={crisis_type}")

    except Exception as e:
        print(f"Banner update failed (settings unchanged): {e}")
    finally:
        if own_conn:
            conn.close()


def run_briefing():
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    subject = f"Montana Blotter Briefing – {datetime.now().strftime('%b %d, %Y')}".replace('\r', '').replace('\n', '')

    # --- Admin briefing (all counties) ---
    posts = get_posts_for_date(yesterday)
    if posts:
        html = build_html(posts, yesterday)
        try:
            send_email(ADMIN_EMAIL, subject, html)
            print(f"Admin briefing sent ({len(posts)} posts)")
        except Exception as e:
            print(f"Admin briefing failed: {e}")
            try:
                conn = get_db()
                recipients = collect_alert_recipients(conn)
                conn.close()
            except Exception:
                recipients = [ADMIN_EMAIL] if ADMIN_EMAIL else []
            _send_admin_alert(
                recipients,
                "[Montana Blotter] Morning briefing failed",
                f"morning_briefing.py failed to send the admin briefing.\n\nError: {e}",
            )
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

    # Update the top-of-site banner based on today's blotter content
    _update_crisis_banner(posts)


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    ).fetchone()
    return bool(row)


def _count_semantic_vectors_last_24h(conn) -> int:
    candidate_tables = [
        ("incident_vectors", "created_at"),
        ("meeting_pdf_chunks", "created_at"),
        ("meeting_embeddings", "created_at"),
    ]
    total = 0
    for table_name, timestamp_col in candidate_tables:
        if not _table_exists(conn, table_name):
            continue
        try:
            value = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM {table_name}
                WHERE datetime(COALESCE({timestamp_col}, datetime('now'))) >= datetime('now', '-24 hours')
                """
            ).fetchone()[0]
            total += int(value or 0)
        except sqlite3.OperationalError:
            continue
    return total


def build_ops_morning_briefing_markdown() -> str:
    conn = get_db()
    try:
        run_rows = conn.execute(
            """
            SELECT
                job_name,
                SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok_runs,
                SUM(CASE WHEN status != 'ok' THEN 1 ELSE 0 END) AS failed_runs
            FROM scheduled_job_runs
            WHERE datetime(COALESCE(started_at, created_at)) >= datetime('now', '-24 hours')
            GROUP BY job_name
            ORDER BY job_name ASC
            """
        ).fetchall() if _table_exists(conn, "scheduled_job_runs") else []
        state_rows = conn.execute(
            """
            SELECT job_name, last_status, last_started_at, last_finished_at, last_output_excerpt
            FROM scheduled_job_state
            ORDER BY job_name ASC
            """
        ).fetchall() if _table_exists(conn, "scheduled_job_state") else []
        vectors_created = _count_semantic_vectors_last_24h(conn)
    finally:
        conn.close()

    by_job = {row["job_name"]: row for row in run_rows}
    lines = [
        "# Morning Briefing",
        "",
        f"- Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC",
        f"- Semantic vectors created (last 24h): {vectors_created}",
        "",
        "## Scheduled Jobs",
    ]
    if not state_rows:
        lines.append("- No scheduled job state rows found.")
        return "\n".join(lines) + "\n"

    for row in state_rows:
        job_name = row["job_name"]
        stats = by_job.get(job_name)
        ok_runs = int(stats["ok_runs"]) if stats and stats["ok_runs"] is not None else 0
        failed_runs = int(stats["failed_runs"]) if stats and stats["failed_runs"] is not None else 0
        lines.append(
            f"- `{job_name}`: last_status={row['last_status']}, ok_24h={ok_runs}, failed_24h={failed_runs}, "
            f"last_started={row['last_started_at'] or 'n/a'}, last_finished={row['last_finished_at'] or 'n/a'}"
        )
        if row["last_status"] != "ok":
            excerpt = (row["last_output_excerpt"] or "").strip().splitlines()
            short_excerpt = excerpt[-1][:200] if excerpt else "no output excerpt"
            lines.append(f"  failure_excerpt: {short_excerpt}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Morning briefing sender and ops summary generator.")
    parser.add_argument(
        "--ops-markdown",
        default="",
        help="Optional output path for the ops Morning Briefing markdown summary.",
    )
    parser.add_argument(
        "--ops-only",
        action="store_true",
        help="Only emit the ops markdown briefing without sending subscriber emails.",
    )
    args = parser.parse_args()

    if args.ops_only or args.ops_markdown:
        markdown = build_ops_morning_briefing_markdown()
        print(markdown)
        if args.ops_markdown:
            with open(args.ops_markdown, "w", encoding="utf-8") as handle:
                handle.write(markdown)
    if not args.ops_only:
        run_briefing()
