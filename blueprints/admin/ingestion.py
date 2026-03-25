from __future__ import annotations

import json
import os
from datetime import datetime

from flask import current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required
from werkzeug.utils import secure_filename

import config
from db import get_db
from utils.auth_constants import ADMIN_ACCESS_ROLES, CONTENT_REVIEW_ROLES, OPERATIONS_ROLES
from blueprints.admin import admin_bp, require_role, _log_admin_action

# ---------------------------------------------------------------------------
# Private helpers (ingestion-only)
# ---------------------------------------------------------------------------

def _allowed_file(filename):
    """Return True if *filename* has an allowed extension (PDF only)."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'pdf'}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@admin_bp.route('/ingestion')
@login_required
def admin_ingestion():
    """Inspect failed and recent ingestion jobs."""
    import app as _app_module

    status_filter = request.args.get('status', 'failed')
    if status_filter not in ('failed', 'published', 'all'):
        status_filter = 'failed'

    conn = get_db()
    where_clause = ''
    params = []
    if status_filter != 'all':
        where_clause = 'WHERE ij.status = ?'
        params.append(status_filter)

    jobs = conn.execute(
        f'''
        SELECT
            ij.id,
            ij.status,
            ij.retry_count,
            ij.last_error,
            ij.started_at,
            ij.finished_at,
            sd.id AS source_document_id,
            sd.source_type,
            sd.source_sender,
            sd.source_subject,
            sd.source_received_at,
            sd.filename AS source_filename,
            sd.storage_path,
            sd.raw_text,
            b.id AS blotter_id,
            b.filename AS blotter_filename,
            b.county AS blotter_county,
            EXISTS(SELECT 1 FROM posts p WHERE p.blotter_id = b.id) AS has_post,
            (
                SELECT pe.stage
                FROM pipeline_events pe
                WHERE pe.ingestion_job_id = ij.id
                ORDER BY pe.id DESC
                LIMIT 1
            ) AS latest_stage,
            (
                SELECT pe.status
                FROM pipeline_events pe
                WHERE pe.ingestion_job_id = ij.id
                ORDER BY pe.id DESC
                LIMIT 1
            ) AS latest_stage_status,
            (
                SELECT pe.details_json
                FROM pipeline_events pe
                WHERE pe.ingestion_job_id = ij.id
                ORDER BY pe.id DESC
                LIMIT 1
            ) AS latest_details_json
        FROM ingestion_jobs ij
        JOIN source_documents sd ON sd.id = ij.source_document_id
        LEFT JOIN blotters b ON b.source_document_id = sd.id
        {where_clause}
        ORDER BY
            CASE ij.status WHEN 'failed' THEN 0 WHEN 'received' THEN 1 WHEN 'published' THEN 2 ELSE 3 END,
            COALESCE(ij.finished_at, ij.started_at) DESC
        LIMIT 100
        ''',
        params,
    ).fetchall()

    counts = conn.execute(
        """
        SELECT
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_count,
            SUM(CASE WHEN status = 'published' THEN 1 ELSE 0 END) AS published_count,
            COUNT(*) AS total_count
        FROM ingestion_jobs
        """
    ).fetchone()
    health_dashboard = _app_module._build_ingestion_health_dashboard(conn)
    conn.close()

    parsed_jobs = []
    for job in jobs:
        details = {}
        if job['latest_details_json']:
            try:
                details = json.loads(job['latest_details_json'])
            except json.JSONDecodeError:
                details = {'raw': job['latest_details_json']}
        job_dict = dict(job)
        job_dict['latest_details'] = details
        job_dict['source_excerpt'] = ((job['raw_text'] or '')[:180] + '...') if job['raw_text'] and len(job['raw_text']) > 180 else (job['raw_text'] or '')
        parsed_jobs.append(job_dict)

    return render_template(
        'admin_ingestion.html',
        health_dashboard=health_dashboard,
        jobs=parsed_jobs,
        status_filter=status_filter,
        failed_count=counts['failed_count'] or 0,
        published_count=counts['published_count'] or 0,
        total_count=counts['total_count'] or 0,
    )


@admin_bp.route('/ingestion/<int:job_id>/retry', methods=['POST'])
@login_required
def admin_retry_ingestion(job_id):
    """Retry a failed ingestion job from its stored source document."""
    conn = get_db()
    job = conn.execute(
        '''
        SELECT
            ij.id,
            ij.source_document_id,
            ij.status,
            sd.source_type,
            sd.source_sender,
            sd.storage_path,
            sd.raw_text
        FROM ingestion_jobs ij
        JOIN source_documents sd ON sd.id = ij.source_document_id
        WHERE ij.id = ?
        ''',
        (job_id,),
    ).fetchone()
    conn.close()

    if not job:
        flash('Ingestion job not found.')
        return redirect(url_for('admin.admin_ingestion'))

    from pipeline_state import log_pipeline_event, set_ingestion_job_status
    from processor import process_new_blotter, process_text_blotter

    try:
        set_ingestion_job_status(job_id, 'received', last_error=None, finished=False)
        log_pipeline_event(job_id, 'retry', 'ok', {'message': 'manual-retry-started'})

        if job['source_type'] in ('imap_pdf', 'local_pdf'):
            storage_path = job['storage_path']
            if not storage_path or not os.path.exists(storage_path):
                raise FileNotFoundError('Stored PDF file is no longer available')
            blotter_id = process_new_blotter(
                storage_path,
                source_document_id=job['source_document_id'],
                ingestion_job_id=job_id,
            )
        elif job['source_type'] == 'imap_text':
            raw_text = job['raw_text']
            if not raw_text:
                raise ValueError('Stored email body is empty')
            blotter_id = process_text_blotter(
                raw_text,
                sender_email=job['source_sender'],
                source_document_id=job['source_document_id'],
                ingestion_job_id=job_id,
            )
        else:
            raise ValueError(f"Unsupported source type: {job['source_type']}")

        flash(f'Retried ingestion job #{job_id}. Blotter #{blotter_id} processed.')
    except Exception as e:
        log_pipeline_event(job_id, 'retry', 'error', {'error': str(e)})
        set_ingestion_job_status(job_id, 'failed', last_error=str(e), finished=True)
        flash(f'Retry failed for job #{job_id}: {e}')

    return redirect(url_for('admin.admin_ingestion'))


@admin_bp.route('/upload', methods=['GET', 'POST'])
@login_required
def admin_upload():
    """Admin PDF upload"""

    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file selected')
            return redirect(request.url)

        file = request.files['file']
        county = request.form.get('county', '')

        if file.filename == '':
            flash('No file selected')
            return redirect(request.url)

        if file and _allowed_file(file.filename):
            filename = secure_filename(file.filename)
            filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)

            # Process the PDF
            try:
                from processor import process_new_blotter
                batch_id = process_new_blotter(filepath, county if county else None)
                flash(f'Successfully processed! Batch #{batch_id} with incidents added.')
                return redirect(url_for('admin.admin_dashboard'))
            except Exception as e:
                flash(f'Error processing PDF: {str(e)}')
                return redirect(request.url)

        flash('Invalid file type. PDF only.')
        return redirect(request.url)

    # GET request - show upload form
    return render_template('admin_upload.html', counties=config.MONTANA_COUNTIES)


@admin_bp.route('/blotters')
@login_required
def admin_blotters():
    """View and manage all blotters"""
    conn = get_db()
    blotters = conn.execute('SELECT * FROM blotters ORDER BY upload_date DESC').fetchall()
    # Fetch the post (id + case_status) associated with each blotter
    latest_fb_queue = {
        row['post_id']: {
            'fb_status': row['status'],
            'fb_queue_id': row['id'],
            'facebook_post_id': row['facebook_post_id'],
        }
        for row in conn.execute(
            '''
            SELECT q.id, q.post_id, q.status, q.facebook_post_id
            FROM facebook_post_queue q
            JOIN (
                SELECT post_id, MAX(id) AS latest_id
                FROM facebook_post_queue
                GROUP BY post_id
            ) latest ON latest.latest_id = q.id
            '''
        )
    }

    posts_map = {}
    for row in conn.execute('SELECT id, blotter_id, case_status FROM posts'):
        fb = latest_fb_queue.get(row['id'], {})
        posts_map[row['blotter_id']] = {
            'id': row['id'],
            'case_status': row['case_status'] or 'pending',
            'fb_status': fb.get('fb_status'),
            'fb_queue_id': fb.get('fb_queue_id'),
            'facebook_post_id': fb.get('facebook_post_id'),
        }
    conn.close()
    return render_template('admin_blotters.html', blotters=blotters, posts_map=posts_map)


@admin_bp.route('/blotter/<int:blotter_id>/delete', methods=['POST'])
@login_required
def admin_delete_blotter(blotter_id):
    """Delete a blotter and its records"""
    conn = get_db()

    # Foreign-key cascades are enabled on this connection, but delete posts explicitly
    # to clean up legacy databases that may have been created without enforcement.
    conn.execute('DELETE FROM posts WHERE blotter_id = ?', (blotter_id,))
    conn.execute('DELETE FROM records WHERE blotter_id = ?', (blotter_id,))
    conn.execute('DELETE FROM blotters WHERE id = ?', (blotter_id,))

    conn.commit()
    conn.close()

    flash('Blotter deleted successfully')
    return redirect(url_for('admin.admin_blotters'))


@admin_bp.route('/post/<int:post_id>/redact', methods=['GET', 'POST'])
@login_required
def admin_redact_post(post_id):
    """PII Redaction Editor — highlight, black-bar, and save a sanitised post summary."""
    conn = get_db()
    post = conn.execute('SELECT * FROM posts WHERE id = ?', (post_id,)).fetchone()
    if not post:
        conn.close()
        flash('Post not found.')
        return redirect(url_for('admin.admin_blotters'))

    if request.method == 'POST':
        redacted_summary = request.form.get('redacted_summary', '').strip()
        mark_clean       = request.form.get('mark_clean', '') == '1'
        new_status       = 'clean' if mark_clean else (post['audit_status'] or 'pending')
        conn.execute(
            'UPDATE posts SET summary = ?, audit_status = ?, audited_at = ? WHERE id = ?',
            (redacted_summary, new_status, datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S') if mark_clean else post['audited_at'], post_id),
        )
        _log_admin_action(
            'redaction.saved',
            target_type='post',
            target_id=post_id,
            metadata={'from': post['audit_status'] or 'pending', 'to': new_status, 'mark_clean': mark_clean},
            conn=conn,
        )
        conn.commit()
        conn.close()
        flash('Post redacted and saved successfully.' if mark_clean
              else 'Draft saved — not yet marked clean.')
        return redirect(url_for('admin.admin_redact_post', post_id=post_id))

    # Build PII spans from current summary
    from blotter_auditor import get_pii_spans
    summary    = post['summary'] or ''
    pii_spans  = get_pii_spans(summary)
    conn.close()
    return render_template('admin_redaction.html',
                           post=post,
                           pii_spans=pii_spans)


@admin_bp.route('/post/<int:post_id>/status', methods=['POST'])
@login_required
def admin_update_post_status(post_id):
    """AJAX endpoint — cycle case_status for a post (active / pending / resolved)."""
    data = request.get_json(force=True) or {}
    new_status = data.get('status', 'pending')
    if new_status not in ('active', 'pending', 'resolved'):
        return jsonify({'ok': False, 'error': 'invalid status'}), 400
    conn = get_db()
    conn.execute('UPDATE posts SET case_status = ? WHERE id = ?', (new_status, post_id))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'status': new_status})


@admin_bp.route('/operations/redaction')
@login_required
@require_role(*CONTENT_REVIEW_ROLES)
def admin_redaction_queue():
    from blotter_auditor import get_pii_spans

    q = (request.args.get('q') or '').strip()[:120]
    status_filter = (request.args.get('status') or 'pending').strip().lower()
    county_filter = (request.args.get('county') or '').strip()[:80]
    if status_filter not in {'pending', 'clean', 'flagged', 'all'}:
        status_filter = 'pending'

    where = []
    params = []
    if status_filter != 'all':
        where.append("COALESCE(p.audit_status, 'pending') = ?")
        params.append(status_filter)
    if county_filter:
        where.append('COALESCE(p.county, b.county, \'\') = ?')
        params.append(county_filter)
    if q:
        where.append('(COALESCE(p.title, \'\') LIKE ? OR COALESCE(p.summary, \'\') LIKE ? OR COALESCE(p.agency_name, \'\') LIKE ?)')
        like = f'%{q}%'
        params.extend([like, like, like])

    where_sql = f"WHERE {' AND '.join(where)}" if where else ''
    conn = get_db()
    rows = conn.execute(
        f'''
        SELECT
            p.id,
            p.title,
            p.summary,
            p.county,
            p.agency_name,
            p.incident_date,
            COALESCE(p.audit_status, 'pending') AS audit_status,
            COALESCE(p.pii_flags, '') AS pii_flags,
            COALESCE(p.audited_at, '') AS audited_at,
            COALESCE(b.filename, '') AS blotter_filename,
            (
                SELECT COUNT(*)
                FROM records r
                WHERE r.blotter_id = p.blotter_id
            ) AS incident_count
        FROM posts p
        LEFT JOIN blotters b ON b.id = p.blotter_id
        {where_sql}
        ORDER BY
            CASE COALESCE(p.audit_status, 'pending')
                WHEN 'flagged' THEN 0
                WHEN 'pending' THEN 1
                ELSE 2
            END,
            datetime(COALESCE(p.audited_at, p.created_at)) DESC,
            p.id DESC
        LIMIT 120
        ''',
        params,
    ).fetchall()
    county_options = conn.execute(
        '''
        SELECT DISTINCT COALESCE(NULLIF(county, ''), '') AS county
        FROM posts
        WHERE COALESCE(NULLIF(county, ''), '') != ''
        ORDER BY county ASC
        '''
    ).fetchall()
    summary = conn.execute(
        '''
        SELECT
            SUM(CASE WHEN COALESCE(audit_status, 'pending') = 'pending' THEN 1 ELSE 0 END) AS pending_count,
            SUM(CASE WHEN COALESCE(audit_status, 'pending') = 'clean' THEN 1 ELSE 0 END) AS clean_count,
            SUM(CASE WHEN COALESCE(audit_status, 'pending') = 'flagged' THEN 1 ELSE 0 END) AS flagged_count,
            COUNT(*) AS total_count
        FROM posts
        '''
    ).fetchone()
    conn.close()

    queue_rows = []
    for row in rows:
        pii_count = len(get_pii_spans(row['summary'] or ''))
        item = dict(row)
        item['pii_count'] = pii_count
        item['summary_preview'] = ((row['summary'] or '')[:180] + '...') if row['summary'] and len(row['summary']) > 180 else (row['summary'] or '')
        queue_rows.append(item)

    return render_template(
        'admin_redaction_queue.html',
        rows=queue_rows,
        summary=summary,
        q=q,
        status_filter=status_filter,
        county_filter=county_filter,
        county_options=[row['county'] for row in county_options],
    )


@admin_bp.route('/operations/redaction/<int:post_id>/approve', methods=['POST'])
@login_required
@require_role(*CONTENT_REVIEW_ROLES)
def admin_redaction_queue_approve(post_id):
    conn = get_db()
    post = conn.execute('SELECT id, title, COALESCE(audit_status, \'pending\') AS audit_status FROM posts WHERE id = ?', (post_id,)).fetchone()
    if not post:
        conn.close()
        flash('Post not found.', 'error')
        return redirect(url_for('admin.admin_redaction_queue'))
    conn.execute(
        "UPDATE posts SET audit_status = 'clean', audited_at = datetime('now') WHERE id = ?",
        (post_id,),
    )
    _log_admin_action(
        'redaction.approved',
        target_type='post',
        target_id=post_id,
        metadata={'title': post['title'], 'from': post['audit_status'], 'to': 'clean'},
        conn=conn,
    )
    conn.commit()
    conn.close()
    flash('Post marked clean.', 'success')
    return redirect(url_for('admin.admin_redaction_queue'))


@admin_bp.route('/operations/redaction/<int:post_id>/reset', methods=['POST'])
@login_required
@require_role(*CONTENT_REVIEW_ROLES)
def admin_redaction_queue_reset(post_id):
    conn = get_db()
    post = conn.execute('SELECT id, title, COALESCE(audit_status, \'pending\') AS audit_status FROM posts WHERE id = ?', (post_id,)).fetchone()
    if not post:
        conn.close()
        flash('Post not found.', 'error')
        return redirect(url_for('admin.admin_redaction_queue'))
    conn.execute(
        "UPDATE posts SET audit_status = 'pending', audited_at = NULL WHERE id = ?",
        (post_id,),
    )
    _log_admin_action(
        'redaction.reset',
        target_type='post',
        target_id=post_id,
        metadata={'title': post['title'], 'from': post['audit_status'], 'to': 'pending'},
        conn=conn,
    )
    conn.commit()
    conn.close()
    flash('Post returned to pending review.', 'success')
    return redirect(url_for('admin.admin_redaction_queue'))
