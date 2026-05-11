from __future__ import annotations

import csv
import hashlib
import io
import sys
from datetime import datetime, timedelta
from html import escape
from urllib.parse import urlparse

import config
from flask import Response, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from blueprints.admin import admin_bp, require_role, _log_admin_action
from db import get_db
from services.publishing.morning_briefing import (
    build_html as build_morning_briefing_html,
    get_posts_for_date as get_morning_briefing_posts,
    send_email as send_morning_briefing_email,
)
from utils.auth_constants import (
    ADMIN_ACCESS_ROLES,
    AUDIENCE_MANAGEMENT_ROLES,
    EMAIL_OPS_SEND_ROLES,
)
from utils.app_settings import _app_setting_text, _save_app_setting

# ---------------------------------------------------------------------------
# Module-level constants (audience-only)
# ---------------------------------------------------------------------------

PUBLIC_COMMENT_TYPES = {
    'record': 'Incident record',
    'case_journey': 'Case journey',
    'blog_post': 'Blog post',
    'daily_post': 'Daily report',
}
PUBLIC_COMMENT_STATUSES = {'pending', 'approved', 'rejected', 'spam'}

BASE_URL = config.BASE_URL
SPONSOR_ANNOUNCEMENT_SETTING_KEYS = {
    'subject': 'email_ops.sponsor_announcement.subject',
    'sponsor_name': 'email_ops.sponsor_announcement.sponsor_name',
    'headline': 'email_ops.sponsor_announcement.headline',
    'body': 'email_ops.sponsor_announcement.body',
    'cta_label': 'email_ops.sponsor_announcement.cta_label',
    'cta_url': 'email_ops.sponsor_announcement.cta_url',
    'notes': 'email_ops.sponsor_announcement.notes',
}

# ---------------------------------------------------------------------------
# Private helpers — audience-exclusive
# ---------------------------------------------------------------------------


def _send_digest_email(*args, **kwargs):
    app_module = sys.modules.get('app')
    sender = getattr(app_module, 'send_morning_briefing_email', None) if app_module else None
    if callable(sender):
        return sender(*args, **kwargs)
    return send_morning_briefing_email(*args, **kwargs)


def _public_comment_target_path(conn, content_type, content_id):
    normalized_type = (content_type or '').strip()
    normalized_id = str(content_id or '').strip()
    if normalized_type == 'record':
        return f'/record/{normalized_id}'
    if normalized_type == 'daily_post':
        return f'/post/{normalized_id}'
    if normalized_type == 'blog_post':
        row = conn.execute('SELECT slug FROM blog_posts WHERE id = ?', (normalized_id,)).fetchone()
        if row and row['slug']:
            return f"/blog/{row['slug']}"
    if normalized_type == 'case_journey':
        row = conn.execute('SELECT slug FROM case_journeys WHERE id = ?', (normalized_id,)).fetchone()
        if row and row['slug']:
            return f"/case-journeys/{row['slug']}"
    return '/'


def _public_comment_admin_context(conn, q='', status_filter='pending', content_type_filter=''):
    normalized_status = (status_filter or 'pending').strip().lower()
    if normalized_status not in PUBLIC_COMMENT_STATUSES and normalized_status != 'all':
        normalized_status = 'pending'

    normalized_type = (content_type_filter or '').strip()
    if normalized_type and normalized_type not in PUBLIC_COMMENT_TYPES:
        normalized_type = ''

    search_term = (q or '').strip()
    sql = '''
        SELECT
            public_comments.*,
            public_users.display_name,
            public_users.email
        FROM public_comments
        JOIN public_users ON public_users.id = public_comments.public_user_id
        WHERE 1 = 1
    '''
    params = []
    if normalized_status != 'all':
        sql += ' AND public_comments.status = ?'
        params.append(normalized_status)
    if normalized_type:
        sql += ' AND public_comments.content_type = ?'
        params.append(normalized_type)
    if search_term:
        token = f'%{search_term}%'
        sql += '''
            AND (
                public_comments.body LIKE ?
                OR public_users.display_name LIKE ?
                OR public_users.email LIKE ?
            )
        '''
        params.extend([token, token, token])
    sql += '''
        ORDER BY
            CASE public_comments.status
                WHEN 'pending' THEN 0
                WHEN 'approved' THEN 1
                WHEN 'rejected' THEN 2
                ELSE 3
            END,
            COALESCE(public_comments.updated_at, public_comments.created_at) DESC,
            public_comments.id DESC
        LIMIT 250
    '''
    rows = conn.execute(sql, params).fetchall()
    summary_rows = conn.execute(
        '''
        SELECT status, COUNT(*) AS total
        FROM public_comments
        GROUP BY status
        '''
    ).fetchall()
    summary = {'total': 0, 'pending': 0, 'approved': 0, 'rejected': 0, 'spam': 0}
    for row in summary_rows:
        status = (row['status'] or '').strip()
        summary['total'] += int(row['total'] or 0)
        if status in summary:
            summary[status] = int(row['total'] or 0)

    comment_rows = []
    for row in rows:
        comment_rows.append(
            {
                **dict(row),
                'content_label': PUBLIC_COMMENT_TYPES.get(row['content_type'], row['content_type']),
                'target_path': _public_comment_target_path(conn, row['content_type'], row['content_id']),
            }
        )

    return {
        'rows': comment_rows,
        'summary': summary,
        'q': search_term,
        'status_filter': normalized_status,
        'content_type_filter': normalized_type,
        'content_types': [{'value': key, 'label': label} for key, label in PUBLIC_COMMENT_TYPES.items()],
    }


def _subscriber_email_hash(email):
    normalized = (email or '').strip().lower()
    if not normalized:
        return ''
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _table_columns(conn, table_name):
    try:
        return {row[1] for row in conn.execute(f'PRAGMA table_info({table_name})').fetchall()}
    except Exception:
        return set()


def _ensure_subscriber_schema(conn):
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS subscribers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            counties TEXT DEFAULT '',
            token TEXT NOT NULL,
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            source TEXT,
            notes TEXT
        )
        '''
    )
    conn.execute('CREATE INDEX IF NOT EXISTS idx_subscribers_active ON subscribers(active)')
    existing_columns = _table_columns(conn, 'subscribers')
    for col, definition in [
        ('updated_at', 'TEXT'),
        ('source', 'TEXT'),
        ('notes', 'TEXT'),
        ('phone', 'TEXT'),
        ('wants_notifications', 'INTEGER NOT NULL DEFAULT 0'),
        ('notification_channels', "TEXT NOT NULL DEFAULT 'email'"),
    ]:
        if col not in existing_columns:
            conn.execute(f'ALTER TABLE subscribers ADD COLUMN {col} {definition}')
            existing_columns.add(col)
    if 'updated_at' in existing_columns:
        conn.execute(
            '''
            UPDATE subscribers
            SET updated_at = COALESCE(NULLIF(updated_at, ''), created_at, datetime('now'))
            WHERE updated_at IS NULL OR updated_at = ''
            '''
        )

    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS subscribe_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            source TEXT,
            page_path TEXT,
            ip_hash TEXT,
            referrer TEXT,
            email_hash TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
        '''
    )
    conn.execute('CREATE INDEX IF NOT EXISTS idx_subscribe_events_created ON subscribe_events(created_at)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_subscribe_events_type ON subscribe_events(event_type)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_subscribe_events_source ON subscribe_events(source)')


def _record_subscribe_event(event_type, source='', page_path='', email=''):
    import app as _app_module
    _app_module._record_subscribe_event(event_type, source=source, page_path=page_path, email=email)


def _subscriber_counties_list(raw_value):
    return [part.strip() for part in (raw_value or '').split(',') if part.strip()]


def _subscriber_counties_label(raw_value):
    counties = _subscriber_counties_list(raw_value)
    return ', '.join(counties) if counties else 'All counties'


def _subscriber_admin_context(conn, q='', status_filter='active', county_filter='', source_filter=''):
    _ensure_subscriber_schema(conn)
    q = (q or '').strip()[:120]
    status_filter = (status_filter or 'active').strip().lower()
    if status_filter not in {'active', 'inactive', 'all'}:
        status_filter = 'active'
    county_filter = (county_filter or '').strip()[:80]
    source_filter = (source_filter or '').strip()[:80]

    subscriber_rows = conn.execute(
        '''
        SELECT
            s.id,
            s.email,
            s.counties,
            COALESCE(s.active, 1) AS active,
            s.created_at,
            COALESCE(s.updated_at, s.created_at) AS updated_at,
            COALESCE(NULLIF(s.source, ''), '(unknown)') AS source,
            COALESCE(s.notes, '') AS notes
        FROM subscribers s
        ORDER BY datetime(COALESCE(s.updated_at, s.created_at)) DESC, s.email ASC
        '''
    ).fetchall()

    rows = []
    counties_seen = set()
    sources_seen = set()
    for subscriber in subscriber_rows:
        email_hash = _subscriber_email_hash(subscriber['email'])
        last_event = conn.execute(
            '''
            SELECT event_type, source, created_at
            FROM subscribe_events
            WHERE email_hash = ?
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT 1
            ''',
            (email_hash,),
        ).fetchone()
        item = dict(subscriber)
        item['last_event_type'] = last_event['event_type'] if last_event else None
        item['last_event_source'] = last_event['source'] if last_event else None
        item['last_event_at'] = last_event['created_at'] if last_event else None
        item['counties_label'] = _subscriber_counties_label(item['counties'])
        item['county_list'] = _subscriber_counties_list(item['counties'])
        rows.append(item)
        for county in item['county_list']:
            counties_seen.add(county)
        sources_seen.add(item['source'])

    filtered = []
    for row in rows:
        if q:
            haystack = ' '.join([
                row['email'] or '',
                row['source'] or '',
                row['last_event_type'] or '',
                row['last_event_source'] or '',
                row['counties_label'] or '',
                row['notes'] or '',
            ]).lower()
            if q.lower() not in haystack:
                continue
        if status_filter == 'active' and not row['active']:
            continue
        if status_filter == 'inactive' and row['active']:
            continue
        if county_filter:
            if row['county_list'] and county_filter not in row['county_list']:
                continue
        if source_filter and row['source'] != source_filter:
            continue
        filtered.append(row)

    summary = {
        'total': len(rows),
        'active': sum(1 for row in rows if row['active']),
        'inactive': sum(1 for row in rows if not row['active']),
        'all_counties': sum(1 for row in rows if not row['counties']),
    }
    return {
        'rows': filtered,
        'summary': summary,
        'counties': sorted(counties_seen),
        'sources': sorted(source for source in sources_seen if source),
        'q': q,
        'status_filter': status_filter,
        'county_filter': county_filter,
        'source_filter': source_filter,
    }


def _normalize_email_ops_recipient(raw_value):
    email = (raw_value or '').strip().lower()
    if email and '@' in email:
        return email[:160]
    return ''


def _digest_support_email():
    for candidate in (
        getattr(current_user, 'email', ''),
        getattr(config, 'SMTP_USER', ''),
        getattr(config, 'EMAIL_USER', ''),
    ):
        email = _normalize_email_ops_recipient(candidate)
        if email:
            return email
    return ''


def _digest_target_date(raw_value=''):
    raw_value = (raw_value or '').strip()
    if not raw_value:
        return (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    try:
        return datetime.strptime(raw_value, '%Y-%m-%d').strftime('%Y-%m-%d')
    except ValueError as exc:
        raise ValueError('Choose a valid digest date in YYYY-MM-DD format.') from exc


def _digest_subject_for_date(target_date):
    try:
        display = datetime.strptime(target_date, '%Y-%m-%d').strftime('%b %d, %Y')
    except ValueError:
        display = target_date
    return f'Montana Blotter Briefing - {display}'


def _current_email_campaign_date():
    return datetime.now().strftime('%Y-%m-%d')


def _sponsor_announcement_defaults():
    return {
        'subject': 'Montana Blotter Update: Meet Our New Sponsor',
        'sponsor_name': 'New Montana Sponsor',
        'headline': 'A new sponsor is helping support statewide public-records coverage.',
        'body': (
            'We wanted to let you know about a new sponsor supporting Montana Blotter.\n\n'
            'Their support helps fund statewide blotter coverage, county-by-county directories, '
            'and the public-records tools we keep available to readers across Montana.'
        ),
        'cta_label': 'Learn more',
        'cta_url': '',
        'notes': '',
    }


def _normalize_multiline_text(raw_value, *, max_length):
    value = str(raw_value or '').replace('\r\n', '\n').replace('\r', '\n')
    lines = [line.rstrip() for line in value.split('\n')]
    return '\n'.join(lines).strip()[:max_length]


def _normalize_http_url(raw_value):
    value = (raw_value or '').strip()
    if not value:
        return ''
    parsed = urlparse(value)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        raise ValueError('Enter a valid sponsor CTA URL starting with http:// or https://.')
    return value[:500]


def _load_sponsor_announcement_draft(conn):
    defaults = _sponsor_announcement_defaults()
    return {
        'subject': _app_setting_text(
            SPONSOR_ANNOUNCEMENT_SETTING_KEYS['subject'],
            defaults['subject'],
            max_length=255,
            conn=conn,
        ),
        'sponsor_name': _app_setting_text(
            SPONSOR_ANNOUNCEMENT_SETTING_KEYS['sponsor_name'],
            defaults['sponsor_name'],
            max_length=120,
            conn=conn,
        ),
        'headline': _app_setting_text(
            SPONSOR_ANNOUNCEMENT_SETTING_KEYS['headline'],
            defaults['headline'],
            max_length=220,
            conn=conn,
        ),
        'body': _app_setting_text(
            SPONSOR_ANNOUNCEMENT_SETTING_KEYS['body'],
            defaults['body'],
            max_length=4000,
            conn=conn,
        ),
        'cta_label': _app_setting_text(
            SPONSOR_ANNOUNCEMENT_SETTING_KEYS['cta_label'],
            defaults['cta_label'],
            max_length=80,
            conn=conn,
        ),
        'cta_url': _app_setting_text(
            SPONSOR_ANNOUNCEMENT_SETTING_KEYS['cta_url'],
            defaults['cta_url'],
            max_length=500,
            conn=conn,
        ),
        'notes': _app_setting_text(
            SPONSOR_ANNOUNCEMENT_SETTING_KEYS['notes'],
            defaults['notes'],
            max_length=500,
            conn=conn,
        ),
    }


def _sponsor_announcement_from_form(form):
    defaults = _sponsor_announcement_defaults()
    draft = {
        'subject': ((form.get('sponsor_subject') or '').strip() or defaults['subject'])[:255],
        'sponsor_name': ((form.get('sponsor_name') or '').strip() or defaults['sponsor_name'])[:120],
        'headline': ((form.get('sponsor_headline') or '').strip() or defaults['headline'])[:220],
        'body': _normalize_multiline_text(
            form.get('sponsor_body') or defaults['body'],
            max_length=4000,
        ),
        'cta_label': ((form.get('sponsor_cta_label') or '').strip() or defaults['cta_label'])[:80],
        'cta_url': '',
        'notes': _normalize_multiline_text(form.get('sponsor_notes') or '', max_length=500),
    }
    cta_url = form.get('sponsor_cta_url') or ''
    draft['cta_url'] = _normalize_http_url(cta_url) if cta_url.strip() else ''
    if not draft['subject']:
        raise ValueError('Enter a sponsor email subject.')
    if not draft['sponsor_name']:
        raise ValueError('Enter the sponsor name.')
    if not draft['headline']:
        raise ValueError('Enter a sponsor email headline.')
    if not draft['body']:
        raise ValueError('Enter sponsor email body copy.')
    if draft['cta_url'] and not draft['cta_label']:
        raise ValueError('Enter a CTA label when a sponsor URL is provided.')
    return draft


def _save_sponsor_announcement_draft(conn, draft):
    for field, key in SPONSOR_ANNOUNCEMENT_SETTING_KEYS.items():
        _save_app_setting(conn, key, draft.get(field, ''))


def _build_sponsor_announcement_html(draft, unsubscribe_url=None):
    sponsor_name = escape(draft.get('sponsor_name') or 'Montana Sponsor')
    subject = escape(draft.get('subject') or _sponsor_announcement_defaults()['subject'])
    headline = escape(draft.get('headline') or '')
    body = draft.get('body') or ''
    body_sections = [
        section.strip()
        for section in body.replace('\r\n', '\n').replace('\r', '\n').split('\n\n')
        if section.strip()
    ]
    if not body_sections:
        body_sections = [body.strip() or _sponsor_announcement_defaults()['body']]
    body_html = ''.join(
        f"<p style=\"color:#334155;line-height:1.7;margin:0 0 14px;\">{escape(section).replace(chr(10), '<br>')}</p>"
        for section in body_sections
    )
    cta_html = ''
    cta_url = draft.get('cta_url') or ''
    cta_label = escape(draft.get('cta_label') or 'Learn more')
    if cta_url:
        cta_html = (
            f'<p style="margin:22px 0 0;">'
            f'<a href="{escape(cta_url)}" '
            f'style="display:inline-block;background:#1d4ed8;color:#ffffff;padding:12px 16px;border-radius:8px;text-decoration:none;font-weight:700;">'
            f'{cta_label}</a></p>'
        )

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:640px;margin:0 auto;background:#f8fafc;border:1px solid #e2e8f0;border-radius:18px;overflow:hidden;">
        <div style="background:#0f172a;color:#ffffff;padding:28px 28px 24px;">
            <p style="margin:0;color:#93c5fd;font-size:12px;font-weight:700;letter-spacing:0.16em;text-transform:uppercase;">Montana Blotter Sponsor Update</p>
            <h2 style="margin:14px 0 0;font-size:28px;line-height:1.15;">{headline}</h2>
            <p style="margin:14px 0 0;color:#cbd5e1;font-size:14px;line-height:1.6;">{subject}</p>
        </div>
        <div style="padding:28px;background:#ffffff;">
            <div style="display:inline-block;background:#eff6ff;color:#1d4ed8;padding:7px 12px;border-radius:999px;font-size:12px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;">
                New Sponsor
            </div>
            <p style="margin:16px 0 8px;color:#0f172a;font-size:18px;font-weight:700;">{sponsor_name}</p>
            {body_html}
            {cta_html}
            <hr style="border:none;border-top:1px solid #e2e8f0;margin:24px 0;">
            <p style="margin:0;color:#64748b;font-size:13px;line-height:1.6;">
                You are receiving this because you subscribed to Montana Blotter updates.
            </p>
            <p style="margin:10px 0 0;color:#64748b;font-size:13px;line-height:1.6;">
                Sponsor support helps fund statewide public-records access, county directories, and daily report coverage.
            </p>
    """
    if unsubscribe_url:
        html += (
            f'<p style="margin:14px 0 0;color:#94a3b8;font-size:12px;">'
            f'<a href="{escape(BASE_URL)}" style="color:#3b82f6;text-decoration:none;">montanablotter.com</a>'
            f' &mdash; <a href="{escape(unsubscribe_url)}" style="color:#94a3b8;">Unsubscribe</a>'
            f'</p>'
        )
    html += '</div></div>'
    return html


def _build_sponsor_announcement_preview(conn):
    draft = _load_sponsor_announcement_draft(conn)
    active_subscribers = conn.execute(
        'SELECT COUNT(*) AS total FROM subscribers WHERE COALESCE(active, 1) = 1'
    ).fetchone()['total']
    return {
        'draft': draft,
        'preview_html': _build_sponsor_announcement_html(draft),
        'active_subscribers': active_subscribers,
    }


def _record_digest_run(
    conn,
    *,
    kind,
    target_date,
    audience,
    status,
    subject,
    preview_posts=0,
    preview_subscribers=0,
    initiated_by='',
    notes='',
    created_by_user_id=None,
):
    cursor = conn.execute(
        '''
        INSERT INTO digest_runs (
            kind, target_date, audience, status, subject,
            preview_posts, preview_subscribers, initiated_by, notes,
            created_by_user_id, started_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ''',
        (
            (kind or '').strip()[:40] or 'morning_briefing',
            target_date,
            (audience or '').strip()[:40] or 'subscribers',
            (status or '').strip()[:40] or 'pending',
            (subject or '').strip()[:255],
            int(preview_posts or 0),
            int(preview_subscribers or 0),
            (initiated_by or '').strip()[:120] or None,
            (notes or '').strip()[:500] or None,
            created_by_user_id,
        ),
    )
    return cursor.lastrowid


def _record_digest_run_recipient(conn, run_id, recipient_email, counties, status, post_count=0, error_message=''):
    conn.execute(
        '''
        INSERT INTO digest_run_recipients (
            run_id, recipient_email, counties, status, post_count, error_message
        ) VALUES (?, ?, ?, ?, ?, ?)
        ''',
        (
            run_id,
            (recipient_email or '').strip().lower(),
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


def _build_email_ops_preview(target_date, selected_run_id=None):
    all_posts = get_morning_briefing_posts(target_date)
    preview_html = build_morning_briefing_html(all_posts, target_date) if all_posts else ''

    conn = get_db()
    _ensure_subscriber_schema(conn)
    subscribers = conn.execute(
        '''
        SELECT id, email, counties, created_at
        FROM subscribers
        WHERE active = 1
        ORDER BY datetime(created_at) DESC, email ASC
        '''
    ).fetchall()

    preview_rows = []
    matching_subscribers = 0
    skipped_subscribers = 0
    posts_cache = {'': all_posts}
    for subscriber in subscribers:
        counties_raw = subscriber['counties'] or ''
        if counties_raw not in posts_cache:
            posts_cache[counties_raw] = get_morning_briefing_posts(
                target_date,
                _subscriber_counties_list(counties_raw) or None,
            )
        post_count = len(posts_cache[counties_raw])
        if post_count:
            matching_subscribers += 1
        else:
            skipped_subscribers += 1
        preview_rows.append({
            'id': subscriber['id'],
            'email': subscriber['email'],
            'counties': counties_raw,
            'counties_label': _subscriber_counties_label(counties_raw),
            'post_count': post_count,
            'created_at': subscriber['created_at'],
        })

    stats = {
        'active_subscribers': conn.execute(
            'SELECT COUNT(*) AS total FROM subscribers WHERE active = 1'
        ).fetchone()['total'],
        'new_subscribers_7d': conn.execute(
            "SELECT COUNT(*) AS total FROM subscribers WHERE datetime(created_at) >= datetime('now', '-7 days')"
        ).fetchone()['total'],
        'unsubscribe_events_30d': conn.execute(
            "SELECT COUNT(*) AS total FROM subscribe_events WHERE event_type = 'unsubscribe' AND datetime(created_at) >= datetime('now', '-30 days')"
        ).fetchone()['total'],
        'subscription_events_30d': conn.execute(
            "SELECT COUNT(*) AS total FROM subscribe_events WHERE event_type IN ('subscribe_success', 'subscribe_update') AND datetime(created_at) >= datetime('now', '-30 days')"
        ).fetchone()['total'],
        'failed_deliveries_30d': conn.execute(
            "SELECT COUNT(*) AS total FROM digest_run_recipients WHERE status = 'failed' AND datetime(created_at) >= datetime('now', '-30 days')"
        ).fetchone()['total'],
    }
    recent_runs = conn.execute(
        '''
        SELECT
            dr.id,
            dr.kind,
            dr.target_date,
            dr.audience,
            dr.status,
            dr.subject,
            dr.preview_posts,
            dr.preview_subscribers,
            dr.sent_count,
            dr.skipped_count,
            dr.failed_count,
            dr.initiated_by,
            dr.created_at,
            u.username AS created_by_username
        FROM digest_runs dr
        LEFT JOIN users u ON u.id = dr.created_by_user_id
        ORDER BY datetime(dr.created_at) DESC, dr.id DESC
        LIMIT 20
        '''
    ).fetchall()
    recent_events = conn.execute(
        '''
        SELECT event_type, source, page_path, created_at
        FROM subscribe_events
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT 30
        '''
    ).fetchall()
    selected_run = None
    selected_run_recipients = []
    if selected_run_id:
        selected_run = conn.execute(
            '''
            SELECT
                dr.id,
                dr.kind,
                dr.target_date,
                dr.audience,
                dr.status,
                dr.subject,
                dr.preview_posts,
                dr.preview_subscribers,
                dr.sent_count,
                dr.skipped_count,
                dr.failed_count,
                dr.initiated_by,
                dr.notes,
                dr.started_at,
                dr.finished_at,
                dr.created_at,
                u.username AS created_by_username
            FROM digest_runs dr
            LEFT JOIN users u ON u.id = dr.created_by_user_id
            WHERE dr.id = ?
            ''',
            (int(selected_run_id),),
        ).fetchone()
        if selected_run:
            selected_run_recipients = conn.execute(
                '''
                SELECT recipient_email, counties, status, post_count, error_message, created_at
                FROM digest_run_recipients
                WHERE run_id = ?
                ORDER BY
                    CASE status
                        WHEN 'failed' THEN 0
                        WHEN 'skipped' THEN 1
                        ELSE 2
                    END,
                    datetime(created_at) DESC,
                    id DESC
                LIMIT 150
                ''',
                (int(selected_run_id),),
            ).fetchall()
    recent_failures = conn.execute(
        '''
        SELECT
            drr.run_id,
            drr.recipient_email,
            drr.counties,
            drr.post_count,
            drr.error_message,
            drr.created_at,
            dr.target_date,
            dr.audience,
            dr.status AS run_status
        FROM digest_run_recipients drr
        JOIN digest_runs dr ON dr.id = drr.run_id
        WHERE drr.status = 'failed'
        ORDER BY datetime(drr.created_at) DESC, drr.id DESC
        LIMIT 40
        '''
    ).fetchall()
    conn.close()

    unsubscribe_rate = 0.0
    if stats['subscription_events_30d']:
        unsubscribe_rate = (
            stats['unsubscribe_events_30d'] / stats['subscription_events_30d'] * 100.0
        )

    return {
        'target_date': target_date,
        'subject': _digest_subject_for_date(target_date),
        'all_posts': all_posts,
        'preview_html': preview_html,
        'preview_rows': preview_rows[:30],
        'matching_subscribers': matching_subscribers,
        'skipped_subscribers': skipped_subscribers,
        'stats': stats,
        'unsubscribe_rate_30d': unsubscribe_rate,
        'recent_runs': recent_runs,
        'recent_events': recent_events,
        'selected_run': selected_run,
        'selected_run_recipients': selected_run_recipients,
        'recent_failures': recent_failures,
    }


def _send_test_digest(target_date, recipient_email, initiated_by):
    if not recipient_email:
        raise ValueError('No valid test recipient email is configured for this admin account.')

    preview = _build_email_ops_preview(target_date)
    if not preview['all_posts']:
        raise ValueError(f'No posts are available for {target_date}.')

    conn = get_db()
    run_id = _record_digest_run(
        conn,
        kind='morning_briefing',
        target_date=target_date,
        audience='test',
        status='running',
        subject=f"[TEST] {preview['subject']}",
        preview_posts=len(preview['all_posts']),
        preview_subscribers=1,
        initiated_by=initiated_by,
        notes='Manual test send from admin email ops.',
        created_by_user_id=current_user.id if current_user.is_authenticated else None,
    )
    conn.commit()
    try:
        _send_digest_email(
            recipient_email,
            f"[TEST] {preview['subject']}",
            preview['preview_html'],
        )
        _record_digest_run_recipient(
            conn,
            run_id,
            recipient_email,
            '',
            'sent',
            post_count=len(preview['all_posts']),
        )
        _finish_digest_run(conn, run_id, status='completed', sent_count=1)
        _log_admin_action(
            'email_ops.test_sent',
            target_type='digest_run',
            target_id=run_id,
            metadata={'target_date': target_date, 'recipient': recipient_email},
            conn=conn,
        )
        conn.commit()
    except Exception as exc:
        _record_digest_run_recipient(
            conn,
            run_id,
            recipient_email,
            '',
            'failed',
            post_count=len(preview['all_posts']),
            error_message=str(exc),
        )
        _finish_digest_run(conn, run_id, status='failed', failed_count=1, notes=str(exc))
        conn.commit()
        conn.close()
        raise
    conn.close()


def _send_digest_to_active_subscribers(target_date, initiated_by):
    preview = _build_email_ops_preview(target_date)
    if not preview['all_posts']:
        raise ValueError(f'No posts are available for {target_date}.')

    conn = get_db()
    subscribers = conn.execute(
        '''
        SELECT email, counties, token
        FROM subscribers
        WHERE active = 1
        ORDER BY datetime(created_at) DESC, email ASC
        '''
    ).fetchall()
    run_id = _record_digest_run(
        conn,
        kind='morning_briefing',
        target_date=target_date,
        audience='subscribers',
        status='running',
        subject=preview['subject'],
        preview_posts=len(preview['all_posts']),
        preview_subscribers=len(subscribers),
        initiated_by=initiated_by,
        notes='Manual subscriber send from admin email ops.',
        created_by_user_id=current_user.id if current_user.is_authenticated else None,
    )
    conn.commit()

    sent_count = 0
    skipped_count = 0
    failed_count = 0
    posts_cache = {'': preview['all_posts']}
    try:
        for subscriber in subscribers:
            counties_raw = subscriber['counties'] or ''
            if counties_raw not in posts_cache:
                posts_cache[counties_raw] = get_morning_briefing_posts(
                    target_date,
                    _subscriber_counties_list(counties_raw) or None,
                )
            subscriber_posts = posts_cache[counties_raw]
            if not subscriber_posts:
                skipped_count += 1
                _record_digest_run_recipient(
                    conn,
                    run_id,
                    subscriber['email'],
                    counties_raw,
                    'skipped',
                    post_count=0,
                    error_message='No matching posts for subscriber counties.',
                )
                continue

            unsubscribe_url = f"{BASE_URL}/unsubscribe?token={subscriber['token']}"
            html = build_morning_briefing_html(
                subscriber_posts,
                target_date,
                unsubscribe_url=unsubscribe_url,
            )
            try:
                _send_digest_email(subscriber['email'], preview['subject'], html)
                sent_count += 1
                _record_digest_run_recipient(
                    conn,
                    run_id,
                    subscriber['email'],
                    counties_raw,
                    'sent',
                    post_count=len(subscriber_posts),
                )
            except Exception as exc:
                failed_count += 1
                _record_digest_run_recipient(
                    conn,
                    run_id,
                    subscriber['email'],
                    counties_raw,
                    'failed',
                    post_count=len(subscriber_posts),
                    error_message=str(exc),
                )

        final_status = 'completed'
        if failed_count and sent_count:
            final_status = 'completed_with_errors'
        elif failed_count and not sent_count:
            final_status = 'failed'
        _finish_digest_run(
            conn,
            run_id,
            status=final_status,
            sent_count=sent_count,
            skipped_count=skipped_count,
            failed_count=failed_count,
        )
        _log_admin_action(
            'email_ops.send_now',
            target_type='digest_run',
            target_id=run_id,
            metadata={
                'target_date': target_date,
                'sent_count': sent_count,
                'skipped_count': skipped_count,
                'failed_count': failed_count,
            },
            conn=conn,
        )
        conn.commit()
    except Exception:
        _finish_digest_run(conn, run_id, status='failed', failed_count=failed_count, notes='Unexpected send error.')
        conn.commit()
        conn.close()
        raise
    conn.close()
    return {'sent_count': sent_count, 'skipped_count': skipped_count, 'failed_count': failed_count}


def _retry_failed_digest_recipients(original_run_id, initiated_by):
    conn = get_db()
    original_run = conn.execute(
        '''
        SELECT id, target_date, subject, failed_count
        FROM digest_runs
        WHERE id = ?
        ''',
        (int(original_run_id),),
    ).fetchone()
    if not original_run:
        conn.close()
        raise ValueError('Digest run not found.')
    if int(original_run['failed_count'] or 0) <= 0:
        conn.close()
        raise ValueError('This digest run has no failed recipients to retry.')

    failed_rows = conn.execute(
        '''
        SELECT DISTINCT lower(recipient_email) AS recipient_email
        FROM digest_run_recipients
        WHERE run_id = ? AND status = 'failed'
        ORDER BY lower(recipient_email) ASC
        ''',
        (int(original_run_id),),
    ).fetchall()
    if not failed_rows:
        conn.close()
        raise ValueError('No failed recipients were recorded for this digest run.')

    preview = _build_email_ops_preview(original_run['target_date'])
    if not preview['all_posts']:
        conn.close()
        raise ValueError(f"No posts are available for {original_run['target_date']}.")

    target_emails = [row['recipient_email'] for row in failed_rows if row['recipient_email']]
    subscribers = []
    if target_emails:
        placeholders = ','.join('?' * len(target_emails))
        subscribers = conn.execute(
            f'''
            SELECT email, counties, token, active
            FROM subscribers
            WHERE lower(email) IN ({placeholders})
            ORDER BY datetime(created_at) DESC, email ASC
            ''',
            target_emails,
        ).fetchall()
    subscriber_map = {str(row['email']).strip().lower(): row for row in subscribers}

    actor_user_id = getattr(current_user, 'id', None) if getattr(current_user, 'is_authenticated', False) else None
    run_id = _record_digest_run(
        conn,
        kind='morning_briefing',
        target_date=original_run['target_date'],
        audience='retry_failed',
        status='running',
        subject=original_run['subject'],
        preview_posts=len(preview['all_posts']),
        preview_subscribers=len(target_emails),
        initiated_by=initiated_by,
        notes=f"Retry failed recipients from digest run #{int(original_run_id)}.",
        created_by_user_id=actor_user_id,
    )
    run_notes = f"Retry failed recipients from digest run #{int(original_run_id)}."
    conn.commit()

    sent_count = 0
    skipped_count = 0
    failed_count = 0
    posts_cache = {'': preview['all_posts']}
    try:
        for row in failed_rows:
            recipient_email = row['recipient_email']
            subscriber = subscriber_map.get(recipient_email)
            if not subscriber or not int(subscriber['active'] or 0):
                skipped_count += 1
                _record_digest_run_recipient(
                    conn,
                    run_id,
                    recipient_email,
                    '',
                    'skipped',
                    post_count=0,
                    error_message='Subscriber inactive or missing.',
                )
                continue

            counties_raw = subscriber['counties'] or ''
            if counties_raw not in posts_cache:
                posts_cache[counties_raw] = get_morning_briefing_posts(
                    original_run['target_date'],
                    _subscriber_counties_list(counties_raw) or None,
                )
            subscriber_posts = posts_cache[counties_raw]
            if not subscriber_posts:
                skipped_count += 1
                _record_digest_run_recipient(
                    conn,
                    run_id,
                    recipient_email,
                    counties_raw,
                    'skipped',
                    post_count=0,
                    error_message='No matching posts for subscriber counties.',
                )
                continue

            unsubscribe_url = f"{BASE_URL}/unsubscribe?token={subscriber['token']}"
            html = build_morning_briefing_html(
                subscriber_posts,
                original_run['target_date'],
                unsubscribe_url=unsubscribe_url,
            )
            try:
                _send_digest_email(recipient_email, original_run['subject'], html)
                sent_count += 1
                _record_digest_run_recipient(
                    conn,
                    run_id,
                    recipient_email,
                    counties_raw,
                    'sent',
                    post_count=len(subscriber_posts),
                )
            except Exception as exc:
                failed_count += 1
                _record_digest_run_recipient(
                    conn,
                    run_id,
                    recipient_email,
                    counties_raw,
                    'failed',
                    post_count=len(subscriber_posts),
                    error_message=str(exc),
                )

        final_status = 'completed'
        if failed_count and sent_count:
            final_status = 'completed_with_errors'
        elif failed_count and not sent_count:
            final_status = 'failed'
        _finish_digest_run(
            conn,
            run_id,
            status=final_status,
            sent_count=sent_count,
            skipped_count=skipped_count,
            failed_count=failed_count,
            notes=run_notes,
        )
        _log_admin_action(
            'email_ops.retry_failed',
            target_type='digest_run',
            target_id=run_id,
            metadata={
                'original_run_id': int(original_run_id),
                'target_date': original_run['target_date'],
                'sent_count': sent_count,
                'skipped_count': skipped_count,
                'failed_count': failed_count,
            },
            conn=conn,
        )
        conn.commit()
    except Exception:
        _finish_digest_run(conn, run_id, status='failed', failed_count=failed_count, notes=run_notes)
        conn.commit()
        conn.close()
        raise
    conn.close()
    return {
        'run_id': run_id,
        'target_date': original_run['target_date'],
        'sent_count': sent_count,
        'skipped_count': skipped_count,
        'failed_count': failed_count,
    }


def _send_sponsor_announcement_test(recipient_email, draft, initiated_by):
    if not recipient_email:
        raise ValueError('No valid test recipient email is configured for this admin account.')

    conn = get_db()
    _ensure_subscriber_schema(conn)
    run_id = _record_digest_run(
        conn,
        kind='sponsor_announcement',
        target_date=_current_email_campaign_date(),
        audience='test',
        status='running',
        subject=f"[TEST] {draft['subject']}",
        preview_posts=0,
        preview_subscribers=1,
        initiated_by=initiated_by,
        notes=f"Manual sponsor announcement test for {draft['sponsor_name']}.",
        created_by_user_id=current_user.id if current_user.is_authenticated else None,
    )
    conn.commit()
    try:
        _send_digest_email(
            recipient_email,
            f"[TEST] {draft['subject']}",
            _build_sponsor_announcement_html(draft),
        )
        _record_digest_run_recipient(
            conn,
            run_id,
            recipient_email,
            '',
            'sent',
            post_count=0,
        )
        _finish_digest_run(conn, run_id, status='completed', sent_count=1)
        _log_admin_action(
            'email_ops.sponsor_test_sent',
            target_type='digest_run',
            target_id=run_id,
            metadata={'recipient': recipient_email, 'sponsor_name': draft['sponsor_name']},
            conn=conn,
        )
        conn.commit()
    except Exception as exc:
        _record_digest_run_recipient(
            conn,
            run_id,
            recipient_email,
            '',
            'failed',
            post_count=0,
            error_message=str(exc),
        )
        _finish_digest_run(conn, run_id, status='failed', failed_count=1, notes=str(exc))
        conn.commit()
        conn.close()
        raise
    conn.close()


def _send_sponsor_announcement_to_active_subscribers(draft, initiated_by):
    conn = get_db()
    _ensure_subscriber_schema(conn)
    subscribers = conn.execute(
        '''
        SELECT email, token
        FROM subscribers
        WHERE COALESCE(active, 1) = 1
        ORDER BY datetime(created_at) DESC, email ASC
        '''
    ).fetchall()
    if not subscribers:
        conn.close()
        raise ValueError('No active subscribers are available for a sponsor announcement.')

    run_id = _record_digest_run(
        conn,
        kind='sponsor_announcement',
        target_date=_current_email_campaign_date(),
        audience='subscribers',
        status='running',
        subject=draft['subject'],
        preview_posts=0,
        preview_subscribers=len(subscribers),
        initiated_by=initiated_by,
        notes=f"Manual sponsor announcement send for {draft['sponsor_name']}.",
        created_by_user_id=current_user.id if current_user.is_authenticated else None,
    )
    conn.commit()

    sent_count = 0
    failed_count = 0
    try:
        for subscriber in subscribers:
            unsubscribe_url = f"{BASE_URL}/unsubscribe?token={subscriber['token']}"
            html = _build_sponsor_announcement_html(draft, unsubscribe_url=unsubscribe_url)
            try:
                _send_digest_email(subscriber['email'], draft['subject'], html)
                sent_count += 1
                _record_digest_run_recipient(
                    conn,
                    run_id,
                    subscriber['email'],
                    '',
                    'sent',
                    post_count=0,
                )
            except Exception as exc:
                failed_count += 1
                _record_digest_run_recipient(
                    conn,
                    run_id,
                    subscriber['email'],
                    '',
                    'failed',
                    post_count=0,
                    error_message=str(exc),
                )

        final_status = 'completed'
        if failed_count and sent_count:
            final_status = 'completed_with_errors'
        elif failed_count and not sent_count:
            final_status = 'failed'
        _finish_digest_run(
            conn,
            run_id,
            status=final_status,
            sent_count=sent_count,
            skipped_count=0,
            failed_count=failed_count,
            notes=f"Sponsor announcement send for {draft['sponsor_name']}.",
        )
        _log_admin_action(
            'email_ops.sponsor_send_now',
            target_type='digest_run',
            target_id=run_id,
            metadata={
                'sponsor_name': draft['sponsor_name'],
                'sent_count': sent_count,
                'failed_count': failed_count,
            },
            conn=conn,
        )
        conn.commit()
    except Exception:
        _finish_digest_run(
            conn,
            run_id,
            status='failed',
            sent_count=sent_count,
            failed_count=failed_count,
            notes=f"Sponsor announcement send for {draft['sponsor_name']}.",
        )
        conn.commit()
        conn.close()
        raise
    conn.close()
    return {'sent_count': sent_count, 'failed_count': failed_count}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@admin_bp.route('/audience/subscribers')
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_subscribers():
    conn = get_db()
    context = _subscriber_admin_context(
        conn,
        q=request.args.get('q'),
        status_filter=request.args.get('status'),
        county_filter=request.args.get('county'),
        source_filter=request.args.get('source'),
    )
    conn.close()
    return render_template('admin_subscribers.html', **context)


@admin_bp.route('/audience/alerts')
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_alerts():
    conn = get_db()
    q = (request.args.get('q') or '').strip().lower()
    county_filter = (request.args.get('county') or '').strip()
    status_filter = request.args.get('status', 'active')

    # Summary stats
    summary = conn.execute('''
        SELECT
            COUNT(*) AS total_subs,
            SUM(active) AS active_subs,
            COUNT(DISTINCT county) AS counties_covered,
            (SELECT COUNT(*) FROM name_watches WHERE active=1) AS active_watches,
            (SELECT COUNT(*) FROM name_watches) AS total_watches
        FROM alert_subscriptions
    ''').fetchone()

    # County breakdown
    county_counts = conn.execute('''
        SELECT county, COUNT(*) as total, SUM(active) as active
        FROM alert_subscriptions
        GROUP BY county
        ORDER BY active DESC, total DESC
    ''').fetchall()

    # Subscriber rows with filters
    where_clauses = []
    params = []
    if status_filter == 'active':
        where_clauses.append('a.active = 1')
    elif status_filter == 'inactive':
        where_clauses.append('a.active = 0')
    if county_filter:
        where_clauses.append('a.county LIKE ?')
        params.append(f'%{county_filter}%')
    if q:
        where_clauses.append('(lower(a.email) LIKE ? OR lower(a.county) LIKE ?)')
        params.extend([f'%{q}%', f'%{q}%'])

    where_sql = ('WHERE ' + ' AND '.join(where_clauses)) if where_clauses else ''
    subs = conn.execute(
        f'''SELECT a.id, a.email, a.county, a.alert_types, a.active,
                   a.source, a.last_alerted_at, a.created_at
            FROM alert_subscriptions a
            {where_sql}
            ORDER BY a.created_at DESC
            LIMIT 200''',
        params,
    ).fetchall()

    # Name watch rows
    watches = conn.execute(
        '''SELECT id, email, watch_name, county, active, last_alerted_at, created_at
           FROM name_watches
           ORDER BY created_at DESC LIMIT 200'''
    ).fetchall()

    conn.close()
    return render_template(
        'admin_alerts.html',
        summary=summary,
        county_counts=county_counts,
        subs=subs,
        watches=watches,
        q=q,
        county_filter=county_filter,
        status_filter=status_filter,
        current_year=datetime.now().year,
    )


@admin_bp.route('/audience/alerts/<int:sub_id>/deactivate', methods=['POST'])
@login_required
@require_role(*AUDIENCE_MANAGEMENT_ROLES)
def admin_alert_deactivate(sub_id):
    conn = get_db()
    conn.execute('UPDATE alert_subscriptions SET active=0 WHERE id=?', (sub_id,))
    conn.commit()
    conn.close()
    flash('Alert subscription deactivated.', 'success')
    return redirect(url_for('admin.admin_alerts'))


@admin_bp.route('/audience/alerts/watch/<int:watch_id>/deactivate', methods=['POST'])
@login_required
@require_role(*AUDIENCE_MANAGEMENT_ROLES)
def admin_watch_deactivate(watch_id):
    conn = get_db()
    conn.execute('UPDATE name_watches SET active=0 WHERE id=?', (watch_id,))
    conn.commit()
    conn.close()
    flash('Name watch deactivated.', 'success')
    return redirect(url_for('admin.admin_alerts'))


@admin_bp.route('/audience/comments')
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_comments():
    conn = get_db()
    context = _public_comment_admin_context(
        conn,
        q=request.args.get('q'),
        status_filter=request.args.get('status'),
        content_type_filter=request.args.get('content_type'),
    )
    conn.close()
    return render_template('admin_comments.html', **context)


@admin_bp.route('/audience/comments/<int:comment_id>/status', methods=['POST'])
@login_required
@require_role(*AUDIENCE_MANAGEMENT_ROLES)
def admin_comment_status(comment_id):
    new_status = (request.form.get('status') or '').strip().lower()
    moderation_note = (request.form.get('moderation_note') or '').strip()[:1000]
    if new_status not in PUBLIC_COMMENT_STATUSES:
        flash('Invalid comment status.', 'error')
        return redirect(url_for('admin.admin_comments'))

    conn = get_db()
    comment = conn.execute(
        '''
        SELECT
            public_comments.*,
            public_users.email
        FROM public_comments
        JOIN public_users ON public_users.id = public_comments.public_user_id
        WHERE public_comments.id = ?
        ''',
        (comment_id,),
    ).fetchone()
    if not comment:
        conn.close()
        flash('Comment not found.', 'error')
        return redirect(url_for('admin.admin_comments'))

    conn.execute(
        '''
        UPDATE public_comments
        SET status = ?, moderation_note = ?, updated_at = datetime('now')
        WHERE id = ?
        ''',
        (new_status, moderation_note, comment_id),
    )
    _log_admin_action(
        'public_comment.status_changed',
        target_type='public_comment',
        target_id=comment_id,
        metadata={
            'from': comment['status'],
            'to': new_status,
            'content_type': comment['content_type'],
            'content_id': comment['content_id'],
            'email': comment['email'],
        },
        conn=conn,
    )
    conn.commit()
    conn.close()
    flash(f'Comment marked {new_status}.', 'success')
    return redirect(url_for('admin.admin_comments'))


@admin_bp.route('/audience/subscribers/<int:subscriber_id>/status', methods=['POST'])
@login_required
@require_role(*AUDIENCE_MANAGEMENT_ROLES)
def admin_subscriber_status(subscriber_id):
    requested = (request.form.get('active') or '').strip()
    new_active = 1 if requested == '1' else 0
    conn = get_db()
    _ensure_subscriber_schema(conn)
    subscriber = conn.execute(
        'SELECT id, email, counties, COALESCE(active, 1) AS active FROM subscribers WHERE id = ?',
        (subscriber_id,),
    ).fetchone()
    if not subscriber:
        conn.close()
        flash('Subscriber not found.', 'error')
        return redirect(url_for('admin.admin_subscribers'))
    conn.execute(
        'UPDATE subscribers SET active = ?, updated_at = datetime(\'now\') WHERE id = ?',
        (new_active, subscriber_id),
    )
    _log_admin_action(
        'subscriber.status_changed',
        target_type='subscriber',
        target_id=subscriber_id,
        metadata={'email': subscriber['email'], 'from': subscriber['active'], 'to': new_active},
        conn=conn,
    )
    conn.commit()
    conn.close()
    _record_subscribe_event(
        'admin_reactivated' if new_active else 'admin_suppressed',
        source='admin_subscribers',
        page_path=request.path,
        email=subscriber['email'],
    )
    flash(f"{'Reactivated' if new_active else 'Suppressed'} {subscriber['email']}.", 'success')
    return redirect(url_for('admin.admin_subscribers'))


@admin_bp.route('/audience/subscribers/<int:subscriber_id>/counties', methods=['POST'])
@login_required
@require_role(*AUDIENCE_MANAGEMENT_ROLES)
def admin_subscriber_counties(subscriber_id):
    raw_counties = (request.form.get('counties') or '').strip()
    counties = [part.strip() for part in raw_counties.split(',') if part.strip()]
    counties_value = ','.join(dict.fromkeys(counties))
    conn = get_db()
    _ensure_subscriber_schema(conn)
    subscriber = conn.execute(
        'SELECT id, email, counties FROM subscribers WHERE id = ?',
        (subscriber_id,),
    ).fetchone()
    if not subscriber:
        conn.close()
        flash('Subscriber not found.', 'error')
        return redirect(url_for('admin.admin_subscribers'))
    conn.execute(
        'UPDATE subscribers SET counties = ?, updated_at = datetime(\'now\') WHERE id = ?',
        (counties_value, subscriber_id),
    )
    _log_admin_action(
        'subscriber.counties_updated',
        target_type='subscriber',
        target_id=subscriber_id,
        metadata={'email': subscriber['email'], 'from': subscriber['counties'], 'to': counties_value},
        conn=conn,
    )
    conn.commit()
    conn.close()
    flash(f'Updated counties for {subscriber["email"]}.', 'success')
    return redirect(url_for('admin.admin_subscribers'))


@admin_bp.route('/audience/subscribers/<int:subscriber_id>/notes', methods=['POST'])
@login_required
@require_role(*AUDIENCE_MANAGEMENT_ROLES)
def admin_subscriber_notes(subscriber_id):
    notes = (request.form.get('notes') or '').strip()[:2000]
    conn = get_db()
    _ensure_subscriber_schema(conn)
    subscriber = conn.execute(
        'SELECT id, email, COALESCE(notes, \'\') AS notes FROM subscribers WHERE id = ?',
        (subscriber_id,),
    ).fetchone()
    if not subscriber:
        conn.close()
        flash('Subscriber not found.', 'error')
        return redirect(url_for('admin.admin_subscribers'))
    conn.execute(
        'UPDATE subscribers SET notes = ?, updated_at = datetime(\'now\') WHERE id = ?',
        (notes, subscriber_id),
    )
    _log_admin_action(
        'subscriber.notes_updated',
        target_type='subscriber',
        target_id=subscriber_id,
        metadata={'email': subscriber['email'], 'had_notes': bool(subscriber['notes']), 'has_notes': bool(notes)},
        conn=conn,
    )
    conn.commit()
    conn.close()
    flash(f'Updated notes for {subscriber["email"]}.', 'success')
    return redirect(url_for('admin.admin_subscribers'))


@admin_bp.route('/audience/subscribers/export.csv')
@login_required
@require_role(*AUDIENCE_MANAGEMENT_ROLES)
def admin_subscribers_export():
    conn = get_db()
    context = _subscriber_admin_context(
        conn,
        q=request.args.get('q'),
        status_filter=request.args.get('status'),
        county_filter=request.args.get('county'),
        source_filter=request.args.get('source'),
    )
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow([
        'id',
        'email',
        'active',
        'counties',
        'source',
        'notes',
        'created_at',
        'updated_at',
        'last_event_type',
        'last_event_source',
        'last_event_at',
    ])
    for row in context['rows']:
        writer.writerow([
            row['id'],
            row['email'],
            int(row['active']),
            row['counties'],
            row['source'],
            row['notes'],
            row['created_at'],
            row['updated_at'],
            row['last_event_type'] or '',
            row['last_event_source'] or '',
            row['last_event_at'] or '',
        ])
    conn.close()

    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=subscribers.csv'},
    )


@admin_bp.route('/audience/email-ops')
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_email_ops():
    selected_run_id = None
    try:
        target_date = _digest_target_date(request.args.get('target_date'))
    except ValueError as exc:
        flash(str(exc), 'error')
        target_date = _digest_target_date('')
    run_id_raw = (request.args.get('run_id') or '').strip()
    if run_id_raw:
        try:
            selected_run_id = int(run_id_raw)
            if selected_run_id <= 0:
                raise ValueError
        except ValueError:
            flash('Choose a valid digest run to inspect.', 'error')
            selected_run_id = None

    preview = _build_email_ops_preview(target_date, selected_run_id=selected_run_id)
    test_recipient = _digest_support_email()
    conn = get_db()
    _ensure_subscriber_schema(conn)
    sponsor_announcement = _build_sponsor_announcement_preview(conn)
    conn.close()
    return render_template(
        'admin_email_ops.html',
        target_date=target_date,
        preview=preview,
        test_recipient=test_recipient,
        sponsor_announcement=sponsor_announcement,
        can_send_email_ops=current_user.role in EMAIL_OPS_SEND_ROLES,
    )


@admin_bp.route('/audience/email-ops/send-test', methods=['POST'])
@login_required
@require_role(*EMAIL_OPS_SEND_ROLES)
def admin_email_ops_send_test():
    try:
        target_date = _digest_target_date(request.form.get('target_date'))
        custom_recipient = _normalize_email_ops_recipient(request.form.get('recipient_email'))
        if (request.form.get('recipient_email') or '').strip() and not custom_recipient:
            raise ValueError('Enter a valid test recipient email.')
        recipient_email = custom_recipient or _digest_support_email()
        _send_test_digest(
            target_date,
            recipient_email=recipient_email,
            initiated_by=current_user.username,
        )
        flash(f'Test digest sent to {recipient_email}.', 'success')
    except ValueError as exc:
        flash(str(exc), 'error')
    except Exception as exc:
        flash(f'Test digest failed: {str(exc)[:200]}', 'error')
    return redirect(url_for('admin.admin_email_ops', target_date=request.form.get('target_date') or ''))


@admin_bp.route('/audience/email-ops/send-now', methods=['POST'])
@login_required
@require_role(*EMAIL_OPS_SEND_ROLES)
def admin_email_ops_send_now():
    try:
        target_date = _digest_target_date(request.form.get('target_date'))
        results = _send_digest_to_active_subscribers(
            target_date,
            initiated_by=current_user.username,
        )
        flash(
            f"Digest send complete: {results['sent_count']} sent, "
            f"{results['skipped_count']} skipped, {results['failed_count']} failed.",
            'success' if results['failed_count'] == 0 else 'warning',
        )
    except ValueError as exc:
        flash(str(exc), 'error')
    except Exception as exc:
        flash(f'Digest send failed: {str(exc)[:200]}', 'error')
    return redirect(url_for('admin.admin_email_ops', target_date=request.form.get('target_date') or ''))


@admin_bp.route('/audience/email-ops/sponsor-draft', methods=['POST'])
@login_required
@require_role(*EMAIL_OPS_SEND_ROLES)
def admin_email_ops_sponsor_draft():
    conn = get_db()
    try:
        draft = _sponsor_announcement_from_form(request.form)
        _save_sponsor_announcement_draft(conn, draft)
        _log_admin_action(
            'email_ops.sponsor_draft_saved',
            target_type='app_settings',
            metadata={
                'sponsor_name': draft['sponsor_name'],
                'has_cta_url': bool(draft['cta_url']),
            },
            conn=conn,
        )
        conn.commit()
        flash('Sponsor announcement draft saved.', 'success')
    except ValueError as exc:
        conn.rollback()
        flash(str(exc), 'error')
    except Exception as exc:
        conn.rollback()
        flash(f'Could not save sponsor draft: {str(exc)[:200]}', 'error')
    finally:
        conn.close()
    return redirect(url_for('admin.admin_email_ops', target_date=request.form.get('target_date') or ''))


@admin_bp.route('/audience/email-ops/sponsor-test', methods=['POST'])
@login_required
@require_role(*EMAIL_OPS_SEND_ROLES)
def admin_email_ops_sponsor_test():
    conn = get_db()
    try:
        draft = _sponsor_announcement_from_form(request.form)
        _save_sponsor_announcement_draft(conn, draft)
        conn.commit()
    except ValueError as exc:
        conn.rollback()
        conn.close()
        flash(str(exc), 'error')
        return redirect(url_for('admin.admin_email_ops', target_date=request.form.get('target_date') or ''))
    except Exception as exc:
        conn.rollback()
        conn.close()
        flash(f'Could not load sponsor email draft: {str(exc)[:200]}', 'error')
        return redirect(url_for('admin.admin_email_ops', target_date=request.form.get('target_date') or ''))
    finally:
        try:
            conn.close()
        except Exception:
            pass

    try:
        custom_recipient = _normalize_email_ops_recipient(request.form.get('recipient_email'))
        if (request.form.get('recipient_email') or '').strip() and not custom_recipient:
            raise ValueError('Enter a valid test recipient email.')
        recipient_email = custom_recipient or _digest_support_email()
        _send_sponsor_announcement_test(
            recipient_email,
            draft=draft,
            initiated_by=current_user.username,
        )
        flash(f'Sponsor test email sent to {recipient_email}.', 'success')
    except ValueError as exc:
        flash(str(exc), 'error')
    except Exception as exc:
        flash(f'Sponsor test send failed: {str(exc)[:200]}', 'error')
    return redirect(url_for('admin.admin_email_ops', target_date=request.form.get('target_date') or ''))


@admin_bp.route('/audience/email-ops/sponsor-send-now', methods=['POST'])
@login_required
@require_role(*EMAIL_OPS_SEND_ROLES)
def admin_email_ops_sponsor_send_now():
    conn = get_db()
    try:
        draft = _sponsor_announcement_from_form(request.form)
        _save_sponsor_announcement_draft(conn, draft)
        conn.commit()
    except ValueError as exc:
        conn.rollback()
        conn.close()
        flash(str(exc), 'error')
        return redirect(url_for('admin.admin_email_ops', target_date=request.form.get('target_date') or ''))
    except Exception as exc:
        conn.rollback()
        conn.close()
        flash(f'Could not load sponsor email draft: {str(exc)[:200]}', 'error')
        return redirect(url_for('admin.admin_email_ops', target_date=request.form.get('target_date') or ''))
    finally:
        try:
            conn.close()
        except Exception:
            pass

    try:
        results = _send_sponsor_announcement_to_active_subscribers(
            draft=draft,
            initiated_by=current_user.username,
        )
        flash(
            f"Sponsor email send complete: {results['sent_count']} sent, {results['failed_count']} failed.",
            'success' if results['failed_count'] == 0 else 'warning',
        )
    except ValueError as exc:
        flash(str(exc), 'error')
    except Exception as exc:
        flash(f'Sponsor email send failed: {str(exc)[:200]}', 'error')
    return redirect(url_for('admin.admin_email_ops', target_date=request.form.get('target_date') or ''))


@admin_bp.route('/audience/email-ops/retry-failures', methods=['POST'])
@login_required
@require_role(*EMAIL_OPS_SEND_ROLES)
def admin_email_ops_retry_failures():
    target_date = request.form.get('target_date') or ''
    run_id_raw = (request.form.get('run_id') or '').strip()
    selected_run_id = ''
    try:
        if not run_id_raw:
            raise ValueError('Choose a digest run to retry.')
        selected_run_id = str(int(run_id_raw))
        results = _retry_failed_digest_recipients(int(run_id_raw), initiated_by=current_user.username)
        flash(
            f"Retry complete: {results['sent_count']} sent, "
            f"{results['skipped_count']} skipped, {results['failed_count']} failed.",
            'success' if results['failed_count'] == 0 else 'warning',
        )
        target_date = results['target_date']
    except ValueError as exc:
        flash(str(exc), 'error')
    except Exception as exc:
        flash(f'Retry failed: {str(exc)[:200]}', 'error')
    return redirect(url_for('admin.admin_email_ops', target_date=target_date, run_id=selected_run_id))
