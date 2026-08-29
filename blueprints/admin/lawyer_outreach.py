"""Admin panel — per-firm lawyer outreach workflow.

Endpoints:
  GET  /admin/lawyer-outreach                         — review queue list
  POST /admin/lawyer-outreach/run-worker              — manual cadence trigger
  POST /admin/lawyer-outreach/import-csv              — manual CSV re-import
  GET  /admin/lawyer-outreach/prospect/<id>           — per-firm detail page
  POST /admin/lawyer-outreach/prospect/<id>/edit      — save notes / fields
  POST /admin/lawyer-outreach/prospect/<id>/advance   — manual stage advance
  POST /admin/lawyer-outreach/prospect/<id>/won       — mark as won (terminal)
  POST /admin/lawyer-outreach/prospect/<id>/lost      — mark as lost (terminal)
  POST /admin/lawyer-outreach/prospect/<id>/email     — re-queue next stage
  POST /admin/lawyer-outreach/email/<id>/send         — SMTP send + status='sent'
  POST /admin/lawyer-outreach/email/<id>/skip         — mark skipped (no SMTP)

SMTP is colocated here on purpose — same pattern as
blueprints/admin/lawyer_ads.py, blueprints/lawyer_ads.py::_smtp_settings.
The cron never sends; this blueprint is the ONLY SMTP path.
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

_STAGES = ('day_1', 'day_3', 'day_5', 'day_10')
_TERMINAL_STATUSES = {'won', 'lost', 'unqualified'}


# ----------------------------------------------------------------------- SMTP

def _smtp_settings():
    """Lazy access to config.SMTP_* so unit tests can patch config."""
    import config as _config

    return {
        'server': getattr(_config, 'SMTP_SERVER', ''),
        'port': int(getattr(_config, 'SMTP_PORT', 0) or 0),
        'user': getattr(_config, 'SMTP_USER', getattr(_config, 'EMAIL_USER', '')),
        'password': getattr(_config, 'SMTP_PASSWORD', getattr(_config, 'EMAIL_PASSWORD', '')),
    }


def _send_email(to_addr: str, subject: str, body: str) -> tuple[bool, str]:
    """Send one outreach email. Returns (ok, error_message).

    Mirrors services/alerts/dispatcher.py::_send_email.
    """
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
    except Exception as e:  # pragma: no cover — error path
        log.warning("lawyer outreach send failed to %s: %s", to_addr, e)
        return False, str(e)[:200]


# --------------------------------------------------------------------- helpers

def _stage_label(stage: str) -> str:
    return {
        'day_1': 'Day 1 — initial email',
        'day_3': 'Day 3 — phone follow-up',
        'day_5': 'Day 5 — sample report',
        'day_10': 'Day 10 — close',
        'won': 'Won — paying customer',
        'lost': 'Lost — passed or disqualified',
    }.get(stage, stage)


def _ensure_schema(conn) -> None:
    from init_db import ensure_lawyer_outreach_schema
    ensure_lawyer_outreach_schema(conn)


# --------------------------------------------------------------- list routes

@admin_bp.route('/revenue/lawyer-outreach')
@login_required
def admin_lawyer_outreach():
    conn = get_db()
    _ensure_schema(conn)

    q = (request.args.get('q') or '').strip()[:120]
    stage_filter = (request.args.get('stage') or '').strip().lower()
    status_filter = (request.args.get('status') or '').strip().lower()

    base_query = 'FROM lawyer_outreach_prospects WHERE 1=1'
    params: list = []
    if q:
        base_query += ' AND (firm_name LIKE ? OR county LIKE ? OR contact_name LIKE ?)'
        like = f'%{q}%'
        params.extend([like, like, like])
    if stage_filter in _STAGES:
        base_query += ' AND stage = ?'
        params.append(stage_filter)
    if status_filter:
        base_query += ' AND status = ?'
        params.append(status_filter)

    prospects = [dict(r) for r in conn.execute(
        f'SELECT * {base_query} ORDER BY '
        f"CASE stage WHEN 'day_1' THEN 1 WHEN 'day_3' THEN 2 "
        f"WHEN 'day_5' THEN 3 WHEN 'day_10' THEN 4 ELSE 5 END, "
        'updated_at DESC LIMIT 300',
        params,
    ).fetchall()]

    pending_emails = [dict(r) for r in conn.execute(
        '''
        SELECT e.id, e.prospect_id, e.stage, e.to_addr, e.subject,
               e.created_at, p.firm_name, p.county
        FROM lawyer_outreach_emails e
        JOIN lawyer_outreach_prospects p ON p.id = e.prospect_id
        WHERE e.status = 'pending'
        ORDER BY e.created_at DESC LIMIT 200
        '''
    ).fetchall()]

    stats = dict(conn.execute(
        '''
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status IN ('queued','in_progress') THEN 1 ELSE 0 END) AS active,
            SUM(CASE WHEN stage='day_1' THEN 1 ELSE 0 END) AS day_1,
            SUM(CASE WHEN stage='day_3' THEN 1 ELSE 0 END) AS day_3,
            SUM(CASE WHEN stage='day_5' THEN 1 ELSE 0 END) AS day_5,
            SUM(CASE WHEN stage='day_10' THEN 1 ELSE 0 END) AS day_10,
            SUM(CASE WHEN status='won' THEN 1 ELSE 0 END) AS won,
            SUM(CASE WHEN status='lost' THEN 1 ELSE 0 END) AS lost
        FROM lawyer_outreach_prospects
        '''
    ).fetchone())

    conn.close()
    return render_template(
        'admin_lawyer_outreach.html',
        prospects=prospects,
        pending_emails=pending_emails,
        stats=stats,
        stages=_STAGES,
        stage_label=_stage_label,
        q=q,
        stage_filter=stage_filter,
        status_filter=status_filter,
        current_year=datetime.now().year,
    )


# ---------------------------------------------------------- per-prospect view

@admin_bp.route('/revenue/lawyer-outreach/prospect/<int:prospect_id>')
@login_required
def admin_lawyer_outreach_prospect(prospect_id):
    conn = get_db()
    _ensure_schema(conn)
    prospect = conn.execute(
        'SELECT * FROM lawyer_outreach_prospects WHERE id = ?', (prospect_id,)
    ).fetchone()
    if not prospect:
        conn.close()
        return render_template('404.html'), 404

    emails = [dict(r) for r in conn.execute(
        '''SELECT * FROM lawyer_outreach_emails
           WHERE prospect_id = ?
           ORDER BY created_at DESC LIMIT 50''',
        (prospect_id,),
    ).fetchall()]

    conn.close()
    return render_template(
        'admin_lawyer_outreach_edit.html',
        prospect=dict(prospect),
        emails=emails,
        stages=_STAGES,
        stage_label=_stage_label,
        current_year=datetime.now().year,
    )


# ----------------------------------------------------------- sample report --

@admin_bp.route('/revenue/lawyer-outreach/prospect/<int:prospect_id>/sample-report')
@login_required
def admin_lawyer_outreach_sample_report(prospect_id):
    """One-page sample report (Day 5 outreach deliverable).

    Two modes: 'sample' (prospect not yet a paying customer; reference
    numbers from the 90-day pilot cohort) or 'live' (prospect matches an
    active lawyer_ad_orders row; reads real lawyer_listing_events +
    lawyer_lead_deliveries for the last 30 days). Renders noindex.

    `?package=gold|silver|bronze` controls the sample-mode tier only —
    ignored in live mode where the order's actual package wins.
    """
    from services.lawyer_outreach.sample_report import generate as build_report

    package_id = (request.args.get('package') or 'gold').strip().lower()
    if package_id not in {'bronze', 'silver', 'gold'}:
        package_id = 'gold'

    conn = get_db()
    _ensure_schema(conn)
    # The report module touches lawyer_ad_orders / lawyer_listing_events /
    # lawyer_lead_deliveries — ensure those schemas exist in tests.
    try:
        from init_db import ensure_lawyer_ad_schema
        ensure_lawyer_ad_schema(conn)
    except Exception:  # pragma: no cover — defensive, init_db is the only caller
        pass
    try:
        report = build_report(conn, prospect_id, package_id=package_id)
    except ValueError:
        conn.close()
        return render_template('404.html'), 404
    conn.close()
    return render_template(
        'admin_lawyer_outreach_sample_report.html',
        report=report,
        current_year=datetime.now().year,
    )


# -------------------------------------------------------- edit / advance / win

@admin_bp.route('/revenue/lawyer-outreach/prospect/<int:prospect_id>/edit', methods=['POST'])
@login_required
def admin_lawyer_outreach_prospect_edit(prospect_id):
    conn = get_db()
    prospect = conn.execute(
        'SELECT id FROM lawyer_outreach_prospects WHERE id = ?', (prospect_id,)
    ).fetchone()
    if not prospect:
        conn.close()
        return render_template('404.html'), 404

    fields = {
        'contact_name': (request.form.get('contact_name') or '').strip()[:120] or None,
        'contact_email': (request.form.get('contact_email') or '').strip()[:120] or None,
        'website': (request.form.get('website') or '').strip()[:255] or None,
        'practice_areas': (request.form.get('practice_areas') or '').strip()[:500] or None,
        'notes': (request.form.get('notes') or '').strip()[:2000] or None,
    }
    conn.execute(
        '''UPDATE lawyer_outreach_prospects
           SET contact_name = ?, contact_email = ?, website = ?,
               practice_areas = ?, notes = ?, updated_at = datetime('now')
           WHERE id = ?''',
        (*fields.values(), prospect_id),
    )
    conn.commit()
    _log_admin_action('lawyer_outreach_prospect_edit', 'lawyer_outreach_prospect',
                      prospect_id, metadata=fields, conn=conn)
    conn.close()
    flash('Prospect updated.', 'success')
    return redirect(url_for('.admin_lawyer_outreach_prospect', prospect_id=prospect_id))


@admin_bp.route('/revenue/lawyer-outreach/prospect/<int:prospect_id>/advance', methods=['POST'])
@login_required
def admin_lawyer_outreach_prospect_advance(prospect_id):
    new_stage = (request.form.get('stage') or '').strip()
    if new_stage not in _STAGES + ('won',):
        flash('Invalid target stage.', 'error')
        return redirect(url_for('.admin_lawyer_outreach_prospect', prospect_id=prospect_id))

    conn = get_db()
    new_status = 'won' if new_stage == 'won' else 'in_progress'
    conn.execute(
        '''UPDATE lawyer_outreach_prospects
           SET stage = ?, status = ?, last_action_at = datetime('now'),
               next_action_at = datetime('now', '+1 day'),
               updated_at = datetime('now')
           WHERE id = ?''',
        (new_stage, new_status, prospect_id),
    )
    conn.commit()
    _log_admin_action('lawyer_outreach_prospect_advance', 'lawyer_outreach_prospect',
                      prospect_id, metadata={'stage': new_stage}, conn=conn)
    conn.close()
    flash(f'Prospect advanced to {_stage_label(new_stage)}.', 'success')
    return redirect(url_for('.admin_lawyer_outreach_prospect', prospect_id=prospect_id))


@admin_bp.route('/revenue/lawyer-outreach/prospect/<int:prospect_id>/won', methods=['POST'])
@login_required
def admin_lawyer_outreach_prospect_won(prospect_id):
    conn = get_db()
    conn.execute(
        '''UPDATE lawyer_outreach_prospects
           SET status = 'won', stage = 'won',
               last_action_at = datetime('now'),
               updated_at = datetime('now')
           WHERE id = ?''',
        (prospect_id,),
    )
    conn.commit()
    _log_admin_action('lawyer_outreach_prospect_won', 'lawyer_outreach_prospect',
                      prospect_id, conn=conn)
    conn.close()
    flash('Prospect marked as won.', 'success')
    return redirect(url_for('.admin_lawyer_outreach_prospect', prospect_id=prospect_id))


@admin_bp.route('/revenue/lawyer-outreach/prospect/<int:prospect_id>/lost', methods=['POST'])
@login_required
def admin_lawyer_outreach_prospect_lost(prospect_id):
    conn = get_db()
    conn.execute(
        '''UPDATE lawyer_outreach_prospects
           SET status = 'lost',
               last_action_at = datetime('now'),
               updated_at = datetime('now')
           WHERE id = ?''',
        (prospect_id,),
    )
    conn.commit()
    _log_admin_action('lawyer_outreach_prospect_lost', 'lawyer_outreach_prospect',
                      prospect_id, conn=conn)
    conn.close()
    flash('Prospect marked as lost.', 'success')
    return redirect(url_for('.admin_lawyer_outreach_prospect', prospect_id=prospect_id))


@admin_bp.route('/revenue/lawyer-outreach/prospect/<int:prospect_id>/email', methods=['POST'])
@login_required
def admin_lawyer_outreach_prospect_email(prospect_id):
    """Manually re-queue the next stage email for a prospect."""
    from services.lawyer_outreach.cadence import run_cadence
    conn = get_db()
    prospect = conn.execute(
        'SELECT id FROM lawyer_outreach_prospects WHERE id = ?', (prospect_id,)
    ).fetchone()
    if not prospect:
        conn.close()
        return render_template('404.html'), 404
    # Reset last_action_at so the recency guard doesn't block this prospect.
    conn.execute(
        "UPDATE lawyer_outreach_prospects SET last_action_at = NULL WHERE id = ?",
        (prospect_id,),
    )
    conn.commit()
    run_cadence(conn, dry_run=False)
    conn.close()
    flash('Email queued (or already pending).', 'success')
    return redirect(url_for('.admin_lawyer_outreach_prospect', prospect_id=prospect_id))


# -------------------------------------------------------- email send / skip --

@admin_bp.route('/revenue/lawyer-outreach/email/<int:email_id>/send', methods=['POST'])
@login_required
def admin_lawyer_outreach_email_send(email_id):
    conn = get_db()
    email = conn.execute(
        'SELECT * FROM lawyer_outreach_emails WHERE id = ?', (email_id,)
    ).fetchone()
    if not email:
        conn.close()
        return render_template('404.html'), 404
    if email['status'] != 'pending':
        conn.close()
        flash(f'Email is already {email["status"]}; not sent.', 'error')
        return redirect(request.referrer or url_for('.admin_lawyer_outreach'))

    ok, err = _send_email(email['to_addr'], email['subject'], email['body'])
    if ok:
        conn.execute(
            '''UPDATE lawyer_outreach_emails
               SET status='sent', sent_at=datetime('now'), error=NULL
               WHERE id = ?''',
            (email_id,),
        )
        conn.execute(
            "UPDATE lawyer_outreach_prospects SET last_action_at = datetime('now') "
            "WHERE id = ?",
            (email['prospect_id'],),
        )
        conn.commit()
        _log_admin_action('lawyer_outreach_email_sent', 'lawyer_outreach_email',
                          email_id, conn=conn)
        conn.close()
        flash('Email sent.', 'success')
    else:
        conn.execute(
            "UPDATE lawyer_outreach_emails SET error = ? WHERE id = ?",
            (err[:200], email_id),
        )
        conn.commit()
        conn.close()
        flash(f'Send failed: {err}', 'error')

    return redirect(url_for('.admin_lawyer_outreach'))


@admin_bp.route('/revenue/lawyer-outreach/email/<int:email_id>/skip', methods=['POST'])
@login_required
def admin_lawyer_outreach_email_skip(email_id):
    conn = get_db()
    email = conn.execute(
        'SELECT * FROM lawyer_outreach_emails WHERE id = ?', (email_id,)
    ).fetchone()
    if not email:
        conn.close()
        return render_template('404.html'), 404
    if email['status'] != 'pending':
        conn.close()
        flash(f'Email is already {email["status"]}; not skipped.', 'error')
        return redirect(url_for('.admin_lawyer_outreach'))
    conn.execute(
        '''UPDATE lawyer_outreach_emails
           SET status='skipped', skipped_at=datetime('now')
           WHERE id = ?''',
        (email_id,),
    )
    conn.commit()
    _log_admin_action('lawyer_outreach_email_skipped', 'lawyer_outreach_email',
                      email_id, conn=conn)
    conn.close()
    flash('Email skipped.', 'success')
    return redirect(url_for('.admin_lawyer_outreach'))


# ----------------------------------------------------- worker / import hooks --

@admin_bp.route('/revenue/lawyer-outreach/run-worker', methods=['POST'])
@login_required
def admin_lawyer_outreach_run_worker():
    from services.lawyer_outreach.cadence import run_cadence
    conn = get_db()
    _ensure_schema(conn)
    counts = run_cadence(conn, dry_run=False)
    conn.close()
    flash(f"Worker run: queued={counts.get('queued', 0)} "
          f"advanced_to_day_3={counts.get('advanced_to_day_3', 0)} "
          f"advanced_to_day_5={counts.get('advanced_to_day_5', 0)} "
          f"advanced_to_day_10={counts.get('advanced_to_day_10', 0)} "
          f"advanced_to_won={counts.get('advanced_to_won', 0)}",
          'success')
    return redirect(url_for('.admin_lawyer_outreach'))


@admin_bp.route('/revenue/lawyer-outreach/import-csv', methods=['POST'])
@login_required
def admin_lawyer_outreach_import_csv():
    from services.lawyer_outreach.importer import (
        import_prospects_from_csv, DEFAULT_CSV_PATH,
    )
    conn = get_db()
    _ensure_schema(conn)
    counts = import_prospects_from_csv(conn, DEFAULT_CSV_PATH, dry_run=False)
    conn.close()
    flash(f"CSV import: inserted={counts['inserted']} updated={counts['updated']} "
          f"skipped_blank={counts['skipped_blank']}", 'success')
    return redirect(url_for('.admin_lawyer_outreach'))