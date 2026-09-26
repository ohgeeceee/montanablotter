"""Admin panel — bail agency outreach workflow.

Endpoints:
  GET  /admin/bail-outreach                 — funnel + draft review queue
  POST /admin/bail-outreach/run-worker      — trigger cadence (queues drafts)
  POST /admin/bail-outreach/draft/<id>/send — SMTP send + flip agency contacted
  POST /admin/bail-outreach/draft/<id>/skip — mark draft skipped
  POST /admin/bail-outreach/agency/<id>/status — manual status set

Drafts live in outreach_drafts (worker_name='bail_outreach_cadence').
The cron never sends; this blueprint is the ONLY SMTP path, mirroring
blueprints/admin/lawyer_outreach.py.
"""
from __future__ import annotations

import logging
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import login_required

from db import get_db
from blueprints.admin import admin_bp, _log_admin_action

log = logging.getLogger(__name__)

WORKER = 'bail_outreach_cadence'
_ALLOWED_STATUSES = {'new', 'draft_queued', 'contacted', 'stalled',
                     'closed_won', 'lost', 'unqualified'}


def _smtp_settings():
    import config as _config
    return {
        'server': getattr(_config, 'SMTP_SERVER', ''),
        'port': int(getattr(_config, 'SMTP_PORT', 0) or 0),
        'user': getattr(_config, 'SMTP_USER', getattr(_config, 'EMAIL_USER', '')),
        'password': getattr(_config, 'SMTP_PASSWORD', getattr(_config, 'EMAIL_PASSWORD', '')),
    }


def _send_email(to_addr: str, subject: str, body: str) -> tuple[bool, str]:
    s = _smtp_settings()
    if not (s['server'] and s['port'] and s['user'] and s['password']):
        return False, 'smtp_not_configured'
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = f"Montana Blotter <{s['user']}>"
    msg['To'] = to_addr
    msg.attach(MIMEText(body, 'plain'))
    try:
        with smtplib.SMTP(s['server'], s['port']) as server:
            server.starttls()
            server.login(s['user'], s['password'])
            server.sendmail(s['user'], to_addr, msg.as_string())
        return True, ''
    except Exception as e:  # pragma: no cover
        log.warning('bail outreach send failed to %s: %s', to_addr, e)
        return False, str(e)[:200]


@admin_bp.route('/bail-outreach')
@admin_bp.route('/revenue/bail-outreach')
@login_required
def admin_bail_outreach():
    conn = get_db()
    agencies = [dict(r) for r in conn.execute(
        """SELECT * FROM bail_agency_outreach
           ORDER BY CASE outreach_status
               WHEN 'draft_queued' THEN 1 WHEN 'contacted' THEN 2
               WHEN 'new' THEN 3 WHEN 'stalled' THEN 4 ELSE 5 END,
             agency_name"""
    ).fetchall()]
    drafts = [dict(r) for r in conn.execute(
        """SELECT * FROM outreach_drafts
           WHERE worker_name=? ORDER BY
             CASE status WHEN 'pending' THEN 0 ELSE 1 END,
             created_at DESC LIMIT 200""", (WORKER,)
    ).fetchall()]
    stats = dict(conn.execute(
        """SELECT COUNT(*) AS total,
             SUM(CASE WHEN outreach_status='new' THEN 1 ELSE 0 END) AS fresh,
             SUM(CASE WHEN outreach_status='draft_queued' THEN 1 ELSE 0 END) AS queued,
             SUM(CASE WHEN outreach_status='contacted' THEN 1 ELSE 0 END) AS contacted,
             SUM(CASE WHEN outreach_status='closed_won' THEN 1 ELSE 0 END) AS won,
             SUM(CASE WHEN outreach_status='stalled' THEN 1 ELSE 0 END) AS stalled
           FROM bail_agency_outreach""").fetchone())
    conn.close()
    return render_template('admin_bail_outreach.html',
                           agencies=agencies, drafts=drafts, stats=stats,
                           allowed_statuses=sorted(_ALLOWED_STATUSES),
                           current_year=datetime.now().year)


@admin_bp.route('/bail-outreach/run-worker', methods=['POST'])
@admin_bp.route('/revenue/bail-outreach/run-worker', methods=['POST'])
@login_required
def admin_bail_outreach_run_worker():
    from services.bail_outreach import cadence
    import sqlite3
    import config
    db_path = getattr(config, 'DB_PATH', 'blotter.db')
    conn = sqlite3.connect(db_path, timeout=60)
    conn.row_factory = sqlite3.Row
    try:
        counts = cadence.run_cadence(conn)
    finally:
        conn.close()
    _log_admin_action("bail_outreach_run_worker", "outreach_draft", None,
                      metadata={"counts": str(counts)})
    flash(f"Cadence run: {counts}", 'success')
    return redirect(url_for('.admin_bail_outreach'))


@admin_bp.route('/bail-outreach/draft/<int:draft_id>/send', methods=['POST'])
@admin_bp.route('/revenue/bail-outreach/draft/<int:draft_id>/send', methods=['POST'])
@login_required
def admin_bail_outreach_draft_send(draft_id):
    conn = get_db()
    d = conn.execute(
        "SELECT * FROM outreach_drafts WHERE id=? AND worker_name=?",
        (draft_id, WORKER)).fetchone()
    if not d:
        conn.close()
        abort(404)
    if d['status'] != 'pending':
        conn.close()
        flash(f"Draft is already {d['status']}.", 'error')
        return redirect(url_for('.admin_bail_outreach'))
    if not d['email_address']:
        conn.close()
        flash('This draft is a phone task, not an email. Update the '
              'agency status manually after the call.', 'error')
        return redirect(url_for('.admin_bail_outreach'))

    ok, err = _send_email(d['email_address'], d['subject'], d['body'])
    if ok:
        from services.bail_outreach import cadence
        cadence.mark_draft_sent(conn, draft_id)
        _log_admin_action('bail_outreach_draft_sent', 'outreach_draft',
                          draft_id, conn=conn)
        conn.close()
        flash('Email sent — agency marked contacted, follow-ups will age.',
              'success')
    else:
        conn.execute("UPDATE outreach_drafts SET notes=? WHERE id=?",
                     (err[:200], draft_id))
        conn.commit()
        conn.close()
        flash(f'Send failed: {err}', 'error')
    return redirect(url_for('.admin_bail_outreach'))


@admin_bp.route('/bail-outreach/draft/<int:draft_id>/skip', methods=['POST'])
@admin_bp.route('/revenue/bail-outreach/draft/<int:draft_id>/skip', methods=['POST'])
@login_required
def admin_bail_outreach_draft_skip(draft_id):
    conn = get_db()
    d = conn.execute(
        "SELECT * FROM outreach_drafts WHERE id=? AND worker_name=?",
        (draft_id, WORKER)).fetchone()
    if not d:
        conn.close()
        abort(404)
    conn.execute(
        "UPDATE outreach_drafts SET status='skipped', "
        "skipped_at=datetime('now') WHERE id=?", (draft_id,))
    conn.commit()
    _log_admin_action('bail_outreach_draft_skipped', 'outreach_draft',
                      draft_id, conn=conn)
    conn.close()
    flash('Draft skipped.', 'success')
    return redirect(url_for('.admin_bail_outreach'))


@admin_bp.route('/bail-outreach/agency/<int:agency_id>/status',
                methods=['POST'])
@admin_bp.route('/revenue/bail-outreach/agency/<int:agency_id>/status',
                methods=['POST'])
@login_required
def admin_bail_outreach_agency_status(agency_id):
    new_status = (request.form.get('outreach_status') or '').strip()
    if new_status not in _ALLOWED_STATUSES:
        flash(f'Unknown status: {new_status}', 'error')
        return redirect(url_for('.admin_bail_outreach'))
    conn = get_db()
    a = conn.execute("SELECT id FROM bail_agency_outreach WHERE id=?",
                     (agency_id,)).fetchone()
    if not a:
        conn.close()
        abort(404)
    conn.execute(
        """UPDATE bail_agency_outreach SET outreach_status=?,
               last_contacted_at=CASE WHEN ?='contacted'
                   THEN datetime('now') ELSE last_contacted_at END,
               updated_at=datetime('now') WHERE id=?""",
        (new_status, new_status, agency_id))
    conn.commit()
    _log_admin_action('bail_outreach_status_set', 'bail_agency_outreach',
                      agency_id, conn=conn, metadata={"status": new_status})
    conn.close()
    flash(f'Agency set to {new_status}.', 'success')
    return redirect(url_for('.admin_bail_outreach'))
