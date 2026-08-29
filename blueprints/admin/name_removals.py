"""Admin review queue for paid name-removal / privacy-suppression requests.

Paid requests land in `name_suppression_requests` with status 'pending' (submitted)
then 'paid' (Stripe webhook confirmed). An admin reviews each and either:
  - approves  -> applies the suppression (redacts the name across records) and
                 writes a `suppressed_names` entry; request becomes 'applied'
  - rejects   -> request becomes 'rejected' with a reason

Suppression redacts (does not delete) the person's name. This is a RED-tier action.
"""

from __future__ import annotations

from flask import Blueprint, abort, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from db import get_db
from utils.auth_constants import ADMIN_ACCESS_ROLES
from services.monetization.name_suppression import apply_suppression

admin_bp = Blueprint('admin_name_removals', __name__, url_prefix='/admin')


def _require_admin():
    if not current_user.is_authenticated:
        abort(401)
    if getattr(current_user, 'role', None) not in ADMIN_ACCESS_ROLES:
        abort(403)


@admin_bp.route('/name-removals')
@login_required
def admin_name_removals():
    _require_admin()
    status_filter = (request.args.get('status') or 'paid').strip().lower()
    if status_filter not in {'pending', 'paid', 'applied', 'rejected', 'all'}:
        status_filter = 'paid'
    conn = get_db()
    try:
        where = "WHERE 1=1"
        params: list = []
        if status_filter != 'all':
            where = "WHERE nsr.status = ?"
            params = [status_filter]
        rows = conn.execute(
            f'''
            SELECT
                nsr.id, nsr.email, nsr.person_name, nsr.dob, nsr.county,
                nsr.status, nsr.stripe_payment_id, nsr.rejection_reason,
                nsr.created_at, nsr.reviewed_at, nsr.applied_at,
                (SELECT COUNT(*) FROM suppressed_names sn
                 WHERE sn.request_id = nsr.id) AS already_applied
            FROM name_suppression_requests nsr
            {where}
            ORDER BY
                CASE nsr.status WHEN 'paid' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,
                datetime(nsr.created_at) DESC,
                nsr.id DESC
            LIMIT 100
            ''',
            params,
        ).fetchall()
    finally:
        conn.close()
    return render_template(
        'admin_name_removals.html',
        rows=rows,
        status_filter=status_filter,
        active_nav='name-removals',
        page_title='Name Removal Requests',
        current_year=__import__('datetime').datetime.now().year,
    )


@admin_bp.route('/name-removals/<int:request_id>/approve', methods=['POST'])
@login_required
def admin_name_removal_approve(request_id):
    _require_admin()
    conn = get_db()
    try:
        row = conn.execute(
            'SELECT id, person_name, county, status FROM name_suppression_requests WHERE id = ?',
            (request_id,),
        ).fetchone()
        if not row:
            abort(404)
        if row['status'] in ('applied', 'rejected'):
            conn.close()
            return redirect(url_for('admin_name_removals.admin_name_removals', status='all'))
        applied = apply_suppression(
            request_id=request_id,
            person_name=row['person_name'],
            county=row['county'],
            applied_by=getattr(current_user, 'id', None),
        )
        conn.execute(
            "UPDATE name_suppression_requests SET status='applied', reviewed_by=?, reviewed_at=datetime('now') WHERE id = ?",
            (getattr(current_user, 'id', None), request_id),
        )
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for('admin_name_removals.admin_name_removals', status='all'))


@admin_bp.route('/name-removals/<int:request_id>/reject', methods=['POST'])
@login_required
def admin_name_removal_reject(request_id):
    _require_admin()
    reason = (request.form.get('reason') or 'Does not meet removal criteria').strip()[:280]
    conn = get_db()
    try:
        conn.execute(
            "UPDATE name_suppression_requests SET status='rejected', rejection_reason=?, reviewed_by=?, reviewed_at=datetime('now') WHERE id = ?",
            (reason, getattr(current_user, 'id', None), request_id),
        )
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for('admin_name_removals.admin_name_removals', status='all'))
