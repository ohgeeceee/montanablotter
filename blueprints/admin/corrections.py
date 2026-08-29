"""Admin routes for the public corrections log.

POST /admin/posts/<post_id>/correct — log a new correction to a published post.
"""
from __future__ import annotations

from flask import flash, redirect, request, url_for
from flask_login import current_user

from db import get_db

from . import _log_admin_action, admin_bp, require_role


@admin_bp.route('/operations/posts/<int:post_id>/correct', methods=['POST'])
@require_role()
def log_correction(post_id):
    """Append a public correction to a post. Reason is required."""
    field_name = (request.form.get('field_name') or '').strip()
    old_value = (request.form.get('old_value') or '').strip() or None
    new_value = (request.form.get('new_value') or '').strip() or None
    reason = (request.form.get('reason') or '').strip()
    is_public = 1 if request.form.get('is_public') == 'on' else 0

    if not field_name:
        flash('Field name is required.', 'error')
        return redirect(request.referrer or url_for('admin.admin_dashboard'))
    if not reason:
        flash('Reason is required for a public correction.', 'error')
        return redirect(request.referrer or url_for('admin.admin_dashboard'))

    db = get_db()
    post = db.execute('SELECT id, title FROM posts WHERE id = ?', (post_id,)).fetchone()
    if not post:
        flash(f'Post {post_id} not found.', 'error')
        return redirect(request.referrer or url_for('admin.admin_dashboard'))

    editor = (
        getattr(current_user, 'username', None)
        or getattr(current_user, 'email', None)
        or 'admin'
    )

    db.execute(
        '''
        INSERT INTO post_corrections
          (post_id, field_name, old_value, new_value, reason, corrected_by, is_public)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ''',
        (post_id, field_name[:80], old_value, new_value, reason[:2000], editor, is_public),
    )
    db.commit()

    _log_admin_action(
        'post_correction_logged',
        target_type='post',
        target_id=post_id,
        metadata={'field': field_name, 'public': bool(is_public)},
    )

    flash(
        f'Correction logged for post {post_id} ({field_name}).'
        + (' Visible on /corrections.' if is_public else ' Held as private.'),
        'success',
    )
    return redirect(request.referrer or url_for('admin.admin_dashboard'))
