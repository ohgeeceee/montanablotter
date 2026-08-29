"""Admin routes for paid county-digest sponsorships.

GET  /admin/sponsored-digests            — list active + past sponsorships
POST /admin/sponsored-digests/add        — create new sponsorship
POST /admin/sponsored-digests/<id>/toggle — activate/deactivate
"""
from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required, current_user

from blueprints.admin import admin_bp, _log_admin_action
from db import get_db


@admin_bp.route('/content/sponsored-digests')
@login_required
def admin_sponsored_digests():
    conn = get_db()
    rows = conn.execute(
        '''
        SELECT * FROM sponsored_digests
        ORDER BY is_active DESC, county ASC
        '''
    ).fetchall()
    counties = [r[0] for r in conn.execute(
        "SELECT DISTINCT county FROM posts WHERE county IS NOT NULL AND county != '' "
        "UNION SELECT DISTINCT county FROM blotters WHERE county IS NOT NULL AND county != '' "
        "ORDER BY 1"
    ).fetchall()]
    conn.close()
    return render_template(
        'admin_sponsored_digests.html',
        rows=rows,
        counties=counties,
    )


@admin_bp.route('/content/sponsored-digests/add', methods=['POST'])
@login_required
def admin_sponsored_digests_add():
    county = (request.form.get('county') or '').strip()[:80]
    sponsor_name = (request.form.get('sponsor_name') or '').strip()[:120]
    sponsor_pitch = (request.form.get('sponsor_pitch') or '').strip()[:400]
    sponsor_url = (request.form.get('sponsor_url') or '').strip()[:400]
    contact_email = (request.form.get('contact_email') or '').strip()[:160].lower()
    monthly_rate = (request.form.get('monthly_rate') or '0').strip()
    try:
        monthly_cents = int(round(float(monthly_rate) * 100))
        if monthly_cents < 0:
            monthly_cents = 0
    except (ValueError, TypeError):
        monthly_cents = 0
    starts_on = (request.form.get('starts_on') or '').strip()[:10] or None
    expires_on = (request.form.get('expires_on') or '').strip()[:10] or None
    notes = (request.form.get('notes') or '').strip()[:600] or None

    if not county or not sponsor_name:
        flash('County and sponsor name are required.', 'error')
        return redirect(url_for('admin.admin_sponsored_digests'))

    conn = get_db()
    try:
        # Deactivate any existing active sponsor for this county
        conn.execute(
            'UPDATE sponsored_digests SET is_active = 0 WHERE county = ? AND is_active = 1',
            (county,),
        )
        cur = conn.execute(
            '''
            INSERT INTO sponsored_digests
              (county, sponsor_name, sponsor_pitch, sponsor_url, contact_email,
               monthly_rate_cents, is_active, starts_on, expires_on, notes,
               created_by_user_id)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
            ''',
            (county, sponsor_name, sponsor_pitch or None, sponsor_url or None,
             contact_email or None, monthly_cents, starts_on, expires_on, notes,
             getattr(current_user, 'id', None)),
        )
        conn.commit()
        _log_admin_action(
            'sponsored_digest_add', 'sponsored_digests', cur.lastrowid,
            {'county': county, 'sponsor_name': sponsor_name, 'monthly_rate_cents': monthly_cents},
            conn=conn,
        )
        flash(f'Sponsorship added for {county} County ({sponsor_name}).', 'success')
    except Exception as exc:
        flash(f'Error adding sponsorship: {exc}', 'error')
    finally:
        conn.close()
    return redirect(url_for('admin.admin_sponsored_digests'))


@admin_bp.route('/content/sponsored-digests/<int:sponsor_id>/toggle', methods=['POST'])
@login_required
def admin_sponsored_digests_toggle(sponsor_id: int):
    conn = get_db()
    row = conn.execute(
        'SELECT id, county, is_active FROM sponsored_digests WHERE id = ?', (sponsor_id,),
    ).fetchone()
    if not row:
        conn.close()
        flash('Sponsorship not found.', 'error')
        return redirect(url_for('admin.admin_sponsored_digests'))

    new_state = 0 if row['is_active'] else 1
    if new_state == 1:
        # Deactivate any other active sponsor for the same county
        conn.execute(
            'UPDATE sponsored_digests SET is_active = 0 WHERE county = ? AND id != ? AND is_active = 1',
            (row['county'], sponsor_id),
        )
    conn.execute(
        'UPDATE sponsored_digests SET is_active = ?, updated_at = datetime("now") WHERE id = ?',
        (new_state, sponsor_id),
    )
    conn.commit()
    _log_admin_action(
        'sponsored_digest_toggle', 'sponsored_digests', sponsor_id,
        {'new_state': new_state}, conn=conn,
    )
    conn.close()
    flash(
        f"Sponsorship {'activated' if new_state else 'deactivated'} for {row['county']} County.",
        'success',
    )
    return redirect(url_for('admin.admin_sponsored_digests'))
