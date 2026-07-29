"""
Admin panel — Lawyer directory paid placements (/lawyers).

Routes:
- GET  /admin/lawyer-ads                       — list orders + recent leads
- POST /admin/lawyer-ads/<id>/cancel           — cancel an active order
- GET  /admin/lawyer-ads/new                   — manual order entry (comp / invoice)
- POST /admin/lawyer-ads/new                   — submit manual order
- GET  /admin/lawyer-ads/<id>/edit             — edit order + listing copy
- POST /admin/lawyer-ads/<id>/edit             — save edit
"""
from __future__ import annotations

import secrets
from datetime import datetime

from flask import flash, redirect, render_template, request
from flask_login import login_required

from db import get_db
from blueprints.admin import admin_bp, _log_admin_action
from init_db import ensure_lawyer_ad_schema


_PRICE_CENTS = {'bronze': 14900, 'silver': 29900, 'gold': 59900}


def _fetch_order(conn, order_id: int):
    row = conn.execute(
        '''
        SELECT o.*, l.tagline, l.description, l.headline, l.body_copy,
               l.cta_text, l.target_url, l.impressions, l.clicks, l.leads
        FROM lawyer_ad_orders o
        LEFT JOIN lawyer_ad_listings l ON l.order_id = o.id
        WHERE o.id = ?
        ''',
        (order_id,),
    ).fetchone()
    return dict(row) if row else None


@admin_bp.route('/lawyer-ads')
@login_required
def admin_lawyer_ads():
    conn = get_db()
    ensure_lawyer_ad_schema(conn)

    q = (request.args.get('q') or '').strip()[:120]
    status_filter = (request.args.get('status') or '').strip().lower()
    package_filter = (request.args.get('package') or '').strip().lower()

    where = ['1=1']
    params: list = []
    if q:
        where.append('(firm_name LIKE ? OR contact_name LIKE ? OR email LIKE ? OR counties_served LIKE ?)')
        like = f'%{q}%'
        params.extend([like, like, like, like])
    if status_filter:
        where.append('status = ?')
        params.append(status_filter)
    if package_filter:
        where.append('package_id = ?')
        params.append(package_filter)

    rows = conn.execute(
        f'''
        SELECT o.*, l.impressions, l.clicks, l.leads
        FROM lawyer_ad_orders o
        LEFT JOIN lawyer_ad_listings l ON l.order_id = o.id
        WHERE {' AND '.join(where)}
        ORDER BY datetime(o.created_at) DESC
        LIMIT 200
        ''',
        params,
    ).fetchall()
    orders = [dict(r) for r in rows]

    stats = {
        'total': conn.execute('SELECT COUNT(*) FROM lawyer_ad_orders').fetchone()[0],
        'active': conn.execute("SELECT COUNT(*) FROM lawyer_ad_orders WHERE status = 'active'").fetchone()[0],
        'gold': conn.execute("SELECT COUNT(*) FROM lawyer_ad_orders WHERE status = 'active' AND package_id = 'gold'").fetchone()[0],
        'silver': conn.execute("SELECT COUNT(*) FROM lawyer_ad_orders WHERE status = 'active' AND package_id = 'silver'").fetchone()[0],
        'bronze': conn.execute("SELECT COUNT(*) FROM lawyer_ad_orders WHERE status = 'active' AND package_id = 'bronze'").fetchone()[0],
    }
    mrr_cents = 0
    for o in orders:
        if o['status'] == 'active' and o['billing_cycle'] == 'monthly':
            mrr_cents += o.get('amount_cents', 0) or _PRICE_CENTS.get(o['package_id'], 0)
        elif o['status'] == 'active' and o['billing_cycle'] == 'annual':
            mrr_cents += (o.get('amount_cents', 0) or _PRICE_CENTS.get(o['package_id'], 0)) // 12
    stats['mrr_cents'] = mrr_cents

    recent_leads_rows = conn.execute(
        '''
        SELECT id, full_name, phone, county, case_type, source, routed_order_ids, created_at
        FROM lawyer_consumer_leads
        ORDER BY datetime(created_at) DESC
        LIMIT 30
        '''
    ).fetchall()
    recent_leads = [dict(r) for r in recent_leads_rows]

    conn.close()

    return render_template(
        'admin_lawyer_ads.html',
        orders=orders,
        recent_leads=recent_leads,
        stats=stats,
        q=q,
        status_filter=status_filter,
        package_filter=package_filter,
    )


@admin_bp.route('/lawyer-ads/<int:order_id>/cancel', methods=['POST'])
@login_required
def admin_lawyer_ads_cancel(order_id):
    conn = get_db()
    ensure_lawyer_ad_schema(conn)
    conn.execute(
        '''
        UPDATE lawyer_ad_orders
        SET status = 'cancelled', cancelled_at = datetime('now'), updated_at = datetime('now')
        WHERE id = ? AND status = 'active'
        ''',
        (order_id,),
    )
    conn.execute(
        '''
        UPDATE lawyer_ad_listings
        SET is_active = 0, updated_at = datetime('now')
        WHERE order_id = ?
        ''',
        (order_id,),
    )
    _log_admin_action(
        conn,
        actor='admin',
        action='lawyer_ad.cancel',
        target_type='lawyer_ad_order',
        target_id=str(order_id),
    )
    conn.commit()
    conn.close()
    flash('Order cancelled.', 'success')
    return redirect('/admin/lawyer-ads')


@admin_bp.route('/lawyer-ads/new', methods=['GET', 'POST'])
@login_required
def admin_lawyer_ads_new():
    if request.method == 'POST':
        form = {k: (request.form.get(k) or '').strip()[:v] for k, v in {
            'firm_name': 200,
            'contact_name': 160,
            'email': 160,
            'phone': 40,
            'website': 300,
            'bar_number': 40,
            'counties_served': 600,
            'practice_areas': 600,
            'package_id': 32,
            'billing_cycle': 16,
        }.items()}

        if not form['firm_name'] or not form['email'] or not form['counties_served']:
            flash('Firm name, email, and counties served are required.', 'error')
            return redirect('/admin/lawyer-ads/new')

        if form['package_id'] not in _PRICE_CENTS:
            form['package_id'] = 'bronze'
        if form['billing_cycle'] not in ('monthly', 'annual'):
            form['billing_cycle'] = 'monthly'

        amount_cents = _PRICE_CENTS[form['package_id']] * (12 if form['billing_cycle'] == 'annual' else 1)
        token = secrets.token_urlsafe(24)

        conn = get_db()
        ensure_lawyer_ad_schema(conn)
        cur = conn.execute(
            '''
            INSERT INTO lawyer_ad_orders (
                firm_name, contact_name, email, phone, website,
                bar_number, counties_served, practice_areas,
                package_id, billing_cycle, amount_cents,
                provider, status, onboarding_token, paid_at, notes, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', 'active', ?, datetime('now'),
                      'Created via admin (comp / invoice)', datetime('now'))
            ''',
            (
                form['firm_name'], form['contact_name'], form['email'], form['phone'],
                form['website'], form['bar_number'], form['counties_served'],
                form['practice_areas'], form['package_id'], form['billing_cycle'],
                amount_cents, token,
            ),
        )
        new_id = cur.lastrowid
        conn.execute(
            '''
            INSERT INTO lawyer_ad_listings
                (order_id, firm_name, counties_served, practice_areas, is_active)
            VALUES (?, ?, ?, ?, 1)
            ''',
            (new_id, form['firm_name'], form['counties_served'], form['practice_areas']),
        )
        _log_admin_action(
            conn,
            actor='admin',
            action='lawyer_ad.create_manual',
            target_type='lawyer_ad_order',
            target_id=str(new_id),
        )
        conn.commit()
        conn.close()
        flash(f'Created lawyer ad order #{new_id} as active.', 'success')
        return redirect(f'/admin/lawyer-ads/{new_id}/edit')

    return render_template('admin_lawyer_ads_edit.html', order=None, listing=None, saved=False)


@admin_bp.route('/lawyer-ads/<int:order_id>/edit', methods=['GET', 'POST'])
@login_required
def admin_lawyer_ads_edit(order_id):
    from blueprints.lawyer_ads import (
        LOGO_UPLOAD_DIR,
        PHOTO_UPLOAD_DIR,
        _ALLOWED_IMAGE_EXTS,
        _MAX_UPLOAD_BYTES,
        _save_upload,
    )

    conn = get_db()
    ensure_lawyer_ad_schema(conn)

    if request.method == 'POST':
        form = {k: (request.form.get(k) or '').strip()[:v] for k, v in {
            'firm_name': 200,
            'contact_name': 160,
            'email': 160,
            'phone': 40,
            'website': 300,
            'bar_number': 40,
            'counties_served': 600,
            'practice_areas': 600,
            'package_id': 32,
            'billing_cycle': 16,
            'status': 32,
            'tagline': 120,
            'description': 1000,
            'headline': 200,
            'body_copy': 1000,
            'cta_text': 80,
            'target_url': 500,
        }.items()}

        if form['package_id'] not in _PRICE_CENTS:
            form['package_id'] = 'bronze'
        if form['billing_cycle'] not in ('monthly', 'annual'):
            form['billing_cycle'] = 'monthly'

        conn.execute(
            '''
            UPDATE lawyer_ad_orders SET
                firm_name = ?, contact_name = ?, email = ?, phone = ?, website = ?,
                bar_number = ?, counties_served = ?, practice_areas = ?,
                package_id = ?, billing_cycle = ?, status = ?, updated_at = datetime('now')
            WHERE id = ?
            ''',
            (
                form['firm_name'], form['contact_name'], form['email'], form['phone'],
                form['website'], form['bar_number'], form['counties_served'],
                form['practice_areas'], form['package_id'], form['billing_cycle'],
                form['status'], order_id,
            ),
        )

        # Upsert listing row
        existing = conn.execute(
            'SELECT id FROM lawyer_ad_listings WHERE order_id = ?', (order_id,)
        ).fetchone()
        is_active = 1 if form['status'] == 'active' else 0

        logo_path = _save_upload(request.files.get('logo'), LOGO_UPLOAD_DIR, f'logo_{order_id}')
        photo_path = _save_upload(request.files.get('photo'), PHOTO_UPLOAD_DIR, f'photo_{order_id}')

        listing_fields = [
            'firm_name = ?', 'tagline = ?', 'description = ?', 'headline = ?', 'body_copy = ?',
            'cta_text = ?', 'target_url = ?', 'counties_served = ?',
            'practice_areas = ?', 'is_active = ?', "updated_at = datetime('now')",
        ]
        listing_params = [
            form['firm_name'], form['tagline'], form['description'], form['headline'], form['body_copy'],
            form['cta_text'], form['target_url'], form['counties_served'],
            form['practice_areas'], is_active,
        ]
        if logo_path:
            listing_fields.append('logo_path = ?')
            listing_params.append(logo_path)
        if photo_path:
            listing_fields.append('photo_path = ?')
            listing_params.append(photo_path)

        if existing:
            listing_params.append(order_id)
            conn.execute(
                f'UPDATE lawyer_ad_listings SET {", ".join(listing_fields)} WHERE order_id = ?',
                listing_params,
            )
        else:
            insert_columns = ['order_id'] + [col.split(' = ')[0] for col in listing_fields if 'updated_at' not in col]
            placeholders = ['?'] * len(insert_columns)
            insert_values = [order_id] + [
                value for col, value in zip(listing_fields, listing_params)
                if 'updated_at' not in col
            ]
            conn.execute(
                f'INSERT INTO lawyer_ad_listings ({", ".join(insert_columns)}) VALUES ({", ".join(placeholders)})',
                insert_values,
            )

        _log_admin_action(
            conn,
            actor='admin',
            action='lawyer_ad.edit',
            target_type='lawyer_ad_order',
            target_id=str(order_id),
        )
        conn.commit()
        conn.close()
        return redirect(f'/admin/lawyer-ads/{order_id}/edit?saved=1')

    order = _fetch_order(conn, order_id)
    conn.close()
    if not order:
        flash('Order not found.', 'error')
        return redirect('/admin/lawyer-ads')

    return render_template(
        'admin_lawyer_ads_edit.html',
        order=order,
        listing=order,
        saved=request.args.get('saved') == '1',
    )