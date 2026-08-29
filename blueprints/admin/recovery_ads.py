"""
Admin panel — Recovery Center Advertising orders, status management, and listing CMS.
"""
from __future__ import annotations

import json
from datetime import datetime

from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required

from db import get_db
from blueprints.admin import admin_bp, _log_admin_action
from blueprints.recovery_ads import _recovery_ad_package_lookup


@admin_bp.route('/revenue/recovery-ads')
@login_required
def admin_recovery_ads():
    from init_db import ensure_recovery_ad_schema
    conn = get_db()
    ensure_recovery_ad_schema(conn)

    q = (request.args.get('q') or '').strip()[:120]
    status_filter = (request.args.get('status') or 'all').strip().lower()

    base_query = '''
        SELECT o.id, o.center_name, o.contact_name, o.email, o.phone,
               o.website, o.package_id, o.billing_cycle, o.status,
               o.created_at, o.activated_at, o.cancelled_at,
               l.impressions, l.clicks, l.logo_path
        FROM recovery_ad_orders o
        LEFT JOIN recovery_ad_listings l ON l.order_id = o.id
    '''
    where = []
    params = []
    if q:
        where.append('(o.center_name LIKE ? OR o.email LIKE ? OR o.contact_name LIKE ?)')
        like = f'%{q}%'
        params.extend([like, like, like])
    if status_filter != 'all':
        where.append('o.status = ?')
        params.append(status_filter)
    if where:
        base_query += ' WHERE ' + ' AND '.join(where)
    base_query += ' ORDER BY o.created_at DESC LIMIT 200'

    orders = [dict(r) for r in conn.execute(base_query, params).fetchall()]

    # MRR summary
    pkg_lookup = _recovery_ad_package_lookup()
    active_orders = conn.execute(
        "SELECT package_id, billing_cycle FROM recovery_ad_orders WHERE status = 'active'"
    ).fetchall()
    mrr_cents = 0
    for row in active_orders:
        pkg = pkg_lookup.get(row['package_id']) or {}
        if row['billing_cycle'] == 'annual':
            mrr_cents += (pkg.get('price_annual_cents') or 0) // 12
        else:
            mrr_cents += pkg.get('price_monthly_cents') or 0

    tier_counts = {'bronze': 0, 'silver': 0, 'gold': 0}
    for row in active_orders:
        if row['package_id'] in tier_counts:
            tier_counts[row['package_id']] += 1

    conn.close()

    return render_template(
        'admin_recovery_ads.html',
        orders=orders,
        package_lookup=pkg_lookup,
        q=q,
        status_filter=status_filter,
        mrr_cents=mrr_cents,
        tier_counts=tier_counts,
        active_count=len(active_orders),
        current_year=datetime.now().year,
    )


@admin_bp.route('/revenue/recovery-ads/order/<int:order_id>/status', methods=['POST'])
@login_required
def admin_recovery_ads_order_status(order_id):
    new_status = (request.form.get('status') or '').strip().lower()
    allowed = {'active', 'inactive', 'cancelled', 'pending'}
    if new_status not in allowed:
        flash('Invalid status.', 'error')
        return redirect(url_for('.admin_recovery_ads'))

    conn = get_db()
    conn.execute(
        'UPDATE recovery_ad_orders SET status = ? WHERE id = ?',
        (new_status, order_id),
    )
    conn.commit()
    _log_admin_action('recovery_ad_status_change', 'recovery_ad_order', order_id,
                      metadata={'new_status': new_status}, conn=conn)
    conn.close()
    return redirect(url_for('.admin_recovery_ads'))


@admin_bp.route('/revenue/recovery-ads/cms/<int:order_id>', methods=['GET', 'POST'])
@login_required
def admin_recovery_ads_cms(order_id):
    from init_db import ensure_recovery_ad_schema
    conn = get_db()
    ensure_recovery_ad_schema(conn)

    order_row = conn.execute(
        '''
        SELECT o.id, o.center_name, o.email, o.package_id, o.status,
               l.tagline, l.description, l.services, l.city, l.county,
               l.logo_path, l.photo_path
        FROM recovery_ad_orders o
        LEFT JOIN recovery_ad_listings l ON l.order_id = o.id
        WHERE o.id = ?
        ''',
        (order_id,),
    ).fetchone()
    if not order_row:
        conn.close()
        return render_template('404.html'), 404

    order = dict(order_row)
    services_list = []
    if order.get('services'):
        try:
            services_list = json.loads(order['services']) or []
        except Exception:
            pass

    if request.method == 'POST':
        pkg = _recovery_ad_package_lookup().get(order['package_id']) or {}
        desc_limit = pkg.get('description_limit') or 0

        tagline = (request.form.get('tagline') or '').strip()[:120]
        description = (request.form.get('description') or '').strip()
        if desc_limit:
            description = description[:desc_limit]
        city = (request.form.get('city') or '').strip()[:80]
        county = (request.form.get('county') or '').strip()[:80]
        raw_services = [s.strip() for s in (request.form.get('services') or '').split(',') if s.strip()]
        services_json = json.dumps(raw_services[:20])

        conn.execute(
            '''
            UPDATE recovery_ad_listings
            SET tagline=?, description=?, services=?, city=?, county=?, updated_at=datetime('now')
            WHERE order_id=?
            ''',
            (tagline, description, services_json, city, county, order_id),
        )
        conn.commit()
        _log_admin_action('recovery_ad_cms_edit', 'recovery_ad_order', order_id, conn=conn)
        conn.close()
        flash('Listing updated.', 'success')
        return redirect(url_for('.admin_recovery_ads_cms', order_id=order_id))

    conn.close()
    pkg = _recovery_ad_package_lookup().get(order['package_id']) or {}
    return render_template(
        'admin_recovery_ads_cms.html',
        order=order,
        package=pkg,
        services_list=services_list,
        current_year=datetime.now().year,
    )
